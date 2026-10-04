# SIH26061 — full technical brief for external review

**What I want from you:** critique and improve this. It is already built and running; I am not
looking for a plan, I am looking for the places where the modelling, the optimisation
formulation, the experimental design, or the argument is weak, and for concrete replacements.
Sections 10 and 11 list the specific open questions. Constraints on your advice are in §12 —
please respect them, because suggestions that violate them cost us the competition rather than
helping.

Everything below is implemented unless explicitly marked as *not built*. Numbers are from real
runs, not projections.

---

## 1. The competition context

Smart India Hackathon 2026, problem statement **SIH26061**, verified against the official
portal (`sih.gov.in/sih2026PS`) on 2026-09-03:

| Field | Value |
|---|---|
| Title | AI-Driven Smart Energy Management System for Polar Research Stations |
| Organisation | Ministry of Earth Sciences (MoES) |
| Department | National Centre for Polar and Ocean Research (NCPOR) |
| Category | Software |
| Theme | Clean & Green Technology |
| Deadline | 30 September 2026 |
| Ideas submitted | 0/500 at time of check (portal-wide max was 4/500 — submissions had just opened, so this is *not* evidence the PS is uncontested) |

**The entire official problem statement is one sentence:**

> "Develop an intelligent energy-management system using AI for load forecasting, renewable
> energy integration and fuel optimization under extreme polar conditions."

There is no Background, no Detailed Description, no Expected Solution, no dataset link, no
YouTube link. Consequences we are designing around:

- Nothing can be lost by "solving a neighbouring problem" — there are no ministry bullets to be
  scored against. The framing carries the entire score.
- The four nouns in that sentence (*load forecasting, renewable integration, fuel optimisation,
  extreme conditions*) are the only official words a judge can map our work onto, so all four
  must be visibly present even though none of them is where our novelty is.
- SIH26060 (remote-management platform for the Indian Antarctic stations) is adjacent and easy
  to confuse with this.

Judging is a ~5–10 minute pitch plus questions, to a panel that may or may not contain a power
systems specialist. There is a 36-hour build round after idea selection.

## 2. The strategic thesis (this is the part most worth attacking)

The generic reading of this statement — forecast demand, optimise dispatch, show a dashboard —
is a **saturated, commercially solved space**:

- Products already shipping this exact feature list: ABB Ability Microgrid Plus, Schneider
  EcoStruxure Microgrid Advisor, Siemens microgrid controllers, Hitachi e-mesh, SMA Fuel Save
  Controller (whose entire stated purpose is displacing diesel with renewables).
- Sizing and planning: HOMER Pro is the de facto standard for remote hybrid systems; NREL REopt
  covers the economics.
- Open-source implementations of the whole backend: PyPSA, oemof, MicroGridsPy, pandapower,
  GridLAB-D, OpenDSS; RAMP for bottom-up stochastic loads; PowerGridWorld, Gym-ANM, CityLearn
  as RL environments for precisely this control problem.
- Academically: MPC for isolated microgrid EMS is a mature literature; robust MPC with interval
  prediction and MILP unit commitment with evolutionary sizing are both a decade old.

So: **load forecasting + MILP dispatch + battery scheduling + critical-load prioritisation is a
feature checklist, not an innovation.** We build all of it, and we deliberately do not pitch it.

The three things we found genuinely thin in both literature and products, all specific to polar
stations:

1. **Annual-resupply fuel budgeting.** Commercial EMS optimise cost against a market price or a
   refillable tank. A polar station receives one fuel delivery a year and has no supplier until
   the ship returns. The correct objective is `max P(critical load served until resupply)` — a
   chance-constrained seasonal allocation problem coupled to a daily dispatch problem. We found
   no productised implementation.
2. **Science-integrity-constrained dispatch.** Locally emitted greenhouse gases are not
   representative of the wider region, so the station's own diesel exhaust corrupts the
   atmospheric record the station exists to collect (de Witt, Chung & Lee 2024). A constraint of
   the form "do not run the genset when forecast wind carries exhaust toward the clean-air
   sampling inlet" reflects real operational practice and is modelled by no EMS product.
3. **Physical-availability forecasting**, as distinct from weather forecasting. The forecast can
   say clear sky while the array is under drift, or 14 m/s while the blades are iced and the
   turbine is stopped. Availability decorrelates from weather, and that decorrelation is polar.

Scoring self-assessment: as "an AI energy management platform for polar stations" this is
~3.5/10 (the fourteenth dashboard the judges see that day). As "a fuel-survivability and
science-integrity operating policy for Maitri II, with a documented digital twin and honest
baselines" it is ~7.5/10.

