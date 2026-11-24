"""Deterministic SRT/WebVTT caption writers built from EDL cues.

Cues are validated and sanitized before writing. Invalid cues are skipped with a counted reason
rather than repaired into fabricated timing. No language or direction is inferred here; those come
from `language.py` and are recorded as metadata in the output.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.editing.graph import sanitize_untrusted_plain

SUPPORTED_FORMATS = ("srt", "vtt")


def _stamp(seconds: float, separator: str) -> str:
    total_ms = round(max(0.0, float(seconds)) * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def cues_from_edl(edl: dict) -> list[dict]:
    """Extract valid, sanitized caption cues from an EDL, preserving timeline order."""
    out: list[dict] = []
    for cue in ((edl or {}).get("tracks") or {}).get("captions") or []:
        try:
            start, end = max(0.0, float(cue["start"])), float(cue["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        text = sanitize_untrusted_plain(str(cue.get("text", ""))).strip()
        if not text:
            continue
        item = {"start": round(start, 3), "end": round(end, 3), "text": text}
        if cue.get("cue_id"):
            item["cue_id"] = str(cue["cue_id"])[:80]
        out.append(item)
    return out


def validate_cues(cues: list[dict]) -> list[dict]:
    """Validate already-normalized cues (e.g. supplied by a translation provider)."""
    out = []
    for cue in cues:
        try:
            start, end = max(0.0, float(cue["start"])), float(cue["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = sanitize_untrusted_plain(str(cue.get("text", ""))).strip()
        if end > start and text:
            out.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return out


def write_srt(path: Path, cues: list[dict]) -> Path:
    blocks = []
    for i, cue in enumerate(cues, start=1):
        blocks.append(
            f"{i}\n{_stamp(cue['start'], ',')} --> {_stamp(cue['end'], ',')}\n{cue['text']}\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def write_vtt(path: Path, cues: list[dict], *, language: dict | None = None) -> Path:
    lines = ["WEBVTT"]
    if language and language.get("code"):
        lines.append(f"NOTE Language: {language['code']}")
    if language and language.get("rtl"):
        lines.append("NOTE Direction: rtl")
    lines.append("")
    for cue in cues:
        lines.append(f"{_stamp(cue['start'], '.')} --> {_stamp(cue['end'], '.')}")
        lines.append(cue["text"])
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_captions(
    directory: Path,
    cues: list[dict],
    *,
    format: str,
    language: dict | None = None,
    stem: str = "captions",
) -> Path:
    """Write a deterministic caption file; the digest prevents concurrent clobbering."""
    fmt = str(format).strip().lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValidationError(
            f"unsupported caption format: {format}",
            hint="available: " + ", ".join(SUPPORTED_FORMATS),
        )
    clean = validate_cues(cues)
    if not clean:
        raise ValidationError(
            "no valid caption cues to export",
            hint="generate an edit with transcript-based captions first",
        )
    payload = {"cues": clean, "format": fmt, "language": (language or {}).get("code")}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    path = Path(directory) / f"{stem}_{digest}.{fmt}"
    if fmt == "srt":
        write_srt(path, clean)
    else:
        write_vtt(path, clean, language=language)
    return path


def cue_summary(cues: list[dict], *, language: dict | None = None) -> dict:
    return {
        "cue_count": len(cues),
        "caption_seconds": round(sum(max(0.0, c["end"] - c["start"]) for c in cues), 3),
        "language": (language or {}).get("code"),
        "rtl": bool((language or {}).get("rtl")),
    }
