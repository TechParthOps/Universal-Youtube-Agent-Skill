"""`creator profile …` — creator profile lifecycle and approval-gated memory."""

from __future__ import annotations

import json
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.profiles.profile import ProfileManager
from creator_core.security.audit import AuditLog
from creator_core.workspace.layout import ensure_workspace


def _manager(ctx: CliContext) -> ProfileManager:
    ws = ensure_workspace(ctx.workspace)
    return ProfileManager(ws, AuditLog(ws.root))


def _parse_assignments(items: list[str]) -> dict:
    out: dict = {}
    for item in items:
        if "=" not in item:
            raise ValidationError(
                f"invalid assignment: {item}", hint="use KEY=VALUE, e.g. channel.niche=gaming"
            )
        key, raw = item.split("=", 1)
        if not key.strip():
            raise ValidationError(f"empty key in assignment: {item}")
        try:
            value = json.loads(raw) if raw[:1] in "[{" or raw in ("true", "false", "null") else raw
        except json.JSONDecodeError:
            value = raw
        parts = key.strip().split(".")
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValidationError(f"cannot set nested field under {part}")
        node[parts[-1]] = value
    return out


def run(ctx: CliContext, args) -> int:
    sub = args.profile_command
    mgr = _manager(ctx)

    if sub == "init":
        data = None
        if getattr(args, "file", None):
            path = Path(args.file)
            if not path.is_file():
                raise ValidationError(f"profile file not found: {path}")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValidationError(f"invalid profile JSON: {exc}") from exc
        profile = mgr.create(data, overwrite=getattr(args, "force", False))
        payload = {"command": "profile init", "ok": True, "profile": profile}

    elif sub == "show":
        profile = mgr.load(getattr(args, "version", None))
        payload = {"command": "profile show", "ok": True, "profile": profile}

    elif sub == "set":
        changes = _parse_assignments(args.assignments)
        profile = mgr.set_fields(changes)
        payload = {
            "command": "profile set",
            "ok": True,
            "updated_fields": sorted(changes),
            "profile": profile,
        }

    elif sub == "changes":
        changes = mgr.pending_changes()
        payload = {
            "command": "profile changes",
            "ok": True,
            "count": len(changes),
            "pending": changes,
        }

    elif sub == "apply":
        change = mgr.apply_change(
            args.change_id,
            approval_token=getattr(args, "approval_token", None),
            generate_approval=getattr(args, "approve", False),
        )
        payload = {"command": "profile apply", "ok": True, "change": change}
    else:  # pragma: no cover - argparse restricts choices
        raise ValidationError(f"unknown profile command: {sub}")

    if ctx.json_mode:
        emit_json(payload)
    else:
        print(f"profile: {payload['command']} — ok")
        if sub in ("init", "show", "set"):
            p = payload["profile"]
            print(
                f"  {p['profile_id']}  niche={p['channel']['niche'] or '-'}  "
                f"category={p['channel']['category']}"
            )
        elif sub == "changes":
            for c in payload["pending"]:
                print(f"  {c['id']}  {c['kind']}.{c['field']}  {c['reason']}")
    return 0
