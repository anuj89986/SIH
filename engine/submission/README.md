# SIH 2026 idea submission — SIH26061

| File | What it is |
|---|---|
| `SIH26061_Idea_Presentation.pptx` | the deck, built on the official SIH idea template |
| `SIH26061_Idea_Presentation.pdf` | **this is what the portal accepts** — PPT/DOC are rejected |

Six slides including the title, as the template's own instruction slide requires. That
instruction slide is deleted in the output (the template explicitly permits this). The idea
pointers — *Proposed Solution*, *Detailed explanation*, *How it addresses the problem*,
*Innovation and uniqueness*, and the headings on every later slide — are kept exactly as the
template words them, with our content underneath.

## Before you upload

1. Open the pptx and fill the two fields left as placeholders on the title slide:
   **Team ID** and **Team Name** (the name registered on the portal). The oval on slides 2–6
   carries the team name too — `make_ppt.py --team "..." --team-id "..."` fills all of them.
2. Confirm with the SPOC that SIH26061 is the statement the college has blocked for this team
   (SIH26060, the remote-management platform for the Indian Antarctic stations, is adjacent and
   easy to confuse).
3. Export to PDF and upload the PDF.

## Regenerating

Every number and chart on the deck is read from `results/run.json` — nothing is typed by hand.
After any change to the model, re-run the pipeline and rebuild:

```bash
python run.py --days 210
python make_ppt.py --team "Your Team" --team-id 12345
```

To re-export the PDF without opening PowerPoint by hand (Windows, PowerPoint installed):

```powershell
$p = (Resolve-Path .\submission\SIH26061_Idea_Presentation.pptx).Path
$o = [System.IO.Path]::ChangeExtension($p, '.pdf')
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Open($p, $true, $false, $false)
$pres.SaveAs($o, 32); $pres.Close(); $app.Quit()
```

## What the numbers on the deck mean

- **Fuel burned** is over the simulated resupply season on real ERA5 weather at Maitri's
  coordinates, with the tank deliberately sized so the season is marginal — that is the regime
  the whole idea is about.
- **Critical outages** count contiguous runs of hours in which critical load went unserved.
  This is the headline reliability metric; quote it before the fuel percentage.
- **Clean-air compliance** is the share of contaminating-wind hours in which no generator ran.
- Controller **B** is a *tuned* rule-based baseline that acts hourly on measured state and
  produces zero critical outages over a full year with an unconstrained tank. Beating a
  strawman would prove nothing, so B is deliberately strong.
- Controller **D** is a perfect-foresight oracle — an upper bound, not a competitor. The
  meaningful figure is the fraction of the B→D gap that C closes.

Never quote a bare "we save X% fuel". Quote it against B, with the oracle bound beside it and
the assumptions one click away.
