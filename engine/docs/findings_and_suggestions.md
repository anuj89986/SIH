# Findings from building it, and what I would do differently

Written after implementing the strategy document end to end. Everything below is either a
verified portal fact or something the code actually did.

## 1. Portal facts that change the plan

Checked against `sih.gov.in/sih2026PS` on 2026-09-03; `watch_ps.py` re-checks and diffs.

- **Deadline is 30 September 2026**, not 20 September. Ten more days than assumed.
- **Theme is "Clean & Green Technology"**, not Miscellaneous or Renewable Energy. The portal is
  the authority, and the neighbouring MoES statements are tagged coherently too (26059
  Transportation & Logistics, 26060 Smart Automation, 26062 Smart Automation, 26063 Smart
  Education). The "themes are scrambled" reading came from a mirror, not the portal. Use the
  theme's vocabulary — diesel displacement, emissions, green station — in the pitch.
- **The official statement is one sentence.** There is no Background / Detailed Description /
  Expected Solution for SIH26061, and no dataset link or YouTube link. Two consequences:
  - there are no ministry bullets to be scored against, so the framing carries everything;
  - the four nouns in that sentence — *load forecasting, renewable energy integration, fuel
    optimization, extreme polar conditions* — must each be visibly present in the demo, because
    they are the only official words a judge can map your work onto.
- **Idea submissions are near zero portal-wide** (189 of 226 statements at 0/500, the busiest
  at 4/500 as of 2026-09-03). That means submissions have only just opened — it is **not**
  evidence that this statement is uncontested. Re-check with `watch_ps.py` closer to the date;
  the relative count late in September is the number worth having.
- **SIH26060 overlaps** (remote management platform for the Indian Antarctic stations).
  Confirm with the SPOC which statements the college has blocked before writing a line of deck.

## 2. What the build taught me about the technical plan

**The fuel budget should be soft, not hard.** As a hard MILP row it made winter horizons slow
(seconds, hitting the time limit) and can become infeasible in a blizzard. Priced at a steep
litre-equivalent penalty instead, solve times fell to about 0.3 s, every horizon stays
feasible, and the behaviour is more defensible: the allowance is a price signal, the physical
tank is the fuse. A controller that browns out a science hall to satisfy an accounting rule is
a bug, not a feature.

**Formulation, not solver, decides whether "solves in under a second" is true.** The
aggregated min-up/min-down constraint (`sum(u) >= n * start`) produced 18–21 s solves. The
tight pairwise form (`u[k] >= u[t] - u[t-1]`) produced 0.5 s solves on the same instances.
Same optimum, forty-fold difference. Do not make the edge-device claim before checking this.

**Baseline B must act every hour.** With the MPC's 6-hourly decision cadence, the rule-based
baseline logged 82 critical outages a year and looked like a strawman. Letting it act hourly on
measured state — which is what a real local controller does — took it to **zero** outages over
a full year. It is now a baseline worth beating, and the comparison is worth reporting.

**Tank size decides whether the story is visible at all.** With a generous tank the seasonal
allocator never binds, and the entire first differentiator becomes decorative. `calibrate.py`
sizes the tank against baseline B's own burn; the configured value is 0.98x of it, so the
season is genuinely marginal. Say that out loud, with the sensitivity range, before a judge
asks why the tank is exactly that size.

**Use Open-Meteo's ERA5 archive, not the Copernicus CDS.** Same reanalysis, no account, no
licence acceptance, no queue — and hourly Antarctic data going back decades. Cached to Parquet
on first call, so the whole pipeline then runs offline. This removes a real hour-3 failure mode.

**Do not train sequence models here.** LightGBM quantile regression reached MAE 1.8 kW on load
(2.2% MAPE), 3.0 kW on PV, 6.5 kW on wind, with p90 coverage of 89–93% — i.e. calibrated,
which is the property the reserve rule actually consumes. It trains in about 40 s on 420k rows.
The calibration table, not the MAE, is your evidence that the AI component earns its place.

## 3. Where the current model is weaker than the pitch would like

- **The winter-peak signal is real but smaller than the literature headline.** In this twin,
  total winter load is roughly equal to summer load, not above it — heat rises but summer adds
  twenty people, workshop and water loads. What *is* unambiguous: winter essential (heat plus
  snow-melt) load exceeds summer's, and per-capita demand roughly doubles in winter. State it
  that way. Overclaiming a winter peak the chart does not show is an unforced error.
- **The renewable share is high** (about 57% of annual load at the configured 120 kWp / 90 kW /
  400 kWh). That is faithful to Maitri II's stated green-station intent, but it invites "so why
  does diesel matter?". Lead with the winter months, where PV is zero for ten weeks and the
  question answers itself.
- **Snow-cover events are episodic, not chronic** at Schirmacher: mean coverage is a few per
  cent because drift scouring clears steep panels. The availability differentiator therefore
  shows up as a handful of sharp events, not a persistent derating. Demo the event, not the
  average.

## 4. Suggested division of labour, given that the pipeline already exists

The build is now a specialisation problem, not a construction problem. Two people can carry the
whole model. The rest should go where the marks are:

1. **Modeller** — freeze `config/station.json` against whatever the team can defend, run
   `calibrate.py`, re-run the pipeline, own the metrics table.
2. **Optimiser** — clean-air sector geometry and penalty, the fuel-budget carry-over rule, the
   ablation run. Owns the "why AI" answer.
3. **Narrative** — the deck, the assumptions tab walkthrough, the five-minute script, and four
   rehearsals. This role is chronically undervalued and it is the one that decides the round.
4. **Frontend** — the dashboard exists and is offline-capable; polish the season replay and the
   clean-air shading, and do not rebuild it in a framework.
5. **Reliability** — stress tests, the backup video, the offline check with Wi-Fi off, and the
   spare laptop with the repo cloned and `data/cache/` warm.

## 5. Things worth adding only if you are ahead of schedule

- Hardware-in-the-loop against a genset simulator, using `emit_setpoints.py` as the interface.
- A live sizing session in front of judges with `sizing.py` — "if MoES buys 200 kWp instead of
  120, here is the tank they no longer need" attaches directly to a real procurement decision.
- Bharati as a second station preset, to show the config is not hard-coded to one site.

## 6. Things to refuse, even if there is time

Reinforcement learning. A chatbot over the results. A mobile app. Cloud deployment. A second
frontend framework. Every one of these costs a day and adds nothing a judge scores, and each
one weakens the claim that the team knew what the actual problem was.
