"""Resupply-horizon fuel budgeting: the seasonal survivability allocator.

Differentiator 1, and the reason this is not a commercial EMS in a parka. ABB,
Schneider, Siemens and SMA optimise cost per kWh against a market price or a
refillable tank. A polar station has N litres and no supplier until the ship
comes back. The correct objective is therefore not minimum cost but

    maximise P(critical load served until the resupply date)

subject to the tank. This module solves the outer level of that problem by
Monte Carlo over historical weather years, and hands the daily litre allowance
down to the MILP as a hard constraint. The two levels together are the product.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config


def specific_consumption(gens: list[dict], mean_kw: float) -> float:
    """Litres per kWh when the sets are run sensibly for a given mean load."""
    best = np.inf
    for g in gens:
        loading = min(max(mean_kw, g["min_load_frac"] * g["rated_kw"]), g["rated_kw"])
        if mean_kw > g["rated_kw"] * 1.05:
            continue  # this set alone cannot carry the load
        lph = g["fuel_a_lph_per_kw_rated"] * g["rated_kw"] + g["fuel_b_lph_per_kw_out"] * loading
        best = min(best, lph / max(loading, 1e-6))
    if not np.isfinite(best):  # all sets running flat out
        rated = sum(g["rated_kw"] for g in gens)
        lph = sum(
            g["fuel_a_lph_per_kw_rated"] * g["rated_kw"]
            + g["fuel_b_lph_per_kw_out"] * g["rated_kw"]
            for g in gens
        )
        best = lph / rated
    return float(best)


def daily_fuel_need(twin: pd.DataFrame, gens: list[dict], battery_kwh: float) -> pd.Series:
    """Litres per day a competent operator would burn, given this weather year.

    Battery smoothing is credited at daily resolution: surplus renewable energy
    up to one battery-worth per day displaces diesel. This is deliberately a
    heuristic -- the outer level only needs the *distribution* of need, and the
    inner MILP re-optimises against reality every step.
    """
    net = (twin["load_kw"] - twin["pv_kw"] - twin["wind_kw"]).to_numpy()
    deficit = np.clip(net, 0.0, None)
    surplus = np.clip(-net, 0.0, None)
    frame = pd.DataFrame({"deficit": deficit, "surplus": surplus}, index=twin.index)
    daily = frame.resample("D").sum()
    stored = np.minimum(daily["surplus"].to_numpy() * 0.9, battery_kwh)
    energy = np.clip(daily["deficit"].to_numpy() - stored, 0.0, None)
    mean_kw = energy / 24.0
    litres = np.array(
        [e * specific_consumption(gens, max(m, 1.0)) for e, m in zip(energy, mean_kw)]
    )
    return pd.Series(litres, index=daily.index, name="litres")


@dataclass
class FuelPlan:
    allowance: pd.Series  # litres per day, indexed by date of the simulated season
    available_l: float
    metrics: dict = field(default_factory=dict)
    ensemble: pd.DataFrame | None = None

    def horizon_budget(self, t0: pd.Timestamp, hours: int, spent_so_far_l: float) -> float:
        """Litres the MILP may burn over the next `hours`, with carry-over.

        Under-spending earlier in the season buys headroom later; over-spending
        tightens the next horizon. This is what stops a mild March from being
        eaten by comfort loads and leaving a cold August.
        """
        window = pd.date_range(t0.normalize(), periods=int(np.ceil(hours / 24)) + 1, freq="D")
        window_alw = float(self.allowance.reindex(window).ffill().fillna(0.0).sum())
        window_alw *= hours / (24.0 * len(window))

        planned_to_date = float(self.allowance.loc[: t0.normalize()].sum())
        carry = planned_to_date - spent_so_far_l  # positive means we are ahead of plan

        # Release only a fraction of the carry, and clamp it both ways. Without the upper
        # clamp a frugal autumn hands the optimiser an effectively unlimited winter horizon,
        # which is exactly the failure the allocator exists to prevent; without the lower one
        # a single bad week would starve the station.
        credit = float(np.clip(0.35 * carry, -0.5 * window_alw, 0.5 * window_alw))
        return float(max(window_alw + credit, 0.15 * window_alw))


def build_fuel_plan(
    twins_by_year: dict[int, pd.DataFrame],
    season_index: pd.DatetimeIndex,
    cfg: Config,
    gens: list[dict],
) -> FuelPlan:
    """Monte Carlo over weather years -> a daily litre allowance for the season."""
    tank = float(cfg["fuel.tank_litres_at_season_start"])
    floor = float(cfg["fuel.reserve_floor_litres"])
    target = float(cfg["fuel.survivability_target"])
    battery = float(cfg["battery.capacity_kwh"])
    available = tank - floor

    rows = {}
    for year, twin in twins_by_year.items():
        need = daily_fuel_need(twin, gens, battery)
        # align every ensemble member on day-of-year so seasons are comparable
        need.index = need.index.dayofyear
        rows[year] = need.groupby(level=0).sum()
    ens = pd.DataFrame(rows)  # day-of-year x year
    ens = ens.reindex(range(1, 367)).interpolate(limit_direction="both")

    season_doy = season_index.normalize().dayofyear.unique()
    ens_season = ens.reindex(season_doy).interpolate(limit_direction="both")

    # quantile allocation: hold the target survivability against weather years
    q = ens_season.quantile(target, axis=1)
    total_q = float(q.sum())
    scale = 1.0
    if total_q > available:
        scale = available / total_q  # tank cannot honour the target: report the shortfall
    allowance = q * scale

    yearly_totals = ens_season.sum(axis=0)
    p_dry = float((yearly_totals > available).mean())
    dates = pd.DatetimeIndex(sorted(set(season_index.normalize())))
    allowance_dated = pd.Series(
        allowance.reindex(dates.dayofyear).to_numpy(), index=dates, name="allowance_l"
    )

    metrics = {
        "available_l": available,
        "tank_l": tank,
        "reserve_floor_l": floor,
        "target_survivability": target,
        "ensemble_years": sorted(twins_by_year),
        "mean_year_need_l": float(yearly_totals.mean()),
        "p_target_need_l": total_q,
        "worst_year_need_l": float(yearly_totals.max()),
        "p_run_dry_uncontrolled": p_dry,
        "allocation_scaled_by": scale,
        "budget_binding": scale < 1.0,
        "planned_total_l": float(allowance_dated.sum()),
    }
    return FuelPlan(allowance_dated, available, metrics, ens_season)
