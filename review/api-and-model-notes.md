# Connected forecast MPC (API 0.3)

Run Simulation now uses backend-owned forecasting, uncertainty reserve, clean-air
penalties, physical fuel feasibility and elapsed generator commitment history.
Station physical parameters in `config/station.json` were not changed.

## Data flow

The frontend sends the measured current hour, up to 72 strictly earlier measured
history hours, installed solar/wind capacities, battery energy, tank fuel,
generator availability and elapsed on/off hours. The backend forecasts up to 36
hours, solves the MILP, and returns a complete plan. The browser executes only
the first measured hour, updates history/state, and replans each hour. Known
outage/restoration boundaries shorten planning windows. This avoids pretending
future measured station output is a forecast. Legacy transport arrays still
have matching lengths, but their entries after index zero are ignored in
forecast mode. The first-hour p50 prediction remains in metadata for actual
minus forecast error reporting, while optimizer index zero uses measurements.

Tier shares are fixed from current measured load for each planning window.
Critical/essential remain their own tiers; domestic maps to sheddable;
deferrable remains zero. Generator order remains 125 kW, then 60 kW.

## Forecast service and artifact preparation

`src/polarems/liveforecast.py` loads nine native LightGBM model files and a JSON
manifest once at API import/startup, using the existing `QuantileForecaster`.
Requests never fit models or fetch weather over the network. Native text model
loading avoids executable pickle artifacts. The included artifacts were trained
on configured 2019–2022 physics twins (420,249 direct multi-horizon samples and
22 features); held-out 2023 is not in training. Configuration mismatch or missing
artifacts triggers a labeled statistical persistence forecast. Rebuild offline:

```powershell
cd C:\Users\yeesh\Desktop\SIH26061\engine
.\.venv\Scripts\python.exe train_forecast.py
```

Restart the API after replacing artifacts. `train_forecast.py` can fetch training
weather only if the corresponding existing Parquet cache is missing.

Future weather uses the existing `NWP` module: lead-degraded cached 2023 ERA5
reanalysis. This is **synthetic/derived NWP, not real operational telemetry**.
Load and renewable labels are physics-model outputs, not station-meter data.
Occupancy is the configured roster. Predictions normalize measured PV/wind
history to training capacity, then scale predictions to installed capacities.
When cached weather is unavailable for requested dates, forecasts use statistical
persistence and clean-air estimation is explicitly unavailable. The missed-lull
test changes actual wind while NWP still describes undisturbed ERA5 weather, so
forecast error is now returned and visible.

## Reserve and clean-air rules

Operational assumptions are separate in `config/connected.json`:

```
reserve = min(load_p90 - load_p50
              + (pv_p50 + wind_p50) - (pv_p10 + wind_p10)
              + 0.02 * load_p50,
              0.50 * load_p50)
```

Differences are floored at zero. The 2% contingency and 50% load cap are explicit
demo design choices, configurable, and not calibrated operational guarantees.
The sum of component quantiles is not a calibrated net-load quantile. Reserve
shortfall is explicit, with a configurable 100 L-equivalent/kW-hour penalty;
served critical load takes priority when the tank/resources cannot meet reserve.
Missing model artifacts use statistical quantile spreads, with configured load,
PV and wind spread fractions of 20%, 25% and 35%, respectively. Their spreads
are assumptions, not claimed model calibration. Legacy callers retain their
explicit reserve arrays. The offline point-forecast ablation has a configurable
10%-of-load fallback. The browser risk selector controls the browser fallback
only, not backend uncertainty math.

Clean-air flags and penalties reuse `cleanair.py` and unchanged station geometry:
meteorological wind direction is converted to plume direction; inlet-sector and
wind-speed bands or stagnation identify sensitive hours. The connected path
always uses soft mode. The 40 L-equivalent per generator-hour price is the
existing config value. On sensitive horizons the solver first minimizes total
critical shortfall, fixes that achievable minimum (1e-7 kWh numerical tolerance),
then optimizes costs/clean air. Thus avoiding contamination cannot purchase
critical shortfall. `diesel_avoided` means generators stayed off in a flagged
hour; it is not a causal claim against an unpriced counterfactual. No real science
schedule or sampling-load rescheduling has been added.

## Physical fuel and generator history

The MILP now imposes total horizon fuel plus the minimum fuel required for any
minimum-runtime obligation extending beyond the horizon <= remaining tank fuel.
The latter is a feasibility reserve, not fuel already consumed. Seasonal
`fuel_budget_l` remains a separate soft allowance priced via `over_budget_l`.
Start costs remain L-equivalent objective costs (wear/purge proxy), not extra
physical consumption; affine running fuel curves are unchanged.

Empty/almost-empty tanks produce controlled optimal shedding and reserve
shortfall. A fuel shortage or physical unavailability may force a shutdown that
overrides an inherited minimum-runtime obligation; these exceptions are reported.
Diesel surplus under mandatory minimum loading may be dumped; `curtailment_kw`
therefore includes both renewable curtailment and diesel dumping. Minimum loading,
SOC, battery limits and power balance remain checked by the adapter.

`gen_state_hours0` is consecutive completed hours in the corresponding
`gen_on0` state. Missing history assumes minimum dwell has already elapsed,
preserving prior Swagger compatibility. First-hour transitions are now subject
to minimum up/down constraints. Returned `generator.state_hours[g][t]` is the
counter after executing that hour; carry the **executed prefix**, not the end of
an unexecuted plan. Offline engine State/controller mapping also carries history.

