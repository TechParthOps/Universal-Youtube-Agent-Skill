# Audio Alignment Validation — Stage 12D

**Status:** Waveform-anchored alignment implemented and locally verified. **Measured word-level
timing is still not available** — V-05 is mitigated, not cleared.
**Version under test:** `0.10.0a0` (contract `1.0`) — unchanged.
**Date:** 2026-11-22.
**Host:** Python 3.11.9, FFmpeg/FFprobe 9.1. The reported Python 3.13.5 / FFmpeg 7.1.5 host was
not available locally (see environment gate below).

---

## 1. Classification of every claim in this document

| Claim / activity | Classification |
|---|---|
| FFmpeg `silencedetect` finds pauses in a synthetic WAV with two known 0.6 s gaps | **INTEGRATION / real local tool** — real subprocess, real waveform, no mocked signal |
| Speech/pause windows inverted from those pauses | **INTEGRATION** — derived from the measured pauses, not invented |
| Text allocated across measured windows by character weight | **UNIT** — pure function `_allocate_with_windows()` |
| `align_segments()` emits waveform-anchored timing metadata | **INTEGRATION** — real WAV + real FFmpeg + real JSON contract |
| Provider timestamp precedence and `"measured"` → `"verified"` normalization | **INTEGRATION with mocked provider metadata** — metadata is synthetic; measurement still real |
| Missing-FFmpeg fallback | **INTEGRATION / real failure path** — real `FileNotFoundError` from a nonexistent binary |
| Word/sentence boundary precision | **ESTIMATED** — character allocation inside measured windows |
| Forced alignment / ASR word timestamps | **NOT IMPLEMENTED** in this repository; external runtimes **BLOCKED BY ENVIRONMENT** |
| Frame-accurate lip sync | **NOT APPLICABLE** — explicitly not claimed anywhere |
| Reported Python 3.13.5 / FFmpeg 7.1.5 host | **BLOCKED BY ENVIRONMENT** — not reproduced here |
| MOS / similarity / neural quality | **NOT MEASURED** — see `VOICE_BENCHMARKS.md` |

No mocked output is presented as measured, and no estimated boundary is presented as precise sync.

---

## 2. What changed in Stage 12D

1. `src/creator_core/voice/alignment.py`
   - `measure_speech_windows()` runs `ffmpeg -af silencedetect=noise=-35dB:d=0.1` on the generated
     WAV, parses `silence_start`/`silence_end` events, merges overlaps, and inverts pauses into
     speech windows. Failures return `ok=false` with a redacted error and **zero** windows.
   - `_alignment_options()` bounds configuration (`-80…0 dB`, `0.02…5 s`, max 200 windows) so a
     bad config cannot turn alignment into an unbounded subprocess or output.
   - `_allocate_with_windows()` distributes text across the measured speech windows using
     cumulative character weight. Word/sentence entries remain `status: "estimated"` but now carry
     `anchor: "waveform_window"` and `window_status: "verified"`; they never cross a measured pause.
   - Provider timestamps still win and are labelled `verified`. Legacy `"measured"` status is
     normalized to `"verified"` (fixing the contract mismatch recorded in the Stage 12A audit).
   - `timing` now records `speech_window_source`, `boundary_method`, and the full `measurement`
     report; the payload has a `measurement` summary and updated `limitations`.
2. `src/creator_core/workspace/layout.py` — declared bounded defaults in
   `config.voice.alignment`.
3. `src/creator_core/cli/cmd_voice.py` — `voice align` now passes the loaded config through.
4. Docs — `VOICE_API_CONTRACT.md`, `SKILL.md`, and `workflow-voice.md` now describe the real
   labeling (`verified`/`estimated`) and the waveform-anchoring limitation.
5. `tests/voice/test_stage12d_alignment.py` — 5 regressions (1 UNIT, 4 INTEGRATION).

---

## 3. Measured evidence (real FFmpeg, synthetic WAV)

Fixture: 16 kHz mono WAV with the exact pattern
`0.6 s tone → 0.6 s silence → 0.8 s tone → 0.6 s silence → 0.5 s tone` (total 3.1 s).

| Measured item | Expected | Observed (tolerance ±0.15 s) |
|---|---|---|
| Speech window 1 | 0.0 – 0.6 s | within tolerance |
| Silence 1 | 0.6 – 1.2 s | detected |
| Speech window 2 | 1.2 – 2.0 s | within tolerance |
| Silence 2 | 2.0 – 2.6 s | detected |
| Speech window 3 | 2.6 – 3.1 s | within tolerance |

