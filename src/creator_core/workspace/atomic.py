"""Atomic file writes (tmp + os.replace) so a crash never leaves partial state (R-17)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from creator_core.cli.errors import WorkspaceIOError


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise WorkspaceIOError(f"could not write {path}: {exc}") from exc


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(Path(path), text.encode("utf-8"))


def atomic_write_json(path: Path, payload) -> None:
    atomic_write_text(Path(path), json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def atomic_create_json(path: Path, payload) -> bool:
    """Create `path` atomically only if absent. Returns False if it already exists.

    Used for immutable version files where a concurrent writer must never overwrite an
    existing version (R-17). `os.link` gives exclusive create without a replace race.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
            return True
        except FileExistsError:
            return False
    except OSError as exc:
        raise WorkspaceIOError(f"could not create {path}: {exc}") from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