**If you think this thesis is wrong — that the differentiators are weaker than claimed, or that
something in the saturated list is actually still open — say so directly. That is the single
most valuable feedback you can give.**

## 3. Verified facts from the polar energy literature

These drive the model; all from de Witt, Chung & Lee (2024), *Sustainability* 16(1) 426, an
open-access review built partly on correspondence with station energy engineers, unless noted.

- Of 81 Antarctic stations, 37 use renewables; renewable share is typically low and essentially
  all retain diesel backup.
- **Station demand peaks in the Antarctic winter, when station population is at its minimum.**
  This inverts the occupancy-driven intuition most teams will encode.
- Largest consumers are heat-related: space heating, tap-water heating, snow melting. These can
  act as flexible loads / thermal storage instead of a dump load.
- Diesel gensets waste fuel above or below their efficiency point; a diesel–battery hybrid lets
  the genset sit at that point while the battery buffers peaks.
- Fuel delivery is on a months-to-a-year cycle; the literature frames this as *mid-term energy
  security*.
- **Local diesel emissions corrupt the station's own atmospheric science.** (Strategically the
  most valuable fact in this project.)
- Active de-icing of turbine blades consumes ~2–12% of nameplate power.
- Wind and solar are seasonally complementary in Antarctica: solar Oct–Feb, wind peaking in
  spring and autumn, lowest in summer.
- Cold helps generation physics: PV output rises 0.35–0.5%/K below STC, snow albedo raises
  incident irradiance, and cold air density gives turbines ~20% higher output at ~−37 °C.
- Failure modes are physical, not computational: storm vibration collapsed a turbine at Mario
  Zucchelli; melt ingress shorted a generator at Neumayer III (2011); wind-blasted ice and
  gravel shatters PV cover glass; drift buries arrays.
- Demand-restriction management was tried at Johann Gregor Mendel Station: it saved fuel but hurt
  occupant comfort, and teaching a fast-rotating crew to use energy optimally proved hard.
- Princess Elisabeth Station runs high renewable penetration with a battery for stabilisation and
  a smart microgrid that prioritises loads by available renewable power.

Indian context: **Maitri** (1989, Schirmacher Oasis, ~25 winter / 40–45 summer) and **Bharati**
(2012, Larsemann Hills) are operated by NCPOR under MoES. **Maitri II** has been approved at
~₹2,000 crore, targeted for January 2029, explicitly planned as a green station with wind and
solar, waste-heat recovery, enhanced insulation and automated instruments that relay data even
when unmanned. Nobody has published its operating policy — that is the live decision we attach
to.

## 4. Data — exact sources, all status-checked 2026-09-08

### In the code

| Purpose | Endpoint | Notes |
|---|---|---|
| Hourly ERA5 at the station | `https://archive-api.open-meteo.com/v1/archive` | no credentials; re-serves ECMWF ERA5; cached to Parquet per year |
| Problem-statement record | `https://sih.gov.in/sih2026PS` | scraped and diffed by `watch_ps.py` |

Exact query used (Maitri, −70.7667 / 11.7333):

```
hourly = temperature_2m, wind_speed_10m, wind_direction_10m, shortwave_radiation,
         snowfall, surface_pressure, relative_humidity_2m
models = era5   timezone = UTC   years = 2015–2023
```

`wind_speed_10m` arrives in km/h and is divided by 3.6; `snowfall` is in cm;
`wind_direction_10m` is meteorological (direction the wind comes *from*).

### Verified alternatives and cross-checks (not currently wired in)

- ERA5 at source: `https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels`
  and `.../reanalysis-era5-land` (needs account + licence acceptance)
- SCAR READER Antarctic station met records: `https://legacy.bas.ac.uk/met/READER/` ,
  surface stations at `.../surface/stationpt.html`
- AMRC/AMRDC Antarctic automatic weather stations: `https://amrdcdata.ssec.wisc.edu/dataset`
- BSRN research-grade surface radiation (Neumayer, Syowa, South Pole, Concordia):
  `https://bsrn.awi.de/stations/listings/`
- Global Solar Atlas (sanity anchor): `https://globalsolaratlas.info/map`
- **NCPOR Indian polar met data portal: `https://data.ncpor.res.in/`** — live, shows current
  Maitri and Bharati conditions and offers dataset browsing. (An earlier strategy document
  assumed this could not be confirmed; it exists.) A sibling host `npdc.ncpor.res.in` appears in
  search results but did not connect from our network.
