"""Path safety: workspace confinement, hostile-name sanitization, Windows long paths.

Every filesystem write in creator_core must go through this module. Rules:
- Resolved paths must stay inside an explicitly-approved base (workspace or registered
  media root). `..`, symlink escapes, cross-drive tricks, UNC traps → UnsafePathError.
- Filenames produced for new artifacts must pass sanitize_filename (reserved Windows
  names, control characters, trailing dots/spaces, length caps).
- Original media is never written to (see editing/; ingest only reads + hashes).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from creator_core.cli.errors import PathNotFoundError, UnsafePathError

# Directories (or their descendants) Windows reserves as device names regardless of extension.
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_FILENAME_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

MAX_FILENAME_LEN = 180


def _is_within(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([str(child), str(parent)]) == str(parent)
    except ValueError:  # different drives (Windows)
        return False


def resolve_within(base: Path, candidate: Path | str) -> Path:
    """Resolve `candidate` and require it to stay inside `base`.

    Absolute candidates are allowed only if they are already inside base. Relative
    candidates are joined to base. Symlinks/junctions are resolved; an escape through
    them is rejected. The path need not exist.
    """
    base_p = Path(base)
    if not base_p.is_absolute():
        base_p = Path.cwd() / base_p
    try:
        base_p = base_p.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - defensive
        raise UnsafePathError(str(base), hint=f"base path could not be resolved: {exc}") from exc
    if not base_p.is_dir():
        raise UnsafePathError(str(base), hint="base must be an existing directory")

    cand = Path(candidate)
    if not cand.is_absolute():
        cand = base_p / cand
    try:
        resolved = cand.resolve(strict=False)
    except OSError as exc:
        raise UnsafePathError(str(candidate), hint=f"path could not be resolved: {exc}") from exc

    if not _is_within(resolved, base_p):
        raise UnsafePathError(
            str(candidate), hint="path resolves outside the approved base directory"
        )
    return resolved


def ensure_within(base: Path, candidate: Path | str, *, must_exist: bool = False) -> Path:
    """resolve_within + optional existence requirement."""
    resolved = resolve_within(base, candidate)
    if must_exist and not resolved.exists():
        raise PathNotFoundError(str(candidate), hint=f"expected an existing path inside {base}")
    return resolved


def sanitize_filename(name: str, *, max_len: int = MAX_FILENAME_LEN) -> str:
    """Return a filesystem-safe filename for new artifacts. Raises on hostile input."""
    if not name or not name.strip():
        raise UnsafePathError(name if isinstance(name, str) else repr(name), hint="empty filename")
    if "\x00" in name:
        raise UnsafePathError(name, hint="filename contains a NUL byte")
    if any(ord(ch) < 32 for ch in name):
        raise UnsafePathError(name, hint="filename contains control characters")

    # Reserved device names match the part before the first dot (CON.txt is reserved too).
    stem = name.split(".", 1)[0].strip().upper()
    if stem in _WINDOWS_RESERVED:
        raise UnsafePathError(name, hint=f"'{stem}' is a reserved Windows device name")

    cleaned = _FILENAME_INVALID.sub("_", name).strip().rstrip(". ")
    if not cleaned:
        raise UnsafePathError(name, hint="filename contains no usable characters")

    if len(cleaned) > max_len:
        suffix = Path(cleaned).suffix
        stem_part = cleaned[: max_len - len(suffix)]
        cleaned = stem_part + suffix
    return cleaned


def long_path(path: Path | str) -> str:
    """Apply the Windows extended-length prefix when a path is at risk of MAX_PATH limits."""
    sp = str(path)
    if os.name == "nt" and not sp.startswith("\\\\?\\"):
        needs = len(sp) >= 240 or any(len(part) > 200 for part in sp.split(os.sep))
        if needs:
            if sp.startswith("\\\\"):  # UNC path
                return "\\\\?\\UNC\\" + sp[2:]
            return "\\\\?\\" + sp
    return sp
