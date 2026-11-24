"""Declarative editing strategies: analysis signals → an EDL with explainable decisions.

Strategies never reorder footage or invent semantic claims. They remove signal-detected dead
air, mark signal-backed moments, and optionally add zoom/captions when evidence exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from creator_core.cli.errors import ValidationError
from creator_core.editing.edl import empty_edl, utc_now
from creator_core.media.injection import sanitize_untrusted
from creator_core.research.provenance import HYPOTHESIS


@dataclass(frozen=True)
class Strategy:
    name: str
    description: str
    min_dead_air_s: float
    keep_moments: bool
    zoom_on_moments: bool
    captions_from_transcript: bool
    hook_marker: bool
    filler_terms: tuple[str, ...] = ()


STRATEGIES: dict[str, Strategy] = {
    "gaming": Strategy(
        "gaming",
        "Remove dead air, mark signal peaks, optional punch-in zoom on peaks.",
        1.2,
        True,
        True,
        True,
        True,
        ("um", "uh", "you know", "like, "),
    ),
    "commentary": Strategy(
        "commentary",
        "Remove dead air and filler, keep speech coherent.",
        0.9,
        True,
        False,
        True,
        True,
        ("um", "uh", "you know", "basically, ", "like, "),
    ),
    "educational": Strategy(
        "educational",
        "Gentler dead-air removal, captions from transcript when available.",
        1.4,
        True,
        False,
        True,
        True,
        ("um", "uh", "you know"),
    ),
    "interview": Strategy(
        "interview",
        "Very conservative edits; preserves conversational pauses.",
        2.0,
        False,
        False,
        True,
        False,
        (),
    ),
    "tutorial": Strategy(
        "tutorial",
        "Step-friendly pacing: removes dead air but keeps every demonstration beat.",
        1.0,
        True,
        False,
        True,
        True,
        ("um", "uh", "you know", "like, "),
    ),
    "documentary": Strategy(
        "documentary",
        "Conservative, atmospheric cuts; preserves pauses and narration context.",
        1.6,
        True,
        False,
        True,
        False,
        ("um", "you know"),
    ),
    "vlog": Strategy(
        "vlog",
        "Energetic tight cuts with optional punch-ins on signal peaks.",
        0.7,
        True,
        True,
        True,
        True,
        ("um", "uh", "you know", "like, ", "basically, "),
    ),
}


def list_strategies() -> list[dict]:
    return [
        {"name": s.name, "description": s.description, "min_dead_air_s": s.min_dead_air_s}
        for s in STRATEGIES.values()
    ]


def get_strategy(name: str) -> Strategy:
    try:
        return STRATEGIES[name]
    except KeyError as exc:
        raise ValidationError(
            f"unknown editing strategy: {name}", hint="available: " + ", ".join(STRATEGIES)
        ) from exc


def _merge_ranges(
    ranges: list[tuple[float, float]], tolerance: float = 0.15
) -> list[tuple[float, float]]:
    if not ranges:
        return []
    out = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1] + tolerance:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _keep_ranges(duration: float, removed: list[tuple[float, float]]) -> list[tuple[float, float]]:
    kept = []
    cursor = 0.0
    for start, end in sorted(removed):
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            continue
        if start - cursor > 0.2:
            kept.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor > 0.2:
        kept.append((cursor, duration))
    return kept


def _timeline_time(source_time: float, removed: list[tuple[float, float]]) -> float:
    shift = sum(max(0.0, min(source_time, end) - start) for start, end in removed)
    return max(0.0, source_time - shift)


def _detection_segments(report: dict, key: str) -> list[dict]:
    if not report:
        return []
    return ((report.get("analysis") or {}).get(key) or {}).get("segments") or []


def _detector_health(report: dict) -> dict:
    """Record which detectors were unavailable/failed so degradation is never silent (M-04)."""
    analysis = (report or {}).get("analysis") or {}
    if not analysis:
        return {"available": False, "failed": [], "detail": {}}
    failed: list[str] = []
    detail: dict[str, dict] = {}
    for key in ("silence", "scenes", "audio_events", "transcript", "moments"):
        block = analysis.get(key) or {}
        if block.get("ok") is False:
            failed.append(key)
            detail[key] = {
                "error": str(block.get("error") or "")[:300],
                "label": block.get("label", HYPOTHESIS),
            }
    return {"available": True, "failed": failed, "detail": detail}


def build_edl(
    source: dict, report: dict, *, project_id: str, strategy: str, concept: str = ""
) -> dict:
    """Build an EDL v1 from a source inspection and a media analysis report."""
    spec = get_strategy(strategy)
    duration = float(source["duration_seconds"])
    if duration <= 0:
        raise ValidationError("source duration must be positive")
    detector_health = _detector_health(report)

    silence = _detection_segments(report, "silence")
    removable = [
        (float(s["start"]), float(s["end"]))
        for s in silence
        if float(s.get("duration", float(s["end"]) - float(s["start"]))) >= spec.min_dead_air_s
    ]
    removable = _merge_ranges(removable)
    keep = _keep_ranges(duration, removable)
    if not keep:
        raise ValidationError("strategy would remove the entire source; no edit produced")

    edl = empty_edl(project_id, source, strategy=f"{strategy}.v1", concept=concept)
    edl["provenance"]["detectors"] = detector_health
    if not detector_health["available"]:
        edl["limitations"] = [
            "No analysis report was supplied; the EDL is a full-length passthrough, not an edit.",
        ]
    elif detector_health["failed"]:
        edl["limitations"] = [
            "Detector(s) failed and contributed no signals: "
            + ", ".join(detector_health["failed"])
            + ". No edits were inferred from them; review this EDL before rendering.",
        ]
    edl["removed_ranges"] = [
        {
            "source": "src1",
            "in": round(a, 3),
            "out": round(b, 3),
            "reason": "dead_air",
            "restorable": True,
        }
        for a, b in removable
    ]
    edl["provenance"]["decisions"].append(
        {
            "op": "remove_dead_air",
            "evidence": f"ffmpeg silencedetect noise=-35dB, gaps >= {spec.min_dead_air_s}s "
            f"({len(removable)} range(s))",
            "approved_by": None,
            "at": utc_now(),
        }
    )

    timeline_cursor = 0.0
    for i, (a, b) in enumerate(keep, start=1):
        clip = {
            "clip_id": f"v{i}",
            "source": "src1",
            "source_in": round(a, 3),
            "source_out": round(b, 3),
            "timeline_in": round(timeline_cursor, 3),
            "speed": 1.0,
            "effects": [],
        }
        edl["tracks"]["video"].append(clip)
        edl["tracks"]["audio"].append(
            {
                "clip_id": f"a{i}",
                "source": "src1",
                "source_in": round(a, 3),
                "source_out": round(b, 3),
                "timeline_in": round(timeline_cursor, 3),
                "gain_db": 0.0,
            }
        )
        timeline_cursor += b - a
        if i > 1:
            edl["transitions"].append({"between": [f"v{i - 1}", f"v{i}"], "type": "cut"})

    moments = _detection_segments(report, "moments") if spec.keep_moments else []
    skipped_zooms = 0
    for i, moment in enumerate(moments[:8], start=1):
        center = float(moment.get("center", (moment["start"] + moment["end"]) / 2))
        t = round(_timeline_time(center, removable), 3)
        edl["markers"].append(
            {
                "at": t,
                "label": f"highlight_{i}",
                "evidence": moment.get("evidence", ""),
                "label_type": "estimated",
            }
        )
        if spec.zoom_on_moments:
            clip = _clip_at(edl, t)
            if clip is None:
                continue
            # EDL v1 renders at most one zoom per clip. A second signal peak in the same clip
            # must be disclosed as a limitation, never silently emitted or truncated later.
            if any(effect.get("type") == "zoom" for effect in clip.get("effects", [])):
                skipped_zooms += 1
                continue
            local_start = max(0.0, t - float(clip["timeline_in"]))
            local_end = local_start + max(
                0.4,
                float(moment.get("end", center + 0.5)) - float(moment.get("start", center - 0.5)),
            )
            clip["effects"].append(
                {
                    "type": "zoom",
                    "factor": 1.18,
                    "anchor": "center",
                    "start_at": round(local_start, 3),
                    "end_at": round(local_end, 3),
                    "label_type": "estimated",
                    "note": "signal-backed punch-in; creator review advised",
                }
            )

    if spec.hook_marker:
        edl["markers"].insert(
            0,
            {
                "at": 0.0,
                "label": "hook",
                "label_type": HYPOTHESIS,
                "note": "first frame suggestion, not a tested hook",
            },
        )

    if spec.captions_from_transcript:
        for i, seg in enumerate(
            _detection_segments(report, "transcript")
            or ((report.get("analysis") or {}).get("transcript") or {}).get("segments", []),
            start=1,
        ):
            start = float(seg["start"])
            end = float(seg["end"])
            t0 = _timeline_time(start, removable)
            t1 = _timeline_time(end, removable)
            if t1 - t0 < 0.2:
                continue
            edl["tracks"]["captions"].append(
                {
                    "cue_id": f"cap{i}",
                    "start": round(t0, 3),
                    "end": round(t1, 3),
                    "text": sanitize_untrusted(seg.get("text", "")).strip(),
                    "style": "default",
                    "label_type": "estimated",
                }
            )

    if skipped_zooms:
        edl.setdefault("limitations", []).append(
            f"{skipped_zooms} signal-backed zoom suggestion(s) were omitted because EDL v1 "
            "supports only one zoom per clip; the markers remain in the timeline."
        )
    edl["provenance"]["decisions"].append(
        {
            "op": "build_timeline",
            "evidence": f"{len(keep)} kept range(s) from {duration:.2f}s source "
            f"using {spec.name}.v1",
            "approved_by": None,
            "at": utc_now(),
        }
    )
    return edl


def _clip_at(edl: dict, timeline_time: float) -> dict | None:
    from creator_core.editing.edl import clip_timeline_out

    for clip in edl["tracks"]["video"]:
        if float(clip["timeline_in"]) <= timeline_time <= clip_timeline_out(clip):
            return clip
    return None
