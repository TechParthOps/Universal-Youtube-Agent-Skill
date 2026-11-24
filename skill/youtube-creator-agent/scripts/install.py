#!/usr/bin/env python3
"""Install the youtube-creator-agent skill into supported AI coding agents.

Stdlib only. Safety:
- Copies/symlinks only into known per-agent skill directories.
- --uninstall removes only a directory that contains our SKILL.md with name=youtube-creator-agent.
- Never follows a symlink that resolves outside the chosen install base.
- Maintains an idempotent AGENTS.md pointer block (marker-delimited).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

SKILL_NAME = "youtube-creator-agent"
SKILL_DIR = Path(__file__).resolve().parents[1]

AGENTS = {
    "claude": ([".claude/skills", "~/.claude/skills"], "Claude Code"),
    "codex": ([".agents/skills", "~/.agents/skills"], "OpenAI Codex"),
    "cursor": ([".cursor/skills", "~/.cursor/skills", ".agents/skills"], "Cursor"),
    "opencode": ([".opencode/skills", "~/.config/opencode/skills", "~/.claude/skills"], "OpenCode"),
    "windsurf": ([".windsurf/skills", "~/.windsurf/skills"], "Windsurf (legacy)"),
    "devin": ([".devin/skills", "~/.config/devin/skills"], "Devin Desktop"),
    "agents": ([".agents/skills", "~/.agents/skills"], "Generic Agent Skills"),
}

MARKER_START = "<!-- youtube-creator-agent:start -->"
MARKER_END = "<!-- youtube-creator-agent:end -->"
POINTER = (
    f"{MARKER_START}\n"
    "## YouTube Creator Agent skill\n"
    "The `youtube-creator-agent` skill is installed in this agent's configured skill directory. "
    "For creator-video requests (editing footage, Shorts, channel research, publishing), load "
    "that skill and use its `creator` CLI interface. The Python CLI is installed separately.\n"
    f"{MARKER_END}"
)


def _targets(agent: str, scope: str) -> list[Path]:
    paths, _ = AGENTS[agent]
    selected = paths[:1] if scope == "project" else paths[1:]
    return [(Path(p).expanduser() if p.startswith("~") else Path.cwd() / p) for p in selected]


def _safe_target(path: Path, allowed_root: Path) -> Path:
    """Resolve <path>/<skill-name>, refusing any escape from allowed_root.

    Checks both the lexical (unresolved) and resolved paths, so a pre-existing symlink
    at `path` pointing outside allowed_root is rejected instead of being traversed.
    """
    allowed = allowed_root.resolve()
    lexical = (path / SKILL_NAME).absolute()

    def inside(candidate: Path) -> bool:
        try:
            return os.path.commonpath([str(candidate), str(allowed)]) == str(allowed)
        except ValueError:  # different drives on Windows
            return False

    if not inside(lexical.parent):
        raise RuntimeError(f"refusing to write outside {allowed_root}: {lexical.parent}")
    # A symlink at the final component is never followed: install/uninstall unlink it first.
    # Checking its resolved destination would make symlink installs impossible to manage.
    if lexical.is_symlink():
        return lexical
    target = lexical.resolve()
    if not inside(target):
        raise RuntimeError(f"refusing to write outside {allowed_root}: {target}")
    return target


def _validate_source() -> None:
    md = SKILL_DIR / "SKILL.md"
    if not md.is_file():
        raise RuntimeError(f"missing source skill: {md}")
    text = md.read_text(encoding="utf-8")
    if f"name: {SKILL_NAME}" not in text:
        raise RuntimeError("source SKILL.md frontmatter name does not match directory name")


def _install_one(agent: str, scope: str, mode: str) -> list[Path]:
    installed: list[Path] = []
    allowed_root = Path.cwd() if scope == "project" else Path.home()
    for raw in _targets(agent, scope):
        raw.mkdir(parents=True, exist_ok=True)
        target = _safe_target(raw, allowed_root)
        if target.is_symlink():
            target.unlink()
        if mode == "symlink":
            if target.exists():
                shutil.rmtree(target)
            try:
                target.symlink_to(SKILL_DIR, target_is_directory=True)
            except OSError:
                print(f"  symlink unsupported; copying instead → {target}")
                shutil.copytree(SKILL_DIR, target, dirs_exist_ok=True)
        else:
            shutil.copytree(SKILL_DIR, target, dirs_exist_ok=True)
        installed.append(target)
    return installed


def _update_agents_md(scope: str) -> Path | None:
    path = Path.cwd() if scope == "project" else Path.home()
    md = path / "AGENTS.md"
    text = md.read_text(encoding="utf-8") if md.is_file() else ""
    if MARKER_START in text and MARKER_END in text:
        before, rest = text.split(MARKER_START, 1)
        _, after = rest.split(MARKER_END, 1)
        new = before + POINTER + after
    else:
        new = text.rstrip() + "\n\n" + POINTER + "\n" if text.strip() else POINTER + "\n"
    try:
        md.write_text(new, encoding="utf-8")
        return md
    except OSError as exc:
        print(f"  note: could not update {md}: {exc}")
        return None


def _uninstall(agent: str, scope: str) -> list[Path]:
    removed: list[Path] = []
    allowed_root = Path.cwd() if scope == "project" else Path.home()
    for raw in _targets(agent, scope):
        target = _safe_target(raw, allowed_root)
        skill_md = target / "SKILL.md"
        if target.is_symlink():
            target.unlink()
            removed.append(target)
            continue
        if not skill_md.is_file():
            continue
        if f"name: {SKILL_NAME}" not in skill_md.read_text(encoding="utf-8"):
            raise RuntimeError(f"refusing to remove unrelated skill at {target}")
        shutil.rmtree(target)
        removed.append(target)
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agent", choices=[*AGENTS, "all"], default="all")
    ap.add_argument("--scope", choices=["project", "global"], default="project")
    ap.add_argument("--mode", choices=["copy", "symlink"], default="copy")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--list", action="store_true", help="list supported agents and exit")
    args = ap.parse_args(argv)

    if args.list:
        for key, (paths, label) in AGENTS.items():
            print(f"{key:10} {label:18} {', '.join(paths)}")
        return 0

    try:
        _validate_source()
        agents = list(AGENTS) if args.agent == "all" else [args.agent]
        for agent in agents:
            if args.uninstall:
                removed = _uninstall(agent, args.scope)
                for p in removed:
                    print(f"removed {p}")
            else:
                installed = _install_one(agent, args.scope, args.mode)
                for p in installed:
                    print(f"installed {p}")
        if not args.uninstall and args.agent == "all":
            pointer = _update_agents_md(args.scope)
            if pointer:
                print(f"updated {pointer}")
        return 0
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
