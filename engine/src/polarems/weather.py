"""Real ERA5 weather for the station site, with zero credentials.

The Copernicus CDS needs an account and an approved licence, which is a bad
dependency to discover at hour 4 of a hackathon. Open-Meteo re-serves the same
ERA5 reanalysis over an unauthenticated HTTP API, hourly, back to 1940,
including Antarctica. We pull once per year-chunk and cache to Parquet, so the
whole pipeline runs offline afterwards.

Columns produced (hourly, UTC):
    temp_c, wind_ms (at 10 m), wind_dir_deg (meteorological, direction FROM),
    ghi_wm2, snowfall_cm, pressure_hpa, rh_pct
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .config import REPO_ROOT, Config

CACHE_DIR = REPO_ROOT / "data" / "cache"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = [
    "temperature_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "shortwave_radiation",
    "snowfall",
    "surface_pressure",
    "relative_humidity_2m",
]


def _cache_path(lat: float, lon: float, year: int) -> Path:
    return CACHE_DIR / f"era5_{lat:.3f}_{lon:.3f}_{year}.parquet"


def fetch_year(lat: float, lon: float, year: int, retries: int = 3) -> pd.DataFrame:
    """One calendar year of ERA5 at the nearest grid point, cached on disk."""
    path = _cache_path(lat, lon, year)
    if path.exists():
        return pd.read_parquet(path)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": ",".join(HOURLY_VARS),
        "models": "era5",
        "timezone": "UTC",
    }
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(ARCHIVE_URL, params=params, timeout=90)
            resp.raise_for_status()
            payload = resp.json()["hourly"]
            break
        except Exception as err:  # noqa: BLE001 - network flakiness is expected
            last_err = err
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"ERA5 fetch failed for {year}: {last_err}")

    df = pd.DataFrame(
        {
            "temp_c": payload["temperature_2m"],
            "wind_ms": np.asarray(payload["wind_speed_10m"], dtype=float) / 3.6,
            "wind_dir_deg": payload["wind_direction_10m"],
            "ghi_wm2": payload["shortwave_radiation"],
            "snowfall_cm": payload["snowfall"],
            "pressure_hpa": payload["surface_pressure"],
            "rh_pct": payload["relative_humidity_2m"],
        },
        index=pd.DatetimeIndex(pd.to_datetime(payload["time"]), name="time"),
    )
    df = df.astype(float).interpolate(limit_direction="both")
    df.to_parquet(path)
    return df


def fetch_years(cfg: Config, years: list[int]) -> pd.DataFrame:
    lat = cfg["station.latitude"]
    lon = cfg["station.longitude"]
    frames = [fetch_year(lat, lon, y) for y in sorted(set(years))]
    return pd.concat(frames).sort_index()


def load_weather(cfg: Config, years: list[int] | None = None) -> pd.DataFrame:
    if years is None:
        years = sorted(
            set(cfg["simulation.train_years"])
            | {cfg["simulation.test_year"]}
            | set(cfg["simulation.mc_years"])
        )
    return fetch_years(cfg, years)