- COMNAP facilities/station catalogue: `https://www.comnap.aq/antarctic-facilities-information`
  and machine-readable at `https://github.com/PolarGeospatialCenter/comnap-antarctic-facilities`

### The data gap that defines the project

**No public electrical load data exists for any polar research station.** Load is therefore
synthetic — built bottom-up from physics, never drawn — and we say so before being asked.
Proxies available for shape calibration if wanted:

- Building Data Genome Project 2 — `https://github.com/buds-lab/building-data-genome-project-2`
- ASHRAE Great Energy Predictor III — `https://www.kaggle.com/competitions/ashrae-energy-prediction`
- Open Power System Data (household) — `https://data.open-power-system-data.org/household_data/`
- UMass Smart\* — `https://traces.cs.umass.edu/docs/traces/smartstar/`
- RAMP bottom-up stochastic load generation — `https://www.rampdemand.org/`

de Witt et al. publish average 2018–2023 fuel demand curves for Jang Bogo and King Sejong,
giving a citable seasonal *shape* for a real Antarctic station — currently used as a qualitative
check, **not** yet as a quantitative calibration target (see open question 5).

## 5. Repository and environment

```
sih26061-polar-ems/
├── config/station.json          every physical number, annotated (see §6)
├── src/polarems/
│   ├── config.py                loader; dotted access; .override(); .assumptions()
│   ├── weather.py               ERA5 fetch + Parquet cache
│   ├── twin.py                  loads, PV, wind, genset/battery physics
│   ├── availability.py          snow-cover and icing state, derates, de-icing decision
│   ├── cleanair.py              contamination-window geometry and pricing
│   ├── forecast.py              NWP degradation + LightGBM quantile models
│   ├── fuelbudget.py            Monte Carlo seasonal allocator, FuelPlan
│   ├── dispatch.py              the MILP
│   ├── controllers.py           A, B, C, C-point, D
│   ├── harness.py               closed loop + shared physical resolver
│   ├── metrics.py               all reported metrics
│   ├── scenarios.py             blizzard, genset failure, forecast bust
│   ├── setpoints.py             Modbus register map + MQTT topics
│   └── report.py                results/run.json + self-contained dashboard
├── run.py                       whole pipeline, one command
├── calibrate.py                 sizes the fuel tank against baseline B
├── sizing.py                    Maitri II PV/wind/battery sweep (MC only, seconds)
├── sensitivity.py               re-runs B and C at the ends of uncertain parameter ranges
├── watch_ps.py                  portal watcher/differ
├── emit_setpoints.py            replays decisions as Modbus/MQTT
├── make_ppt.py                  builds the SIH deck from results/run.json
├── dashboard/template.html      offline single-file console (Plotly inlined)
├── tests/test_core.py           14 tests, ~2 s
└── docs/                        demo script, findings, this brief
```

Environment: Python 3.14 on Windows. numpy 2.5.2, pandas 3.0.5, scipy 1.18.1, pulp 3.3.2,
highspy 1.15.1, lightgbm 4.7.0, scikit-learn 1.9.0, pvlib 0.15.2, pyarrow 25.0.1, plotly 7.0.0,
matplotlib 3.11.1, python-pptx 1.0.2.

Run: `python run.py --days 210` (full season, ~45–60 min, dominated by MILP solves);
`python run.py --days 30 --no-scenarios --controllers A,B,C` for a ~4 min sanity loop.

## 6. Configuration discipline

Every leaf in `config/station.json` is
`{value, unit, source, confidence, sensitivity: [lo, hi]}`. The dashboard's Assumptions tab and
`sensitivity.py` are both generated from that file, and rows whose source begins `ASSUMPTION:`
or `DESIGN CHOICE:` are flagged as such in the UI. 64 parameters currently.

Key values (all with sensitivity ranges in the file):

