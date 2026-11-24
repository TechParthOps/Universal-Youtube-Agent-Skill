"""CreatorProfileManager — versioned profile + explicit memory updates (module #2).

The profile stores everything the engine needs to personalize research, editing, and publishing.
Changes made by automated modules (e.g. PerformanceLearner, editing memory) are *staged* in
`memory.pending_changes` and never applied without creator approval.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.security.approval import require_approval
from creator_core.security.audit import AuditLog
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import WorkspacePaths

PROFILE_FILE = "profile.json"
PROFILE_SCHEMA_VERSION = "1.0"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_profile(profile_id: str | None = None) -> dict:
    """Return a complete, empty-but-valid example profile (no fabricated data)."""
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": profile_id or f"creator_{uuid.uuid4().hex[:8]}",
        "created_at": _now(),
        "updated_at": _now(),
        "channel": {
            "name": "",
            "handle": "",
            "niche": "",
            "sub_niches": [],
            "category": "gaming",
            "channel_id": "",
            "subscribers": 0,
        },
        "audience": {"description": "", "age_range": "", "interests": [], "language": ""},
        "content": {"formats": ["long_form"], "categories": [], "series": []},
        "voice": {"personality": [], "speaking_style": "", "signature_phrases": []},
        "editing": {
            "pacing": "medium",
            "captions": "on",
            "transitions": "cut",
            "zoom": "subtle",
            "music": "low",
            "sound_effects": False,
            "avg_clip_seconds": 6.0,
            "preferred_aspect": "16:9",
        },
        "branding": {"colors": [], "font": "", "logo_path": None, "intro_text": ""},
        "history": {
            "baseline": {"views": 0, "avg_view_duration_s": 0, "avg_percentage_viewed": 0},
            "videos": [],
        },
        "publishing": {
            "timezone": "UTC",
            "preferred_days": [],
            "preferred_hours_utc": [],
        },
        "memory": {
            "approved_recommendations": [],
            "rejected_recommendations": [],
            "editing_preferences": {},
            "performance_notes": [],
            "pending_changes": [],
        },
        "provenance": [],
    }


def _merge_known(template: dict, changes: dict, path: str = "") -> None:
    """Recursively merge changes, rejecting keys absent from the profile template."""
    for key, value in changes.items():
        key_path = f"{path}.{key}" if path else key
        if key not in template:
            raise ValidationError(
                f"unknown profile field: {key_path}",
                hint="run `creator profile show --json` to see valid fields",
            )
        if isinstance(value, dict):
            if not isinstance(template[key], dict):
                raise ValidationError(f"field {key_path} is not an object")
            _merge_known(template[key], value, key_path)
        else:
            template[key] = value


class ProfileManager:
    def __init__(self, ws: WorkspacePaths, audit: AuditLog | None = None):
        self.ws = ws
        self.dir = Path(ws.profiles)
        self.path = self.dir / PROFILE_FILE
        self.versions = self.dir / "versions"
        self.audit = audit or AuditLog(ws.root)

    def exists(self) -> bool:
        return self.path.is_file()

    def create(self, data: dict | None = None, *, overwrite: bool = False) -> dict:
        if self.exists() and not overwrite:
            raise ValidationError(
                "a profile already exists", hint="use `profile show/set` or --force"
            )
        p = default_profile()
        if data:
            _merge_known(p, data)
        p["updated_at"] = _now()
        self._save(p)
        self.audit.log("profile.create", profile_id=p["profile_id"])
        return p

    def load(self, version: int | None = None) -> dict:
        path = self.path
        if version is not None:
            path = self.versions / f"profile.v{version:03d}.json"
            if not path.is_file():
                raise ValidationError(f"profile version v{version:03d} not found")
        if not path.is_file():
            raise ValidationError("no creator profile found", hint="run `creator profile init`")
        try:
            import json

            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"profile is unreadable: {exc}") from exc

    def set_fields(self, changes: dict) -> dict:
        """Direct, explicitly supplied field changes (e.g. from `profile set`)."""
        p = self.load()
        _merge_known(p, changes)
        p["updated_at"] = _now()
        p["provenance"].append({"at": _now(), "source": "creator.set", "changes": list(changes)})
        self._save(p)
        self.audit.log("profile.set", fields=sorted(changes))
        return p

    def stage_memory_change(self, kind: str, field: str, value, reason: str) -> dict:
        """Stage a module-generated change. It has NO effect until explicitly approved."""
        allowed = {
            "editing_preferences",
            "performance_notes",
            "approved_recommendations",
            "rejected_recommendations",
        }
        if kind not in allowed:
            raise ValidationError(
                f"unknown memory kind: {kind}", hint=f"allowed: {sorted(allowed)}"
            )
        p = self.load()
        change = {
            "id": f"chg_{uuid.uuid4().hex[:10]}",
            "kind": kind,
            "field": field,
            "value": value,
            "reason": reason,
            "status": "pending",
            "staged_at": _now(),
        }
        p["memory"]["pending_changes"].append(change)
        self._save(p)
        self.audit.log("profile.memory.stage", change_id=change["id"], kind=kind)
        return change

    def pending_changes(self) -> list[dict]:
        return self.load()["memory"]["pending_changes"]

    def apply_change(
        self, change_id: str, *, approval_token: str | None = None, generate_approval: bool = False
    ) -> dict:
        p = self.load()
        change = next((c for c in p["memory"]["pending_changes"] if c["id"] == change_id), None)
        if change is None:
            raise ValidationError(f"change not found: {change_id}")
        # S-01: bind the entire staged change (field, value, reason, kind) and consume the token.
        approval = require_approval(
            self.ws,
            "profile.memory.apply",
            change_id,
            approval_token,
            generate=generate_approval,
            payload=change,
            hint=f"approval required to apply change {change_id}; rerun with --approve",
        )
        memory = p["memory"]
        if change["kind"] in (
            "approved_recommendations",
            "rejected_recommendations",
            "performance_notes",
        ):
            memory[change["kind"]].append(
                {
                    "field": change["field"],
                    "value": change["value"],
                    "reason": change["reason"],
                    "at": _now(),
                }
            )
        else:
            memory[change["kind"]][change["field"]] = change["value"]
        change["status"] = "applied"
        change["applied_at"] = _now()
        # Keep history bounded: applied/rejected changes remain for audit, pending are removed.
        memory["pending_changes"] = [
            c for c in memory["pending_changes"] if c["status"] == "pending"
        ]
        p["provenance"].append(
            {"at": _now(), "source": "creator.approved_memory", "change_id": change_id}
        )
        p["updated_at"] = _now()
        self._save(p)
        self.audit.log(
            "profile.memory.applied", change_id=change_id, approval=approval["fingerprint"]
        )
        return change

    def _save(self, profile: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.versions.mkdir(parents=True, exist_ok=True)
        existing = [p.name for p in self.versions.glob("profile.v*.json")]
        version = len(existing) + 1
        snapshot = copy.deepcopy(profile)
        snapshot["_version"] = version
        atomic_write_json(self.versions / f"profile.v{version:03d}.json", snapshot)
        atomic_write_json(self.path, profile)
