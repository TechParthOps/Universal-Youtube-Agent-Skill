# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/) (0.x while pre-1.0).

## [0.10.0a0] — Public Alpha (2026-11-23)

**Status: PUBLIC ALPHA — not production-stable.**

The alpha prioritizes the local-first creator workflow (footage analysis → edit → render →
preview → revise → local export) with YouTube account access optional. Research, ideas, trends
and competitor analysis are **offline**, driven by snapshot/fixture files you supply or that
ship in `examples/`. Neural voice cloning, measured word alignment, live YouTube publishing, and
full cross-agent compatibility are **experimental or blocked** and are not advertised as verified.
Full capability matrix: [`ALPHA_RELEASE_NOTES.md`](ALPHA_RELEASE_NOTES.md).

### Added
- Public alpha release notes with an explicit capability matrix separating implemented-and-tested,
  partially-tested, experimental, blocked, and owner-required capabilities.
- `THIRD-PARTY-NOTICES.md` — runtime/optional dependency licences, neural voice model-licence
  caveats, external-program notices (FFmpeg), bundled-asset inventory, and data-source disclaimers.

### Fixed
- **Cross-platform artifact-manifest containment (P0):** replaced the hard-coded Windows
  string-prefix containment check with platform-independent `Path.is_relative_to()` component
  comparison after symlink-resolving `Path.resolve()`. Valid artifacts now verify on POSIX as well
  as Windows; similar-prefix siblings (`/models-safe` vs `/models`) and cross-drive paths are
  rejected. Tests: `tests/voice/test_artifact_manifest.py` (31 passed, 3 env-skipped on Windows;
  32 passed, 2 skipped on WSL Ubuntu 24.04).
- **Approval-ledger lock race (P1):** `_nonce_lock` re-raised `FileExistsError` when the lock
  holder unlinked the lock between the failed `O_EXCL` open and the `lock.exists()` check, which
  crashed racing verifiers. Contention is now always retried. Tests:
  `tests/security/test_audit_fixes.py::test_nonce_lock_retries_when_holder_releases_before_stat`.
- **Quota-ledger ownership consolidation (P1):** `youtube verify`, `youtube publish` and
  `youtube thumbnail` reserved quota at the orchestration layer *and* called the transport
  without a ledger, which broke live mode and split accounting ownership. The transport is now
  the single accounting owner for `channels.list`, `videos.list`, `videos.update` and
  `thumbnails.set` (it raises when no ledger is supplied, so no live request can bypass
  accounting); orchestration reserves only the complex resumable `videos.insert` flow, with
  `mark_sent` emitted exactly once at the transport boundary. Stub `update_video` now propagates
  the ledger to its inner `get_video`, matching live behavior. See `FINAL_QUOTA_ACCEPTANCE.md`.
- **Packaging inventory (P1):** the release inventory now also includes required runtime-referenced
  documentation (`VOICE_ARTIFACT_MANIFEST_SCHEMA.md`) and fails closed if it is missing from disk or
  the archive; `MANIFEST.in` includes it in the sdist.

### Verified (executed on this repository, current source)
- Full Windows regression: `python -m pytest -o addopts="" -q` => **653 passed, 6 skipped**
  (skips are Windows symlink-privilege; escaped-path cases are covered by NTFS junction tests).
- POSIX (WSL2 Ubuntu 24.04, Python 3.12.3): voice suite **135 passed, 5 skipped**.
- `ruff check src tests scripts` and `ruff format --check` clean (261 files).
- `bandit -r src`: **0 High / 1 Medium (torch.load, weights_only-gated) / 12 Low**.
- `mypy src`: 191 findings (pre-existing; no mypy gate).
- `twine check` PASSED (wheel + sdist); clean-source ZIP scan: 0 secrets, 0 machine paths.
- Fresh out-of-repo venv install from the clean-source ZIP: `creator 0.10.0a0`,
  `creator doctor --json` ok, `creator_core` imported from installed site-packages.
- Non-neural media workflow executed end-to-end (real FFmpeg render, FFprobe-verified duration,
  revision, re-render, exports).
- OpenCode 1.19.5 skill discovery + invocation verified end-to-end.

### Experimental / blocked (NOT advertised as verified)
- **V-01 real neural OpenVoice inference** — no runtime, no verified checkpoints/manifest, no
  authorized voice sample. Only non-neural providers (`system_sapi`, `test_tone`) are available.