| Group | Values |
|---|---|
| Site | Maitri, −70.7667, 11.7333, 130 m |
| Population | 45 summer (15 Nov – 1 Mar), 25 winter, 10-day ramps |
| Thermal | envelope UA 4.2 kW/K; setpoint 20 °C; electrified heat fraction 0.45; waste-heat recovery 0.35; snow-melt 11 kWh/person/day |
| Loads | science 12 kW 24×7; life-safety 8 kW; domestic 0.42 kW/person; workshop 18 kW peak (summer, deferrable) |
| PV | 120 kWp, 70° tilt, north-facing, albedo 0.85, temp coeff −0.004/K, losses 0.12, bifacial gain 0.10 |
| Wind | 3 × 30 kW, hub 20 m, cut-in 3.5, rated 12, cut-out 25 m/s, shear α 0.14, de-icing 6% nameplate |
| Battery | 400 kWh / 150 kW, SoC 15–95%, round-trip 0.90, degradation 0.02 L-eq/kWh |
| Gensets | DG1 125 kW (a=0.084 L/h per kW rated, b=0.246 L/h per kW out, start 3.5 L), DG2 60 kW (0.084 / 0.252, start 2.0 L), min load 30%, min up/down 2 h |
| Fuel | tank 72,000 L, reserve floor 6,000 L, resupply 1 Feb, survivability target 0.99 |
| Clean air | inlet bearing 45°, sector half-width 60°, wind band 1–8 m/s, penalty 40 L-eq per genset-hour, soft by default |
| Simulation | train 2019–2022, test 2023, MC 2015–2022, horizon 36 h, control step 6 h, seed 26061 |

**The tank is calibrated, not chosen.** `calibrate.py` runs baseline B over a full year with an
effectively unlimited tank (it burns 111,314 L, 0 critical outages) and over the 210-day demo
season (73,719 L). The configured tank is 72,000 L ≈ 0.98× the season burn, so survivability is
genuinely at stake. This is disclosed as a DESIGN CHOICE in the config and in the pitch.

## 7. The model, in detail

### 7.1 Digital twin (`twin.py`)

**Occupancy:** step between summer/winter headcount with a 10-day centred rolling mean so
thermal and water loads do not step.

**Heating (the winter driver):**
`heat_thermal = UA · max(0, T_set − T_out) − 0.12 · occupancy`, then
`heating_elec = heat_thermal · (1 − WHR) · electrified_fraction`.

**Snow melt / water:** `kWh_per_person_day · occ / 24`, shaped by a daytime-weighted diurnal
profile, scaled by `1 + 0.010 · max(0, −10 − T)` (colder snow costs more to melt).

**Domestic:** `kW_per_person · occ · diurnal(7–23) · N(1, 0.08)`.
**Workshop:** summer only, `18 kW · diurnal(8–18) · U(0.4, 1)`, deferrable tier.
**Science + life safety:** constant, 24×7, critical tier.

Tiers: `critical = science + life_safety`, `essential = heating + snow-melt`,
`deferrable = workshop`, `sheddable = domestic`.

**PV:** pvlib `get_solarposition` → **Erbs** decomposition of ERA5 GHI → `get_total_irradiance`
(isotropic transposition, tilt 70°, azimuth 0° = north, albedo 0.85) → **Faiman** cell
temperature → `kW = kWp · POA/1000 · (1 + γ(T_cell − 25)) · (1 − losses) · (1 + bifacial)`.

**Wind:** power-law shear 10 m → 20 m hub; air density `ρ = p/(R_air · T)` and a `ρ/1.225`
multiplier (≈1.2 at −37 °C); cubic interpolation between cut-in and rated, flat to cut-out.

**Genset:** affine fuel curve `L/h = a · P_rated + b · P_out`, zero when off. Specific
consumption therefore falls monotonically with loading, which is what makes running a big set on
a small load expensive and gives the min-load constraint its teeth.

**Battery:** SoC bounds, symmetric `√RTE` on charge and discharge, power limit, throughput
penalty as a degradation proxy.

### 7.2 Availability (`availability.py`)

Snow coverage is an integrated state, hour by hour:

```
c += 0.22 · snowfall_cm
c -= 0.030 · max(0, wind − 8 m/s)          # drift scouring on steep tilt
c -= 0.05 if T > −2 °C                     # melt
c  = 0.05 every 7 days if c > 0.4          # crew clears the array
c  = clip(c, 0, 1)
```

Icing risk: nonzero only inside the temperature window [−12, +0.5] °C, driven by
`0.55 · humidity_ramp(RH>80) + 0.45 · precip_ramp`, scaled by a wind-flux term, then persisted
with a 4-hour half-life EWM (ice does not vanish when the driving conditions stop).

Derates: `pv_kw = pv_potential · (1 − snow_cover)`, `wind_kw = wind_potential · (1 − 0.55 · icing)`.
De-icing spends 6% of nameplate and is only worth it when recovered energy exceeds the spend by
15%.

### 7.3 Clean-air windows (`cleanair.py`)

