# SIH26061 implementation report

Completed locally on 4 October 2026 in `C:\Users\yeesh\Desktop\SIH26061`.
No commit or push was made. Existing uncommitted integration work was preserved.

## Behavior delivered

1. **Uncertainty reserve:** backend load p90−p50 plus renewable p50−p10 plus a
   configurable 2% load contingency, capped at a configurable 50% of forecast
   load. Reserve requirements, actual reserve shortage and explanations are
   returned. Missing models produce labeled statistical quantiles; legacy
   explicit reserve arrays remain accepted.
2. **Clean-air preference:** existing inlet geometry, wind-direction/speed rules
   and the configured 40 L-equivalent per generator-hour penalty are active.
   Sensitive hours and discouraged/avoided/operated diesel flags are returned.
   If the cost solve leaves critical demand unmet, an additional reliability
   priority pass establishes achievable critical service before pricing clean
   air. A zero-critical-loss cost solve already certifies that priority. Avoided
   diesel is an observed dispatch outcome, not a causal savings claim.
3. **Physical tank:** MILP horizon burn and minimum fuel for runtime obligations
   extending past the horizon must fit remaining physical fuel. The seasonal
   soft allowance and its over-budget price remain separate. Dry tanks return
   controlled shedding/reserve shortfall. No accepted result has negative fuel.
4. **Forecast input:** nine persisted LightGBM models trained offline on
   2019–2022 physics twins load once. No HTTP request trains models. Current
   measured load/renewable values are optimizer hour zero; future inputs use
   p50 forecasts, independent of later client measurement-array entries. p10,
   p50 and p90 plus forecast errors and source metadata are exposed. The
   configurable default horizon is 24 hours; the backend supports 36 hours.
   Only the current measured hour is executed before replanning.
5. **Generator history:** consecutive on/off hours are carried from the executed
   prefix across horizons. The first-hour transition now obeys minimum up/down
   constraints. Physical fuel shortage or unavailability can force a reported
   emergency shutdown. Optional missing history assumes prior dwell was met.

Dashboard charts, browser baseline, original browser fallback, local CORS,
cancellation and CSV/JSON serialization were retained. Forecast/reserve/clean-air
details were added to the existing dispatch panels. The browser risk selector
now explicitly applies to the browser fallback. New backend results bypass
recursive fuel repair; repair remains only as a safeguard for older servers.

## Exactly which repository files changed in this task

The following eleven existing files were modified, including existing untracked
integration files. Paths are absolute:

```text
C:\Users\yeesh\Desktop\SIH26061\README.md
C:\Users\yeesh\Desktop\SIH26061\engine\api.py
C:\Users\yeesh\Desktop\SIH26061\engine\src\polarems\dispatch.py
C:\Users\yeesh\Desktop\SIH26061\engine\src\polarems\forecast.py
C:\Users\yeesh\Desktop\SIH26061\engine\src\polarems\controllers.py
C:\Users\yeesh\Desktop\SIH26061\engine\src\polarems\harness.py
C:\Users\yeesh\Desktop\SIH26061\web\README.md
C:\Users\yeesh\Desktop\SIH26061\web\dist\app.js
C:\Users\yeesh\Desktop\SIH26061\web\dist\optimise.js
C:\Users\yeesh\Desktop\SIH26061\web\dist\BUILD_GUIDE.md
C:\Users\yeesh\Desktop\SIH26061\web\tests\optimise.test.mjs
```

The following fifteen files were added (including the ten artifact files):

```text
C:\Users\yeesh\Desktop\SIH26061\engine\config\connected.json
C:\Users\yeesh\Desktop\SIH26061\engine\src\polarems\liveforecast.py
C:\Users\yeesh\Desktop\SIH26061\engine\train_forecast.py
C:\Users\yeesh\Desktop\SIH26061\engine\tests\test_connected.py
C:\Users\yeesh\Desktop\SIH26061\engine\docs\CONNECTED_MPC.md
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\manifest.json
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\load_kw_p10.txt
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\load_kw_p50.txt
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\load_kw_p90.txt
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\pv_kw_p10.txt
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\pv_kw_p50.txt
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\pv_kw_p90.txt
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\wind_kw_p10.txt
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\wind_kw_p50.txt
C:\Users\yeesh\Desktop\SIH26061\engine\data\forecast_models\wind_kw_p90.txt
```

