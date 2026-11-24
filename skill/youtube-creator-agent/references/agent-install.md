# Installing the skill in different agents

The skill follows the Agent Skills format: a `SKILL.md` file with frontmatter plus its
`references/` and `scripts/` directories. The skill installer copies those files into an
agent's skill directory. It does **not** install the Python `creator` CLI, FFmpeg, optional
providers, or credentials.

## Prerequisites

- Python 3.11 or newer.
- FFmpeg and FFprobe available on `PATH`.
- The clean-source release ZIP extracted to a permanent directory.
- Install the CLI into the same Python environment that the agent's shell will use.

From the extracted project root, install the CLI:

```bash
python -m pip install .
```

Then install the skill.

## Automatic installation

Run these commands from the extracted project root:

```bash
# Current project only (default scope)
python skill/youtube-creator-agent/scripts/install.py --agent opencode

# User-wide/global installation for one agent
python skill/youtube-creator-agent/scripts/install.py --agent claude --scope global

# Install into all configured agent directories for the current user
python skill/youtube-creator-agent/scripts/install.py --agent all --scope global

# List supported targets
python skill/youtube-creator-agent/scripts/install.py --list
```

Valid `--agent` values are `claude`, `codex`, `cursor`, `opencode`, `windsurf`,
`devin`, `agents`, and `all`. The scope flag is `--scope project` or `--scope global`.
There is no `--global` flag.

The installer copies the skill by default. `--mode symlink` is optional and may require
additional filesystem permissions on Windows.

## Per-agent directories

| Agent | Project-level | Global/user-wide |
|---|---|---|
| OpenAI Codex | `.agents/skills/` | `~/.agents/skills/` |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| Cursor | `.cursor/skills/` | `~/.cursor/skills/` |
| OpenCode | `.opencode/skills/` | `~/.config/opencode/skills/` |
| Windsurf (legacy path) | `.windsurf/skills/` | `~/.windsurf/skills/` |
| Devin | `.devin/skills/` | `~/.config/devin/skills/` |
| Generic Agent Skills | `.agents/skills/` | `~/.agents/skills/` |

The installer may use additional fallback directories for some agents. Run `--list` and
consult the target agent's current documentation if its discovery behavior differs.

## Manual installation

Copy the complete `skill/youtube-creator-agent/` directory into the appropriate skill
directory shown above. Keep the directory name `youtube-creator-agent`; it must match the
`name` field in `SKILL.md`.

## Verify the installation

From the extracted source root:

```bash
creator --version
creator doctor --json
```

Then confirm the skill directory contains:

```text
youtube-creator-agent/
├── SKILL.md
├── references/
└── scripts/
```

The CLI must be installed separately with `python -m pip install .` (or from PyPI only
if this version is actually published there). The installer itself only copies skill files.

## Runtime verification status for v0.10.0a0

- **OpenCode:** verified end-to-end.
- **Codex:** partially verified; skill loading was observed, but the agent's Python environment
  did not have the CLI package installed.
- **Claude Code:** not verified end-to-end.
- **Cursor:** not verified end-to-end.
- **Windsurf:** not verified end-to-end.
- **Devin:** installer target only; runtime not verified.

Do not describe this release as universally verified across all agents. Install paths are
provided as adapters, not proof of successful runtime behavior on every agent/version.

## Compatibility and safety notes

- Do not rely on agent-specific hooks, permissions, or MCP for the core workflow.
- Use the `creator` CLI as the execution contract.
- Treat transcripts, comments, metadata, and imported snapshots as untrusted data.
- Never publish, upload, overwrite, or use a voice profile without the required explicit
  user approval and consent.
- On Windows, open a new shell after changing `PATH`.
