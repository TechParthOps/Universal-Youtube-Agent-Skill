"""YouTube publishing orchestration (Phase 8).

Hard rules (ADR-011/ADR-012):
- Private-first: `upload`/`schedule` never create a public video. Public visibility requires a
  separate, freshly approved `publish` after processing is verified.
- No upload without a fresh action+target-bound HMAC token created by an explicit creator action.
- Shorts require their own recorded creator approval AND a parent video that is already public.
- Quota is pre-flight checked and charged per operation; the ledger is inspectable.
- Every state change is audit-logged; token values are never logged or returned.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import (
    ApprovalRequiredError,
    AuthRequiredError,
    ProviderError,
    ValidationError,
)
from creator_core.media.ingest import sha256_file, validate_media_path
from creator_core.security.approval import require_approval
from creator_core.security.audit import AuditLog
from creator_core.security.paths import resolve_within
from creator_core.shorts.engine import SHORT_ID_RE
from creator_core.workspace.layout import project_dir
from creator_core.youtube.quota import DEFAULT_DAILY_LIMIT, QuotaLedger
from creator_core.youtube.state import PublishState
from creator_core.youtube.transport import SCOPES, ResumableInterrupted, get_transport

PROCESSING_STATES = {"uploaded", "processing", "processed"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _daily_limit(config: dict) -> int:
    return int((config.get("youtube") or {}).get("daily_units", DEFAULT_DAILY_LIMIT))


def _prefer(live: bool | None) -> str | None:
    """Explicit `--live` wins; otherwise let config `youtube.transport` decide (stub default)."""
    return "live" if live else None


def _require_approval(
    ws,
    action: str,
    target: str,
    approval_token: str | None,
    *,
    generate: bool,
    request: dict | None = None,
) -> dict:
    """Bind approval to the full request (metadata, media hash) and enforce one-time use."""
    approval = require_approval(
        ws,
        action,
        target,
        approval_token,
        generate=generate,
        payload=request,
        hint="rerun with --approve after confirming the exact title, privacy and target",
    )
    return {"fingerprint": approval["fingerprint"], "expires": approval["expires"]}


def _short_gate(
    ws, project_id: str, short_id: str | None, parent_id: str | None, state: PublishState
) -> tuple[str | None, dict]:
    """Return (short_id, gate info). Must pass immediately before any upload."""
    if not short_id:
        return None, {"is_short": False}
    project = project_dir(ws, project_id)
    if not isinstance(short_id, str) or not SHORT_ID_RE.fullmatch(short_id):
        raise ValidationError(f"invalid short id: {short_id}", hint="use short_001 style ids")
    shorts_root = resolve_within(project, "shorts")
    short_dir = resolve_within(shorts_root, short_id)
    timeline_path = resolve_within(short_dir, "timeline.json")
    if not timeline_path.is_file():
        raise ValidationError(f"Short {short_id} not found in project {project_id}")
    try:
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Short timeline is unreadable: {exc}") from exc
    gate = timeline.get("publish_gate") or {}
    if not gate.get("creator_approved") or timeline.get("status") != "approved":
        raise ApprovalRequiredError(
            f"Short {short_id} has not been reviewed and approved",
            hint="run `creator shorts review <project> --short <id> --approve` first",
        )
    if not parent_id:
        raise ValidationError(
            "publishing a Short requires --parent <video_id>",
            hint="the parent video must already be public",
        )
    check = state.require_published_parent({"is_short": True, "parent_video_id": parent_id})
    return short_id, {
        "is_short": True,
        "parent_video_id": parent_id,
        "parent": check["parent"],
        "short_timeline": str(timeline_path),
    }


def _body(
    title: str,
    description: str,
    *,
    privacy: str,
    publish_at: str | None,
    tags: list[str],
    category: str,
) -> dict:
    if not title.strip():
        raise ValidationError("title must not be empty")
    if len(title) > 100:
        raise ValidationError("title exceeds YouTube's 100-character limit")
    if len(description) > 5000:
        raise ValidationError("description exceeds YouTube's 5000-character limit")
    if privacy not in ("private", "unlisted", "public"):
        raise ValidationError(f"unsupported privacy: {privacy}")
    status = {"privacyStatus": privacy, "embeddable": True, "selfDeclaredMadeForKids": False}
    if publish_at:
        if privacy != "private":
            raise ValidationError("publishAt scheduling requires privacy=private")
        try:
            scheduled = datetime.fromisoformat(str(publish_at))
        except ValueError as exc:
            raise ValidationError("publishAt must be an ISO-8601 UTC timestamp") from exc
        if scheduled.tzinfo is None or scheduled.utcoffset().total_seconds() != 0:
            raise ValidationError("publishAt must include a UTC offset (Z or +00:00)")
        status["publishAt"] = scheduled.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "snippet": {
            "title": title.strip(),
            "description": description.strip(),
            "tags": [str(t)[:100] for t in tags[:60]],
            "categoryId": str(category),
        },
        "status": status,
    }


def connect(
    ws, config: dict, *, client_secrets: str | None = None, live: bool | None = None
) -> dict:
    transport = get_transport(ws, config, prefer=_prefer(live))
    result = transport.connect(client_secrets_path=Path(client_secrets) if client_secrets else None)
    return {
        "command": "youtube connect",
        "ok": True,
        "transport": transport.name,
        "live": bool(result.get("live")),
        "channel": result.get("channel"),
        "scopes": list(SCOPES) if result.get("live") else [],
        "note": (
            "Live OAuth connected to YouTube"
            if result.get("live")
            else "LOCAL STUB connected — no request will reach YouTube"
        ),
    }


def status(ws, config: dict, *, video_id: str | None = None) -> dict:
    """Local read-only view of publishing state and quota.

    Remote status is deliberately NOT polled here: that is the explicit, quota-charging job of
    `youtube verify`. This keeps `status` free of side effects and avoids querying a live video id
    through the stub transport (or vice versa).
    """
    state = PublishState(ws)
    records = [state.get(video_id)] if video_id else state.find()
    return {
        "command": "youtube status",
        "ok": True,
        "transport": (config.get("youtube") or {}).get("transport") or "stub",
        "count": len(records),
        "videos": records,
        "quota": QuotaLedger(ws).status(daily_limit=_daily_limit(config)),
        "note": "Local state only; run `youtube verify --video-id ID` to poll remote processing.",
    }


def quota(ws, config: dict) -> dict:
    """Report the current UTC quota ledger (read-only)."""
    return {
        "command": "youtube quota",
        "ok": True,
        "quota": QuotaLedger(ws).status(daily_limit=_daily_limit(config)),
    }


def _start_upload(
    ws,
    config: dict,
    *,
    project_id: str,
    media: Path,
    body: dict,
    approval: dict,
    short_id: str | None,
    parent_id: str | None,
    gate: dict,
    thumbnail: str | None,
    live: bool | None,
) -> dict:
    state = PublishState(ws)
    ledger = QuotaLedger(ws)
    transport = get_transport(ws, config, prefer=_prefer(live))
    if not transport.connected():
        raise AuthRequiredError("YouTube is not connected", hint="run `creator youtube connect`")
    # Final gate re-check, immediately before the state-changing call.
    if short_id:
        _short_gate(ws, project_id, short_id, parent_id, state)

    resume = 0
    resume_uri = ""
    result = None
    reservation = ledger.reserve(
        "videos.insert",
        detail=f"upload {media.name}",
        daily_limit=_daily_limit(config),
        attempt=1,
        resumable_session_id=None,
    )
    try:
        for attempt in range(3):
            try:
                result = transport.insert_video(
                    body,
                    media,
                    resume_at=resume,
                    resumable_uri=resume_uri,
                    ledger=ledger,
                    reservation=reservation,
                    daily_limit=_daily_limit(config),
                )
                ledger.settle(reservation, detail=f"upload {result['video_id']}")
                break
            except ResumableInterrupted as exc:
                resume = exc.uploaded_bytes
                resume_uri = exc.resumable_uri
                ledger.mark_unknown(reservation, error="resumable upload interrupted")
                AuditLog(ws.root).log(
                    "youtube.resume",
                    video_id=None,
                    uploaded_bytes=resume,
                    resumable=bool(resume_uri),
                )
                result = None
        if result is None:
            ledger.fail(reservation, error="resumable upload did not complete", request_sent=True)
            raise ProviderError(
                "resumable upload did not complete after 3 attempts",
                hint="retry later; the source file was not modified",
            )
    except ProviderError:
        raise
    except Exception as exc:
        ledger.fail(reservation, error=str(exc), request_sent=True)
        raise
    quota = ledger.status(daily_limit=_daily_limit(config))

    record = {
        "video_id": result["video_id"],
        "project_id": project_id,
        "state": "uploading",
        "short_id": short_id,
        "is_short": bool(short_id),
        "parent_video_id": parent_id,
        "gate": gate,
        "title": body["snippet"]["title"],
        "privacy": body["status"]["privacyStatus"],
        "publish_at": body["status"].get("publishAt"),
        "media_path": str(media),
        "media_sha256": sha256_file(media),
        "remote_status": result.get("status"),
        "transport": transport.name,
        "live": bool(result.get("live")),
        "approval": approval,
        "thumbnail": None,
        "quota": {"used": quota["used"], "remaining": quota["remaining"]},
        "history": [
            {
                "at": _now(),
                "from": "draft",
                "to": "uploading",
                "detail": "resumable insert completed",
            }
        ],
        "created_at": _now(),
        "updated_at": _now(),
    }
    state.save(record)
    state.transition(
        result["video_id"], "processing", detail="upload accepted; awaiting processing"
    )
    if thumbnail:
        thumb_path = validate_media_path(thumbnail, ws, config)
        transport.set_thumbnail(
            result["video_id"],
            thumb_path,
            ledger=ledger,
            daily_limit=_daily_limit(config),
        )
        record = state.get(result["video_id"])
        record["thumbnail"] = str(thumb_path)
        state.save(record)
    return state.get(result["video_id"])


def upload(
    ws,
    config: dict,
    *,
    project_id: str,
    source: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category: str = "22",
    privacy: str = "private",
    publish_at: str | None = None,
    thumbnail: str | None = None,
    parent: str | None = None,
    short_id: str | None = None,
    approval_token: str | None = None,
    generate_approval: bool = False,
    live: bool | None = None,
) -> dict:
    """Upload a render as private (or scheduled private). Never publishes immediately."""
    if privacy == "public":
        raise ValidationError(
            "upload cannot create a public video",
            hint="upload is private-first; run `creator youtube publish --approve` after "
            "`creator youtube verify` confirms processing",
        )
    media = validate_media_path(source, ws, config)
    request = {
        "project_id": project_id,
        "media_name": media.name,
        "media_sha256": sha256_file(media),
        "title": title,
        "description": description or "",
        "tags": list(tags or []),
        "category": str(category),
        "privacy": privacy,
        "publish_at": publish_at,
        "thumbnail": thumbnail,
        "parent": parent,
        "short_id": short_id,
    }
    approval = _require_approval(
        ws,
        "youtube.upload",
        f"{project_id}/{media.name}",
        approval_token,
        generate=generate_approval,
        request=request,
    )
    short_id, gate = _short_gate(ws, project_id, short_id, parent, PublishState(ws))
    body = _body(
        title,
        description or f"Draft upload from project {project_id}. Review required.",
        privacy=privacy,
        publish_at=publish_at,
        tags=tags or [],
        category=category,
    )
    record = _start_upload(
        ws,
        config,
        project_id=project_id,
        media=media,
        body=body,
        approval=approval,
        short_id=short_id,
        parent_id=parent,
        gate=gate,
        thumbnail=thumbnail,
        live=live,
    )
    return {
        "command": "youtube upload",
        "ok": True,
        "video_id": record["video_id"],
        "state": record["state"],
        "privacy": record["privacy"],
        "publish_at": record["publish_at"],
        "live": record["live"],
        "transport": record["transport"],
        "is_short": record["is_short"],
        "needs_approval": False,
        "note": "Private-first: run `youtube verify`, then `youtube publish --approve` "
        "to release publicly after processing is confirmed.",
    }


def verify(ws, config: dict, *, video_id: str) -> dict:
    """Poll remote status and advance processing → private (or scheduled) when processed.

    Quota accounting is owned by the transport (videos.list via ledger.call), so
    this function does not reserve/settle independently — doing so would double-charge.
    """
    state = PublishState(ws)
    record = state.get(video_id)
    transport = get_transport(ws, config, prefer="live" if record.get("live") else "stub")
    ledger = QuotaLedger(ws)
    remote = transport.get_video(video_id, ledger=ledger, daily_limit=_daily_limit(config))
    processed = str(remote.get("status", "")).lower() in PROCESSING_STATES
    if processed and record["state"] == "processing":
        target = "scheduled" if record.get("publish_at") else "private"
        state.transition(video_id, target, detail=f"remote status {remote['status']}")
    record = state.get(video_id)
    return {
        "command": "youtube verify",
        "ok": True,
        "video_id": video_id,
        "state": record["state"],
        "remote_status": remote["status"],
        "processed": processed,
        "remote": remote,
        "needs_approval": record["state"] != "public",
        "note": (
            "Public release requires `youtube publish --approve`."
            if record["state"] != "public"
            else "Video is already public."
        ),
    }


def publish(
    ws,
    config: dict,
    *,
    video_id: str,
    approval_token: str | None = None,
    generate_approval: bool = False,
    live: bool | None = None,
) -> dict:
    """Make a verified private video public — requires a fresh explicit approval."""
    state = PublishState(ws)
    record = state.get(video_id)
    approval = _require_approval(
        ws,
        "youtube.publish",
        video_id,
        approval_token,
        generate=generate_approval,
        request={"video_id": video_id, "new_privacy": "public"},
    )
    if record["state"] == "processing":
        raise ValidationError(
            f"video {video_id} is still processing; cannot publish",
            hint="run `creator youtube verify` until processing is confirmed first",
        )
    if record["state"] not in ("private", "scheduled"):
        raise ValidationError(f"video {video_id} is '{record['state']}'; cannot publish")
    if record.get("is_short"):
        state.require_published_parent(record)
    prefer = "live" if live is True or (live is None and record.get("live")) else "stub"
    transport = get_transport(ws, config, prefer=prefer)
    ledger = QuotaLedger(ws)
    transport.update_video(
        video_id,
        {
            "id": video_id,
            "status": {
                "privacyStatus": "public",
                "embeddable": True,
                "selfDeclaredMadeForKids": False,
            },
        },
        ledger=ledger,
        daily_limit=_daily_limit(config),
    )
    state.transition(video_id, "public", detail=f"approved by {approval['fingerprint']}")
    updated = state.get(video_id)
    updated["approval"]["publish"] = approval
    state.save(updated)
    updated = state.get(video_id)
    updated["approval"]["publish"] = approval
    state.save(updated)
    return {
        "command": "youtube publish",
        "ok": True,
        "video_id": video_id,
        "state": "public",
        "live": bool(record.get("live")),
        "needs_approval": False,
        "note": "Video is public. Verify playback and monetization in YouTube Studio.",
    }


def set_thumbnail(
    ws,
    config: dict,
    *,
    video_id: str,
    image: str,
    approval_token: str | None = None,
    generate_approval: bool = False,
) -> dict:
    state = PublishState(ws)
    record = state.get(video_id)
    path = validate_media_path(image, ws, config)
    approval = _require_approval(
        ws,
        "youtube.thumbnail",
        video_id,
        approval_token,
        generate=generate_approval,
        request={"video_id": video_id, "image_sha256": sha256_file(path)},
    )
    transport = get_transport(ws, config, prefer="live" if record.get("live") else "stub")
    ledger = QuotaLedger(ws)
    result = transport.set_thumbnail(
        video_id, path, ledger=ledger, daily_limit=_daily_limit(config)
    )
    record["thumbnail"] = str(path)
    record["approval"]["thumbnail"] = approval
    state.save(record)
    return {
        "command": "youtube thumbnail",
        "ok": True,
        "video_id": video_id,
        "thumbnail": result["thumbnail"],
        "live": result["live"],
    }