Station physical configuration was unchanged. Other changes already present when
this task began were left alone: the generated engine dashboard/results, engine
requirements, browser simulation implementation and build-check script. These
still appear in Git status but were not modified by this task.

## API changes and backward compatibility

All previous required request fields remain valid. Omitting the new `forecast`
object preserves explicit client-series dispatch (with corrected fuel/history
constraints). Generator order remains 125 kW, then 60 kW.

| Optional request addition | Contents |
|---|---|
| `forecast` | `start_time`, ordered prior `history` points `{time, load_kw, pv_kw, wind_kw}`, `solar_capacity_kw`, `wind_capacity_kw` |
| `gen_state_hours0` | Non-negative completed consecutive hours in each corresponding `gen_on0` state |

History contains 1–72 strictly prior points and ends one hour before start.
Forecast horizons support up to 36 hours. Prior optional battery settings and
generator availability are still accepted.

| Response addition | Contents |
|---|---|
| `status`, `solver` | Optimal or validated Feasible; native status, actual gap, configured time budget and whether optimality was proven within tolerance |
| `forecast` | p10/p50/p90 arrays for load, PV, wind and summed renewables; null for legacy callers |
| `forecast_metadata` | Method, fallback reason, training years, history/issue/target times and synthetic weather/telemetry disclosure |
| `optimizer_inputs` | Load tiers and renewable series actually optimized; measured-prefix length |
| `reserve` | Required and shortfall kW series; method, config, fallback and coverage caveat |
| `clean_air` | Sensitive flags/hour offsets, penalty series, diesel discouraged/avoided/operated flags, source and soft-mode metadata |
| `generator.state_hours` | Elapsed state counters after each plan hour; carry the executed hour, not the plan end |
| `generator.history_assumption` | Explicit counter or prior-dwell-met legacy default |
| `generator.emergency_history_override` / `unavailable_history_override` | Physical shutdown exceptions |
| `fuel.physical_limit_l`, `remaining_l`, `hard_limit_enforced`, `soft_allowance_l` | Physical tank feasibility separately from soft seasonal allowance |
| Health endpoint additions | Capability flags, forecast mode/fallback reason, maximum and default horizons |

A time-limited integer incumbent is now honestly labeled **Feasible**, rather than
Optimal. The updated adapter accepts either successful status. Every accepted
MILP model is validated against all constraints; no-feasible-solution cases
retain the existing controlled-error/browser-fallback behavior. The original
generator/battery/shedding/fuel fields remain present. Full JSON
exports preserve complete planning metadata; hourly CSV gains derived forecast
errors, reserve/clean-air fields and generator counters through the existing
column-union exporter.

## Fallback behavior

- Missing, incompatible or broken model artifacts use a labeled statistical
  persistence forecast, with configured assumed uncertainty spreads; no training
  occurs during a request.
- Unavailable cached weather dates use statistical forecasts and explicitly
  unavailable clean-air estimation; real wind telemetry is not fabricated.
- Requests without `forecast` keep supplied legacy reserve/penalty arrays.
- An older API can use the legacy adapter fuel safeguard. A new API violating
  its claimed hard tank limit is rejected.
- Offline/CORS/timeout/HTTP/invalid-response cases still show the complete,
  clearly labeled original browser fallback. Cancellation retains the previous
  completed run. Failed/partial backend results never replace a completed run.

## Test and build results

- Python: **29 passed**, including all original 14 tests and the connected
  forecast/fuel/reserve/clean-air/history regressions. Final run: 8.14 seconds.
- Frontend and live API: **16 passed, 0 failed, 0 skipped**, including all
  six adapter/live-API checks and ten original simulator checks. Complete run:
  467.69 seconds. The 30-day/720-hour live mission check took 365.28 seconds;
  all disruption/custom-battery cases took 83.11 seconds; low-fuel cases took
  18.85 seconds; CORS/malformed-input checks took 0.12 seconds.
