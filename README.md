# Universal YouTube Creator Agent Skill

An agent-agnostic creator automation toolkit: **research → ideation → scripting → footage
analysis → real FFmpeg editing → preview/revision → Shorts → scheduling → publishing →
analytics → learning**. The skill is the interface for AI coding agents (Codex, Claude Code,
Cursor, Windsurf, OpenCode, …); the Python core performs the actual computation.

> ### 📢 Public alpha — v0.10.0a0
>
> This is a **public alpha, not a stable release**. The alpha prioritizes the **local-first
> creator workflow** that works with your own data and your own authorized footage: analyze
> footage, build versioned edit plans, render locally, generate captions/metadata, preview and
> revise, and export — all on your machine. YouTube account access is **optional**; publishing is
> a separate, explicit, approval-gated step you perform yourself.
>
> **Verified in this alpha:** research, idea discovery, trend/competitor analysis and channel
> analysis all run **offline from snapshot/fixture files you supply** — there is **no live
> internet research and no live public-channel retrieval**. There is **no thumbnail
> generation**; `youtube thumbnail` only uploads an image **you** already have.
>
> **Experimental / blocked and clearly labelled:** neural voice cloning (VoiceDNA inference),
> measured WhisperX word alignment, live YouTube publishing, and full cross-agent compatibility.
> See [`ALPHA_RELEASE_NOTES.md`](ALPHA_RELEASE_NOTES.md) for the full capability matrix,
> verified agent runtimes, known limitations, and how to report bugs.

## Status (honest, see PROGRESS.md)

> **Public alpha (2026-11-23):** local regression/quality/packaging gates pass, and
> scanner-backed security/dependency audits were **executed** (bandit: 0 High / 1 Medium / 12 Low
> with the one High fixed; mypy: 191 pre-existing findings documented; pip-audit:
> closure-relevant subset triaged; `twine check` PASSED). Cross-platform artifact-manifest
> containment verified on both Windows and POSIX. Full Windows regression: **653 passed,
> 6 skipped**. POSIX voice suite: 135 passed, 5 skipped.
>
> **Experimental or blocked (not advertised as verified):** V-01 real neural OpenVoice inference,
> V-05 measured WhisperX word alignment, live YouTube publishing, and full cross-agent runtime
> compatibility (only OpenCode exercised end-to-end). See
> [`ALPHA_RELEASE_NOTES.md`](ALPHA_RELEASE_NOTES.md) for the capability matrix, and
> `FINAL_RELEASE_ACCEPTANCE.md` for the underlying evidence.

| Phase | Area | Status |
|---|---|---|
| 0 | Architecture, decisions, risks, test strategy | complete |
| 1 | Skill skeleton, `creator` CLI, workspace, security primitives | complete |
| 2–7 | Profiles, research, media, editing, preview/revision, Shorts | complete |
| 8 | YouTube OAuth/uploads/scheduling (stub-first, private-first approvals) | complete (live external acceptance blocked) |
| 9 | Analytics ingestion, cited reports, approval-gated learning | complete (live external acceptance blocked) |
| 10 | Editing breadth, multilingual captions, turn-only diarization, optional MCP, packaging | complete |
| 11 | VoiceDNA: consented narration, providers, alignment, additive EDL, mix, preview, hybrid, QA | **local implementation present; real neural inference/measured alignment NOT VERIFIED** |

Only the commands listed as ✅ in `skill/youtube-creator-agent/SKILL.md` are implemented so far;
🚧 commands deliberately do not exist yet — no placeholder stubs. YouTube publishing and analytics
both run against explicit offline stubs/fixtures by default; real Data/Analytics API calls require
the `[youtube]` extra, `--live` and OAuth credentials, and have not been verified against a real
account. Analytics findings are correlations with cited evidence, never predictions or proof of
cause; learning changes nothing until a creator explicitly approves it. The optional MCP adapter is
strictly read-only and the CLI remains the authoritative execution contract. Caption translations
and diarization are opt-in, fail closed, and never claim an unavailable model ran.

## Install

This alpha is distributed as a **clean-source ZIP**. It contains the Python CLI source,
the agent skill, its references, examples, and the local preview assets. The installer
copies the skill into an agent's skill directory; it does **not** install the Python CLI
or FFmpeg for you.

### Prerequisites

