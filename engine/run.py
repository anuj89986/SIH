"""End-to-end pipeline: one command, real ERA5 in, dashboard out.

    python run.py                     # default: 210-day season from the resupply date
    python run.py --days 60 --step 6  # fast iteration
    python run.py --full              # whole test year, all controllers, scenarios

Stages: weather -> digital twin -> quantile forecaster -> Monte Carlo fuel plan
-> closed-loop season for every controller -> metrics -> stress tests ->
results/run.json + dashboard/dashboard.html.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from polarems import controllers as ctrl_mod  # noqa: E402
from polarems import metrics as metrics_mod  # noqa: E402
from polarems import report as report_mod  # noqa: E402
from polarems import scenarios as scen_mod  # noqa: E402
from polarems.config import load_config  # noqa: E402
from polarems.forecast import NWP, QuantileForecaster, evaluate_forecaster  # noqa: E402
from polarems.fuelbudget import build_fuel_plan  # noqa: E402
from polarems.harness import run_season  # noqa: E402
from polarems.twin import build_twin, gensets  # noqa: E402
from polarems.weather import fetch_year  # noqa: E402


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="SIH26061 polar station energy pipeline")
    ap.add_argument("--config", default=None)
    ap.add_argument("--start", default=None, help="season start date (default: resupply date)")
    ap.add_argument("--days", type=int, default=210, help="season length in days")
    ap.add_argument("--step", type=int, default=None, help="control step in hours")
    ap.add_argument("--horizon", type=int, default=None, help="MPC horizon in hours")
    ap.add_argument("--controllers", default="A,B,C,C-point,D")
    ap.add_argument("--no-scenarios", action="store_true")
    ap.add_argument("--full", action="store_true", help="whole test year")
    ap.add_argument("--skip-forecast-eval", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.step:
        cfg = cfg.override("simulation.control_step_h", args.step)
    if args.horizon:
        cfg = cfg.override("simulation.horizon_h", args.horizon)

    gens = gensets(cfg)
    test_year = int(cfg["simulation.test_year"])
    train_years = list(cfg["simulation.train_years"])
    mc_years = list(cfg["simulation.mc_years"])

    # ---- weather -------------------------------------------------------
    log("fetching ERA5 (cached after first run)")
    years = sorted(set(train_years) | {test_year} | set(mc_years))
    weather = {y: fetch_year(cfg["station.latitude"], cfg["station.longitude"], y) for y in years}
    log(f"  {len(years)} years, {sum(len(w) for w in weather.values()):,} hourly records")

    # ---- twin ----------------------------------------------------------
    log("building physics digital twin")
    twins = {y: build_twin(w, cfg, seed=int(cfg["simulation.seed"]) + y) for y, w in weather.items()}
    twin_test = twins[test_year]
    twin_train = pd.concat([twins[y] for y in train_years]).sort_index()
    winter = twin_test.loc[f"{test_year}-06":f"{test_year}-08"]
    summer = twin_test.loc[f"{test_year}-12":f"{test_year}-12"]
    log(
        f"  mean load winter {winter['load_kw'].mean():.1f} kW at {winter['occupancy'].mean():.0f} people"
        f" vs summer {summer['load_kw'].mean():.1f} kW at {summer['occupancy'].mean():.0f} people"
    )

    # ---- forecaster ----------------------------------------------------
    log("training LightGBM quantile forecasters (load / pv / wind, p10-p50-p90)")
    nwp_train = NWP(twin_train, seed=int(cfg["simulation.seed"]))
    fc = QuantileForecaster(cfg).fit(twin_train, nwp_train)
    log(f"  {fc.train_report}")

    nwp_test = NWP(twin_test, seed=int(cfg["simulation.seed"]) + 7)
    forecast_report = {}
    if not args.skip_forecast_eval:
        log("evaluating forecast skill walk-forward on the unseen test year")
        forecast_report = evaluate_forecaster(fc, twin_test, nwp_test, n_issues=60)
        for tgt, rep in forecast_report.items():
            log(
                f"  {tgt:8s} MAE {rep['mae']:.2f} kW  MAPE {rep['mape_pct']:.1f}%  "
                f"p90 coverage {100 * rep['p90_coverage']:.0f}%"
            )

    # ---- season window -------------------------------------------------
    if args.full:
        season = twin_test
    else:
        if args.start:
            start = pd.Timestamp(args.start)
        else:
            mm, dd = (int(x) for x in str(cfg["fuel.resupply_mmdd"]).split("-"))
            start = pd.Timestamp(test_year, mm, dd)
        end = start + pd.Timedelta(days=args.days)
        season = twin_test.loc[start:end]
    log(f"season: {season.index[0].date()} -> {season.index[-1].date()} ({len(season)} h)")

    # ---- fuel plan -----------------------------------------------------
    log("Monte Carlo seasonal fuel allocation over historical weather years")
    fuel_plan = build_fuel_plan({y: twins[y] for y in mc_years}, season.index, cfg, gens)
    fm = fuel_plan.metrics
    log(
        f"  available {fm['available_l']:,.0f} L | mean-year need {fm['mean_year_need_l']:,.0f} L | "
        f"P{int(100 * fm['target_survivability'])} need {fm['p_target_need_l']:,.0f} L | "
        f"budget binding: {fm['budget_binding']}"
    )

    # ---- closed loop ---------------------------------------------------
    wanted = [c.strip() for c in args.controllers.split(",") if c.strip()]
    all_ctrls = {c.name: c for c in ctrl_mod.default_controllers(cfg)}
    logs: dict[str, pd.DataFrame] = {}
    summaries = []
    for name in wanted:
        if name not in all_ctrls:
            log(f"  ! unknown controller {name}, skipping")
            continue
        controller = all_ctrls[name]
        t0 = time.perf_counter()
        run_log = run_season(
            controller, season, cfg, gens, nwp_test, fc, fuel_plan if name != "A" else None
        )
        logs[name] = run_log
        summary = metrics_mod.summarise(run_log, cfg, gens)
        summaries.append(summary)
        log(
            f"  {name:8s} fuel {summary['fuel_l']:>9,.0f} L | critical outages "
            f"{summary['critical_outage_events']:>2d} | clean-air compliance "
            f"{summary['clean_air_compliance_pct']:5.1f}% | {time.perf_counter() - t0:5.1f} s"
        )

    table = metrics_mod.comparison_table(summaries)
    headline = metrics_mod.headline(table)
    if headline:
        log(
            f"HEADLINE: C uses {-headline['fuel_vs_B_pct']:.1f}% less fuel than tuned baseline B, "
            f"closes {headline['gap_to_oracle_closed_pct']:.0f}% of the gap to the oracle, "
            f"{headline['critical_outage_events_C']} critical outages vs "
            f"{headline['critical_outage_events_B']} for B"
        )

    # ---- stress tests --------------------------------------------------
    scenario_results = {}
    if not args.no_scenarios:
        log("stress tests")
        for scen in scen_mod.default_scenarios(season.index):
            scenario_results[scen.name] = {"label": scen.label, "controllers": {}}
            for name in [n for n in ("B", "C") if n in all_ctrls and n in logs]:
                run_log = run_season(
                    all_ctrls[name], season, cfg, gens, nwp_test, fc, fuel_plan, scenario=scen
                )
                s = metrics_mod.summarise(run_log, cfg, gens)
                scenario_results[scen.name]["controllers"][name] = s
                log(
                    f"  {scen.name:15s} {name}: unserved critical "
                    f"{s['unserved_critical_kwh']:.1f} kWh in {s['critical_outage_events']} events"
                )
        # forecast bust: truth unchanged, forecast badly wrong for 12 h
        bust_start = season.index[len(season) // 2].normalize()
        bust_nwp = scen_mod.ForecastBustNWP(nwp_test, bust_start)
        if "C" in all_ctrls:
            run_log = run_season(all_ctrls["C"], season, cfg, gens, bust_nwp, fc, fuel_plan)
            s = metrics_mod.summarise(run_log, cfg, gens)
            scenario_results["forecast_bust"] = {
                "label": "12 h badly wrong forecast, truth unchanged",
                "controllers": {"C": s},
            }
            log(
                f"  forecast_bust   C: unserved critical {s['unserved_critical_kwh']:.1f} kWh, "
                f"fuel {s['fuel_l']:,.0f} L"
            )

    # ---- report --------------------------------------------------------
    run_params = {
        "season_start": str(season.index[0]),
        "season_end": str(season.index[-1]),
        "days": round(len(season) / 24, 1),
        "control_step_h": int(cfg["simulation.control_step_h"]),
        "horizon_h": int(cfg["simulation.horizon_h"]),
        "test_year": test_year,
        "train_years": train_years,
        "mc_years": mc_years,
        "demo_week_start": str(season.index[len(season) // 2].normalize().date()),
    }
    payload = report_mod.assemble(
        cfg, season, logs, summaries, table, headline, fuel_plan, forecast_report,
        scenario_results, run_params,
    )
    json_path = report_mod.write_results(payload)
    log(f"wrote {json_path}")
    try:
        html_path = report_mod.build_dashboard(payload)
        log(f"wrote {html_path}")
    except FileNotFoundError:
        log("dashboard/template.html missing; skipped dashboard build")

    table.to_csv(report_mod.RESULTS_DIR / "metrics.csv")
    log(f"wrote {report_mod.RESULTS_DIR / 'metrics.csv'}")
    print()
    print(
        table[
            [
                "label",
                "fuel_l",
                "fuel_vs_B_pct",
                "critical_outage_events",
                "unserved_critical_kwh",
                "renewable_utilisation_pct",
                "clean_air_compliance_pct",
                "starts",
            ]
        ].to_string(float_format=lambda x: f"{x:,.2f}")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
