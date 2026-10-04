"""Results serialisation and the single-file dashboard.

Writes ``results/run.json`` (machine-readable, everything the demo shows) and
``dashboard/dashboard.html`` (self-contained: opens by double-click, no server,
no build step, works with the network down -- which is the point, given the
connectivity story).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config import REPO_ROOT, Config

RESULTS_DIR = REPO_ROOT / "results"
DASHBOARD_DIR = REPO_ROOT / "dashboard"


def _jsonable(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, pd.Series):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, float) and np.isnan(obj):
        return None
    return obj


def daily_series(log: pd.DataFrame) -> dict:
    daily = log.resample("D").agg(
        {
            "fuel_l": "sum",
            "fuel_remaining_l": "last",
            "renewable_kw": "sum",
            "curtail_kw": "sum",
            "load_kw": "sum",
            "unserved_critical_kwh": "sum",
            "gen_on_count": "mean",
            "soc_frac": "mean",
            "clean_air_violation_h": "sum",
        }
    )
    return {
        "date": [d.strftime("%Y-%m-%d") for d in daily.index],
        "fuel_l": daily["fuel_l"].round(1).tolist(),
        "fuel_remaining_l": daily["fuel_remaining_l"].round(0).tolist(),
        "renewable_kwh": daily["renewable_kw"].round(1).tolist(),
        "curtailed_kwh": daily["curtail_kw"].round(1).tolist(),
        "load_kwh": daily["load_kw"].round(1).tolist(),
        "unserved_critical_kwh": daily["unserved_critical_kwh"].round(2).tolist(),
        "gen_on_mean": daily["gen_on_count"].round(2).tolist(),
        "soc_frac": daily["soc_frac"].round(3).tolist(),
        "clean_air_violation_h": daily["clean_air_violation_h"].round(1).tolist(),
    }


def hourly_window(log: pd.DataFrame, start: str, days: int = 7) -> dict:
    seg = log.loc[start:].head(days * 24)
    return {
        "time": [t.strftime("%Y-%m-%d %H:%M") for t in seg.index],
        "pv_kw": seg["pv_kw"].round(2).tolist(),
        "wind_kw": seg["wind_kw"].round(2).tolist(),
        "gen_kw": seg["gen_total_kw"].round(2).tolist(),
        "discharge_kw": seg["discharge_kw"].round(2).tolist(),
        "charge_kw": seg["charge_kw"].round(2).tolist(),
        "load_kw": seg["load_kw"].round(2).tolist(),
        "soc_frac": seg["soc_frac"].round(3).tolist(),
        "clean_air_window": seg["clean_air_window"].astype(int).tolist(),
        "clean_air_violation": seg["clean_air_violation_h"].astype(int).tolist(),
        "unserved_critical_kwh": seg["unserved_critical_kwh"].round(3).tolist(),
    }


def validation_signals(twin: pd.DataFrame) -> dict:
    """Three signals nobody fitted: winter load peak at minimum occupancy,
    seasonal wind/solar complementarity, and the cold-air density bonus."""
    monthly = twin.resample("MS").mean(numeric_only=True)
    return {
        "month": [d.strftime("%Y-%m") for d in monthly.index],
        "load_kw": monthly["load_kw"].round(2).tolist(),
        "occupancy": monthly["occupancy"].round(1).tolist(),
        "pv_kw": monthly["pv_kw"].round(2).tolist(),
        "wind_kw": monthly["wind_kw"].round(2).tolist(),
        "temp_c": monthly["temp_c"].round(2).tolist(),
        "snow_cover": monthly["snow_cover"].round(3).tolist(),
        "icing_risk": monthly["icing_risk"].round(3).tolist(),
        "pv_potential_kw": monthly["pv_potential_kw"].round(2).tolist(),
        "wind_potential_kw": monthly["wind_potential_kw"].round(2).tolist(),
    }


def write_results(payload: dict, name: str = "run.json") -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_jsonable(payload), fh, indent=1)
    return path


CDN_TAG = '<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.2/plotly.min.js"></script>'


def build_dashboard(payload: dict, out_name: str = "dashboard.html", inline_vendor: bool = True) -> Path:
    """Render the template with the run embedded, and the plotting library with it.

    Inlining the vendor bundle is not a nicety: the connectivity story is part of the
    submission, and a dashboard that needs a CDN cannot be demonstrated on a station laptop
    with the link down -- or in a hall with bad Wi-Fi.
    """
    html = (DASHBOARD_DIR / "template.html").read_text(encoding="utf-8")
    data = json.dumps(_jsonable(payload), separators=(",", ":"))
    html = html.replace("/*__DATA__*/null", data)

    vendor = DASHBOARD_DIR / "vendor" / "plotly.min.js"
    if inline_vendor and vendor.exists():
        js = vendor.read_text(encoding="utf-8").replace("</script>", r"<\/script>")
        html = html.replace(CDN_TAG, f"<script>{js}</script>")

    out = DASHBOARD_DIR / out_name
    out.write_text(html, encoding="utf-8")
    return out


def assemble(
    cfg: Config,
    twin: pd.DataFrame,
    logs: dict[str, pd.DataFrame],
    summaries: list[dict],
    table: pd.DataFrame,
    headline: dict,
    fuel_plan,
    forecast_report: dict,
    scenario_results: dict,
    run_params: dict,
) -> dict:
    demo_start = run_params.get("demo_week_start")
    if demo_start is None:
        idx = next(iter(logs.values())).index
        demo_start = (idx[len(idx) // 2]).strftime("%Y-%m-%d")
    return {
        "meta": {
            **cfg.raw["_meta"],
            "station": cfg["station.name"],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_params": run_params,
        },
        "headline": headline,
        "metrics": [_jsonable(s) for s in summaries],
        "comparison": json.loads(table.reset_index().to_json(orient="records")),
        "fuel_plan": {
            "metrics": fuel_plan.metrics,
            "allowance": {
                "date": [d.strftime("%Y-%m-%d") for d in fuel_plan.allowance.index],
                "litres": fuel_plan.allowance.round(1).tolist(),
            },
        },
        "forecast": forecast_report,
        "validation": validation_signals(twin),
        "daily": {name: daily_series(log) for name, log in logs.items()},
        "demo_week": {
            name: hourly_window(log, demo_start) for name, log in logs.items() if name in ("A", "B", "C")
        },
        "scenarios": scenario_results,
        "assumptions": cfg.assumptions(),
    }
