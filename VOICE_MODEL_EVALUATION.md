# Voice Model Evaluation — Stage 12C

**Status:** License review COMPLETE. Real neural inference **BLOCKED BY ENVIRONMENT**.
**Version under evaluation:** `0.10.0a0` (contract `1.0`) — unchanged.
**Review date:** 2026-11-22.
**Preceding research:** `VOICE_MODEL_COMPARISON.md` (2026-11-21), `VOICE_BENCHMARKS.md`.

> This document is engineering diligence, **not legal advice**. It records what was checked, what
> evidence was seen, and what was *not* executed. No neural quality figure is asserted anywhere.

---

## 1. Classification of this evaluation

| Item | Classification |
|---|---|
| Public licence terms for OpenVoice v2, Chatterbox, F5-TTS, XTTS-v2 | **Verified against primary sources** (project README / model card / licence text fetched 2026-11-22) |
| Specific checkpoint files that a creator may later download | **Not verified** — a licence grant for a project does not prove a particular file's terms |
| Neural inference in this repository | **NOT IMPLEMENTED** — all neural adapters fail closed (`LazyNeuralProvider.generate_speech()` raises unconditionally) |
| Real neural inference in this environment | **BLOCKED BY ENVIRONMENT** — no `torch`, `torchaudio`, upstream adapter runtime, or checkpoint present |
| Windows SAPI speech | **Real local TTS, not voice cloning** (`clones_voice=False`); available only on Windows |
| `test_tone` | **Mocked** deterministic tone bursts; gated behind `--allow-test-provider`; never publishable |
| MOS / similarity / latency benchmarks | **Not measured** — no figures are claimed; see `VOICE_BENCHMARKS.md` |

---

## 2. Primary-source licence verification (fetched 2026-11-22)

| Model | Source checked | Evidence observed | Commercial weights |
|---|---|---|---|
| **OpenVoice v2** (MyShell) | `raw.githubusercontent.com/myshell-ai/OpenVoice/main/README.md`; `huggingface.co/myshell-ai/OpenVoiceV2/raw/main/README.md` | README: *"OpenVoice V1 and V2 are MIT Licensed. Free for both commercial and research use."* HF model card front matter `license: mit` and *"Free Commercial Use. Starting from April 2024, both V2 and V1 are released under MIT License."* | **Yes — explicitly** |
| **Chatterbox** (Resemble AI) | `raw.githubusercontent.com/resemble-ai/chatterbox/master/README.md` | *"As of August 2026, Chatterbox is retired research and is no longer maintained… The code stays public as an archive under the MIT license, with no support, updates, or paid tier."* PerTh watermark on all self-hosted output. | **Yes (MIT archive), but unmaintained** |
| **F5-TTS** (SWivid) | `raw.githubusercontent.com/SWivid/F5-TTS/main/README.md` | *"Our code is released under MIT License. The pre-trained models are licensed under the CC-BY-NC license due to the training data Emilia."* | **No** for published weights |
| **XTTS-v2** (Coqui) | `huggingface.co/coqui/XTTS-v2/raw/main/LICENSE.txt` | *"This license allows only non-commercial use of a machine learning model and its outputs."* CPML 1.0.0; the maintained MPL-2.0 toolkit fork does not relicense these weights. | **No** — and outputs are restricted |
| **Fish Speech / Drama 3** | Source notes in `VOICE_MODEL_COMPARISON.md` (2026-11-21); not re-fetched today | Fish Audio Community License v1.0: conditional, revenue-thresholded; S2-Pro uses a Research License. | **Conditional** |
| **MeloTTS** (OpenVoice base) | Source notes in `VOICE_MODEL_COMPARISON.md` (2026-11-21) | MIT, CPU real-time; fixed speakers (no cloning by itself). | **Yes** |

This confirms the prior research in `VOICE_MODEL_COMPARISON.md` §2–3. The only unconditional
commercial cloning stack in the reviewed set is **OpenVoice v2 (MIT code + MIT weights)**. Chatterbox
is commercially usable but retired; F5-TTS and XTTS-v2 are non-commercial; Fish Speech is conditional.

**No model weights were downloaded, no upstream inference package was installed, and nothing was
bundled into the repository.** Licence review was a prerequisite for any such action, not a
substitute for checking the exact checkpoint the creator later chooses.

---

## 3. Selection decision

**Default neural candidate: OpenVoice v2.** Rationale (unchanged from `VOICE_MODEL_COMPARISON.md` §4):

1. Unambiguous MIT grant for both code and checkpoints, explicitly free for commercial use.
2. Timbre/style separation matches the product's per-segment delivery-control requirement.
3. CPU-real-time base keeps the feature usable without a GPU.
4. Six native languages plus a cross-lingual base, with honest rejection outside that set.

**No silent substitution** was made: F5-TTS, XTTS-v2 and Fish Speech remain licence-gated and
refuse commercial use. `system_sapi` remains an ordinary TTS fallback labelled `clones_voice=False`.

---

## 4. Why real inference was not executed here

