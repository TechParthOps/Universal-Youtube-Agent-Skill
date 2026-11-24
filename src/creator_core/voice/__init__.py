"""VoiceDNA — creator voice intelligence and synchronization (Phase 11).

Design invariants:
  * A voice profile is a sensitive asset owned by exactly one `owner_id` in one workspace.
  * Consent is explicit, recorded, purpose-scoped and revocable; generation is impossible without it.
  * No neural model is downloaded or executed implicitly. Providers are lazy, capability-tagged and
    fail closed. Mocked tests never claim to be model inference.
  * Reference audio is stored once, inside the workspace, in a per-owner directory.
  * Nothing in this package performs impersonation: only the verified owner may use a profile.
"""

from creator_core.voice.alignment import align_segments, validate_av_sync
from creator_core.voice.compose import build_voice_edl
from creator_core.voice.consent import PURPOSES, grant_consent, revoke_consent, verify_consent
from creator_core.voice.hybrid import apply_section_actions, classify_sections
from creator_core.voice.mix import render_voice_mix
from creator_core.voice.onboarding import register_voice
from creator_core.voice.paths import VoicePaths, voice_paths
from creator_core.voice.preview import approve_segment, preview_data, segment_preview
from creator_core.voice.profile import (
    PROFILE_SCHEMA_VERSION,
    create_profile,
    delete_profile,
    list_profiles,
    load_profile,
    new_profile_id,
    update_profile,
)
from creator_core.voice.qa import assess_disclosure, run_voice_qa
from creator_core.voice.script import build_voice_script
from creator_core.voice.synthesis import (
    current_state,
    generate_narration,
    history,
    regenerate_segment,
)

__all__ = [
    "PROFILE_SCHEMA_VERSION",
    "PURPOSES",
    "VoicePaths",
    "align_segments",
    "apply_section_actions",
    "approve_segment",
    "assess_disclosure",
    "build_voice_edl",
    "build_voice_script",
    "classify_sections",
    "create_profile",
    "current_state",
    "delete_profile",
    "generate_narration",
    "grant_consent",
    "history",
    "list_profiles",
    "load_profile",
    "new_profile_id",
    "preview_data",
    "regenerate_segment",
    "register_voice",
    "render_voice_mix",
    "revoke_consent",
    "run_voice_qa",
    "segment_preview",
    "update_profile",
    "validate_av_sync",
    "verify_consent",
    "voice_paths",
]
