# VoiceDNA Architecture — Creator Voice Intelligence and Synchronization Engine

Status: Phase 11B design, implemented incrementally in 11C–11J. Version 0.10.0a0, contract 1.0.

## 1. Principles

1. **Additive integration.** VoiceDNA never changes the frozen `WorkspacePaths` dataclass or the
   existing EDL v1 message semantics. New state lives in a sibling `.creator/voice/<owner>/` tree;
   new EDL data is additive top-level metadata that older readers ignore.
2. **Consent before capability.** No provider can synthesise until an explicit, purpose-scoped,
   owner-bound consent record exists and is verified at generation time.
3. **Fail closed.** Optional neural dependencies, weights and vendor APIs are lazy-loaded. Missing
   dependency → structured exit code 3; missing consent → 4; disallowed licence → 2. Nothing is
   silently simulated or downloaded.
4. **Honest evidence.** Every derived timing, quality and similarity figure is labelled
   `measured`, `estimated` or `unverified`. Mocked provider output is labelled `mocked=True`.
5. **Smallest complete loop first.** A real local `system_sapi` provider and a deterministic
   `test_tone` provider prove the whole pipeline end to end before any neural model is involved.

## 2. Package layout

```
src/creator_core/voice/
  paths.py           owner-scoped storage layout, strict owner-id validation
  consent.py         purpose-scoped grants, verification, revocation, audit
  profile.py         immutable versioned profiles, deletion, reference path resolution
  reference.py       real FFprobe/PCM validation, single-copy secure storage
  providers/
    base.py          VoiceProvider ABC, capability flags, result dataclasses
    registry.py      provider selection, licence gates, lazy availability checks
    system_sapi.py   REAL local Windows SAPI speech (clones_voice=False)
    test_tone.py     deterministic offline tone WAVs for tests (never for production)
    openvoice.py     OpenVoice v2 adapter (MIT; default neural; lazy)
    chatterbox.py    Chatterbox adapter (MIT archive; secondary; lazy)
    gated.py         F5-TTS / XTTS-v2 / Fish Speech licence-gated adapters
  script.py          footage-grounded, timestamp-aware narration segments
  synthesis.py       segment generation with consent + history + version compare
  alignment.py       sentence/segment timing, optional word timing, AV validation
  mix.py             narration/original/gameplay/music levels, ducking, hybrid narration
  compose.py         map segments to footage, build additive voice EDL + captions
  preview.py         segment-scoped preview/regenerate/approve data model
  qa.py              voice + video objective checks
  disclosure.py      YouTube synthetic-content disclosure assessment
  cli.py             `creator voice ...` command handlers
```

## 3. Data flow (final acceptance demo)

```
reference WAV → validate_reference → store_reference (one owner-scoped copy)
             → grant_consent → create_profile (version 1)

silent footage → media.analyze (real FFmpeg scenes/silence/events)
              → media.index (injection-safe, searchable moments)
              → script.build_voice_script(footage_report, concept, profile)
                 [segments grounded in measured moments; refuses missing footage]
              → creator approval (HMAC token, full request bound)

approved script → registry.select(profile, purpose)
               → verify_consent(owner, profile, "narration")
               → provider.generate_segment(...) per segment
               → SpeechResult audio WAVs + measured durations/timestamps
               → synthesis saves immutable version + history record

SpeechResult + footage signals → alignment.align_segments (duration-derived/tagged)
                               → compose.build_voice_edl (video clips + captions metadata)
                               → mix.render_voice_mix (narration + gameplay + music ducking)
                               → existing editing/render.py produces base video
                               → second FFmpeg pass muxes the narration mix
                               → qa.run_voice_qa → preview workspace data

preview → regenerate ONE segment (new version, siblings untouched) → re-mix → re-render
        → revision via existing editing/revisions.py
        → disclosure.assess → existing private-first publish approval
        → consent revocation + delete_profile removes all local voice data
```

## 4. Owner isolation