1. **No inference implementation exists in the repository.** `LazyNeuralProvider.generate_speech()`
   (`src/creator_core/voice/providers/base.py`) raises `MissingDependencyError` unconditionally;
   `gated.py` raises `ValidationError`. Adding real inference is a feature implementation, not a
   verification step, and would require designing lazy model loading, checkpoint verification,
   consent re-checks, and provenance metadata before any audio could be honestly produced.
2. **No runtime is installed.** `importlib.util.find_spec("torch")`, `torchaudio`, `openvoice`, and
   `chatterbox` all returned `False`. Disk space is sufficient (E: 211 GB free, C: 139 GB free), so
   this is a deliberate environment boundary, not a hardware constraint.
3. **No licensed checkpoint is present** and none may be silently substituted. A creator must choose
   and verify the exact checkpoint themselves.
4. **Windows host.** OpenVoice's documented install path is Linux/conda; the Windows path is a
   community-maintained guide, which would add unverifiable platform risk to a release gate.

Therefore V-01 remains **Open — P0 gate unresolved** in `FINAL_REMEDIATION_AUDIT.md`.
Stage 12C does **not** claim the neural gate passes.

---

## 5. Reproducible manual harness for a licensed environment

Run this *outside* the release test suite, in an environment the creator has licensed:

```bash
# 1. Install the declared runtime extra (now present in pyproject.toml).
python -m pip install -e '.[voice-neural]'

# 2. Install OpenVoice v2 from its MIT source (no PyPI package is reliable).
git clone https://github.com/myshell-ai/OpenVoice.git
cd OpenVoice && python -m pip install -e . && cd ..
python -m unidic download

# 3. Download the OpenVoice V2 checkpoint yourself from the upstream link and verify its
#    LICENSE file states MIT. Never bypass this step for a commercial output.
#    Set CREATOR_OPENVOICE_CHECKPOINT to the extracted checkpoints_v2 directory.

# 4. Register a voice with explicit consent, then generate one short segment.
creator voice setup --owner <owner> --confirm-voice-ownership --approve
creator voice register --owner <owner> --display-name "..." --reference <clean-wav> \
  --purpose narration --provider openvoice_v2 --confirm-voice-ownership --approve
creator voice generate --owner <owner> --profile <id> --script "<short script>" \
  --approve <payload-bound-token> --provider openvoice_v2
```

Acceptance evidence the creator must record before any quality claim:

- `creator voice providers --json` shows `openvoice_v2` with `available=true`,
  `commercial_ok=true`, `weights_verified=true` (after real checkpoint verification).
- Generated WAV is non-silent, has the expected duration, and `mocked=false`.
- A listening test with at least one independent rater; MOS/similarity recorded as **measured**,
  with rater count and method — never copied from upstream marketing.
- The exact checkpoint SHA-256 and licence file archived with the result.

Until that evidence exists, `VOICE_BENCHMARKS.md`'s "not measured" entries remain authoritative.

---

## 6. Stage 12C code fixes applied (testable without neural weights)

| Defect | Fix | Verification |
|---|---|---|
| V-04 — pronunciation preferences rejected by every provider | `apply_pronunciation()` in `providers/base.py` applies creator preferences as deterministic word-bounded text substitutions before synthesis; `generate_segment()` no longer forwards them as an unsupported provider control. | `tests/voice/test_stage12c_fixes.py` UNIT + INTEGRATION |
| V-03 — revocation left stale profile `consent_status` | `profile.set_consent_status()` mirrors grant/revoke onto immutable profile versions; `update_profile()` cannot set it directly. | `tests/voice/test_stage12c_fixes.py` INTEGRATION |
| V-06 — `voice-neural` install hint referenced a non-existent extra | Extra declared in `pyproject.toml`; OpenVoice hint updated to state the source-install requirement. | `tests/voice/` regression suite; config inspection |
| C-04 / gated adapter — `SpeechResult` annotation undefined | Imported from `providers.base`; `type: ignore` removed. | `python -m ruff check` on changed files |

**Not fixed in Stage 12C (remain open):** V-01 (no inference), V-02 (no encryption at rest),
V-05 (measured word timing), V-07 (`test_tone` packaged). The consent/approval items listed here
were subsequently closed in Stage 12I: consent statements are mandatory and registration approvals
bind the final model, version and languages (`FINAL_SECURITY_AUDIT.md`).

---

## 7. Gate statement

| Gate | Status |
|---|---|
| Model licence review | **PASS** for public terms; specific checkpoint still creator-verified |
| Real neural inference | **BLOCKED BY ENVIRONMENT / NOT IMPLEMENTED** — P0 gate NOT passed |
| Neural quality benchmark | **NOT MEASURED** — no fabricated figures |
| Voice registration / consent / deletion | **Implemented and partially verified** (real SAPI optional) |
| Pronunciation preferences | **Fixed in this stage** — UNIT + INTEGRATION verified |
| Consent state consistency | **Fixed in this stage** — INTEGRATION verified |

**Conclusion:** Stage 12C does **not** clear the neural-voice P0 gate. The overall release decision
remains governed by `FINAL_RELEASE_VERIFICATION.md` and `RELEASE_READINESS_REPORT.md`.
