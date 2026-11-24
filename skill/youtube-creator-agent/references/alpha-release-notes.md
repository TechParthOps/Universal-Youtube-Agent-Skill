# Public Alpha Release Notes — v0.10.0a0

> ## ⚠️ This is a **public alpha**, not a stable release
>
> This alpha is for early adopters who want to evaluate the **local-first creator workflow**:
> research → ideas → footage analysis → edit → render → preview → export. It is **not
> production-stable**. Neural voice cloning, measured alignment, live YouTube publishing, and
> full cross-agent compatibility are **experimental or blocked** and are clearly labelled as such
> throughout the documentation. Do not ship creator deliverables to YouTube on the strength of
> this alpha alone — always review generated output before publishing.

---

## What this alpha is for

The alpha deliberately prioritizes the workflow that works fully **offline, on your machine, with
your own data and your own authorized footage**:

- ✅ **Authorized source-footage analysis** (FFmpeg/FFprobe; optional local ASR).
- ✅ **Editing plans and local video rendering** (versioned EDLs, real FFmpeg renders, QA checks).
- ✅ **Metadata generation** — titles, descriptions, subtitles, captions, chapters.
- ✅ Preview and agent-assisted revision (browser preview UI + revision queue).
- ✅ **Local export without mandatory YouTube OAuth**.
- ✅ **Offline research** — channel/niche/competitor/trend analysis and content-idea discovery
  from **snapshot/fixture files you supply** (see "Not included in this alpha" for what this does
  *not* mean).
- ⚠️ **YouTube thumbnail *upload*** — `youtube thumbnail` uploads an image **you already
  have** (≤ 2 MB). It does **not** create, render, template, or size an image for you.
- YouTube account access is **optional**. Uploading is a separate, explicit, approval-gated step
  that you perform yourself.

Everything is stored **locally** in a `.creator/` workspace next to your media. You inspect and
revise every generated deliverable before you manually upload it.

---

## Installation

This release is a clean-source ZIP containing the Python CLI source, agent skill, references,
examples, and local preview assets. Installation currently has **two required steps**:
install the Python CLI, then copy the skill into the coding agent. The skill installer does
not install the CLI or system dependencies.

### Prerequisites

- Python 3.11+.
- FFmpeg and FFprobe installed and available on `PATH`.
- A supported coding agent with shell access.

### Install from the GitHub Release ZIP (Windows PowerShell)

1. Download and extract `universal-creator-agent-0.10.0a0-clean-source.zip` to a permanent
   location, such as `C:\tools\universal-creator-agent-0.10.0a0-source`.
2. Install the Python CLI into the Python environment that your agent's shell will use:

```powershell
cd C:\tools\universal-creator-agent-0.10.0a0-source
python -m pip install .
```

3. Install the skill into one agent globally:

```powershell
python skill\youtube-creator-agent\scripts\install.py --agent opencode --scope global
```

Replace `opencode` with `codex`, `claude`, `cursor`, `windsurf`, or `devin` as appropriate.
Use `--scope project` (the default) to install into the current project instead of the
user-wide skill directory. Use `--agent all --scope global` to install into all configured
global skill directories.

4. Verify:

```powershell
creator --version
creator doctor --json
python skill\youtube-creator-agent\scripts\install.py --list
```

If `creator` is not found, confirm that the CLI was installed into the Python environment
used by the agent and that its Scripts directory is on `PATH`. Open a new terminal after
changing `PATH`.

### POSIX example

From the extracted source root:

```bash
python3 -m pip install .
python3 skill/youtube-creator-agent/scripts/install.py --agent opencode --scope global
creator --version
creator doctor --json
```

### PyPI alternative

If and only if version `0.10.0a0` has been published to PyPI, the CLI can be installed with
`python -m pip install universal-creator-agent==0.10.0a0` instead of `python -m pip install .`.
A GitHub Release does not imply a PyPI release. The skill ZIP and installer are still required.

### Preview UI

The preview UI is served from `web/preview/` in the extracted source tree and is not bundled
in the wheel. Keep the source tree in place and set `PYTHONPATH` to its `src/` directory
before running `creator edit preview <project> --json`.

## Not included in this alpha (stated plainly)

- **No live internet research.** Research, trend, competitor and idea-discovery commands operate
  **only** on snapshot/fixture files you supply or that ship in `examples/`. They do not crawl,
  search, or fetch live web sources, and they do not report live event/news data.
