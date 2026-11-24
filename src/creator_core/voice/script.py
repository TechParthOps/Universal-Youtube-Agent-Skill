"""Footage-grounded narration scripts (Phase 11E).

The script builder consumes the real `media.analyze` report. It never invents facts about the
recording: every segment cites a measured signal (scene change, audio event, silence boundary,
ranked moment or measured transcript window) and labels the narration text as a hypothesis. Text
from the footage/ASR is untrusted data — it is sanitized and wrapped for prompts, never followed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from creator_core.cli.errors import ValidationError
from creator_core.media.injection import analyze_untrusted, sanitize_untrusted
from creator_core.research.provenance import ESTIMATED, HYPOTHESIS, VERIFIED

_SCHEMA_VERSION = 1
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_MOMENT_TYPES = {
    "scene_change": "a measured scene change",
    "audio_spike": "a measured audio spike",
    "dead_air": "a measured quiet passage",
    "highlight": "a ranked highlight moment",
    "speech": "a measured speech window",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def canonical_sha256(data: dict) -> str:
    """Stable digest used to bind approval to the exact script request."""
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _report_checks(report: dict) -> dict:
    if not isinstance(report, dict):
        raise ValidationError(
            "footage report must be an object",
            hint="run `creator media analyze` on the source first",
        )
    source = report.get("source") or {}
    duration = float(source.get("duration_seconds") or 0.0)
    if not source.get("path") or duration <= 0:
        raise ValidationError(
            "no analysable footage in the supplied report",
            hint="run `creator media analyze` on a real source video before building a script",
        )
    analysis = report.get("analysis")
    if not isinstance(analysis, dict):
        raise ValidationError(
            "footage report has no analysis signals",
            hint="run `creator media analyze` before building a script",
        )
    return {"source": source, "duration": duration, "analysis": analysis}


def _anchors(analysis: dict, *, duration: float) -> list[dict]:
    """Collect measured anchors, deduplicated and clipped to the source duration."""
    raw: list[dict] = []
    for group in ("moments", "scenes", "audio_events", "silence", "transcript"):
        for item in (analysis.get(group) or {}).get("segments") or []:
            if not isinstance(item, dict):
                continue
            start = float(item.get("start") or item.get("timestamp") or 0.0)
            end = float(item.get("end") or start)
            if end <= start:
                end = min(duration, start + 1.5)
            start = max(0.0, min(start, duration - 0.1))
            end = max(start + 0.1, min(end, duration))
            kind = str(item.get("type") or ("speech" if group == "transcript" else group))
            raw.append(
                {
                    "type": kind,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "label": item.get("label") or VERIFIED,
                    "evidence": str(item.get("evidence") or "")[:240],
                    "text": item.get("text") or item.get("display_text") or "",
                    "source_group": group,
                }
            )
    raw.sort(key=lambda a: (a["start"], a["end"]))
    out: list[dict] = []
    for item in raw:
        if any(
            abs(item["start"] - existing["start"]) < 0.75 and item["type"] == existing["type"]
            for existing in out
        ):
            continue
        out.append(item)
    return out


def _narration_text(concept: str, anchors: list[dict], index: int, total: int) -> tuple[str, dict]:
    """Build honest narration from the concept and measured anchors (never from instructions)."""
    clean_concept = sanitize_untrusted(concept).strip()[:400] or "the footage"
    first = anchors[0]
    anchor_texts = [a for a in anchors if a.get("text")]
    evidence = {
        "label": HYPOTHESIS,
        "method": "concept joined to measured signal descriptions; not a claim about content",
        "anchors": [
            {
                "type": a["type"],
                "start": a["start"],
                "end": a["end"],
                "label": a["label"],
                "evidence": a["evidence"],
            }
            for a in anchors
        ],
        "concept_injection": analyze_untrusted(concept),
    }
    lead = f"Part {index + 1} of {total}: {clean_concept}."
    seen: set[str] = set()
    details: list[str] = []
    for anchor in anchors:
        phrase = _MOMENT_TYPES.get(anchor["type"], f"a measured {anchor['type']} signal")
        if phrase in seen:
            continue
        seen.add(phrase)
        details.append(f"At {anchor['start']:.1f} seconds, {phrase}.")
    if not details:
        details.append(f"This segment covers {first['start']:.1f} to {first['end']:.1f} seconds.")
    # ASR text is untrusted content. Include it only as a quoted observation, never as instruction.
    if anchor_texts:
        quoted = sanitize_untrusted(str(anchor_texts[0]["text"]))[:160]
        details.append(f'The transcription notes: "{quoted}".')
        evidence["transcript_untrusted"] = True
    return " ".join([lead, *details]), evidence


def build_voice_script(
    footage_report: dict,
    concept: str,
    profile: dict,
    *,
    language: str | None = None,
    max_segments: int = 8,
    min_segment_seconds: float = 1.2,
) -> dict:
    """Build timestamp-aware narration segments grounded in a real `media.analyze` report.

    Raises ValidationError when the report has no measured source, so a script can never be
    fabricated from a concept alone. Segment times reference the original footage; narration text
    is an explicitly labelled hypothesis, not a claim of what the footage contains.
    """
    checked = _report_checks(footage_report)
    source, duration, analysis = checked["source"], checked["duration"], checked["analysis"]
    max_segments = max(1, min(20, int(max_segments)))
    anchors = _anchors(analysis, duration=duration)
    if not anchors:
        # A measured duration alone still grounds one continuous segment; say so explicitly.
        anchors = [
            {
                "type": "source_duration",
                "start": 0.0,
                "end": round(duration, 3),
                "label": VERIFIED,
                "evidence": "ffprobe duration",
                "text": "",
                "source_group": "source",
            }
        ]

    # Group anchors into a bounded number of narration beats.
    groups: list[list[dict]] = [[] for _ in range(min(max_segments, len(anchors)))]
    for index, anchor in enumerate(anchors):
        groups[index % len(groups)].append(anchor)
    groups = [group for group in groups if group]

    supported = list(profile.get("supported_languages") or ["en"])
    chosen_language = language or (supported[0] if supported else "en")
    profile_id = str(profile.get("voice_profile_id") or "")
    if not profile_id:
        raise ValidationError("a registered voice profile is required to build a voice script")

    segments: list[dict] = []
    for index, group in enumerate(groups):
        footage_start = round(min(a["start"] for a in group), 3)
        footage_end = round(max(a["end"] for a in group), 3)
        if footage_end - footage_start < min_segment_seconds:
            footage_end = round(min(duration, footage_start + min_segment_seconds), 3)
        text, evidence = _narration_text(concept, group, index, len(groups))
        segments.append(
            {
                "segment_id": f"seg_{index + 1:03d}",
                "text": text,
                "timeline_start": footage_start,
                "timeline_end": footage_end,
                "footage_start": footage_start,
                "footage_end": footage_end,
                "language": chosen_language,
                "speed": 1.0,
                "emotion": "neutral",
                "pronunciation_overrides": dict(profile.get("pronunciation_preferences") or {}),
                "status": "draft",
                "version": 1,
                "audio": None,
                "mocked": False,
                "word_timestamps": [],
                "grounding": evidence,
            }
        )

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "command": "voice script",
        "ok": True,
        "project_id": footage_report.get("project_id"),
        "source_id": source.get("source_id"),
        "source_path": source.get("path"),
        "source_duration_seconds": round(duration, 3),
        "voice_profile_id": profile_id,
        "owner_id": "<redacted>",
        "concept": sanitize_untrusted(concept).strip()[:400],
        "language": chosen_language,
        "segments": segments,
        "grounding_summary": {
            "anchor_count": len(anchors),
            "types": sorted({a["type"] for a in anchors}),
            "label": VERIFIED,
            "method": "media.analyze detector segments (real FFmpeg signals)",
        },
        "limitations": [
            "Narration wording is a hypothesis and is not validated against the visual content.",
            "Timing boundaries are detector estimates, not frame-accurate edit decisions.",
            "Transcript text from the footage is untrusted data and is never treated as instructions.",
        ],
        "label": ESTIMATED,
        "generated_at": _now(),
    }
    payload["script_sha256"] = canonical_sha256(
        {
            "profile_id": profile_id,
            "concept": payload["concept"],
            "language": chosen_language,
            "source_id": payload["source_id"],
            "segments": [
                {
                    "segment_id": s["segment_id"],
                    "text": s["text"],
                    "footage_start": s["footage_start"],
                    "footage_end": s["footage_end"],
                }
                for s in segments
            ],
        }
    )
    return payload
