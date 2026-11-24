"""`creator analyze …` — channel/content/audience analysis."""

from __future__ import annotations

import json

from creator_core.cli.errors import ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.workspace.layout import ensure_workspace
from creator_core.youtube.channel import analyze


def run(ctx: CliContext, args) -> int:
    ws = ensure_workspace(ctx.workspace)
    if args.analyze_command != "channel":
        raise ValidationError(
            f"analysis target not available yet: {args.analyze_command}",
            hint="Phase 3 adds content/audience analysis",
        )
    path = ws.research / "channel_snapshot.json"
    if not path.is_file():
        raise ValidationError(
            "no channel snapshot found",
            hint="run `creator channel connect --from-file <snapshot.json>`",
        )
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"snapshot unreadable: {exc}") from exc

    profile = {}
    ppath = ws.profiles / "profile.json"
    if ppath.is_file():
        profile = json.loads(ppath.read_text(encoding="utf-8"))
    payload = analyze(profile, snapshot)

    if ctx.json_mode:
        emit_json(payload)
    else:
        print(
            f"channel analysis — {len(payload['findings'])} findings "
            f"(source: {payload['snapshot_source']})"
        )
        for f in payload["findings"]:
            print(f"  [{f['kind']}] {f['statement']}")
    return 0