- **No live public-channel retrieval.** `channel connect --from-file FILE` reads a local snapshot.
  The live transport's `channels.list` uses `mine=True`, so it can only ever describe **your own
  authenticated channel** — it cannot look up a third-party public channel by URL or handle.
- **No thumbnail generation.** Nothing in this alpha creates an image. `youtube thumbnail` uploads
  an image you supply (≤ 2 MB, any aspect ratio you choose — YouTube crops client-side).

---

## Agent runtimes — actual verified status

| Runtime | Status | Evidence |
|---|---|---|
| **OpenCode** | **VERIFIED end-to-end** | Skill installed via documented installer; discovered by `opencode debug skill`; invoked in a real session; `creator --version` returned `creator 0.10.0a0 (contract 1.0)`. |
| **Codex** | **PARTIALLY VERIFIED** | Skill installed and instructions loaded; Codex attempted the requested command but its sandbox Python lacks `creator_core` (install `creator` into the Python your Codex sandbox uses). |
| **Claude Code** | **NOT VERIFIED** | Skill files installed and spec-compliant; not exercised because the runtime requires authentication (`/login` or `ANTHROPIC_API_KEY`) which is not present. |
| **Cursor** | **NOT VERIFIED** | Runtime not installed in the test environment. |
| **Windsurf** | **NOT VERIFIED** | Runtime not installed in the test environment. |

**No claim of universal cross-agent compatibility is made.** The CLI (`creator … --json`) is the
only execution contract and works from any shell that has it on `PATH`.

---

## Capability matrix

| Capability | Status |
|---|---|
| Research / ideas / trends / competitors / channel analysis | ✅ Implemented and tested — **offline only**, from snapshot/fixture files you supply |
| Media inspect / analyze / search (FFmpeg + FFprobe) | ✅ Implemented and tested |
| Public channel retrieval (live, by URL/handle) | ❌ **Not implemented** — live transport uses `channels.list(mine=True)`, so only your own authenticated channel is reachable; `channel connect --from-file` reads a local snapshot |
| Optional local ASR transcription (`faster-whisper`) | ⚠️ Implemented; requires optional extra; not exercised on real corpora in this release |
| Editing: versioned EDLs, render, QA, exports | ✅ Implemented and tested; verified end-to-end on real FFmpeg |
| Preview UI + agent-assisted revision | ✅ Implemented and tested |
| Shorts ranking / 9:16 reframe / captions | ✅ Implemented and tested |
| Titles / descriptions / subtitles / metadata generation | ✅ Implemented and tested |
| Thumbnail **generation** (create/render/template an image) | ❌ **Not implemented** — no image is produced by this alpha |
| Thumbnail **upload** (`youtube thumbnail --image FILE`) | ✅ Implemented and tested — uploads an image **you** supply (≤ 2 MB); no generation, preview, revision, or format/dimension conversion |
| Multilingual captions (SRT/VTT, offline translation) | ✅ Implemented and tested; translations are `estimated` |
| Turn-only diarization (`--provider heuristic`) | ⚠️ Implemented; emits `speaker_turn_N` hypotheses only, never identities |
| Analytics ingestion / cited reports | ✅ Fixture-first and tested; `--live` unverified |
| Approval-gated learning | ✅ Implemented and tested; inert until explicitly approved |
| YouTube publishing (stub transport) | ✅ Implemented and tested; local stub only |
| YouTube publishing (live Data API) | ❌ **Blocked** — not verified against a real account; OAuth optional |
| Quota ledger / accounting boundary | ✅ Implemented and tested; single-owner accounting |
| Read-only MCP adapter | ✅ Implemented and tested; optional convenience |
| VoiceDNA — registration, consent, reference analysis, non-neural generation | ✅ Implemented and tested |
| VoiceDNA — **neural voice cloning** (OpenVoice v2, Chatterbox, XTTS-v2, F5, Fish) | 🧪 **Experimental / blocked** — requires runtime + verified checkpoints + licensed model + owner consent; fail closed |
| VoiceDNA — real neural inference (V-01) | ❌ **Blocked** — not executed; no fabricated audio |
| Word alignment — estimated (character/pause heuristic) | ✅ Implemented and tested; always labelled `estimated` |
| Word alignment — **measured** WhisperX (V-05) | ❌ **Blocked** — not benchmarked; never labelled `measured` |

