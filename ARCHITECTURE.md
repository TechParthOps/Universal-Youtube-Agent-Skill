# ARCHITECTURE — Universal YouTube Creator Agent Skill

Status: Draft v1.0 (pre-Phase 1) · Owner: project · Last updated: 2026-11-22

This document defines the system architecture for a portable, agent-agnostic YouTube creator
automation toolkit. It is the source of truth for layer boundaries, data contracts, and module
responsibilities. Companion documents: `DEVELOPMENT_ROADMAP.md`, `TECHNICAL_DECISIONS.md`,
`RISK_REGISTER.md`, `TEST_STRATEGY.md`.

---

## 1. Product summary

The system lets an AI coding agent (Codex, Claude Code, Cursor, Windsurf, OpenCode, …) operate a
full creator workflow:

RESEARCH → IDEATION → PLANNING → SCRIPTING → FOOTAGE ANALYSIS → VIDEO EDITING → PREVIEW →
REVISION → EXPORT → SHORTS → SCHEDULING → PUBLISHING → ANALYTICS → LEARNING.

It is **not** a chatbot. It is an executable toolkit with three independently testable layers,
joined by one portable contract: a CLI (`creator …`) that emits structured (JSON) output.

## 2. Layer diagram

```
┌───────────────────────────────────────────────────────────────────────────┐
│ LAYER A — Universal Agent Skill (skill/youtube-creator-agent/)             │
│  SKILL.md (activation + command semantics) · references/ (workflows,      │
│  CLI reference, agent-install guides) · scripts/install.py (adapters)     │
└──────────────┬────────────────────────────────────────────────────────────┘
               │ natural-language request → maps to CLI invocation
               ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ LAYER B — Creator Intelligence Core (Python package `creator_core`)       │
│  profiles · research · planning · media (ingest/transcribe/index)         │
│  editing (EDL + render) · qa · shorts · youtube · analytics · learning    │
│  captions (i18n export/translation) · media/diarize · mcp · release       │
│  providers (LLM abstraction) · security (paths, secrets, audit)           │
└──────────────┬───────────────────────────────┬────────────────────────────┘
               │ CLI (creator/ entry point)     │ library import
               ▼                                ▼
┌───────────────────────────────────────────┐  (read-only MCP adapter, Phase 10, optional)
│ LAYER C — Creator Preview Workspace       │
│  stdlib HTTP server (127.0.0.1) + local   │
│  web UI: player, timeline, compare,       │
│  revisions, export management             │
└───────────────────────────────────────────┘
```

**Rule:** every capability is reachable three ways — CLI, direct library import, and (optionally)
MCP. Nothing in Layer B may depend on Layer A or Layer C. Layer C depends only on Layer B.
Layer A depends only on the CLI contract.

## 3. Repository layout

```
<checkout-root>\
├── SKILL-ROOT NOTE: skill lives in its own named directory (spec requires
│   directory name == skill name):
├── skill\youtube-creator-agent\
│   ├── SKILL.md
│   ├── references\cli-reference.md
│   ├── references\workflow-research-to-publish.md
│   ├── references\workflow-edit-and-revise.md
│   ├── references\workflow-shorts.md
│   ├── references\agent-install.md        (per-agent adapters)
│   ├── references\security-and-data.md
│   └── scripts\install.py                 (copies skill into agent dirs)
├── src\creator_core\                      (Layer B package)
│   ├── cli\                               (argparse entry `creator`)
│   ├── profiles\  research\  planning\
│   ├── media\     editing\    qa\  shorts\
│   ├── youtube\   analytics\  learning\
│   ├── providers\ security\   workspace\ (storage, .creator layout)
│   └── preview\                           (Layer C server, stdlib http)
├── web\preview\                           (vanilla JS/HTML UI, no build step)
├── tests\  (unit | integration | media | security)
├── examples\ (example profile, example editing project, sample config)
├── docs\ (SECURITY.md, INSTALL.md, SUPPORTED_AGENTS.md, DEVELOPMENT_LOG.md)
├── pyproject.toml   README.md   CHANGELOG.md   LICENSE
└── ARCHITECTURE.md  DEVELOPMENT_ROADMAP.md  TECHNICAL_DECISIONS.md
    RISK_REGISTER.md  TEST_STRATEGY.md
```

## 4. Layer A — Universal Agent Skill

