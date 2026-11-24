"""Additive voice EDL composition and narration captions (Phase 11F/11G).

The EDL v1 tracks are never rewritten: voice data is a top-level `voice` object that old readers
ignore, and narration cues are appended with distinct ids. All captions are sanitized plain text.
"""

from __future__ import annotations

import copy

from creator_core.cli.errors import ValidationError
from creator_core.media.injection import sanitize_untrusted
from creator_core.research.provenance import ESTIMATED

_SCHEMA_VERSION = 1
_DEFAULT_MIX = {
    "narration_db": 0.0,
    "gameplay_db": -16.0,
    "music_db": -24.0,
    "duck_to_db": -28.0,
    "mode": "insert",
    "hybrid_sections": [],
}


def _validate(edl: dict) -> None:
    if not isinstance(edl, dict) or not isinstance(edl.get("tracks"), dict):
        raise ValidationError(
            "base EDL must have a tracks object", hint="run `creator edit new` first"
        )
    for track in ("video", "audio", "music", "captions"):
        if track not in edl["tracks"]:
            raise ValidationError(f"base EDL is missing the {track!r} track")


def build_voice_edl(
    base_edl: dict,
    aligned: dict,
    mix_config: dict | None = None,
    *,
    disclosure: dict | None = None,
    owner_id: str | None = None,
) -> dict:
    """Return a copy of `base_edl` carrying additive VoiceDNA metadata and narration captions."""
    _validate(base_edl)
    if not isinstance(aligned, dict) or not aligned.get("segments"):
        raise ValidationError("alignment payload has no segments")
    mix = dict(_DEFAULT_MIX)
    if mix_config:
        unknown = set(mix_config) - set(_DEFAULT_MIX)
        if unknown:
            raise ValidationError(f"unknown voice mix setting(s): {', '.join(sorted(unknown))}")
        mix.update(mix_config)

    out = copy.deepcopy(base_edl)
    segments = []
    cues = []
    existing_cue_ids = {
        str(c.get("cue_id")) for c in out["tracks"]["captions"] if isinstance(c, dict)
    }
    for index, segment in enumerate(aligned["segments"]):
        safe_text = sanitize_untrusted(str(segment.get("text") or "")).strip()[:400]
        if not safe_text:
            raise ValidationError(f"segment {segment.get('segment_id')} has no narration text")
        segments.append(
            {
                "segment_id": segment["segment_id"],
                "text": safe_text,
                "version": segment.get("version", 1),
                "status": segment.get("status", "generated"),
                "mocked": bool(segment.get("mocked")),
                "language": segment.get("language") or "en",
                "timeline_start": segment["timeline_start"],
                "timeline_end": segment["timeline_end"],
                "footage_start": segment.get("footage_start"),
                "footage_end": segment.get("footage_end"),
                "audio": segment.get("audio"),
                "audio_sha256": segment.get("audio_sha256"),
                "duration_seconds": segment.get("duration_seconds"),
                "word_timestamps": segment.get("word_timestamps") or [],
                "grounding": segment.get("grounding"),
            }
        )
        pieces = segment.get("sentence_timestamps") or [
            {
                "text": safe_text,
                "start": segment["timeline_start"],
                "end": segment["timeline_end"],
                "status": ESTIMATED,
            }
        ]
        for part_index, piece in enumerate(pieces):
            cue_id = f"voice_{index + 1:03d}_{part_index + 1:02d}"
            if cue_id in existing_cue_ids:
                continue
            start = float(piece.get("start") or segment["timeline_start"])
            end = float(piece.get("end") or segment["timeline_end"])
            if end <= start:
                continue
            cue_text = sanitize_untrusted(str(piece.get("text") or safe_text)).strip()[:240]
            if not cue_text:
                continue
            cue = {
                "cue_id": cue_id,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": cue_text,
                "style": "narration",
                "label_type": "synthetic_narration",
            }
            out["tracks"]["captions"].append(cue)
            existing_cue_ids.add(cue_id)
            cues.append(cue)

    voice = {
        "schema_version": _SCHEMA_VERSION,
        "profile_id": aligned.get("profile_id"),
        "owner_id": owner_id or "<redacted>",
        "generation_id": aligned.get("generation_id"),
        "script_sha256": aligned.get("script_sha256"),
        "consent_fingerprint": aligned.get("consent_fingerprint"),
        "provider": aligned.get("provider") or {},
        "segments": segments,
        "mix": mix,
        "timeline_duration_seconds": aligned.get("timeline_duration_seconds"),
        "caption_cues": cues,
        "note": (
            "Additive metadata. EDL v1 tracks remain valid for older readers; the voice mix "
            "is applied as a separate, non-destructive pass."
        ),
    }
    # Derive a concrete synthetic-content assessment from provider capability, segment provenance
    # and the recorded consent fingerprint — not an empty "review" placeholder. The creator must
    # still explicitly review and accept it (creator_reviewed stays False).
    if disclosure is None:
        from creator_core.voice.qa import assess_disclosure

        disclosure = assess_disclosure(voice)
    voice["disclosure"] = disclosure
    out["voice"] = voice
    return out
