import sys
import json
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Make engine/src importable
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from polarems.config import load_config, Config
from polarems.twin import gensets
from polarems.dispatch import DispatchInputs, reserve_from_quantiles, solve_dispatch
from polarems.cleanair import clean_air_flags, penalty_vector
from polarems.liveforecast import ForecastService


app = FastAPI(
    title="PolarEMS API",
    version="0.3.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "https://aditya-eng.github.io",
    ],
    allow_credentials=False,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:[0-9]+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load these once when server starts
cfg = load_config()
cfg = Config({**cfg.raw, **json.loads((ROOT / "config" / "connected.json").read_text())}, cfg.path)
gens = gensets(cfg)
forecast_service = ForecastService(cfg, ROOT)


class HistoryPoint(BaseModel):
    time: str
    load_kw: float = Field(ge=0, allow_inf_nan=False)
    pv_kw: float = Field(ge=0, allow_inf_nan=False)
    wind_kw: float = Field(ge=0, allow_inf_nan=False)


class ForecastOptions(BaseModel):
    start_time: str
    history: list[HistoryPoint] = Field(min_length=1, max_length=72)
    solar_capacity_kw: float = Field(ge=0, le=500, allow_inf_nan=False)
    wind_capacity_kw: float = Field(ge=0, le=500, allow_inf_nan=False)


class OptimiseRequest(BaseModel):
    load_critical: list[float]
    load_essential: list[float]
    load_deferrable: list[float]
    load_sheddable: list[float]

    renewable: list[float]
    reserve_req: list[float]
    clean_air_penalty: list[float]

    fuel_budget_l: float
    soc0_kwh: float
    gen_on0: list[int]
    fuel_remaining_l: float

    # Optional web settings. Existing Swagger requests remain compatible.
    battery_capacity_kwh: float | None = Field(default=None, ge=0, le=2000, allow_inf_nan=False)
    battery_power_kw: float | None = Field(default=None, ge=0, le=500, allow_inf_nan=False)
    generator_available: list[bool] | None = None
    gen_state_hours0: list[int] | None = None
    forecast: ForecastOptions | None = None


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "web_integration": 1,
        "engine": "PolarEMS Python Engine",
        "controller": "Quantile MPC + Fuel Budget",
        "capabilities": {"hard_fuel_limit": True, "forecast": True, "generator_history": True},
        "forecast_method": forecast_service.mode,
        "forecast_fallback_reason": forecast_service.reason,
        "max_horizon_h": cfg["forecast.max_horizon_h"],
        "default_horizon_h": cfg["forecast.default_horizon_h"],
    }