- `SKILL.md` follows the agentskills.io spec: frontmatter `name: youtube-creator-agent`,
  `description` ≤1024 chars with trigger keywords **first** (some agents truncate the listing at
  1,024–1,536 chars), optional `compatibility` (Python ≥3.11, FFmpeg on PATH), `metadata`.
  Body < 500 lines; details live in `references/`.
- **Command semantics are agent-independent.** SKILL.md contains a routing table:
  natural-language intent → `creator <subcommand>` + flags. Agents never need custom tools; they
  run the CLI and read JSON stdout.
- **Progressive disclosure:** SKILL.md holds routing + safety rules; `references/*.md` hold
  per-workflow steps loaded on demand.
- **Installer adapters** (`scripts/install.py`): copy/symlink the skill directory into
  `.claude/skills`, `.agents/skills`, `.cursor/skills`, `.opencode/skills`, `.windsurf|devin/skills`,
  or print manual steps. Also writes/patches a root `AGENTS.md` pointer (the one file every
  harness reads) with a short skill notice.
- **Optional MCP (Phase 10, implemented):** a thin, strictly read-only stdio JSON-RPC 2.0 server
  (`mcp/server.py`) exposes eight curated tools scoped to one fixed workspace. It cannot execute
  shell commands, write files, read arbitrary paths or leak secrets. It is optional — the CLI
  remains the authoritative execution contract for every agent.

## 5. Layer B — Creator Intelligence Core

### 5.1 Module map (prompted modules → implementation units)

| # | Prompted module | Implementation unit(s) | Notes |
|---|---|---|---|
| 1 | ChannelAnalyzer | `youtube/channel.py` | Data API: channel stats, uploads playlist, video metadata |
| 2 | CreatorProfileManager | `profiles/manager.py` | Versioned `profile.json`, explicit-approval memory changes |
| 3 | AudienceAnalyzer | `research/audience.py` | From available public data + Analytics API when connected; labels verified vs estimated |
| 4 | ContentHistoryAnalyzer | `research/history.py` | Per-video performance vs channel baseline |
| 5 | CompetitorAnalyzer | `research/competitors.py` | Public Data API search/lists only (no scraping) |
| 6 | TrendResearcher | `research/trends.py` | Data API + optional RSS/official sources; provenance on every datum |
| 7 | IdeaGapFinder | `research/gaps.py` | Topic clustering over history + competitor corpora (embeddings when a provider is configured; TF-IDF fallback) |
| 8 | OpportunityScorer | `research/scoring.py` | Transparent weighted criteria; configurable; never "virality prediction" |
| 9 | ContentPlanner | `planning/planner.py` | Calendar, formats, production-time estimates |
| 10 | PerformanceLearner | `analytics/{ingest,report,learning,transport}.py` | Fixture-first Analytics ingestion → cited, correlation-only reports → hypothesis proposals → approval-gated feedback into `research/scoring_config.json` |

### 5.2 Media subsystem (`media/`, `editing/`)

**Ingest** (`media/ingest.py`): FFprobe JSON inspection → resolution, fps, duration, streams,
codecs, metadata, size, `sha256`. Supported: MP4, MOV, MKV, WEBM, MP3, WAV (+ common codecs
FFmpeg handles). Originals are **read-only**; every project records source hashes.

**Understanding** (`media/analyze.py`), each feature degrades independently:
- Speech-to-text: `faster-whisper` (optional extra), timestamped segments + word timings.
- Scene detection: FFmpeg `select='gt(scene,T)'` with scdet thresholds.
- Silence/dead-air: FFmpeg `silencedetect` → removable segments.
- Audio events: RMS/energy spikes (shouts, hits) via FFmpeg `astats`/`ebur128` scans.
- Visual moments: scene-change + motion-energy ranking; gameplay "highlight" heuristic = energy
  + audio spike + fast scene transitions (tunable per category).
- Repeated takes: transcript n-gram + scene similarity clustering.
- Speaker turns (Phase 10): the optional offline heuristic groups transcript segments by pause
  gap and emits neutral `speaker_turn_N` hypotheses. It never analyses voices or identifies people;
  vendor diarization is a lazy, fails-closed optional extra. Speaker *identity* remains out of scope.

Output → **searchable media index** (`media_index.json` per project): segments with type,
timestamps, text, confidence, evidence. `creator media search "…"` does keyword + optional
embedding search and returns ranked timestamps + evidence + confidence.

