"""`.creator/` workspace layout and configuration (ADR-009).

All state is plain JSON/JSONL files — greppable, portable, and inspectable by agents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.security.paths import resolve_within, sanitize_filename
from creator_core.workspace.atomic import atomic_write_json

WORKSPACE_DIRNAME = ".creator"

CONFIG_SCHEMA_VERSION = 1

DEFAULT_CONFIG: dict = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "timezone": "UTC",
    "ffmpeg": {"path": None, "ffprobe_path": None},  # explicit overrides; None = auto-discover
    "limits": {
        "subprocess_timeout_s": 600,
        "max_input_gb": 8,
        "max_output_mb": 64,
        "render_threads": 2,
        "max_audit_bytes": 5_000_000,
    },
    "providers": {"text": None, "vision": None, "embeddings": None},  # configured later
    # Stage 12D: waveform-anchored narration alignment (bounded, real FFmpeg silencedetect).
    "voice": {"alignment": {"silence_db": -35.0, "min_silence_s": 0.1, "max_windows": 200}},
    "retention": {"keep_versions": 20, "purge_tmp_on_exit": True},
}


@dataclass(frozen=True)
class WorkspacePaths:
    base: Path  # the directory containing .creator/
    root: Path  # <base>/.creator
    config: Path
    profiles: Path
    projects: Path
    research: Path
    analytics: Path
    publishing: Path
    logs: Path
    tmp: Path


def workspace_path_for(base: Path | str) -> Path:
    return Path(base) / WORKSPACE_DIRNAME


def ensure_workspace(base: Path | str, *, create: bool = False) -> WorkspacePaths:
    """Return WorkspacePaths; with create=True, materialize missing directories + config."""
    base = Path(base)
    if base.exists() and not base.is_dir():
        raise ValidationError(f"workspace base is not a directory: {base}")
    root = base / WORKSPACE_DIRNAME
    if not create and not root.is_dir():
        from creator_core.cli.errors import WorkspaceIOError

        raise WorkspaceIOError(
            f"no workspace found at {root}",
            hint="run `creator init` to create one, or pass --workspace <dir>",
        )

    ws = WorkspacePaths(
        base=base,
        root=root,
        config=root / "config.json",
        profiles=root / "profiles",
        projects=root / "projects",
        research=root / "research",
        analytics=root / "analytics",
        publishing=root / "publishing",
        logs=root / "logs",
        tmp=root / "tmp",
    )
    if create:
        for d in (
            root,
            ws.profiles,
            ws.projects,
            ws.research,
            ws.analytics,
            ws.publishing,
            ws.logs,
            ws.tmp,
        ):
            d.mkdir(parents=True, exist_ok=True)
        if not ws.config.exists():
            atomic_write_json(ws.config, DEFAULT_CONFIG)
    return ws


def load_config(ws: WorkspacePaths) -> dict:
    if not ws.config.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        data = json.loads(ws.config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        from creator_core.cli.errors import WorkspaceIOError

        raise WorkspaceIOError(
            f"config.json is unreadable: {exc}", hint="fix or delete the file"
        ) from exc
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def save_config(ws: WorkspacePaths, config: dict) -> None:
    atomic_write_json(ws.config, config)


def project_dir(ws: WorkspacePaths, project_id: str, *, create: bool = False) -> Path:
    """Path for a project id, confined to the workspace projects dir."""
    clean = sanitize_filename(project_id)
    p = resolve_within(ws.projects, clean)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p
