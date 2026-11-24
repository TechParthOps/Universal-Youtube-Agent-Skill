"""Objective voice QA and synthetic-content disclosure (Phase 11I).

QA is check-based, not a subjective quality score. Every result carries a provenance label, and
mocked audio or a missing creator disclosure always produces `needs_approval` entries rather than
being silently waved through.
"""

from __future__ import annotations

from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.research.provenance import ESTIMATED, VERIFIED
from creator_core.security.paths import resolve_within
from creator_core.voice.alignment import validate_av_sync
from creator_core.voice.mix import find_profile_owner
from creator_core.voice.synthesis import generation_dir

_SCHEMA_VERSION = 1


def assess_disclosure(voice: dict) -> dict:
    """Assess YouTube synthetic-content disclosure obligations for a voice object."""
    if not isinstance(voice, dict):
        raise ValidationError("voice metadata is required for disclosure assessment")
    reasons: list[str] = []
    provider = voice.get("provider") or {}
    segments = voice.get("segments") or []
    if provider.get("clones_voice"):
        reasons.append("A voice-cloning provider was registered for this narration.")
    if segments:
        reasons.append("The video contains AI-generated narration segments.")
    if any(s.get("mocked") for s in segments):
        reasons.append("At least one segment is MOCKED tone audio and must never be published.")
    if not voice.get("consent_fingerprint"):
        reasons.append("No consent fingerprint is recorded for the generation.")
    assessment = "required" if reasons else "none"
    return {
        "schema_version": _SCHEMA_VERSION,
        "assessment": assessment,
        "reasons": reasons,
        "creator_reviewed": False,
        "youtube_synthetic_content_disclosure": (
            "Creator should mark the upload as containing synthetic content."
            if reasons
            else "No synthetic voice disclosure indicated by available metadata."
        ),
        "label": VERIFIED,
        "method": "provider capability flags, segment provenance and consent metadata",
    }


def _file_check(path: Path, expected_sha: str | None) -> dict:
    if not path.is_file():
        return {"ok": False, "status": VERIFIED, "detail": f"missing audio: {path.name}"}
    if expected_sha:
        from creator_core.media.ingest import sha256_file

        if sha256_file(path) != expected_sha:
            return {"ok": False, "status": VERIFIED, "detail": f"sha256 mismatch: {path.name}"}
    return {"ok": True, "status": VERIFIED, "detail": f"verified audio: {path.name}"}


def run_voice_qa(ws, project_id: str, edl: dict, mix: dict | None = None) -> dict:
    """Run objective checks on a composed voice EDL and optional mix render."""
    voice = edl.get("voice") if isinstance(edl, dict) else None
    if not isinstance(voice, dict):
        raise ValidationError("EDL has no voice metadata to check")
    checks: list[dict] = []
    needs_approval: list[str] = []

    def add(
        name: str, ok: bool, detail: str, *, status: str = VERIFIED, approval: str | None = None
    ) -> None:
        checks.append({"name": name, "ok": bool(ok), "status": status, "detail": detail})
        if not ok and approval:
            needs_approval.append(approval)

    add(
        "profile_id",
        bool(voice.get("profile_id")),
        "voice profile id present",
        approval="Missing voice profile id",
    )
    add(
        "consent",
        bool(voice.get("consent_fingerprint")),
        "consent fingerprint recorded inside generation"
        if voice.get("consent_fingerprint")
        else "no consent fingerprint",
        approval="Generation lacks recorded consent",
    )

    segments = voice.get("segments") or []
    add(
        "segments",
        bool(segments),
        f"{len(segments)} narration segment(s)",
        approval="No narration segments",
    )
    owner = None
    root = None
    if voice.get("profile_id") and segments:
        try:
            owner = find_profile_owner(ws, str(voice["profile_id"]))
            root = generation_dir(ws, owner, str(voice["profile_id"]))
        except ValidationError as exc:
            add("profile_owner", False, str(exc), approval="Voice profile could not be resolved")

    for segment in segments:
        sid = str(segment.get("segment_id") or "?")
        if root is not None and segment.get("audio"):
            try:
                check = _file_check(
                    resolve_within(root, str(segment["audio"])), segment.get("audio_sha256")
                )
                add(
                    f"audio.{sid}",
                    check["ok"],
                    check["detail"],
                    approval=f"Narration audio for {sid} failed verification",
                )
            except ValidationError as exc:
                add(
                    f"audio.{sid}", False, str(exc), approval=f"Narration audio for {sid} is unsafe"
                )
        start, end = (
            float(segment.get("timeline_start") or 0),
            float(segment.get("timeline_end") or 0),
        )
        add(
            f"timing.{sid}",
            end > start,
            f"timeline {start:.3f}–{end:.3f}s",
            status=ESTIMATED,
            approval=f"Segment {sid} has invalid timing",
        )
        if segment.get("mocked"):
            needs_approval.append(f"MOCKED audio in {sid} must not be published")

    caption_ids = {
        c.get("cue_id")
        for c in (edl.get("tracks", {}).get("captions") or [])
        if isinstance(c, dict)
    }
    add(
        "captions",
        bool(caption_ids),
        f"{len(caption_ids)} caption cue(s)",
        approval="No narration captions were composed",
    )
    for cue in edl.get("tracks", {}).get("captions") or []:
        if not isinstance(cue, dict) or cue.get("label_type") != "synthetic_narration":
            continue
        ok = float(cue.get("end") or 0) > float(cue.get("start") or 0) and bool(cue.get("text"))
        add(
            f"caption.{cue.get('cue_id')}",
            ok,
            "caption timing/text valid",
            status=ESTIMATED,
            approval="A narration caption is invalid",
        )

    disclosure = voice.get("disclosure") or assess_disclosure(voice)
    add(
        "disclosure",
        bool(disclosure.get("creator_reviewed")),
        "creator has reviewed the synthetic-content disclosure"
        if disclosure.get("creator_reviewed")
        else "creator disclosure review is outstanding",
        approval="Creator has not reviewed the synthetic-content disclosure",
    )

    if mix and float(mix.get("duration_seconds") or 0) > 0:
        alignment = {
            "segments": segments,
            "timeline_end": max((float(s.get("timeline_end") or 0) for s in segments), default=0.0),
        }
        sync = validate_av_sync(alignment, mix["duration_seconds"], tolerance_seconds=0.5)
        add(
            "av_sync",
            sync["within_tolerance"],
            f"delta={sync['delta_seconds']}s tolerance={sync['tolerance_seconds']}s",
            approval="Narration timeline exceeds the rendered duration",
        )

    # De-duplicate while preserving order.
    needs_approval = list(dict.fromkeys(needs_approval))
    return {
        "schema_version": _SCHEMA_VERSION,
        "command": "voice qa",
        "ok": all(check["ok"] for check in checks) and not needs_approval,
        "project_id": project_id,
        "checks": checks,
        "needs_approval": needs_approval,
        "disclosure": disclosure,
        "label": ESTIMATED,
        "note": "Objective checks only; no subjective voice-quality or similarity score is claimed.",
    }