Legend: ✅ implemented & tested · ⚠️ implemented, needs extra / limited testing · 🧪 experimental,
fail closed · ❌ blocked by owner/runtime prerequisite.

---

## Security & privacy

- **No secrets are distributed.** No OAuth client secrets, tokens, keys, machine-specific paths,
  or personal data are present in the artifacts (verified by automated scan: 0 fatal findings).
- **Credentials stay on your machine**, in the OS keyring, and are never shipped or logged.
- **Every state-changing action requires an explicit, single-use approval token** bound to the
  full canonical request (upload, publish, thumbnail, voice register/generate/delete).
- **Prompt injection is treated as data.** Transcripts, comments, descriptions and media metadata
  are never interpreted as instructions.
- **Analytics findings are correlations with cited evidence**, never predictions, guarantees of
  views/virality/revenue, or proof of cause.
- **Path confinement, symlink-escape rejection, and audit logging** are enforced across media,
  preview, and artifact-manifest paths on both Windows and POSIX.
- Scanners executed on the release source: **Bandit 0 High / 1 Medium / 12 Low** (the Medium is the
  `torch.load` in the manifest-gated, owner-blocked neural path), **Mypy** 191 findings
  (pre-existing; not a declared gate), **pip-audit** closure-relevant subset triaged.
- See `THIRD-PARTY-NOTICES.md`, `FINAL_SECURITY_ACCEPTANCE.md`, `FINAL_DEPENDENCY_AUDIT.md`.

---

## Known limitations (stated plainly, not green-washed)

1. **No live internet research.** Research, trend, competitor and idea-discovery commands are
   **offline**: they read snapshot/fixture files you supply or that ship in `examples/`. They do
   not search, crawl, or fetch live web/news sources, and they do not report live event data.
2. **No live public-channel retrieval.** `channel connect --from-file FILE` reads a local
   snapshot; the live transport's `channels.list` uses `mine=True` and can only describe your own
   authenticated channel. There is no "give me a channel URL" lookup.
3. **No thumbnail generation.** Nothing here creates an image. `youtube thumbnail` uploads an
   image you already have (≤ 2 MB). No generation, templates, text overlay, size/aspect
   normalization, preview, or revision.
4. **No neural voice cloning.** The alpha ships the *plumbing* (consent, profiles, manifests,
   provider gating, license evidence) but not the inference. Provider runs require you to install
   the runtime, obtain licensed checkpoints, and provide consent.
5. **No measured word alignment.** Word-level timing is estimated from duration and speech/pause
   windows, and is always labelled `estimated`. WhisperX measured alignment is not benchmarked.
6. **Live YouTube publishing unverified.** The stub transport is deterministic and offline; the
   live Data API path is untested against a real account.
7. **Partial cross-agent verification.** Only OpenCode is verified end-to-end.
8. **Core wheel excludes skill + preview UI.** Install from the clean-source ZIP for the full
   package (see Installation §B/§C).
9. **Optional ASR/diarization are not exercised on real corpora in this release.**
10. **Alpha quality.** Expect rough edges; the CLI JSON contract (v1.0) is stable, but command
    surface may change between alpha builds.

---

## Legal boundaries

- **Local editing requires authorized source media.** Do not feed footage you have no right to
  modify. All renders are new files; source files are never modified and their SHA-256 is recorded.
- **Generated deliverables are stored locally** in `.creator/`. Nothing is uploaded implicitly.
- **Publishing requires your explicit authorization** — a deliberate approval token per action.
- **YouTube OAuth is optional.** The entire local workflow works without it.
- **Never clone or imitate a third party's voice** without their documented consent.
- FFmpeg is installed by you and is not redistributed; see `THIRD-PARTY-NOTICES.md`.

---

## Feedback and bug reports

Please report bugs and feedback through the project's issue tracker:

- Include the exact command (`creator … --json`), OS, Python version (`creator doctor --json`
  output), and the JSON payload or stderr.
- **Never paste OAuth tokens, client secrets, or API keys.** Redact them before posting.
- State whether you were using the stub or live transport, and which agent runtime you used.
- For voice/consent issues, describe the workflow, not the audio content.

See `FINAL_INDEPENDENT_REVIEW_REMEDIATION.md` and the `FINAL_*.md` acceptance reports for the
evidence behind each status above.