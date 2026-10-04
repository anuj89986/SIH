"""Replay a controller's decisions as the setpoints real plant would receive.

Answers "can this drive actual station hardware?" without claiming more than is true:
the controller emits Modbus holding registers and MQTT messages; nothing here has been
tested against a physical genset controller, and that test is the next step.

    python emit_setpoints.py                       # 24 h of controller C, both encodings
    python emit_setpoints.py --hours 6 --format modbus
    python emit_setpoints.py --register-map        # print the map for the slide
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from polarems.config import load_config  # noqa: E402
from polarems.controllers import MPCController, RuleBasedController  # noqa: E402
from polarems.forecast import NWP, QuantileForecaster  # noqa: E402
from polarems.fuelbudget import build_fuel_plan  # noqa: E402
from polarems.harness import run_season  # noqa: E402
from polarems.setpoints import REGISTER_MAP, from_log_row, register_map_markdown  # noqa: E402
from polarems.twin import build_twin, gensets  # noqa: E402
from polarems.weather import fetch_year  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--start", default=None, help="default: 1 July of the test year (polar night)")
    ap.add_argument("--controller", default="C", choices=["B", "C"])
    ap.add_argument("--format", default="both", choices=["both", "modbus", "mqtt"])
    ap.add_argument("--register-map", action="store_true")
    args = ap.parse_args()

    if args.register_map:
        print(register_map_markdown())
        return 0

    cfg = load_config()
    gens = gensets(cfg)
    year = int(cfg["simulation.test_year"])
    start = args.start or f"{year}-07-01"

    twins = {
        y: build_twin(fetch_year(cfg["station.latitude"], cfg["station.longitude"], y), cfg,
                      seed=int(cfg["simulation.seed"]) + y)
        for y in sorted(set(cfg["simulation.train_years"]) | {year} | set(cfg["simulation.mc_years"]))
    }
    train = pd.concat([twins[y] for y in cfg["simulation.train_years"]]).sort_index()
    fc = QuantileForecaster(cfg).fit(train, NWP(train))

    # a short warm-up window so the controller has history before the hours we print
    season = twins[year].loc[pd.Timestamp(start) - pd.Timedelta(hours=72) :].head(
        72 + args.hours + 48
    )
    plan = build_fuel_plan({y: twins[y] for y in cfg["simulation.mc_years"]}, season.index, cfg, gens)
    controller = MPCController() if args.controller == "C" else RuleBasedController()
    log = run_season(controller, season, cfg, gens, NWP(season), fc, plan)

    allowance = plan.allowance.reindex(log.index.normalize()).ffill().to_numpy() / 24.0
    print(f"# setpoints from controller {args.controller}, {args.hours} h from {log.index[0]}\n")
    for i, (ts, row) in enumerate(log.head(args.hours).iterrows()):
        sp = from_log_row(row, plan_note="rolling MILP, 36 h horizon", allowance_lph=float(allowance[i]))
        print(f"--- {ts} ---")
        if args.format in ("both", "modbus"):
            regs = sp.to_modbus()
            print("  modbus: " + " ".join(f"{r}={v}" for r, v in sorted(regs.items())))
        if args.format in ("both", "mqtt"):
            for topic, payload in sp.to_mqtt():
                print(f"  mqtt   {topic}  {payload}")
    print(f"\n{len(REGISTER_MAP)} registers defined; see emit_setpoints.py --register-map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
