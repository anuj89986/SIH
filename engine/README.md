# SIH26061 — polar station fuel-survivability and science-integrity operating policy

A working, end-to-end implementation of the reframing argued in the strategy analysis:
**not** "an AI energy management platform for polar stations" (a saturated, commercially
solved category), but a **fuel-survivability and science-integrity operating policy** for an
Indian Antarctic station, with a documented digital twin and honest baselines.

Real ERA5 weather at Maitri's coordinates. Physics twin. LightGBM quantile forecasts.
MILP/MPC dispatch under a Monte Carlo seasonal fuel allocation. Four controllers, an oracle
bound, an ablation, three stress tests, and a self-contained dashboard.

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python run.py --days 30 --no-scenarios --controllers A,B,C   # ~4 min, sanity check
python run.py --days 210                                     # full demo run
```

Outputs: `results/run.json`, `results/metrics.csv`, `dashboard/dashboard.html`
(opens by double-click, no server, works offline).

---

## Portal facts, verified 2026-09-03

Checked directly against `sih.gov.in/sih2026PS`, which **is** machine-fetchable — the strategy
document assumed it was not. Three corrections follow from that:

| Field | Value |
|---|---|
| ID / title | SIH26061 — AI-Driven Smart Energy Management System for Polar Research Stations |
| Organisation | Ministry of Earth Sciences (MoES) |
| Department | National Centre for Polar and Ocean Research (NCPOR) |
| Category | Software |
| Theme (portal) | **Clean & Green Technology** — not "Miscellaneous", not "Renewable Energy" |
| Deadline (portal) | **30 September 2026** — not 20 September |
| Official description | *"Develop an intelligent energy-management system using AI for load forecasting, renewable energy integration and fuel optimization under extreme polar conditions."* |

**There is no extended Background / Detailed Description / Expected Solution for this PS.**
The portal record is that single sentence — the community mirrors are not truncated, the
statement really is that thin. Consequences:

- Nothing can be lost by "solving a neighbouring problem"; there are no Expected Solution
  bullets to map against. The scoring will run on the pitch, so the framing carries everything.
- The one official sentence names four things: **load forecasting, renewable integration,
  fuel optimisation, extreme conditions.** All four are implemented here as table stakes.
  The two differentiators sit on top and are what gets pitched.
- Adjacent statements are worth knowing: SIH26060 (remote management platform for the Indian
  Antarctic stations, theme Smart Automation) and SIH26062 (expedition logistics). Confirm with
  the SPOC which of the cluster the college has blocked.

## What this builds, and what it deliberately does not

**Table stakes (built, not pitched as innovation):** load / PV / wind forecasting, MILP
dispatch, battery scheduling, critical-load prioritisation, a dashboard. Every commercial
EMS — ABB Ability, Schneider EcoStruxure, Siemens, SMA Fuel Save — already ships this
combination. Claiming the combination as novel loses the room.

**Differentiator 1 — resupply-horizon fuel budgeting** (`src/polarems/fuelbudget.py`).
Commercial EMS minimise cost against a market price or a refillable tank. A polar station has
N litres and no supplier until the ship returns. The objective becomes
`max P(critical load served until resupply)`. A Monte Carlo allocator over eight historical
ERA5 weather years produces a daily litre allowance; the MILP prices every litre beyond it,
with carry-over between horizons, so under-spending a mild March buys headroom for August.
The allowance is the price signal and the physical tank is the fuse — a controller that browns
out the science hall to satisfy an accounting rule would be a bug. No productised
implementation of this was found.

**Differentiator 2 — science-integrity-constrained dispatch** (`src/polarems/cleanair.py`).
Locally emitted greenhouse gases are unrepresentative of the wider region and corrupt the
station's own atmospheric record (de Witt, Chung & Lee 2024). When forecast wind would carry
exhaust to the sampling inlet, generator hours are priced (or forbidden), and battery plus
renewables must carry the clean-air window. Modelled by no EMS product.

**Differentiator 3 — physical-availability forecasting** (`src/polarems/availability.py`).
Weather forecast is not generation forecast: snow coverage is a state variable with drift
scouring and crew clearing, blade icing derates the power curve, and de-icing costs 2–12 % of
nameplate. Built here because it is cheap once the twin exists; drop it first if time runs out.

**Not built, on purpose:** reinforcement learning, blockchain, an LLM chatbot, microservices,
Kubernetes, a mobile app, IoT hardware, auth and role management, a 3D station model.

## Architecture

```
config/station.json         every physical number, with source + confidence + sensitivity
  |
weather.py     ERA5 hourly at the station grid point via Open-Meteo (no credentials), cached
  |
twin.py        occupancy -> thermal + snowmelt + domestic + science loads (bottom-up)
               pvlib POA with 70 deg tilt, 0.85 snow albedo, cold-temperature gain
               wind power curve with air-density correction
availability.py  snow coverage + icing state -> what is physically available
cleanair.py      wind direction + speed -> clean-air windows
  |
forecast.py    NWP degradation of the reanalysis (error grows with lead time)
               LightGBM quantile models: load / pv / wind at p10-p50-p90, 1-48 h
  |
fuelbudget.py  Monte Carlo over weather years -> daily litre allowance (survivability target)
  |
dispatch.py    MILP: commitment, loading, battery, curtailment, tiered shedding
               reserve sized from the forecast's own spread; seasonal allowance priced in
