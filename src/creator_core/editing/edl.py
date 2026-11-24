"""Timeline EDL schema v1: immutable, versioned JSON, source of truth for renders.

Files `timeline_vNNN.json` are never modified after creation. `timeline_latest.json` is a
mutable pointer. Every render is reproducible from an EDL plus the recorded source hashes.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError, WorkspaceIOError
from creator_core.security.paths import sanitize_filename
from creator_core.workspace.atomic import atomic_create_json, atomic_write_json
from creator_core.workspace.layout import WorkspacePaths, project_dir

SCHEMA_VERSION = "1.0"
_VERSION_RE = re.compile(r"^timeline_v(\d{3})\.json$")

# EDLs are user-editable JSON that becomes FFmpeg arguments/filter strings. Allowlists keep
# that boundary safe: a malicious timeline can never smuggle a new filter or CLI option.
ALLOWED_VIDEO_CODECS = {"libx264", "mpeg4"}
ALLOWED_AUDIO_CODECS = {"aac", "libmp3lame"}
ALLOWED_PRESETS = {"ultrafast", "superfast", "veryfast", "fast", "medium", "slow"}
ALLOWED_PIXEL_FORMATS = {"yuv420p", "yuv444p"}
ALLOWED_CONTAINERS = {"mp4", "mkv", "webm"}
ALLOWED_TRANSITIONS = {
    "cut",
    "fade",
    "wipeleft",
    "wiperight",
    "slideleft",
    "slideright",
    "circleopen",
    "circleclose",
    "dissolve",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_project_id(concept: str = "") -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    token = re.sub(r"[^a-z0-9]+", "_", concept.lower()).strip("_")[:24] or "edit"
    return f"prj_{stamp}_{token}"


def clip_duration(clip: dict) -> float:
    speed = float(clip.get("speed", 1.0) or 1.0)
    if speed <= 0:
        raise ValidationError("clip speed must be greater than zero")
    return (float(clip["source_out"]) - float(clip["source_in"])) / speed


def clip_timeline_out(clip: dict) -> float:
    return float(clip["timeline_in"]) + clip_duration(clip)


def edl_duration(edl: dict) -> float:
    clips = edl.get("tracks", {}).get("video") or []
    return max((clip_timeline_out(c) for c in clips), default=0.0)


def default_render(source: dict | None = None) -> dict:
    source = source or {}
    return {
        "container": "mp4",
        "video_codec": "libx264",
        "crf": 20,
        "preset": "veryfast",
        "fps": float(source.get("fps") or 30) or 30.0,
        "width": int(source.get("width") or 1920),
        "height": int(source.get("height") or 1080),
        "audio_codec": "aac",
        "audio_bitrate": "192k",
        "pixel_format": "yuv420p",
    }


def empty_edl(
    project_id: str, source: dict, *, strategy: str = "manual", concept: str = ""
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": sanitize_filename(project_id),
        "parent_version": None,
        "created_at": utc_now(),
        "concept": concept,
        "sources": [
            {
                "id": "src1",
                "path": source["path"],
                "sha256": source["sha256"],
                "duration": source["duration_seconds"],
                "fps": (source.get("video") or {}).get("fps") or 0.0,
                "width": (source.get("video") or {}).get("width") or 0,
                "height": (source.get("video") or {}).get("height") or 0,
                "audio_streams": source.get("audio_streams") or [],
            }
        ],
        "tracks": {"video": [], "audio": [], "music": [], "captions": []},
        "transitions": [],
        "markers": [],
        "removed_ranges": [],
        "render": default_render(source.get("video")),
        "provenance": {"strategy": strategy, "decisions": []},
    }


def _require_num(value, name: str, *, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be a number") from exc
    if number < minimum:
        raise ValidationError(f"{name} must be >= {minimum}")
    return number


def validate_edl(data: dict) -> dict:
    """Validate structural + timing invariants. Returns the same dict on success."""
    if not isinstance(data, dict):
        raise ValidationError("EDL must be a JSON object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError(f"unsupported EDL schema_version: {data.get('schema_version')}")
    if not data.get("project_id"):
        raise ValidationError("EDL project_id is required")
    sources = data.get("sources") or []
    if not sources:
        raise ValidationError("EDL must contain at least one source")
    src_ids = [s.get("id") for s in sources]
    if len(src_ids) != len(set(src_ids)):
        raise ValidationError("duplicate source id in EDL")
    for s in sources:
        if not s.get("path") or not s.get("sha256"):
            raise ValidationError(f"source {s.get('id')} needs path and sha256")
        _require_num(s.get("duration", 0), f"source {s.get('id')} duration")

    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        raise ValidationError("EDL tracks must be an object")
    video = tracks.get("video") or []
    if not video:
        raise ValidationError("EDL video track has no clips")
    src_by_id = {s["id"]: s for s in sources}

    def check_clips(kind: str) -> None:
        clips = tracks.get(kind) or []
        last_end = 0.0
        for i, clip in enumerate(clips):
            src = src_by_id.get(clip.get("source"))
            if src is None:
                raise ValidationError(f"{kind} clip {i} references unknown source")
            sin = _require_num(clip.get("source_in"), f"{kind} clip {i} source_in")
            sout = _require_num(clip.get("source_out"), f"{kind} clip {i} source_out")
            if sout <= sin:
                raise ValidationError(f"{kind} clip {i} source_out must exceed source_in")
            if sout > float(src["duration"]) + 0.001:
                raise ValidationError(f"{kind} clip {i} exceeds source duration")
            tin = _require_num(clip.get("timeline_in"), f"{kind} clip {i} timeline_in")
            if i and tin + 0.001 < last_end:
                raise ValidationError(f"{kind} clip {i} overlaps the previous clip")
            last_end = clip_timeline_out(clip)

    check_clips("video")
    check_clips("audio")

    for cue in tracks.get("captions") or []:
        start = _require_num(cue.get("start"), "caption start")
        end = _require_num(cue.get("end"), "caption end")
        if end <= start:
            raise ValidationError("caption end must exceed start")
        if not str(cue.get("text", "")).strip():
            raise ValidationError("caption text must not be empty")

    transitions = data.get("transitions") or []
    for trans in transitions:
        kind = trans.get("type", "cut")
        if kind not in ALLOWED_TRANSITIONS:
            raise ValidationError(f"unsupported transition type: {kind!r}")

    render = data.get("render") or {}
    _require_num(render.get("fps", 0), "render fps", minimum=0.001)
    _require_num(render.get("width", 0), "render width", minimum=1)
    _require_num(render.get("height", 0), "render height", minimum=1)
    choices = (
        ("container", ALLOWED_CONTAINERS),
        ("video_codec", ALLOWED_VIDEO_CODECS),
        ("audio_codec", ALLOWED_AUDIO_CODECS),
        ("preset", ALLOWED_PRESETS),
        ("pixel_format", ALLOWED_PIXEL_FORMATS),
    )
    for field, allowed in choices:
        value = render.get(field)
        if value is not None and value not in allowed:
            raise ValidationError(f"unsupported render {field}: {value!r}")
    crf = int(_require_num(render.get("crf", 20), "render crf"))
    if not 0 <= crf <= 51:
        raise ValidationError("render crf must be between 0 and 51")
    if float(render["fps"]) > 240 or int(render["width"]) > 7680 or int(render["height"]) > 7680:
        raise ValidationError("render dimensions/fps exceed supported limits")
    return data


def _versions(pdir: Path) -> list[int]:
    if not pdir.is_dir():
        return []
    out = []
    for p in pdir.iterdir():
        m = _VERSION_RE.match(p.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def list_versions(ws: WorkspacePaths, project_id: str) -> list[dict]:
    pdir = project_dir(ws, project_id)
    out = []
    for n in _versions(pdir):
        path = pdir / f"timeline_v{n:03d}.json"
        try:
            edl = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "version": n,
                "path": str(path),
                "duration": round(edl_duration(edl), 3),
                "clips": len((edl.get("tracks") or {}).get("video") or []),
                "parent_version": edl.get("parent_version"),
                "strategy": (edl.get("provenance") or {}).get("strategy"),
                "created_at": edl.get("created_at"),
            }
        )
    return out


def save_edl(ws: WorkspacePaths, edl: dict) -> dict:
    """Persist an immutable timeline version. Returns version metadata."""
    existing = _versions(project_dir(ws, edl["project_id"]))
    # `derive()` cannot know the next number; resolve its symbolic marker here.
    if edl.get("parent_version") == "latest":
        edl["parent_version"] = max(existing) if existing else None
    validate_edl(edl)
    pdir = project_dir(ws, edl["project_id"], create=True)
    next_n = (max(existing) + 1) if existing else 1
    for candidate in range(next_n, next_n + 100):
        path = pdir / f"timeline_v{candidate:03d}.json"
        if atomic_create_json(path, edl):
            next_n = candidate
            break
    else:
        raise WorkspaceIOError("could not allocate an immutable timeline version")
    atomic_write_json(
        pdir / "timeline_latest.json",
        {"version": next_n, "path": path.name, "project_id": edl["project_id"]},
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "version": next_n,
        "path": str(path),
        "sha256": digest,
        "duration": round(edl_duration(edl), 3),
    }


def load_edl(ws: WorkspacePaths, project_id: str, version: int | None = None) -> dict:
    pdir = project_dir(ws, project_id)
    versions = _versions(pdir)
    if not versions:
        raise ValidationError(
            f"no timeline found for project {project_id}",
            hint="run `creator edit new --source FILE --strategy gaming` first",
        )
    if version is None:
        version = max(versions)
    if version not in versions:
        raise ValidationError(
            f"timeline version {version} not found for project {project_id}",
            hint=f"available versions: {versions}",
        )
    path = pdir / f"timeline_v{version:03d}.json"
    try:
        edl = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceIOError(f"timeline version {version} is unreadable: {exc}") from exc
    validate_edl(edl)
    return edl


def derive(parent: dict, *, decisions: list[dict] | None = None) -> dict:
    """Create a child EDL object. Caller mutates tracks, then saves it as a new version."""
    child = json.loads(json.dumps(parent))
    child["parent_version"] = "latest"  # resolved to the concrete number by save_edl()
    child["created_at"] = utc_now()
    child["provenance"].setdefault("decisions", [])
    for d in decisions or []:
        child["provenance"]["decisions"].append({**d, "at": utc_now()})
    return child


def summary(edl: dict) -> dict:
    tracks = edl.get("tracks") or {}
    return {
        "project_id": edl["project_id"],
        "schema_version": edl["schema_version"],
        "duration_seconds": round(edl_duration(edl), 3),
        "video_clips": len(tracks.get("video") or []),
        "audio_clips": len(tracks.get("audio") or []),
        "captions": len(tracks.get("captions") or []),
        "music": len(tracks.get("music") or []),
        "removed_ranges": len(edl.get("removed_ranges") or []),
        "strategy": (edl.get("provenance") or {}).get("strategy"),
        "decisions": len((edl.get("provenance") or {}).get("decisions") or []),
    }
