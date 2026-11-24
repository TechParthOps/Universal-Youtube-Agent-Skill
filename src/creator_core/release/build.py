"""Local sdist/wheel build + SHA-256 manifest (Phase 10). No upload or publish, ever.

The build is a real `python -m build` invocation through a list-argument subprocess (never a
shell). Artifacts stay inside the requested output directory, which is confined to the checkout.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from creator_core.cli.errors import MissingDependencyError, ValidationError, WorkspaceIOError
from creator_core.security.paths import resolve_within
from creator_core.workspace.atomic import atomic_write_text

Runner = Callable[[list[str], Path, int], subprocess.CompletedProcess]


def _default_runner(cmd: list[str], cwd: Path, timeout_s: int) -> subprocess.CompletedProcess:
    """Check for the optional build backend, then run it without a shell."""
    try:
        available = importlib.util.find_spec("build") is not None
    except (ImportError, ValueError):
        available = False
    if not available:
        raise MissingDependencyError(
            "the 'build' package is required to create sdist/wheel artifacts",
            hint="pip install 'universal-creator-agent[build]' (nothing is uploaded)",
        )
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s, check=False
    )


def _hash_line(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{digest}  {path.name}"


def build_artifacts(
    root: Path, out: str | Path = "dist", *, timeout_s: int = 300, runner: Runner | None = None
) -> dict:
    """Build sdist+wheel into `<root>/<out>` and write a SHA-256 manifest beside them."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValidationError(f"release root is not a directory: {root}")
    out_dir = resolve_within(root, out)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Remove artifacts from an earlier build so the hashes always describe THIS run (M5).
    for stale in (*out_dir.glob("*.whl"), *out_dir.glob("*.tar.gz"), out_dir / "SHA256SUMS.txt"):
        stale.unlink(missing_ok=True)
    run = runner or _default_runner
    cmd = [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(out_dir), str(root)]
    try:
        proc = run(cmd, root, timeout_s)
    except MissingDependencyError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspaceIOError(f"build process could not run: {exc}") from exc
    if proc.returncode != 0:
        raise WorkspaceIOError(
            f"build failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[-400:]}"
        )

    wheels = sorted(out_dir.glob("*.whl"))
    sdists = sorted(out_dir.glob("*.tar.gz"))
    if not wheels or not sdists:
        raise WorkspaceIOError(
            "build reported success but did not produce both a wheel and an sdist",
            hint="check the [build-system] section of pyproject.toml",
        )
    artifacts = wheels + sdists
    lines = [_hash_line(p) for p in artifacts]
    manifest = out_dir / "SHA256SUMS.txt"
    atomic_write_text(manifest, "\n".join(lines) + "\n")
    return {
        "command": "release build",
        "ok": True,
        "out": str(out_dir),
        "artifacts": [
            {
                "name": p.name,
                "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in artifacts
        ],
        "manifest": str(manifest),
        "note": "Local artifacts only. Nothing was uploaded or published.",
    }