ERA5 wind direction is where the wind comes *from*, so the plume travels toward
`(dir + 180) mod 360`. A window is flagged when that bearing is within ±60° of the sampling
inlet bearing **and** wind speed is in [1, 8] m/s (above that the plume disperses before
reaching the inlet), **or** when speed < 1 m/s (stagnation contaminates regardless of
direction). About 23% of hours are flagged at Maitri under the current geometry.

Pricing: 40 L-equivalent per genset-hour inside a window (soft), or a hard prohibition via a
config flag. The controller sees flags computed from the **forecast** wind, not the truth, so a
bad forecast produces a real violation that shows up in the compliance metric.

### 7.4 Forecasting (`forecast.py`)

ERA5 is a reanalysis (a hindcast), so a realistic forecast has to be manufactured. `NWP`
degrades the truth with AR(1) noise (ρ = 0.85) whose σ is
`field_std · factor · sqrt(lead / 48)`, with per-field factors: temp 0.35, wind speed 0.55, wind
direction 0.50, GHI 0.45, snowfall 0.70, pressure 0.25, RH 0.40. **The controller never sees the
truth** — perfect foresight is reserved for the oracle.

`QuantileForecaster`: LightGBM, quantile objective, α ∈ {0.1, 0.5, 0.9}, one model per
(target, quantile) — 9 models — over targets `load_kw`, `pv_kw`, `wind_kw`. Direct
multi-horizon: lead time is a feature, trained on leads {1,2,3,6,9,12,18,24,30,36,42,48}.
22 features: hour/day-of-year sin-cos, lead, occupancy (the roster is legitimately known),
degraded NWP fields, wind-direction sin/cos, heating-degree-hours, wind³, plus last observed
value and last-24 h mean of each target. 420,249 training rows from 4 years. Quantiles are
sorted post-hoc to enforce monotonicity. Trains in ~35 s.

**The one place AI earns its place:** reserve requirement =
`(load_p90 − load_p50) + (renewable_p50 − renewable_p10)`, fed into the MILP as a constraint.
Reserve is conventionally a fixed rule; here it is sized from the forecast's own predicted
spread. Controller `C-point` is the ablation: same optimiser, point forecast, fixed
10%-of-load reserve.

### 7.5 Seasonal fuel allocator (`fuelbudget.py`)

Outer level of the two-level problem.

1. For each of 8 historical weather years, rebuild the twin and compute a daily fuel *need*
   heuristic: daily energy deficit minus a battery-worth of credited surplus, converted to
   litres at the best achievable specific consumption for that mean load.
2. Align the ensemble by day-of-year → a per-day distribution of need across years.
3. Allowance for each day = the `target` (0.99) quantile across years, scaled down uniformly if
   the total exceeds `tank − reserve_floor` (and the shortfall is reported rather than hidden).
4. Online, `horizon_budget(t0, hours, spent_so_far)` returns the window's allowance plus a
   **carry-over credit** `clip(0.35 · (planned_to_date − spent_to_date), ±0.5 · window)`, floored
   at `0.15 · window`. Under-spending a mild March buys headroom for August; over-spending
   tightens the next horizon.

Reported: `P(run dry)` under heuristic operation across the ensemble, mean-year need, worst-year
need, and whether the allocation is binding. In the current configuration it *is* binding:
66,000 L usable against a 72,024 L mean-year need and 132,303 L at the P99 year.

### 7.6 MILP dispatch (`dispatch.py`)

Rolling horizon `T = 36 h`, hourly resolution, re-solved every 6 h (i.e. MPC).

Variables per hour: `u[g,t]` binary commitment, `p[g,t]` genset power, `start[g,t] ∈ [0,1]`,
`charge`, `discharge`, `soc`, `battery_reserve`, `curtail`, `shed[tier] ≥ 0`, and a single
`over_budget ≥ 0`.

Constraints:

```
Σ_g p[g,t] + dis[t] − ch[t] + renewable[t] − curtail[t] = Σ_tier (load[tier,t] − shed[tier,t])
p[g,t] ≤ P_rated[g] · u[g,t]                       p[g,t] ≥ minload[g] · P_rated[g] · u[g,t]
start[g,t] ≥ u[g,t] − u[g,t−1]
soc[t] = soc[t−1] + √η · ch[t] − dis[t]/√η          soc_lo ≤ soc[t] ≤ soc_hi
bres[t] ≤ P_bat − dis[t]                            bres[t] ≤ soc[t] − soc_lo
Σ_g (P_rated[g]·u[g,t] − p[g,t]) + bres[t] ≥ reserve_req[t]
min-up/down in tight pairwise form:  u[g,k] ≥ u[g,t] − u[g,t−1]  for k ∈ (t, t+min_up)
Σ_t fuel[t] ≤ budget + over_budget
```

