"""Configuration loader.

The station config is the single source of truth for every physical number in
the project. Each leaf is either a plain value or a dict of the form
``{"value": x, "unit": ..., "source": ..., "confidence": ..., "sensitivity": [lo, hi]}``.

``load_config`` returns a :class:`Config` that resolves dotted paths to the bare
value, while keeping the annotated tree so the dashboard's Assumptions tab and
the sensitivity sweep can be generated from the same file. Nothing downstream
hard-codes a physical constant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "station.json"


def _unwrap(node: Any) -> Any:
    if isinstance(node, dict):
        if "value" in node and not any(isinstance(v, dict) for v in node.values()):
            return node["value"]
        return {k: _unwrap(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_unwrap(v) for v in node]
    return node


class Config:
    def __init__(self, raw: dict, path: Path | None = None):
        self.raw = raw
        self.path = path
        self.values = _unwrap(raw)

    def __getitem__(self, dotted: str) -> Any:
        node = self.values
        for part in dotted.split("."):
            if isinstance(node, list):
                node = node[int(part)]
            else:
                node = node[part]
        return node

    def get(self, dotted: str, default: Any = None) -> Any:
        try:
            return self[dotted]
        except (KeyError, IndexError):
            return default

    def override(self, dotted: str, value: Any) -> "Config":
        """Return a copy with one parameter replaced (used by the sensitivity sweep)."""
        raw = json.loads(json.dumps(self.raw))
        node = raw
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        leaf = node[parts[-1]]
        if isinstance(leaf, dict) and "value" in leaf:
            leaf["value"] = value
        else:
            node[parts[-1]] = value
        return Config(raw, self.path)

    def assumptions(self) -> list[dict]:
        """Flatten every annotated parameter into rows for the Assumptions tab."""
        rows: list[dict] = []

        def walk(node: Any, prefix: str) -> None:
            if isinstance(node, dict):
                if "value" in node and "source" in node:
                    rows.append(
                        {
                            "parameter": prefix,
                            "value": node["value"],
                            "unit": node.get("unit", ""),
                            "source": node["source"],
                            "confidence": node.get("confidence", "unstated"),
                            "sensitivity": node.get("sensitivity"),
                            "is_assumption": str(node["source"]).upper().startswith(
                                ("ASSUMPTION", "DESIGN CHOICE")
                            ),
                        }
                    )
                    return
                for key, value in node.items():
                    if key.startswith("_"):
                        continue
                    walk(value, f"{prefix}.{key}" if prefix else key)
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f"{prefix}.{i}")

        walk(self.raw, "")
        return rows


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as fh:
        return Config(json.load(fh), path)
