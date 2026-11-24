"""Voice registration orchestration (Phase 11C).

Single entry point used by the CLI and the universal skill: validate a reference, store one owner-
scoped copy, create an immutable profile and record explicit purpose-scoped consent.
"""

from __future__ import annotations

from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.security.audit import AuditLog
from creator_core.voice.consent import grant_consent
from creator_core.voice.profile import create_profile, new_profile_id
from creator_core.voice.reference import analyze_reference, store_reference
from creator_core.workspace.layout import load_config


def register_voice(
    ws,
    *,
    owner_id: str,
    reference: Path | str,
    display_name: str,
    purposes: list[str],
    model_provider: str,
    model_name: str,
    model_version: str,
    supported_languages: list[str],
    consent_statement: str,
    pronunciation_preferences: dict | None = None,
    voice_characteristics: dict | None = None,
    require_suitable: bool = True,
    audit: AuditLog | None = None,
) -> dict:
    """Validate + store + profile + consent in one audited operation.

    `consent_statement` is mandatory: consent is never inferred from registration alone (S-04).
    """
    audit = audit or AuditLog(ws.root)
    assessment = analyze_reference(reference, ws, config=load_config(ws))
    if require_suitable and not assessment["suitable"]:
        raise ValidationError(
            "reference recording is not suitable for voice registration",
            hint="; ".join(assessment["reasons"]) + " | " + " ".join(assessment["guidance"][:3]),
        )
    profile_id = new_profile_id()
    meta = store_reference(ws, owner_id, profile_id, reference, assessment)
    profile = create_profile(
        ws,
        owner_id,
        {
            "voice_profile_id": profile_id,
            "display_name": display_name,
            "model_provider": model_provider,
            "model_name": model_name,
            "model_version": model_version,
            "reference_audio_metadata": meta,
            "supported_languages": supported_languages,
            "pronunciation_preferences": pronunciation_preferences or {},
            "voice_characteristics": voice_characteristics or {},
        },
    )
    consent = grant_consent(
        ws, owner_id, profile_id, purposes, statement=consent_statement, audit=audit
    )
    audit.log(
        "voice.register",
        owner_id=owner_id,
        profile_id=profile_id,
        provider=model_provider,
        quality_score=assessment["quality_score"],
        sha256=assessment["sha256"],
    )
    return {
        "ok": True,
        "command": "voice register",
        "voice_profile_id": profile_id,
        "owner_id": owner_id,
        "display_name": profile["display_name"],
        "model_provider": model_provider,
        "model_name": model_name,
        "assessment": assessment,
        "reference": meta,
        "consent": {
            "status": consent["status"],
            "purposes": consent["purposes"],
            "consent_timestamp": consent["consent_timestamp"],
            "statement": consent_statement,
        },
        "needs_approval": False,
        "note": (
            "Reference validated with real audio analysis. Neural voice cloning is not "
            "performed by this operation; generation requires a separate consent check."
        ),
    }