- **V-05 measured WhisperX word alignment** — no WhisperX runtime or annotated permissioned
  dataset. Alignment is always labelled `estimated`.
- **Live YouTube publishing** — not verified against a real account; OAuth is optional and no
  live upload was performed.
- **Cross-agent compatibility** — only OpenCode verified end-to-end; Codex skill-loading
  verified but its sandbox Python lacks `creator_core`; Claude Code not authenticated;
  Cursor and Windsurf not installed.


### Not implemented in this alpha (do not expect these)
- **Live internet research.** `src/creator_core/research/*` uses no HTTP client; research, trend,
  competitor and idea-discovery commands are offline and snapshot/fixture-driven.
- **Third-party public-channel retrieval.** The live transport's `channels.list` uses
  `mine=True`, so only your own authenticated channel is reachable. `channel connect --from-file
  FILE` reads a local snapshot. There is no channel-URL lookup.
- **Thumbnail generation.** Nothing creates an image. `youtube thumbnail --image FILE` uploads an
  image you supply (≤ 2 MB). No generation, template, text overlay, size/aspect
  normalization, preview, or revision.

## [Unreleased]

_Historical entries below record earlier closure passes. Their test counts were correct for the
source state at the time they were written and are retained as historical evidence._

### Independent-review remediation — real implementation fixes (2026-11-23)

#### Fixed (independently verified; all with executable tests)
- **P0-1 VoiceDNA artifact verification:** new `src/creator_core/voice/artifact_manifest.py` — a strict, versioned (schema 1.0), fail-closed validator that re-verifies every required artifact from disk (existence, actual SHA-256, size, resolvable license evidence within the model root, enum validity, unique ids, dependencies, base-speaker), with path confinement (rejects absolute paths, `..`, symlink escapes). Commercial clearance is computed by the verifier and never trusts a caller boolean. Replaced the weak `_load_licence_manifest()` that trusted `commercial_use_verified` + a nonempty license string. OpenVoice re-verifies artifacts immediately before `torch.load` and prefers `weights_only=True`. Tests: `tests/voice/test_artifact_manifest.py` (21 passed, 1 env-skipped symlink test).
- **P0-2 Alignment transcript coverage:** new `src/creator_core/voice/aligners/coverage.py` — token-normalizing validator that compares aligned words to the input transcript (missing/duplicate/unexpected/unordered/out-of-duration/overlap) and computes coverage separately from timestamp validity. `WhisperXAligner` now classifies a result `measured` only when the FULL transcript is covered with valid ordered timestamps; partial results are returned as partial and downstream `align_segments` refuses to label them measured. Tests: `tests/voice/test_aligner_coverage.py` (18 passed).
- **P0-3 Clean-source ZIP completeness:** `scripts/pack_release.py` now builds the release from a filesystem-verified inventory (tracked files ∪ all `.py` on disk under `src/scripts/tests`), so untracked-but-required source — the previously-omitted `src/creator_core/voice/aligners/*` — is included; it detects missing required source and untracked-but-required files and **fails closed** if a required file is omitted. Tests: `tests/release/test_pack_inventory.py` (5 passed).
- **P1-4 Quota enforcement at the transport boundary:** `GoogleApiTransport` live operations (`channels.list`, `videos.list`, `videos.update`, `thumbnails.set`, `videos.insert`) now **require** a quota ledger and refuse to execute (raise) if it is missing, instead of logging a bypass and sending anyway. `youtube.publish` passes its ledger/reservation into `insert_video` (mark-sent at the transport boundary). Analytics remains a separate quota system (not charged as Data API units). Tests: new `test_live_refuses_without_quota_ledger`; youtube suite 80 passed.
- **Machine-path scrub (F-03) preserved**; the Owner username / machine absolute paths remain redacted from shipped docs.

#### Verified
- Full regression after the final source change: `python -m pytest -o addopts="" -q` => **642 passed, 4 skipped** (skips are Windows symlink-privilege).
- `ruff check src tests scripts` and `ruff format --check` clean (193 files).
- `bandit -r src`: **0 High / 1 Medium (torch.load, weights_only-gated) / 12 Low**. `twine check` PASSED (after rebuild).
- Clean-source ZIP now contains the alignment modules; single authoritative `dist/SHA256SUMS.txt`; duplicate `SHA256SUMS_RELEASE.txt` remains removed.

