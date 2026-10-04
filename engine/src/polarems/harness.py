"""Closed-loop season runner.

Every controller is judged on the same weather, the same digital twin and the
same physical resolver. A controller only chooses commitments; the hour is then
settled by physics -- generators respect their min-load band, the battery
respects power and SoC limits, and anything still short is shed in tier order,
critical last. This is what makes the comparison honest: a controller with a
bad forecast pays for it in the resolver, not in a metric it computed itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .controllers import TIERS, Controller
from .fuelbudget import FuelPlan


@dataclass
class State:
    soc_kwh: float
    fuel_remaining_l: float
    fuel_spent_l: float = 0.0
    gen_on: list[int] = field(default_factory=list)
    gen_state_hours: list[int] | None = None


@dataclass
class Context:
    cfg: Config
    gens: list[dict]
    t0: pd.Timestamp
    hours: int
    horizon: int
    index: pd.DatetimeIndex
    history: pd.DataFrame
    truth: pd.DataFrame
    future_truth: pd.DataFrame
    nwp: object
    forecaster: object
    fuel_plan: FuelPlan | None
    state: State


def allocate_gensets(gens: list[dict], on: np.ndarray, target_kw: float) -> np.ndarray:
    """Split a power target across committed sets, cheapest marginal litre first."""
    G = len(gens)
    p = np.zeros(G)
    live = [g for g in range(G) if on[g]]
    if not live:
        return p
    for g in live:
        p[g] = gens[g]["min_load_frac"] * gens[g]["rated_kw"]
    remaining = target_kw - p.sum()
    if remaining <= 0:
        return p  # committed sets are already over-supplying; surplus is handled outside
    for g in sorted(live, key=lambda g: gens[g]["fuel_b_lph_per_kw_out"]):
        room = gens[g]["rated_kw"] - p[g]
        take = min(room, remaining)
        p[g] += take
        remaining -= take
        if remaining <= 1e-9:
            break
    return p


def fuel_burn(gens: list[dict], on: np.ndarray, p: np.ndarray) -> float:
    return float(
        sum(
            (gens[g]["fuel_a_lph_per_kw_rated"] * gens[g]["rated_kw"] + gens[g]["fuel_b_lph_per_kw_out"] * p[g])
            * (1 if on[g] else 0)
            for g in range(len(gens))
        )
    )


def resolve_hour(
    row: pd.Series,
    commit_on: np.ndarray,
    batt_cmd_kw: float,
    shed_frac: dict[str, float],
    state: State,
    cfg: Config,
    gens: list[dict],
    unavailable: np.ndarray | None = None,
) -> dict:
    """Settle one hour of physics under the controller's commitment."""
    cap = float(cfg["battery.capacity_kwh"])
    p_bat = float(cfg["battery.power_kw"])
    soc_lo = float(cfg["battery.soc_min"]) * cap
    soc_hi = float(cfg["battery.soc_max"]) * cap
    eta = float(cfg["battery.roundtrip_eff"]) ** 0.5

    on = commit_on.copy()
    if unavailable is not None:
        on = on * (1 - unavailable)
    if state.fuel_remaining_l <= 0.0:
        on = np.zeros_like(on)  # dry tank: the ultimate constraint

    renew = float(row["pv_kw"] + row["wind_kw"])
    demand = {}
    for tier in TIERS:
        demand[tier] = float(row[f"load_{tier}_kw"]) * (1.0 - float(shed_frac.get(tier, 0.0)))
    planned_shed = sum(
        float(row[f"load_{tier}_kw"]) * float(shed_frac.get(tier, 0.0)) for tier in TIERS
    )
    total_demand = sum(demand.values())

    target = total_demand + max(batt_cmd_kw, 0.0) + min(batt_cmd_kw, 0.0) - renew
    p = allocate_gensets(gens, on, max(target, 0.0))
    gen_total = float(p.sum())

    residual = renew + gen_total - total_demand  # + surplus, - deficit
    charge = discharge = 0.0
    curtail = 0.0
    if residual >= 0:
        charge = min(residual, p_bat, max((soc_hi - state.soc_kwh) / eta, 0.0))
        curtail = residual - charge
    else:
        need = -residual
        discharge = min(need, p_bat, max((state.soc_kwh - soc_lo) * eta, 0.0))
        residual += discharge

    unserved = {tier: 0.0 for tier in TIERS}
    if residual < -1e-6:
        short = -residual
        for tier in ["sheddable", "deferrable", "essential", "critical"]:
            take = min(short, demand[tier])
            unserved[tier] += take
            short -= take
            if short <= 1e-9:
                break

    new_soc = state.soc_kwh + charge * eta - discharge / eta
    burn = fuel_burn(gens, on, p)
    burn = min(burn, state.fuel_remaining_l)

    starts = int(np.sum((on == 1) & (np.array(state.gen_on) == 0)))
    state.soc_kwh = float(np.clip(new_soc, soc_lo, soc_hi))
    state.fuel_remaining_l = max(state.fuel_remaining_l - burn, 0.0)
    state.fuel_spent_l += burn
    elapsed = state.gen_state_hours or [g["min_up_h"] if running else g["min_down_h"]
                                      for g, running in zip(gens, state.gen_on)]
    state.gen_state_hours = [elapsed[g] + 1 if int(on[g]) == state.gen_on[g] else 1 for g in range(len(gens))]
    state.gen_on = [int(x) for x in on]

    record = {
        "pv_kw": float(row["pv_kw"]),
        "wind_kw": float(row["wind_kw"]),
        "renewable_kw": renew,
        "load_kw": float(row["load_kw"]),
        "served_kw": total_demand - sum(unserved.values()),
        "gen_total_kw": gen_total,
        "gen_on_count": int(on.sum()),
        "starts": starts,
        "charge_kw": charge,
        "discharge_kw": discharge,
        "soc_kwh": state.soc_kwh,
        "soc_frac": state.soc_kwh / cap,
        "curtail_kw": curtail,
        "fuel_l": burn,
        "fuel_remaining_l": state.fuel_remaining_l,
        "planned_shed_kw": planned_shed,
        "clean_air_window": bool(row["clean_air_window"]),
        "clean_air_violation_h": float(bool(row["clean_air_window"]) and on.sum() > 0),
    }
    for g in range(len(gens)):
        record[f"gen{g}_kw"] = float(p[g])
        record[f"gen{g}_on"] = int(on[g])
        if on[g]:
            best = 0.85 * gens[g]["rated_kw"]
            record[f"gen{g}_at_eff_point"] = float(abs(p[g] - best) <= 0.10 * gens[g]["rated_kw"])
        else:
            record[f"gen{g}_at_eff_point"] = np.nan
    for tier in TIERS:
        record[f"unserved_{tier}_kwh"] = unserved[tier]
        record[f"served_{tier}_kw"] = demand[tier] - unserved[tier]
    return record