- Python 3.11 or newer.
- FFmpeg and FFprobe available to the same user account.
- A supported coding agent with shell access.
- Use the same Python environment for the CLI that the agent's shell will use.

Install FFmpeg first:

```powershell
winget install Gyan.FFmpeg
```

On macOS: `brew install ffmpeg`. On Debian/Ubuntu: `sudo apt install ffmpeg`.

### Install from the GitHub Release ZIP

1. Download `universal-creator-agent-0.10.0a0-clean-source.zip` from the GitHub Release.
2. Extract it to a permanent directory, for example:

```powershell
C:\tools\universal-creator-agent-0.10.0a0-source
```

3. Open PowerShell in the extracted project root and install the CLI into the active
   Python environment:

```powershell
cd C:\tools\universal-creator-agent-0.10.0a0-source
python -m pip install .
```

4. Install the skill. For one agent, replace `opencode` with `codex`, `claude`, `cursor`,
   `windsurf`, or `devin`:

```powershell
python skill\youtube-creator-agent\scripts\install.py --agent opencode --scope global
```

To install into the current project instead of the user-wide location, omit `--scope global`
or pass `--scope project`. To install into all configured agent directories:

```powershell
python skill\youtube-creator-agent\scripts\install.py --agent all --scope global
```

5. Verify both parts:

```powershell
creator --version
creator doctor --json
python skill\youtube-creator-agent\scripts\install.py --list
```

The `creator` command must be available in the shell used by your coding agent. If it is not,
check that you installed into the same Python environment and that its Scripts directory is on
`PATH`, then open a new terminal.

**Important:** the current installer installs the skill files only. It does not install Python,
FFmpeg, optional AI models, OAuth credentials, or the `creator` CLI. The CLI installation above
is a separate required step.

### Install the CLI from PyPI (only if this version is published there)

If `0.10.0a0` is available on PyPI, the CLI can instead be installed with:

```powershell
python -m pip install universal-creator-agent==0.10.0a0
```

This replaces step 3 only. You still need to extract the GitHub ZIP and install the skill in step 4.
Do not assume the package is on PyPI merely because a GitHub Release exists.

### Browser preview UI

The browser preview UI (`web/preview/`) is served from the extracted source tree and is not
included in the wheel. Keep the extracted source directory in place. In PowerShell:

```powershell
$env:PYTHONPATH = "C:\tools\universal-creator-agent-0.10.0a0-source\src"
creator edit preview <project> --json
```

On POSIX:

```bash
export PYTHONPATH=/opt/universal-creator-agent-0.10.0a0-source/src
creator edit preview <project> --json
```

Verify the assets are present:

```bash
python -c "from creator_core.preview.server import STATIC_DIR; print(STATIC_DIR.is_dir(), STATIC_DIR)"
```

The result must show `True` and a path ending in `web/preview`.

### Optional extras

Install optional extras into the same Python environment as the CLI:

```bash
python -m pip install ".[transcribe]"
python -m pip install ".[youtube]"
python -m pip install ".[diarize]"
```

Run these from the extracted project root. They are not required for the core offline workflow.
Neural voice capabilities remain experimental and require separately installed runtimes,
verified model artifacts, license evidence, and explicit voice-owner consent.

## Current commands

```bash
creator --version
creator doctor [--check-workspace] [--json]
creator init [--dir DIR] [--json]
creator audit show [--limit N] [--json]
```

Publishing (private-first, approval-gated; see the
[publishing workflow](skill/youtube-creator-agent/references/workflow-publish.md)):

```bash
creator youtube connect --json             # offline stub (default; --live for real OAuth)
creator youtube upload --project ID --source FILE --title T --approve --json
creator youtube verify --video-id ID --json
creator youtube publish --video-id ID --approve --json
creator youtube thumbnail --video-id ID --image FILE --approve --json
creator youtube status|quota --json
```

Analytics & learning (fixture-first; see the
[analytics workflow](skill/youtube-creator-agent/references/workflow-analytics.md)):

```bash
creator analytics refresh --json                    # bundled offline fixture (--from-file, --live)
creator analytics report --json                     # cited, correlation-only markdown + JSON
creator analytics learn --json                      # stage hypothesis proposals (inert)
creator analytics learn --apply --approve --json    # explicit approval → scoring_config.json
creator analytics status --json
```

