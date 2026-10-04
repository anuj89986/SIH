"""Watch the SIH portal record for SIH26061 and report any change.

The official statement is a single sentence today, and the theme, deadline and dataset link
have all moved at least once across community mirrors. Everything the submission is scored
against lives on that page, so it is worth checking rather than remembering.

    python watch_ps.py                 # fetch, print, diff against the last snapshot
    python watch_ps.py --ps 26060      # any other statement in the cluster
    python watch_ps.py --all-moes      # the whole MoES / NCPOR polar block

Snapshots are stored in data/ps_snapshots/. A non-zero exit code means something changed.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

PORTAL = "https://sih.gov.in/sih2026PS"
SNAP_DIR = Path(__file__).resolve().parent / "data" / "ps_snapshots"
FIELDS = ["Organization", "Department", "Category", "Theme", "Youtube Link", "Dataset Link", "Contact info"]


def fetch_portal(timeout: int = 90) -> str:
    resp = requests.get(PORTAL, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse(page: str) -> dict[str, dict]:
    """Pull every problem statement block out of the portal HTML."""
    out: dict[str, dict] = {}
    for chunk in page.split("Problem Statement ID")[1:]:
        text = re.sub(r"<[^>]+>", "|", chunk[:8000])
        text = html.unescape(text)
        lines = [ln.strip() for ln in re.sub(r"(\|\s*)+", "\n", text).split("\n") if ln.strip()]
        if not lines or not lines[0].isdigit():
            continue
        ps_id = lines[0]
        record = {"id": f"SIH{ps_id}", "title": lines[2] if len(lines) > 2 else ""}
        for field in FIELDS:
            if field in lines:
                i = lines.index(field)
                value = lines[i + 1] if i + 1 < len(lines) else ""
                if value in FIELDS or value.startswith("--"):  # empty cell or HTML comment
                    value = ""
                record[field.lower().replace(" ", "_")] = value
        desc = [ln for ln in lines if len(ln) > 80]
        record["description"] = desc[0] if desc else ""
        deadlines = [ln for ln in lines if re.fullmatch(r"\d{2}-\d{2}-\d{4}", ln)]
        record["deadline"] = deadlines[0] if deadlines else ""
        submissions = [ln for ln in lines if re.fullmatch(r"\d+/\d+", ln)]
        record["ideas_submitted"] = submissions[0] if submissions else ""
        out[record["id"]] = record
    return out


def diff(old: dict, new: dict) -> list[str]:
    changes = []
    for key in sorted(set(old) | set(new)):
        if key.startswith("_"):
            continue
        a, b = old.get(key, ""), new.get(key, "")
        if a != b:
            changes.append(f"  {key}: {a!r}  ->  {b!r}")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ps", default="26061")
    ap.add_argument("--all-moes", action="store_true", help="watch SIH26057-SIH26067")
    args = ap.parse_args()

    targets = [f"SIH{n}" for n in range(26057, 26068)] if args.all_moes else [f"SIH{args.ps}"]
    records = parse(fetch_portal())
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    changed = False

    for target in targets:
        record = records.get(target)
        if record is None:
            print(f"{target}: NOT FOUND on the portal (the listing may have been restructured)")
            changed = True
            continue
        record["_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        print(f"\n{target} - {record['title']}")
        for key in ("organization", "department", "category", "theme", "deadline",
                    "ideas_submitted", "youtube_link", "dataset_link"):
            if record.get(key):
                print(f"  {key:16s} {record[key]}")
        if record.get("description"):
            print(f"  description      {record['description']}")

        snap = SNAP_DIR / f"{target}.json"
        if snap.exists():
            previous = json.loads(snap.read_text(encoding="utf-8"))
            deltas = diff(
                {k: v for k, v in previous.items() if not k.startswith("_")},
                {k: v for k, v in record.items() if not k.startswith("_")},
            )
            if deltas:
                changed = True
                print("  CHANGED since last check:")
                print("\n".join(deltas))
            else:
                print("  (unchanged since last check)")
        else:
            print("  (first snapshot)")
        snap.write_text(json.dumps(record, indent=1), encoding="utf-8")

    if changed:
        print("\nSomething moved. Re-read the record before touching the pitch.")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