#### Still blocked by owner/environment prerequisites
- Real OpenVoice neural inference (V-01): no torch/openvoice runtime, no owner-verified manifest/checkpoints, no authorized voice sample.
- Measured WhisperX alignment benchmark (V-05): no whisperx runtime or annotated dataset.
- Live YouTube private upload: no OAuth secrets / test channel / approval.
- Agent runtime discovery: OpenCode/Codex/Claude Code/Cursor/Windsurf not installed here.

### Final blocker closure — independent verification and real fixes (2026-11-22)

#### Fixed (independently verified)
- Preview server (F-01): oversized POST bodies no longer abort the TCP connection; the request is drained and a clean HTTP 400 is returned. Fixes `tests/preview/test_preview_server.py::test_host_binding_and_body_limit` (previously failed with `WinError 10053`). Full suite is now genuinely **597 passed, 3 skipped** (3 skips are Windows symlink-privilege, explained).
- Research idea ids (F-02): SHA-1 marked `usedforsecurity=False`, resolving Bandit B324 (High). Digest value unchanged.
- PII scrub (F-03): owner username and machine-specific absolute paths removed from 8 tracked docs (released as `%TEMP%` / `%HOME%` / `<checkout-root>`).
- Security/dependency/packaging tools installed in the release environment are now actually executed and reported: `bandit` (0 High / 1 Medium / 12 Low), `mypy` (181 pre-existing, documented), `pip-audit` (146 vulns in 13 whole-env packages; closure-relevant subset triaged), `twine check` PASSED (wheel + sdist). The prior report's "scanner unavailable" claims were incorrect and are corrected.
- Packaging: one authoritative `dist/SHA256SUMS.txt` (wheel + sdist + clean-source ZIP); duplicate `dist/SHA256SUMS_RELEASE.txt` removed; new deterministic `scripts/pack_release.py` ships itself for reproducibility; machine-path/secret scan is 0 fatal.

#### Still blocked by owner/environment prerequisites
- VoiceDNA real neural inference: no torch/openvoice runtime, no verified artifact manifest, no authorized voice sample/consent.
- Measured WhisperX alignment benchmark: no whisperx runtime or annotated permissioned dataset.
- Live YouTube private upload: no OAuth client secrets / test channel / approval.
- Cross-agent runtime discovery: OpenCode/Codex/Claude Code/Cursor/Windsurf not installed here.

Classification: **NOT READY — OWNER ACTION REQUIRED**.

### Phase 13.2 final release closure (local, 2026-11-22)

#### Verified locally
- Full regression: `python -m pytest -o addopts="" -q` => **597 passed, 3 skipped**.
- Quality: `ruff check` PASS; `ruff format --check` PASS.
- Packaging: `python -m build` PASS; clean source ZIP + SHA256 manifest generated; fresh venv wheel install + `creator --version` smoke PASS.
- YouTube quota (local): sent-failed requests remain consumed; reserve/settle/fail/uncertain/reconcile regressions pass (youtube suite 77 passed).

#### Not verified / blocked
- V-01 real neural OpenVoice inference: **BLOCKED BY ENVIRONMENT** (no runtime/checkpoints/authorized sample/manifest verification).
- V-05 real measured WhisperX word-level alignment benchmark: **BLOCKED BY ENVIRONMENT** (no runtime/models/annotated dataset).
- Live YouTube private acceptance: **BLOCKED BY ENVIRONMENT**.
- Agent runtime compatibility: **BLOCKED BY ENVIRONMENT**.
- Security/dependency scanners (mypy/bandit/pip-audit/twine): previously recorded here as "unavailable"; **CORRECTED in the Final blocker closure entry above — they are installed in the release environment and were executed (bandit/mypy/pip-audit/twine all ran; results recorded).**

#### Decision
**NOT READY — OWNER ACTION REQUIRED**. See `FINAL_RELEASE_ACCEPTANCE.md`.

## [Unreleased] — Phase 11: VoiceDNA (0.10.0a0, contract 1.0, no release-status change)

### Added
- **11A audit**: `POST_RELEASE_AUDIT.md`, `SECURITY_AUDIT.md`; fixed HIGH-1 (approval HMAC now binds
  the full request payload and is one-time), HIGH-2 (Short id path confinement), HIGH-3 (release
  checker detects stale shipped artifacts), MED-1..MED-4 (preview host/origin, analytics paths,
  music asset paths, installer uninstall); 27 regression tests in `tests/security/test_audit_fixes.py`.
