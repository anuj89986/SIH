"""Build the SIH 2026 idea-submission deck from the official template + the real run.

Every number on the deck is read from ``results/run.json`` -- nothing is typed by hand, so a
re-run of the pipeline regenerates a deck that matches it.

    python make_ppt.py                       # -> submission/SIH26061_Idea_Presentation.pptx
    python make_ppt.py --team "Team Name" --team-id 12345

Rules taken from the template's own instruction slide (which is deleted in the output):
six slides including the title, points not paragraphs, the idea pointers kept in place,
and the file finally uploaded to the portal as PDF.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.dml.color import RGBColor  # noqa: E402
from pptx.oxml.ns import qn  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402

ROOT = Path(__file__).resolve().parent
TEMPLATE = Path.home() / "Downloads" / "SIH2025-IDEA-Presentation-Format.pptx"
RESULTS = ROOT / "results" / "run.json"
ASSETS = ROOT / "assets" / "deck"
OUT_DIR = ROOT / "submission"

IDEA_NAME = "HIMSHAKTI"
IDEA_TAGLINE = "Fuel-survivability operating policy for Indian Antarctic stations"

INK = RGBColor(0x11, 0x1A, 0x27)
MUTED = RGBColor(0x4A, 0x57, 0x68)
ACCENT = RGBColor(0x0B, 0x4F, 0x9E)
GOOD = RGBColor(0x0E, 0x7A, 0x4F)
WARN = RGBColor(0xB0, 0x6A, 0x0C)
FONT = "Calibri"

# matplotlib palette, kept in step with the colours above
C_ACCENT, C_GOOD, C_WARN, C_BAD, C_MUTED = "#0b4f9e", "#0e7a4f", "#b06a0c", "#b3312a", "#6b7785"


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
def load_results() -> dict:
    if not RESULTS.exists():
        raise SystemExit(f"no results at {RESULTS} -- run `python run.py --days 210` first")
    with open(RESULTS, encoding="utf-8") as fh:
        data = json.load(fh)
    days = data.get("meta", {}).get("run_params", {}).get("days", 0)
    if days < 100:
        print(f"  ! warning: deck is being built from a {days}-day run, not the 210-day season")
    return data


def facts(data: dict) -> dict:
    """Everything the slides quote, in one place."""
    comp = {row["controller"]: row for row in data["comparison"]}
    head = data.get("headline", {})
    fp = data["fuel_plan"]["metrics"]
    fc = data.get("forecast", {})
    rp = data["meta"]["run_params"]
    b, c = comp.get("B", {}), comp.get("C", {})

    fuel_saved = (b.get("fuel_l", 0) - c.get("fuel_l", 0)) if b and c else 0
    scen = data.get("scenarios", {})

    return {
        "days": rp.get("days"),
        "season": f"{str(rp.get('season_start'))[:10]} to {str(rp.get('season_end'))[:10]}",
        "horizon_h": rp.get("horizon_h"),
        "test_year": rp.get("test_year"),
        "n_years": len(set(rp.get("mc_years", []) + rp.get("train_years", []) + [rp.get("test_year")])),
        "fuel_b": b.get("fuel_l"),
        "fuel_c": c.get("fuel_l"),
        "fuel_saved_l": fuel_saved,
        "fuel_vs_b_pct": head.get("fuel_vs_B_pct"),
        "gap_closed_pct": head.get("gap_to_oracle_closed_pct"),
        "outages_b": head.get("critical_outage_events_B"),
        "outages_c": head.get("critical_outage_events_C"),
        "unserved_b": b.get("unserved_critical_kwh"),
        "unserved_c": c.get("unserved_critical_kwh"),
        "clean_b": head.get("clean_air_compliance_B_pct"),
        "clean_c": head.get("clean_air_compliance_C_pct"),
        "renew_b": b.get("renewable_utilisation_pct"),
        "renew_c": c.get("renewable_utilisation_pct"),
        "solve_ms": head.get("mean_solve_time_ms"),
        "ablation_outages": head.get("ablation_point_forecast_outages"),
        "tank_l": fp.get("tank_l"),
        "reserve_l": fp.get("reserve_floor_l"),
        "target": fp.get("target_survivability"),
        "load_mape": (fc.get("load_kw") or {}).get("mape_pct"),
        "pv_mape": (fc.get("pv_kw") or {}).get("mape_pct"),
        "wind_mape": (fc.get("wind_kw") or {}).get("mape_pct"),
        "load_cover": (fc.get("load_kw") or {}).get("p90_coverage"),
        "scenarios": scen,
        "n_assumptions": len(data.get("assumptions", [])),
    }


def fnum(x, nd=0, dash="--"):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return dash
    return f"{x:,.{nd}f}"


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c8d0d9")
    ax.tick_params(colors="#4a5768", labelsize=7.5, length=3)
    ax.grid(axis="y", color="#e6ebf0", lw=0.7)
    ax.set_axisbelow(True)


def fig_constraints(data: dict, f: dict) -> Path:
    """The two constraints that make this a polar problem, side by side."""
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(5.5, 2.5), dpi=220, gridspec_kw={"width_ratios": [1.35, 1]}
    )
    fig.patch.set_facecolor("white")

    daily = data["daily"]
    dates = np.arange(len(daily["B"]["date"]))
    ax1.plot(dates, daily["B"]["fuel_remaining_l"], color=C_WARN, lw=1.6, label="rule-based")
    ax1.plot(dates, daily["C"]["fuel_remaining_l"], color=C_ACCENT, lw=2.2, label="HIMSHAKTI")
    ax1.axhline(f["reserve_l"], color=C_BAD, ls=(0, (2, 2)), lw=1.2)
    ax1.text(len(dates) * 0.99, f["reserve_l"], "emergency reserve ", color=C_BAD, fontsize=6.5,
             va="bottom", ha="right")
    ax1.set_title("One tank, one ship a year", fontsize=8.5, color="#111a27", loc="left", pad=6)
    ax1.set_ylabel("litres in tank", fontsize=7.5, color="#4a5768")
    ax1.set_xlabel("day of season", fontsize=7.5, color="#4a5768")
    ax1.legend(frameon=False, fontsize=7, loc="lower left", bbox_to_anchor=(0.0, 0.08))
    _style(ax1)

    # clean-air sector: where the plume must not go
    ax2.remove()
    ax2 = fig.add_subplot(1, 2, 2, projection="polar")
    ax2.set_theta_zero_location("N")
    ax2.set_theta_direction(-1)
    inlet, half = 45.0, 60.0
    theta = np.deg2rad(np.linspace(inlet - half, inlet + half, 80))
    ax2.fill_between(theta, 0, 1, color=C_BAD, alpha=0.18)
    ax2.plot([np.deg2rad(inlet)] * 2, [0, 1], color=C_BAD, lw=1.4)
    ax2.plot([0], [0], marker="s", color=C_MUTED, ms=6)
    ax2.text(np.deg2rad(inlet), 1.16, "clean-air\ninlet", ha="center", fontsize=6.5, color=C_BAD)
    ax2.set_yticklabels([])
    ax2.set_xticks(np.deg2rad([0, 90, 180, 270]))
    ax2.set_xticklabels(["N", "E", "S", "W"], fontsize=7, color="#4a5768")
    ax2.grid(color="#e6ebf0", lw=0.7)
    ax2.set_title("Exhaust must miss the\nsampling sector", fontsize=8.5, color="#111a27", pad=10)

    fig.tight_layout(pad=0.6)
    path = ASSETS / "fig_constraints.png"
    fig.savefig(path, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


def fig_architecture() -> Path:
    """Data -> twin -> forecast -> allocator -> MILP -> setpoints, as one flow."""
    fig, ax = plt.subplots(figsize=(11.4, 2.25), dpi=220)
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 26)
    ax.axis("off")

    stages = [
        ("ERA5 weather\n9 years, hourly", "real data", C_MUTED),
        ("Physics digital twin\nloads - PV - wind - diesel", "no drawn curves", C_MUTED),
        ("Availability model\nsnow cover - blade ice", "differentiator 3", C_WARN),
        ("Quantile forecaster\nLightGBM p10/p50/p90", "the AI", C_ACCENT),
        ("Seasonal fuel allocator\nMonte Carlo, P99 survivability", "differentiator 1", C_GOOD),
        ("Rolling MILP / MPC\nHiGHS, 36 h horizon", "clean-air priced in", C_ACCENT),
        ("Setpoints + console\nModbus / MQTT, offline", "differentiator 2", C_MUTED),
    ]
    w, gap = 12.2, 1.6
    x = 0.6
    for label, tag, colour in stages:
        ax.add_patch(
            plt.Rectangle((x, 7), w, 12, facecolor="white", edgecolor=colour, lw=1.4, zorder=2,
                          joinstyle="round")
        )
        ax.text(x + w / 2, 14.6, label.split("\n")[0], ha="center", va="center", fontsize=7.6,
                color="#111a27", fontweight="bold", zorder=3)
        ax.text(x + w / 2, 10.6, label.split("\n")[1], ha="center", va="center", fontsize=6.6,
                color="#4a5768", zorder=3)
        ax.text(x + w / 2, 4.6, tag, ha="center", va="center", fontsize=6.4, color=colour,
                fontweight="bold")
        if x > 1:
            ax.annotate("", xy=(x - 0.35, 13), xytext=(x - gap + 0.35, 13),
                        arrowprops=dict(arrowstyle="-|>", color="#9aa5b1", lw=1.1))
        x += w + gap

    ax.text(0.6, 22.5, "Closed loop: every controller is settled by the same physical resolver "
                       "(min-load bands, SoC limits, tier shedding) on identical weather",
            fontsize=7, color="#4a5768")
    fig.tight_layout(pad=0.3)
    path = ASSETS / "fig_architecture.png"
    fig.savefig(path, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


def fig_results(data: dict, f: dict) -> Path:
    """Four controllers on the metrics that decide the round."""
    comp = {row["controller"]: row for row in data["comparison"]}
    order = [c for c in ("A", "B", "C", "D") if c in comp]
    names = {"A": "A fixed", "B": "B tuned rule", "C": "C ours", "D": "D oracle"}
    colours = {"A": C_MUTED, "B": C_WARN, "C": C_ACCENT, "D": C_GOOD}

    fig, axes = plt.subplots(1, 3, figsize=(5.6, 2.35), dpi=220)
    fig.patch.set_facecolor("white")

    panels = [
        ("fuel_l", "Fuel burned (L)", lambda v: v),
        ("critical_outage_events", "Critical outages", lambda v: v),
        ("clean_air_compliance_pct", "Clean-air compliance (%)", lambda v: v),
    ]
    for ax, (key, title, fn) in zip(axes, panels):
        vals = [fn(comp[c].get(key) or 0) for c in order]
        bars = ax.bar([names[c] for c in order], vals, color=[colours[c] for c in order], width=0.62)
        ax.set_title(title, fontsize=8, color="#111a27", loc="left", pad=6)
        _style(ax)
        ax.tick_params(axis="x", labelrotation=30, labelsize=6.8)
        top = max(vals) if max(vals) > 0 else 1
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + top * 0.03,
                    f"{v:,.0f}" if abs(v) >= 10 else f"{v:,.1f}",
                    ha="center", fontsize=6.6, color="#111a27")
        ax.set_ylim(0, top * 1.22)

    fig.tight_layout(pad=0.5)
    path = ASSETS / "fig_results.png"
    fig.savefig(path, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


def fig_impact(f: dict) -> Path:
    """Impact, in the two units a station engineer actually cares about."""
    fig, ax = plt.subplots(figsize=(5.4, 2.3), dpi=220)
    fig.patch.set_facecolor("white")
    ax.axis("off")

    tiles = [
        (f"{fnum(f['fuel_saved_l'])} L", "diesel saved per season\nvs a tuned rule-based controller", C_ACCENT),
        (f"{fnum(f['outages_b'])} -> {fnum(f['outages_c'])}", "critical outages in the same\nweather, same station", C_GOOD),
        (f"{fnum(f['clean_b'], 0)}% -> {fnum(f['clean_c'], 0)}%", "of clean-air sampling windows\nkept free of own exhaust", C_WARN),
        (f"{fnum(f['solve_ms'])} ms", "per optimisation step\non laptop-class hardware", C_MUTED),
    ]
    for i, (big, small, colour) in enumerate(tiles):
        x, y = (i % 2) * 0.5, 0.52 - (i // 2) * 0.5
        ax.add_patch(plt.Rectangle((x + 0.01, y), 0.47, 0.42, transform=ax.transAxes,
                                   facecolor="#f6f8fb", edgecolor="#dbe2ea", lw=1))
        ax.text(x + 0.045, y + 0.27, big, transform=ax.transAxes, fontsize=15,
                fontweight="bold", color=colour)
        ax.text(x + 0.045, y + 0.07, small, transform=ax.transAxes, fontsize=7, color="#4a5768")

    fig.tight_layout(pad=0.2)
    path = ASSETS / "fig_impact.png"
    fig.savefig(path, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# slide helpers
# ---------------------------------------------------------------------------
def delete_slide(prs: Presentation, index: int) -> None:
    xml_slides = prs.slides._sldIdLst
    slides = list(xml_slides)
    rid = slides[index].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    prs.part.drop_rel(rid)
    xml_slides.remove(slides[index])


def shape_by_name(slide, name: str):
    for shape in slide.shapes:
        if shape.name == name:
            return shape
    return None


def set_title(slide, text: str, size: int = 28) -> None:
    for shape in slide.shapes:
        if shape.is_placeholder and shape.placeholder_format.idx == 0:
            tf = shape.text_frame
            tf.clear()
            para = tf.paragraphs[0]
            run = para.add_run()
            run.text = text
            run.font.size = Pt(size)
            run.font.bold = True
            run.font.name = FONT
            return


def _no_bullet(paragraph) -> None:
    """Drop the template's inherited list glyph; our bullets are typed characters."""
    pPr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buChar", "a:buAutoNum", "a:buNone"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    pPr.append(pPr.makeelement(qn("a:buNone"), {}))


