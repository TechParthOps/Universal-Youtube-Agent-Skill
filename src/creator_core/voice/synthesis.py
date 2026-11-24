"""Consent-checked segment synthesis and immutable generation history (Phase 11E).

The storage helpers and both generation entry points live here. Consent is verified **inside**
this module on every call — never only in the CLI — and every provider failure is recorded in the
append-only history with redacted detail. Generated audio is stored once per segment version under
`generation/<profile_id>/versions/<segment_id>/vNNN.wav`; a mutable `current.json` pointer selects
the version used by the timeline.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import PathNotFoundError, ProviderError, ValidationError
from creator_core.media.ingest import sha256_file
from creator_core.security.paths import resolve_within
from creator_core.voice.consent import verify_consent
from creator_core.voice.paths import ensure_voice_dirs, owner_dir
from creator_core.voice.profile import load_profile, profile_sha256
from creator_core.voice.providers import select
from creator_core.voice.script import canonical_sha256
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import load_config

_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---- storage helpers ------------------------------------------------------------------------


def generation_dir(ws, owner_id: str, profile_id: str) -> Path:
    """Owner-scoped generation directory (created on first read so history can be empty)."""
    base = owner_dir(ensure_voice_dirs(ws), owner_id, "generation")
    base.mkdir(parents=True, exist_ok=True)
    directory = resolve_within(base, profile_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def history_path(ws, owner_id: str, profile_id: str) -> Path:
    return resolve_within(generation_dir(ws, owner_id, profile_id), "history.json")


def current_path(ws, owner_id: str, profile_id: str) -> Path:
    return resolve_within(generation_dir(ws, owner_id, profile_id), "current.json")


def history(ws, owner_id: str, profile_id: str) -> list[dict]:
    """Read the immutable generation history (empty when nothing has been generated)."""
    path = history_path(ws, owner_id, profile_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"voice generation history is unreadable: {exc}") from exc
    entries = data.get("entries") if isinstance(data, dict) else data
    return list(entries or [])


def _append_history(ws, owner_id: str, profile_id: str, entry: dict) -> None:
    entries = history(ws, owner_id, profile_id)
    entries.append(entry)
    atomic_write_json(
        history_path(ws, owner_id, profile_id),
        {
            "schema_version": _SCHEMA_VERSION,
            "voice_profile_id": profile_id,
            "entries": entries,
        },
    )


def current_state(ws, owner_id: str, profile_id: str) -> dict | None:
    path = current_path(ws, owner_id, profile_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"voice generation state is unreadable: {exc}") from exc


def _voice_info(ws, profile: dict, config: dict | None = None) -> dict:
    info = dict(profile)
    info["workspace"] = ws
    info["config"] = config or load_config(ws)
    return info


def _store_audio(result, base: Path, segment_id: str, version: int) -> tuple[str, str]:
    versions = resolve_within(base, "versions")
    target_dir = resolve_within(versions, segment_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = resolve_within(target_dir, f"v{version:03d}.wav")
    if target.exists():
        raise ValidationError(f"audio version {version} for {segment_id} already exists")
    source = Path(result.audio_path)
    if not source.is_file():
        raise ProviderError(f"provider did not create audio at {source}")
    try:
        source.replace(target)
    except OSError:
        shutil.copy2(source, target)
        source.unlink(missing_ok=True)
    return f"versions/{segment_id}/v{version:03d}.wav", sha256_file(target)


def _next_version(base: Path, segment_id: str) -> int:
    versions = resolve_within(base, "versions")
    versions.mkdir(parents=True, exist_ok=True)
    directory = resolve_within(versions, segment_id)
    if not directory.is_dir():
        return 1
    numbers = [
        int(p.stem[1:]) for p in directory.glob("v[0-9][0-9][0-9].wav") if p.stem[1:].isdigit()
    ]
    return max(numbers, default=0) + 1


def _record_failure(
    ws,
    owner_id: str,
    profile_id: str,
    *,
    action: str,
    script_sha256: str,
    segment_id: str,
    provider_id: str,
    exc: BaseException,
) -> dict:
    """Append a redacted provider failure to history before propagating it."""
    from creator_core.voice.providers.base import VoiceProvider

    entry = {
        "generation_id": f"gen_{uuid.uuid4().hex[:12]}",
        "action": action,
        "status": "failed",
        "created_at": _now(),
        "voice_profile_id": profile_id,
        "segment_id": segment_id,
        "script_sha256": script_sha256,
        "provider_id": provider_id,
        "error": VoiceProvider.safe_error(exc),
    }
    try:
        _append_history(ws, owner_id, profile_id, entry)
    except (OSError, ValidationError):  # pragma: no cover - history must not mask provider error
        pass
    return entry


def _consent_fingerprint(consent: dict) -> str:
    return canonical_sha256(
        {
            k: consent.get(k)
            for k in ("voice_profile_id", "owner_id", "status", "purposes", "consent_timestamp")
        }
    )


def _validate_segments(segments: list[dict]) -> list[dict]:
    if not isinstance(segments, list) or not segments:
        raise ValidationError("at least one narration segment is required")
    out: list[dict] = []
    seen: set[str] = set()
    for item in segments:
        if not isinstance(item, dict):
            raise ValidationError("each narration segment must be an object")
        segment_id = str(item.get("segment_id") or "")
        if not segment_id or not re_safe_id(segment_id):
            raise ValidationError(f"invalid segment id: {segment_id!r}")
        if segment_id in seen:
            raise ValidationError(f"duplicate segment id: {segment_id}")
        seen.add(segment_id)
        text = str(item.get("text") or "").strip()
        if not text:
            raise ValidationError(f"segment {segment_id} has no narration text")
        out.append({**item, "segment_id": segment_id, "text": text})
    return out


def re_safe_id(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value))


# ---- generation -----------------------------------------------------------------------------


def generate_narration(
    ws,
    owner_id: str,
    profile_id: str,
    segments: list[dict],
    *,
    purpose: str = "narration",
    script_sha256: str | None = None,
    project_id: str | None = None,
    config: dict | None = None,
    allow_external: bool = False,
    allow_noncommercial: bool = False,
    allow_test_provider: bool = False,
    acknowledgement: str | None = None,
) -> dict:
    """Synthesise every approved segment and commit one immutable generation version.

    Consent is verified here, not delegated to the caller. Each segment can fail independently:
    the first failure is recorded in history and propagated (exit 7 via the CLI), with no partial
    generation committed to `current.json`.
    """
    profile = load_profile(ws, owner_id, profile_id)
    consent = verify_consent(ws, owner_id, profile_id, purpose)  # authoritative gate
    checked = _validate_segments(segments)
    provider = select(
        profile,
        purpose=purpose,
        allow_external=allow_external,
        allow_noncommercial=allow_noncommercial,
        allow_test_provider=allow_test_provider,
        acknowledgement=acknowledgement,
    )
    config = config or load_config(ws)
    voice_info = _voice_info(ws, profile, config)
    base = generation_dir(ws, owner_id, profile_id)
    generation_id = f"gen_{uuid.uuid4().hex[:12]}"
    voice = dict(voice_info)
    voice["workspace"] = ws
    voice["config"] = config

    records: list[dict] = []
    for segment in checked:
        language = str(segment.get("language") or (profile.get("supported_languages") or ["en"])[0])
        provider.validate_language(language)
        result = None
        try:
            enriched = {**segment, "voice": voice}
            result = provider.generate_segment(enriched, language=language)
            version = _next_version(base, segment["segment_id"])
            audio_rel, audio_sha = _store_audio(result, base, segment["segment_id"], version)
        except Exception as exc:
            _record_failure(
                ws,
                owner_id,
                profile_id,
                action="generate",
                script_sha256=script_sha256 or "",
                segment_id=segment["segment_id"],
                provider_id=provider.provider_id,
                exc=exc,
            )
            if isinstance(exc, ValidationError):
                raise
            raise ProviderError(
                f"voice synthesis failed for {segment['segment_id']}: {provider.safe_error(exc)}"
            ) from exc

        records.append(
            {
                "segment_id": segment["segment_id"],
                "text": segment["text"],
                "audio": audio_rel,
                "audio_sha256": audio_sha,
                "version": version,
                "duration_seconds": round(float(result.duration_seconds), 3),
                "sample_rate": result.sample_rate,
                "mocked": bool(result.mocked),
                "model_provider": result.model_provider,
                "model_name": result.model_name,
                "model_version": result.model_version,
                "language": language,
                "status": "generated",
                "timeline_start": segment.get("timeline_start"),
                "timeline_end": segment.get("timeline_end"),
                "footage_start": segment.get("footage_start"),
                "footage_end": segment.get("footage_end"),
                "grounding": segment.get("grounding"),
                "word_timestamps": result.word_timestamps,
                "sentence_timestamps": result.sentence_timestamps,
                "notes": result.notes,
            }
        )

    state = {
        "schema_version": _SCHEMA_VERSION,
        "generation_id": generation_id,
        "voice_profile_id": profile_id,
        "project_id": project_id,
        "purpose": purpose,
        "created_at": _now(),
        "segments": {record["segment_id"]: record for record in records},
    }
    atomic_write_json(current_path(ws, owner_id, profile_id), state)
    entry = {
        "generation_id": generation_id,
        "action": "generate",
        "status": "ok",
        "created_at": state["created_at"],
        "voice_profile_id": profile_id,
        "project_id": project_id,
        "purpose": purpose,
        "profile_sha256": profile_sha256(profile),
        "consent_fingerprint": _consent_fingerprint(consent),
        "script_sha256": script_sha256,
        "provider": {"id": provider.provider_id, "name": provider.display_name},
        "segments": [
            {
                "segment_id": r["segment_id"],
                "version": r["version"],
                "audio_sha256": r["audio_sha256"],
                "mocked": r["mocked"],
            }
            for r in records
        ],
    }
    _append_history(ws, owner_id, profile_id, entry)

    # Ownership is never exposed in exports/JSON beyond the explicit owner_id argument.
    return {
        "ok": True,
        "command": "voice generate",
        "generation_id": generation_id,
        "voice_profile_id": profile_id,
        "owner_id": owner_id,
        "project_id": project_id,
        "purpose": purpose,
        "provider": entry["provider"],
        "consent_fingerprint": entry["consent_fingerprint"],
        "script_sha256": script_sha256,
        "mocked": any(r["mocked"] for r in records),
        "segments": records,
        "history_entries": len(history(ws, owner_id, profile_id)),
    }


def regenerate_segment(
    ws,
    owner_id: str,
    profile_id: str,
    segment_id: str,
    *,
    text: str | None = None,
    purpose: str = "narration",
    config: dict | None = None,
    allow_external: bool = False,
    allow_noncommercial: bool = False,
    allow_test_provider: bool = False,
    acknowledgement: str | None = None,
) -> dict:
    """Regenerate exactly one segment; all sibling segments stay byte-for-byte untouched."""
    state = current_state(ws, owner_id, profile_id)
    if not state or segment_id not in state.get("segments", {}):
        raise PathNotFoundError(
            f"segment {segment_id} has no generated audio for profile {profile_id}",
            hint="run `creator voice generate` first",
        )
    existing = state["segments"][segment_id]
    profile = load_profile(ws, owner_id, profile_id)
    consent = verify_consent(ws, owner_id, profile_id, purpose)  # gate again on every regeneration
    provider = select(
        profile,
        purpose=purpose,
        allow_external=allow_external,
        allow_noncommercial=allow_noncommercial,
        allow_test_provider=allow_test_provider,
        acknowledgement=acknowledgement,
    )
    config = config or load_config(ws)
    voice_info = _voice_info(ws, profile, config)
    voice_info["workspace"] = ws
    voice_info["config"] = config

    segment = dict(existing)
    segment["text"] = str(text).strip() if text is not None else existing["text"]
    if not segment["text"]:
        raise ValidationError("regenerated segment text cannot be empty")
    language = str(segment.get("language") or (profile.get("supported_languages") or ["en"])[0])
    provider.validate_language(language)

    base = generation_dir(ws, owner_id, profile_id)
    try:
        result = provider.generate_segment({**segment, "voice": voice_info}, language=language)
        version = _next_version(base, segment_id)
        audio_rel, audio_sha = _store_audio(result, base, segment_id, version)
    except Exception as exc:
        _record_failure(
            ws,
            owner_id,
            profile_id,
            action="regenerate",
            script_sha256="",
            segment_id=segment_id,
            provider_id=provider.provider_id,
            exc=exc,
        )
        if isinstance(exc, ValidationError):
            raise
        raise ProviderError(
            f"voice regeneration failed for {segment_id}: {provider.safe_error(exc)}"
        ) from exc

    siblings_before = {
        key: value.get("audio_sha256")
        for key, value in state["segments"].items()
        if key != segment_id
    }
    updated = dict(state)
    segments = dict(state["segments"])
    segments[segment_id] = {
        **existing,
        "text": segment["text"],
        "audio": audio_rel,
        "audio_sha256": audio_sha,
        "version": version,
        "duration_seconds": round(float(result.duration_seconds), 3),
        "sample_rate": result.sample_rate,
        "mocked": bool(result.mocked),
        "model_provider": result.model_provider,
        "model_name": result.model_name,
        "model_version": result.model_version,
        "status": "regenerated",
        "word_timestamps": result.word_timestamps,
        "sentence_timestamps": result.sentence_timestamps,
        "notes": result.notes,
    }
    updated["segments"] = segments
    updated["generation_id"] = f"gen_{uuid.uuid4().hex[:12]}"
    updated["updated_at"] = _now()
    atomic_write_json(current_path(ws, owner_id, profile_id), updated)
    siblings_after = {
        key: value.get("audio_sha256") for key, value in segments.items() if key != segment_id
    }
    if siblings_before != siblings_after:
        raise ValidationError("regeneration unexpectedly changed sibling segments")

    entry = {
        "generation_id": updated["generation_id"],
        "action": "regenerate",
        "status": "ok",
        "created_at": updated["updated_at"],
        "voice_profile_id": profile_id,
        "segment_id": segment_id,
        "previous_version": existing["version"],
        "version": version,
        "previous_audio_sha256": existing.get("audio_sha256"),
        "audio_sha256": audio_sha,
        "provider": {"id": provider.provider_id, "name": provider.display_name},
        "consent_fingerprint": _consent_fingerprint(consent),
        "preserved_siblings": sorted(siblings_after),
    }
    _append_history(ws, owner_id, profile_id, entry)
    return {
        "ok": True,
        "command": "voice regenerate",
        "generation_id": updated["generation_id"],
        "voice_profile_id": profile_id,
        "segment_id": segment_id,
        "previous_version": existing["version"],
        "version": version,
        "audio": audio_rel,
        "audio_sha256": audio_sha,
        "text": segment["text"],
        "mocked": bool(result.mocked),
        "preserved_siblings": sorted(siblings_after),
        "history_entries": len(history(ws, owner_id, profile_id)),
    }