@app.post("/api/optimise")
def optimise(req: OptimiseRequest):

    # All horizon arrays must have same length
    lengths = {
        len(req.load_critical),
        len(req.load_essential),
        len(req.load_deferrable),
        len(req.load_sheddable),
        len(req.renewable),
        len(req.reserve_req),
        len(req.clean_air_penalty),
    }

    if len(lengths) != 1 or not len(req.renewable):
        raise HTTPException(
            status_code=400,
            detail="All time-series inputs must have the same length."
        )
    if len(req.renewable) > (cfg["forecast.max_horizon_h"] if req.forecast else 168):
        raise HTTPException(status_code=400, detail="Horizon exceeds supported length.")

    # Validate the transport boundary; do not modify the dispatch solver.
    arrays = [req.load_critical, req.load_essential, req.load_deferrable,
              req.load_sheddable, req.renewable, req.reserve_req, req.clean_air_penalty]
    scalars = [req.fuel_budget_l, req.soc0_kwh, req.fuel_remaining_l]
    if any(not np.all(np.isfinite(a)) or np.any(np.array(a) < 0) for a in arrays + [scalars]):
        raise HTTPException(status_code=400, detail="Inputs must be finite and non-negative.")
    if len(req.gen_on0) != len(gens) or any(v not in (0, 1) for v in req.gen_on0):
        raise HTTPException(status_code=400, detail="gen_on0 must contain one 0/1 per generator.")
    if req.gen_state_hours0 is not None and (len(req.gen_state_hours0) != len(gens) or any(v < 0 for v in req.gen_state_hours0)):
        raise HTTPException(status_code=400, detail="gen_state_hours0 must contain one non-negative elapsed hour count per generator.")
    available = req.generator_available if req.generator_available is not None else [True] * len(gens)
    if len(available) != len(gens):
        raise HTTPException(status_code=400, detail="generator_available must match generator count.")
    request_cfg = cfg
    if req.battery_capacity_kwh is not None:
        request_cfg = request_cfg.override("battery.capacity_kwh", req.battery_capacity_kwh)
    if req.battery_power_kw is not None:
        request_cfg = request_cfg.override("battery.power_kw", req.battery_power_kw)
    capacity = request_cfg["battery.capacity_kwh"]
    if not capacity * request_cfg["battery.soc_min"] - 1e-5 <= req.soc0_kwh <= capacity * request_cfg["battery.soc_max"] + 1e-5:
        raise HTTPException(status_code=400, detail="Initial battery energy is outside its operating band.")
    active = [i for i, enabled in enumerate(available) if enabled]
    request_gens = [gens[i] for i in active]

    T = len(req.renewable)
    tiers = ["critical", "essential", "deferrable", "sheddable"]
    loads = {k: np.array(getattr(req, f"load_{k}"), dtype=float) for k in tiers}
    renewable = np.array(req.renewable, dtype=float)
    reserve = np.array(req.reserve_req, dtype=float)
    penalty = np.array(req.clean_air_penalty, dtype=float)
    forecast = None
    forecast_meta = {"method": "legacy-client-series", "weather_source": "client supplied",
                     "first_hour": "client supplied", "operational_telemetry": False}
    reserve_method = "legacy-client-reserve"
    clean_source = "legacy-client-penalty"
    if req.forecast:
        try:
            pred, fields, forecast_meta = forecast_service.predict(req.forecast, T)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid forecast inputs: {exc}")
        forecast = {target: {q: pred[f"{target}_{q}"].tolist() for q in ("p10", "p50", "p90")}
                    for target in ("load_kw", "pv_kw", "wind_kw")}
        forecast["renewable_kw"] = {q: (pred[f"pv_kw_{q}"] + pred[f"wind_kw_{q}"]).tolist()
                                    for q in ("p10", "p50", "p90")}
        shares = np.array([loads[k][0] for k in tiers])
        shares = shares / shares.sum() if shares.sum() else np.array([1., 0., 0., 0.])
        for k, share in zip(tiers, shares):
            current = loads[k][0]
            loads[k] = pred["load_kw_p50"].to_numpy() * share
            loads[k][0] = current
        renewable = pred["pv_kw_p50"].to_numpy() + pred["wind_kw_p50"].to_numpy()
        renewable[0] = req.renewable[0]
        reserve = reserve_from_quantiles(pred, request_cfg)
        reserve_method = "load-upside + renewable-downside + contingency; capped at load fraction"
        # Always soft in the connected path, regardless of offline hard-mode settings.
        soft_cfg = request_cfg.override("clean_air.hard_constraint", False)
        if fields is not None:
            flags = clean_air_flags(fields, soft_cfg)
            penalty = penalty_vector(flags, soft_cfg)
            clean_source = "forecast wind direction/speed from synthetic ERA5 NWP"
        else:
            penalty = np.zeros(T)
            clean_source = "unavailable weather; explicitly no clean-air estimate"
    flags = penalty > 0
    elapsed = req.gen_state_hours0 or [g["min_up_h"] if on else g["min_down_h"] for g, on in zip(gens, req.gen_on0)]

    inp = DispatchInputs(
        load_critical=loads["critical"],
        load_essential=loads["essential"],
        load_deferrable=loads["deferrable"],
        load_sheddable=loads["sheddable"],

        renewable=renewable,
        reserve_req=reserve,
        clean_air_penalty=penalty,

        fuel_budget_l=req.fuel_budget_l,
        soc0_kwh=req.soc0_kwh,
        gen_on0=[req.gen_on0[i] for i in active],
        fuel_remaining_l=req.fuel_remaining_l,
        gen_state_hours0=[elapsed[i] for i in active],
    )

    try:
        result = solve_dispatch(inp, request_cfg, request_gens)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Optimisation failed: {str(e)}"
        )

    if result.status not in ("Optimal", "Feasible"):
        raise HTTPException(status_code=503, detail=f"Solver returned {result.status}; no dispatch accepted.")
    if not np.isfinite(result.fuel_l).all() or np.sum(result.fuel_l) > req.fuel_remaining_l + 1e-5:
        raise HTTPException(status_code=503, detail="Solver result violates physical tank limit.")

    # Restore the fixed generator ordering for the web dashboard and old clients.
    def expand(values):
        out = np.zeros((len(gens), len(req.renewable)))
        for row, original in enumerate(active):
            out[original] = values[row]
        return out.tolist()

    on_series = np.array(expand(result.gen_on), dtype=int)
    hours = np.zeros_like(on_series)
    for g in range(len(gens)):
        previous, count = req.gen_on0[g], elapsed[g]
        for t in range(T):
            count = count + 1 if on_series[g, t] == previous else 1
            hours[g, t] = count
            previous = on_series[g, t]

    return {
    "status": result.status,
    "solve_time_s": result.solve_time_s,
    "objective": result.objective,
    "solver": result.solver_metadata,

    "generator": {
        "power_kw": expand(result.gen_kw),
        "on": expand(result.gen_on),
        "starts": expand(result.starts),
        "state_hours": hours.tolist(),
        "history_assumption": "elapsed dwell met" if req.gen_state_hours0 is None else "explicit consecutive elapsed hours",
        "emergency_history_override": result.emergency_history_override,
        "unavailable_history_override": [bool(not available[g] and req.gen_on0[g]) for g in range(len(gens))],
    },

    "battery": {
        "charge_kw": result.charge_kw.tolist(),
        "discharge_kw": result.discharge_kw.tolist(),
        "soc_kwh": result.soc_kwh.tolist(),
    },

    "curtailment_kw": result.curtail_kw.tolist(),

    "load_shed": {
        name: values.tolist()
        for name, values in result.shed.items()
    },

    "fuel": {
        "hourly_l": result.fuel_l.tolist(),
        "total_l": float(np.sum(result.fuel_l)),
        "over_budget_l": result.over_budget_l,
        "physical_limit_l": req.fuel_remaining_l,
        "remaining_l": np.maximum(0, req.fuel_remaining_l - np.cumsum(result.fuel_l)).tolist(),
        "hard_limit_enforced": True,
        "soft_allowance_l": req.fuel_budget_l,
    },
    "forecast": forecast,
    "forecast_metadata": forecast_meta,
    "optimizer_inputs": {**{f"load_{k}": v.tolist() for k, v in loads.items()}, "renewable": renewable.tolist(),
                         "current_measured_hours": 1 if req.forecast else T},
    "reserve": {"required_kw": reserve.tolist(), "shortfall_kw": result.reserve_shortfall_kw.tolist(),
                "method": reserve_method, "config": request_cfg["reserve"],
                "fallback_method": "legacy client reserve; statistical quantiles when models unavailable",
                "coverage_claim": "PV/wind quantile sums are conservative components, not calibrated net-load quantiles"},
    "clean_air": {"sensitive": flags.tolist(), "sensitive_hours": np.flatnonzero(flags).tolist(),
                  "penalty_l_per_gen_h": penalty.tolist(), "source": clean_source, "soft": True,
                  "diesel_discouraged": flags.tolist(),
                  "diesel_avoided": (flags & (on_series.sum(axis=0) == 0)).tolist(),
                  "diesel_operated": (flags & (on_series.sum(axis=0) > 0)).tolist(),
                  "causal_avoidance_claim": False},
}
