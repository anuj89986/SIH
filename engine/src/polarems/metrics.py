"""Metrics. The headline number is unserved critical energy, not fuel percent.

Fuel savings are contestable (they are savings against our own twin). "The
baseline browns out three times in August and ours does not" is not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config

TIERS = ["critical", "essential", "deferrable", "sheddable"]


def summarise(log: pd.DataFrame, cfg: Config, gens: list[dict]) -> dict:
    hours = len(log)
    days = hours / 24.0
    fuel = float(log["fuel_l"].sum())
    renewable = float(log["renewable_kw"].sum())
    curtailed = float(log["curtail_kw"].sum())

    unserved_crit = float(log["unserved_critical_kwh"].sum())
    outage_hours = int((log["unserved_critical_kwh"] > 0.05).sum())
    # an outage event is a contiguous run of unserved critical hours
    flag = (log["unserved_critical_kwh"] > 0.05).to_numpy().astype(int)
    events = int(np.sum(np.diff(np.concatenate([[0], flag])) == 1))

    runtime = {}
    eff_point = []
    for g in range(len(gens)):
        on = log[f"gen{g}_on"].to_numpy()
        runtime[gens[g]["id"]] = int(on.sum())
        at_eff = log[f"gen{g}_at_eff_point"].to_numpy()
        eff_point.append(np.nanmean(at_eff) if np.any(on) else np.nan)

    diags = log.attrs.get("plan_diagnostics") or []
    solve_times = [d["solve_time_s"] for d in diags if "solve_time_s" in d]
    solve_ms = float(np.mean(solve_times) * 1000) if solve_times else float("nan")
    solve_ms_p95 = float(np.percentile(solve_times, 95) * 1000) if solve_times else float("nan")

    mean_daily = fuel / max(days, 1e-9)
    remaining = float(log["fuel_remaining_l"].iloc[-1])

    return {
        "controller": log.attrs.get("controller", "?"),
        "label": log.attrs.get("label", "?"),
        "hours": hours,
        "fuel_l": fuel,
        "fuel_per_day_l": mean_daily,
        "fuel_remaining_l": remaining,
        "days_of_fuel_left": remaining / max(mean_daily, 1e-9),
        "ran_dry": bool(remaining <= 1.0),
        "unserved_critical_kwh": unserved_crit,
        "critical_outage_hours": outage_hours,
        "critical_outage_events": events,
        "unserved_essential_kwh": float(log["unserved_essential_kwh"].sum()),
        "shed_planned_kwh": float(log["planned_shed_kw"].sum()),
        "renewable_available_kwh": renewable,
        "renewable_used_kwh": renewable - curtailed,
        "renewable_utilisation_pct": 100.0 * (renewable - curtailed) / max(renewable, 1e-9),
        "curtailed_kwh": curtailed,
        "genset_runtime_h": runtime,
        "genset_total_runtime_h": int(sum(runtime.values())),
        "starts": int(log["starts"].sum()),
        "pct_runtime_at_efficiency_point": float(100 * np.nanmean(eff_point))
        if len(eff_point)
        else float("nan"),
        "battery_throughput_kwh": float(log["discharge_kw"].sum()),
        "battery_equivalent_full_cycles": float(
            log["discharge_kw"].sum() / float(cfg["battery.capacity_kwh"])
        ),
        "clean_air_violation_hours": int(log["clean_air_violation_h"].sum()),
        "clean_air_windows": int(log["clean_air_window"].sum()),
        "clean_air_compliance_pct": 100.0
        * (1 - log["clean_air_violation_h"].sum() / max(log["clean_air_window"].sum(), 1e-9)),
        "mean_solve_time_ms": solve_ms,
        "p95_solve_time_ms": solve_ms_p95,
    }


def comparison_table(summaries: list[dict]) -> pd.DataFrame:
    """Report every controller against the weak baseline A and the strong baseline B."""
    frame = pd.DataFrame(summaries).set_index("controller")
    base_a = frame.loc["A", "fuel_l"] if "A" in frame.index else np.nan
    base_b = frame.loc["B", "fuel_l"] if "B" in frame.index else np.nan
    oracle = frame.loc["D", "fuel_l"] if "D" in frame.index else np.nan

    frame["fuel_vs_A_pct"] = 100.0 * (frame["fuel_l"] - base_a) / base_a
    frame["fuel_vs_B_pct"] = 100.0 * (frame["fuel_l"] - base_b) / base_b
    # how much of the achievable gap between B and the oracle we actually closed
    frame["gap_to_oracle_closed_pct"] = 100.0 * (base_b - frame["fuel_l"]) / max(base_b - oracle, 1e-9)
    return frame


def headline(frame: pd.DataFrame) -> dict:
    """The three sentences that go on the results slide."""
    if "C" not in frame.index:
        return {}
    c = frame.loc["C"]
    out = {
        "fuel_vs_B_pct": float(c["fuel_vs_B_pct"]),
        "fuel_vs_A_pct": float(c["fuel_vs_A_pct"]),
        "gap_to_oracle_closed_pct": float(c["gap_to_oracle_closed_pct"]),
        "critical_outage_events_C": int(c["critical_outage_events"]),
        "critical_outage_events_B": int(frame.loc["B", "critical_outage_events"])
        if "B" in frame.index
        else None,
        "clean_air_compliance_C_pct": float(c["clean_air_compliance_pct"]),
        "clean_air_compliance_B_pct": float(frame.loc["B", "clean_air_compliance_pct"])
        if "B" in frame.index
        else None,
        "mean_solve_time_ms": float(c["mean_solve_time_ms"]),
    }
    if "C-point" in frame.index:
        out["ablation_point_forecast_outages"] = int(frame.loc["C-point", "critical_outage_events"])
        out["ablation_point_forecast_fuel_pct_vs_C"] = float(
            100.0 * (frame.loc["C-point", "fuel_l"] - c["fuel_l"]) / c["fuel_l"]
        )
    return out
