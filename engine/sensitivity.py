"""Sensitivity of the headline result to the parameters we are least sure about.

"Your data is invented" is the question that decides this problem statement. The answer is
not a denial -- it is this table: the same closed-loop comparison, re-run at the low and high
end of each uncertain parameter's documented range, showing whether the conclusion survives.

Defaults sweep the three parameters that carry the most weight and the least evidence:
envelope heat loss, the electrified fraction of heat, and the 24x7 science base load.

    python sensitivity.py                        # 3 parameters, 60-day winter window
    python sensitivity.py --days 90 --params thermal.envelope_UA,pv.kwp
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from polarems.config import load_config  # noqa: E402
from polarems.controllers import MPCController, RuleBasedController  # noqa: E402
from polarems.forecast import NWP, QuantileForecaster  # noqa: E402
from polarems.fuelbudget import build_fuel_plan  # noqa: E402
from polarems.harness import run_season  # noqa: E402
from polarems.metrics import summarise  # noqa: E402
from polarems.report import RESULTS_DIR, write_results  # noqa: E402
from polarems.twin import build_twin, gensets  # noqa: E402
from polarems.weather import fetch_year  # noqa: E402

DEFAULT_PARAMS = [
    "thermal.envelope_UA",
    "thermal.heat_electrified_frac",
    "loads.science_base_kw",
]


def sensitivity_range(cfg, dotted: str) -> list[float]:
    node = cfg.raw
    for part in dotted.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    lo, hi = node.get("sensitivity", [node["value"] * 0.7, node["value"] * 1.3])
    return [float(lo), float(node["value"]), float(hi)]


def one_case(cfg, start: str, days: int) -> dict:
    """Train, plan and run B and C end to end under this parameter set."""
    gens = gensets(cfg)
    lat, lon = cfg["station.latitude"], cfg["station.longitude"]
    test_year = int(cfg["simulation.test_year"])
    twins = {
        y: build_twin(fetch_year(lat, lon, y), cfg, seed=int(cfg["simulation.seed"]) + y)
        for y in sorted(set(cfg["simulation.train_years"]) | {test_year} | set(cfg["simulation.mc_years"]))
    }
    twin_train = pd.concat([twins[y] for y in cfg["simulation.train_years"]]).sort_index()
    fc = QuantileForecaster(cfg).fit(twin_train, NWP(twin_train))
    season = twins[test_year].loc[start:].head(days * 24)
    nwp = NWP(season, seed=int(cfg["simulation.seed"]) + 7)
    plan = build_fuel_plan({y: twins[y] for y in cfg["simulation.mc_years"]}, season.index, cfg, gens)

    out = {}
    for name, controller, fuel_plan in (
        ("B", RuleBasedController(), None),
        ("C", MPCController(), plan),
    ):
        log = run_season(controller, season, cfg, gens, nwp, fc, fuel_plan)
        out[name] = summarise(log, cfg, gens)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--start", default=None, help="season start (default: 1 May of the test year)")
    ap.add_argument("--params", default=",".join(DEFAULT_PARAMS))
    args = ap.parse_args()

    base = load_config()
    start = args.start or f"{int(base['simulation.test_year'])}-05-01"
    params = [p.strip() for p in args.params.split(",") if p.strip()]

    rows = []
    t0 = time.perf_counter()
    print(f"baseline case ({args.days} d from {start})")
    ref = one_case(base, start, args.days)
    rows.append(
        {
            "parameter": "(baseline)",
            "setting": "configured",
            "value": None,
            "B_fuel_l": ref["B"]["fuel_l"],
            "C_fuel_l": ref["C"]["fuel_l"],
            "C_vs_B_pct": 100 * (ref["C"]["fuel_l"] - ref["B"]["fuel_l"]) / ref["B"]["fuel_l"],
            "B_outages": ref["B"]["critical_outage_events"],
            "C_outages": ref["C"]["critical_outage_events"],
            "C_clean_air_pct": ref["C"]["clean_air_compliance_pct"],
        }
    )

    for dotted in params:
        lo, mid, hi = sensitivity_range(base, dotted)
        for label, value in (("low", lo), ("high", hi)):
            if value == mid:
                continue
            print(f"  {dotted} = {value} ({label})")
            res = one_case(base.override(dotted, value), start, args.days)
            rows.append(
                {
                    "parameter": dotted,
                    "setting": label,
                    "value": value,
                    "B_fuel_l": res["B"]["fuel_l"],
                    "C_fuel_l": res["C"]["fuel_l"],
                    "C_vs_B_pct": 100 * (res["C"]["fuel_l"] - res["B"]["fuel_l"]) / res["B"]["fuel_l"],
                    "B_outages": res["B"]["critical_outage_events"],
                    "C_outages": res["C"]["critical_outage_events"],
                    "C_clean_air_pct": res["C"]["clean_air_compliance_pct"],
                }
            )

    frame = pd.DataFrame(rows)
    print()
    print(frame.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    span = frame["C_vs_B_pct"]
    print(
        f"\nC vs B ranges from {span.min():.1f}% to {span.max():.1f}% across this sweep; "
        f"C outages {frame['C_outages'].min()}-{frame['C_outages'].max()} against "
        f"B {frame['B_outages'].min()}-{frame['B_outages'].max()}."
    )
    print("Report the range, not the single number. That is what makes the number credible.")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(RESULTS_DIR / "sensitivity.csv", index=False)
    write_results(
        {"days": args.days, "start": start, "rows": frame.to_dict(orient="records")},
        "sensitivity.json",
    )
    print(f"wrote {RESULTS_DIR / 'sensitivity.csv'}  ({time.perf_counter() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
