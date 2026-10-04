"""MILP dispatch over a rolling horizon (i.e. model predictive control).

Deterministic, optimal within the horizon, explainable, and solved in well under
a second at hourly resolution -- which is why this is a MILP and not a genetic
algorithm or a reinforcement-learning policy. The learned part of the system
enters here as *reserve requirements* derived from the forecast quantiles.

Decision variables per hour: genset commitment and loading, start indicator,
battery charge/discharge and state of charge, curtailment, and tiered load
shedding down to a hard penalty on unserved critical load.
"""

from __future__ import annotations

import time
from threading import Lock
from dataclasses import dataclass

import numpy as np
import pulp

from .config import Config

# Litre-equivalent penalties. Critical load is priced far above any fuel saving,
# so the optimiser will burn the tank before browning out the science hall.
PENALTY = {
    "critical": 5000.0,
    # price per litre burned beyond the seasonal allowance. The allowance is a
    # price signal, not a fuse -- the fuse is the physical tank. Keeping it soft
    # also keeps every horizon feasible and the solve times honest.
    "over_budget": 25.0,
    "essential": 40.0,
    "deferrable": 3.0,
    "sheddable": 8.0,
    "curtail": 0.001,
}


def _solver():
    """HiGHS when available (fast, MIT-licensed), CBC as the portable fallback.

    A 2% relative gap and a 3 s cap keep every step inside the edge-device
    feasibility claim; at this size the gap is almost always closed anyway.
    """
    opts = dict(msg=False, timeLimit=3, gapRel=0.02, threads=4)
    for name in ("HiGHS", "HiGHS_CMD", "PULP_CBC_CMD"):
        try:
            solver = pulp.getSolver(name, **opts)
            if solver.available():
                return solver
        except Exception:  # noqa: BLE001
            continue
    return pulp.PULP_CBC_CMD(msg=False, timeLimit=5, gapRel=0.003)


SOLVER = None
SOLVE_LOCK = Lock()


def get_solver():
    global SOLVER
    if SOLVER is None:
        SOLVER = _solver()
    return SOLVER


@dataclass
class DispatchInputs:
    load_critical: np.ndarray
    load_essential: np.ndarray
    load_deferrable: np.ndarray
    load_sheddable: np.ndarray
    renewable: np.ndarray  # expected available PV + wind (p50)
    reserve_req: np.ndarray  # from forecast spread, kW
    clean_air_penalty: np.ndarray  # litre-equivalent per genset-hour
    fuel_budget_l: float  # litres allowed over this horizon
    soc0_kwh: float
    gen_on0: list[int]
    fuel_remaining_l: float
    # Consecutive elapsed hours in gen_on0's state. None means prior dwell met.
    gen_state_hours0: list[int] | None = None


@dataclass
class DispatchResult:
    gen_kw: np.ndarray  # (n_gen, T)
    gen_on: np.ndarray
    starts: np.ndarray
    charge_kw: np.ndarray
    discharge_kw: np.ndarray
    soc_kwh: np.ndarray
    curtail_kw: np.ndarray
    shed: dict[str, np.ndarray]
    fuel_l: np.ndarray
    objective: float
    solve_time_s: float
    status: str
    over_budget_l: float = 0.0
    reserve_shortfall_kw: np.ndarray | None = None
    emergency_history_override: bool = False
    solver_metadata: dict | None = None