def run_season(
    controller: Controller,
    twin: pd.DataFrame,
    cfg: Config,
    gens: list[dict],
    nwp,
    forecaster,
    fuel_plan: FuelPlan | None = None,
    scenario=None,
    progress: bool = False,
) -> pd.DataFrame:
    """Run one controller over the whole season and return the hourly log."""
    step = int(getattr(controller, "control_step", None) or cfg["simulation.control_step_h"])
    horizon = int(cfg["simulation.horizon_h"])
    cap = float(cfg["battery.capacity_kwh"])
    state = State(
        soc_kwh=0.7 * cap,
        fuel_remaining_l=float(cfg["fuel.tank_litres_at_season_start"]),
        gen_on=[0] * len(gens),
    )

    truth = twin if scenario is None else scenario.apply_to_twin(twin, cfg)
    # the forecaster must see the same world the physics will settle -- including
    # the blizzard -- and must not be asked about hours outside the season
    nwp = nwp.rebase(truth) if hasattr(nwp, "rebase") else nwp
    index = truth.index
    records = []
    plan_diags = []
    warm = 48  # hours of history the controllers may look back on

    for start in range(warm, len(index), step):
        t0 = index[start - 1]
        window = index[start : start + step]
        if len(window) == 0:
            break
        ctx = Context(
            cfg=cfg,
            gens=gens,
            t0=t0,
            hours=len(window),
            horizon=horizon,
            index=window,
            history=truth.loc[:t0].tail(72),
            truth=truth,
            future_truth=truth.loc[index[start] : index[min(start + horizon - 1, len(index) - 1)]],
            nwp=nwp,
            forecaster=forecaster,
            fuel_plan=fuel_plan,
            state=state,
        )
        plan = controller.plan(ctx)
        if plan.diagnostics:
            plan_diags.append({"time": t0, **plan.diagnostics})

        for h, ts in enumerate(window):
            unavailable = None
            if scenario is not None:
                unavailable = scenario.genset_unavailable(ts, len(gens))
            rec = resolve_hour(
                truth.loc[ts],
                plan.gen_on[:, h],
                float(plan.batt_cmd_kw[h]),
                {tier: float(plan.shed_frac[tier][h]) for tier in TIERS},
                state,
                cfg,
                gens,
                unavailable=unavailable,
            )
            rec["time"] = ts
            records.append(rec)
        if progress and start % (24 * 30) < step:
            print(f"   {controller.name}: {t0.date()}  fuel left {state.fuel_remaining_l:,.0f} L")

    log = pd.DataFrame(records).set_index("time")
    log.attrs["controller"] = controller.name
    log.attrs["label"] = getattr(controller, "label", controller.name)
    # kept as plain dicts: a DataFrame in .attrs breaks pandas' attrs comparison
    log.attrs["plan_diagnostics"] = [
        {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in d.items()}
        for d in plan_diags
    ]
    return log
