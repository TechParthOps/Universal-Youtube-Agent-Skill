# VoiceDNA — Voice Model Comparison & Selection Evidence

Status: research report for Phase 11B. Prepared 2026-11-21.
Sources were consulted on 2026-11-21; licence statements are attributed to the project owners below.
This is engineering diligence, **not legal advice**. Always read the exact licence of the specific
checkpoint you download before commercial use.

## 1. The trap: code licence ≠ weight licence ≠ output rights

A Python package marked MIT/Apache says nothing about the model weights it downloads, and nothing
about the rights to the voice used as a reference. Three separate questions must be answered:

1. **Code licence** — may I run/modify/redistribute the software?
2. **Weight licence** — may I use this specific checkpoint for my intended purpose?
3. **Voice rights** — does the creator own or authorise the reference voice, for this purpose?

VoiceDNA enforces all three at different layers: adapters declare a `weight_licence` and a
`commercial_ok` flag; consent is checked before a profile can generate speech; disclosure is
assessed before publishing.

## 2. Evidence table

| Model | Code licence | Weight licence | Commercial use | Zero-shot cloning | CPU | Notable controls |
|---|---|---|---|---|---|---|
| **OpenVoice v2** (MyShell) | MIT | MIT (V1 & V2, since Apr 2024) | **Yes, explicitly free** | Yes, 1–5 s reference | Yes (MeloTTS base is MIT and CPU real-time) | timbre vs style separated: emotion, accent, rhythm, pauses, intonation; 6 native languages |
| **Chatterbox Multilingual V3 / Turbo / Nano** (Resemble AI) | MIT | MIT | **Yes** (code stays MIT as an archive) | Yes, ~5–10 s reference | Nano: ~3× realtime on 8 CPU cores | exaggeration/CFG; paralinguistic tags; PerTh watermark on self-hosted output; 23+ languages |
| **F5-TTS** (SWivid) | MIT | **CC-BY-NC-4.0** (Emilia training data) | **No** for the published weights — even after fine-tuning | Yes, 5–15 s reference | Possible but heavy | high fidelity, limited explicit style control |
| **Fish Speech / Drama 3** (Fish Audio) | Fish Audio Community License v1.0 | Fish Audio Community License v1.0 (Drama 3); **Fish Audio Research License** for S2-Pro | Conditional: free only below **USD 500k annual revenue**, with restrictions; separate written licence otherwise; TTS-service use excluded | Yes | Varies | expressive, multi-character |
| **XTTS-v2** (Coqui) | Coqui TTS toolkit MPL-2.0 | **Coqui Public Model License 1.0.0** — non-commercial, and no commercial licence is obtainable (Coqui shut down Jan 2024) | **No** | Yes, ~6 s reference | Slow | 17 languages, no explicit style controls |
| **MeloTTS** (MyShell, base speaker for OpenVoice) | MIT | MIT | Yes | No (fixed speakers) | Yes, CPU real-time | language/accent selection |

### Source notes

- **OpenVoice:** the project README states "OpenVoice V1 and V2 are MIT Licensed. Free for both
  commercial and research use", and the V2 model card repeats "Free Commercial Use" (GitHub
  `myshell-ai/OpenVoice`, HF `myshell-ai/OpenVoiceV2`). MeloTTS, its documented base speaker,
  is MIT and CPU real-time (GitHub `myshell-ai/MeloTTS`).
- **Chatterbox:** GitHub `resemble-ai/chatterbox` is MIT; the project's Aug 2026 notice says it is
  "retired research and no longer maintained", the code stays public as an MIT archive. Multilingual
  V3 advertises 23+ languages and PerTh watermarking; the maintainers caution that Korean and
  Vietnamese are not production quality.
- **F5-TTS:** GitHub `SWivid/F5-TTS` README: "Our code is released under MIT License. The pre-trained
  models are licensed under the CC-BY-NC license due to the training data Emilia". The maintainer
  confirmed in issue #997 that an Emilia-trained base model "cannot be used commercially also after
  finetuning"; issue #709 confirms a from-scratch model on licensed data would be different.
- **Fish Audio:** HF `fishaudio/drama-3` Fish Audio Community License v1.0 (13 Oct 2026) grants
  commercial use only while annual revenue is below USD 500,000 and forbids cloning a real person
  without consent; `fishaudio/s2-pro` is under the Fish Audio Research License requiring a separate
  written commercial agreement.
- **XTTS-v2:** HF `coqui/XTTS-v2` ships CPML 1.0.0, a non-commercial licence that also restricts
  the model's *outputs*. Coqui Inc. closed in Jan 2024; community discussion #4304 confirms no
  commercial licence can now be purchased. The maintained toolkit fork (`idiap/coqui-ai-TTS`,
  `coqui-tts`) is MPL-2.0 but that does not relicense the CPML weights.

