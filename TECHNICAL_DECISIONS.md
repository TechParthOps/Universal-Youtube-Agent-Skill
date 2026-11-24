# TECHNICAL DECISIONS — Universal YouTube Creator Agent Skill

Status: Draft v1.0 (pre-Phase 1) · Format: lightweight ADRs (context → decision → consequences)

## ADR-001 — Single language: Python ≥ 3.11 for core, CLI, and preview server

**Context:** Cross-platform media/CLI tooling, agent ecosystems that reliably have Python, and
the need for one test stack.
**Decision:** Python 3.11+ (stdlib `tomllib` available; match-version features). Preview UI is
vanilla JS/HTML served by a stdlib HTTP server — no Node build step.
**Consequences:** + one toolchain, strong libraries (FFmpeg wrappers via subprocess, Google APIs,
faster-whisper). − Slightly more work for a rich UI without a JS framework (acceptable: UI is
project-scoped, not generic).

## ADR-002 — CLI as the universal agent contract

**Context:** Agents differ in tool execution, permissions, hooks, and file handling. Every
checked agent (Claude Code, Codex, Cursor, Windsurf/Devin, OpenCode) can run shell commands and
read files.
**Decision:** All capabilities exposed as `creator <subcommand>` with `--json` output and
stable exit codes. SKILL.md maps intents → CLI. Optional MCP adapter later; never required.
**Consequences:** + agent-agnostic, testable, composable. − One indirection layer; JSON schema
must stay stable (versioned in `creator --version` output).

## ADR-003 — Skill packaging per agentskills.io spec, with installer adapters

**Context:** Verified spec: directory name must equal skill `name`; `description` ≤1024 chars
and truncated aggressively by some harnesses (1,024–1,536 chars); skill dirs differ per agent
(`.claude/skills`, `.agents/skills`, `.cursor/skills`, `.opencode/skills`, `.devin/skills`).
**Decision:** Skill at `skill/youtube-creator-agent/`; body <500 lines; trigger keywords first
in description; `references/` for workflows; `scripts/install.py` copies/symlinks into agent
dirs and maintains an `AGENTS.md` pointer note.
**Consequences:** + spec-valid (checkable with `skills-ref validate`), per-agent portability.
− Adapters must be re-verified as agents evolve (tracked in RISK_REGISTER R-08).

## ADR-004 — FFmpeg/FFprobe as the media foundation; binaries not bundled

**Context:** FFmpeg is the only credible open engine for ingest/edit/render/QA. Binaries are
GPL/LGPL-licensed; redistribution creates license obligations and AV-signature noise.
**Decision:** Require FFmpeg+FFprobe on PATH (or configured path). `creator doctor` verifies
and prints install guidance (winget `Gyan.FFmpeg`, choco, brew, apt). We never redistribute
binaries. All media work is subprocess-driven: list args only, no `shell=True`.
**Consequences:** + license-clean, user-controlled versions. − Install prerequisite (documented;
doctor gives actionable errors, exit code 3).

## ADR-005 — JSON EDL ("timeline") as the single source of truth for edits

**Context:** Non-destructive editing requires a stable, diffable, machine-writable decision
record that agents (LLMs) can read and write reliably.
**Decision:** Versioned JSON timeline documents (schema in ARCHITECTURE §7). Revisions are new
immutable version files; renders reference the exact version + input hashes (reproducible
manifests). Deleted/removed ranges are first-class (`removed_ranges[]`) so restores are trivial.
**Consequences:** + auditability, agent-editability, easy A/B compare, trivial undo via versions.
− Render must be a faithful EDL interpreter (validated by synthetic-media tests with known
durations/orderings).

## ADR-006 — Originals are immutable, verified by hash

**Context:** The cardinal sin of automated editors is destroying source footage.
**Decision:** All renders/temps go to project dirs or the temp dir; ingest records `sha256` of
every source; renders re-verify source hashes before starting; no write-capable code path
touches a registered source file. Enforced in `security/paths.py` + tested.
**Consequences:** + safety by construction. − Slight overhead hashing large files (cached).

## ADR-007 — Deterministic-first intelligence; LLM optional and pluggable

**Context:** Core value (dead-air removal, highlight detection, QA, Shorts candidate ranking,
research aggregation) can be achieved with signal processing + heuristics. LLMs add hooks,
titles, narrative reasoning, and semantic search. Paid/local availability varies per user.
**Decision:** Provider adapter layer (OpenAI-compatible, Anthropic-compatible, Gemini, Ollama)
for text/vision/embeddings; transcription via faster-whisper (local) when installed. All AI
features have deterministic fallbacks. Unavailable provider ⇒ typed error, never silent fake
success. Credentials via env-var *references* or keyring, never stored in config files.
**Consequences:** + zero-cost local baseline, no vendor lock, honest failures. − More code paths
to test (mitigated: contract tests with fake providers).

## ADR-008 — Transcription: faster-whisper as the optional local engine

