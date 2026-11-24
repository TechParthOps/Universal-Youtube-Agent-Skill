"""Segment-scoped preview, comparison and approval (Phase 11H).

The preview layer reads immutable generation versions and the additive EDL voice object. Approving
a segment writes a new immutable EDL version; it never mutates generated audio or the base render.
"""

from __future__ import annotations

import re

from creator_core.cli.errors import PathNotFoundError, ValidationError
from creator_core.editing.edl import derive, load_edl, save_edl
from creator_core.security.paths import resolve_within
from creator_core.voice.mix import find_profile_owner
from creator_core.voice.synthesis import generation_dir

_VERSION_RE = re.compile(r"^v(\d{3})\.wav$")


def _voice_of(edl: dict) -> dict:
    voice = edl.get("voice") if isinstance(edl, dict) else None
    if not isinstance(voice, dict) or not voice.get("segments"):
        raise ValidationError(
            "EDL has no voice segments to preview",
            hint="run `creator voice generate` and compose the EDL first",
        )
    return voice


def preview_data(ws, project_id: str, *, version: int | None = None) -> dict:
    """Return a compact, reviewable view of all narration segments in an EDL."""
    edl = load_edl(ws, project_id, version)
    voice = _voice_of(edl)
    owner = find_profile_owner(ws, str(voice["profile_id"]))
    root = generation_dir(ws, owner, str(voice["profile_id"]))
    segments = []
    for segment in voice["segments"]:
        rel = str(segment.get("audio") or "")
        path = resolve_within(root, rel) if rel else None
        segments.append(
            {
                "segment_id": segment["segment_id"],
                "text": segment.get("text"),
                "version": segment.get("version"),
                "status": segment.get("status"),
                "mocked": bool(segment.get("mocked")),
                "language": segment.get("language"),
                "timeline_start": segment.get("timeline_start"),
                "timeline_end": segment.get("timeline_end"),
                "audio": rel,
                "audio_exists": bool(path and path.is_file()),
                "audio_sha256": segment.get("audio_sha256"),
            }
        )
    return {
        "ok": True,
        "command": "voice preview",
        "project_id": project_id,
        "timeline_version": _latest_version(ws, project_id),
        "profile_id": voice.get("profile_id"),
        "segments": segments,
        "mix": voice.get("mix"),
        "disclosure": voice.get("disclosure"),
        "note": "Preview metadata only — no audio bytes are returned.",
    }


def _latest_version(ws, project_id: str) -> int | None:
    from creator_core.editing.edl import list_versions

    versions = list_versions(ws, project_id)
    return int(versions[-1]["version"]) if versions else None


def segment_preview(ws, project_id: str, segment_id: str) -> dict:
    """Return one segment plus every immutable version available for comparison."""
    data = preview_data(ws, project_id)
    segment = next((s for s in data["segments"] if s["segment_id"] == segment_id), None)
    if segment is None:
        raise PathNotFoundError(f"segment {segment_id} is not in project {project_id}")
    owner = find_profile_owner(ws, str(data["profile_id"]))
    versions_dir = resolve_within(
        generation_dir(ws, owner, str(data["profile_id"])) / "versions", segment_id
    )
    candidates = []
    if versions_dir.is_dir():
        for path in sorted(versions_dir.glob("v*.wav")):
            match = _VERSION_RE.match(path.name)
            if match:
                candidates.append(
                    {
                        "version": int(match.group(1)),
                        "path": str(path),
                        "is_current": int(match.group(1)) == segment["version"],
                    }
                )
    return {
        **data,
        "segment": segment,
        "candidates": candidates,
        "command": "voice preview segment",
    }


def approve_segment(ws, project_id: str, segment_id: str, *, version: int | None = None) -> dict:
    """Mark a segment approved in a new immutable EDL version."""
    edl = load_edl(ws, project_id)
    voice = _voice_of(edl)
    target = None
    for segment in voice["segments"]:
        if segment.get("segment_id") == segment_id:
            target = segment
            break
    if target is None:
        raise PathNotFoundError(f"segment {segment_id} is not in project {project_id}")
    if version is not None and int(target.get("version") or 0) != int(version):
        raise ValidationError(
            f"segment {segment_id} current version is {target.get('version')}, not {version}"
        )
    derived = derive(
        edl,
        decisions=[
            {
                "type": "voice.segment.approve",
                "segment_id": segment_id,
                "version": target.get("version"),
                "text": str(target.get("text") or "")[:200],
            }
        ],
    )
    derived["voice"]["segments"] = [
        {**segment, "status": "approved"} if segment.get("segment_id") == segment_id else segment
        for segment in derived["voice"]["segments"]
    ]
    saved = save_edl(ws, derived)
    return {
        "ok": True,
        "command": "voice preview approve",
        "project_id": project_id,
        "segment_id": segment_id,
        "version": target.get("version"),
        "timeline_version": saved["version"],
        "note": "Approval written to a new immutable timeline version; audio is untouched.",
    }