def write_block(shape, blocks: list[tuple[str, list[str]]], head_pt=12.5, body_pt=10.5,
                head_colour=ACCENT) -> None:
    """Fill a text box with (pointer heading, bullet list) pairs."""
    tf = shape.text_frame
    tf.clear()
    tf.word_wrap = True
    first = True
    for heading, bullets in blocks:
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        run = para.add_run()
        run.text = heading
        run.font.size = Pt(head_pt)
        run.font.bold = True
        run.font.name = FONT
        run.font.color.rgb = head_colour
        para.space_before = Pt(6)
        para.space_after = Pt(2)
        _no_bullet(para)
        for bullet in bullets:
            bp = tf.add_paragraph()
            br = bp.add_run()
            br.text = "•  " + bullet
            br.font.size = Pt(body_pt)
            br.font.name = FONT
            br.font.color.rgb = INK
            bp.space_after = Pt(1)
            _no_bullet(bp)


def add_textbox(slide, left, top, width, height, blocks, head_pt=12.5, body_pt=10.5):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    write_block(box, blocks, head_pt=head_pt, body_pt=body_pt)
    return box


def add_caption(slide, left, top, width, text, size=8.5, colour=MUTED):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(0.3))
    tf = box.text_frame
    tf.word_wrap = True
    run = tf.paragraphs[0].add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.name = FONT
    run.font.italic = True
    run.font.color.rgb = colour
    return box


