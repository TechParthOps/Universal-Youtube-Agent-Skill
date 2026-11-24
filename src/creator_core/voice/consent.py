"""Explicit, purpose-scoped, revocable voice consent (Phase 11C).

A consent record is the only authority to synthesise with a registered profile. It is created by
an explicit creator action, audited, and checked on every generation. Revocation is immediate and
irreversible without a new explicit grant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import AuthRequiredError, ValidationError
from creator_core.security.audit import AuditLog
from creator_core.voice.paths import ensure_voice_dirs, owner_dir, validate_owner_id

PURPOSES = ("narration", "shorts", "dubbing", "localization")
CONSENT_STATEMENT = (
    "I confirm that I own this voice or hold explicit written authorisation to use it, and I "
    "authorise Creator Agent to generate synthetic narration for the purposes recorded below. "
    "I understand I may revoke this consent and delete the profile at any time."
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _path(ws, owner_id: str, profile_id: str) -> Path:
    validate_owner_id(owner_id)
    if (
        not isinstance(profile_id, str)
        or not profile_id.startswith("vpr_")
        or "/" in profile_id
        or "\\" in profile_id
    ):
        raise ValidationError(f"invalid voice profile id: {profile_id!r}")
    directory = owner_dir(ensure_voice_dirs(ws), owner_id, "profiles") / "consent"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{profile_id}.json"


def grant_consent(
    ws,
    owner_id: str,
    profile_id: str,
    purposes: list[str],
    *,
    statement: str = CONSENT_STATEMENT,
    audit: AuditLog | None = None,
) -> dict:
    """Record an explicit grant. Refuses empty or unknown purposes."""
    validate_owner_id(owner_id)
    if not purposes:
        raise ValidationError("at least one consent purpose is required")
    unknown = sorted(set(purposes) - set(PURPOSES))
    if unknown:
        raise ValidationError(
            f"unknown consent purpose(s): {', '.join(unknown)}",
            hint=f"supported: {', '.join(PURPOSES)}",
        )
    if CONSENT_STATEMENT.split(".")[0] not in statement:
        raise ValidationError("the exact voice ownership/authorisation statement must be confirmed")

    record = {
        "schema_version": 1,
        "voice_profile_id": profile_id,
        "owner_id": owner_id,
        "status": "active",
        "purposes": list(dict.fromkeys(purposes)),
        "statement": statement,
        "consent_timestamp": _now(),
        "revoked_at": None,
        "revocation_reason": None,
    }
    path = _path(ws, owner_id, profile_id)
    if path.exists():
        existing = load_consent(ws, owner_id, profile_id)
        if existing and existing.get("status") == "active":
            raise ValidationError("active consent already exists; revoke it before granting again")
    from creator_core.voice.profile import set_consent_status
    from creator_core.workspace.atomic import atomic_write_json

    atomic_write_json(path, record)
    # Mirror the grant onto the versioned profile; a missing profile fails closed here instead
    # of leaving an orphan consent record. The consent record remains authoritative.
    set_consent_status(ws, owner_id, profile_id, "active")
    (audit or AuditLog(ws.root)).log(
        "voice.consent.grant", owner_id=owner_id, profile_id=profile_id, purposes=record["purposes"]
    )
    return record


def load_consent(ws, owner_id: str, profile_id: str) -> dict | None:
    path = _path(ws, owner_id, profile_id)
    if not path.is_file():
        return None
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"consent record for {profile_id} is unreadable: {exc}") from exc
    return data


def verify_consent(ws, owner_id: str, profile_id: str, purpose: str = "narration") -> dict:
    """Raise AuthRequiredError unless active consent covers this exact owner and purpose."""
    validate_owner_id(owner_id)
    if purpose not in PURPOSES:
        raise ValidationError(f"unknown consent purpose: {purpose}")
    record = load_consent(ws, owner_id, profile_id)
    if record is None:
        raise AuthRequiredError(
            "no voice consent record exists for this profile",
            hint="register the voice with explicit ownership confirmation",
        )
    if record.get("status") != "active":
        raise AuthRequiredError(
            "voice consent has been revoked",
            hint="record a new explicit consent grant to reuse this voice",
        )
    if record.get("owner_id") != owner_id or record.get("voice_profile_id") != profile_id:
        raise AuthRequiredError("voice consent does not belong to this owner/profile")
    if purpose not in (record.get("purposes") or []):
        raise AuthRequiredError(
            f"voice consent does not permit the {purpose!r} purpose",
            hint=f"granted purposes: {', '.join(record['purposes'])}",
        )
    return record


def revoke_consent(
    ws,
    owner_id: str,
    profile_id: str,
    *,
    reason: str = "creator request",
    audit: AuditLog | None = None,
) -> dict:
    """Immediately revoke consent. Idempotent for an already-revoked record."""
    record = load_consent(ws, owner_id, profile_id)
    if record is None:
        raise ValidationError("no consent record to revoke")
    if record.get("status") != "revoked":
        record["status"] = "revoked"
        record["revoked_at"] = _now()
        record["revocation_reason"] = reason[:200]
        from creator_core.voice.profile import set_consent_status
        from creator_core.workspace.atomic import atomic_write_json

        atomic_write_json(_path(ws, owner_id, profile_id), record)
        set_consent_status(ws, owner_id, profile_id, "revoked")
        (audit or AuditLog(ws.root)).log(
            "voice.consent.revoke", owner_id=owner_id, profile_id=profile_id, reason=reason[:200]
        )
    return record