Objective (all in litre-equivalents):

```
fuel  +  Σ start_cost·start  +  Σ clean_air_penalty[t]·u[g,t]  +  0.02·(ch+dis)
      +  5000·shed_critical + 40·shed_essential + 3·shed_deferrable + 8·shed_sheddable
      +  0.001·curtail        +  25·over_budget
```

Solver: HiGHS via PuLP, `gapRel = 0.02`, `timeLimit = 3 s`, 4 threads.

Two formulation notes that mattered a great deal:

- The **aggregated** min-up/down form (`Σ u ≥ n · start`) gave 18–21 s solves; the tight pairwise
  form gives ~0.5 s for the same optimum. Forty-fold, same answer.
- Making the fuel budget a **hard** row produced 3 s time-limit hits and can go infeasible in a
  blizzard. Pricing it instead keeps solves at ~0.3 s, keeps every horizon feasible, and is more
  defensible: the allowance is a price signal, the physical tank is the fuse.

### 7.7 Controllers (`controllers.py`)

| | Description |
|---|---|
| **A** | Fixed schedule (weak baseline): DG1 06:00–24:00, DG2 00:00–06:00, battery idle, surplus curtailed |
| **B** | Tuned rule-based (strong baseline): acts **every hour** on measured state; persistence expectation `0.7·last + 0.3·24h-mean`; starts on SoC < 0.45, SoC < 0.25 floor, or expected deficit > 0.8·P_bat; picks the smallest set that covers the need; holds the efficiency point; stops at SoC > 0.85 |
| **C** | Proposed: quantile forecast → reserve → MILP under seasonal allowance and clean-air pricing |
| **C-point** | Ablation: identical to C but point forecast and fixed 10%-of-load reserve |
| **D** | Perfect-foresight oracle: identical optimiser, true future — an upper bound, not a competitor |

Two information-leak fixes worth noting, because they were bugs first: the MPC takes its
**tier split** from the last 24 h of metered history (not the future), and its **clean-air
flags** from forecast wind (not the truth). Only D looks forward.

### 7.8 Shared physical resolver (`harness.py`)

Controllers choose *commitments only* (which sets are on, a battery preference, tier shedding).
Every hour is then settled by identical physics for all of them:

1. Committed gensets are allocated cheapest-marginal-litre first, respecting min-load bands.
2. Surplus charges the battery within power and SoC limits; the remainder is curtailed.
3. Deficit discharges the battery within limits; anything still short is shed in order
   sheddable → deferrable → essential → **critical last**.
4. Fuel is decremented from the physical tank; a dry tank forces all gensets off.

This is what makes the comparison honest — a controller with a bad forecast pays for it in the
resolver, not in a metric it computed itself.

### 7.9 Metrics (`metrics.py`)

Fuel (litres, and % vs A and vs B), genset runtime and starts, % of runtime within ±10% of the
efficiency point, renewable utilisation and curtailed kWh, **unserved critical energy and count
of contiguous critical outage events** (the headline), unserved essential, days of fuel left,
battery equivalent full cycles, clean-air compliance %, MILP solve time mean and p95, and
`gap_to_oracle_closed = (B − C) / (B − D)`.

### 7.10 Stress tests (`scenarios.py`)

- **Blizzard** (4 days): wind forced above cut-out, PV zero, snow cover 1.0, temperature −8 K,
  heating load correspondingly higher.
- **Genset failure**: primary set unavailable for 48 h in deep winter.
- **Forecast bust**: the NWP promises a benign, windy, sunny 12-hour window that never arrives;
  the truth is unchanged.

### 7.11 Hardware path (`setpoints.py`, *interface only, never tested against hardware*)

Modbus holding-register map (10 registers: run commands, power setpoints, BESS command, SoC
read-back, two load-shed relays, clean-air hold, current allowance) plus MQTT topics under
`station/maitri2/ems/...`. Honest framing: this is a translation layer, and hardware-in-the-loop
is the next step, not a rewrite.

## 8. Results

Season 2023-02-01 → 2023-08-30 (210 days, 5,041 h), tank 72,000 L, reserve floor 6,000 L,
36 h horizon, 6 h control step, identical weather for every controller.

