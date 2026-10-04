"""Offline artifact preparation; the HTTP server never trains models.

    .venv/Scripts/python train_forecast.py
"""
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from polarems.config import load_config
from polarems.forecast import HAS_LGB, NWP, QuantileForecaster
from polarems.twin import build_twin
from polarems.weather import fetch_year

if __name__ == "__main__":
    if not HAS_LGB:
        raise SystemExit("Install engine/requirements.txt including LightGBM first.")
    cfg = load_config()
    years = cfg["simulation.train_years"]
    frames = []
    for year in years:
        print(f"Building training twin {year}", flush=True)
        weather = fetch_year(cfg["station.latitude"], cfg["station.longitude"], year)
        frames.append(build_twin(weather, cfg, seed=int(cfg["simulation.seed"]) + year))
    twin = pd.concat(frames).sort_index()
    print("Training nine quantile models once (held-out 2023 excluded)", flush=True)
    fc = QuantileForecaster(cfg).fit(twin, NWP(twin, seed=int(cfg["simulation.seed"])))
    fc.save(ROOT / "data" / "forecast_models", {"training_years": years,
            "station_config": cfg.values, "nwp": "lead-degraded ERA5; synthetic forecast inputs"})
    print(f"Saved native LightGBM artifacts: {fc.train_report}", flush=True)
