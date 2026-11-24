"""Searchable media index: segments with timestamps, evidence and confidence.

Search is deterministic keyword scoring over derived signals. Untrusted transcript text is
sanitized before it reaches search results or any optional AI prompt.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from creator_core.cli.errors import ValidationError
from creator_core.media.injection import sanitize_untrusted
from creator_core.research.provenance import ESTIMATED, HYPOTHESIS
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import WorkspacePaths, project_dir

SEGMENT_TYPES = {"dead_air", "scene_change", "audio_spike", "speech", "highlight"}

# Generated labels (not transcript text) so signal segments are discoverable offline.
TYPE_LABELS = {
    "dead_air": "silence dead air quiet pause",
    "scene_change": "scene change cut visual transition",
    "audio_spike": "audio spike loud hit shout energy",
    "highlight": "highlight exciting moment best part",
}


def _text_for(segment: dict) -> str:
    raw = segment.get("text") or segment.get("display_text") or ""
    return sanitize_untrusted(raw)


def build_index(report: dict, ws: WorkspacePaths) -> dict:
    """Merge an analysis report into the project's media_index.json."""
    project = project_dir(ws, report["project_id"], create=True)
    path = project / "media_index.json"
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        index = {"schema_version": 1, "segments": [], "sources": {}}
    index.setdefault("sources", {})
    index.setdefault("segments", [])
    source_id = report["source"]["source_id"]
    index["sources"][source_id] = {
        "path": report["source"]["path"],
        "sha256": report["source"]["sha256"],
        "duration_seconds": report["source"]["duration_seconds"],
    }
    # Replace this source's previous derived segments (idempotent rebuilds).
    index["segments"] = [s for s in index["segments"] if s.get("source_id") != source_id]

    analysis = report["analysis"]
    for key in ("silence", "scenes", "audio_events", "moments"):
        detector = analysis.get(key) or {}
        for seg in detector.get("segments") or []:
            entry = {
                "source_id": source_id,
                "type": seg["type"],
                "start": float(seg["start"]),
                "end": float(seg.get("end", seg["start"])),
                "text": TYPE_LABELS.get(seg["type"], ""),
                "confidence": seg.get("confidence", 0.5),
                "evidence": seg.get("evidence", detector.get("method", key)),
                "label": seg.get("label", ESTIMATED),
            }
            if seg.get("timestamp") is not None:
                entry["timestamp"] = seg["timestamp"]
            index["segments"].append(entry)

    for seg in (analysis.get("transcript") or {}).get("segments") or []:
        index["segments"].append(
            {
                "source_id": source_id,
                "type": "speech",
                "start": float(seg["start"]),
                "end": float(seg["end"]),
                "text": sanitize_untrusted(seg.get("text", "")),
                "confidence": seg.get("confidence", 0.5),
                "evidence": "ASR transcript segment",
                "label": ESTIMATED,
                "injection": seg.get("injection", {"flagged": False}),
            }
        )

    index["segments"].sort(key=lambda s: (s["source_id"], s["start"], s["type"]))
    index["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    atomic_write_json(path, index)
    return index


def load_index(ws: WorkspacePaths, project_id: str) -> dict:
    path = project_dir(ws, project_id) / "media_index.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"no usable media index for project {project_id}",
            hint="run `creator media analyze <file> --project ID` first",
        ) from exc


def search(
    index: dict, query: str, *, limit: int = 10, types: set[str] | None = None
) -> list[dict]:
    terms = [t for t in re.findall(r"[a-z0-9+#]{2,}", query.lower()) if t]
    if not terms:
        raise ValidationError("search query must contain at least one word")
    allowed = types or SEGMENT_TYPES
    results = []
    for seg in index.get("segments", []):
        if seg.get("type") not in allowed:
            continue
        text = _text_for(seg).lower()
        hits = sum(1 for term in terms if term in text)
        if not hits:
            continue
        score = hits / len(terms)
        if seg.get("type") == "speech":
            score *= 1.2
        results.append(
            {
                "source_id": seg["source_id"],
                "type": seg["type"],
                "start": seg["start"],
                "end": seg["end"],
                "text": _text_for(seg),
                "confidence": round(min(1.0, seg.get("confidence", 0.5) * score), 3),
                "match_score": round(score, 3),
                "evidence": seg.get("evidence"),
                "label": seg.get("label", HYPOTHESIS),
                "injection": seg.get("injection", {"flagged": False}),
            }
        )
    results.sort(key=lambda r: (r["match_score"], r["confidence"]), reverse=True)
    return results[: max(1, min(int(limit), 50))]
