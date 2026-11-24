"""Burned, animated ASS captions for Shorts (no external subtitle dependency)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from creator_core.editing.graph import _ass_time, sanitize_untrusted_plain

MAX_CUE_CHARS = 84
_STYLE = (
    "Style: Short,Arial,58,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,"
    "-1,0,0,0,100,100,0,0,1,3,2,2,30,30,150,1"
)


def _escape(text: str) -> str:
    return (
        sanitize_untrusted_plain(text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    )


def write_short_captions(
    directory: Path, cues: list[dict], *, width: int = 1080, height: int = 1920
) -> Path | None:
    """Write a deterministic ASS file; returns None when there is nothing to burn."""
    clean: list[dict] = []
    for cue in cues:
        try:
            start, end = max(0.0, float(cue["start"])), float(cue["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        text = _escape(str(cue.get("text", "")))[:MAX_CUE_CHARS]
        if text.strip():
            clean.append({"start": start, "end": end, "text": text})
    if not clean:
        return None
    digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    path = directory / f"short_captions_{digest}.ass"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        _STYLE,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in clean:
        # Pop-in then settle, plus a soft fade — animation is in the ASS, not an API claim.
        effect = r"{\fad(120,120)\t(0,180,\fs68)\t(180,380,\fs58)}"
        lines.append(
            f"Dialogue: 0,{_ass_time(cue['start'])},{_ass_time(cue['end'])},"
            f"Short,,0,0,40,,{effect}{cue['text']}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def captions_from_transcript(segments: list[dict], *, offset: float, end: float) -> list[dict]:
    """Clip transcript segments into a Short window, preserving measured boundaries."""
    out: list[dict] = []
    for seg in segments:
        try:
            start, stop = float(seg["start"]) - offset, float(seg["end"]) - offset
        except (KeyError, TypeError, ValueError):
            continue
        start, stop = max(0.0, start), min(end, stop)
        text = str(seg.get("text", "")).strip()
        if stop > start and text:
            out.append({"start": round(start, 3), "end": round(stop, 3), "text": text})
    return out
