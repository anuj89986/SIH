# Connected Run Simulation update

Run Simulation now uses 24-hour backend LightGBM quantile forecasts, executes
the current measured hour, and replans hourly. Reserve follows forecast spread,
clean-air penalties follow forecast wind, and the MILP enforces a hard tank limit
plus generator runtime/downtime history. Models are loaded once; synthetic NWP is
derived from cached ERA5. Missing artifacts use labeled statistical forecasts.
The browser reserve selector controls only the retained fallback. Python/browser
fuel differences are not controller savings. The initial saved example remains
browser-generated; the historical guide below describes that retained browser model.
Full implementation/API notes: engine/docs/CONNECTED_MPC.md in the repository.

# Pink Monster — Build & Demo Guide

**Project:** Fuel-survivability energy management for polar research stations
**Problem statement:** SIH26061 (Ministry of Earth Sciences / NCPOR)
**Team:** Pink Monster
**Weather:** ERA5 reanalysis, India's Maitri station, from 1 May 2023

## 1. What this demo shows

An offline decision-support console that coordinates diesel, solar, wind and a battery to keep
critical station loads supplied until the next fuel delivery. Everything runs on real hourly
Antarctic weather, and every number on screen is computed in the browser from your inputs.

| Input | Source |
|---|---|
| Weather (temperature, wind, sunlight) | ERA5 reanalysis (ECMWF/Copernicus), hourly, Maitri grid point −70.75, 11.75 |
| Solar output | Plane-of-array model at 70° tilt with snow cover on the panels, scaled to the solar capacity you enter |
| Wind output | Turbine power curve corrected for cold, dense air and blade icing, scaled to the wind capacity you enter |
| Station demand | Physics model: headcount, building heat loss at the measured temperature, snow melting for water, a constant 20 kW science and life-safety load |

The four views are Mission overview, Dispatch explorer, Resilience lab and Model & guide.

## 2. Run it

Node.js 20 or later. From the `web/` folder:

```sh
npm run dev        # http://localhost:3000
npm test           # 10 checks: energy, fuel, limits, priorities, faults, real-data guards
npm run build      # syntax, assets, and the saved example reproduces exactly
```

Serve over HTTP; opening `index.html` as a `file://` page will not load the modules or data.
No internet connection, API key or install step is needed — the weather is bundled.

## 3. The demonstration, step by step

### A. Configure and run
Defaults: 30 days from 1 May 2023, 6,300 L of fuel, 80 kW solar, 100 kW wind, a 400 kWh battery
with a 100 kW power limit, 65% starting charge, Balanced reserve, Polar winter.

The 6,300 L tank is deliberately tight: just under what the rule-based controller burns in an
ordinary 30-day winter at this station, so fuel is a real constraint.

Click **Run simulation**. Both controllers run on exactly the same hours in a background worker
(about 1–2 seconds). The example shown on page load was produced by the same code with the
default inputs.

### B. Mission overview
Fuel left, critical demand served, all unmet demand and the battery at the end. The chart compares
both controllers against a straight-line fuel budget; the table adds unmet energy by priority and
end-of-mission battery energy. Read fuel together with shortages and the battery — a controller
that saves fuel by shedding load has not saved anything.

At the defaults the rule-based controller runs the tank dry and sheds essential and domestic
demand; the forecast planner finishes with fuel to spare and nothing shed.

### C. Dispatch explorer
Move the hour slider and pick a controller. Each hour shows solar, wind, the active diesel unit and
battery flow against demand, the plain-English reason for the decision, and whether the planner
re-planned. The priority panel shows that hour's critical, essential and domestic demand from the
station model and any shortage in each. Domestic demand is shed first, then essential, critical
last.

The forecast panel shows what the planner expected: wind as last measured when the plan was made,
solar as the same hour the day before, with a planning band that widens from 10% to 30% over
36 hours.

### D. Fuel budgeting
The starting daily allowance is fuel divided by mission days. Unused allowance carries forward;
the revised allowance is remaining fuel over remaining time. The planner applies a scarcity cost
from it — advisory, not a hard cap.

### E. Resilience lab
Each button re-runs both controllers with one thing broken. Each disruption is placed at the most
demanding point of the mission, found from the real data:

| Test | What changes | Where it is placed |
|---|---|---|
| Polar winter | Nothing — real weather as recorded | — |
| Four-day blizzard | Solar and wind drop to zero for 96 hours | the highest-demand 96 hours |
| Generator failure | The 125 kW unit is out for 48 hours; the 60 kW unit remains | where demand most exceeds wind and solar |
| Forecast misses a lull | Wind falls to 3% for 48 hours; the forecast still expects normal wind | where wind is normally strongest |

