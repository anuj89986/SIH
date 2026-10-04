"""Physical availability, as distinct from weather.

Differentiator 3. The forecast can say clear sky while the array is under 30 cm
of drift, and can say 14 m/s while the blades are iced and shut down. Snow
coverage and ice accretion are state variables with their own dynamics; they
decorrelate from the weather forecast, and that decorrelation is polar-specific.

Documented in de Witt, Chung & Lee (2024): snow accumulation buries panels,
wind-blasted ice and gravel shatters protective glass, blade icing alters
aerodynamics, and active de-icing consumes 2-12% of nameplate power.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config


def snow_coverage(df: pd.DataFrame, cfg: Config) -> np.ndarray:
    """Fraction of the array optically blocked, integrated hour by hour."""
    accum = float(cfg["snow.accum_per_cm_snowfall"])
    clear_rate = float(cfg["snow.wind_clearing_per_ms"])
    v_thr = float(cfg["snow.wind_clear_threshold"])
    melt_above = float(cfg["snow.melt_clear_above_c"])
    clean_every_h = int(cfg["snow.manual_clean_interval_days"]) * 24

    snowfall = df["snowfall_cm"].to_numpy()
    wind = df["wind_ms"].to_numpy()
    temp = df["temp_c"].to_numpy()

    cover = np.zeros(len(df))
    c = 0.0
    for i in range(len(df)):
        c += accum * snowfall[i]
        c -= clear_rate * max(0.0, wind[i] - v_thr)  # drift scouring on steep tilt
        if temp[i] > melt_above:
            c -= 0.05
        if i % clean_every_h == 0 and c > 0.4:  # crew clears the array
            c = 0.05
        c = min(max(c, 0.0), 1.0)
        cover[i] = c
    return cover


def icing_risk(df: pd.DataFrame, cfg: Config) -> np.ndarray:
    """0-1 accretion risk on turbine blades from temperature, humidity and precipitation."""
    t_lo = float(cfg["snow.icing_temp_low_c"])
    t_hi = float(cfg["snow.icing_temp_high_c"])
    temp = df["temp_c"].to_numpy()
    rh = df["rh_pct"].to_numpy()
    snowfall = df["snowfall_cm"].to_numpy()
    wind = df["wind_ms"].to_numpy()

    in_window = (temp >= t_lo) & (temp <= t_hi)
    humid = np.clip((rh - 80.0) / 20.0, 0.0, 1.0)
    precip = np.clip(snowfall / 0.5, 0.0, 1.0)
    flux = np.clip(wind / 15.0, 0.0, 1.0)  # more mass flux, faster accretion
    risk = in_window * np.clip(0.55 * humid + 0.45 * precip, 0.0, 1.0) * (0.5 + 0.5 * flux)

    # ice persists for a few hours after the driving conditions end: decay the
    # accreted state, and never let it fall below what this hour is producing
    series = pd.Series(risk, index=df.index)
    persisted = series.ewm(halflife=4, adjust=False).mean()
    return np.clip(np.maximum(series.to_numpy(), persisted.to_numpy()), 0.0, 1.0)


def apply_availability(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Attach snow_cover, icing_risk and the derated pv_kw / wind_kw actually available."""
    out = df.copy()
    out["snow_cover"] = snow_coverage(df, cfg)
    out["icing_risk"] = icing_risk(df, cfg)

    out["pv_kw"] = out["pv_potential_kw"] * (1.0 - out["snow_cover"])
    derate = 1.0 - float(cfg["snow.icing_derate_max"]) * out["icing_risk"]
    out["wind_kw"] = out["wind_potential_kw"] * derate

    # cost of running blade de-icing this hour, if the controller chooses to
    out["deice_cost_kw"] = (
        float(cfg["wind.deice_kw_frac"])
        * float(cfg["wind.rated_kw"])
        * int(cfg["wind.n_turbines"])
        * (out["icing_risk"] > 0.25)
    )
    # energy recovered if de-icing runs: the derated share comes back
    out["deice_gain_kw"] = out["wind_potential_kw"] - out["wind_kw"]
    return out


def deice_decision(deice_cost_kw: np.ndarray, deice_gain_kw: np.ndarray) -> np.ndarray:
    """Spend nameplate power on de-icing only when the recovered energy exceeds it."""
    return (deice_gain_kw > deice_cost_kw * 1.15) & (deice_cost_kw > 0)
