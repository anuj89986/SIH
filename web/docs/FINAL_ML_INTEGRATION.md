# Connecting the demo to the full engine

The browser demo already runs on the engine's real inputs: ERA5 weather for Maitri and the
engine's physics model of the station (`engine/export_demo_weather.py` writes
`dist/data/maitri-2023.js`). Two parts remain deliberately simpler than the engine, and this is
how each would be swapped in.

## 1. Forecasts

Today `forecast()` uses persistence from the real record: wind as last measured, solar and demand
as the same hour yesterday. To use the engine's trained LightGBM quantile forecasts instead,
export them per issue hour and replace `forecast()` with a lookup. Keep one-hour steps and units:

```json
{
  "issued_at": "2023-05-01T00:00Z",
  "model_version": "lightgbm-quantile-1",
  "horizon_hours": 36,
  "steps": [
    { "valid_at": "2023-05-01T01:00Z", "load_kw": 0, "pv_kw": 0, "wind_kw": 0,
      "renewable_low_kw": 0, "renewable_high_kw": 0 }
  ]
}
```

(The zeros only show the schema.) The planner must consume only forecasts issued at or before
its decision time. Replace the fixed 10%–30% planning band with the forecast's own p10/p90.

## 2. Scheduling

The demo's planner is a width-14 beam search. The engine's mixed-integer optimiser (PuLP + HiGHS)
adds generator start/stop and minimum up/down constraints, a seasonal fuel allocation, battery
bounds and the clean-air sampling constraint. It solves in about a second per step, which is too
slow to run a 210-day mission live in a browser; the realistic integration is to precompute
engine runs for a set of station sizes and show them alongside the live demo.

## 3. Evaluation discipline

Compare controllers only on identical inputs. Report fuel together with unserved energy by tier,
critical outage hours, curtailment and end-of-mission battery energy. State whether fuel budgets
and reserves are hard limits or advisory targets. Do not relabel the beam search as an
optimiser, or the planning band as a forecast confidence interval, until the corresponding method
is implemented and evaluated.