- **11B design**: `VOICEDNA_ARCHITECTURE.md`, `VOICE_MODEL_COMPARISON.md`, `VOICE_SECURITY.md`,
  `VOICE_CONSENT_POLICY.md`, `VOICE_API_CONTRACT.md`.
- **11C registration**: owner-scoped `.creator/voice/<owner>/` storage, reference assessment and
  single-copy storage, versioned immutable profiles, explicit purpose-scoped revocable consent,
  onboarding, deletion and registration tests.
- **11D providers**: lazy capability-tagged registry with licence/tests-only/external gates, real
  Windows SAPI adapter (`clones_voice: false`), deterministic `test_tone` adapter, consent checks,
  and the `creator voice` command tree.
- **11E script-to-voice**: footage-grounded script builder (rejects reports without measured
  signals, sanitises untrusted concept/transcript text), approval-bound `voice generate`, consent
  checked inside generation, immutable segment versions/history and `voice regenerate`.
- **11F–11I timeline**: honest alignment (measured WAV duration, estimated boundaries), additive
  EDL `voice` object plus narration captions, real FFmpeg mix with narration delay and ducking
  automation, hybrid section classification/actions, preview with per-segment candidates and
  immutable approval, objective QA and synthetic-content disclosure assessment.
- **11J integration**: `creator voice align|compose|mix|preview|qa|hybrid|disclosure`, the full
  `scripts/voice_acceptance_demo.py` acceptance flow, `VOICE_TEST_REPORT.md`,
  `VOICE_BENCHMARKS.md`, CLI/skill-reference updates and rebuilt local release artifacts.

### Fixed
- `align_segments()` now accepts `voice_profile_id` from the generation payload (previously every
  alignment failed to resolve audio because only `profile_id` was read).
- `build_voice_edl()` now derives a real disclosure assessment from provider capability, segment
  provenance and consent metadata instead of storing an unhelpful `"review"` placeholder.
- Corrected the false "87% coverage" claim in the 0.10.0a0 changelog entry, README, PROGRESS.md,
  TEST_STRATEGY.md and DEVELOPMENT_LOG.md.
- Updated three stale tests caught by the final full gate: the music-asset path-confinement message
  (MED-3), the request-bound publish approval token (HIGH-1), and a `conftest` name collision from
  the new `tests/voice/` package (fixed with `tests/voice/__init__.py`). Product behaviour was
  correct; only the tests needed updating.

### Added (pre-Phase-11 planning)
- Planning & architecture documentation set: `ARCHITECTURE.md`,
  `DEVELOPMENT_ROADMAP.md`, `TECHNICAL_DECISIONS.md`, `RISK_REGISTER.md`,
  `TEST_STRATEGY.md`, `PROGRESS.md`.
- `CHANGELOG.md`, `LICENSE` (MIT), and development log scaffold.
- Environment survey results (Python 3.11.9, Git 2.55, Node 24, winget available;
  FFmpeg/FFprobe **absent** — flagged as R-01 with remediation plan).

### Notes
- Architecture approved 2026-11-21; FFmpeg 9.1 installed via winget (R-01 closed).

## [0.10.0a0] — 2026-11-21 — Phase 10: Advanced & breadth

### Added
- Editing breadth: `tutorial`, `documentary` and `vlog` strategies join the declarative catalog.
- Multilingual captions: deterministic SRT/WebVTT export, a fixed language catalog with RTL
  metadata, provenance-aware language detection, an honest no-translation default, offline
  file translations and a guarded optional HTTP translation provider (`creator captions`).
- Optional diarization: pause-based speaker *turns* only (never identities), an honest `none`
  default and a lazy, fails-closed vendor adapter (`creator media diarize`).
- Optional read-only MCP adapter: JSON-RPC 2.0 over stdio with eight curated tools, strict
  argument validation, fixed workspace scope and recursive secret redaction (`creator mcp`).
- Packaging/release: `MANIFEST.in`, a `build` extra, a release checker, a source SHA-256
  manifest and a real local sdist/wheel build script with artifact hashes (`creator release`).
  Nothing is ever uploaded or published.

