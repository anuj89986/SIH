"""Science-integrity constraint on dispatch.

Differentiator 2, and the one line in the pitch that shows the team read the
polar literature rather than a microgrid tutorial: locally emitted greenhouse
gases are not representative of the wider region, so the station's own diesel
exhaust corrupts the atmospheric record the station exists to collect
(de Witt, Chung & Lee 2024).

A clean-air window is an hour in which forecast wind would carry exhaust from
the powerhouse toward the sampling inlet. During those hours the optimiser is
either forbidden to start a generator (hard mode) or pays a per-hour price for
doing so (soft mode, the default -- survivability outranks a sampling hour).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config


def _angular_distance(a: np.ndarray, b: float) -> np.ndarray:
    return np.abs((a - b + 180.0) % 360.0 - 180.0)


def clean_air_flags(df: pd.DataFrame, cfg: Config) -> np.ndarray:
    """True where running a genset would contaminate the station's own samples."""
    if not bool(cfg["clean_air.enabled"]):
        return np.zeros(len(df), dtype=bool)

    # meteorological direction is where the wind comes FROM; the plume travels to
    to_dir = (df["wind_dir_deg"].to_numpy() + 180.0) % 360.0
    toward_inlet = _angular_distance(to_dir, float(cfg["clean_air.inlet_bearing_deg"])) <= float(
        cfg["clean_air.sector_halfwidth_deg"]
    )
    speed = df["wind_ms"].to_numpy()
    in_band = (speed >= float(cfg["clean_air.min_wind_ms"])) & (
        speed <= float(cfg["clean_air.max_wind_ms"])
    )
    stagnant = speed < float(cfg["clean_air.min_wind_ms"])
    return (toward_inlet & in_band) | stagnant


def penalty_vector(flags: np.ndarray, cfg: Config) -> np.ndarray:
    """Litre-equivalent price per hour of generator operation inside a clean-air window."""
    if bool(cfg["clean_air.hard_constraint"]):
        return np.where(flags, 1e6, 0.0)
    return np.where(flags, float(cfg["clean_air.penalty_l_per_h"]), 0.0)
