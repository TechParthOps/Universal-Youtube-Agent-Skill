# VoiceDNA API Contract (v1, additive to contract 1.0)

All new functions are importable from `creator_core.voice.*`. Existing CLI JSON conventions apply:
success is JSON on stdout; errors are `{"error":{"code","message","hint"}}` on stderr with the
existing exit codes (0 ok, 1 unexpected, 2 validation, 3 missing dependency, 4 auth, 5 approval,
6 I/O, 7 provider).

## Registration layer (Phase 11C — implemented)

```python
from creator_core.voice import (
    voice_paths,
    create_profile,
    load_profile,
    update_profile,
    list_profiles,
    delete_profile,
    grant_consent,
    verify_consent,
    revoke_consent,
    PURPOSES,
)
from creator_core.voice.reference import analyze_reference, store_reference, RECORDING_GUIDANCE

paths = voice_paths(ws)  # sibling of frozen WorkspacePaths
assessment = analyze_reference(path, ws)  # real ffprobe + bounded PCM analysis
meta = store_reference(ws, owner, profile_id, path, assessment)
profile = create_profile(ws, owner, {...})  # immutable voice_v001.json
grant_consent(ws, owner, profile_id, ["narration"], audit=AuditLog(ws.root))
consent = verify_consent(ws, owner, profile_id, "narration")
```

`assessment` fields: `ok`, `sha256`, `duration_seconds`, `sample_rate_hz`, `channels`, `codec`,
`peak_dbfs`, `rms_dbfs`, `noise_floor_dbfs`, `snr_estimate_db`, `clipping_ratio`, `silence_ratio`,
`longest_silence_seconds`, `speaker_count_estimate{value,verified,evidence}`, `checks{}`,
`quality_score`, `suitable`, `reasons[]`, `guidance[]`, `label`, `method`.

Profile versions are immutable; `update_profile()` writes `voice_vNNN.json` and moves a latest
pointer. Provider identity is immutable within a profile.

## Provider layer (Phase 11D)

```python
class VoiceProvider(Protocol):
    provider_id: str
    display_name: str
    weight_licence: str
    commercial_ok: bool
    clones_voice: bool

    def is_available(self) -> bool: ...  # never imports heavy deps eagerly
    def validate_model(self) -> dict: ...  # exit 3 if missing
    def validate_reference(self, meta: dict) -> dict: ...
    def register_voice(self, profile: dict, reference: Path) -> dict: ...
    def generate_speech(
        self, text: str, *, language: str, voice: dict, controls: dict
    ) -> SpeechResult: ...
    def generate_segment(self, segment: dict, **kw) -> SpeechResult: ...
    def supported_languages(self) -> list[str]: ...
    def supported_controls(self) -> list[str]: ...
    def delete_voice_data(self, profile_id: str) -> dict: ...
```

`SpeechResult` fields: `audio_path`, `duration_seconds`, `sample_rate`, `word_timestamps[]`,
`sentence_timestamps[]`, `model_provider`, `model_name`, `model_version`, `mocked`, `notes`.
A timestamp entry is `{"text"|"word", "start", "end", "status": "verified"|"estimated"}`
(`"measured"` from older payloads is normalized to `"verified"` by `align_segments`). Provider
timestamps are `verified`; waveform-anchored boundaries carry `anchor: "waveform_window"` and
`window_status: "verified"` but remain `status: "estimated"` because silencedetect finds pauses,
not words.

`registry.select(profile, *, purpose, allow_external=False, allow_noncommercial=False)` returns a
provider or raises a structured error. Licence gates are enforced here, not in the UI.

## Generation / timeline layer (Phases 11E–11I)

```python
segments = build_voice_script(footage_report, concept, profile)  # footage-grounded
result = generate_narration(ws, owner, profile_id, segments, purpose="narration")
aligned = align_segments(ws, results, segments)  # honest timing labels
edl = build_voice_edl(base_edl, aligned, mix_config)  # additive "voice" object
mix = render_voice_mix(ws, project_id, edl)  # narration/gameplay/music/hybrid
report = run_voice_qa(ws, project_id, edl, mix)  # objective checks
preview = preview_data(ws, project_id)  # list/compare/regenerate/approve
```

Segment fields: `segment_id`, `text`, `timeline_start/end`, `footage_start/end`, `language`,
`speed`, `emotion`, `pronunciation_overrides`, `status`, `version`, `audio`, `mocked`,
`word_timestamps`. Regeneration replaces exactly one version and preserves the previous approved
version in `generation/<profile_id>/versions/`.

## CLI (Phase 11D onward)

```
creator voice setup --owner ID --reference FILE --display-name NAME [--provider ID] --confirm-voice-ownership --approve [--json]
creator voice providers [--json]
creator voice languages [--json]
creator voice register --owner ID --reference FILE --display-name NAME --purpose narration --confirm-voice-ownership --approve [--json]
creator voice inspect --owner ID --profile ID [--json]
creator voice test --owner ID --profile ID --text "..." [--json]
creator voice generate --owner ID --profile ID --project PRJ --concept "..." [--approve] [--json]
creator voice profiles [--owner ID] [--json]
creator voice history --owner ID --profile ID [--json]
creator voice regenerate --owner ID --profile ID --segment seg_003 [--text ...] [--approve] [--json]
creator voice delete --owner ID --profile ID --approve [--json]
creator voice consent grant --owner ID --profile ID --purpose narration --confirm-voice-ownership --approve [--json]
creator voice consent revoke --owner ID --profile ID --purpose narration [--approve] [--json]
```

All `voice` subcommands emit contract JSON with `--json`. Approval-gated commands (register,
generate, regenerate, delete, consent grant/revoke, disclosure) use a single HMAC primitive bound to
the full canonical request and consumed once; replay or a changed field exits `5`. Registration and
`consent grant` additionally require `--confirm-voice-ownership` against the displayed statement.
At the library level, `register_voice(..., consent_statement: str)` is mandatory: consent is never
inferred from a default string.

## Backward compatibility

The Stage 12I security contract is intentionally stricter than the alpha CLI: older commands that
omit `--confirm-voice-ownership`, or pre-issued tokens built without the final profile fields, now
fail closed. The EDL v1 data format is unchanged: it gains an optional `voice` object that readers
which do not know it ignore. `editing/graph.py`, `editing/render.py`, QA, preview, Shorts,
publishing and MCP continue to operate on unmodified timelines. Migration for callers: include the
ownership confirmation and rebuild approval tokens from the exact request.