## 3. Model selection criteria and assessment

Scores are **documented qualitative assessments from the cited project documentation**, not
benchmarks measured in this environment. No model inference was run here (see §6).

| Criterion | OpenVoice v2 | Chatterbox V3/Nano | F5-TTS | Fish Drama 3 | XTTS-v2 |
|---|---|---|---|---|---|
| Voice similarity (reported) | high | high (V3 improved) | high | high | high |
| Naturalness (reported) | good | high | high | high | high |
| Word-level timestamps | **no** | **no** | no | no | no |
| Language support | 6 native + cross-lingual base | 23+ | zh/en | multilingual | 17 |
| Explicit emotion/style controls | **yes (core feature)** | partial (exaggeration/tags) | limited | multi-character | no |
| Inference speed (reported) | fast, CPU real-time base | Nano 3× realtime CPU | moderate | heavy MoE | moderate |
| CPU fallback | **yes** | **yes (Nano)** | possible | constrained | possible |
| Commercial weight licence | **MIT** | **MIT** | **No** | Conditional | **No** |
| Maintenance | stale since ~2025 | retired archive (2026) | active | active | toolkit fork active, weights frozen |
| Redistribution restrictions | none | none | CC BY-NC | revenue/consent clauses | CPML non-commercial |

## 4. Selected default: OpenVoice v2

Rationale, in priority order:

1. **Licence clarity.** Code, checkpoints and the documented MeloTTS base are all MIT with an
   explicit commercial-use statement. This is the only cloning stack in the table with an
   unambiguous, unrestricted commercial grant for both code and weights.
2. **Control surface maps to the product requirement.** OpenVoice separates timbre from style,
   which is exactly what "change the emotional delivery of one segment" needs without
   re-registering the voice.
3. **Hardware realism.** CPU real-time base synthesis keeps the feature usable without a GPU;
   GPU simply accelerates it.
4. **Language path.** Six native languages plus cross-lingual conversion covers a sensible first
   release, with honest rejection outside that set.

**Secondary adapter: Chatterbox (MIT)** — stronger reported similarity, built-in PerTh watermarking
and a CPU Nano checkpoint, but retired research with no support. Exposed as an opt-in adapter, not
the default.

**Explicitly gated adapters:** F5-TTS and XTTS-v2 are implemented as * licence-gated* adapters that
**refuse to run for commercial purposes** unless the creator supplies their own compliant weights
and records a licence acknowledgement. Fish Speech requires an explicit revenue/commercial
attestation. No adapter downloads model weights automatically.

**System TTS (`system_sapi`, Windows only)** is a real, local, no-download speech engine. It is
`clones_voice: false`: it proves the full pipeline with intelligible speech but does **not**
reproduce the creator's registered voice. It is a demonstration/fallback provider, never presented
as voice cloning.

`test_tone` is a deterministic offline WAV generator used exclusively in automated tests. It
emits labelled tone bursts, not speech, and can never be selected from the CLI for production
generation.

## 5. Controls that will NOT be simulated

Only controls an adapter advertises in `supported_controls()` are accepted. An unsupported control
is rejected with a structured validation error — the CLI never shows a control that silently does
nothing. In particular:

- `whisper` / `shouting` are only accepted from OpenVoice v2 (style controls) where the upstream
  model actually exposes them; from other providers they are rejected.
- No provider in this project provides native **word-level timestamps**. Alignment therefore
  reports sentence/segment timing as `verified` when derived from measured audio duration and
  word timing as `estimated` when interpolated. If a future adapter returns real word timings,
  they are promoted to `verified`. The UI never claims word-accurate timing that was interpolated.

## 6. Verification status (honest)

- No neural model inference was executed in this environment. No GPU model, model weights, or
  vendor API were available, and no benchmark was fabricated.
- Real local speech synthesis **was** verified with the Windows SAPI engine (`Microsoft David
  Desktop`, `Microsoft Zira Desktop`) and real FFmpeg 9.1 is installed; these are used only by the
  `system_sapi` and `test_tone` adapters.
- All neural adapters are lazy-imported, fail closed with exit code 3 when their optional extra is
  missing, and are covered by deterministic **mocked** provider tests. Mocked tests are labelled as
  such in `VOICE_TEST_REPORT.md` and are never reported as model inference.
- Before using any neural adapter commercially, the creator must run real inference, verify the
  checkpoint's licence file, and review output quality. `creator voice providers --json` prints
  the exact licence/commercial flags for the configured provider.
