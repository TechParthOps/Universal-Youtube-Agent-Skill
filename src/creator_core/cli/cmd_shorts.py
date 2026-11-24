"""`creator shorts …` — draft generation and explicit creator approval (Phase 7)."""

from __future__ import annotations

from creator_core.cli.errors import ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.shorts.engine import generate, review
from creator_core.workspace.layout import ensure_workspace, load_config


def _print_generate(payload: dict) -> None:
    print(
        f"shorts generate — {payload['count']} draft(s) from "
        f"{payload['candidate_count']} candidate(s), project {payload['project_id']}"
    )
    for short in payload["shorts"]:
        print(
            f"  {short['short_id']}  {short['duration_seconds']}s  "
            f"{short['width']}x{short['height']}  focus={short['focus_mode']}  "
            f"QA={'pass' if short['qa_ok'] else 'issues'}"
        )
        print(f"    hook: {short['hook']}")
        print(f"    file: {short['output']}")
    if payload["needs_approval"]:
        print(f"  approval required: {payload['approval_hint']}")


def _print_review(payload: dict) -> None:
    if "shorts" in payload:
        print(f"shorts review — {payload['count']} draft(s) for {payload['project_id']}")
        for item in payload["shorts"]:
            print(
                f"  {item['short_id']}  {item['status']}  "
                f"score={item['score']}  QA={'pass' if item['qa_ok'] else 'issues'}"
            )
    else:
        print(f"shorts review — {payload['short_id']} approved for review")
        print("  publishing still requires fresh upload approval and a public parent video")


def run(ctx: CliContext, args) -> int:
    ws = ensure_workspace(ctx.workspace)
    config = load_config(ws)
    sub = args.shorts_command
    if sub == "generate":
        payload = generate(
            ws,
            args.source,
            project_id=getattr(args, "project", None),
            config=config,
            target_duration=float(args.target),
            limit=int(args.limit),
            focus_mode=args.focus,
            provider_enabled=not args.no_provider,
            transcribe=bool(args.transcribe),
        )
    elif sub == "review":
        payload = review(
            ws,
            args.project,
            short_id=getattr(args, "short", None),
            approval_token=getattr(args, "approval_token", None),
            generate_approval=bool(args.approve),
            config=config,
        )
    else:  # pragma: no cover - argparse restricts choices
        raise ValidationError(f"unknown shorts command: {sub}")

    if ctx.json_mode:
        emit_json(payload)
    elif sub == "generate":
        _print_generate(payload)
    else:
        _print_review(payload)
    return 0
