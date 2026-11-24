"""Natural-language revision requests → explicit, reviewable EDL diffs.

Deterministic and LLM-free: requests are matched against a fixed set of intents, numeric
arguments are parsed with bounded patterns, and every produced operation is data that an agent
or human can inspect before it is applied. Unmatched or ambiguous requests are returned as
`needs_clarification` rather than silently guessing.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.editing.edl import (
    clip_duration,
    derive,
    edl_duration,
    utc_now,
    validate_edl,
)
from creator_core.media.injection import sanitize_untrusted
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import WorkspacePaths, project_dir

MAX_REQUEST_CHARS = 500

_TIMECODE = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d)(?!\d)")
_SECONDS = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)(?!\w)")
_DB = re.compile(r"(-?\d+(?:\.\d+)?)\s*db\b", re.IGNORECASE)
_MULTIPLIER = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*x\b|\bdouble\b|\bhalf\b", re.IGNORECASE)

# (name, pattern). Priority order: specific requests before generic ones.
_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    (
        "restore",
        re.compile(
            r"\b(restore|reinsert|re-insert|bring back|undo (?:the )?delet\w*|undelete|add back)\b"
            r"|\bpart\b.{0,20}\bdelet\w*\b",
            re.IGNORECASE,
        ),
    ),
    (
        "excite_start",
        re.compile(
            r"\b(first|opening|intro|start|beginning)\b.{0,60}\b(excit\w*|energ\w*|punch\w*|tight\w*|fast\w*|dynamic|zoom\w*|interesting)\b"
            r"|\b(excit\w*|energ\w*|punch\w*|tight\w*|fast\w*|dynamic|zoom\w*)\b.{0,60}\b(first|opening|intro|start|beginning)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "remove_wait",
        re.compile(
            r"\b(remove|cut|drop|trim|delete|get rid of|tighten|shorten)\b.{0,50}"
            r"\b(wait|waiting|dead air|silence|silent|pause|paus|boring|slow part|unnecessary)\b"
            r"|\b(wait|waiting|dead air|silence|silent|pause|boring|unnecessary)\b.{0,50}"
            r"\b(remove|cut|drop|trim|delete|out)\b",
            re.IGNORECASE,
        ),
    ),
    ("captions", re.compile(r"\b(subtitle|subtitles|caption|captions)\b", re.IGNORECASE)),
    ("music", re.compile(r"\b(music|background track|commentary|duck|ducking)\b", re.IGNORECASE)),
    (
        "transition",
        re.compile(r"\b(fade|transition|dissolve|wipe|cross[- ]?fade)\b", re.IGNORECASE),
    ),
    (
        "speed",
        re.compile(
            r"\b(speed up|slow down|faster|slower|tempo|\d+(?:\.\d+)?\s*x)\b", re.IGNORECASE
        ),
    ),
    (
        "trim_end",
        re.compile(
            r"\b(last|end|ending|tail|outro|final)\b.{0,40}\b(cut|trim|remove|drop|shorten)\b"
            r"|\b(cut|trim|remove|drop|shorten)\b.{0,40}\b(last|end|ending|tail|outro)\b",
            re.IGNORECASE,
        ),
    ),
]

_EXAMPLE_REQUESTS = [
    "remove the unnecessary waiting sections",
    "make the first 20s more exciting",
    "add stronger subtitles to important moments",
    "reduce background music during my commentary",
    "restore the part I deleted at 1:20",
    "fade between clips",
    "speed up by 1.25x",
    "cut the last 5 seconds",
]


def normalize_request(text: str) -> str:
    """Validate and sanitize a revision request before it is stored or echoed."""
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("revision request must not be empty")
    if len(text) > MAX_REQUEST_CHARS:
        raise ValidationError(f"revision request exceeds {MAX_REQUEST_CHARS} characters")
    clean = sanitize_untrusted(text).strip()
    if "\x00" in clean:
        raise ValidationError("revision request contains a NUL character")
    return clean


def _parse_seconds(text: str) -> float | None:
    m = _TIMECODE.search(text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = _SECONDS.search(text)
    if m:
        value = float(m.group(1))
        return value if 0 < value <= 86_400 else None
    return None


def _parse_db(text: str) -> float | None:
    m = _DB.search(text)
    if not m:
        return None
    value = float(m.group(1))
    return value if -60.0 <= value <= 20.0 else None


def _parse_multiplier(text: str) -> float | None:
    m = _MULTIPLIER.search(text)
    if not m:
        return None
    if m.group(0).lower() == "double":
        return 2.0
    if m.group(0).lower() == "half":
        return 0.5
    return float(m.group(1) or m.group(2))


def _match_intent(text: str) -> tuple[str | None, re.Match[str] | None]:
    for name, pattern in _INTENTS:
        m = pattern.search(text)
        if m:
            return name, m
    return None, None


def parse_request(request: str, edl: dict) -> dict:
    """Return a structured plan. Never mutates the EDL."""
    clean = normalize_request(request)
    name, _match = _match_intent(clean)
    plan = {
        "schema_version": 1,
        "request": clean,
        "intent": name or "unsupported",
        "ops": [],
        "summary": "",
        "needs_approval": True,  # every revision is reviewed before it is rendered
        "needs_clarification": False,
        "questions": [],
        "examples": _EXAMPLE_REQUESTS if name is None else [],
        "parsed_at": utc_now(),
    }

    if name is None:
        plan["needs_clarification"] = True
        plan["questions"].append(
            "I could not map that request to a supported edit operation. "
            "Choose one of the example requests or describe a specific change."
        )
        return plan

    tracks = edl.get("tracks") or {}
    video = tracks.get("video") or []

    if name == "restore":
        seconds = _parse_seconds(clean)
        removed = edl.get("removed_ranges") or []
        if seconds is None:
            if not removed:
                plan["needs_clarification"] = True
                plan["questions"].append("There are no deleted ranges to restore. Which timestamp?")
            else:
                plan["ops"] = [
                    {
                        "type": "restore_range",
                        "index": len(removed) - 1,
                        "reason": "restore_last_deleted",
                    }
                ]
                plan["summary"] = (
                    f"Restore the last removed range ({removed[-1]['in']:.1f}–{removed[-1]['out']:.1f}s)."
                )
        else:
            idx = next(
                (
                    i
                    for i, r in enumerate(removed)
                    if float(r["in"]) - 0.5 <= seconds <= float(r["out"]) + 0.5
                ),
                None,
            )
            if idx is None:
                plan["needs_clarification"] = True
                plan["questions"].append(
                    f"No removed range covers {seconds:.1f}s. Available: "
                    + (", ".join(f"{r['in']:.1f}–{r['out']:.1f}s" for r in removed) or "none")
                    + "."
                )
            else:
                plan["ops"] = [{"type": "restore_range", "index": idx, "reason": "creator_request"}]
                plan["summary"] = f"Restore the range at {seconds:.1f}s from `removed_ranges`."

    elif name == "remove_wait":
        seconds = _parse_seconds(clean)
        plan["ops"] = [
            {
                "type": "remove_ranges_by_signal",
                "limit": 5,
                "window_end": seconds,
                "reason": "creator_requested_wait_removal",
            }
        ]
        plan["summary"] = (
            "Remove up to five silent/dead-air spans from the analysis report"
            + (f" within the first {seconds:.1f}s" if seconds else "")
            + ". Restorable via `removed_ranges`."
        )

    elif name == "excite_start":
        seconds = _parse_seconds(clean) or 20.0
        if not video:
            plan["needs_clarification"] = True
            plan["questions"].append("This timeline has no video clips to emphasise.")
        else:
            plan["ops"] = [
                {
                    "type": "emphasise_window",
                    "start": 0.0,
                    "end": seconds,
                    "zoom_factor": 1.18,
                    "shorten_transitions_to": 0.3,
                },
            ]
            plan["summary"] = (
                f"Add a signal-backed punch-in and shorten transitions in the first "
                f"{seconds:.1f}s. This changes emphasis, not factual content."
            )

    elif name == "captions":
        markers = edl.get("markers") or []
        if not markers:
            plan["needs_clarification"] = True
            plan["questions"].append(
                "There are no signal-backed moment markers in this timeline to caption. "
                "Transcribe the source or mark a moment first."
            )
        else:
            stronger = bool(
                re.search(r"\b(strong\w*|emphas\w*|bold|big|clear)\b", clean, re.IGNORECASE)
            )
            plan["ops"] = [
                {
                    "type": "caption_markers",
                    "style": "emphasis" if stronger else "default",
                    "max_cues": 10,
                    "text_source": "marker_label",
                }
            ]
            plan["summary"] = (
                "Add caption cues at up to 10 signal-backed markers"
                + (" with emphasis styling" if stronger else "")
                + "."
            )

    elif name == "music":
        bed = tracks.get("music") or []
        if not bed:
            plan["needs_clarification"] = True
            plan["questions"].append(
                "This timeline has no music bed. Supply a licensed music asset before ducking it."
            )
        else:
            db = _parse_db(clean)
            lower = bool(re.search(r"\b(lower|reduce|less|quiet|duck)\b", clean, re.IGNORECASE))
            if lower:
                change = db if db is not None and db < 0 else -4.0
                value = max(-60.0, float(bed[0].get("gain_db", -18.0)) + change)
            else:
                value = db if db is not None else float(bed[0].get("gain_db", -18.0))
            plan["ops"] = [{"type": "set_music_gain", "gain_db": round(value, 2)}]
            plan["summary"] = f"Set the music bed gain to {value:.2f} dB for clearer commentary."

    elif name == "transition":
        if len(video) < 2:
            plan["needs_clarification"] = True
            plan["questions"].append("A transition needs at least two video clips.")
        else:
            seconds = _parse_seconds(clean) or 0.4
            duration = min(1.0, max(0.1, seconds))
            kind = "fade" if re.search(r"fade|dissolve", clean, re.IGNORECASE) else "cut"
            plan["ops"] = [{"type": "set_transitions", "kind": kind, "duration": duration}]
            plan["summary"] = f"Use {kind} transitions of {duration:.2f}s at every clip boundary."

    elif name == "speed":
        multiplier = _parse_multiplier(clean)
        if multiplier is None:
            plan["needs_clarification"] = True
            plan["questions"].append("Specify a speed such as `1.25x` or `half`.")
        else:
            speed = min(2.0, max(0.5, multiplier))
            plan["ops"] = [{"type": "set_speed", "speed": round(speed, 3)}]
            plan["summary"] = f"Change playback speed to {speed:.2f}x (audio tempo corrected)."

    elif name == "trim_end":
        seconds = _parse_seconds(clean)
        if seconds is None or not video:
            plan["needs_clarification"] = True
            plan["questions"].append(
                "Specify how many seconds to cut from the end, e.g. `cut the last 5 seconds`."
            )
        else:
            plan["ops"] = [{"type": "trim_end", "seconds": seconds}]
            plan["summary"] = f"Trim {seconds:.1f}s from the final clip (originals are untouched)."

    # Every returned plan is data. The caller decides whether to apply it.
    return plan


def _retime(edl: dict) -> None:
    """Recompute sequential timeline_in values after clip durations change."""
    cursor = 0.0
    for kind in ("video", "audio"):
        cursor = 0.0
        for clip in edl["tracks"].get(kind) or []:
            clip["timeline_in"] = round(cursor, 3)
            cursor += clip_duration(clip)


def _remap_after_removal(edl: dict, removed: list[tuple[float, float]]) -> None:
    """Shift markers/captions left by removed duration; drop anything inside a removed span."""

    def shifted(t: float) -> float | None:
        shift = 0.0
        for a, b in removed:
            if a <= t <= b:
                return None
            if b <= t:
                shift += b - a
        return round(max(0.0, t - shift), 3)

    for marker in edl.get("markers") or []:
        new = shifted(float(marker["at"]))
        if new is None:
            marker["_removed_by_revision"] = True
        else:
            marker["at"] = new
    edl["markers"] = [
        m for m in (edl.get("markers") or []) if not m.pop("_removed_by_revision", False)
    ]

    cues = []
    for cue in (edl.get("tracks") or {}).get("captions") or []:
        s, e = shifted(float(cue["start"])), shifted(float(cue["end"]))
        if s is not None and e is not None and e > s:
            cue["start"], cue["end"] = s, e
            cues.append(cue)
    edl["tracks"]["captions"] = cues


def _rebuild_from_removed(edl: dict, ranges: list[tuple[float, float]]) -> None:
    """Rebuild paired video/audio tracks from absolute source ranges."""
    source = edl["sources"][0]
    duration = float(source["duration"])
    cursor = 0.0
    kept = []
    for a, b in sorted(ranges):
        a, b = max(0.0, a), min(duration, b)
        if b <= a:
            continue
        if a - cursor > 0.2:
            kept.append((cursor, a))
        cursor = max(cursor, b)
    if duration - cursor > 0.2:
        kept.append((cursor, duration))
    if not kept:
        raise ValidationError("revision would remove the entire source; no edit produced")

    old_video = (edl.get("tracks") or {}).get("video") or []
    video, audio = [], []
    for i, (a, b) in enumerate(kept, start=1):
        effects = []
        for old in old_video:
            if float(old["source_in"]) <= a < float(old["source_out"]):
                effects = json.loads(json.dumps(old.get("effects") or []))
                break
        video.append(
            {
                "clip_id": f"v{i}",
                "source": source["id"],
                "source_in": round(a, 3),
                "source_out": round(b, 3),
                "timeline_in": 0.0,
                "speed": 1.0,
                "effects": effects,
            }
        )
        audio.append(
            {
                "clip_id": f"a{i}",
                "source": source["id"],
                "source_in": round(a, 3),
                "source_out": round(b, 3),
                "timeline_in": 0.0,
                "gain_db": 0.0,
            }
        )
    edl["tracks"]["video"] = video
    edl["tracks"]["audio"] = audio
    edl["transitions"] = [
        {"between": [f"v{i}", f"v{i + 1}"], "type": "cut"} for i in range(1, len(video))
    ]
    _retime(edl)
    _remap_after_removal(edl, ranges)


def _analysis_ranges(edl: dict) -> list[tuple[float, float]]:
    """Re-derive removable silence ranges from provenance recorded at build time.

    This is intentionally conservative: only ranges explicitly recorded as analysable silence
    are reused. `removed_ranges` are *already removed* spans and must not be cut twice, so a
    fresh `analysis_report` is still required when none were recorded.
    """
    ranges: list[tuple[float, float]] = []
    for decision in (edl.get("provenance") or {}).get("decisions") or []:
        for rng in decision.get("silence_ranges") or []:
            ranges.append((float(rng["in"]), float(rng["out"])))
    return _merge(ranges)


def _merge(ranges: list[tuple[float, float]], tolerance: float = 0.15) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + tolerance:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def apply_plan(parent: dict, plan: dict, *, analysis_report: dict | None = None) -> dict:
    """Apply a parsed plan to a derived EDL. Returns a new EDL; never mutates the parent."""
    if plan.get("needs_clarification") or not plan.get("ops"):
        raise ValidationError(
            "cannot apply a revision that needs clarification",
            hint="answer the question in the plan before applying",
        )
    child = derive(
        parent,
        decisions=[
            {
                "op": "revision",
                "intent": plan["intent"],
                "request": plan["request"],
                "ops": plan["ops"],
                "approved_by": None,
            }
        ],
    )
    for op in plan["ops"]:
        kind = op["type"]
        if kind == "remove_ranges_by_signal":
            # Prefer fresh analysis when supplied; otherwise use previously recorded ranges.
            segs = []
            if analysis_report:
                segs = ((analysis_report.get("analysis") or {}).get("silence") or {}).get(
                    "segments"
                ) or []
            ranges = [(float(s["start"]), float(s["end"])) for s in segs]
            if not ranges:
                ranges = _analysis_ranges(parent)
            window = op.get("window_end")
            if window is not None:
                ranges = [(a, b) for a, b in ranges if b <= float(window) + 0.1]
            ranges = _merge(ranges)[: int(op.get("limit", 5))]
            if not ranges:
                raise ValidationError(
                    "no silence/dead-air spans are available for this revision",
                    hint="run `creator media analyze` on the source first",
                )
            existing = [
                (float(r["in"]), float(r["out"])) for r in child.get("removed_ranges") or []
            ]
            all_ranges = _merge(existing + ranges)
            child["removed_ranges"] = [
                {
                    "source": "src1",
                    "in": round(a, 3),
                    "out": round(b, 3),
                    "reason": op.get("reason", "signal"),
                    "restorable": True,
                }
                for a, b in all_ranges
            ]
            _rebuild_from_removed(child, all_ranges)

        elif kind == "restore_range":
            removed = child.get("removed_ranges") or []
            idx = int(op["index"])
            if not 0 <= idx < len(removed):
                raise ValidationError("revision references a removed range that no longer exists")
            child["removed_ranges"] = [r for i, r in enumerate(removed) if i != idx]
            _rebuild_from_removed(
                child, [(float(r["in"]), float(r["out"])) for r in child["removed_ranges"]]
            )

        elif kind == "emphasise_window":
            start, end = float(op["start"]), float(op["end"])
            for clip in child["tracks"]["video"]:
                c0 = float(clip["timeline_in"])
                c1 = c0 + clip_duration(clip)
                if c1 <= start or c0 >= end:
                    continue
                local_start = max(0.0, start - c0)
                local_end = min(clip_duration(clip), end - c0)
                clip.setdefault("effects", []).append(
                    {
                        "type": "zoom",
                        "factor": float(op["zoom_factor"]),
                        "anchor": "center",
                        "start_at": round(local_start, 3),
                        "end_at": round(max(local_start + 0.4, local_end), 3),
                        "label_type": "hypothesis",
                        "note": "creator revision; review before publish",
                    }
                )
            target = float(op["shorten_transitions_to"])
            for trans in child.get("transitions") or []:
                if trans.get("type") != "cut":
                    trans["duration"] = min(float(trans.get("duration", 0.5)), target)

        elif kind == "caption_markers":
            existing = {
                (float(c["start"]), float(c["end"])) for c in child["tracks"].get("captions") or []
            }
            for i, marker in enumerate(child.get("markers") or []):
                at = float(marker["at"])
                s, e = max(0.0, at - 1.0), min(edl_duration(child), at + 1.5)
                if e - s < 0.2 or (s, e) in existing:
                    continue
                child["tracks"]["captions"].append(
                    {
                        "cue_id": f"cap_r{i}_{len(existing) + 1}",
                        "start": round(s, 3),
                        "end": round(e, 3),
                        "text": sanitize_untrusted(str(marker.get("label", "highlight"))),
                        "style": op.get("style", "default"),
                        "label_type": "estimated",
                    }
                )
                existing.add((s, e))
                if len(existing) >= int(op.get("max_cues", 10)):
                    break
            child["tracks"]["captions"].sort(key=lambda c: float(c["start"]))

        elif kind == "set_music_gain":
            bed = child["tracks"].get("music") or []
            if not bed:
                raise ValidationError("revision references a music bed that no longer exists")
            bed[0]["gain_db"] = float(op["gain_db"])

        elif kind == "set_transitions":
            existing = child.get("transitions") or []
            if not existing:
                existing = [{} for _ in range(max(0, len(child["tracks"]["video"]) - 1))]
            child["transitions"] = [
                {
                    "between": t.get("between") or [f"v{i}", f"v{i + 1}"],
                    "type": op["kind"],
                    "duration": float(op["duration"]),
                }
                for i, t in enumerate(existing, start=1)
            ]

        elif kind == "set_speed":
            speed = float(op["speed"])
            for clip in child["tracks"]["video"]:
                clip["speed"] = speed
            _retime(child)

        elif kind == "trim_end":
            video = child["tracks"]["video"]
            last = video[-1]
            old_in, old_out = float(last["source_in"]), float(last["source_out"])
            new_out = max(old_in + 0.5, old_out - float(op["seconds"]))
            if new_out >= old_out:
                raise ValidationError("the requested end trim is shorter than the final clip")
            last["source_out"] = round(new_out, 3)
            # Trim the matching audio clip's out point by the same amount.
            for audio in reversed(child["tracks"].get("audio") or []):
                if abs(float(audio["source_in"]) - old_in) < 0.01:
                    audio["source_out"] = round(new_out, 3)
                    break
            _retime(child)

        else:  # pragma: no cover - parse_request never emits unknown ops
            raise ValidationError(f"unsupported revision operation: {kind!r}")

    validate_edl(child)
    return child


def _project_revisions(ws: WorkspacePaths, project_id: str, subdir: str) -> Path:
    path = project_dir(ws, project_id) / "revisions" / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_pending(ws: WorkspacePaths, project_id: str, plan: dict) -> dict:
    """Persist a parsed revision request for the CLI/workspace loop."""
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    rev_id = f"rev_{stamp}_{len(list_pending(ws, project_id)) + 1:03d}"
    payload = {"id": rev_id, "status": "pending", "project_id": project_id, **plan}
    atomic_write_json(_project_revisions(ws, project_id, "pending") / f"{rev_id}.json", payload)
    return payload


def list_pending(ws: WorkspacePaths, project_id: str) -> list[dict]:
    out = []
    for path in sorted(_project_revisions(ws, project_id, "pending").glob("rev_*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def load_pending(ws: WorkspacePaths, project_id: str, rev_id: str) -> dict:
    if not re.fullmatch(r"rev_[0-9]{8}_[0-9]{6}_[0-9]{3}", rev_id):
        raise ValidationError("invalid revision id")
    path = _project_revisions(ws, project_id, "pending") / f"{rev_id}.json"
    if not path.is_file():
        raise ValidationError(f"pending revision {rev_id} not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"pending revision {rev_id} is unreadable: {exc}") from exc


def load_revision(ws: WorkspacePaths, project_id: str, rev_id: str) -> dict:
    """Load a revision from either the pending or applied store."""
    if not re.fullmatch(r"rev_[0-9]{8}_[0-9]{6}_[0-9]{3}", rev_id):
        raise ValidationError("invalid revision id")
    for subdir in ("applied", "pending"):
        path = _project_revisions(ws, project_id, subdir) / f"{rev_id}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValidationError(f"revision {rev_id} is unreadable: {exc}") from exc
    raise ValidationError(f"revision {rev_id} not found")


def consume_pending(ws: WorkspacePaths, project_id: str, rev_id: str) -> None:
    payload = load_pending(ws, project_id, rev_id)
    payload["status"] = "applied"
    atomic_write_json(_project_revisions(ws, project_id, "applied") / f"{rev_id}.json", payload)
    (_project_revisions(ws, project_id, "pending") / f"{rev_id}.json").unlink(missing_ok=True)
