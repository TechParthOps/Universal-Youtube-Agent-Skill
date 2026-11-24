"""`creator edit …` — non-destructive timeline, render, revision and preview commands."""

from __future__ import annotations

import json

from creator_core.cli.errors import ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.editing.edl import (
    list_versions,
    load_edl,
    new_project_id,
    save_edl,
    summary,
)
from creator_core.editing.render import render, render_history
from creator_core.editing.revisions import (
    apply_plan,
    consume_pending,
    list_pending,
    load_pending,
    parse_request,
    save_pending,
)
from creator_core.editing.strategies import build_edl
from creator_core.media.analyze import analyze
from creator_core.media.ingest import inspect
from creator_core.security.audit import AuditLog
from creator_core.workspace.layout import ensure_workspace, load_config, project_dir


def _ws(ctx: CliContext):
    ws = ensure_workspace(ctx.workspace)
    return ws, load_config(ws), AuditLog(ws.root)


def _emit(ctx: CliContext, payload: dict, human) -> int:
    if ctx.json_mode:
        emit_json(payload)
    else:
        human()
    return 0


def run_edit(ctx: CliContext, args) -> int:
    handlers = {
        "new": run_new,
        "render": run_render,
        "versions": run_versions,
        "status": run_status,
        "show": run_show,
        "revise": run_revise,
        "revisions": run_revisions,
        "exports": run_exports,
        "preview": run_preview,
    }
    return handlers[args.edit_command](ctx, args)


def _load_analysis(ws, source_info: dict, project_id: str, config: dict) -> dict:
    path = project_dir(ws, project_id) / "analysis" / f"{source_info['source_id']}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    # No report yet: analyze the source (read-only) so the strategy has real signals.
    return analyze(source_info["path"], ws, project_id=project_id, config=config)