**Context:** Timestamped transcripts power search, caption generation, filler removal, narrative
ranking. Cloud STT costs money/privacy; faster-whisper is local, fast, and pip-installable.
**Decision:** `creator-core[transcribe]` extra → faster-whisper (model selectable, default
`small`, cached). If absent, media understanding runs without text (silence/scene/energy only)
and caption-dependent features degrade with explicit warnings.
**Consequences:** + privacy, offline. − Model download on first use (documented; disk check).

## ADR-009 — Storage: plain files under a `.creator/` workspace; JSON for data, TOML for config

**Context:** Agents and humans both need to inspect state; no server/DB infrastructure desired.
**Decision:** Filesystem storage (layout in ARCHITECTURE §7): JSON documents for profiles,
timelines, indexes, reports, plans; `config.json`/TOML for settings; append-only JSONL for audit
and publishing events. No SQLite dependency for v1 (indexes are per-project and small).
**Consequences:** + greppable, portable, trivially backed up. − Concurrent-write care (single
writer discipline + atomic writes via temp-file rename).

## ADR-010 — Preview workspace: stdlib HTTP server, loopback-only, Range streaming

**Context:** The review workflow (play, compare, revise, export) must be genuinely functional
without heavy dependencies or exposed network surfaces.
**Decision:** `http.server`-based server bound to `127.0.0.1` (configurable port), serving the
project JSON APIs, versioned renders with HTTP Range support, and a vanilla-JS UI (player,
timeline lanes, A/B compare, revision composer, QA report, export manager). Revision requests
POST to disk as JSON; `creator edit revise` consumes them — the agent loop stays the executor.
**Consequences:** + zero extra deps, real interactivity, safe surface. − Not multi-user (by
design); no HTTPS (loopback only).

## ADR-011 — YouTube: official client libraries, OAuth desktop flow, keyring secrets

**Context:** Data API v3 (2026 granular quotas: uploads bucket ~100/day + search bucket ~100/day
+ 10k units/day), resumable uploads, `publishAt` scheduling, Analytics API for learning.
**Decision:** `google-api-python-client` + `google-auth-oauthlib` behind a `youtube` extra;
desktop OAuth flow with refresh; tokens in OS keyring (Windows Credential Manager via `keyring`);
client-secret file path via config/env reference; scope minimization; quota ledger per day;
resumable chunked uploads with retry/backoff on 5xx/308 semantics; `uploadLimitExceeded`
classified as retry-later (platform limit, not a bug).
**Consequences:** + reliable, ToS-compliant. − Users must create a Google Cloud project
(documented walk-through in INSTALL.md; unverified-project caveat: uploads are forced private
until audit, which matches our private-first state machine anyway).

## ADR-012 — Publishing requires explicit, auditable approval; Shorts depend on the parent

**Context:** No unauthorized publishing; dependent Shorts must not go out if the main video
fails/delays.
**Decision:** Publishing state machine (ARCHITECTURE §9): uploads always start `private`;
`scheduled(publishAt)` only after approval event; Shorts re-verify parent `published` state
immediately before each upload; parent not published ⇒ Shorts blocked and event-logged.
`--approve` + typed confirmation token recorded in audit log; no code path bypasses it (tested).
**Consequences:** + safety, compliance. − Extra friction (intended).

## ADR-013 — Research: official APIs + permitted public data only; provenance labels mandatory

**Context:** Prompt forbids unauthorized scraping; hallucinated metrics would poison planning.
**Decision:** YouTube Data API v3 for discovery/metadata (quota-ledgered), optional RSS feed
ingest for trend tracking (official YouTube feeds), no browser scraping. Every datum stored with
`{source, method, retrieved_at, confidence}` and one of three labels: `verified` (API value),
`estimated` (derived), `hypothesis` (model-generated). Scorer output always framed as
opportunity assessment, never performance prediction.
**Consequences:** + trust, auditability. − Less raw signal than scraping (accepted trade-off).

## ADR-014 — Windows-first development, POSIX-portable implementation

**Context:** Dev environment is Windows 11; users will be on all platforms.
**Decision:** `pathlib` everywhere; long-path handling; explicit UTF-8 for subprocess IO;
no POSIX-only calls; CI-style test matrix documented (Windows verified locally; POSIX paths
covered in unit tests via path abstraction).
**Consequences:** + works where the author works. − POSIX specifics (symlink adapters in
installer, keyring backends) verified at milestone gates.

## ADR-015 — Dependency policy: minimal required, feature-gated extras

**Decision:** Required runtime deps: `requests` only (HTTP for provider adapters). Extras:
`[transcribe]` faster-whisper · `[youtube]` google-api-python-client, google-auth-oauthlib,
keyring · `[dev]` pytest, coverage, ruff. Config/data formats use stdlib (`json`, `tomllib`).
**Consequences:** + fast, portable install; `creator doctor` reports optional-feature status.
− YAML skill frontmatter parsed by a small hand-rolled parser in the installer (sufficient for
the fixed spec shape).

