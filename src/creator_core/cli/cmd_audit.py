"""`creator audit show` — read recent audit events (append-only log)."""

from __future__ import annotations

from creator_core.cli.errors import EXIT_OK
from creator_core.cli.main import CliContext, emit_json
from creator_core.security.audit import AuditLog
from creator_core.workspace.layout import ensure_workspace


def run(ctx: CliContext, args) -> int:
    ws = ensure_workspace(ctx.workspace)
    audit = AuditLog(ws.root)
    events = audit.recent(limit=max(1, min(int(args.limit), 1000)))
    payload = {
        "command": "audit show",
        "ok": True,
        "log": str(audit.path),
        "count": len(events),
        "events": events,
    }
    if ctx.json_mode:
        emit_json(payload)
    else:
        for ev in events:
            print(f"{ev.get('ts')}  {ev.get('event')}  {ev.get('workspace_base', '')}".rstrip())
    return EXIT_OK
