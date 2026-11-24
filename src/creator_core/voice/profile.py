"""Versioned, owner-scoped voice profiles (Phase 11C).

Each mutation writes a new immutable `voice_vNNN.json`; `voice_latest.json` is a mutable pointer.
Reference audio is never copied here — the profile stores only metadata and a confined path.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import PathNotFoundError, ValidationError
from creator_core.security.paths import resolve_within
from creator_core.voice.paths import ensure_voice_dirs, owner_dir, validate_owner_id
from creator_core.workspace.atomic import atomic_create_json, atomic_write_json

PROFILE_SCHEMA_VERSION = 1
_VERSION_RE = re.compile(r"^voice_v(\d{3})\.json$")

REQUIRED_FIELDS = (
    "voice_profile_id",
    "owner_id",
    "display_name",
    "model_provider",
    "model_name",
    "model_version",
    "reference_audio_metadata",
    "supported_languages",
    "pronunciation_preferences",
    "voice_characteristics",
    "consent_status",
    "consent_timestamp",
    "profile_version",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_profile_id() -> str:
    """Collision-resistant id: second-resolution timestamps alone collide in fast tests."""
    import uuid

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"vpr_{stamp}_{uuid.uuid4().hex[:8]}"


def _profile_dir(ws, owner_id: str, profile_id: str) -> Path:
    validate_owner_id(owner_id)
    if not isinstance(profile_id, str) or not re.fullmatch(r"vpr_[A-Za-z0-9_]+", profile_id):
        raise ValidationError(f"invalid voice profile id: {profile_id!r}")
    directory = owner_dir(ensure_voice_dirs(ws), owner_id, "profiles") / profile_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _versions(directory: Path) -> list[int]:
    if not directory.is_dir():
        return []
    return sorted(int(m.group(1)) for p in directory.iterdir() if (m := _VERSION_RE.match(p.name)))


def create_profile(ws, owner_id: str, data: dict) -> dict:
    """Create version 1 of a profile after validating all required fields."""
    validate_owner_id(owner_id)
    profile_id = data.get("voice_profile_id") or new_profile_id()
    directory = _profile_dir(ws, owner_id, profile_id)
    if _versions(directory):
        raise ValidationError(f"voice profile {profile_id} already exists for this owner")
    now = _now()
    profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "voice_profile_id": profile_id,
        "owner_id": owner_id,
        "display_name": str(data.get("display_name") or "My voice")[:100],
        "model_provider": str(data.get("model_provider") or "")[:80],
        "model_name": str(data.get("model_name") or "")[:80],
        "model_version": str(data.get("model_version") or "")[:80],
        "reference_audio_metadata": data.get("reference_audio_metadata") or {},
        "supported_languages": list(data.get("supported_languages") or []),
        "pronunciation_preferences": data.get("pronunciation_preferences") or {},
        "voice_characteristics": data.get("voice_characteristics") or {},
        "consent_status": "active",
        "consent_timestamp": data.get("consent_timestamp") or now,
        "profile_version": 1,
        "created_at": now,
        "updated_at": now,
        "history": [],
    }
    missing = [f for f in REQUIRED_FIELDS if f not in profile]
    if missing:
        raise ValidationError(f"incomplete voice profile: {', '.join(missing)}")
    if not atomic_create_json(directory / "voice_v001.json", profile):
        raise ValidationError(f"voice profile {profile_id} already exists")
    atomic_write_json(
        directory / "voice_latest.json", {"voice_profile_id": profile_id, "version": 1}
    )
    return profile


def load_profile(ws, owner_id: str, profile_id: str, *, version: int | None = None) -> dict:
    """Load a profile owned by `owner_id`. Never crosses the owner boundary."""
    directory = _profile_dir(ws, owner_id, profile_id)
    versions = _versions(directory)
    if not versions:
        raise PathNotFoundError(
            f"no voice profile {profile_id} for owner {owner_id}",
            hint="run `creator voice profiles --owner ID`",
        )
    chosen = version if version is not None else versions[-1]
    if chosen not in versions:
        raise PathNotFoundError(f"voice profile {profile_id} has no version {chosen}")
    path = directory / f"voice_v{chosen:03d}.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"voice profile version {chosen} is unreadable: {exc}") from exc
    if profile.get("owner_id") != owner_id:
        raise ValidationError("voice profile owner mismatch")
    return profile


def update_profile(ws, owner_id: str, profile_id: str, changes: dict) -> dict:
    """Append a new immutable version with `changes` applied to the allowlisted fields."""
    current = load_profile(ws, owner_id, profile_id)
    allowed = {
        "display_name",
        "model_provider",
        "model_name",
        "model_version",
        "supported_languages",
        "pronunciation_preferences",
        "voice_characteristics",
        "reference_audio_metadata",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValidationError(
            f"voice profile fields cannot be changed here: {', '.join(sorted(unknown))}"
        )
    if changes.get("model_provider") and changes["model_provider"] != current.get("model_provider"):
        raise ValidationError(
            "changing model provider requires registering a new voice profile",
            hint="provider identity is immutable for provenance/audit reasons",
        )
    next_version = int(current["profile_version"]) + 1
    updated = dict(current)
    updated.update(changes)
    updated["profile_version"] = next_version
    updated["updated_at"] = _now()
    updated["parent_version"] = current["profile_version"]
    directory = _profile_dir(ws, owner_id, profile_id)
    if not atomic_create_json(directory / f"voice_v{next_version:03d}.json", updated):
        raise ValidationError("could not allocate a new voice profile version")
    atomic_write_json(
        directory / "voice_latest.json", {"voice_profile_id": profile_id, "version": next_version}
    )
    return updated


def set_consent_status(ws, owner_id: str, profile_id: str, status: str) -> dict:
    """Mirror consent state onto the versioned profile so listings cannot go stale.

    Only the consent record is authoritative for synthesis; this field exists so `voice inspect`
    and `voice profiles` agree with it. It is intentionally not reachable through `update_profile`.
    """
    if status not in ("active", "revoked"):
        raise ValidationError(f"invalid consent status: {status!r}")
    current = load_profile(ws, owner_id, profile_id)
    if current.get("consent_status") == status:
        return current
    next_version = int(current["profile_version"]) + 1
    updated = dict(current)
    updated.update(
        {
            "consent_status": status,
            "profile_version": next_version,
            "updated_at": _now(),
            "parent_version": current["profile_version"],
        }
    )
    directory = _profile_dir(ws, owner_id, profile_id)
    if not atomic_create_json(directory / f"voice_v{next_version:03d}.json", updated):
        raise ValidationError("could not allocate a new voice profile version")
    atomic_write_json(
        directory / "voice_latest.json", {"voice_profile_id": profile_id, "version": next_version}
    )
    return updated


def list_profiles(ws, owner_id: str | None = None) -> list[dict]:
    """List profiles. With `owner_id=None` only summaries are returned (no cross-owner reads)."""
    paths = ensure_voice_dirs(ws)
    owners = (
        [validate_owner_id(owner_id)]
        if owner_id
        else sorted(p.name for p in paths.profiles.iterdir() if p.is_dir())
    )
    out = []
    for owner in owners:
        for p in sorted((paths.profiles / owner).iterdir()):
            if not p.is_dir() or not (p / "voice_latest.json").is_file():
                continue
            try:
                profile = load_profile(ws, owner, p.name)
            except (ValidationError, PathNotFoundError):
                continue
            item = {
                "voice_profile_id": profile["voice_profile_id"],
                "owner_id": profile["owner_id"] if owner_id else "<redacted>",
                "display_name": profile["display_name"],
                "model_provider": profile["model_provider"],
                "model_name": profile["model_name"],
                "profile_version": profile["profile_version"],
                "consent_status": profile.get("consent_status"),
                "reference_suitable": (profile.get("reference_audio_metadata") or {}).get(
                    "suitable"
                ),
            }
            out.append(item)
    return out


def reference_path(ws, owner_id: str, profile: dict) -> Path:
    """Resolve the stored reference path strictly inside the owner's reference directory."""
    stored = (profile.get("reference_audio_metadata") or {}).get("stored_path")
    if not stored:
        raise PathNotFoundError("profile has no stored reference audio")
    base = owner_dir(ensure_voice_dirs(ws), owner_id, "references")
    return resolve_within(base, stored)


