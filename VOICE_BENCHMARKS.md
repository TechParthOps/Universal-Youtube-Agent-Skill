# VoiceDNA Benchmarks — honest measurement record

Status: Phase 11J. Version 0.10.0a0, contract 1.0. This document deliberately contains **no
invented performance figures**. Every number below came from a command run in this checkout; anything
not measured is marked `not measured`.

## 1. Neural model inference — NOT BENCHMARKED

No neural TTS/voice-cloning model was downloaded, loaded or executed in this environment. The
following are therefore **unmeasured** and must not be inferred from adapter declarations or tests:

| Provider | Synthesis latency | MOS / naturalness | Speaker similarity | Language accuracy | Commercial-use verification |
|---|---|---|---|---|---|
| OpenVoice v2 (default) | not measured | not measured | not measured | not measured | licence reviewed (MIT code + MIT weights, explicit commercial use); weights not executed |
| Chatterbox (secondary) | not measured | not measured | not measured | not measured | licence reviewed (MIT, retired archive); weights not executed |
| F5-TTS | not measured | not measured | not measured | not measured | licence-gated (non-commercial terms); adapter refuses without licensed weights |
| XTTS-v2 | not measured | not measured | not measured | not measured | licence-gated (CPLA); adapter refuses without licensed weights |
| Fish Speech | not measured | not measured | not measured | not measured | licence-gated; adapter refuses without licensed weights |

Why: `VOICEDNA_ARCHITECTURE.md §3` requires optional neural dependencies and weights to be lazy and
fail closed. `creator voice providers` reports `available=false` for these adapters here. Tests use
the deterministic `test_tone` provider (labelled `mocked=true`) or the real local Windows SAPI
adapter. Neither is a neural model, so neither can stand in as a benchmark for one.

To produce real benchmarks, a separately licensed environment must run an explicit harness that
records: model/weight hashes, sample rate, text, wall-clock latency, RT factor, FFprobe duration and
a human MOS panel with rater count and confidence intervals. Until then this table stays empty.

## 2. What WAS measured (real tools, real synthetic media)

All figures below come from `pytest` runs, the acceptance demo (`scripts/voice_acceptance_demo.py`),
or direct FFprobe/FFmpeg calls on locally generated fixtures. They describe the *pipeline*, not
model quality.

| Measurement | Observed value | Source / method |
|---|---|---|
| Baseline full suite before VoiceDNA | `450 passed, 2 skipped`, `TOTAL 6276 1254 80%` | `python -m pytest --cov=creator_core --cov-report=term-missing` at `bf937bdf` |
| Historical "87% coverage" claim | **false — measured 80%** | `POST_RELEASE_AUDIT.md §1`; `CHANGELOG.md` corrected at 11J |
| Voice test suite after 11I | `73 passed` | `python -m pytest tests/voice -q --no-cov` |
| Security regression alongside voice | `79 passed, 1 skipped` | `python -m pytest tests/voice tests/security/test_audit_fixes.py -q --no-cov` after 11I (before the 11J CLI tests) |
| Real SAPI synthesis (no mocking) | `mocked=false`, `duration_seconds=5.784`, `sample_rate=22050` | `creator voice test` with installed `Microsoft David Desktop` (Phase 11D) |
| SAPI voices discovered on this host | `Microsoft David Desktop`, `Microsoft Zira Desktop` | `system_sapi` adapter `validate_model()` |
| Acceptance demo provider | `system_sapi` / `Microsoft David Desktop`, `mocked=false` | `python scripts/voice_acceptance_demo.py --json` |
| Acceptance demo source footage | real FFprobe duration `12.0s`, six detector groups (`silence`, `scenes`, `audio_events`, `moments`, `transcript`, `repeated_takes`) | `media.analyze` on a locally rendered silent three-scene MP4 |
| Acceptance demo script grounding | `5` measured anchors, one approved HMAC token, consumed once | demo trace steps 3–4 |
| Acceptance demo narration | `2` real SAPI segments, `mocked=false` | demo trace step 4 |
| Acceptance demo alignment | timing label `estimated`, `8` narration captions | demo trace step 5 |
| Acceptance demo mix | real FFmpeg output, FFprobe duration `12.0s`, `2` duck windows | demo trace step 7 |
| Acceptance demo regeneration | `seg_001` v1 → v2, sibling `seg_002` preserved byte-for-byte | demo trace step 8 |
| Acceptance demo original integrity | source + base-render SHA-256 unchanged after all passes | demo trace step 11 |
| Acceptance demo QA | `ok=false` — unreviewed disclosure + narration timeline longer than the 12s render | demo trace step 10 (correct safety behaviour, not a pass) |

Timings were not collected with a high-resolution benchmark harness, so no latency/RT-factor number
is claimed for any provider — including SAPI. The SAPI duration above is the *measured length of the
generated WAV* (FFprobe), which is a property of the audio, not of synthesis speed.

## 3. Interpretation rules

1. `mocked=true` output is never evidence of speech quality. `test_tone` writes a deterministic
   waveform so tests can assert file, hash, duration and versioning behaviour.
2. An adapter's licence flags are declarations reviewed from public licence terms, not a legal
   opinion and not proof that a specific weight file is licensed. Users must verify their own
   weights.
3. Alignment figures are `estimated` (duration measured from WAV; boundaries allocated by character
   count). They are not word-level ground truth.
4. A/V validation is a duration-tolerance check, explicitly **not** a lip-sync or frame-accuracy
   assertion.
5. No benchmark here implies publication readiness. The demo deliberately ends with
   `publication_ready=false` because disclosure review and A/V fit remain human gates.

## 4. Reproduce

```powershell
python -m pytest tests/voice -q --no-cov
python scripts/voice_acceptance_demo.py --json
python -m pytest --cov=creator_core --cov-report=term-missing
```