# ---------------------------------------------------------------------------
# deck
# ---------------------------------------------------------------------------
def build(team: str, team_id: str, repo: str) -> Path:
    data = load_results()
    f = facts(data)
    ASSETS.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    figs = {
        "constraints": fig_constraints(data, f),
        "architecture": fig_architecture(),
        "results": fig_results(data, f),
        "impact": fig_impact(f),
    }

    prs = Presentation(str(TEMPLATE))
    delete_slide(prs, 6)  # the instruction slide, as the template itself permits
    s1, s2, s3, s4, s5, s6 = prs.slides

    # ---- slide 1: title -------------------------------------------------
    for shape in s1.shapes:
        if shape.has_text_frame and "SMART INDIA HACKATHON" in shape.text_frame.text:
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    run.text = run.text.replace("2025", "2026")
    box = shape_by_name(s1, "TextBox 9")
    tf = box.text_frame
    tf.clear()
    lines = [
        ("Problem Statement ID", "SIH26061"),
        ("Problem Statement Title", "AI-Driven Smart Energy Management System for Polar Research Stations"),
        ("Theme", "Clean & Green Technology"),
        ("PS Category", "Software"),
        ("Team ID", team_id),
        ("Team Name", team),
    ]
    for i, (label, value) in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        lr = para.add_run()
        lr.text = f"{label}: "
        lr.font.size = Pt(13)
        lr.font.bold = True
        lr.font.name = FONT
        lr.font.color.rgb = INK
        vr = para.add_run()
        vr.text = value
        vr.font.size = Pt(13)
        vr.font.name = FONT
        vr.font.color.rgb = ACCENT if label.startswith("Problem") else INK
        para.space_after = Pt(7)
    idea = s1.shapes.add_textbox(Inches(0.36), Inches(5.55), Inches(5.9), Inches(1.2))
    itf = idea.text_frame
    itf.word_wrap = True
    r1 = itf.paragraphs[0].add_run()
    r1.text = IDEA_NAME
    r1.font.size = Pt(24)
    r1.font.bold = True
    r1.font.name = FONT
    r1.font.color.rgb = ACCENT
    p2 = itf.add_paragraph()
    r2 = p2.add_run()
    r2.text = IDEA_TAGLINE
    r2.font.size = Pt(12)
    r2.font.name = FONT
    r2.font.color.rgb = MUTED

    # ---- slide 2: idea / proposed solution -------------------------------
    set_title(s2, f"{IDEA_NAME} — fuel survivability, not another dashboard", 22)
    box = shape_by_name(s2, "TextBox 8")
    box.left, box.top, box.width, box.height = (
        Inches(0.45), Inches(1.25), Inches(7.15), Inches(5.5),
    )
    write_block(
        box,
        [
            ("Proposed Solution", [
                "Two-level operating policy for a station with ONE fuel delivery a year",
                "Outer level: Monte Carlo allocator sets a daily litre allowance that holds "
                f"P(critical load served to the ship) ≥ {fnum(100 * (f['target'] or 0.99))}%",
                "Inner level: rolling MILP re-optimises every step inside that allowance",
            ]),
            ("Detailed explanation", [
                f"Physics digital twin of Maitri/Bharati on {f['n_years']} years of real ERA5 weather",
                "Loads built bottom-up: headcount, envelope heat loss, snow-melt water, 24x7 science",
                "PV modelled at 70 deg tilt with snow albedo and cold-temperature gain; wind with air-density gain",
                "LightGBM quantile forecasts (p10/p50/p90) for load, solar and wind, 1–48 h",
                "MILP commits gensets, battery, curtailment and tiered shedding on a 36 h horizon",
            ]),
            ("How it addresses the problem", [
                "Delivers all four asks of the statement: load forecasting, renewable integration, "
                "fuel optimisation, extreme conditions",
                "But scores them on survivability to the next ship, not on cost per kWh",
            ]),
            ("Innovation and uniqueness", [
                "Annual-resupply fuel budgeting — absent from every commercial EMS (ABB, Schneider, SMA)",
                "Science-integrity dispatch — generator hours priced when forecast wind carries "
                "exhaust into the station's own clean-air samplers",
                "Physical-availability forecasting — drifted panels and iced blades, which decorrelate "
                "from the weather forecast",
            ]),
        ],
        head_pt=12,
        body_pt=9.8,
    )
    s2.shapes.add_picture(str(figs["constraints"]), Inches(7.65), Inches(1.55), width=Inches(5.35))
    add_caption(s2, 7.65, 4.5, 5.35,
                "Left: same weather, same station, two tanks draining. Right: the sector in which "
                "our own exhaust would corrupt our own measurements.")

    # ---- slide 3: technical approach -------------------------------------
    set_title(s3, "TECHNICAL APPROACH", 26)
    box = shape_by_name(s3, "TextBox 8")
    box.left, box.top, box.width, box.height = Inches(0.55), Inches(1.15), Inches(12.3), Inches(2.6)
    write_block(
        box,
        [
            ("Technologies to be used", [
                "Python · pandas · NumPy · pvlib (PV physics) · LightGBM (quantile regression)",
                "PuLP + HiGHS mixed-integer solver · SciPy · Parquet cache · pytest",
                "ERA5 reanalysis (Copernicus, via an open archive API — no credentials, cached offline)",
                "Offline single-file HTML console (Plotly inlined) · Modbus + MQTT setpoint interface",
            ]),
            ("Methodology and process for implementation", [
                f"Closed-loop simulation of a {fnum(f['days'])}-day season ({f['season']}), "
                f"re-optimised on a {f['horizon_h']} h rolling horizon",
                "Four controllers on identical weather: fixed schedule, tuned rule-based, ours, "
                "perfect-foresight oracle — plus a point-forecast ablation",
                "Stress tests: four-day blizzard, 48 h genset failure, 12 h badly wrong forecast",
            ]),
        ],
        head_pt=12,
        body_pt=10,
    )
    s3.shapes.add_picture(str(figs["architecture"]), Inches(0.55), Inches(3.55), width=Inches(12.25))
    add_caption(s3, 0.55, 6.0, 12.25,
                "Every stage is implemented and runs end to end on one command; the whole pipeline "
                "works with the network down, which is the station's real operating condition.")

    # ---- slide 4: feasibility and viability ------------------------------
    set_title(s4, "FEASIBILITY AND VIABILITY", 26)
    box = shape_by_name(s4, "TextBox 8")
    box.left, box.top, box.width, box.height = Inches(0.55), Inches(1.15), Inches(7.0), Inches(5.6)
    write_block(
        box,
        [
            ("Analysis of the feasibility", [
                "Already built and running: no hardware, no data collection, no annotation",
                f"MILP solves in ~{fnum(f['solve_ms'])} ms per step — an edge laptop is enough",
                f"Forecast skill on an unseen year: load {fnum(f['load_mape'], 1)}% MAPE, "
                f"solar {fnum(f['pv_mape'], 1)}%, wind {fnum(f['wind_mape'], 1)}%",
                f"p90 forecast band covers {fnum(100 * (f['load_cover'] or 0))}% of hours — "
                "calibrated, which is what the reserve rule consumes",
            ]),
            ("Potential challenges and risks", [
                "No public electrical-load data exists for ANY polar research station",
                "Maitri II generator, PV and turbine ratings are not yet published",
                "The team writes both the simulator and the controller",
                "Margin over a well-tuned rule-based controller can be modest",
            ]),
            ("Strategies for overcoming these challenges", [
                "Load synthesised bottom-up from physics, never drawn; calibrated to published "
                "Antarctic station fuel-demand shapes",
                f"All {fnum(f['n_assumptions'])} parameters carry source, confidence and a sensitivity "
                "range, shown in an Assumptions tab in the demo",
                "Honest baselines: a tuned rule-based controller and a perfect-foresight oracle bound",
                "Framed as a sizing and operating-policy explorer for Maitri II, turning the unknown "
                "ratings into the use case",
            ]),
        ],
        head_pt=12,
        body_pt=9.6,
    )
    s4.shapes.add_picture(str(figs["results"]), Inches(7.7), Inches(1.5), width=Inches(5.3))
    add_caption(
        s4, 7.7, 4.35, 5.3,
        f"Same weather, same twin. Ours closes {fnum(f['gap_closed_pct'])}% of the gap between the "
        f"tuned baseline and a perfect-foresight bound, at {fnum(f['outages_c'])} critical outages "
        f"against {fnum(f['outages_b'])}.",
    )

    # ---- slide 5: impact and benefits ------------------------------------
    set_title(s5, "IMPACT AND BENEFITS", 26)
    box = shape_by_name(s5, "TextBox 8")
    box.left, box.top, box.width, box.height = Inches(0.55), Inches(1.15), Inches(7.0), Inches(5.6)
    write_block(
        box,
        [
            ("Potential impact on the target audience", [
                "NCPOR station engineers at Maitri and Bharati: decision support for a fatigued "
                "operator in polar night, not a replacement",
                "Maitri II — approved, ~Rs 2,000 crore, targeted 2029, explicitly a green station: "
                "this is an operating policy and a sizing tool for a station still on the drawing board",
                "Directly transferable to Himalayan, island and forward defence outposts on annual resupply",
            ]),
            ("Benefits of the solution", [
                f"Economic — {fnum(f['fuel_saved_l'])} L less diesel over the modelled season; "
                "fuel delivered by ice-class ship costs a multiple of its pump price",
                f"Operational — critical outages cut from {fnum(f['outages_b'])} to "
                f"{fnum(f['outages_c'])} on identical weather; unserved critical energy "
                f"{fnum(f['unserved_b'], 1)} → {fnum(f['unserved_c'], 1)} kWh",
                f"Scientific — clean-air sampling windows protected {fnum(f['clean_b'], 0)}% "
                f"→ {fnum(f['clean_c'], 0)}%, so the station's own record stays usable",
                f"Environmental — less combustion and fewer fuel movements in an Antarctic Treaty "
                f"protected area; renewables actually used {fnum(f['renew_c'], 0)}% of what they generate",
                "Strategic — an Indian-built operating policy for Indian polar assets, not a "
                "licensed foreign EMS",
            ]),
        ],
        head_pt=12,
        body_pt=9.6,
    )
    s5.shapes.add_picture(str(figs["impact"]), Inches(7.65), Inches(1.6), width=Inches(5.35))
    add_caption(s5, 7.65, 4.35, 5.35,
                "Measured over the simulated season on identical weather; every figure is "
                "regenerated from results/run.json, never typed in.")

    # ---- slide 6: research and references --------------------------------
    set_title(s6, "RESEARCH AND REFERENCES", 26)
    box = shape_by_name(s6, "TextBox 8")
    box.left, box.top, box.width, box.height = Inches(0.55), Inches(1.15), Inches(6.2), Inches(5.6)
    write_block(
        box,
        [
            ("Polar energy literature", [
                "de Witt, Chung & Lee (2024). Mapping Renewable Energy among Antarctic Research "
                "Stations. Sustainability 16(1), 426. doi:10.3390/su16010426",
                "Modeling Hybrid Renewable Microgrids in Remote Northern Regions. Energies (2025) "
                "18, 5827. doi:10.3390/en18215827",
                "Learning from Arctic Microgrids: cost and resiliency with hydrogen and battery "
                "storage. Sustainability (2025) 17, 5996. doi:10.3390/su17135996",
                "Parent & Ilinca (2011). Anti-icing and de-icing techniques for wind turbines. "
                "Cold Regions Science and Technology 65, 88–96",
                "MPC-based energy management for an isolated electro-thermal microgrid. "
                "Energy Conversion and Management (2024)",
            ]),
        ],
        head_pt=12,
        body_pt=9.2,
    )
    box2 = s6.shapes.add_textbox(Inches(6.95), Inches(1.15), Inches(5.95), Inches(5.6))
    write_block(
        box2,
        [
            ("Data and tooling", [
                "ERA5 reanalysis, Copernicus Climate Change Service / ECMWF — hourly, "
                f"{f['n_years']} years at 70.77 S, 11.73 E (Maitri, Schirmacher Oasis)",
                "pvlib-python for irradiance transposition and cell temperature",
                "HiGHS mixed-integer solver; LightGBM quantile regression",
                "SCAR READER, AMRC automatic weather stations, BSRN radiation — cross-checks",
            ]),
            ("Station and programme sources", [
                "NCPOR / COMNAP Antarctic Station Catalogue: Maitri (1989), Bharati (2012)",
                "Public reporting on Maitri II approval: ~Rs 2,000 crore, target January 2029, "
                "wind and solar powered, waste-heat recovery, unmanned data relay",
                "SIH 2026 portal record for SIH26061 (MoES / NCPOR, Clean & Green Technology)",
            ]),
            ("Our work", [
                f"Code, tests, results and the offline console: {repo}",
                "One command reproduces every number on these slides from real ERA5 weather",
            ]),
        ],
        head_pt=12,
        body_pt=9.2,
    )

    # team name ovals
    for slide in (s2, s3, s4, s5, s6):
        oval = next((sh for sh in slide.shapes if sh.name.startswith("Oval")), None)
        if oval is not None and oval.has_text_frame:
            tf = oval.text_frame
            tf.clear()
            run = tf.paragraphs[0].add_run()
            run.text = team
            run.font.size = Pt(10)
            run.font.bold = True
            run.font.name = FONT

    out = OUT_DIR / "SIH26061_Idea_Presentation.pptx"
    prs.save(str(out))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="<Team Name>")
    ap.add_argument("--team-id", default="<Team ID>")
    ap.add_argument("--repo", default="github.com/NSUT-SIH-26/NSUT-SIH-DEMO")
    args = ap.parse_args()

    out = build(args.team, args.team_id, args.repo)
    print(f"wrote {out}")
    print("Next: open in PowerPoint, check the two team fields, then Save As PDF "
          "(the portal accepts PDF only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
