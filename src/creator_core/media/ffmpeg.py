"""FFmpeg/FFprobe discovery and version probing (ADR-004; R-01).

Discovery order: explicit config/env override → PATH → well-known winget install locations
( handles the "just installed, PATH not refreshed" case) → common install dirs.
Binaries are executed via creator_core.security.subprocess (list args, timeouts).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from creator_core.security import subprocess as ssub

_VERSION_RE = re.compile(r"^(?P<tool>ffmpeg|ffprobe)\s+version\s+(?P<version>\S+)")


def _winget_package_dirs() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    base = Path(local) / "Microsoft" / "WinGet" / "Packages"
    if not base.is_dir():
        return []
    out: list[Path] = []
    for pkg in base.iterdir():
        if "ffmpeg" in pkg.name.lower():
            out.extend(d for d in pkg.glob("ffmpeg-*-full_build/bin") if d.is_dir())
    return out


def _common_dirs() -> list[Path]:
    cands = []
    for env in ("ProgramFiles", "PROGRAMFILES(X86)"):
        pf = os.environ.get(env)
        if pf:
            cands.append(Path(pf) / "ffmpeg" / "bin")
    cands.append(Path("C:/ffmpeg/bin"))
    return [c for c in cands if c.is_dir()]


def candidate_paths(tool: str) -> list[Path]:
    """Ordered candidate locations for `tool` ('ffmpeg' or 'ffprobe')."""
    if tool not in ("ffmpeg", "ffprobe"):
        raise ValueError(tool)
    env_key = f"CREATOR_{tool.upper()}"
    candidates: list[Path] = []
    env_override = os.environ.get(env_key)
    if env_override:
        candidates.append(Path(env_override))
    which = shutil.which(tool)
    if which:
        candidates.append(Path(which))
    candidates.extend(d / f"{tool}.exe" for d in _winget_package_dirs())
    candidates.extend(d / f"{tool}.exe" for d in _common_dirs())
    seen: set[Path] = set()
    ordered: list[Path] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def locate(tool: str) -> tuple[Path | None, str | None]:
    """Return (path, version) for the first candidate that executes successfully.

    An explicit CREATOR_<TOOL> override is authoritative: if it is set but unusable we
    report "missing" rather than silently falling back to another binary.
    """
    env_key = f"CREATOR_{tool.upper()}"
    override = os.environ.get(env_key)
    candidates = [Path(override)] if override else candidate_paths(tool)
    for cand in candidates:
        if not cand.is_file():
            continue
        try:
            result = ssub.run([str(cand), "-version"], timeout=15.0, max_output=200_000)
        except Exception:  # noqa: BLE001, S112 - intentional continue: non-fatal probe path
            continue
        if result.exit_code != 0:
            continue
        first = result.stdout.splitlines()[0] if result.stdout else ""
        match = _VERSION_RE.match(first)
        version = match.group("version") if match else (first.strip() or None)
        return cand, version
    return None, None