### Tests
- 98 new tests. Full gate: **450 passed, 2 skipped** (352 prior + 98 new). The historical
  "87% total coverage" figure recorded here was **incorrect**: independent re-measurement at
  `bf937bdf` produced `TOTAL 6276 1254 80%` (`POST_RELEASE_AUDIT.md §1`). Corrected during the
  Phase 11 audit; the final measured coverage is recorded in `TEST_VERIFICATION.md`. Not every
  new core module cleared 80% at that point (`editing/revisions.py` 78%, `youtube/publish.py` 72%,
  `security/approval.py` 46% before new regression tests). A real local sdist/wheel build was
  verified with a SHA-256 manifest; nothing was uploaded.

### Notes
- All new optional integrations fail closed and never fabricate model output or claim virality.

## [0.9.0a0] — 2026-11-21 — Phase 9: Analytics & performance learning

### Added
- `creator analytics refresh|report|learn|status` behind the shared `--json`/exit-code contract.
  Ingestion is **fixture-first**: the bundled `examples/analytics_fixture.json` is used offline by
  default, `--from-file` imports any API-shaped or simplified JSON report, and `--live` (or config
  `analytics.transport = "live"`) opts into the official YouTube Analytics API. Live results are
  labelled `verified: true` only when the source is `youtube_analytics_api`; fixtures are always
  `estimated`.
- `analytics/ingest.py` normalizes both API-shaped `reports[].data.rows[]` payloads (with
  `columnHeaders`) and simplified `{videos: [...]}` fixtures. It handles fractional/percent CTR,
  aliases (e.g. `viewCount`, `impressionsClickThroughRate`), tag strings, and rejects NaN/Inf.
  Derived metrics (engagement rate, watch time per view) and median baselines are computed locally.
  Snapshots are written versioned under `.creator/analytics/snapshots/`.
- `analytics/report.py` builds evidence-backed reports that **cite the metric value, sample median,
  sample size, source and provenance** for every finding, compare against an earlier snapshot or
  the profile baseline, and render as markdown. All interpretations are labelled `hypothesis` with
  explicit association-only wording; no report claims causation, prediction, virality or revenue.
- `analytics/learning.py` derives deterministic, bounded scorer-weight proposals from measured
  findings. Every proposal is a `hypothesis` with its evidence attached. Staging writes to profile
  memory and has **no effect**; applying requires a fresh action+target-bound approval token and
  writes `research/scoring_config.json`, which `creator ideas` already loads. Repeated `learn`
  calls reuse the pending change instead of accumulating orphans.
- Analytics-aware guards: audience-fit is never penalized when tag data is absent (the Analytics
  API does not return tags), volatility is based on genuine per-video dispersion rather than report
  extremes, and live mode never silently falls back to the fixture.

### Security / adversarial review (M9)
- No claim is promoted to `verified` from fixture data; live mode fails closed (exit 3/4) when
  credentials or optional Google libraries are missing. No live API call is made in CI.
- Learning is inert until explicit approval; forged tokens are rejected with `approval_invalid`
  (exit 5), and only fingerprints are audit-logged.
- New analytics core modules are above the 80% coverage floor (ingest 99%, learning 96%, report
  100%, transport 100%; aggregate 99%). CLI handlers are child-process tested.
- Full gate: **352 passed, 2 skipped, 88% total coverage** (284 prior + 68 new tests). Analytics
  core modules aggregate 99%; the total dipped from 89% because `cli/cmd_analytics.py` is
  child-process tested and not traced in-process (same caveat as other CLI handlers).

## [0.8.0a0] — 2026-11-21 — Phase 8: YouTube publishing

### Added
- `creator youtube connect|status|quota|upload|verify|publish|thumbnail` behind the shared
  `--json`/exit-code contract. The transport is **stub-first**: every command is offline unless
  `--live` (or config `youtube.transport = "live"`) is explicitly requested and OAuth credentials
  are present. Stub results are labelled `live: false`; nothing claims a real upload happened.
- `YouTubeTransport` interface with a deterministic offline `StubTransport` and an optional
  `GoogleApiTransport` using the official `google-api-python-client`/`google-auth-oauthlib` behind
  the `[youtube]` extra. Missing optional dependencies raise a clear `missing_dependency` (exit 3)
  with an install hint. Live APIs are not exercised against a real account in CI.