| Controller | Fuel (L) | vs B | Critical outages (events / kWh) | Renewable used | Clean-air compliance | Starts |
|---|---|---|---|---|---|---|
| A fixed schedule | 72,000 (**tank dry**) | — | 36 / 9,266.6 | 69.1% | 40.5% | 280 |
| B tuned rule-based | 72,000 (**tank dry**) | 0% | 6 / 328.4 | 84.8% | 27.4% | 277 |
| **C proposed** | **63,384.7** | **−12.0%** | **0 / 0.0** | 92.5% | 43.7% | 437 |
| C-point ablation | 63,279.6 | −12.1% | 1 / 8.3 | 92.7% | 43.3% | 439 |
| D perfect-foresight oracle | 62,484.6 | −13.2% | 0 / 0.0 | 93.5% | 49.0% | 358 |

**C closes 91% of the B→D gap.** Both baselines empty the tank before the season ends; C
finishes above the reserve floor. Wall clock: ~600–890 s per MPC controller for 840 solves
(~0.7–1.1 s per solve including forecaster inference).

Stress tests (same season, controller C against baseline B):

| Scenario | B — unserved critical | C — unserved critical |
|---|---|---|
| Four-day blizzard (no solar, turbines at cut-out, +8 K heat demand) | 809.1 kWh in 8 events | **0.0 kWh in 0 events** |
| Primary genset unavailable 48 h in deep winter | 325.8 kWh in 3 events | 97.9 kWh in 1 event |
| 12 h badly wrong forecast, truth unchanged | — | 0.0 kWh; fuel 63,373 L vs 63,385 L nominal |

The genset-failure row is the honest one: C degrades rather than collapsing, but it does not
escape unharmed, and we present it that way.

Forecast skill, walk-forward on the unseen test year:

| Target | MAE | MAPE | p90 coverage |
|---|---|---|---|
| Load | 1.82 kW | 2.2% | 89% |
| PV | 3.01 kW | 12.1% | 93% |
| Wind | 6.53 kW | 20.0% | 90% |

Calibration (coverage ≈ nominal) is the property the reserve rule actually consumes, and is the
number we lead with rather than MAE.

Seasonal allocator, this configuration: 66,000 L usable, mean-year need 72,024 L, P99-year need
132,303 L, allocation **binding**.

Sizing sweep (`sizing.py`, Monte Carlo only, 8 weather years, 210-day season): at the configured
tank, the smallest swept configuration that never runs dry is 80 kWp PV / 180 kW wind / 400 kWh
battery (~53,147 L per season mean); the configured 120/90/400 runs dry in 88% of weather years
under heuristic operation.

## 9. What is deliberately not built

Reinforcement learning (no data, no time to tune an environment, no safety guarantee — and the
first question, "what happens when it takes an unsafe action?", has no good 36-hour answer),
blockchain, an LLM chatbot over the results, microservices, Kubernetes, a mobile app, physical
IoT hardware, user authentication and roles, a 3D station model, and cloud deployment beyond a
single VM.

## 10. Known weaknesses (we state these before a judge does)

1. **We grade our own homework.** The same team writes the simulator and the controller, so
   measured savings are savings against our own modelling assumptions. Mitigations: strong
   baseline B, oracle bound D, the ablation, and a sensitivity table — but no complete defence.
2. **No real load data.** Stated plainly.
3. **The winter-peak signal is weaker than the literature headline.** In our twin, total winter
   load ≈ total summer load (84.8 kW at 25 people vs 88.1 kW at 45). What *is* unambiguous:
   winter essential (heat + melt) load exceeds summer's, and per-capita demand roughly doubles.
   We phrase it that way rather than overclaiming.
4. **Renewable share is high** (~57% of annual load at the configured sizing), which invites
   "so does diesel even matter?". We lead with the winter months, where PV is zero for ten weeks.
5. **Maitri II ratings are speculative**, so the deliverable is framed as a sizing and
   operating-policy explorer rather than a retrofit.
6. **Single test year, single seed.** No confidence intervals on any headline number.
7. **The ablation is currently unflattering-looking**: C-point uses ~0.2% *less* fuel than C
   while incurring 1 critical outage versus 0. The story is "quantile forecasts buy reliability,
   not fuel" — which is true and defensible, but it is one outage event, not a distribution.
8. **No hardware in the loop.**

## 11. Open questions — where I want your help

1. **Decomposition.** Is allowance-plus-carry-over the right outer level, or should the fuel
   state enter the MPC directly as a terminal value function (SDDP / approximate dynamic
   programming on remaining litres × day-of-season)? Is that tractable inside a 3 s per-step
   budget and a 36-hour build, and would it measurably beat the heuristic?