def _existing_analysis(ws, edl: dict) -> dict | None:
    """Find a previously stored analysis report for the EDL's first source."""
    pdir = project_dir(ws, edl["project_id"])
    for path in sorted((pdir / "analysis").glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (report.get("source") or {}).get("path") == edl["sources"][0]["path"]:
            return report
    return None


def run_new(ctx: CliContext, args) -> int:
    ws, config, audit = _ws(ctx)
    source = inspect(args.source, ws, config)
    project_id = args.project or new_project_id(args.concept or source["filename"])
    analysis = _load_analysis(ws, source, project_id, config)
    edl = build_edl(
        source, analysis, project_id=project_id, strategy=args.strategy, concept=args.concept or ""
    )
    saved = save_edl(ws, edl)
    audit.log(
        "edit.timeline.created",
        project_id=project_id,
        version=saved["version"],
        strategy=args.strategy,
        source_id=source["source_id"],
    )
    payload = {
        "command": "edit new",
        "ok": True,
        "project_id": project_id,
        **saved,
        "summary": summary(edl),
        "edl": edl,
    }
    return _emit(ctx, payload, lambda: _print_new(payload))


def run_render(ctx: CliContext, args) -> int:
    ws, config, audit = _ws(ctx)
    version = getattr(args, "version", None)
    edl = load_edl(ws, args.project, version)
    actual_version = version or max(v["version"] for v in list_versions(ws, args.project))
    result = render(
        ws, edl, project=project_dir(ws, args.project), version=actual_version, config=config
    )
    audit.log(
        "edit.render.completed",
        project_id=args.project,
        version=actual_version,
        qa_ok=result["qa_ok"],
    )
    return _emit(ctx, result, lambda: _print_render(result))


def run_versions(ctx: CliContext, args) -> int:
    ws, _, _ = _ws(ctx)
    versions = list_versions(ws, args.project)
    if not versions:
        raise ValidationError(
            f"no timeline found for project {args.project}",
            hint="run `creator edit new --source FILE` first",
        )
    payload = {
        "command": "edit versions",
        "ok": True,
        "project_id": args.project,
        "count": len(versions),
        "versions": versions,
    }
    return _emit(ctx, payload, lambda: _print_versions(payload))


def run_status(ctx: CliContext, args) -> int:
    ws, _, _ = _ws(ctx)
    versions = list_versions(ws, args.project)
    if not versions:
        raise ValidationError(
            f"no timeline found for project {args.project}",
            hint="run `creator edit new --source FILE` first",
        )
    latest = max(v["version"] for v in versions)
    edl = load_edl(ws, args.project, latest)
    renders = render_history(project_dir(ws, args.project))
    payload = {
        "command": "edit status",
        "ok": True,
        "project_id": args.project,
        "latest_version": latest,
        "summary": summary(edl),
        "versions": len(versions),
        "renders": renders,
    }
    return _emit(ctx, payload, lambda: _print_status(payload))


def run_show(ctx: CliContext, args) -> int:
    ws, _, _ = _ws(ctx)
    edl = load_edl(ws, args.project, getattr(args, "version", None))
    payload = {"command": "edit show", "ok": True, "edl": edl, "summary": summary(edl)}
    return _emit(ctx, payload, lambda: print(json.dumps(edl, indent=2, ensure_ascii=False)))


def run_revise(ctx: CliContext, args) -> int:
    ws, _, audit = _ws(ctx)
    version = getattr(args, "version", None)
    parent = load_edl(ws, args.project, version)
    actual = version or max(v["version"] for v in list_versions(ws, args.project))
    pending_id = getattr(args, "pending", None)

    if pending_id:
        plan = load_pending(ws, args.project, pending_id)
        request_text = plan.get("request", "")
    else:
        request_text = getattr(args, "request", None) or ""
        plan = parse_request(request_text, parent)
        audit.log(
            "edit.revision.planned",
            project_id=args.project,
            version=actual,
            intent=plan["intent"],
            clarification=plan["needs_clarification"],
        )

    if getattr(args, "queue", False) and not pending_id:
        saved = save_pending(ws, args.project, plan)
        payload = {
            "command": "edit revise",
            "ok": True,
            "status": "pending",
            "project_id": args.project,
            **saved,
        }
        return _emit(ctx, payload, lambda: _print_plan(payload))

    if getattr(args, "plan_only", False) or plan.get("needs_clarification") or not plan.get("ops"):
        payload = {
            "command": "edit revise",
            "ok": True,
            "project_id": args.project,
            "parent_version": actual,
            **plan,
        }
        return _emit(ctx, payload, lambda: _print_plan(payload))

    child = apply_plan(parent, plan, analysis_report=_existing_analysis(ws, parent))
    saved = save_edl(ws, child)
    if pending_id:
        consume_pending(ws, args.project, pending_id)
    audit.log(
        "edit.revision.applied",
        project_id=args.project,
        version=saved["version"],
        parent_version=actual,
        intent=plan["intent"],
        revision_id=pending_id,
    )
    payload = {
        "command": "edit revise",
        "ok": True,
        "project_id": args.project,
        "parent_version": actual,
        "revision_id": pending_id,
        **saved,
        "summary": summary(child),
        "edl": child,
    }
    return _emit(ctx, payload, lambda: _print_new(payload))


def run_revisions(ctx: CliContext, args) -> int:
    ws, _, _ = _ws(ctx)
    revisions = list_pending(ws, args.project)
    payload = {
        "command": "edit revisions",
        "ok": True,
        "project_id": args.project,
        "count": len(revisions),
        "revisions": revisions,
    }
    return _emit(ctx, payload, lambda: _print_revisions(payload))


def run_exports(ctx: CliContext, args) -> int:
    ws, _, _ = _ws(ctx)
    exports = []
    for item in render_history(project_dir(ws, args.project)):
        exports.append(
            {
                **item,
                "ready_for_export": bool(item["qa_ok"]),
                "note": "A/V sync needs human review before publishing.",
            }
        )
    payload = {
        "command": "edit exports",
        "ok": True,
        "project_id": args.project,
        "count": len(exports),
        "exports": exports,
    }
    return _emit(ctx, payload, lambda: _print_exports(payload))


def run_preview(ctx: CliContext, args) -> int:
    from creator_core.preview.server import PreviewServer

    ws, _, _ = _ws(ctx)
    try:
        server = PreviewServer(ws, args.project, host=args.host, port=args.port)
    except ValidationError as exc:
        raise ValidationError(
            exc.message, hint=exc.hint or "the preview server only binds to 127.0.0.1"
        ) from exc
    url = server.start()
    payload = {
        "command": "edit preview",
        "ok": True,
        "project_id": args.project,
        "url": url,
        "endpoints": [
            "/api/project",
            "/api/timeline",
            "/api/video",
            "/api/video/source",
            "/api/qa",
            "/api/revisions",
            "/api/render",
            "/api/exports",
        ],
    }
    if ctx.json_mode:
        emit_json(payload)
    else:
        print(f"creator preview — {url}")
        print("  Ctrl+C to stop; the server is bound to loopback only.")
    try:
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


def _print_plan(p: dict) -> None:
    print(f"edit revise — {p.get('intent', 'unsupported')} on {p['project_id']}")
    if p.get("needs_clarification"):
        for question in p.get("questions") or []:
            print(f"  question: {question}")
        return
    if p.get("status") == "pending":
        print(
            f"  queued {p['id']} — apply with `creator edit revise {p['project_id']} "
            f"--pending {p['id']}`"
        )
        return
    for op in p.get("ops") or []:
        print(f"  op: {op['type']} — {json.dumps(op, ensure_ascii=False)}")
    print("  No EDL version created (plan only).")


def _print_new(p: dict) -> None:
    s = p["summary"]
    print(f"edit new — project {p['project_id']}, timeline v{p['version']}")
    print(f"  strategy: {s['strategy']}  duration: {s['duration_seconds']}s")
    print(
        f"  clips: {s['video_clips']}  removed ranges: {s['removed_ranges']}  "
        f"captions: {s['captions']}"
    )


def _print_render(p: dict) -> None:
    print(f"edit render — v{p['timeline_version']} r{p['render_number']} → {p['output']}")
    print(f"  duration: {p['duration_seconds']}s  QA: {'pass' if p['qa_ok'] else 'issues'}")
    for item in p["needs_approval"]:
        print(f"  review: {item['name']} — {item['detail']}")


def _print_versions(p: dict) -> None:
    print(f"edit versions — {p['count']} for {p['project_id']}")
    for v in p["versions"]:
        print(
            f"  v{v['version']:03d}  {v['duration']}s  {v['clips']} clips  "
            f"parent={v['parent_version']}"
        )


def _print_status(p: dict) -> None:
    s = p["summary"]
    print(
        f"edit status — {p['project_id']} (v{p['latest_version']}, "
        f"{p['versions']} version(s), {len(p['renders'])} render(s))"
    )
    print(
        f"  {s['duration_seconds']}s, {s['video_clips']} clips, "
        f"{s['removed_ranges']} removed ranges"
    )


def _print_revisions(p: dict) -> None:
    print(f"edit revisions — {p['count']} pending for {p['project_id']}")
    for rev in p["revisions"]:
        flag = " (needs clarification)" if rev.get("needs_clarification") else ""
        print(f"  {rev['id']}  {rev['intent']}{flag}  {rev['request']}")


def _print_exports(p: dict) -> None:
    print(f"edit exports — {p['project_id']} ({p['count']} render(s))")
    for item in p["exports"]:
        print(
            f"  v{item['timeline_version']} r{item['render_number']}  "
            f"QA: {'pass' if item['qa_ok'] else 'issues'}  "
            f"ready: {item['ready_for_export']}"
        )