- Resumable uploads with interruption recovery: the stub can simulate a mid-stream drop via
  `youtube.stub_fail_after_bytes`; the orchestrator retries from the reported offset (max 3
  attempts) and audit-logs each resume. The live adapter wraps `next_chunk()` network failures.
- **Private-first** publishing: `upload` creates a private (or `publishAt`-scheduled private)
  video and leaves it in `processing`; `verify` polls status and advances to `private`/`scheduled`;
  only a separate `youtube publish --approve` makes it public. `--privacy public` is rejected at
  upload time with a pointer to the publish step. Scheduling uses documented `publishAt` semantics.
- Action+target+expiry-bound HMAC approval for upload, publish and thumbnail changes. Missing
  tokens exit 5 (`approval_required`); forged/expired/wrong-target tokens exit 5
  (`approval_invalid`). Only 12-char fingerprints are persisted or audit-logged.
- Shorts parent gate re-checked immediately before **both** upload and publish: an approved Short
  (`creator shorts review --approve`) still requires `--parent` and a parent video in state
  `public`. A demoted/missing parent blocks release without mutating state.
- Explicit publishing state machine (`draft → approved → uploading → processing → private →
  public`; invalid transitions are rejected and every transition is audit-logged).
- UTC quota ledger (`<workspace>/publishing/quota.json`) with documented Data API v3 costs
  (`videos.insert` 1600, `videos.update`/`thumbnails.set` 50, list calls 1), pre-flight checks,
  per-operation charges, a configurable daily ceiling and automatic UTC-day reset. Exhaustion is
  a `provider_error` (exit 7) before any API call.
- Thumbnail updates via `thumbnails.set` with their own approval, 2 MB limit and quota charge.

### Security / adversarial review (M8)
- No token values appear in JSON output, stderr, state files or the audit log (grep-tested).
- Stub and live results are explicitly distinguished so a stub run can never be mistaken for a
  live publication. Live mode requires an explicit opt-in and stored OAuth credentials.
- Private-first plus the parent gate means no code path can publish a Short before its parent is
  public, and no upload can create a public video directly.
- Every new Phase 8 core module is above the 80% coverage floor (quota 100%, state 95%, transport
  95%, publish 91%); the live adapter is exercised with injected fake Google modules.
- Full gate: **284 passed, 2 skipped, 89% total coverage** (226 prior + 58 new tests).

## [0.7.0a0] — 2026-11-21 — Phase 7: Shorts generation

### Added
- `creator shorts generate <source> [--project ID] [--target S] [--limit N] [--focus auto|center]
  [--transcribe] [--no-provider]`: ranks candidate windows from real `media analyze` signals and
  renders each into a 1080×1920 H.264/AAC Short draft. Source files are never modified; each
  draft writes `timeline.json`, `manifest.json` and a QA report with a reproducible FFmpeg arg
  list. No YouTube API is called.
- Deterministic candidate ranking with transparent weighted heuristics (`audio_energy`,
  `visual_change`, `narrative_completeness`, `creator_relevance`, `duration_fit`, minus detected
  silence). Scores are explicitly `estimated` and documented as **not** predictions of views or
  virality; windows without detector signal are labelled `hypothesis` uniform suggestions.
- 9:16 reframe with measured motion-centroid crop tracking: low-resolution grayscale frames are
  decoded with real FFmpeg, a linear focus trajectory is fitted, and an animated `crop` follows
  the subject. Static/no-motion sources use a centered foreground over a blurred expanded
  background (`blur_pad`) — never a naive center crop. `--focus center` forces blur-pad.
- Burned animated ASS captions from transcript segments when available; otherwise from the
  generated hook with the source explicitly recorded as template (no fabricated transcript).
  All caption text passes through the injection sanitizer and length bounds.
- Template hook/title/description generator always available; optional OpenAI-compatible,
  Anthropic, Gemini or Ollama text providers degrade to templates with an explicit
  `fallback_reason`. A fallback never claims a model ran (`model: null`, `provider: template`).
- `creator shorts review <project>` lists drafts; `creator shorts review <project> --short
  short_001 --approve` (or `--approval-token TOKEN`) records explicit creator approval with an
  HMAC token (fingerprint only in the audit log). Missing/invalid tokens exit 5
  (`approval_required`/`approval_invalid`). Approved timelines set a publish gate requiring
  creator approval, a published parent video and a fresh upload token — Phase 8 re-checks it.
