"""`creator youtube …` — private-first publishing with explicit approval (Phase 8).

The stub transport is the default: no command in this module contacts YouTube unless the caller
explicitly opts into live mode (`--live` or config `youtube.transport = live`) *and* OAuth
credentials are present. Every printed payload states whether it came from the stub.
"""

from __future__ import annotations

from creator_core.cli.errors import ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.workspace.layout import ensure_workspace, load_config
from creator_core.youtube import publish as core


def _print_connect(payload: dict) -> None:
    print(f"youtube connect — transport={payload['transport']} live={payload['live']}")
    if payload.get("channel"):
        print(f"  channel: {payload['channel'].get('title')} ({payload['channel'].get('id')})")
    print(f"  {payload['note']}")


def _print_status(payload: dict) -> None:
    print(f"youtube status — transport={payload['transport']} videos={payload['count']}")
    for video in payload["videos"]:
        print(
            f"  {video['video_id']}  state={video['state']}  privacy={video['privacy']}  "
            f"live={video.get('live')}  short={video.get('is_short')}"
        )
    quota = payload["quota"]
    print(
        f"  quota: {quota['used']}/{quota['daily_limit']} units (UTC {quota['date']}), "
        f"remaining {quota['remaining']}"
    )


def _print_upload(payload: dict) -> None:
    print(
        f"youtube upload — {payload['video_id']} state={payload['state']} "
        f"privacy={payload['privacy']} live={payload['live']}"
    )
    if payload.get("publish_at"):
        print(f"  scheduled for {payload['publish_at']}")
    print(f"  {payload['note']}")


def _print_verify(payload: dict) -> None:
    print(
        f"youtube verify — {payload['video_id']} state={payload['state']} "
        f"remote_status={payload['remote_status']}"
    )
    print(f"  {payload['note']}")


def _print_publish(payload: dict) -> None:
    print(
        f"youtube publish — {payload['video_id']} is now {payload['state']} "
        f"(live={payload['live']})"
    )
    print(f"  {payload['note']}")


def _print_thumbnail(payload: dict) -> None:
    print(
        f"youtube thumbnail — {payload['video_id']} ← {payload['thumbnail']} "
        f"(live={payload['live']})"
    )


def _print_quota(payload: dict) -> None:
    quota = payload["quota"]
    print(
        f"youtube quota — {quota['used']}/{quota['daily_limit']} units used on UTC "
        f"{quota['date']} (remaining {quota['remaining']})"
    )
    for op in quota["operations"]:
        print(f"  {op['at']}  {op['operation']}  +{op['units']}  {op['detail']}")


def run(ctx: CliContext, args) -> int:
    ws = ensure_workspace(ctx.workspace)
    config = load_config(ws)
    sub = args.youtube_command

    if sub == "connect":
        payload = core.connect(
            ws,
            config,
            client_secrets=getattr(args, "client_secrets", None),
            live=True if getattr(args, "live", False) else None,
        )
        printer = _print_connect
    elif sub == "status":
        payload = core.status(ws, config, video_id=getattr(args, "video_id", None))
        printer = _print_status
    elif sub == "quota":
        payload = core.quota(ws, config)
        printer = _print_quota
    elif sub == "upload":
        payload = core.upload(
            ws,
            config,
            project_id=args.project,
            source=args.source,
            title=args.title,
            description=getattr(args, "description", "") or "",
            tags=list(getattr(args, "tags", None) or []),
            category=str(args.category),
            privacy=args.privacy,
            publish_at=getattr(args, "publish_at", None),
            thumbnail=getattr(args, "thumbnail", None),
            parent=getattr(args, "parent", None),
            short_id=getattr(args, "short", None),
            approval_token=getattr(args, "approval_token", None),
            generate_approval=bool(args.approve),
            live=True if getattr(args, "live", False) else None,
        )
        printer = _print_upload
    elif sub == "verify":
        payload = core.verify(ws, config, video_id=args.video_id)
        printer = _print_verify
    elif sub == "publish":
        payload = core.publish(
            ws,
            config,
            video_id=args.video_id,
            approval_token=getattr(args, "approval_token", None),
            generate_approval=bool(args.approve),
            live=True if getattr(args, "live", False) else None,
        )
        printer = _print_publish
    elif sub == "thumbnail":
        payload = core.set_thumbnail(
            ws,
            config,
            video_id=args.video_id,
            image=args.image,
            approval_token=getattr(args, "approval_token", None),
            generate_approval=bool(args.approve),
        )
        printer = _print_thumbnail
    else:  # pragma: no cover - argparse restricts choices
        raise ValidationError(f"unknown youtube command: {sub}")

    if ctx.json_mode:
        emit_json(payload)
    else:
        printer(payload)
    return 0
