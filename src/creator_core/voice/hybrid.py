"""Hybrid narration section classification (Phase 11I).

Sections are derived only from real `media.analyze` signals. Original spoken passages are kept by
default; generated narration is inserted into gameplay-only or silent passages. Nothing is
overwritten in the source media — these are instructions for the non-destructive mix pass.
"""

from __future__ import annotations

from creator_core.cli.errors import ValidationError
from creator_core.research.provenance import ESTIMATED, VERIFIED

_SCHEMA_VERSION = 1
_ACTIONS = ("preserve", "insert", "replace", "silence")


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start < b_end and b_start < a_end


def classify_sections(footage_report: dict, *, window_seconds: float = 2.0) -> dict:
    """Classify a source into original / gameplay_only / silent_reaction / generated windows."""
    source = footage_report.get("source") or {}
    duration = float(source.get("duration_seconds") or 0.0)
    analysis = footage_report.get("analysis") or {}
    if duration <= 0 or not isinstance(analysis, dict):
        raise ValidationError("a real media analysis report with source duration is required")
    window_seconds = max(0.5, min(10.0, float(window_seconds)))

    silence = [
        s for s in (analysis.get("silence") or {}).get("segments") or [] if isinstance(s, dict)
    ]
    spikes = [
        s for s in (analysis.get("audio_events") or {}).get("segments") or [] if isinstance(s, dict)
    ]
    speech = [
        s for s in (analysis.get("transcript") or {}).get("segments") or [] if isinstance(s, dict)
    ]

    sections: list[dict] = []
    index = 0
    cursor = 0.0
    while cursor < duration:
        end = min(duration, cursor + window_seconds)
        is_silent = any(
            _overlaps(cursor, end, float(s.get("start") or 0), float(s.get("end") or 0))
            for s in silence
        )
        has_speech = any(
            _overlaps(cursor, end, float(s.get("start") or 0), float(s.get("end") or 0))
            for s in speech
        )
        has_spike = any(
            _overlaps(cursor, end, float(s.get("start") or 0), float(s.get("end") or 0))
            for s in spikes
        )
        if has_speech or has_spike:
            kind = "original"
        elif is_silent:
            kind = "silent_reaction"
        else:
            kind = "gameplay_only"
        action = "preserve" if kind == "original" else "insert"
        if sections and sections[-1]["type"] == kind and sections[-1]["end"] == round(cursor, 3):
            sections[-1]["end"] = round(end, 3)
        else:
            index += 1
            sections.append(
                {
                    "section_id": f"sec_{index:03d}",
                    "type": kind,
                    "start": round(cursor, 3),
                    "end": round(end, 3),
                    "default_action": action,
                    "action": action,
                    "label": VERIFIED,
                    "evidence": (
                        "transcript/scene/audio detector signals from media.analyze; "
                        "classification is heuristic"
                    ),
                }
            )
        cursor = end

    return {
        "schema_version": _SCHEMA_VERSION,
        "command": "voice hybrid",
        "ok": True,
        "source_id": source.get("source_id"),
        "source_duration_seconds": round(duration, 3),
        "sections": sections,
        "allowed_actions": list(_ACTIONS),
        "label": ESTIMATED,
        "limitations": [
            "Signal classification cannot identify a specific speaker; it only marks likely speech.",
            "Original passages are preserved by default; every change is non-destructive.",
        ],
    }


def apply_section_actions(sections: dict, actions: dict[str, str]) -> dict:
    """Apply creator-permitted per-section actions with strict validation."""
    if not isinstance(actions, dict):
        raise ValidationError("hybrid section actions must be an object")
    result = sections.copy()
    result["sections"] = [dict(section) for section in sections.get("sections") or []]
    known = {s["section_id"] for s in result["sections"]}
    unknown = set(actions) - known
    if unknown:
        raise ValidationError(f"unknown hybrid section id(s): {', '.join(sorted(unknown))}")
    for section in result["sections"]:
        action = actions.get(section["section_id"], section["default_action"])
        if action not in _ACTIONS:
            raise ValidationError(f"invalid hybrid action for {section['section_id']}: {action!r}")
        section["action"] = action
    return result