- `portrait_needed` media fixture (moving bright region via `geq`) for focus-tracking tests.

### Security / adversarial review (M7)
- Hook copy, reasons and profile voice are treated as untrusted data: sanitized before ASS, JSON
  or optional provider prompts; instruction-shaped text is flagged, never executed.
- Approval tokens are action+target+expiry bound HMACs; stale, forged and wrong-target tokens are
  rejected with a stable exit code and a safe hint. Audit stores only 12-char fingerprints.
- Output paths are exclusively allocated inside the project's `shorts/` tree; failed renders are
  removed; source hashes are verified before render and copied into the draft timeline.

### Fixed
- Provider hook lines are sanitized without pre-truncation so the `truncated` flag is accurate when
  an optional model returns copy longer than 140 characters (previously the flag never fired).

## [0.6.0a0] — 2026-11-21 — Phase 6: Preview workspace & revision history

### Added
- `creator edit revise|revisions|exports|preview` commands. Revision requests are parsed into
  explicit, reviewable EDL ops; ambiguous or unsupported requests return questions and create no
  version. Applying a plan derives an immutable `timeline_vNNN.json` child.
- Loopback-only preview workspace (`creator edit preview`) with a stdlib HTTP server and vanilla
  JS UI: player, removed-footage timeline lanes, caption/marker tracks, A/B compare, QA report,
  editing decisions, pending revisions, exports and render trigger.
- HTTP Range/206 streaming for versioned renders and original sources; loopback bind is enforced
  and static assets are allowlisted.
- Pending revision queue shared by the preview UI and CLI (`--queue`, `--pending`), with applied
  requests retained in the audit trail.

### Fixed
- Adversarial review M6: preview server rejects non-loopback hosts, caps POST bodies, sanitizes
  revision text before storage/echo, allowlists endpoints/static files, resolves project files
  inside the project directory, and never leaks tracebacks to the browser.
- Revision ids are strict-pattern validated and pending/applied revision reads are path-safe.
- Structured revision errors include actionable hints; a missing analysis report yields
  `needs_clarification` instead of inventing silence ranges.

## [0.5.0a0] — 2026-11-21 — Phase 5: Intelligent editing & rendering

### Added
- `creator edit new|render|versions|status|show` commands. Every version is an immutable EDL JSON
  file (`timeline_vNNN.json`) with source SHA-256 hashes, decision provenance and a mutable
  `timeline_latest.json` pointer.
- Declarative editing strategies (`gaming`, `commentary`, `educational`, `interview`) that remove
  signal-detected dead air, mark signal-backed moments, shift captions to timeline time and
  sanitize untrusted transcript text. No reordering or semantic claims are invented.
- FFmpeg render graph from the EDL: trim/concat cuts, optional `zoompan` punch-ins,
  `xfade`/`acrossfade` transitions, ASS captions and optional music bed with sidechain ducking.
- Reproducible render manifests (settings, sources, filter graph, full ffmpeg args) and QA reports
  with objective checks plus an honest `av_sync` item in `needs_approval`.
- Render outputs use exclusive, non-overwriting names (`edit_vNNN_rNNN.mp4`); originals untouched.

### Fixed
- Adversarial review M5: source files are re-hashed at render time and must match the EDL hash, so
  a replaced source cannot silently corrupt a programmed edit.
- Adversarial review M5: EDL is user-editable JSON feeding FFmpeg arguments → strict allowlists for
  codecs, presets, pixel formats, containers, transitions, CRF and render dimensions/fps.
- Adversarial review M5: concurrent version/render allocation could collide → atomic exclusive
  create for timeline versions and exclusive output reservation for renders (R-17).
- Adversarial review M5: concurrent EDLs sharing a project could clobber `captions.ass` → caption
  assets now carry a content hash suffix.
- Structured validation errors in all `edit` subcommands now include an actionable hint.

## [0.4.0a0] — 2026-11-21 — Phase 4: Media ingestion & understanding

### Added
- `creator media inspect|analyze|search|root` commands backed by real FFprobe/FFmpeg.
- Read-only media ingest: SHA-256 identity, stream/codec/resolution/fps metadata, size caps and
  explicit registered-media-root enforcement for files outside the workspace.
- Independent signal detectors (silence/dead air, scene cuts via `select`, audio spikes via
  `astats`), signal-backed highlight ranking with honest empty results, and repeated-take
  clustering from optional transcripts.
