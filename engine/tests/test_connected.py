"""Connected forecast, fuel and horizon boundary regressions; no training/network."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import api
from polarems.config import load_config
from polarems.dispatch import DispatchInputs, reserve_from_quantiles, solve_dispatch
from polarems.forecast import QuantileForecaster
from polarems.liveforecast import ForecastService
from polarems.twin import gensets


def request(hours=6, fuel=1000):
    return dict(load_critical=[20.] * hours, load_essential=[30.] * hours,
                load_deferrable=[0.] * hours, load_sheddable=[10.] * hours,
                renewable=[0.] * hours, reserve_req=[6.] * hours,
                clean_air_penalty=[0.] * hours, fuel_budget_l=1000,
                soc0_kwh=0, gen_on0=[0, 0], fuel_remaining_l=fuel,
                battery_capacity_kwh=0, battery_power_kw=0)


def forecast_options(start="2023-05-01T00:00Z", solar=80, wind=100):
    idx = pd.date_range(pd.Timestamp(start) - pd.Timedelta(hours=24), periods=24, freq="h")
    return dict(start_time=start, solar_capacity_kw=solar, wind_capacity_kw=wind,
                history=[dict(time=str(t), load_kw=60., pv_kw=0., wind_kw=0.) for t in idx])


def run(q):
    return api.optimise(api.OptimiseRequest(**q))


def test_reserve_increases_with_uncertainty_and_has_floor_cap():
    pred = pd.DataFrame(dict(load_kw_p50=[100., 100.], load_kw_p90=[101., 115.],
                             pv_kw_p50=[20., 20.], pv_kw_p10=[19., 10.],
                             wind_kw_p50=[30., 30.], wind_kw_p10=[29., 15.]))
    reserve = reserve_from_quantiles(pred, api.cfg)
    assert reserve == pytest.approx([5., 42.])
    pred.load_kw_p90 = [100., 1000.]
    pred.pv_kw_p10 = pred.pv_kw_p50
    pred.wind_kw_p10 = pred.wind_kw_p50
    assert reserve_from_quantiles(pred, api.cfg) == pytest.approx([2., 50.])
    assert reserve_from_quantiles(pred, api.cfg, False) == pytest.approx([10., 10.])


@pytest.mark.parametrize("fuel", [0., 0.01, 10., 25.])
def test_hard_tank_low_renewable_stress_and_no_negative_fuel(fuel):
    q = request(fuel=fuel)
    q["forecast"] = forecast_options(solar=0, wind=0)
    res = run(q)
    assert res["status"] == "Optimal"
    assert res["fuel"]["total_l"] <= fuel + 1e-5
    assert min(res["fuel"]["remaining_l"]) >= 0
    assert sum(res["load_shed"]["critical"]) > 0
    assert res["fuel"]["hard_limit_enforced"]
    if fuel == 0:
        assert sum(sum(row) for row in res["generator"]["power_kw"]) == 0
        assert sum(res["reserve"]["shortfall_kw"]) > 0


def test_old_swagger_body_still_works_and_soft_budget_is_separate():
    q = request()
    for k in ("battery_capacity_kwh", "battery_power_kw"):
        q.pop(k)
    q["soc0_kwh"] = 260
    q["fuel_budget_l"] = 0
    res = run(q)
    assert res["status"] == "Optimal"
    assert res["forecast"] is None
    assert res["reserve"]["required_kw"] == q["reserve_req"]
    assert res["reserve"]["method"] == "legacy-client-reserve"
    assert res["fuel"]["over_budget_l"] > 0
    assert res["fuel"]["total_l"] <= q["fuel_remaining_l"]


def test_loaded_forecasts_shape_monotone_and_never_fit_per_request(monkeypatch):
    def no_fit(*args, **kwargs):
        raise AssertionError("The request path attempted training")
    monkeypatch.setattr(QuantileForecaster, "fit", no_fit)
    service = ForecastService(api.cfg, ROOT)
    assert service.mode == "lightgbm-quantile"
    q = request(hours=36)
    q["forecast"] = forecast_options()
    res = run(q)
    res2 = run(q)
    assert res["forecast"] == res2["forecast"]
    assert res["forecast_metadata"]["training_years"] == [2019, 2020, 2021, 2022]
    for target in res["forecast"].values():
        assert all(len(v) == 36 for v in target.values())
        assert np.all(np.array(target["p10"]) <= target["p50"])
        assert np.all(np.array(target["p50"]) <= target["p90"])
    # Future client measurements cannot leak into forecasts/optimizer planning.
    for key in ["load_critical", "load_essential", "load_sheddable", "renewable"]:
        q[key][1:] = [999.] * 35
    changed = run(q)
    assert changed["forecast"] == res["forecast"]
    assert changed["optimizer_inputs"] == res["optimizer_inputs"]
    assert res["optimizer_inputs"]["renewable"][0] == q["renewable"][0]


def test_statistical_artifact_and_weather_fallback_are_explicit(tmp_path):
    service = ForecastService(api.cfg, tmp_path)
    assert service.mode == "statistical-persistence"
    pred, fields, meta = service.predict(api.ForecastOptions(**forecast_options("2024-01-01")), 6)
    assert fields is None and len(pred) == 6
    assert meta["weather_source"] == "unavailable"
    assert meta["method"] == "statistical-persistence" and meta["fallback_reason"]


def test_broken_native_model_load_keeps_labeled_fallback(monkeypatch):
    def broken_load(*args, **kwargs):
        raise RuntimeError("native artifact unavailable")
    monkeypatch.setattr(QuantileForecaster, "load", broken_load)
    service = ForecastService(api.cfg, ROOT)
    assert service.mode == "statistical-persistence"
    assert service.reason == "native artifact unavailable"
    pred, fields, meta = service.predict(api.ForecastOptions(**forecast_options()), 6)
    assert len(pred) == 6 and fields is not None
    assert meta["method"] == "statistical-persistence"


def test_clean_air_sensitive_scenario_and_reliability_priority():
    # Find a relevant synthetic forecast window in held-out cached weather.
    from polarems.cleanair import clean_air_flags
    start = None
    for t in pd.date_range("2023-05-01", periods=72, freq="h"):
        fields = api.forecast_service.nwp.forecast(t - pd.Timedelta(hours=1), 6)
        if clean_air_flags(fields, api.cfg)[0]:
            start = str(t)
            break
    assert start is not None
    q = request()
    q["forecast"] = forecast_options(start, solar=0, wind=0)
    res = run(q)
    assert res["clean_air"]["sensitive"][0]
    assert res["clean_air"]["penalty_l_per_gen_h"][0] == api.cfg["clean_air.penalty_l_per_h"]
    assert res["clean_air"]["diesel_discouraged"][0]
    assert res["clean_air"]["diesel_operated"][0]
    assert sum(res["load_shed"]["critical"]) < 1e-5
    # A zero-demand hour can actually avoid diesel inside the sensitive window.
    for k in ["load_critical", "load_essential", "load_sheddable", "reserve_req"]:
        q[k] = [0.] * 6
    q["forecast"]["history"] = [{**h, "load_kw": 0.} for h in q["forecast"]["history"]]
    q.update(battery_capacity_kwh=400, battery_power_kw=100, soc0_kwh=380)
    for key in ["load_critical", "load_essential", "load_deferrable", "load_sheddable", "renewable", "reserve_req", "clean_air_penalty"]:
        q[key] = q[key][:1]
    res = run(q)
    assert res["clean_air"]["diesel_avoided"][0]
    assert res["clean_air"]["soft"]


def single_gen(load, on=0, elapsed=2, fuel=1000):
    cfg = load_config().override("battery.capacity_kwh", 0).override("battery.power_kw", 0)
    n = len(load)
    z = np.zeros(n)
    inp = DispatchInputs(np.array(load, float), z, z, z, z, z, z, 1000., 0., [on], fuel, [elapsed])
    return solve_dispatch(inp, cfg, [gensets(cfg)[1]])


def test_even_tiny_critical_load_wins_over_clean_air_penalty():
    q = request(hours=1)
    q.update(load_critical=[0.005], load_essential=[0.], load_sheddable=[0.],
             reserve_req=[0.], clean_air_penalty=[40.])
    res = run(q)
    assert res["load_shed"]["critical"][0] < 1e-6
    assert res["clean_air"]["diesel_operated"][0]


def test_feasible_incumbent_is_accepted_without_claiming_optimality(monkeypatch):
    import pulp
    original = pulp.HiGHS.actualSolve
    def incumbent_only(self, problem, **kwargs):
        status = original(self, problem, **kwargs)
        problem.sol_status = pulp.LpSolutionIntegerFeasible
        return status
    monkeypatch.setattr(pulp.HiGHS, "actualSolve", incumbent_only)
    res = run(request())
    assert res["status"] == "Feasible"
    assert not res["solver"]["optimality_proven_within_gap"]
    assert res["fuel"]["total_l"] <= res["fuel"]["physical_limit_l"]


def test_first_hour_start_and_min_up_history_across_boundary():
    first = single_gen([40.])
    assert first.gen_on[0, 0] == 1 and first.starts[0, 0] == 1
    following = single_gen([0., 0.], on=1, elapsed=1)
    assert following.gen_on[0].tolist() == [1, 0]
    inside = single_gen([40., 0., 0.])
    assert inside.gen_on[0].tolist() == [1, 1, 0]


def test_min_down_history_and_dry_tank_emergency_shutdown():
    following = single_gen([40., 40.], on=0, elapsed=1)
    assert following.gen_on[0].tolist() == [0, 1]
    dry = single_gen([40., 40.], on=1, elapsed=1, fuel=0)
    assert dry.status == "Optimal" and dry.gen_on[0].sum() == 0
    assert dry.emergency_history_override


def test_api_state_hours_can_be_carried_from_executed_prefix():
    q = request(hours=1)
    q["gen_on0"] = [0, 1]
    q["gen_state_hours0"] = [10, 1]
    res = run(q)
    assert res["generator"]["on"][1][0] == 1
    assert res["generator"]["state_hours"][1][0] == 2
    q["gen_on0"] = [row[0] for row in res["generator"]["on"]]
    q["gen_state_hours0"] = [row[0] for row in res["generator"]["state_hours"]]
    q["fuel_remaining_l"] -= res["fuel"]["total_l"]
    for k in ["load_critical", "load_essential", "load_sheddable", "reserve_req"]:
        q[k] = [0.]
    assert run(q)["status"] == "Optimal"