**Editing engine** (`editing/`): strategies per content category (gaming, commentary, educational,
tutorial, documentary, vlog). A strategy is a declarative pipeline of operations; effects are
pluggable data, applied only when the strategy/creator profile calls for them.

**Timeline EDL** — the source of truth (JSON, schema_versioned, example in §7):
- `sources[]` (hash, fps, duration, audio streams), `tracks{video[],audio[],music[],captions[]}`,
  per-clip `source_in/out`, `timeline_in`, effects (zoom/pan/speed), transitions, markers,
  `render{}` (container, codec, crf, fps, dimensions), and `provenance.decisions[]`
  (operation, evidence, approver, timestamp).
- **Non-destructive by construction:** renders read sources + timeline; originals never touched;
  every revision is a new immutable timeline version file; renders are versioned and reproducible
  (render settings + input hashes recorded in `render.manifest.json`).

**Rendering** (`editing/render.py`): builds an FFmpeg filter graph from the EDL:
- Cuts via accurate seek (`-ss` before `-i` for keyframe seek + trim filters; frame-accurate mode
  re-encodes segments), concat filter, per-clip effects (zoompan/crop/scale), audio mixing
  (`amix`/`sidechaincompress` for music ducking), caption burn-in via `subtitles` (ASS generated
  from cues), transitions via `xfade`, aspect conversion via crop/scale with pad.
- Progress streamed to stdout/preview server; subprocess timeout + disk-space preflight.

**QA** (`qa/checks.py`): blackdetect, freezedetect, audio clipping (peak/true-peak), missing
audio, A/V sync spot checks (correlation of onset energy vs frame changes), subtitle timing
validation (overlap, negative gaps, read-speed, sync vs audio), blank sections, aspect ratio,
missing assets, corrupt-output verification (decode full output). Output: `qa.report.json` with
`auto_fixed` vs `needs_approval` classes.

**Shorts** (`shorts/engine.py`): candidate moment ranking (narrative completeness from
transcript boundaries, audio/visual energy, creator-relevance from profile) → hook generation
(LLM optional; template fallback) → 9:16 reframe (focus-selectable: subject windowing via motion
centroids, fallback center+blur-pad; **not** naive center crop) → animated captions → audio
balance → per-Short preview + MP4 + title/description. All Shorts require creator approval.

**YouTube integration** (`youtube/`): stub-first transport plus an optional real adapter using
`google-api-python-client` + OAuth (installed-app flow). One `youtube connect --live` requests
`youtube.upload`, `youtube` and `yt-analytics.readonly` so a single stored token can serve both the
Data API and Analytics (older tokens must reconnect). Resumable chunked uploads preserve the
session URI on retry; processing-status polling is the explicit `youtube verify` command;
`publishAt` scheduling requires ISO-8601 UTC and privacy `private`; thumbnail upload and a local
**advisory** quota ledger exist (default 10k units/day; `channels.list` is declared but not charged
by orchestration, and failed attempts are not charged). The **publishing state machine** (see §9)
enforces a local parent-public gate for Shorts and fresh payload-bound approval. Live paths require
the `[youtube]` extra and are not exercised in CI — see `YOUTUBE_INTEGRATION_VALIDATION.md`.

**Analytics & learning** (`analytics/`): fixture-first Analytics ingestion (API-shaped rows or
simplified JSON; views, impressions, CTR, AVD, avg %, watch time, likes/comments/shares, subs
gained; per-second retention curve is not implemented) → normalized snapshots with strict provenance (`verified`
only for the live API, otherwise `estimated`) → written reports that cite metric, median, sample
size, source and provenance with correlation-only language → deterministic hypothesis proposals.
Comparisons use an earlier snapshot or the profile baseline. Learning is inert until a fresh HMAC
approval: staging writes `profile.memory.pending_changes` (no effect) and applying writes
`research/scoring_config.json`, which `creator ideas` loads. Live API calls are explicit (`--live`)
and never fall back to the fixture.

### 5.3 Provider abstraction (`providers/`)

```python
class TextProvider:      complete(prompt, schema=None) -> str | dict   # JSON-schema structured output
class VisionProvider:    analyze(image, prompt) -> dict
class EmbeddingProvider: embed(texts) -> list[vector]
class TranscriptionProvider: transcribe(audio_path) -> Transcript
```

