"""Media ingest: FFprobe inspection, hashing, size caps, and registered media roots.

Original files are read-only inputs. Only derived metadata is written, inside the
workspace project directory. Reading a file outside the workspace requires an explicitly
registered media root (`creator media root add <dir>`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import MissingDependencyError, UnsafePathError, ValidationError
from creator_core.media.ffmpeg import locate
from creator_core.media.injection import analyze_untrusted, sanitize_untrusted
from creator_core.security import subprocess as ssub
from creator_core.security.paths import long_path, resolve_within
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import WorkspacePaths, load_config, project_dir

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def ffmpeg_binary(config: dict | None = None, tool: str = "ffmpeg") -> str:
    """Resolve an FFmpeg binary from config overrides, then PATH/winget discovery."""
    if tool not in ("ffmpeg", "ffprobe"):
        raise ValueError(tool)
    config = config or {}
    override = (config.get("ffmpeg") or {}).get("path" if tool == "ffmpeg" else "ffprobe_path")
    if override:
        return str(override)
    path, _ = locate(tool)
    if path is None:
        raise MissingDependencyError(
            f"{tool} not found",
            hint="install FFmpeg (e.g. `winget install Gyan.FFmpeg`) or set ffmpeg.path in config",
        )
    return str(path)


def registered_roots(ws: WorkspacePaths, config: dict | None = None) -> list[Path]:
    config = config or load_config(ws)
    roots = [ws.base]
    for raw in config.get("media_roots") or []:
        root = Path(raw)
        # Relative roots resolve against the workspace, never the process CWD.
        roots.append(root if root.is_absolute() else ws.base / root)
    out: list[Path] = []
    for root in roots:
        try:
            resolved = Path(root).resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in out:
            out.append(resolved)
    return out


def validate_media_path(path: Path | str, ws: WorkspacePaths, config: dict | None = None) -> Path:
    candidate = Path(path)
    for root in registered_roots(ws, config):
        try:
            resolved = resolve_within(root, candidate)
        except UnsafePathError:
            continue
        if resolved.is_file():
            return resolved
    raise UnsafePathError(
        str(path),
        hint="file must be inside the workspace or a registered media root "
        "(run `creator media root add <dir>`)",
    )


def add_media_root(ws: WorkspacePaths, path: Path | str) -> dict:
    candidate = Path(path).resolve()
    if not candidate.is_dir():
        raise ValidationError(f"not a directory: {candidate}")
    config = load_config(ws)
    roots = list(config.get("media_roots") or [])
    if str(candidate) not in roots:
        roots.append(str(candidate))
        config["media_roots"] = roots
        from creator_core.workspace.layout import save_config

        save_config(ws, config)
    return {"path": str(candidate), "media_roots": roots}


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(long_path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def probe(path: Path, config: dict | None = None) -> dict:
    config = config or {}
    cmd = [
        ffmpeg_binary(config, "ffprobe"),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        long_path(path),
    ]
    result = ssub.run(
        cmd,
        timeout=float((config.get("limits") or {}).get("subprocess_timeout_s", 600)),
        max_output=8_000_000,
    )
    if result.exit_code != 0:
        raise ValidationError(f"ffprobe failed for {path.name}: {result.stderr[-400:]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"ffprobe returned invalid JSON for {path.name}: {exc}") from exc


def normalize_probe(raw: dict, path: Path, digest: str) -> dict:
    fmt = raw.get("format") or {}
    streams = raw.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    duration = _number(fmt.get("duration"))
    if duration is None and videos:
        duration = _number(videos[0].get("duration"))
    first_video = videos[0] if videos else {}
    rate = str(first_video.get("avg_frame_rate") or first_video.get("r_frame_rate") or "0/1")
    fps = (
        _number(rate.split("/")[0]) / _number(rate.split("/")[1])
        if "/" in rate and _number(rate.split("/")[1])
        else 0.0
    )
    raw_tags = dict(fmt.get("tags") or {})
    hostile_keys = [
        k for k in raw_tags if k.lower() in ("comment", "lyrics", "description", "title")
    ]
    # Metadata can contain instruction-shaped text. Store only sanitized values and record
    # which fields were flagged, so agent consumers never receive raw injected instructions.
    tags = {k: sanitize_untrusted(v) for k, v in raw_tags.items()}
    flagged = {
        k: analyze_untrusted(v)["patterns"]
        for k, v in raw_tags.items()
        if analyze_untrusted(v)["flagged"]
    }
    return {
        "schema_version": 1,
        "source_id": digest[:16],
        "path": str(path.resolve()),
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": int(_number(fmt.get("size")) or path.stat().st_size),
        "sha256": digest,
        "duration_seconds": round(duration or 0.0, 3),
        "format_name": fmt.get("format_name", ""),
        "bit_rate": int(_number(fmt.get("bit_rate")) or 0),
        "video": {
            "codec": first_video.get("codec_name"),
            "width": int(first_video.get("width") or 0),
            "height": int(first_video.get("height") or 0),
            "fps": round(fps or 0.0, 4),
            "pix_fmt": first_video.get("pix_fmt"),
        }
        if videos
        else None,
        "audio_streams": [
            {
                "index": s.get("index"),
                "codec": s.get("codec_name"),
                "channels": s.get("channels"),
                "language": (s.get("tags") or {}).get("language"),
            }
            for s in audios
        ],
        "metadata": tags,
        "metadata_review": {
            "untrusted_fields": hostile_keys,
            "flagged_fields": flagged,
            "note": "Metadata values are sanitized DATA. Instruction-shaped text is never executed.",
        },
        "inspected_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def inspect(path: Path | str, ws: WorkspacePaths, config: dict | None = None) -> dict:
    config = config or load_config(ws)
    resolved = validate_media_path(path, ws, config)
    max_bytes = float((config.get("limits") or {}).get("max_input_gb", 8)) * 1024**3
    size = resolved.stat().st_size
    if size > max_bytes:
        raise ValidationError(f"input exceeds configured max_input_gb limit: {size} bytes")
    if resolved.suffix.lower() not in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
        raise ValidationError(f"unsupported media extension: {resolved.suffix}")
    digest = sha256_file(resolved)
    return normalize_probe(probe(resolved, config), resolved, digest)


def register_source(ws: WorkspacePaths, info: dict, project_id: str | None = None) -> dict:
    """Persist source metadata in a project; dedupe by SHA-256. Never writes the original."""
    pid = project_id or f"prj_{info['filename'].split('.')[0]}"
    pdir = project_dir(ws, pid, create=True)
    sources_dir = pdir / "sources"
    sources_dir.mkdir(exist_ok=True)
    record = sources_dir / f"{info['source_id']}.json"
    if not record.exists():
        atomic_write_json(record, info)
    index_path = pdir / "media_sources.json"
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        entries = []
    if not any(e.get("sha256") == info["sha256"] for e in entries):
        entries.append(
            {
                "source_id": info["source_id"],
                "sha256": info["sha256"],
                "path": info["path"],
                "duration_seconds": info["duration_seconds"],
            }
        )
        atomic_write_json(index_path, entries)
    return {"project_id": pid, "project_dir": str(pdir), "source_record": str(record)}
