"""Stress tests. Demonstrate graceful degradation, not only the happy path.

    blizzard        four days of zero solar and turbine cut-out on overspeed
    genset_failure  the primary set is unavailable for 48 h in deep winter
    forecast_bust   twelve hours of badly wrong forecast, truth unchanged

Each is a documented polar failure mode, not an invented worst case: storm
vibration collapsed a turbine at Mario Zucchelli, melt ingress shorted a
generator at Neumayer III, and drift buries arrays routinely.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config


class Scenario:
    name = "none"
    label = "Nominal season"

    def apply_to_twin(self, twin: pd.DataFrame, cfg: Config) -> pd.DataFrame:
        return twin

    def genset_unavailable(self, ts: pd.Timestamp, n_gens: int) -> np.ndarray | None:
        return None


class Blizzard(Scenario):
    name = "blizzard"
    label = "Four-day blizzard: no solar, turbines cut out, heat demand up"

    def __init__(self, start: pd.Timestamp, days: int = 4):
        self.start = pd.Timestamp(start)
        self.days = days

    def apply_to_twin(self, twin: pd.DataFrame, cfg: Config) -> pd.DataFrame:
        out = twin.copy()
        end = self.start + pd.Timedelta(days=self.days)
        mask = (out.index >= self.start) & (out.index < end)
        out.loc[mask, "wind_ms"] = 30.0  # above cut-out
        out.loc[mask, "wind_kw"] = 0.0
        out.loc[mask, "wind_potential_kw"] = 0.0
        out.loc[mask, "snow_cover"] = 1.0
        out.loc[mask, "pv_kw"] = 0.0
        out.loc[mask, "temp_c"] = out.loc[mask, "temp_c"] - 8.0
        extra_heat = (
            8.0
            * float(cfg["thermal.envelope_UA"])
            * (1.0 - float(cfg["thermal.waste_heat_recovery_frac"]))
            * float(cfg["thermal.heat_electrified_frac"])
        )
        out.loc[mask, "load_essential_kw"] += extra_heat
        out.loc[mask, "load_kw"] += extra_heat
        out["net_load_kw"] = out["load_kw"] - out["pv_kw"] - out["wind_kw"]
        return out


class GensetFailure(Scenario):
    name = "genset_failure"
    label = "Primary genset unavailable for 48 h in deep winter"

    def __init__(self, start: pd.Timestamp, hours: int = 48, unit: int = 0):
        self.start = pd.Timestamp(start)
        self.hours = hours
        self.unit = unit

    def genset_unavailable(self, ts: pd.Timestamp, n_gens: int) -> np.ndarray | None:
        if self.start <= ts < self.start + pd.Timedelta(hours=self.hours):
            flags = np.zeros(n_gens, dtype=int)
            flags[self.unit] = 1
            return flags
        return None


class ForecastBustNWP:
    """Wraps the NWP so a 12 h window is badly wrong while the truth is unchanged."""

    def __init__(self, nwp, start: pd.Timestamp, hours: int = 12, factor: float = 4.0):
        self.nwp = nwp
        self.start = pd.Timestamp(start)
        self.hours = hours
        self.factor = factor
        self.truth = nwp.truth

    def rebase(self, truth: pd.DataFrame) -> "ForecastBustNWP":
        return ForecastBustNWP(self.nwp.rebase(truth), self.start, self.hours, self.factor)

    def forecast(self, issue_time: pd.Timestamp, horizon_h: int) -> pd.DataFrame:
        out = self.nwp.forecast(issue_time, horizon_h)
        if len(out) == 0:
            return out
        end = self.start + pd.Timedelta(hours=self.hours)
        mask = (out.index >= self.start) & (out.index < end)
        if mask.any():
            # the forecast promises a benign, windy, sunny window that never arrives
            out.loc[mask, "wind_ms"] = out.loc[mask, "wind_ms"] * self.factor + 6.0
            out.loc[mask, "ghi_wm2"] = out.loc[mask, "ghi_wm2"] * self.factor + 80.0
            out.loc[mask, "temp_c"] = out.loc[mask, "temp_c"] + 9.0
        return out


def default_scenarios(index: pd.DatetimeIndex) -> list[Scenario]:
    """Place each stress test in the part of the season where it hurts most."""
    start = index[0]
    end = index[-1]

    def clamp(ts: pd.Timestamp) -> pd.Timestamp:
        lo = start + pd.Timedelta(days=7)
        hi = end - pd.Timedelta(days=7)
        return min(max(ts, lo), hi)

    year = start.year
    return [
        Blizzard(clamp(pd.Timestamp(year, 7, 12))),
        GensetFailure(clamp(pd.Timestamp(year, 8, 5))),
    ]