controllers.py A fixed schedule | B tuned rule-based | C proposed | C-point ablation | D oracle
harness.py     closed loop; all controllers settled by the same physical resolver
metrics.py     fuel, outages, renewable use, clean-air compliance, solve time
report.py      results/run.json + dashboard/dashboard.html
```

### Where AI actually earns its place

Dispatch is a MILP and we say so — it is optimal, deterministic, explainable, and solves in
well under a second. The learned component does exactly one job: **calibrated uncertainty**.
Reserve and battery headroom are sized from the forecaster's own predicted spread
(`p90 − p50` load, `p50 − p10` renewables) instead of a fixed 10 %-of-load rule. Controller
`C-point` is that ablation: same optimiser, same twin, point forecast and fixed reserve.
The difference between `C` and `C-point` is the answer to "why do you need AI at all?".

### Honesty machinery, built in

- **Baseline B is strong.** It acts every hour on measured state, prioritises renewables,
  buffers with the battery, starts on an SoC threshold, holds the efficiency point, and over a
  full year with an unconstrained tank it produces **zero** critical outages. Beating a
  strawman would be worthless.
- **Oracle D** is the same optimiser with perfect foresight — the upper bound. The number that
  matters is the fraction of the B→D gap that C closes.
- **The physical resolver is shared.** Controllers choose commitments only; the hour is settled
  by identical physics for all of them, so nobody profits from privately knowing the future.
- **The tank is calibrated, not chosen to flatter.** `calibrate.py` runs baseline B over a full
  year with an unlimited tank and reports the burn; the configured tank is 0.98× the baseline
  burn over the demo season, which is what puts survivability genuinely at stake. Say that out
  loud in the pitch, and show the sensitivity range.
- **Every parameter carries its source.** `config/station.json` leaves are
  `{value, unit, source, confidence, sensitivity}`, and the dashboard's Assumptions tab is
  generated from that file. Rows sourced `ASSUMPTION:` are flagged as such.

## Data

| Source | Use | Status |
|---|---|---|
| ERA5 via Open-Meteo archive API | hourly temperature, wind speed and direction, GHI, snowfall, pressure, RH at -70.767, 11.733 | **real, no credentials, cached to Parquet** |
| pvlib | irradiance decomposition, plane-of-array transposition, cell temperature | real physics |
| COMNAP / NCPOR station records | headcount, station function | real |
| de Witt, Chung & Lee 2024 (Sustainability 16(1) 426) | polar energy facts: winter peak, de-icing cost, exhaust contamination, cold-density gain | real, open access |
| Electrical load | **synthetic**, bottom-up from headcount and outdoor temperature | no public polar station load data exists — say so before being asked |

If the CDS ERA5 API is ever wanted instead, swap `weather.fetch_year`; everything downstream
is indifferent. `data/cache/` makes the whole pipeline runnable with the network down.

## Results and how to read them

`python run.py --days 210` writes the table below into `results/metrics.csv`. Read it in this
order: unserved critical energy first, then the margin over **B**, then the gap closed to **D**.
Never quote a bare fuel percentage — quote it against the tuned baseline, with the oracle bound
next to it and the sensitivity table one click away.

The demo season runs from the resupply date through deep winter. The tank is deliberately
marginal, so the season is a survivability problem rather than a cost problem — which is the
entire argument of the submission.

## Scripts

| Command | What it does | Runtime |
|---|---|---|
| `python run.py --days 210` | the whole pipeline: weather, twin, forecasts, fuel plan, five controllers, stress tests, dashboard | ~45 min |
| `python run.py --days 30 --no-scenarios --controllers A,B,C` | fast sanity loop while changing anything | ~4 min |
| `python calibrate.py` | sizes the fuel tank against baseline B so the season is genuinely marginal | ~1 min |
| `python sizing.py` | Maitri II sizing explorer: PV / wind / battery sweep against survivability, no MILP | seconds |
| `python sensitivity.py` | re-runs B and C at the ends of each uncertain parameter's range — the answer to "your data is invented" | ~20 min |
| `python watch_ps.py --all-moes` | re-fetches the SIH portal and diffs the SIH26061 record against the last snapshot | seconds |
| `python emit_setpoints.py` | replays controller decisions as Modbus registers and MQTT messages | ~2 min |
| `python -m pytest tests -q` | 14 tests over physics, geometry, the resolver and the MILP | ~2 s |

## Files worth knowing

| Path | Why it matters |
|---|---|
| `config/station.json` | freeze this first at the hackathon; every module reads it |
| `dashboard/template.html` | the demo; `dashboard/dashboard.html` is the generated artefact, Plotly inlined so it works offline |
| `docs/demo_script.md` | the five-minute run of show, and the adversarial Q&A |
| `docs/findings_and_suggestions.md` | portal facts, what the build taught, where the model is weaker than the pitch |
| `results/run.json` | everything the dashboard shows, machine-readable |

## Known weaknesses (state these before a judge does)

1. **We grade our own homework.** The same team wrote the simulator and the controller.
   Mitigations: a strong baseline, an oracle bound, an ablation, and a sensitivity table.
2. **No real load data exists** for any polar research station. The load is synthetic and
   physics-driven; the Assumptions tab is the defence, and it is one click away in the demo.
3. **Maitri II is not yet designed**, so generator, PV and turbine ratings are speculative.
   That is why the deliverable is framed as a sizing and operating-policy explorer for a station
   still on the drawing board, rather than a retrofit for one that exists.
4. **The margin over a well-tuned rule-based controller may be modest.** If it comes out at
   4 %, report 4 % and move the argument to reliability and clean-air compliance, where
   forecast-aware control wins by a wider margin. Do not weaken baseline B.
5. **No hardware in the loop.** The controller emits setpoints with Modbus/MQTT semantics;
   testing against a real genset controller is the honest next step, not a rewrite.
