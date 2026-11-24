"""`creator ideas/trends/competitors/plan/script` — research-to-planning CLI commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.planning.planner import ContentPlanner
from creator_core.planning.script import draft_script
from creator_core.profiles.profile import ProfileManager
from creator_core.research.audience import AudienceAnalyzer
from creator_core.research.competitors import CompetitorAnalyzer
from creator_core.research.gaps import IdeaGapFinder
from creator_core.research.history import ContentHistoryAnalyzer
from creator_core.research.ideas import IdeaGenerator
from creator_core.research.scoring import OpportunityScorer, load_weights
from creator_core.research.trends import TrendResearcher
from creator_core.security.audit import AuditLog
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import ensure_workspace
from creator_core.youtube.channel import normalize_snapshot


def _ctx_parts(ctx: CliContext):
    ws = ensure_workspace(ctx.workspace)
    return ws, AuditLog(ws.root)


def _load_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise ValidationError(
            f"no {what} found at {path}",
            hint="run the earlier workflow step first (profile init / channel connect)",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path} is unreadable: {exc}") from exc


def _require_profile(ctx: CliContext) -> dict:
    ws, _ = _ctx_parts(ctx)
    return ProfileManager(ws).load()


def run_ideas(ctx: CliContext, args) -> int:
    ws, audit = _ctx_parts(ctx)
    profile = _require_profile(ctx)
    snapshot_path = ws.research / "channel_snapshot.json"
    snapshot = _load_json(snapshot_path, "channel snapshot")

    competitors_path = ws.research / "competitor_snapshots.json"
    competitor_snaps = (
        json.loads(competitors_path.read_text(encoding="utf-8"))
        if competitors_path.is_file()
        else []
    )
    trends = TrendResearcher(ws.research).analyze()
    history = ContentHistoryAnalyzer().analyze(snapshot)
    audience = AudienceAnalyzer().analyze(profile, snapshot)
    competitor_report = (
        CompetitorAnalyzer().analyze(snapshot, competitor_snaps)
        if competitor_snaps
        else {
            "frequent_topics": {"value": [], "label": "estimated"},
            "topics_absent_from_own_history": {"value": [], "label": "estimated"},
        }
    )
    gaps = IdeaGapFinder().find(history, competitor_report, trends)
    scorer_weights = load_weights(ws.research / "scoring_config.json")
    ideas = IdeaGenerator().generate(
        profile=profile,
        history=history,
        audience=audience,
        competitors=competitor_report,
        trends=trends,
        gaps=gaps,
        scorer=OpportunityScorer(scorer_weights),
        limit=max(1, min(int(args.limit), 50)),
    )
    atomic_write_json(
        ws.research / "ideas.json",
        {
            "ideas": ideas,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    audit.log("ideas.generated", count=len(ideas), snapshot_source=snapshot.get("source"))
    payload = {
        "command": "ideas",
        "ok": True,
        "count": len(ideas),
        "ideas": ideas,
        "disclaimer": "Opportunity scores are planning aids, not virality predictions.",
    }
    return _emit(ctx, payload, human=lambda: _print_ideas(payload))


def run_trends(ctx: CliContext, args) -> int:
    ws, audit = _ctx_parts(ctx)
    researcher = TrendResearcher(ws.research)
    if getattr(args, "from_file", None):
        snap = researcher.import_snapshot(Path(args.from_file))
        audit.log("trends.imported", source=snap["source"], topics=len(snap["topics"]))
    payload = researcher.analyze()
    if not payload.get("ok"):
        raise ValidationError("no trend snapshot available", hint=payload.get("hint"))
    payload["command"] = "trends"
    return _emit(ctx, payload, human=lambda: _print_trends(payload))


def run_competitors(ctx: CliContext, args) -> int:
    ws, audit = _ctx_parts(ctx)
    own = _load_json(ws.research / "channel_snapshot.json", "channel snapshot")
    path = ws.research / "competitor_snapshots.json"
    snaps = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    if getattr(args, "from_file", None):
        raw = _load_json(Path(args.from_file), "competitor snapshot file")
        if "videos" in raw and "channel" in raw:
            snaps.append(normalize_snapshot(raw, source="local_fixture"))
        elif isinstance(raw.get("competitors"), list):
            snaps.extend(normalize_snapshot(c, source="local_fixture") for c in raw["competitors"])
        else:
            raise ValidationError("unrecognized competitor snapshot shape")
        atomic_write_json(path, snaps)
        audit.log("competitors.imported", count=len(snaps))
    payload = CompetitorAnalyzer().analyze(own, snaps)
    payload["command"] = "competitors"
    payload["ok"] = True
    return _emit(ctx, payload, human=lambda: _print_competitors(payload))


def run_plan(ctx: CliContext, args) -> int:
    ws, audit = _ctx_parts(ctx)
    profile = _require_profile(ctx)
    ideas_path = ws.research / "ideas.json"
    ideas = _load_json(ideas_path, "ideas (run `creator ideas` first)").get("ideas", [])
    if not ideas:
        raise ValidationError("no ideas available", hint="run `creator ideas --json` first")
    plan = ContentPlanner().plan(ideas, profile, limit=max(1, min(int(args.limit), 20)))
    atomic_write_json(ws.research / "plan.json", plan)
    audit.log("plan.generated", entries=len(plan["entries"]))
    payload = {"command": "plan", "ok": True, **plan}
    return _emit(ctx, payload, human=lambda: _print_plan(payload))


def run_script(ctx: CliContext, args) -> int:
    ws, audit = _ctx_parts(ctx)
    profile = _require_profile(ctx)
    ideas = _load_json(ws.research / "ideas.json", "ideas").get("ideas", [])
    idea = next((i for i in ideas if i["idea_id"] == args.idea), None)
    if idea is None:
        raise ValidationError(f"idea not found: {args.idea}", hint="run `creator ideas --json`")
    script = draft_script(idea, profile)
    path = ws.research / "scripts" / f"{args.idea}.json"
    atomic_write_json(path, script)
    audit.log("script.drafted", idea_id=args.idea)
    payload = {"command": "script draft", "ok": True, "path": str(path), "script": script}
    return _emit(ctx, payload, human=lambda: _print_script(payload))


def _emit(ctx: CliContext, payload: dict, *, human) -> int:
    if ctx.json_mode:
        emit_json(payload)
    else:
        human()
    return 0


def _print_ideas(p: dict) -> None:
    print(f"ideas — {p['count']} opportunity assessments (not virality predictions)")
    for i in p["ideas"]:
        print(
            f"  [{i['opportunity']['score']:.2f}] {i['idea_id']}: {i['suggested_title']['value']}"
        )


def _print_trends(p: dict) -> None:
    if not p.get("ok"):
        print(p.get("hint", "no trend data"))
        return
    print(f"trends — {len(p['topics'])} topics from {p['source']}")
    for d in p["topics"][:10]:
        t = d["value"]
        print(f"  {t['signal']:>6.1f}  {t['name']}")


def _print_competitors(p: dict) -> None:
    print(f"competitors — {len(p['competitors'])} channels, {p['videos_sampled']} videos")
    for term, n in p["frequent_topics"]["value"][:10]:
        print(f"  {n:>3}  {term}")


def _print_plan(p: dict) -> None:
    print(f"content plan — {len(p['entries'])} proposed slots ({p['timezone']})")
    for e in p["entries"]:
        print(f"  {e['proposed_publish_utc']}  {e['title'][:60]}")


def _print_script(p: dict) -> None:
    s = p["script"]
    print(f"script draft for {s['title']} — {len(s['sections'])} beats (saved: {p['path']})")
    for sec in s["sections"]:
        print(f"  {sec['beat']}: {sec['draft_line']['value'][:90]}")