2. **Reserve.** `(p90 − p50) + (p50 − p10)` is a heuristic proxy for a chance constraint. Is
   there a formulation with comparable solve cost that is actually correct — scenario-based
   robust MPC on a handful of quantile paths, or an explicit joint chance constraint?
3. **Making the ablation decisive, honestly.** Given #7 above, what is the right experiment
   design to show that calibrated uncertainty matters? Multiple seeds × multiple weather years
   and a reliability-vs-fuel Pareto? An expected-unserved-energy metric rather than an event
   count? What is the minimum compute that gives a defensible claim?
4. **Baseline fairness.** Is hourly persistence + SoC thresholds the strongest reasonable
   *non-forecast* baseline? Should we add a fourth baseline — MPC with point forecasts but no
   fuel budget — to separate "the MILP helps" from "the allocator helps"?
5. **Twin calibration.** How would you calibrate `envelope_UA` and `heat_electrified_frac`
   against the published Jang Bogo / King Sejong fuel-demand curves, given we have shape but not
   absolute magnitudes for our own station? Is there a defensible identifiability argument, or
   should we present a family of calibrations instead of one?
6. **Clean-air constraint.** Soft pricing at 40 L-equivalent/hour is a design choice with no
   empirical basis. Is there a principled way to price a contaminated sampling hour — or should
   it be a hard constraint with an explicit relaxation budget (e.g. "at most N contaminated
   hours per month")?
7. **Availability parameters.** The snow-accumulation and icing coefficients are invented.
   Are there published parameterisations for snow shedding on steeply tilted panels and blade
   ice accretion that we could adopt with citations?
8. **Numerics.** C took 608 s for 840 solves (~0.7 s each) despite a 0.3 s benchmark median.
   Where is the remaining slack — MIP warm starts across horizons (PuLP's HiGHS wrapper exposes
   no warm start), symmetry breaking, coarser resolution beyond hour 24, or a rolling LP with
   integer variables only in the first 12 hours?
9. **Validation without ground truth.** What is the strongest defensible validation protocol for
   a synthetic twin in this setting, beyond "three qualitative signals emerge that we did not
   fit"?
10. **The pitch.** Given §1 (one-sentence PS, non-specialist panel, five minutes), is
    "fuel survivability + science integrity" the right lead, or is there a stronger framing we
    have missed? Is there a fourth polar-specific constraint we have not thought of?

## 12. Constraints on your suggestions

- **36-hour build round, 6 students** of normal college-level ability. Anything requiring a
  research programme is out.
- **Software only.** No hardware, no field data collection, no instrumentation.
- **Explainability is a scoring criterion.** A method we cannot explain to a non-specialist in
  two sentences is worth less than a slightly worse method we can.
- **No reinforcement learning**, for the reasons in §9 — unless you can answer the safety
  question convincingly within the time budget.
- **Everything must run offline on a laptop.** The connectivity story is part of the pitch.
- **Do not suggest adding features for completeness.** Five half-built features lose to two
  built properly with a measured result. If you propose adding something, say what it replaces.
- Prefer suggestions that make the *existing* result more defensible over suggestions that add
  new capability.

## 13. References

- de Witt, M.; Chung, C.; Lee, J. (2024). *Mapping Renewable Energy among Antarctic Research
  Stations.* Sustainability 16(1), 426. doi:10.3390/su16010426 — the backbone source.
- *Modeling Hybrid Renewable Microgrids in Remote Northern Regions.* Energies (2025) 18, 5827.
  doi:10.3390/en18215827
- *Learning from Arctic Microgrids: Cost and Resiliency Projections for Renewable Energy
  Expansion with Hydrogen and Battery Storage.* Sustainability (2025) 17, 5996.
  doi:10.3390/su17135996
- *Model predictive control-based energy management system for an isolated electro-thermal
  microgrid.* Energy Conversion and Management (2024).
- Parent, O.; Ilinca, A. (2011). *Anti-icing and de-icing techniques for wind turbines.*
  Cold Regions Science and Technology 65, 88–96.
- Obara, S. et al. (2013). Syowa Base distributed engine-generator networks. Applied Energy 111.
- Olivier, J.R. et al. (2008). SANAE IV solar evaluation. Renewable Energy 33, 1073–1084.
- Tin, T. et al. (2010). Energy efficiency and renewable energy under extreme conditions:
  case studies from Antarctica. Renewable Energy 35, 1715–1723.
- Tooling: ERA5 (ECMWF/Copernicus), pvlib-python, LightGBM, HiGHS, PuLP.