def profile_sha256(profile: dict) -> str:
    """Stable fingerprint of the identity-defining fields (never includes raw audio)."""
    payload = {
        k: profile.get(k)
        for k in (
            "voice_profile_id",
            "owner_id",
            "model_provider",
            "model_name",
            "model_version",
            "profile_version",
            "reference_audio_metadata",
        )
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def delete_profile(ws, owner_id: str, profile_id: str, *, audit=None) -> dict:
    """Permanently delete a profile and all of its local reference/generation assets."""
    import shutil

    from creator_core.security.audit import AuditLog

    validate_owner_id(owner_id)
    profile = load_profile(ws, owner_id, profile_id)
    paths = ensure_voice_dirs(ws)
    targets = [
        owner_dir(paths, owner_id, "profiles") / profile_id,
        owner_dir(paths, owner_id, "references") / profile_id,
        owner_dir(paths, owner_id, "generation") / profile_id,
        owner_dir(paths, owner_id, "exports") / profile_id,
    ]
    consent_file = owner_dir(paths, owner_id, "profiles") / "consent" / f"{profile_id}.json"
    removed = []
    for target in [*targets, consent_file]:
        # Targets are built exclusively from confined owner_dir() bases plus a validated
        # profile id and fixed names, so they cannot point outside the owner's scope.
        # A symlink is unlinked rather than followed, preventing a directory-escape delete.
        if target.is_symlink():
            target.unlink()
            removed.append(str(target.relative_to(paths.base)))
        elif target.is_dir():
            shutil.rmtree(target)
            removed.append(str(target.relative_to(paths.base)))
        elif target.is_file():
            target.unlink()
            removed.append(str(target.relative_to(paths.base)))
    (audit or AuditLog(ws.root)).log(
        "voice.profile.delete",
        owner_id=owner_id,
        profile_id=profile_id,
        profile_sha256=profile_sha256(profile),
        removed=len(removed),
    )
    return {
        "ok": True,
        "voice_profile_id": profile_id,
        "removed": removed,
        "note": "Profile, consent record, reference audio and generation history deleted locally.",
    }
