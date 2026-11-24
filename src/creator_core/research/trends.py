"""TrendResearcher (module #6): trend snapshots from permitted sources only (ADR-013).

No scraping. Inputs are user-supplied JSON fixtures or the official Data API via Phase 8.
Snapshots are timestamped and retained so changes over time are measurable.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.research.provenance import ESTIMATED, HYPOTHESIS, VERIFIED, datum
from creator_core.workspace.atomic import atomic_write_json


class TrendResearcher:
    def __init__(self, research_dir: Path):
        self.dir = Path(research_dir)
        self.history_dir = self.dir / "trends" / "history"
        self.latest = self.dir / "trends" / "latest.json"

    def import_snapshot(self, path: Path, *, source: str = "local_fixture") -> dict:
        p = Path(path)
        if not p.is_file():
            raise ValidationError(f"trend file not found: {p}")
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid trend JSON: {exc}") from exc
        items = raw.get("topics") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ValidationError("trend snapshot must contain a 'topics' list")
        normalized = []
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValidationError("each trend topic must have a 'name'")
            normalized.append(
                {
                    "name": str(item["name"]),
                    "signal": float(item.get("signal", 0)),
                    "source_url": item.get("source_url"),
                    "observed_at": item.get("observed_at") or raw.get("retrieved_at"),
                }
            )
        snap = {
            "source": source,
            "verified": source == "youtube_data_api",
            "retrieved_at": raw.get("retrieved_at"),
            "topics": normalized,
        }
        self.history_dir.mkdir(parents=True, exist_ok=True)
        day = date.today().isoformat()
        atomic_write_json(self.history_dir / f"{day}.json", snap)
        atomic_write_json(self.latest, snap)
        return snap

    def analyze(self, snapshot: dict | None = None) -> dict:
        if snapshot is None:
            if not self.latest.is_file():
                return {
                    "module": "TrendResearcher",
                    "ok": False,
                    "hint": "import a trend snapshot with `creator trends --from-file <json>`",
                }
            snapshot = json.loads(self.latest.read_text(encoding="utf-8"))
        label = VERIFIED if snapshot.get("verified") else ESTIMATED
        topics = sorted(snapshot.get("topics", []), key=lambda t: t.get("signal", 0), reverse=True)
        previous = self._previous_snapshot()
        momentum = {}
        if previous:
            prev = {t["name"]: t.get("signal", 0) for t in previous.get("topics", [])}
            for t in topics:
                momentum[t["name"]] = round(t.get("signal", 0) - prev.get(t["name"], 0), 2)
        return {
            "module": "TrendResearcher",
            "ok": True,
            "source": snapshot.get("source"),
            "topics": [
                datum(
                    t,
                    label=label,
                    source=snapshot.get("source", "unknown"),
                    method="timestamped trend snapshot",
                )
                for t in topics
            ],
            "momentum": datum(
                momentum,
                label=ESTIMATED if momentum else HYPOTHESIS,
                source="trend snapshot history",
                method="signal delta vs previous snapshot",
            )
            if momentum
            else datum({}, label=HYPOTHESIS, source="none", method="only one snapshot available"),
            "note": "External topic signals are observations, not guarantees of channel performance.",
        }

    def _previous_snapshot(self) -> dict | None:
        if not self.history_dir.is_dir():
            return None
        files = sorted(p for p in self.history_dir.glob("*.json") if p != self.latest)
        if len(files) < 2:
            return None
        try:
            return json.loads(files[-2].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