Editing breadth & multilingual captions:

```bash
creator edit new --source CLIP --strategy tutorial|documentary|vlog --project ID --json
creator captions languages --json                       # fixed catalog + RTL metadata
creator captions export --project ID --format vtt|srt --json
creator captions export --project ID --translations FILE --to-lang es --json  # offline translation
creator media diarize --from-file REPORT --provider heuristic --json          # turns, never identities
```

VoiceDNA — consented creator narration (see `VOICEDNA_ARCHITECTURE.md`,
`VOICE_CONSENT_POLICY.md`, `VOICE_SECURITY.md` and `VOICE_API_CONTRACT.md`):

```bash
creator voice providers|languages --json
creator voice setup --owner ID --reference FILE --display-name NAME --confirm-voice-ownership --approve --json
creator voice register --owner ID --reference FILE --display-name NAME --purpose narration --confirm-voice-ownership --approve --json
creator voice inspect|profiles|history --owner ID [--profile ID] --json
creator voice test --owner ID --profile ID --text "..." --json
creator voice generate --owner ID --profile ID --project PRJ --concept "..." --approve --json
creator voice align --owner ID --profile ID --json
creator voice compose --owner ID --profile ID --project PRJ --json
creator voice mix --project PRJ [--dry-run] --json        # non-destructive second pass
creator voice preview --project PRJ [--segment seg_001] --json
creator voice regenerate --owner ID --profile ID --segment seg_001 --approve --json
creator voice hybrid --project PRJ [--actions JSON] --json
creator voice qa --project PRJ --json
creator voice disclosure --project PRJ [--approve] --json
creator voice consent grant|revoke --owner ID --profile ID --purpose narration --approve --json
creator voice delete --owner ID --profile ID --approve --json
python scripts/voice_acceptance_demo.py --json            # full 16-stage local acceptance demo
```

Voice DNA never changes the frozen `WorkspacePaths` contract or EDL v1 track semantics: it adds a
top-level `voice` object that older readers ignore. Consent is explicit, purpose-scoped, revocable and
checked inside generation. Neural models are lazy and fail closed; `test_tone` output is labelled
`mocked=true` and can never be published. Nothing is downloaded or uploaded implicitly. See
`VOICE_MODEL_COMPARISON.md` for licence review and `VOICE_BENCHMARKS.md` for what was and was not
measured.

Optional read-only MCP adapter and local release tooling:

```bash
creator mcp tools --json                 # curated, workspace-scoped tool catalog
creator mcp serve                        # JSON-RPC 2.0 over stdin/stdout (blocking)
creator release check --json             # version agreement + packaged files
creator release manifest --json          # SHA-256 of required source files
python scripts/build.py                  # local sdist + wheel + SHA256SUMS.txt (no upload)
```

Full CLI contract: `skill/youtube-creator-agent/references/cli-reference.md`.

## Development

```bash
python -m pytest                     # unit + integration + security suites
python -m pytest tests/security -q   # security only
python -m pytest --cov=creator_core --cov-report=term-missing   # full phase gate
```

Latest gate (v0.10.0a0, executed on this repository): `python -m pytest -o addopts="" -q` →
**653 passed, 6 skipped, 0 failed**. The 6 skips are Windows symlink-privilege; escaped-path
cases are covered by NTFS junction tests that run without that privilege. POSIX (WSL2 Ubuntu
24.04): voice suite **135 passed, 5 skipped**. `TEST_VERIFICATION.md` records earlier
measurements (532, 450 …) as historical evidence. The historical "87% coverage" figure was
incorrect — the independently measured baseline was **80%** (see `POST_RELEASE_AUDIT.md` section
1). No coverage figure in this repository is rounded up.

Architecture and engineering rules live in `ARCHITECTURE.md`, `TECHNICAL_DECISIONS.md`,
`RISK_REGISTER.md`, `TEST_STRATEGY.md`, `DEVELOPMENT_ROADMAP.md`. Changes are tracked in
`CHANGELOG.md` and `PROGRESS.md`; milestone evidence in `docs/DEVELOPMENT_LOG.md`.

## License

MIT — see `LICENSE`. Third-party dependencies, optional extras, neural voice model-licence
caveats, and external programs are disclosed in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).
FFmpeg is installed by the user and is not redistributed with this project.