- Build: **passed** (`npm run build`): syntax, entry assets and exact reproduction
  of the original saved browser example.
- Whitespace verification: **passed** (`git diff --check`); only line-ending
  normalization warnings on existing Windows working copies.
- Browser: final **Run simulation** button completed against port 8000;
  status/controller displayed Python MILP and the dispatch panel displayed
  LightGBM forecasts, forecast errors, reserve and clean-air metadata.
- Native LightGBM artifacts were produced once from 420,249 samples / 22
  features, excluding held-out 2023. Request-time fitting is prohibited by a
  regression test. Statistical and broken-artifact fallbacks are tested.

Stress coverage includes zero renewables with 0, 0.01, 10 and 25 L tanks,
non-negative remaining fuel, sensitive wind windows with active 40 L-equivalent
penalties, actual diesel avoidance with battery reserve, critical service even
for a 0.005 kW load, and minimum up/down transitions across one-hour boundaries.
Legacy Swagger compatibility and independence from future measurement arrays
are tested. The live suite covers the original 30-day mission/export mapping,
outage/restoration, blizzard, missed lull, custom battery settings, empty/almost
empty tanks, malformed inputs and local CORS.

During development, a clean-air avoidance fixture was corrected to provide
reserve-capable battery headroom. Earlier live attempts interrupted by the
development reloader were repeated. Long diagnostic runs were stopped while
tuning runtime; only the final completed run above is counted as passing.

The browser automation's attempt to capture a downloaded JSON file timed out;
the run remained intact. The export serialization/mapping checks were run
automatically; downloaded-file capture is not claimed as verified.

## Run locally

Backend terminal:

```powershell
cd C:\Users\yeesh\Desktop\SIH26061\engine
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

Frontend terminal:

```powershell
cd C:\Users\yeesh\Desktop\SIH26061\web
node scripts/serve.mjs
```

Open http://127.0.0.1:3000 and select **Run simulation**. Swagger is available at
http://127.0.0.1:8000/docs. The saved initial example remains browser-generated.
The included artifacts are already prepared; no retraining is needed to run.
To rebuild them after station/configuration changes, run the offline command
`.\.venv\Scripts\python.exe train_forecast.py` from the engine directory and
restart the API. Keep Python files unchanged during live tests when using the
development reloader, since reloading interrupts requests.

## Honest modeling limits

- Forecast weather is synthetic, lead-degraded cached ERA5; load/generation
  labels come from physics twins, not operational electrical telemetry.
- Capacity scaling, stress cases and the reserve contingency/cap have not been
  independently calibrated. Sum-of-component quantiles is not a calibrated
  net-load quantile. Statistical fallback spreads are stated assumptions.
- Connected seasonal allowance remains remaining fuel apportioned over mission
  time; it does not activate the offline Monte Carlo fuel-plan service.
- Current measured tier shares apply over each horizon; deferrable demand is
  zero. There is no operational science schedule/flexible sampling load.
- Runtime obligations beyond horizon boundaries reserve minimum fuel. Actual
  fuel shortage or equipment unavailability can force a reported dwell override.
- Start prices are L-equivalent wear/purge proxies, not added physical burn.
  Curtailment can include diesel dumping under mandatory minimum loading.
- HiGHS retains its existing 2% relative gap target. Connected cost solves have a
  configurable 0.5-second cap; critical priority keeps a separate 3-second cap.
  Validated time-limited incumbents are Feasible and expose their actual gap; accepted
  feasibility and critical priority use numerical tolerances, not an operational
  safety certification. No ramp/start-delay/subhour/cold-battery/hardware model
  has been added.
- Hourly MPC is slower than 24-hour batch planning. The default was set to 24
  hours, with 36 supported, and small-batch inference uses one thread. The verified
  30-day default mission took about six minutes on this computer; a shorter
  mission is more suitable for an interactive demonstration.
- Python and browser still use different fuel curves, battery efficiencies
  (sqrt(0.90) versus 0.95) and generator operation. Their fuel differences are
  **not controller savings**. Historical seasonal benchmark outputs were not
  regenerated and must not be claimed as results of this new connected path.

Detailed API/model notes and a final browser screenshot accompany this report.