`measure_speech_windows()` returned `ok=true`, `label="verified"`, `method="ffmpeg silencedetect"`,
and three `status="verified"` windows. The test asserts the numbers rather than printing them, so
there is no hand-copied benchmark here.

Alignment integration test: the same WAV was attached to a segment starting at timeline 5.0 s.
Every emitted word timestamp stayed inside one of the shifted measured windows
(5.0–5.6, 6.2–7.0, 7.6–8.1 s), was labelled `estimated`, and carried `window_status="verified"`.
The payload reported `measurement.segments_with_speech_windows = 1`.

Failure test: pointing `config.ffmpeg.path` at a nonexistent binary produced
`measurement.ok=false`, an error string, `windows=[]`, `speech_window_source="estimated"`, and a
plain character-count fallback — i.e. no fabricated windows.

---

## 4. Test results

| Command | Classification | Result |
|---|---|---|
| `python -m pytest tests/voice/test_stage12d_alignment.py` | UNIT + INTEGRATION | **5 passed** |
| `python -m pytest tests/voice/ tests/test_workspace.py` | UNIT + INTEGRATION (includes real FFmpeg fixtures) | **88 passed in 73.99 s** |
| `python -m ruff check src/creator_core/voice/alignment.py src/creator_core/cli/cmd_voice.py src/creator_core/workspace/layout.py tests/voice/test_stage12d_alignment.py` | static analysis | **All checks passed!** |

The post-Stage-12C full suite (before Stage 12D edits) was independently measured as
`546 passed, 3 skipped in 334.75s`, `TOTAL 8257 1465 82%`
(raw: `%TEMP%\stage12c_full.txt`). A final full-suite run after all
Stage 12 stages is recorded in `FINAL_TEST_VERIFICATION.md`.

---

## 5. Honest limitations

- `silencedetect` is a **level threshold**, not speech recognition. It finds pauses; it cannot
  identify words, phonemes, or speakers. Word boundaries remain character-count estimates, and
  this document never calls them measured.
- No frame-accurate synchrony is asserted. `validate_av_sync()` compares rendered duration with the
  narration timeline end and is explicitly a tolerance check.
- `fit` of text to windows is a heuristic (character weight). Punctuation, proper nouns, and
  creator pronunciation substitutions can shift perceived boundaries; `voice qa` still sends A/V
  synchrony to human approval.
- The detected windows depend on the WAV's level and the configured threshold; the values are
  recorded in every `measurement` report so results are reproducible and auditable.
- Provider-supplied word timestamps would be higher quality, but no bundled adapter emits them.
  Real forced alignment would require an ASR/alignment runtime and a licensed model; neither is
  present. `faster_whisper`, `whisper`, and `torch` were absent. `vosk` is importable but no model
  was installed or licence-reviewed, so it was deliberately **not** used.

---

## 6. Gate status

| Gate | Status | Evidence / reason |
|---|---|---|
| Real waveform measurement of speech/pause windows | **PASS** | `test_silencedetect_measures_real_waveform_windows` — real FFmpeg on known gaps |
| Word boundaries never cross a measured pause | **PASS** | `test_align_segments_anchors_text_inside_measured_windows` |
| Provider timestamp precedence + contract labels | **PASS** | `test_provider_timestamps_take_precedence_and_are_normalized` |
| Failure never fabricates windows | **PASS** | `test_waveform_failure_falls_back_without_fabricating_windows` |
| Measured word-level timing / forced alignment | **FAIL** (not implemented) — external path **BLOCKED BY ENVIRONMENT** | No word-timestamp provider, no licensed alignment runtime/model present |
| Frame-accurate lip sync | **NOT APPLICABLE** | Never claimed; `validate_av_sync` is tolerance-only |
| Reported Python 3.13.5 / FFmpeg 7.1.5 host | **BLOCKED BY ENVIRONMENT** | Not available locally; manual reproduction procedure below |

**V-05 verdict:** downgraded from "no measurement at all" to **MITIGATED — P0 gate still FAIL**.
The product now has real waveform evidence for speech placement, but it still cannot produce
measured word timings, so the release gate cannot be marked PASS.

### Manual reproduction on the reported host

```bash
python -m pytest tests/voice/test_stage12d_alignment.py -q
python -m pytest tests/voice/ tests/test_workspace.py -q
```

Expected: 5 passed, then 88 passed. If FFmpeg is absent or old, the first two INTEGRATION tests
must fail visibly rather than being skipped — that is the intended fail-closed behaviour, not a
reason to relax the assertions.
