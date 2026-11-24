"""`creator init` — create the `.creator` workspace with default configuration."""

from __future__ import annotations

from pathlib import Path

from creator_core.cli.errors import EXIT_OK
from creator_core.cli.main import CliContext, emit_json
from creator_core.security.audit import AuditLog
from creator_core.workspace.layout import ensure_workspace, load_config


def run_init(ctx: CliContext, args) -> int:
    base = Path(args.dir) if getattr(args, "dir", None) else ctx.workspace
    ws = ensure_workspace(base, create=True)
    config = load_config(ws)
    audit = AuditLog(ws.root)
    audit.log("workspace.init", base=str(base))

    payload = {
        "command": "init",
        "ok": True,
        "workspace": str(ws.root),
        "directories": [
            str(p)
            for p in (
                ws.root,
                ws.profiles,
                ws.projects,
                ws.research,
                ws.publishing,
                ws.logs,
                ws.tmp,
            )
        ],
        "config_defaults": config,
    }
    if ctx.json_mode:
        emit_json(payload)
    else:
        print(f"workspace ready: {ws.root}")
    return EXIT_OK