The results table shows both controllers side by side: critical, essential and domestic energy
lost, fuel left, and when the first critical shortfall happened. At the default station every test
costs something, and the planner loses less than the rule-based controller in each.

### F. Sampling-window advisory
The best four-hour window on the first day by forecast renewable margin — a suggestion for when an
air-sampling task would be least affected by generator exhaust. It does not move a real task.

### G. Exports
**Hourly CSV** and **Full run JSON** contain the latest completed run: every hour, both
controllers, the configuration, the disruption window and the data source.

## 4. Implemented model

| Component | Behaviour |
|---|---|
| Duration | 1–210 days from 1 May 2023, one-hour steps |
| Weather | ERA5 reanalysis, hourly |
| Solar / wind | Physics model output per installed kW × the capacity you enter |
| Demand | Physics model; tiers from the model: ~24% critical, ~65% essential, ~11% domestic on average |
| Diesel | One of 60 / 125 kW; minimum output 30% of rating |
| Fuel curves (assumed) | 60 kW: `2.1 + 0.24 × P` L/h; 125 kW: `3.5 + 0.25 × P` L/h |
| Low tank | Output limited by fuel; the unit stops if it cannot run at minimum load for an hour |
| Battery | 15–95% state of charge; power limit; 95% charge and discharge efficiency |
| Rules controller | Reacts every hour to measured demand, renewables and battery thresholds |
| Forecast planner | Width-14 beam search over seven generator actions, 36 hours ahead; re-plans every 6 hours and on any change in generator availability |
| Protection layer | If the measured shortfall exceeds what the plan and battery can supply, the smallest suitable generator starts and the plan is redone |
| Forecast | Persistence: wind as last measured, solar and demand as the same hour yesterday |
| Reserve targets | Lean 25%, Balanced 35%, Conservative 45% of battery capacity |

```text
PV + wind + diesel + battery discharge = served demand + battery charge + curtailment
E_next = E + 0.95 × charge − discharge / 0.95
```

The planner's shortage penalties are 10,000,000 / 100,000 / 10,000 per kWh for critical /
essential / domestic demand. It is an approximate search, not a proof of optimality; the full
HIMSHAKTI engine uses a mixed-integer optimiser.

Not modelled: sub-hour frequency, the heat network, ramp rates, start delays, minimum up/down
time, cold battery derating, sensor feedback or equipment control. This is decision support,
not a controller for station equipment.

## 5. Relationship to the full engine

The HIMSHAKTI engine (`../engine`) runs the same station on ERA5 2015–2023 with trained LightGBM
quantile forecasts, a mixed-integer optimiser and a seasonal fuel allocator. This browser demo
uses the engine's weather and physics so it can respond live; its planner and forecast are
deliberately simpler. Quote engine results from the engine and demo results from the demo.

## 6. Suggested voiceover (about three minutes)

**0:00–0:25 — Problem.** “A polar station gets fuel once a year. Pink Monster decides how to spend
it so critical power never runs out. This is real Antarctic weather — ERA5 for India's Maitri
station, May 2023.”

**0:25–0:55 — Run and compare.** “Same month, same weather, two controllers. The rule-based one
runs the tank dry and sheds load; the forecast planner finishes with fuel left and nothing shed.”

**0:55–1:30 — An hour up close.** “Here is any hour: what the wind and sun gave, what the battery
and diesel did, and why. Critical loads are protected last.”

**1:30–2:10 — Bad days.** “A four-day blizzard at the worst moment of the month. Both controllers
lose power; the planner loses less. The same holds when the big generator fails or the forecast
misses a lull.”

**2:10–2:35 — Exports.** “Every hourly decision exports as CSV, the whole run as JSON.”

**2:35–3:00 — The full engine.** “Behind this sits the HIMSHAKTI engine: trained forecasts and an
optimiser over nine years of weather.”

## 7. Code map

- `dist/simulation.js` — weather and forecast from the real record, dispatch physics, both controllers, accounting, CSV
- `dist/data/maitri-2023.js` — the hourly station record (generated by `engine/export_demo_weather.py`)
- `dist/worker.js` — runs the model off the UI thread
- `dist/app.js` — the four views, charts, explanations, exports
- `dist/style.css` — interface theme
- `dist/data/default-run.json` — the example shown on load, produced by the current code
- `scripts/check.mjs` — syntax, assets and default-run reproducibility
- `tests/simulation.test.mjs` — conservation, limits, priorities, faults, reproducibility, real-data and stress-test guards
