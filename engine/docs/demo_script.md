# Five-minute demo, adversarial Q&A, and the 36-hour plan against this repo

## Before anything runs

```bash
python run.py --days 210          # produces results/run.json + dashboard/dashboard.html
```

Open `dashboard/dashboard.html` in a browser. It is one file, needs no server, and works with
the network down. **Record a 90-second screen capture of a good run as insurance** — laptops,
projectors and Wi-Fi all fail at hour 34.

## The five minutes

| Time | What is on screen | What you say |
|---|---|---|
| 0:00–0:45 | Overview tab, the three-row "What this is" card | Two constraints exist here and nowhere else: **one fuel delivery a year**, and **the station's own exhaust corrupts the station's own science**. Everything else in this problem is a solved commercial category. |
| 0:45–1:45 | Season replay tab, press **Play** | Same weather, same station, two tanks. The tuned baseline crosses the reserve floor in *[month]*; ours does not. The counter on the left is days to the next ship. |
| 1:45–2:30 | Stress tests tab, blizzard block | Four days, no solar, turbines cut out on overspeed, heat demand up. Unserved critical energy, both controllers. Degradation, not collapse. |
| 2:30–3:15 | Demo week tab | The shaded bands are clean-air windows: forecast wind would carry exhaust to the sampling inlet. Watch the diesel band go to zero across them while the battery carries the load. |
| 3:15–4:15 | Results tab | Against the **tuned** baseline, not the strawman: fuel margin, critical outages, clean-air compliance, and the fraction of the gap to a perfect-foresight oracle that we close. Then the ablation row — same optimiser, point forecast instead of quantiles. That row is the answer to "why AI". |
| 4:15–5:00 | Assumptions tab, filter to **Assumptions only** | No public electrical load data exists for any polar station. Ours is synthetic, built bottom-up from headcount and outdoor temperature. Here is every parameter, its source, its confidence and its sensitivity range. |

End on the Assumptions tab. Naming your own weakness before a judge does is worth more than
any extra feature.

**Never say** "AI-powered energy management platform". If the first noun is *platform* or
*dashboard*, the round is already lost to whoever says *fuel survivability*.

## Adversarial Q&A

**"Why do you need AI at all?"**
We do not, for dispatch — that is a MILP and we say so. The learned part does one job:
calibrated uncertainty. Reserve is conventionally sized by a fixed rule; we size it from the
forecast's own predicted spread. The ablation is controller `C-point` in the results table:
same optimiser, point forecast, fixed reserve.

**"Don't ABB, Schneider, Siemens and HOMER already do this?"**
Yes — for grid-connected or refillable-tank economics. None optimises against a single annual
resupply as a hard survivability constraint, and none models exhaust contamination of the
station's own instruments. Those two constraints are the product.

**"Where did your data come from?"**
Weather is real ERA5 at Maitri's coordinates, hourly, eight years. Load is synthetic and we
say so before being asked: bottom-up from headcount, envelope heat loss and snow-melt water.
*(Open the Assumptions tab. This is the strongest moment in the demo — rehearse it.)*

**"How do you know your simulation is realistic?"**
Three signals we did not fit, all documented in the polar energy literature, all emerging from
the physics: per-capita demand peaks in winter at minimum occupancy; wind and solar are
seasonally complementary; and PV output rises in the cold while turbines gain about 20 % from
air density at −37 °C. Validation charts are on the Results tab.

**"What happens when the forecast is wrong?"**
The horizon re-solves every step, so error is corrected within one interval. Critical load
carries a reserve margin from the P90 forecast, not the mean. The `forecast_bust` stress test
is exactly this: twelve hours of a badly wrong forecast with the truth unchanged.

**"Why is this specific to polar stations?"**
Three constraints that exist nowhere else: annual resupply; contamination of the station's own
science by its own generation; and physical asset unavailability that decorrelates from the
weather forecast. Remove any one and this is a generic microgrid controller.

**"Can it run with limited connectivity?"**
Everything runs locally. Mean MILP solve time is on the Results tab (tens to hundreds of
milliseconds on laptop-class hardware). Models are a few megabytes. Weather comes from the
on-site AWS; cloud sync is opportunistic, not required. The dashboard is a single offline file.

**"Will it work with real station hardware?"**
The controller emits setpoints with Modbus/MQTT semantics — what gensets and inverters already
expose. We have not tested against hardware; that is a hardware-in-the-loop test, not a
rewrite, and it is the honest next step.

**"Your fuel savings are against your own simulator."**
Correct, and that is why the baseline is tuned rather than a strawman, why there is an oracle
bound, and why the tank size was calibrated by a script rather than chosen. If you distrust
the fuel number, use the reliability number: unserved critical energy under identical weather.

## The 36-hour plan, mapped to this repository

Because the pipeline already exists, the hackathon build is a *specialisation*, not a
construction. Hours 0–3 stay exactly as the strategy document has them: no code, lock scope to
two differentiators, freeze `config/station.json`, storyboard the demo backwards.

| Hour | Deliverable | Where it lives |
|---|---|---|
| 0–3 | scope locked, station spec frozen, demo storyboarded | `config/station.json` |
| 3–6 | ERA5 pulled and cached, twin runs a full year, validation charts look right | `weather.py`, `twin.py` |
| 6–10 | quantile forecasters trained, walk-forward skill table exists | `forecast.py` |
| 10–14 | one deterministic MILP solve end to end on true data | `dispatch.py` |
| 14–18 | rolling loop closed, baselines A and B running, first ugly full demo | `harness.py`, `controllers.py` |
| 18–24 | fuel allocator wired in, clean-air constraint firing, oracle and ablation | `fuelbudget.py`, `cleanair.py` |
| 24 | **feature freeze.** metrics table exists, nothing new is added after this | `metrics.py` |
| 24–32 | stress scenarios, dashboard polish, assumptions tab, sensitivity runs | `scenarios.py`, `dashboard/` |
| 32–36 | rehearse the demo at least four times | `docs/demo_script.md` |

Sleep in shifts. Teams that do not plan sleep make their worst decisions at hour 28.

## If you are behind schedule

Cut in this order: (1) physical-availability forecasting, (2) the ablation controller,
(3) the stress tests beyond the blizzard. Never cut baseline B, the oracle, or the Assumptions
tab — those three are what make the numbers believable.
