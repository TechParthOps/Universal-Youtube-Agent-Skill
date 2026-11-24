"""Append-only audit log (JSONL) under <workspace>/logs/audit.jsonl.

Records commands, file mutations, approvals, publishing actions, and config changes with
secret redaction applied to every field. Rotation: audit.jsonl → audit.jsonl.1 (one kept).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from creator_core.security.secrets import redact_mapping

DEFAULT_MAX_BYTES = 5_000_000


class AuditLog:
    def __init__(self, workspace_root: Path, *, max_bytes: int = DEFAULT_MAX_BYTES):
        self._dir = Path(workspace_root) / "logs"
        self._path = self._dir / "audit.jsonl"
        self._max_bytes = max_bytes

    @property
    def path(self) -> Path:
        return self._path

    def log(self, event: str, **fields) -> dict:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event,
            **redact_mapping(fields),
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line)
        return record

    def _rotate_if_needed(self) -> None:
        try:
            if self._path.exists() and self._path.stat().st_size >= self._max_bytes:
                rotated = self._path.with_suffix(".jsonl.1")
                os.replace(self._path, rotated)
        except OSError:
            # Rotation is best-effort; appending must never fail because of it.
            pass

    def recent(self, limit: int = 50) -> list[dict]:
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
        out: list[dict] = []
        for line in reversed(lines):
            if len(out) >= limit:
                break
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