Implementations (config-selected, all optional at runtime): OpenAI-compatible (OpenAI, Groq,
OpenRouter, LM Studio, vLLM…), Anthropic-compatible, Google Gemini, Ollama (local). The media
layer never requires any provider: deterministic heuristics produce usable results without one.
Unavailable provider ⇒ explicit typed error (`ProviderUnavailableError`); the system never
pretends a model ran. Config references credentials by **environment variable name or keyring
entry**, never literal secrets in files.

### 5.4 Security layer (`security/`)

- `paths.py`: workspace confinement — resolve + reject traversal, symlink escapes, reserved
  Windows names, NUL/UNC tricks; media roots must be explicitly registered.
- `subprocess.py`: list-args only (never `shell=True`), timeouts, output size caps, env allowlist.
- `secrets.py`: keyring-first storage (Windows Credential Manager/DPAPI); explicit opt-in
  plaintext fallback with warning; secrets never logged, never included in LLM prompts. Redaction
  is recursive through nested mappings, lists and tuples before audit output.
- `audit.py`: append-only JSONL audit log (`.creator/logs/audit.jsonl`) — commands, file
  mutations, publishing actions, approvals, config changes.
- `approval.py`: the single state-change approval primitive. `--approve` issues a short-lived HMAC
  bound to action, target and the **full canonical request digest**; verification consumes it once.
  The nonce ledger is append-only and guarded by a cross-process lock, so replay/concurrent reuse is
  rejected. Audit records store only fingerprints. Stage 12I removed local create/verify variants.
- `injection.py`: untrusted-text handling (transcripts, comments, media metadata) — treated as
  data, fenced, pattern-flagged; never interpreted as instructions by core code.

## 6. Layer C — Creator Preview Workspace

Local, stdlib-HTTP server (`creator edit preview` → `http://127.0.0.1:8765`), loopback bind
only, no build toolchain (vanilla JS + HTML in `web/preview/`).

Endpoints: `GET /api/project`, `GET /api/timeline?version=`, `GET /api/video?version=` (HTTP
Range streaming of renders), `GET /api/video/source?id=` (original stream), `GET /api/qa/<v>`,
`GET/POST /api/revisions` (natural-language revision requests → `revisions/pending/*.json`
picked up by `creator edit revise`), `POST /api/render` (invokes core render), `GET /api/exports`.