## ADR-016 — Milestone discipline and honest status reporting

**Decision:** Every phase has acceptance tests (TEST_STRATEGY §4) run before "complete" status;
`PROGRESS.md` and `CHANGELOG.md` updated per milestone; features are marked `planned`,
`experimental`, or `complete` — nothing between. Known limitations are listed in the README
per release. No TODO stubs behind "complete" labels.

## ADR-017 — Shorts: measured reframe, transparent heuristics, drafts only

**Decision:** 9:16 reframing uses a focus trajectory fitted from motion centroids measured on real
decoded frames; when motion is unreliable it falls back to the full frame over a blurred expanded
background — never a naive centre crop. Candidate scores are published weighted heuristics with
`verified`/`estimated`/`hypothesis` provenance, never view or virality predictions. Hook copy is
deterministic-by-default (template), with optional pluggable providers that degrade transparently
(`model: null`, `fallback_reason`) rather than claiming an unavailable model ran. Generation writes
only local drafts; approval is a separate HMAC-gated creator action and Phase 8 re-checks approval
plus parent publication before any upload.
**Consequences:** + honest, inspectable drafts with a recorded focus model and audit trail;
+ works fully offline; − heavy LLM-generated hooks require optional provider config; crop tracking
depends on detectable motion and remains a human-review aid (it is not a semantic subject detector).

## ADR-018 — Publishing: stub-first transport, private-first flow, UTC quota ledger

**Context:** ADR-011/ADR-012 define *what* publishing must guarantee. The implementation must also
be testable offline and never let a stub run be mistaken for a live publication.
**Decision:**
- A single `YouTubeTransport` interface has two implementations. `StubTransport` is the default
  and performs no network I/O; `GoogleApiTransport` is selected only by explicit `--live`/config
  and lazily imports the optional `[youtube]` extra. Missing dependencies raise
  `missing_dependency` (exit 3) with an install hint. Every result carries `live: true/false`.
- Uploads are resumable: an interruption raises `ResumableInterrupted(uploaded_bytes, uri)`; the
  orchestrator retries from that offset (max 3 attempts), audit-logging each resume. The source
  file is read-only and its SHA-256 is recorded in the publishing state.
- Private-first is enforced in the core, not only the CLI: `upload()` rejects `--privacy public`;
  `verify()` advances `processing → private`/`scheduled`; only `publish()` (with a fresh HMAC token)
  transitions to `public`. Shorts re-check approval and parent `public` state immediately before
  both upload and publish, and a demoted parent blocks release without mutating state.
- The UTC quota ledger uses the documented Data API v3 unit costs, pre-flight checks every
  billable call, charges only after success, and refuses an operation that would exceed the daily
  ceiling (exit 7) before any network call. State, quota and history are plain JSON under
  `<workspace>/publishing/`; only token fingerprints enter the audit log.
**Consequences:** + the entire publishing flow is testable with zero network and zero credentials;
+ a stub result can never claim a live upload; + quota and approval failures are explicit and
auditable; − live API behaviour is verified only when the user explicitly authorizes a real run.

## ADR-019 — Analytics: fixture-first ingestion, cited correlation-only reports, inert learning

**Context:** Phase 9 must turn channel metrics into actionable feedback without overstating what
observational data can prove, and it must remain testable with no YouTube account or network.
**Decision:**
- Ingestion is fixture-first. `FixtureTransport` reads the bundled API-shaped example by default
  or `--from-file`; `GoogleAnalyticsTransport` is selected only by explicit `--live`/config and
  lazily imports the `[youtube]` extra. Normalization accepts both API-shaped rows (with
  `columnHeaders`) and simplified fixtures, and derives metrics locally. Provenance is binary and
  strict: `verified` **only** when `source == youtube_analytics_api`, otherwise `estimated`.
- Reports cite the metric, sample median, sample size, source and provenance for every finding.
  Every interpretation is `hypothesis` with association-only wording and explicit disclaimers
  against causation, prediction, virality and revenue claims. Comparisons use an earlier snapshot
  or the profile baseline and are labelled by their own source.
- Learning proposals are deterministic, bounded heuristics with attached evidence. Staging writes
  to profile memory and is deliberately inert; `apply` requires a fresh HMAC approval bound to the
  complete staged change and consumed once, then writes `research/scoring_config.json`, which
  `creator ideas` already loads, so the feedback loop is real but cannot fire without the creator.
  Missing tag data never penalizes audience fit (the Analytics API does not return tags).
- `.creator/analytics/` holds versioned snapshots and reports as plain JSON/markdown; snapshots
  are read-only inputs to reports and learning.
**Consequences:** + the full learning loop runs offline; + unverified numbers can never be presented
as verified or causal; + no scoring change takes effect without explicit approval; − live Analytics
behaviour is verified only when the user explicitly authorizes a real run, and tag-based hypotheses
require a separate local channel snapshot.