def solve_dispatch(inp: DispatchInputs, cfg: Config, gens: list[dict]) -> DispatchResult:
    T = len(inp.load_critical)
    G = len(gens)
    cap = float(cfg["battery.capacity_kwh"])
    p_bat = float(cfg["battery.power_kw"])
    soc_lo = float(cfg["battery.soc_min"]) * cap
    soc_hi = float(cfg["battery.soc_max"]) * cap
    eta = float(cfg["battery.roundtrip_eff"]) ** 0.5
    deg = float(cfg["battery.degradation_cost_per_kwh"])

    m = pulp.LpProblem("polar_dispatch", pulp.LpMinimize)
    idx = range(T)

    u = [[pulp.LpVariable(f"u_{g}_{t}", cat="Binary") for t in idx] for g in range(G)]
    p = [
        [pulp.LpVariable(f"p_{g}_{t}", lowBound=0, upBound=gens[g]["rated_kw"]) for t in idx]
        for g in range(G)
    ]
    st = [[pulp.LpVariable(f"s_{g}_{t}", lowBound=0, upBound=1) for t in idx] for g in range(G)]

    ch = [pulp.LpVariable(f"ch_{t}", lowBound=0, upBound=p_bat) for t in idx]
    di = [pulp.LpVariable(f"di_{t}", lowBound=0, upBound=p_bat) for t in idx]
    soc = [pulp.LpVariable(f"soc_{t}", lowBound=soc_lo, upBound=soc_hi) for t in idx]
    bres = [pulp.LpVariable(f"br_{t}", lowBound=0, upBound=p_bat) for t in idx]
    cur = [pulp.LpVariable(f"cu_{t}", lowBound=0) for t in idx]
    over = pulp.LpVariable("over_budget", lowBound=0)
    reserve_short = [pulp.LpVariable(f"reserve_short_{t}", lowBound=0) for t in idx]

    shed = {
        "critical": [pulp.LpVariable(f"xc_{t}", lowBound=0) for t in idx],
        "essential": [pulp.LpVariable(f"xe_{t}", lowBound=0) for t in idx],
        "deferrable": [pulp.LpVariable(f"xd_{t}", lowBound=0) for t in idx],
        "sheddable": [pulp.LpVariable(f"xs_{t}", lowBound=0) for t in idx],
    }
    tier_load = {
        "critical": inp.load_critical,
        "essential": inp.load_essential,
        "deferrable": inp.load_deferrable,
        "sheddable": inp.load_sheddable,
    }

    # ---- objective -------------------------------------------------------
    fuel_terms = []
    for g in range(G):
        a = gens[g]["fuel_a_lph_per_kw_rated"] * gens[g]["rated_kw"]
        b = gens[g]["fuel_b_lph_per_kw_out"]
        for t in idx:
            fuel_terms.append(a * u[g][t] + b * p[g][t])
    obj = pulp.lpSum(fuel_terms)
    obj += pulp.lpSum(gens[g]["start_cost_l"] * st[g][t] for g in range(G) for t in idx)
    obj += pulp.lpSum(
        float(inp.clean_air_penalty[t]) * u[g][t] for g in range(G) for t in idx
    )
    obj += pulp.lpSum(deg * (ch[t] + di[t]) for t in idx)
    for tier, var in shed.items():
        obj += pulp.lpSum(PENALTY[tier] * var[t] for t in idx)
    obj += pulp.lpSum(PENALTY["curtail"] * cur[t] for t in idx)
    obj += PENALTY["over_budget"] * over
    # Reserve yields to served critical load when resources run out.
    obj += float(cfg.get("dispatch.reserve_shortfall_penalty", 100.0)) * pulp.lpSum(reserve_short)
    m += obj

    # ---- constraints -----------------------------------------------------
    for t in idx:
        served = pulp.lpSum(tier_load[k][t] - shed[k][t] for k in tier_load)
        m += (
            pulp.lpSum(p[g][t] for g in range(G)) + di[t] - ch[t] + float(inp.renewable[t]) - cur[t]
            == served,
            f"balance_{t}",
        )
        for k in tier_load:
            m += shed[k][t] <= float(tier_load[k][t]), f"shedcap_{k}_{t}"
        # Includes diesel dumping under mandatory minimum loading.
        m += cur[t] <= float(inp.renewable[t]) + pulp.lpSum(p[g][t] for g in range(G)), f"curtcap_{t}"

        for g in range(G):
            m += p[g][t] <= gens[g]["rated_kw"] * u[g][t], f"pmax_{g}_{t}"
            m += p[g][t] >= gens[g]["min_load_frac"] * gens[g]["rated_kw"] * u[g][t], f"pmin_{g}_{t}"
            prev = inp.gen_on0[g] if t == 0 else u[g][t - 1]
            m += st[g][t] >= u[g][t] - prev, f"start_{g}_{t}"
            m += st[g][t] <= u[g][t]
            m += st[g][t] <= 1 - prev

        prev_soc = inp.soc0_kwh if t == 0 else soc[t - 1]
        m += soc[t] == prev_soc + eta * ch[t] - di[t] / eta, f"soc_{t}"

        # battery reserve can only be promised out of stored energy and free power
        m += bres[t] <= p_bat - di[t], f"bres_pow_{t}"
        m += bres[t] <= soc[t] - soc_lo, f"bres_energy_{t}"

        spinning = pulp.lpSum(gens[g]["rated_kw"] * u[g][t] - p[g][t] for g in range(G))
        m += spinning + bres[t] + reserve_short[t] >= float(inp.reserve_req[t]), f"reserve_{t}"

    # minimum up / down time, in the tight pairwise form: a turn-on at t forces
    # u=1 for the next min_up hours, a turn-off forces u=0 for min_down hours.
    # (The aggregated form is valid too but its LP relaxation is far weaker,
    # which turned a millisecond solve into a twenty-second one.)
    remaining_up = []
    for g in range(G):
        elapsed = (inp.gen_state_hours0[g] if inp.gen_state_hours0 is not None
                   else gens[g]["min_up_h"] if inp.gen_on0[g] else gens[g]["min_down_h"])
        remaining_up.append(max(0, gens[g]["min_up_h"] - elapsed) if inp.gen_on0[g] else 0)
    min_burn = [g["rated_kw"] * (g["fuel_a_lph_per_kw_rated"] +
                g["fuel_b_lph_per_kw_out"] * g["min_load_frac"]) for g in gens]
    # A physical shortage can force a shutdown despite a prior runtime obligation.
    emergency = sum(min_burn[g] * remaining_up[g] for g in range(G)) > inp.fuel_remaining_l + 1e-7
    terminal_fuel = []
    for g in range(G):
        up, dn = gens[g]["min_up_h"], gens[g]["min_down_h"]
        elapsed = (inp.gen_state_hours0[g] if inp.gen_state_hours0 is not None else up if inp.gen_on0[g] else dn)
        residual = max(0, (up if inp.gen_on0[g] else dn) - elapsed)
        if not (emergency and inp.gen_on0[g]):
            for t in range(min(T, residual)):
                m += u[g][t] == inp.gen_on0[g], f"initial_dwell_{g}_{t}"
            if inp.gen_on0[g]:
                terminal_fuel.append(min_burn[g] * max(0, residual - T))
        for t in range(T):
            prev = inp.gen_on0[g] if t == 0 else u[g][t - 1]
            for k in range(t + 1, min(T, t + up)):
                m += u[g][k] >= u[g][t] - prev, f"minup_{g}_{t}_{k}"
            for k in range(t + 1, min(T, t + dn)):
                m += 1 - u[g][k] >= prev - u[g][t], f"mindn_{g}_{t}_{k}"
            terminal_fuel.append(min_burn[g] * max(0, up - (T - t)) * st[g][t])

    # seasonal fuel budget: what makes this a survivability problem rather than a
    # cost problem. Exceeding the allowance is allowed at a steep price, so a
    # blizzard cannot make the horizon infeasible -- it just gets expensive.
    m += pulp.lpSum(fuel_terms) <= float(max(inp.fuel_budget_l, 0.0)) + over, "fuel_budget"
    m += pulp.lpSum(fuel_terms) + pulp.lpSum(terminal_fuel) <= float(max(inp.fuel_remaining_l, 0.0)), "physical_tank"

    t0 = time.perf_counter()
    # The reused native solver is not safe for simultaneous FastAPI worker threads.
    with SOLVE_LOCK:
        solver = get_solver()
        cost_limit = float(cfg.get("dispatch.cost_solve_time_limit_s", 3.0))
        solver.timeLimit = cost_limit
        status = m.solve(solver)
        if (pulp.LpStatus[status] == "Optimal" and np.any(inp.clean_air_penalty > 0)
                and sum(float(v.value() or 0.0) for v in shed["critical"]) > 1e-7):
            # Lexicographic reliability: even a tiny critical shortfall must not
            # buy a clean-air hour. First find minimum achievable critical loss.
            critical_loss = pulp.lpSum(shed["critical"])
            m.setObjective(critical_loss)
            solver.timeLimit = float(cfg.get("dispatch.critical_solve_time_limit_s", 3.0))
            priority_status = m.solve(solver)
            if pulp.LpStatus[priority_status] != "Optimal":
                raise RuntimeError("Critical-reliability optimisation did not finish optimally")
            minimum_loss = float(pulp.value(critical_loss) or 0.0)
            if m.sol_status != pulp.LpSolutionOptimal and minimum_loss > 1e-7:
                raise RuntimeError("Critical priority optimum is not established; no plan accepted")
            m += critical_loss <= minimum_loss + 1e-7, "critical_reliability_priority"
            m.setObjective(obj)
            solver.timeLimit = cost_limit
            status = m.solve(solver)
    elapsed = time.perf_counter() - t0
    status_name = pulp.LpStatus[status]
    if status_name == "Optimal" and m.sol_status == pulp.LpSolutionIntegerFeasible:
        status_name = "Feasible"
    if status_name in ("Optimal", "Feasible") and not m.valid(eps=1e-5):
        status_name = "Invalid"
    metadata = {"solution_status": pulp.LpSolution.get(m.sol_status),
                "cost_time_limit_s": cost_limit, "relative_gap_target": 0.02,
                "optimality_proven_within_gap": status_name == "Optimal"}
    if hasattr(m, "solverModel") and hasattr(m.solverModel, "getInfo"):
        info = m.solverModel.getInfo()
        metadata["native_status"] = str(m.solverModel.getModelStatus())
        metadata["relative_gap"] = float(info.mip_gap) if np.isfinite(info.mip_gap) else None

    def val(v):
        x = v.value()
        return 0.0 if x is None else float(x)

    gen_kw = np.array([[val(p[g][t]) for t in idx] for g in range(G)])
    gen_on = np.array([[round(val(u[g][t])) for t in idx] for g in range(G)])
    fuel = np.zeros(T)
    for g in range(G):
        a = gens[g]["fuel_a_lph_per_kw_rated"] * gens[g]["rated_kw"]
        b = gens[g]["fuel_b_lph_per_kw_out"]
        fuel += a * gen_on[g] + b * gen_kw[g]

    return DispatchResult(
        gen_kw=gen_kw,
        gen_on=gen_on,
        starts=np.array([[round(val(st[g][t])) for t in idx] for g in range(G)]),
        charge_kw=np.array([val(ch[t]) for t in idx]),
        discharge_kw=np.array([val(di[t]) for t in idx]),
        soc_kwh=np.array([val(soc[t]) for t in idx]),
        curtail_kw=np.array([val(cur[t]) for t in idx]),
        shed={k: np.array([val(v[t]) for t in idx]) for k, v in shed.items()},
        fuel_l=fuel,
        over_budget_l=val(over),
        reserve_shortfall_kw=np.array([val(v) for v in reserve_short]),
        emergency_history_override=emergency,
        solver_metadata=metadata,
        objective=float(pulp.value(m.objective) or 0.0),
        solve_time_s=elapsed,
        status=status_name,
    )


def reserve_from_quantiles(pred, cfg: Config, use_quantiles: bool = True) -> np.ndarray:
    """The coupling between learning and optimisation.

    Reserve = upside load surprise + downside renewable surprise, both read off
    the forecaster's own predicted spread. With ``use_quantiles=False`` this
    collapses to the conventional fixed 10%-of-load rule -- that switch is the
    ablation that answers "why do you need AI at all?".
    """
    load50 = pred["load_kw_p50"].to_numpy()
    if not use_quantiles:
        return float(cfg.get("reserve.fallback_load_fraction", 0.10)) * load50
    load_up = np.clip(pred["load_kw_p90"].to_numpy() - load50, 0.0, None)
    ren50 = pred["pv_kw_p50"].to_numpy() + pred["wind_kw_p50"].to_numpy()
    ren10 = pred["pv_kw_p10"].to_numpy() + pred["wind_kw_p10"].to_numpy()
    ren_down = np.clip(ren50 - ren10, 0.0, None)
    floor = float(cfg.get("reserve.contingency_load_fraction", 0.02)) * load50
    cap = float(cfg.get("reserve.cap_load_fraction", 0.50)) * load50
    return np.minimum(load_up + ren_down + floor, cap)
