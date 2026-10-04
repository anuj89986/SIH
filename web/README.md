# Pink Monster — Polar Mission Console

**SIH26061 · Fuel-survivability energy management · Team Pink Monster**

**Live:** https://aditya-eng.github.io/SIH26061/

An API-connected browser console for a polar research station. Configure solar, wind, battery and fuel,
compare a rule-based controller with a forecast planner, break things on purpose, inspect every
hourly decision and export the run.

It runs on **real Antarctic weather**: ERA5 reanalysis for India's Maitri station, hourly, from
1 May 2023. Solar, wind and station demand come from the HIMSHAKTI engine's physics model of the
station on that weather. Run Simulation compares the browser rules controller with the real Python
MILP. The bundled browser planner remains a clearly labelled fallback if FastAPI is unavailable.

## Run it

Run two PowerShell terminals (Node.js 20+ and the existing engine virtual environment):

```powershell
cd C:\Users\yeesh\Desktop\SIH26061\engine
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

Restart any older server already on port 8000 to load the API wrapper changes. If API packages
are missing, install them into this environment with
`.\.venv\Scripts\python.exe -m pip install fastapi uvicorn` (the engine requirements must also be installed).

```powershell
cd C:\Users\yeesh\Desktop\SIH26061\web
node scripts/serve.mjs
```

Open http://localhost:3000, select **Run simulation**, and look for **Connected to FastAPI ·
Python MILP results**. The initial saved example is browser-generated; it does not call the API.
The API is http://127.0.0.1:8000; Swagger is http://127.0.0.1:8000/docs.
CORS permits HTTP localhost/127.0.0.1 origins, including ports 3000 and 5500, following the
[FastAPI CORS setup](https://fastapi.tiangolo.com/tutorial/cors/).

No frontend dependency installation is required. `node scripts/serve.mjs` also works if the
system's npm launcher is broken. When npm is available:

```sh
npm run dev        # http://localhost:3000
npm test           # browser + adapter checks; live API tests opt in below
npm run build      # syntax, assets, and the saved example reproduces exactly
```

No frontend install step, API key, database or internet connection is needed — the weather record is
bundled in `dist/data/maitri-2023.js`. Serve over HTTP rather than opening `index.html` directly.

Checks without npm:

```powershell
node --test tests/*.test.mjs
node scripts/check.mjs
$env:POLAREMS_TEST_API='http://127.0.0.1:8000'
node --test tests/optimise.test.mjs
```

The live checks run real MILP solves for the default 30-day mission, all stress scenarios,
custom battery settings, almost empty/empty fuel tanks, response mapping, exports and CORS.

## API mapping and limits

`dist/optimise.js` sends current measured inputs and prior history to the backend,
which forecasts p10/p50/p90 over up to 36 hours, derives reserve, activates clean-air
penalties, and enforces hard tank feasibility separately from the soft fuel allowance.
The browser executes the first measured hour and replans hourly, carrying SOC, fuel,
generator on/off and consecutive elapsed state hours. Outage/restoration boundaries
shorten planning windows. Current critical/essential shares are retained; domestic
maps to sheddable and deferrable is zero. Generator order is **125 kW, 60 kW**.

LightGBM artifacts are trained offline on 2019–2022 physics twins, then loaded once.
Missing models use labeled statistical quantiles. Future weather remains synthetic
lead-degraded ERA5, not operational telemetry. The missed-lull scenario now exposes
actual-minus-forecast errors. The browser risk control affects the fallback only.

The adapter validates status, arrays, quantiles, physical balance, SOC, fuel and
generator limits. It accepts only the measured prefix; JSON exports include full
forecast/optimizer/reserve/clean-air metadata and CSV includes hourly derived fields.
New backend results never use recursive fuel repair; that safeguard remains only
for older servers without hard-fuel capability. Prior Swagger requests remain valid.
See [CONNECTED_MPC.md](../engine/docs/CONNECTED_MPC.md) for complete API fields and limits.

Station physical values remain unchanged. Python uses sqrt(0.90) battery efficiency
and both diesel units; browser rules/fallback retain 0.95 and a single generator
with different fuel curves. Their fuel differences are **not controller savings**.

Offline/CORS/timeout/HTTP/invalid-result failures show an explicit warning and the complete
browser fallback. Cancellation retains the previous result and aborts requests (a solve
already executing server-side may finish). Successful API results say Python MILP in the
controller selector, fuel legend and status banner.

## Demo flow

1. Run the default 30-day mission (1–30 May 2023, 6,300 L tank).
2. Compare fuel, all unserved energy, critical shortfall and end battery for both controllers.
3. Open **Dispatch explorer**: pick an hour and a controller; read the power balance, the
   priority panel and the reason for the decision.
4. Open **Resilience lab** and run the blizzard, generator failure and missed-lull tests. Each is
   placed at the most demanding point of the mission; both controllers are shown side by side.
5. Check the fuel allowance, carry-forward and the four-hour sampling advisory.
6. Open **Model & guide**, then export the hourly CSV and full-run JSON.

The full narrated walkthrough is in [`dist/BUILD_GUIDE.md`](dist/BUILD_GUIDE.md).

## Retained browser baseline/fallback model

| Part | Implementation |
|---|---|
| Weather | ERA5 reanalysis, hourly, Maitri (−70.75, 11.75) |
| Solar / wind | Engine physics model per installed kW × your capacities (snow cover, cold air density, icing included) |
| Demand | Engine physics model: headcount, heat loss at measured temperature, snow-melt water, 20 kW science and life safety |
| Priorities | Critical, essential, domestic from the model; shed domestic first, critical last |
| Diesel | 60 / 125 kW units, one active, 30% minimum load, finite tank |
| Battery | 15–95% charge, power limit, 95% efficiency each way |
| Rules controller | Hourly reaction to measured demand, renewables and battery thresholds |
| Forecast planner | 36-hour, width-14 beam search; re-plans every 6 h and on generator faults; protection layer starts a generator if a measured shortfall exceeds the plan |
| Forecast | Persistence from the real record: latest wind, same-hour-yesterday solar and demand |
| Stress tests | Blizzard (96 h), 125 kW failure (48 h), missed wind lull (48 h), each at the mission's most demanding point |
| Export | Hourly CSV and full JSON with configuration, data source and model version |

The retained browser planner is an approximate search. Connected runs use the trained
forecasts and mixed-integer optimizer described in
[CONNECTED_MPC.md](../engine/docs/CONNECTED_MPC.md).

This is decision support for a hackathon demonstration. It does not control station equipment.

## Project map

```text
dist/
  index.html               entry point
  app.js                   views, charts, explanations, exports
  style.css                interface theme (Figtree bundled in fonts/)
  simulation.js            weather and forecast from the record, dispatch, controllers, accounting
  optimise.js              API request/response mapping, tank checks, sequential horizons
  worker.js                runs the simulation off the UI thread
  data/maitri-2023.js      hourly station record (engine/export_demo_weather.py)
  data/default-run.json    the example shown on load, produced by the current code
  vendor/motion.js         Motion animation library (MIT), bundled for offline use
  BUILD_GUIDE.md           step-by-step demo guide and voiceover
scripts/   serve.mjs (local server), check.mjs (build checks)
tests/     simulation.test.mjs
docs/      FINAL_ML_INTEGRATION.md
```

## Deploy

GitHub Actions publishes `dist/` to GitHub Pages on every push to `main`
(`.github/workflows/deploy-demo.yml` at the repository root). Netlify (`netlify.toml`, base
`web`) and Vercel (`vercel.json`, Root Directory `web`) also work.
