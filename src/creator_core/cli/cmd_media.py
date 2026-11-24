"""`creator media …` — inspect, analyze, index and search real media with FFmpeg."""

from __future__ import annotations

import hashlib
import json

from creator_core.cli.errors import ProviderUnavailableError, ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.media.analyze import analyze
from creator_core.media.diarize import (
    HeuristicDiarizer,
    NullDiarizer,
    PyannoteDiarizer,
)
from creator_core.media.diarize import (
    get_provider as get_diarizer,
)
from creator_core.media.index import build_index, load_index, search
from creator_core.media.ingest import add_media_root, inspect, registered_roots
from creator_core.media.injection import analyze_untrusted, sanitize_untrusted
from creator_core.security.audit import AuditLog
from creator_core.security.paths import resolve_within
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import ensure_workspace, load_config


def _ws(ctx: CliContext):
    ws = ensure_workspace(ctx.workspace)
    return ws, load_config(ws), AuditLog(ws.root)


def _emit(ctx: CliContext, payload: dict, human) -> int:
    if ctx.json_mode:
        emit_json(payload)
    else:
        human()
    return 0


def run_media(ctx: CliContext, args) -> int:
    handlers = {
        "inspect": run_inspect,
        "analyze": run_analyze,
        "diarize": run_diarize,
        "search": run_search,
        "root": run_root,
    }
    return handlers[args.media_command](ctx, args)


def run_inspect(ctx: CliContext, args) -> int:
    ws, config, audit = _ws(ctx)
    info = inspect(args.path, ws, config)
    audit.log(
        "media.inspect",
        source_id=info["source_id"],
        filename=info["filename"],
        duration=info["duration_seconds"],
    )
    payload = {"command": "media inspect", "ok": True, "source": info}
    video = info["video"] or {}

    def _print() -> None:
        print(
            f"{info['filename']}: {info['duration_seconds']}s, "
            f"{video.get('width', 0)}x{video.get('height', 0)}, "
            f"sha256={info['source_id'][:12]}"
        )

    return _emit(ctx, payload, _print)


def run_analyze(ctx: CliContext, args) -> int:
    ws, config, audit = _ws(ctx)
    report = analyze(
        args.path,
        ws,
        project_id=getattr(args, "project", None),
        transcribe=getattr(args, "transcribe", False),
        config=config,
    )
    index = build_index(report, ws)
    audit.log(
        "media.analyze",
        project_id=report["project_id"],
        source_id=report["source"]["source_id"],
        segments=len(index["segments"]),
    )
    counts = {
        key: len((report["analysis"].get(key) or {}).get("segments") or [])
        for key in ("silence", "scenes", "audio_events", "moments")
    }
    payload = {
        "command": "media analyze",
        "ok": True,
        "project_id": report["project_id"],
        "source_id": report["source"]["source_id"],
        "detections": counts,
        "transcript_available": bool((report["analysis"].get("transcript") or {}).get("available")),
        "segments": len(index["segments"]),
        "untrusted_metadata": report["untrusted_metadata"],
        "report": report,
    }
    return _emit(ctx, payload, lambda: _print_analysis(payload))


def run_search(ctx: CliContext, args) -> int:
    ws, _, audit = _ws(ctx)
    project = getattr(args, "project", None) or "default"
    index = load_index(ws, project)
    results = search(index, args.query, limit=getattr(args, "limit", 10))
    audit.log("media.search", project_id=project, query=analyze_untrusted(args.query)["length"])
    payload = {
        "command": "media search",
        "ok": True,
        "project_id": project,
        "query": sanitize_untrusted(args.query),
        "count": len(results),
        "results": results,
    }
    return _emit(ctx, payload, lambda: _print_search(payload))


def run_root(ctx: CliContext, args) -> int:
    ws, _, audit = _ws(ctx)
    if args.root_command == "add":
        result = add_media_root(ws, args.path)
        audit.log("media.root.add", path=result["path"])
        payload = {"command": "media root add", "ok": True, **result}
    else:
        payload = {
            "command": "media root list",
            "ok": True,
            "roots": [str(p) for p in registered_roots(ws)],
        }
    return _emit(
        ctx, payload, lambda: print("\n".join(payload.get("roots", [payload.get("path", "")])))
    )


def run_diarize(ctx: CliContext, args) -> int:
    ws, config, audit = _ws(ctx)
    if not args.from_file:
        raise ValidationError(
            "a report file is required for diarization",
            hint="pass --from-file with a media analysis report JSON",
        )
    source = resolve_within(ws.base, args.from_file)
    try:
        report = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"analysis report is unreadable: {exc}") from exc
    if not isinstance(report, dict):
        raise ValidationError("analysis report must be a JSON object")
    analysis = report.get("analysis") if isinstance(report.get("analysis"), dict) else report

    choice = args.provider
    if choice == "auto":
        provider = get_diarizer(config, enabled=True)
    elif choice == "heuristic":
        provider = HeuristicDiarizer()
    elif choice == "pyannote":
        provider = PyannoteDiarizer()
    else:
        provider = NullDiarizer()
    if not provider.available():
        raise ProviderUnavailableError(
            f"diarization provider '{provider.name}' is not available",
            hint="install the [diarize] extra and grant model access, or use --provider heuristic",
        )
    # Model providers open this file: confine it to the workspace like every other path (M2).
    audio = resolve_within(ws.base, args.audio) if args.audio else None
    result = provider.diarize(audio, analysis)

    digest = hashlib.sha256(
        json.dumps(
            {"report": report, "provider": provider.name}, sort_keys=True, default=str
        ).encode("utf-8")
    ).hexdigest()[:12]
    out = (
        resolve_within(ws.base, args.out)
        if args.out
        else resolve_within(ws.base, "research") / f"diarization_{digest}.json"
    )
    atomic_write_json(
        out,
        {
            "command": "media diarize",
            "source_report": str(source),
            "provider": provider.name,
            "ok": True,
            **result,
        },
    )
    audit.log(
        "media.diarize",
        provider=provider.name,
        turns=len(result["turns"]),
        speaker_count=result["speaker_count"],
    )
    payload = {
        "command": "media diarize",
        "ok": True,
        "provider": provider.name,
        "source_report": str(source),
        "turns": result["turns"],
        "speaker_count": result["speaker_count"],
        "note": result["note"],
        "path": str(out),
    }
    return _emit(ctx, payload, lambda: _print_diarize(payload))


def _print_diarize(p: dict) -> None:
    print(
        f"media diarize — {len(p['turns'])} turn(s) via {p['provider']}, "
        f"{p['speaker_count']} turn label(s)"
    )
    print(f"  file: {p['path']}")
    print(f"  {p['note']}")


def _print_analysis(p: dict) -> None:
    print(f"media analyze — project {p['project_id']}, source {p['source_id']}")
    for key, count in p["detections"].items():
        print(f"  {key}: {count}")
    print(f"  speech transcript: {'yes' if p['transcript_available'] else 'no'}")
    print(f"  indexed segments: {p['segments']}")


def _print_search(p: dict) -> None:
    print(f"media search — {p['count']} matches for '{p['query']}'")
    for r in p["results"]:
        preview = r["text"][:70].replace("\n", " ")
        print(
            f"  [{r['start']:.2f}-{r['end']:.2f}] {r['type']} "
            f"score={r['match_score']:.2f} {preview}"
        )
