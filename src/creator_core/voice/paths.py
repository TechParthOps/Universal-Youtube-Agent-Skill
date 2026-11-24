"""Workspace-scoped storage layout for VoiceDNA assets (Phase 11C).

Owner scoping is structural: every profile, consent record, reference file and generation history
entry lives under `<workspace>/.creator/voice/<owner>/...`. A caller cannot name another owner's
directory because owner ids are strictly validated and every path is re-confined with
`resolve_within`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.security.paths import resolve_within

_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
VOICE_DIRNAME = "voice"


@dataclass(frozen=True)
class VoicePaths:
    base: Path
    root: Path
    profiles: Path
    references: Path
    generation: Path
    exports: Path


def validate_owner_id(owner_id: str) -> str:
    """Return a safe owner id or reject it before it can touch the filesystem."""
    if not isinstance(owner_id, str) or not _OWNER_RE.fullmatch(owner_id):
        raise ValidationError(
            f"invalid owner id: {owner_id!r}",
            hint="use 2-64 letters, digits, '.', '_' or '-', starting with a letter or digit",
        )
    if owner_id in (".", "..") or ".." in owner_id:
        raise ValidationError(f"invalid owner id: {owner_id!r}")
    return owner_id


def voice_paths(ws) -> VoicePaths:
    """Derive VoiceDNA paths without modifying the existing WorkspacePaths contract."""
    base = Path(ws.base)
    root = Path(ws.root) / VOICE_DIRNAME
    return VoicePaths(
        base=base,
        root=root,
        profiles=root / "profiles",
        references=root / "references",
        generation=root / "generation",
        exports=root / "exports",
    )


def ensure_voice_dirs(ws) -> VoicePaths:
    paths = voice_paths(ws)
    for d in (paths.root, paths.profiles, paths.references, paths.generation, paths.exports):
        d.mkdir(parents=True, exist_ok=True)
    return paths


def owner_dir(paths: VoicePaths, owner_id: str, kind: str) -> Path:
    """Return a strictly confined per-owner directory for one asset kind."""
    validate_owner_id(owner_id)
    if kind not in ("profiles", "references", "generation", "exports"):
        raise ValidationError(f"unknown voice asset kind: {kind}")
    return resolve_within(getattr(paths, kind), validate_owner_id(owner_id))
