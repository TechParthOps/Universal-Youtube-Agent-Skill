# DEVELOPMENT ROADMAP — Universal YouTube Creator Agent Skill

Status: Draft v1.0 (pre-Phase 1) · Milestone discipline: TEST_STRATEGY §4/§6 gate every phase.

## 0. MVP definition (smallest complete end-to-end slice)

**A gaming video, raw file → published-ready package, fully local:**

1. `creator profile init` with the bundled example profile.
2. `creator media inspect/analyze` a raw gameplay file (silence, scenes, audio events, optional
   transcript) → searchable media index.
3. `creator edit new --strategy gaming --concept "…"` → dead-air removal + highlight extraction
   → timeline v1 → render → QA report.
4. `creator edit preview` → review in browser; `creator edit revise` applies a natural-language
   change → v2 render.
5. `creator shorts generate` → one approved-worthy Short (9:16, captions, hook).
6. Publishing/analytics paths exercised against local stubs; live OAuth/upload lands in Phase 8.

**MVP exit criteria:** the six steps run green on a synthetic gaming fixture *and* on one real
user-provided file without crashing, with every claim in `PROGRESS.md` backed by a test.

## Phase plan

| Phase | Scope | Key acceptance demo |
|---|---|---|
| **P1 — Foundation** | Repo, packaging (`creator` CLI via pyproject), `doctor`, `--json` contract + exit codes, workspace `.creator/` layout, path/subprocess/audit security primitives, skill skeleton (`SKILL.md` + installer + 2 references), FFmpeg install guidance | `creator doctor` → 0; hostile-path suite green; skill validates |
| **P2 — Profiles & channel intelligence** | Creator profile manager (versioned, approval-gated memory), ChannelAnalyzer + connect flow (stub-first), example profile asset | `creator profile init/show/set`; channel snapshot from fixture |
| **P3 — Research & ideas** | History/competitor/trend research (quota-ledgered), IdeaGapFinder, OpportunityScorer (transparent weights), ContentPlanner, idea objects with all mandated fields, provenance labels | `creator ideas` returns scored, evidence-backed ideas from fixture data |
| **P4 — Media ingestion & understanding** | Ingest (ffprobe + hashes), transcription (faster-whisper extra), silence/scene/audio-event/moment detection, repeated-take clustering, media index + natural-language `media search` | `talk_60s`/`scenes_30s` fixtures: exact detection; search finds scripted moment |
| **P5 — Editing & rendering** | EDL schema v1, gaming/commentary/educational strategies, dead-air + filler + highlight ops, zoom/captions/music/ducking/transitions/aspect conversion, FFmpeg graph builder, QA engine, `creator edit render` | MVP steps 2–3 green; immutability + duration tests green |
| **P6 — Preview workspace & revisions** | Preview server + UI (player, timeline, A/B compare, revisions, exports), revision request parsing → EDL diffs, versioning | MVP steps 4 green; Range/206 tests; revision round-trip test |
| **P7 — Shorts** | Candidate ranking, hook generation (LLM optional), 9:16 focus reframe, animated captions, per-Short preview + titles, approval gate | MVP step 5 green on fixtures |
| **P8 — YouTube publishing** | Real OAuth (keyring), resumable uploads (private-first), processing verification, `publishAt` scheduling, publishing state machine + Shorts dependency, quota ledger, thumbnail upload | Stub-suite green + one optional live private upload (user-authorized) |
| **P9 — Analytics & learning** | Analytics API ingestion, baseline comparison, retention/CTR analysis, written performance reports, feedback into scorer/profile memory (explicit) | Fixture-based learning loop green; `creator analytics report` cites metrics |
| **P10 — Advanced & breadth** | Remaining categories (tutorial/documentary/vlog) hardened, speaker diarization (optional), multilingual captions/ASR, MCP adapter, packaging/release (sdist/wheel, versioned releases) | Per-feature criteria defined at phase start |
| **P11 — VoiceDNA** | Independent audit + security fixes (11A), architecture/licence research (11B), owner-scoped registration/consent/profiles (11C), lazy licence-gated providers + real SAPI (11D), footage-grounded scripts + approval-bound generation/regeneration (11E), honest alignment + additive EDL + captions (11F), non-destructive real FFmpeg mix with ducking (11G), preview/comparison/approval (11H), hybrid narration + QA + disclosure (11I), full CLI/skill integration + acceptance demo + rebuilt artifacts (11J) | `python scripts/voice_acceptance_demo.py --json` completes registration → silent footage → grounded script → approval → narration → alignment → captions → mix → render → preview → regeneration → revision → QA → untouched originals → consent revocation/deletion → hybrid; full coverage gate green |

Sequencing note: P1–P7 implement the complete local pipeline (no network required). P8–P9 add
the connected surface. P10 broadens. Any phase may be interrupted cleanly; the repo stays
consistent at each gate (all tests green, CHANGELOG/PROGRESS updated).

## Milestone gate procedure

1. Implement phase scope (no placeholder stubs behind "complete").
2. Run unit + integration + media + security suites.
3. Adversarial review (TEST_STRATEGY §6); fix or register findings.
4. Regression pass; update docs; assign status labels; demo the acceptance script.
5. Record evidence in `docs/DEVELOPMENT_LOG.md`; update RISK_REGISTER ratings.
