"""`creator analytics …` — fixture/live ingestion, cited reports, explicit learning (Phase 9)."""

from __future__ import annotations

import json
from pathlib import Path

from creator_core.analytics import ingest, learning
from creator_core.analytics import report as reporting
from creator_core.analytics.transport import get_transport
from creator_core.cli.errors import UnsafePathError, ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.media.ingest import registered_roots
from creator_core.profiles.profile import ProfileManager
from creator_core.security.audit import AuditLog
from creator_core.security.paths import resolve_within
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import ensure_workspace, load_config


def _confined(ws, value):
    """Confine caller-supplied JSON paths to the workspace or registered media roots."""
    if not value:
        return value
    candidate = Path(value)
    for root in registered_roots(ws):
        try:
            return resolve_within(root, candidate)
        except UnsafePathError:
            continue
    raise UnsafePathError(
        str(value),
        hint="file must be inside the workspace or a registered media root "
        "(run `creator media root add <dir>`)",
    )


def _print_refresh(payload: dict) -> None:
    print(
        f"analytics refresh — {payload['video_count']} video(s), source={payload['source']} "
        f"verified={payload['verified']}"
    )
    print(f"  snapshot: {payload['snapshot']}")
    print(f"  {payload['note']}")


def _print_report(payload: dict) -> None:
    print(
        f"analytics report — {payload['finding_count']} finding(s), "
        f"source={payload['source']} verified={payload['verified']}"
    )
    print(f"  markdown: {payload['report']}")
    print(f"  json:     {payload['json']}")
    print(f"  {payload['note']}")


def _print_learn(payload: dict) -> None:
    print(
        f"analytics learn — {len(payload['proposals'])} proposal(s), "
        f"staged={payload.get('change_id') or 'none'}"
    )
    for item in payload["proposals"]:
        print(f"  {item['criterion']}: {item['from']} → {item['to']} ({item['kind']})")
    if payload.get("applied"):
        print(f"  applied to {payload['applied']['config']}")
    elif payload["needs_approval"]:
        print(f"  approval required: {payload['approval_hint']}")
    print(f"  {payload['disclaimer']}")


def _print_status(payload: dict) -> None:
    print(
        f"analytics status — {payload['snapshot_count']} snapshot(s), "
        f"{payload['report_count']} report(s), {len(payload['pending_changes'])} pending change(s)"
    )
    for item in payload["snapshots"]:
        print(f"  snapshot: {item}")
    for item in payload["reports"]:
        print(f"  report:   {item}")


def run(ctx: CliContext, args) -> int:
    ws = ensure_workspace(ctx.workspace)
    sub = args.analytics_command

    if sub == "refresh":
        config = load_config(ws)
        transport = get_transport(
            ws, config, prefer="live" if getattr(args, "live", False) else None
        )
        raw = transport.fetch(
            source_path=_confined(ws, getattr(args, "from_file", None)),
            start_date=getattr(args, "start_date", None),
            end_date=getattr(args, "end_date", None),
        )
        snapshot = ingest.normalize_report(raw, source=transport.name)
        path = ingest.save_snapshot(ws, snapshot)
        AuditLog(ws.root).log(
            "analytics.refresh",
            source=transport.name,
            verified=snapshot["verified"],
            videos=snapshot["totals"]["videos"],
        )
        payload = {
            "command": "analytics refresh",
            "ok": True,
            "source": transport.name,
            "live": snapshot["verified"],
            "verified": snapshot["verified"],
            "video_count": snapshot["totals"]["videos"],
            "snapshot": str(path),
            "note": snapshot["note"],
        }
        printer = _print_refresh

    elif sub == "report":
        snapshot_path = _confined(ws, getattr(args, "from_file", None)) or ingest.latest_snapshot(
            ws
        )
        if not snapshot_path:
            raise ValidationError(
                "no analytics snapshot found", hint="run `creator analytics refresh --json` first"
            )
        snapshot = ingest.load_snapshot(snapshot_path)
        prior = (
            ingest.load_snapshot(_confined(ws, args.prior))
            if getattr(args, "prior", None)
            else None
        )
        profile = ProfileManager(ws).load() if ProfileManager(ws).exists() else None
        data = reporting.build(snapshot, prior=prior, profile=profile)
        md_path = reporting.save_report(ws, data)
        json_path = md_path.with_suffix(".json")
        atomic_write_json(json_path, data)
        payload = {
            "command": "analytics report",
            "ok": True,
            "source": data["source"],
            "verified": data["verified"],
            "finding_count": len(data["findings"]),
            "report": str(md_path),
            "json": str(json_path),
            "snapshot": str(snapshot_path),
            "note": data["note"],
        }
        printer = _print_report

    elif sub == "learn":
        snapshot_path = _confined(ws, getattr(args, "from_file", None)) or ingest.latest_snapshot(
            ws
        )
        if not snapshot_path:
            raise ValidationError(
                "no analytics snapshot found", hint="run `creator analytics refresh --json` first"
            )
        snapshot = ingest.load_snapshot(snapshot_path)
        report_file = _confined(ws, getattr(args, "report", None)) or reporting.latest_report(ws)
        if report_file and str(report_file).endswith(".json"):
            data = json.loads(Path(report_file).read_text(encoding="utf-8"))
        else:
            data = reporting.build(snapshot)
        if not ProfileManager(ws).exists():
            raise ValidationError(
                "no creator profile found",
                hint="run `creator profile init` before applying learning",
            )
        proposal = learning.propose(ws, ProfileManager(ws).load(), data, snapshot)
        # Staging is idempotent: reuse the latest pending scorer change instead of
        # accumulating orphans when `learn` is called repeatedly.
        existing = learning.pending(ws)
        if existing:
            staged = existing[-1]
        elif proposal["proposals"]:
            staged = learning.stage(ws, proposal)
        else:
            staged = None
        applied = None
        if getattr(args, "apply", False):
            if staged is None:
                raise ValidationError("nothing to apply: no proposals met the thresholds")
            applied = learning.apply(
                ws,
                staged["id"],
                approval_token=getattr(args, "approval_token", None),
                generate_approval=bool(getattr(args, "approve", False)),
            )
        payload = {
            **proposal,
            "change_id": staged["id"] if staged else None,
            "applied": applied,
            "approval_hint": "rerun with `analytics learn --apply --approve`" if staged else None,
        }
        printer = _print_learn

    elif sub == "status":
        snapshots = sorted((Path(ws.analytics) / "snapshots").glob("snapshot_*.json"))
        reports = sorted((Path(ws.analytics) / "reports").glob("report_*.md"))
        payload = {
            "command": "analytics status",
            "ok": True,
            "snapshot_count": len(snapshots),
            "report_count": len(reports),
            "snapshots": [str(p) for p in snapshots],
            "reports": [str(p) for p in reports],
            "pending_changes": learning.pending(ws),
        }
        printer = _print_status
    else:  # pragma: no cover - argparse restricts choices
        raise ValidationError(f"unknown analytics command: {sub}")

    if ctx.json_mode:
        emit_json(payload)
    else:
        printer(payload)
    return 0