- Optional faster-whisper ASR provider with graceful no-transcript degradation; no fabricated
  speech, timestamps or captions when the provider is absent or fails.
- Injection-safe media index and keyword search: transcript and metadata text is flagged and
  sanitized; generated type labels make signal segments discoverable offline.

### Fixed
- Adversarial review M4: raw FFprobe metadata values (e.g. `comment`) were emitted verbatim and
  could carry injected instructions into agent consumers → values are sanitized and flagged
  fields are reported separately.
- Adversarial review M4: relative `media_roots` config entries resolved against the process CWD →
  now resolve against the workspace base.
- Adversarial review M4: unbounded detector output on pathological sources → derived segment
  lists capped at 500 with an explicit `truncated` flag.
- `UnsafePathError.__str__` now includes its hint, so structured and human error paths agree.

## [0.3.0a0] — 2026-11-21 — Phase 3: Research & idea generation

### Added
- Research modules: ContentHistoryAnalyzer, AudienceAnalyzer, CompetitorAnalyzer,
  TrendResearcher (timestamped snapshots), IdeaGapFinder, OpportunityScorer with transparent
  configurable weights, and deterministic IdeaGenerator.
- ContentPlanner and beat-aware script drafts driven by the creator profile.
- `creator ideas`, `creator trends [--from-file]`, `creator competitors [--from-file]`,
  `creator plan`, `creator script draft --idea ID`; bundled trend/competitor JSON fixtures.
- Mandatory provenance labels (`verified`/`estimated`/`hypothesis`) on all research data and
  generated idea fields; scores explicitly disclaim views/virality guarantees.

### Fixed
- Adversarial review M3: Windows cp1252 console crashed on non-ASCII output → CLI streams are
  now UTF-8 and research method strings avoid unsupported symbols.
- Adversarial review M3: ContentPlanner could reuse one slot / stop early → search now spans a
  full week and advances to the next day after each scheduled idea.
- Adversarial review M3: `creator trends` with no snapshot returned exit 0 with `ok: false` →
  now returns a structured validation error (exit 2) with an actionable hint, while idea
  generation still tolerates missing trend data.

## [0.2.0a0] — 2026-11-21 — Phase 2: Profiles & channel intelligence

### Added
- Versioned creator profile with all mandated sections and an approval-gated memory system
  (module-generated changes are staged, never silently applied).
- HMAC approval tokens with action/target binding and expiry; reusable for publishing in P8.
- `creator profile init/show/set/changes/apply`, `creator channel connect/status`
  (local fixture mode, explicitly labeled as not a live connection), `creator analyze channel`.
- ChannelAnalyzer with median-baseline, outlier, format-mix, repeated-topic findings and
  `verified`/`estimated`/`hypothesis` labels.
- Example profile and channel snapshot fixtures under `examples/`.

### Fixed
- Adversarial review M2: audit log paths were inconsistent (`<base>/logs` vs `<base>/.creator/logs`);
  all command handlers now use the workspace log directory.

## [0.1.0a0] — 2026-11-21 — Phase 1: Foundation

### Added
- `creator` CLI (contract v1.0): `doctor`, `init`, `audit show`, `--json` on every command,
  structured/redacted errors, stable exit codes, flags before or after subcommands.
- Security primitives: workspace/path confinement (traversal, symlink escape, Windows reserved
  names), safe list-args subprocess wrapper with timeouts/output caps, redaction filters,
  OS-keyring secret storage with explicit opt-in plaintext fallback, append-only audit log.
- `.creator/` workspace layout with default config and atomic writes.
- FFmpeg/FFprobe auto-discovery (config/env → PATH → winget package dirs → common dirs).
- Universal Agent Skill skeleton at `skill/youtube-creator-agent/` with per-agent installer
  adapters (Codex, Claude Code, Cursor, Windsurf/Devin, OpenCode, generic), CLI reference,
  editing workflow reference, and safety rules.
- Test suite: CLI contract, path/subprocess/secret/installer security, workspace, skill-spec,
  FFmpeg discovery; synthetic stub binaries for offline doctor tests.

### Fixed
- Adversarial review M1: doctor version-requirement string (`3>=11` → `>=3.11`).
- Adversarial review M1: skill installer symlinked-parent escape (now checks lexical + resolved
  paths against the install root; uninstall refuses unrelated skills).