UI: synchronized video player + timeline (clip blocks, boundaries, gaps/removed footage),
audio + caption track lanes, editing-decision log, original-vs-edited A/B compare with synced
playhead, subtitle review, revision request composer (with the prompt's example presets),
version list + compare, QA report, export management.

## 7. Timeline EDL sketch (v1 schema)

```json
{
  "schema_version": "1.0",
  "project_id": "prj_20261121_0913_bosufight",
  "parent_version": null,
  "sources": [{
    "id": "src1", "path": "raw/gameplay_raw.mp4", "sha256": "…",
    "duration": 1234.5, "fps": 60.0, "width": 1920, "height": 1080,
    "audio_streams": [{"index": 0, "codec": "aac", "language": null}]
  }],
  "tracks": {
    "video": [{"clip_id": "c1", "source": "src1", "source_in": 12.32,
               "source_out": 48.90, "timeline_in": 0.0, "speed": 1.0,
               "effects": [{"type": "zoom", "factor": 1.25, "anchor": "center",
                            "start_at": 0.0, "end_at": 3.2}]}],
    "audio":  [{"clip_id": "a1", "source": "src1", "source_in": 12.32,
                "source_out": 48.90, "timeline_in": 0.0, "gain_db": 0.0}],
    "music":  [{"asset": "assets/loop.mp3", "gain_db": -18.0,
                "duck_to_db": -28.0, "start": 0.0, "end": 95.0}],
    "captions": [{"cue_id": "cap1", "start": 0.4, "end": 2.1,
                  "text": "this boss is impossible", "style": "karaoke"}]
  },
  "transitions": [{"between": ["c1", "c2"], "type": "cut"}],
  "markers": [{"at": 0.0, "label": "hook"}],
  "removed_ranges": [{"source": "src1", "in": 48.90, "out": 63.10,
                      "reason": "dead_air", "restorable": true}],
  "render": {"container": "mp4", "video_codec": "libx264", "crf": 20,
             "fps": 60, "width": 1920, "height": 1080,
             "audio_codec": "aac", "audio_bitrate": "192k"},
  "provenance": {"strategy": "gaming.v1", "decisions": [
      {"op": "remove_dead_air", "evidence": "silence>1.2s ×14",
       "approved_by": null, "at": "2026-11-21T09:20:00Z"}]}
}
```

Project storage (inside a user workspace, default `./.creator/`):

```
.creator\
├── config.json              (limits, paths, provider selection, time zone)
├── profiles\profile.json    (creator profile, versioned)
├── projects\<project_id>\
│   ├── project.json         (sources, status)
│   ├── timeline\versions\v001.json … vNNN.json
│   ├── revisions\pending\…  decided\…
│   ├── renders\v003.mp4 + v003.render.manifest.json
│   ├── qa\v003.qa.report.json
│   ├── media_index.json  transcripts\  exports\
│   └── shorts\<short_id>\timeline.json + preview.mp4
├── research\  publishing\plan.json + events.jsonl
└── logs\audit.jsonl
```

## 8. CLI design (contract)

Verb-noun subcommands, `--json` on everything, deterministic exit codes:

| Command | Purpose |
|---|---|
| `creator doctor` | Environment check (Python, FFmpeg/FFprobe, optional deps, workspace) |
| `creator profile init\|show\|set` | Creator profile lifecycle |
| `creator channel connect\|status` | YouTube OAuth connect, channel snapshot |
| `creator analyze channel` | Channel/content/audience analysis |
| `creator ideas` · `trends` · `competitors` · `plan` | Research & planning suite |
| `creator script draft` | Script/hook drafting (LLM optional) |
| `creator media inspect\|analyze\|search` | Ingest, understanding, natural-language media search |
| `creator edit new\|render\|preview\|revise\|status\|versions` | Editing lifecycle |
| `creator shorts generate\|review` | Shorts pipeline |
| `creator youtube connect\|status\|quota\|upload\|verify\|publish\|thumbnail` | Publishing state machine |
| `creator analytics refresh\|report\|learn\|status` | Analytics ingestion, cited performance reports, approval-gated learning |
| `creator audit show` | Security/audit log access |

Exit codes: `0` ok · `2` validation · `3` missing dependency · `4` auth required ·
`5` approval required · `6` I/O · `7` provider/API error. All errors are structured
(`{"error": {"code", "message", "hint"}}`) on stderr, never raw tracebacks (unless `--debug`).

## 9. Publishing state machine

```
draft → approved(by creator) → uploading(private, resumable) → processing
                                                    ├→ private → public
                                                    └→ scheduled(publishAt) → public
failed ← reachable from any active state (recorded, re-approvable)
Shorts: require an approved Short review + --parent whose state is public; re-checked immediately
before BOTH upload and publish; a demoted/missing parent blocks release without mutating state.
```

Implemented states are `draft, approved, uploading, processing, private, public, scheduled, failed`
(`youtube/state.py`); invalid transitions are rejected and every transition is audit-logged.
Approval is an explicit operator action: `--approve` issues a short-lived HMAC bound to the action,
target and full canonical request (title, privacy, media hash, and all fields that determine the
mutation). It is consumed once; the fingerprint (never the token) is recorded in the audit log. No
publishing code path exists without a prior approval event, and `upload()` cannot create a public
video.

## 10. Cross-cutting concerns

- **Determinism & provenance:** every generated artifact carries timestamps, tool versions
  (FFmpeg/FFprobe, model IDs), and evidence; research data is labeled `verified` / `estimated` /
  `hypothesis`.
- **Local-first:** default configuration makes zero network calls; YouTube + optional LLM calls
  are the only egress, both opt-in and audit-logged. Optional translation endpoints are restricted
  to documented provider base URLs; custom gateways require an explicit config opt-in and must be
  plain https (or localhost http) without embedded credentials, query or fragment.
- **Release integrity:** `release check` is a gate, not a report: version agreement, required files,
  MANIFEST coverage and shipped-wheel source-byte equality must all pass. Stale artifacts make the
  overall result false; local rebuild/summary generation never uploads.
- **Encoding:** UTF-8 everywhere; Windows long paths handled via `\\?\` prefixes when needed.
- **Performance:** keyframe-accurate seeking, chunked media scans with progress, configurable
  max input size/render duration, temp dir under workspace with cleanup on exit and on boot.

## 11. Extensibility points

Editing strategies, effects, QA checks, research sources, LLM providers, Short hooks, and
narrative structures are all registry-based (entry-point-like registries in code + config), so
new categories (e.g., podcast, music) slot in without touching the engine.
