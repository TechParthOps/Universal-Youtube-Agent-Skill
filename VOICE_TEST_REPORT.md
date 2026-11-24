# VoiceDNA Test Report — Phases 11A–11J

Version 0.10.0a0, contract 1.0. All commands were run from the repository root on Windows with
Python 3.11 and FFmpeg 9.1. Nothing was uploaded or published.

## 1. Test inventory

| Area | Test file | Mandatory coverage |
|---|---|---|
| Registration & profiles | `tests/voice/test_registration.py` | versioned profiles, single-copy references, owner-scoped paths, onboarding, deletion |
| Consent | `tests/voice/test_registration.py`, `tests/voice/test_synthesis.py` | explicit grant, purpose scope, revocation, generation-time enforcement |
| Isolation | `tests/voice/test_registration.py`, `tests/security/test_audit_fixes.py` | owner ids validated, cross-owner reads rejected, `list_profiles` redaction |
| Adapters & gates | `tests/voice/test_providers.py` | lazy availability, licence/tests-only/external gates, real SAPI, deterministic tone |
| Failures & redaction | `tests/voice/test_providers.py`, `tests/voice/test_synthesis.py` | provider errors, structured exit codes, secrets stripped from history |
| Generation & regeneration | `tests/voice/test_synthesis.py` | immutable versions, history, one-segment regeneration, sibling hashes unchanged |
| Alignment & timeline | `tests/voice/test_timeline.py` | measured duration vs estimated boundaries, A/V tolerance, additive EDL, real mix |
| Languages & pronunciation | `tests/voice/test_providers.py`, `tests/voice/test_script.py` | provider language/control allowlists, per-profile pronunciation preferences |
| Hybrid narration | `tests/voice/test_timeline.py`, `tests/voice/test_cli_timeline.py` | signal-derived sections, per-section actions, non-destructive mix |
| Preview & approval | `tests/voice/test_timeline.py`, `tests/voice/test_cli_timeline.py` | version candidates, immutable EDL approval, no audio bytes in payloads |
| QA & disclosure | `tests/voice/test_timeline.py`, `tests/voice/test_cli_timeline.py` | mocked-audio flag, consent fingerprint, creator disclosure review gate |
| CLI & skill invocation | `tests/voice/test_cli.py`, `tests/voice/test_cli_timeline.py` | real subprocess CLI, JSON contract, approval-bound commands, full timeline flow |

## 2. Real media vs mocked output

- **Real synthetic media**: all footage fixtures are generated locally with FFmpeg (`lavfi` colour
  sources, `anullsrc`/`aevalsrc` audio, `libx264`/`aac` encoding). `media.analyze` runs real
  `silencedetect`/`scene`/audio-event detectors on them. FFprobe durations, WAV digests and mix
  outputs are measured, never fabricated.
- **Real speech**: `system_sapi` calls the installed Windows SAPI engine. Verified `mocked=false`,
  `duration_seconds=5.784`, `sample_rate=22050`, voices `Microsoft David Desktop` and
  `Microsoft Zira Desktop`.
- **Mocked by design**: `test_tone` writes a deterministic waveform and sets `mocked=true` in every
  result and history entry. It exists so versioning/CLI/approval paths can be tested without a
  speech engine. Mocked audio is rejected by QA and can never be published.
- **Neural models**: not executed. Adapters stay lazy and fail closed. See `VOICE_BENCHMARKS.md`.

## 3. Runs completed during implementation

| Phase | Command | Result |
|---|---|---|
| 11D | `python -m pytest tests/voice -q --no-cov` | `50 passed` (before 11E tests were added) |
| 11D | `python -m pytest tests/voice tests/security/test_audit_fixes.py -q --no-cov` | `57 passed, 1 skipped` |
| 11E | `python -m pytest tests/voice/test_script.py tests/voice/test_synthesis.py -q --no-cov` | `13 passed` |
| 11E | `python -m pytest tests/voice -q --no-cov` | `64 passed` |
| 11F–11I | `python -m pytest tests/voice/test_timeline.py -q --no-cov` | `8 passed` |
| 11F–11I | `python -m pytest tests/voice tests/security/test_audit_fixes.py -q --no-cov` | `79 passed, 1 skipped` |
| 11J | `python -m pytest tests/voice/test_cli_timeline.py -q --no-cov` | `1 passed` (full 16-command flow) |
| 11J | `python -m pytest tests/voice -q --no-cov` | `73 passed` |

