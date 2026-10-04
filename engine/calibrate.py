"""Size the fuel tank so the season is genuinely marginal.

A tank four times larger than the season needs makes the survivability
constraint decorative: the interesting regime -- and the one the pitch is about
-- is the one where the allocator has to say no. This script runs the tuned
rule-based baseline over a full test year with an effectively unlimited tank,
then reports what the tank should be for a given margin.

    python calibrate.py --margin 1.05
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from polarems.config import load_config  # noqa: E402
from polarems.controllers import RuleBasedController  # noqa: E402
from polarems.forecast import NWP  # noqa: E402
from polarems.harness import run_season  # noqa: E402
from polarems.metrics import summarise  # noqa: E402
from polarems.twin import build_twin, gensets  # noqa: E402
from polarems.weather import fetch_year  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--margin", type=float, default=1.05, help="tank / baseline-B annual burn")
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args()

    cfg = load_config().override("fuel.tank_litres_at_season_start", 5_000_000.0)
    gens = gensets(cfg)
    year = int(cfg["simulation.test_year"])
    twin = build_twin(fetch_year(cfg["station.latitude"], cfg["station.longitude"], year), cfg)
    season = twin.head(args.days * 24)

    log = run_season(RuleBasedController(), season, cfg, gens, NWP(season), None, None)
    s = summarise(log, cfg, gens)
    burn = s["fuel_l"]
    monthly = log["fuel_l"].resample("MS").sum()

    print(f"baseline B over {args.days} d: {burn:,.0f} L  ({s['fuel_per_day_l']:,.1f} L/day)")
    print(f"peak month: {monthly.idxmax():%B} at {monthly.max():,.0f} L")
    print(f"critical outages under B: {s['critical_outage_events']}")
    print()
    for margin in (0.95, 1.0, args.margin, 1.25, 1.5):
        print(f"  margin {margin:4.2f} -> tank {burn * margin:>10,.0f} L")
    print()
    print("Set fuel.tank_litres_at_season_start in config/station.json to the margin you")
    print("intend to defend, and say in the pitch which margin you chose and why.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
