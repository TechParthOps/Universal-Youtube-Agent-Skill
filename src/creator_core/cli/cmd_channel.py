"""`creator channel …` — connect/status (local snapshot in P2; OAuth in P8)."""

from __future__ import annotations

import json
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.security.audit import AuditLog
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import ensure_workspace
from creator_core.youtube.channel import normalize_snapshot


def _snapshot_path(ctx: CliContext) -> Path:
    ws = ensure_workspace(ctx.workspace)
    return ws.research / "channel_snapshot.json"


def run(ctx: CliContext, args) -> int:
    ws = ensure_workspace(ctx.workspace)
    audit = AuditLog(ws.root)
    path = _snapshot_path(ctx)

    if args.channel_command == "connect":
        source_file = getattr(args, "from_file", None)
        if not source_file:
            raise ValidationError(
                "--from-file <snapshot.json> is required for fixture-based channel analysis",
                hint="for live OAuth use `creator youtube connect --live --client-secrets FILE`; "
                "a demo fixture is bundled at examples/channel_snapshot_fixture.json",
            )
        p = Path(source_file)
        if not p.is_file():
            raise ValidationError(f"snapshot file not found: {p}")
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid snapshot JSON: {exc}") from exc
        snapshot = normalize_snapshot(raw, source="local_fixture")
        atomic_write_json(path, snapshot)
        audit.log(
            "channel.connect.local_fixture",
            channel_id=snapshot["channel"]["id"],
            videos=snapshot["baseline"]["video_count"],
        )
        payload = {
            "command": "channel connect",
            "ok": True,
            "mode": "local_fixture",
            "snapshot": path,
            "verified": False,
            "channel": snapshot["channel"],
            "baseline": snapshot["baseline"],
            "note": "Local fixture only; no live YouTube connection has been made.",
        }

    elif args.channel_command == "status":
        if path.is_file():
            snap = json.loads(path.read_text(encoding="utf-8"))
            payload = {
                "command": "channel status",
                "ok": True,
                "connected": False,
                "local_snapshot": True,
                "source": snap.get("source"),
                "channel": snap.get("channel"),
                "baseline": snap.get("baseline"),
                "oauth": {
                    "connected": False,
                    "hint": "use `creator youtube connect --live --client-secrets FILE`",
                },
            }
        else:
            payload = {
                "command": "channel status",
                "ok": True,
                "connected": False,
                "local_snapshot": False,
                "hint": "creator channel connect --from-file examples/channel_snapshot_fixture.json",
                "oauth": {
                    "connected": False,
                    "hint": "use `creator youtube connect --live --client-secrets FILE`",
                },
            }
    else:  # pragma: no cover
        raise ValidationError(f"unknown channel command: {args.channel_command}")

    if ctx.json_mode:
        emit_json(payload)
    else:
        print(f"channel: {payload['command']} — ok")
        if payload.get("channel"):
            print(f"  {payload['channel']['title']} (local snapshot, not live)")
    return 0