### Full-gate stale-test findings (Phase 11J)

The first authoritative full-suite run after integration exposed three pre-existing tests that had
not been updated for the Phase 11A security fixes and the new `tests/voice/` directory. These were
**test defects, not product defects** — the source behaviour was the intended hardened behaviour:

| Failing test | Root cause | Fix |
|---|---|---|
| `tests/editing/test_graph.py::test_invalid_graphs` | MED-3 now confines music assets to workspace/registered roots; a missing asset raises `UnsafePathError` before the old `"music asset"` branch | Expect the path-confinement message; added a comment citing MED-3 |
| `tests/youtube/test_publish.py::test_publish_rejects_processing_state_directly` | HIGH-1 binds the approval HMAC to the full request, so a bare token no longer authorises publish | Create the token with `payload={"video_id": vid, "new_privacy": "public"}`; assert the `"still processing"` rejection |
| `tests/mcp/test_mcp_server.py::test_media_search_reads_index_from_fixed_workspace` | Function-local `from conftest import run_cli` resolved to the new `tests/voice/conftest.py` instead of the root conftest | Added `tests/voice/__init__.py` so the voice conftest is namespaced as `voice.conftest` |

All three pass in isolation and in the combined voice+MCP+CLI+release collection-order reproduction
(`107 passed`). No product source change was required.

Two defects were found and fixed during Phase 11F–11I test integration, both real code faults rather
than test-only issues:

1. `align_segments()` only read `profile_id` from the generation payload, but `generate_narration()`
   emits `voice_profile_id`. The missing fallback made every alignment resolve audio relative to the
   workspace root and fail with `segment seg_001 audio is missing`. Fixed by accepting both keys.
2. `build_voice_edl()` stored an unhelpful `"assessment": "review"` disclosure placeholder, so QA
   never saw the real synthetic-content reasons. Fixed by deriving the assessment from provider
   capability, segment provenance and the consent fingerprint at composition time. `creator_reviewed`
   stays `False` until the creator explicitly approves it.

## 4. Final acceptance demo

`python scripts/voice_acceptance_demo.py --json` completed all 16 mandated stages in order on a real
local SAPI voice (see `VOICE_BENCHMARKS.md §2` for the measured trace). Key evidence:

- registration → silent gameplay → grounded script (5 anchors) → HMAC approval consumed once;
- 2 real SAPI narration segments (`mocked=false`) → alignment (estimated boundaries) → 8 captions;
- base render → non-destructive voice mix (FFprobe `12.0s`, 2 duck windows);
- preview → `seg_001` regenerated v1→v2 with sibling unchanged → immutable EDL revision → re-mix;
- QA correctly returned `ok=false` (unreviewed disclosure + narration longer than the 12s render);
- source and base-render SHA-256 unchanged; consent revoked and profile deleted; hybrid sections
  classified from real signals with a `silence` action applied non-destructively.

The demo intentionally ends with `publication_ready=false`: safety gates are working, not failing.

## 5. Coverage and release gate

Final full-suite coverage and release-check evidence are recorded in `TEST_VERIFICATION.md` and
`RELEASE_VERIFICATION.md`. The historical 87% claim was false; the measured baseline was 80% and the
final number is reported without rounding up.

## 6. Reproduce

```powershell
python -m pytest tests/voice -q --no-cov
python -m pytest tests/voice tests/security/test_audit_fixes.py -q --no-cov
python scripts/voice_acceptance_demo.py --json
python -m pytest --cov=creator_core --cov-report=term-missing
```