Owner ids are strictly validated (`^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$`, no `..`). Every profile,
reference, consent record, generation history entry and export is beneath
`<workspace>/.creator/voice/<owner_id>/...` and re-confined with `resolve_within()`. Cross-owner
loads are impossible without both a valid owner id and a profile id that was created under it.
`list_profiles()` redacts owner ids unless a specific owner is requested.

## 5. Consent and provider coupling

`consent.verify_consent(ws, owner, profile, purpose)` is called inside `synthesis.generate_*`, not
just by the CLI. Each profile carries `supported_languages` and `pronunciation_preferences`;
providers expose `supported_languages()` and `supported_controls()`. A requested language or
control not advertised by the selected provider is rejected (exit 2) rather than ignored.
External/hosted providers additionally require an explicit `allow_external=True` flag recorded in
the audit log before any reference bytes leave the workspace.

## 6. Timeline model

The existing EDL v1 remains the render source of truth. VoiceDNA adds an **additive** top-level
object:

```json
{
  "voice": {
    "schema_version": 1,
    "profile_id": "vpr_...",
    "owner_id": "<redacted in exports>",
    "provider": {"id": "system_sapi", "model": "Microsoft David Desktop", "version": "1"},
    "consent_fingerprint": "sha256:...",
    "segments": [
      {"segment_id": "seg_001", "text": "...", "timeline_start": 0.0, "timeline_end": 4.2,
       "footage_start": 3.1, "footage_end": 7.3, "audio": "voice/.../seg_001.wav",
       "word_timestamps": [{"word": "...", "start": 0.1, "end": 0.4, "status": "estimated"}],
       "version": 2, "mocked": false, "status": "approved"}
    ],
    "mix": {"narration_db": 0.0, "gameplay_db": -16.0, "music_db": -24.0,
            "duck_to_db": -28.0, "hybrid_sections": []},
    "disclosure": {"assessment": "review", "reasons": ["..."], "creator_reviewed": false}
  }
}
```

Existing tracks (`video`, `audio`, `music`, `captions`) are still produced exactly as before, so
`editing/graph.py`, render and QA continue to work unchanged. The narration mix is applied as a
separate mux pass; the editor can inspect/modify every segment through the `voice.segments` array.

## 7. Alignment honesty

No selected provider exposes native word-level timestamps. Segment boundaries come from measured
WAV duration (FFprobe) and sentence boundaries from punctuation with durations allocated by character
count; both are labelled `estimated`. If a future adapter returns real word timings they are marked
`measured`. A/V validation compares the rendered duration with the narration timeline and reports
tolerance breaches; it never claims frame-accurate synchrony.

## 8. Hybrid narration

Sections are classified as `original` (creator speech retained), `generated` (new narration),
`gameplay_only` or `silent_reaction`. Original spoken sections are detected using existing
`media.analyze` silence/audio-event signals plus optional ASR segments; they are preserved by
default. `mix` controls per section allow preserve, insert, replace and silence. No source track is
ever overwritten — all mixing is non-destructive.

## 9. Failure semantics

| Condition | Exit code | Behaviour |
|---|---:|---|
| Invalid owner/profile/path | 2 | Reject before any filesystem mutation |
| Missing consent or revoked | 4 | No provider called |
| Optional neural dependency/weights missing | 3 | Clear install guidance; no implicit download |
| Disallowed commercial licence | 2 | Adapter refuses; no inference |
| Provider error | 7 | Error recorded in history with redacted detail |
| Approval missing for script/publish | 5 | Existing HMAC gate applies |

## 10. Deferred / explicitly unsupported

- No model training or fine-tuning pipeline.
- No voice conversion of third parties, no impersonation, no "celebrity" presets.
- No native word timestamps or emotion vector from providers that do not advertise them.
- No encrypted-at-rest reference storage in this single-user local release (owner-scoped + audit
  logging only; see `VOICE_SECURITY.md`).
- No claim of neural inference until it is actually executed and reported in `VOICE_BENCHMARKS.md`.