The new backend avoids the frontend recursive fuel repair. A new server violating
its hard limit is rejected. Horizon shortening/diesel disabling remain only for
an older server without the hard-limit capability. Browser rules, beam-search
fallback, CORS, cancellation, charts and export entry points are retained.
Offline/CORS/timeouts/HTTP/invalid responses still trigger the clearly labeled
complete browser fallback; a partial plan never replaces a completed result.

## API additions (all old request fields retained)

Optional request fields:

| Field | Meaning |
|---|---|
| `forecast.start_time` | Current measured hour, hourly ISO timestamp (UTC recommended) |
| `forecast.history` | 1–72 ordered unique points `{time, load_kw, pv_kw, wind_kw}`, strictly earlier and ending one hour before start |
| `forecast.solar_capacity_kw`, `forecast.wind_capacity_kw` | Installed capacities; finite 0–500 kW |
| `gen_state_hours0` | Non-negative completed consecutive on/off hours per generator |

Prior optional battery settings and `generator_available` remain supported.
Omitting `forecast` retains explicit client load/renewable/reserve/penalty arrays;
the solver still enforces the hard tank and corrected commitment rules.

Added response fields:

| Field | Meaning |
|---|---|
| `status`, `solver` | Optimal within configured gap or validated Feasible incumbent; native status, actual relative gap, time budget and optimality flag |
| `forecast` | `load_kw`, `pv_kw`, `wind_kw`, `renewable_kw`, each with p10/p50/p90 arrays; null in legacy mode |
| `forecast_metadata` | Method, fallback reason, training years, history/issue/target times, synthetic weather source, operational telemetry flag |
| `optimizer_inputs` | Actual inputs used by MILP, with `current_measured_hours` |
| `reserve` | Required and shortfall kW series, method/config/fallback explanation and coverage caveat |
| `clean_air` | Sensitive flags/hour offsets, per-generator penalties, discouraged/avoided/operated flags, source/soft mode and no causal claim |
| `generator.state_hours` | Elapsed state counters after each hour, per generator |
| `generator.history_assumption` | Explicit history or safe legacy default |
| `generator.emergency_history_override`, `unavailable_history_override` | Reported physical shutdown exceptions |
| `fuel.physical_limit_l`, `remaining_l`, `hard_limit_enforced`, `soft_allowance_l` | Separate physical feasibility and soft allowance metadata |
| `/api/health.capabilities`, `forecast_method`, `forecast_fallback_reason`, `max_horizon_h`, `default_horizon_h` | Adapter capability negotiation and startup forecast state |

Array lengths equal the requested horizon. `sensitive_hours` contains zero-based
offsets; `forecast_metadata.target_times` supplies timestamps. JSON exports retain
complete forecast/planning metadata per solve; hourly CSV gains forecast errors,
reserve/clean-air and generator counter fields automatically.

## Run and verify locally

```powershell
cd C:\Users\yeesh\Desktop\SIH26061\engine
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

The default connected horizon is 24 hours (configurable through
`forecast.default_horizon_h`); up to 36 hours is supported. Small online forecast
batches use one inference thread to avoid parallel scheduling overhead. A cost
solve with zero critical loss already certifies reliability; the extra critical
priority pass runs only when a sensitive horizon has positive critical shortfall.

In a second terminal:

```powershell
cd C:\Users\yeesh\Desktop\SIH26061\web
node scripts/serve.mjs
```

Open http://127.0.0.1:3000, select Run simulation, and inspect Dispatch explorer.
Swagger: http://127.0.0.1:8000/docs. Start the backend before testing; avoid editing
Python during live tests, since the development reloader interrupts requests.

```powershell
cd C:\Users\yeesh\Desktop\SIH26061\engine
.\.venv\Scripts\python.exe -m pytest tests -q
cd ..\web
$env:POLAREMS_TEST_API='http://127.0.0.1:8000'
node --test tests/*.test.mjs
npm run build
```

## Remaining limits

Forecast calibration has not been re-evaluated for the changed connected capacity
scaling, synthetic stress inputs or reserve cap. Old published seasonal results
were not regenerated and should not be presented as results of this new path.
Seasonal allowance in the connected demo is still remaining fuel apportioned
over remaining mission time, not the offline Monte Carlo fuel-plan service.
Clean-air geometry and physical ratings remain stated station assumptions.
Generator availability is known over each split window; no ramp/start delay,
subhour stability, cold battery derating or real hardware control is modeled.
Hourly MPC makes longer missions slower than the former 24-hour batch path. The
connected cost solve has a configurable 0.5 s budget; a time-limited integer
incumbent is reported as Feasible, not Optimal. Every accepted model is checked
against all MILP constraints. If critical shortfall exists, the separate critical
priority pass keeps a 3 s budget and must establish an optimal result (or certify
zero critical loss); otherwise no plan is accepted. Optimal means within the
existing 2% relative gap, not an exact-cost proof. Solver metadata discloses the
native status and actual gap.
Python uses sqrt(0.90) battery efficiency and both sets; browser uses 0.95 and
one set with different fuel curves. Fuel differences are not controller savings.
