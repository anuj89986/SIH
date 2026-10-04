"""Fast tests over the parts that are easy to get quietly wrong.

    python -m pytest tests -q     (about 20 s, no network after the first ERA5 fetch)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polarems.availability import apply_availability  # noqa: E402
from polarems.cleanair import clean_air_flags  # noqa: E402
from polarems.config import load_config  # noqa: E402
from polarems.dispatch import DispatchInputs, solve_dispatch  # noqa: E402
from polarems.harness import State, allocate_gensets, resolve_hour  # noqa: E402
from polarems.twin import build_twin, genset_fuel_lph, gensets, wind_potential  # noqa: E402
from polarems.weather import fetch_year  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def twin(cfg):
    weather = fetch_year(cfg["station.latitude"], cfg["station.longitude"], 2023)
    return build_twin(weather.head(24 * 40), cfg)


def test_config_unwraps_and_documents(cfg):
    assert isinstance(cfg["pv.kwp"], float)
    rows = cfg.assumptions()
    assert len(rows) > 30
    assert all(r["source"] for r in rows)
    # the tank is a design choice and must be flagged as one
    tank = [r for r in rows if r["parameter"].endswith("tank_litres_at_season_start")][0]
    assert tank["is_assumption"]


def test_config_override_is_isolated(cfg):
    other = cfg.override("pv.kwp", 999.0)
    assert other["pv.kwp"] == 999.0
    assert cfg["pv.kwp"] != 999.0


def test_fuel_curve_is_affine_and_off_is_zero(cfg):
    g = cfg["gensets"][0]
    assert genset_fuel_lph(g, 0.0) == 0.0
    lo, hi = genset_fuel_lph(g, 40.0), genset_fuel_lph(g, 80.0)
    assert hi > lo > 0
    # specific consumption improves with loading: the efficiency-point argument
    assert hi / 80.0 < lo / 40.0


def test_cold_air_density_bonus(cfg):
    """A turbine makes more power in cold dense air at the same wind speed."""
    idx = pd.date_range("2023-06-01", periods=2, freq="h")
    warm = pd.DataFrame(
        {"wind_ms": [10.0, 10.0], "temp_c": [0.0, 0.0], "pressure_hpa": [980.0, 980.0]}, index=idx
    )
    cold = warm.assign(temp_c=[-37.0, -37.0])
    assert wind_potential(cold, cfg).iloc[0] > wind_potential(warm, cfg).iloc[0] * 1.1


def test_clean_air_flags_geometry(cfg):
    """Flagged when the plume travels toward the inlet at a moderate speed."""
    idx = pd.date_range("2023-06-01", periods=3, freq="h")
    inlet = float(cfg["clean_air.inlet_bearing_deg"])
    df = pd.DataFrame(
        {
            # wind FROM (inlet+180) travels TO the inlet -> contaminating
            "wind_dir_deg": [(inlet + 180) % 360, inlet, (inlet + 180) % 360],
            "wind_ms": [5.0, 5.0, 20.0],  # third: too windy, plume disperses
        },
        index=idx,
    )
    flags = clean_air_flags(df, cfg)
    assert flags[0] and not flags[1] and not flags[2]


def test_snow_reduces_available_pv(cfg, twin):
    covered = twin["snow_cover"] > 0.2
    if covered.any():
        assert (twin.loc[covered, "pv_kw"] <= twin.loc[covered, "pv_potential_kw"] + 1e-9).all()
    assert (twin["wind_kw"] <= twin["wind_potential_kw"] + 1e-9).all()


def test_twin_load_tiers_sum_to_total(twin):
    tiers = twin[
        ["load_critical_kw", "load_essential_kw", "load_deferrable_kw", "load_sheddable_kw"]
    ].sum(axis=1)
    assert np.allclose(tiers.to_numpy(), twin["load_kw"].to_numpy())


def test_winter_load_exceeds_summer_per_person(cfg):
    """The signal nobody fitted: heat, not headcount, drives the polar load."""
    weather = fetch_year(cfg["station.latitude"], cfg["station.longitude"], 2023)
    full = build_twin(weather, cfg)
    winter = full.loc["2023-06":"2023-08"]
    summer = full.loc["2023-12":"2023-12"]
    assert winter["load_kw"].mean() / winter["occupancy"].mean() > (
        summer["load_kw"].mean() / summer["occupancy"].mean()
    )
    assert winter["load_essential_kw"].mean() > summer["load_essential_kw"].mean()


def test_allocation_respects_min_load(cfg):
    gens = gensets(cfg)
    p = allocate_gensets(gens, np.array([1, 0]), 10.0)
    assert p[0] >= gens[0]["min_load_frac"] * gens[0]["rated_kw"] - 1e-9
    p = allocate_gensets(gens, np.array([0, 0]), 50.0)
    assert p.sum() == 0.0


def test_resolver_conserves_energy(cfg, twin):
    gens = gensets(cfg)
    state = State(soc_kwh=200.0, fuel_remaining_l=50000.0, gen_on=[0, 0])
    row = twin.iloc[100]
    before = state.soc_kwh
    rec = resolve_hour(row, np.array([1, 0]), 0.0, {}, state, cfg, gens)
    supply = rec["renewable_kw"] + rec["gen_total_kw"] + rec["discharge_kw"]
    demand = rec["served_kw"] + rec["charge_kw"] + rec["curtail_kw"]
    assert abs(supply - demand) < 1e-6
    eta = float(cfg["battery.roundtrip_eff"]) ** 0.5
    assert abs(state.soc_kwh - (before + rec["charge_kw"] * eta - rec["discharge_kw"] / eta)) < 1e-6


def test_dry_tank_stops_the_gensets(cfg, twin):
    gens = gensets(cfg)
    state = State(soc_kwh=200.0, fuel_remaining_l=0.0, gen_on=[1, 0])
    rec = resolve_hour(twin.iloc[100], np.array([1, 1]), 0.0, {}, state, cfg, gens)
    assert rec["gen_total_kw"] == 0.0 and rec["fuel_l"] == 0.0


def test_milp_respects_the_fuel_budget(cfg, twin):
    gens = gensets(cfg)
    seg = twin.iloc[200:224]
    T = len(seg)
    base = dict(
        load_critical=seg["load_critical_kw"].to_numpy(),
        load_essential=seg["load_essential_kw"].to_numpy(),
        load_deferrable=seg["load_deferrable_kw"].to_numpy(),
        load_sheddable=seg["load_sheddable_kw"].to_numpy(),
        renewable=(seg["pv_kw"] + seg["wind_kw"]).to_numpy(),
        reserve_req=np.full(T, 10.0),
        clean_air_penalty=np.zeros(T),
        soc0_kwh=250.0,
        gen_on0=[0, 0],
        fuel_remaining_l=50000.0,
    )
    loose = solve_dispatch(DispatchInputs(fuel_budget_l=5000.0, **base), cfg, gens)
    tight = solve_dispatch(DispatchInputs(fuel_budget_l=120.0, **base), cfg, gens)
    assert loose.status == "Optimal" and tight.status == "Optimal"

    # the allowance is priced, not a fuse: a starved horizon burns less and sheds the
    # cheap tiers, but it is never made infeasible and never browns out critical load
    assert tight.fuel_l.sum() < loose.fuel_l.sum()
    assert tight.shed["sheddable"].sum() + tight.shed["deferrable"].sum() > 0
    assert tight.shed["critical"].sum() < 1e-6
    assert tight.over_budget_l == pytest.approx(max(0.0, tight.fuel_l.sum() - 120.0), abs=1e-3)
    assert loose.over_budget_l == pytest.approx(0.0, abs=1e-6)


def test_milp_honours_min_load_and_commitment(cfg, twin):
    gens = gensets(cfg)
    seg = twin.iloc[400:424]
    T = len(seg)
    res = solve_dispatch(
        DispatchInputs(
            load_critical=seg["load_critical_kw"].to_numpy(),
            load_essential=seg["load_essential_kw"].to_numpy(),
            load_deferrable=seg["load_deferrable_kw"].to_numpy(),
            load_sheddable=seg["load_sheddable_kw"].to_numpy(),
            renewable=(seg["pv_kw"] + seg["wind_kw"]).to_numpy(),
            reserve_req=np.full(T, 10.0),
            clean_air_penalty=np.zeros(T),
            fuel_budget_l=5000.0,
            soc0_kwh=250.0,
            gen_on0=[0, 0],
            fuel_remaining_l=50000.0,
        ),
        cfg,
        gens,
    )
    for g, gen in enumerate(gens):
        on = res.gen_on[g].astype(bool)
        assert (res.gen_kw[g][~on] < 1e-6).all()
        if on.any():
            assert (res.gen_kw[g][on] >= gen["min_load_frac"] * gen["rated_kw"] - 1e-6).all()


def test_clean_air_penalty_moves_the_schedule(cfg, twin):
    """Pricing contamination must change dispatch, or the constraint is decorative."""
    gens = gensets(cfg)
    seg = twin.iloc[600:636]
    T = len(seg)
    base = dict(
        load_critical=seg["load_critical_kw"].to_numpy(),
        load_essential=seg["load_essential_kw"].to_numpy(),
        load_deferrable=seg["load_deferrable_kw"].to_numpy(),
        load_sheddable=seg["load_sheddable_kw"].to_numpy(),
        renewable=(seg["pv_kw"] + seg["wind_kw"]).to_numpy(),
        reserve_req=np.full(T, 10.0),
        fuel_budget_l=5000.0,
        soc0_kwh=380.0,
        gen_on0=[0, 0],
        fuel_remaining_l=50000.0,
    )
    flags = np.zeros(T, dtype=bool)
    flags[8:20] = True
    free = solve_dispatch(DispatchInputs(clean_air_penalty=np.zeros(T), **base), cfg, gens)
    priced = solve_dispatch(
        DispatchInputs(clean_air_penalty=np.where(flags, 200.0, 0.0), **base), cfg, gens
    )
    on_free = free.gen_on.sum(axis=0)[flags].sum()
    on_priced = priced.gen_on.sum(axis=0)[flags].sum()
    assert on_priced <= on_free
