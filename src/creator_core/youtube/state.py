"""Publishing state machine and the Shorts parent-publication gate (ADR-012).

States are persisted as plain JSON under `<workspace>/publishing/`. Transitions are explicit:
a failed transition raises instead of silently rewriting state. Shorts can never be published
before their parent video is public, and every mutation is audit-logged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.security.audit import AuditLog
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import resolve_within, sanitize_filename

STATES = (
    "draft",
    "approved",
    "uploading",
    "processing",
    "private",
    "public",
    "scheduled",
    "failed",
)
ALLOWED = {
    "draft": {"approved", "failed"},
    "approved": {"uploading", "failed"},
    "uploading": {"processing", "failed"},
    "processing": {"private", "scheduled", "failed"},
    "private": {"public", "scheduled", "failed"},
    "scheduled": {"public", "private", "failed"},
    "public": {"failed"},
    "failed": {"approved"},
}
PUBLISHED = {"public"}
VISIBLE = {"public", "scheduled"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class PublishState:
    def __init__(self, ws, *, audit: AuditLog | None = None):
        self.root = Path(ws.publishing) / "videos"
        self.root.mkdir(parents=True, exist_ok=True)
        self.audit = audit or AuditLog(ws.root)

    def _path(self, video_id: str) -> Path:
        clean = sanitize_filename(video_id)
        return resolve_within(self.root, f"{clean}.json")

    def save(self, record: dict) -> dict:
        video_id = str(record.get("video_id") or "")
        if not video_id or video_id != sanitize_filename(video_id):
            raise ValidationError(f"invalid video id for state file: {video_id!r}")
        record = dict(record)
        record["updated_at"] = _now()
        record.setdefault("created_at", record["updated_at"])
        atomic_write_json(self._path(video_id), record)
        return record

    def get(self, video_id: str) -> dict:
        path = self._path(video_id)
        if not path.is_file():
            raise ValidationError(f"no publishing state for video {video_id!r}")
        return json_load(path)

    def find(self, *, project_id: str | None = None, short_id: str | None = None) -> list[dict]:
        if not self.root.is_dir():
            return []
        out = []
        for path in sorted(self.root.glob("*.json")):
            try:
                record = json_load(path)
            except (OSError, TypeError, ValueError):
                continue
            if project_id and record.get("project_id") != project_id:
                continue
            if short_id and record.get("short_id") != short_id:
                continue
            out.append(record)
        return out

    def transition(self, video_id: str, new_state: str, *, detail: str = "") -> dict:
        if new_state not in STATES:
            raise ValidationError(f"unknown publishing state: {new_state}")
        record = self.get(video_id)
        current = record.get("state")
        if new_state not in ALLOWED.get(current, set()):
            raise ValidationError(
                f"invalid publishing transition {current} → {new_state} for {video_id}",
                hint="check the video's state with `creator youtube status`",
            )
        record["state"] = new_state
        record.setdefault("history", []).append(
            {
                "at": _now(),
                "from": current,
                "to": new_state,
                "detail": detail[:200],
            }
        )
        self.save(record)
        self.audit.log(
            "youtube.state_transition",
            video_id=video_id,
            from_state=current,
            to_state=new_state,
            detail=detail,
        )
        return record

    def require_published_parent(self, record: dict) -> dict:
        """Enforce ADR-012: a Short's parent must be public before any upload."""
        if not record.get("is_short"):
            return {"ok": True, "parent": None}
        parent_id = record.get("parent_video_id")
        if not parent_id:
            raise ValidationError(
                "a Short requires a parent_video_id before publishing",
                hint="pass --parent <video_id> and publish the parent first",
            )
        parent = self.get(parent_id)
        if parent.get("state") not in PUBLISHED:
            raise ValidationError(
                f"parent video {parent_id} is '{parent.get('state')}', not public",
                hint="publish the parent first; Shorts cannot go live before it",
            )
        return {"ok": True, "parent": {"video_id": parent_id, "state": parent["state"]}}


def json_load(path: Path) -> dict:
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} is not a JSON object")
    return data
