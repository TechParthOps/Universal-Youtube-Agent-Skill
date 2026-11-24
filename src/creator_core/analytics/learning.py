"""Explicit learning loop (Phase 9): propose scorer adjustments, then apply only with approval.

Proposals are heuristics labeled `hypothesis`. They never claim a metric *caused* an outcome.
Staging a change has no effect; applying it writes `research/scoring_config.json`, which
`creator ideas` already loads, and records the evidence in the profile memory and audit log.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.profiles.profile import ProfileManager
from creator_core.research.scoring import DEFAULT_WEIGHTS
from creator_core.security.audit import AuditLog
from creator_core.workspace.atomic import atomic_write_json

MIN_WEIGHT, MAX_WEIGHT = 0.01, 0.5
SCORER_CONFIG = "scoring_config.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _tags_from_channel_snapshot(ws) -> dict[str, list[str]]:
    path = Path(ws.research) / "channel_snapshot.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, list[str]] = {}
    for video in data.get("videos") or []:
        vid = video.get("id") or video.get("video_id")
        if vid:
            out[str(vid)] = [str(t).lower() for t in (video.get("tags") or [])]
    return out


def propose(ws, profile: dict, report: dict, snapshot: dict) -> dict:
    """Derive deterministic, bounded scorer weight hints from measured report findings."""
    weights = dict(DEFAULT_WEIGHTS)
    proposals = []
    notes = []

    def adjust(criterion: str, delta: float, reason: str, evidence: dict) -> None:
        if not delta or criterion not in weights:
            return
        before = weights[criterion]
        after = round(max(MIN_WEIGHT, min(MAX_WEIGHT, before + delta)), 4)
        if after == before:
            return
        weights[criterion] = after
        proposals.append(
            {
                "criterion": criterion,
                "from": before,
                "to": after,
                "delta": round(after - before, 4),
                "reason": reason,
                "evidence": evidence,
                "kind": "hypothesis",
            }
        )

    if snapshot.get("verified"):
        adjust(
            "evidence_strength",
            0.02,
            "Snapshot is verified API data rather than a fixture.",
            {"source": snapshot.get("source")},
        )

    comparison = {c["metric"]: c for c in report.get("channel_comparison") or []}
    views = comparison.get("views")
    if views and views.get("delta_pct") is not None:
        delta = views["delta_pct"]
        if delta >= 10:
            adjust(
                "trend_momentum",
                0.02,
                "Median views are at least 10% above the earlier baseline in this sample.",
                {
                    "metric": "median_views",
                    "delta_pct": delta,
                    "prior_source": views.get("prior_source"),
                },
            )
        elif delta <= -10:
            adjust(
                "trend_momentum",
                -0.01,
                "Median views are at least 10% below the earlier baseline in this sample.",
                {
                    "metric": "median_views",
                    "delta_pct": delta,
                    "prior_source": views.get("prior_source"),
                },
            )

    interests = {
        str(i).lower()
        for i in ((profile.get("audience") or {}).get("interests") or [])
        if str(i).strip()
    }
    videos = snapshot.get("videos") or []
    id_tags = _tags_from_channel_snapshot(ws)
    # The Analytics API does not return tags; an empty overlap may simply mean "no tag data",
    # not "no audience alignment". Never penalize on missing data.
    has_tag_data = any((id_tags.get(v["video_id"]) or (v.get("tags") or [])) for v in videos)
    if interests and len(videos) >= 3:
        ranked = sorted(videos, key=lambda v: v.get("views", 0), reverse=True)
        top = ranked[: max(1, len(ranked) // 4)]
        top_tags = set()
        for video in top:
            top_tags.update(id_tags.get(video["video_id"], []))
            top_tags.update(str(t).lower() for t in (video.get("tags") or []))
        if not has_tag_data:
            notes.append(
                "No video tag data is available (the Analytics API does not return tags); "
                "audience_fit was not adjusted. Add a local channel snapshot to enable this signal."
            )
        else:
            overlap = len(top_tags & interests) / len(interests)
            if overlap >= 0.3:
                adjust(
                    "audience_fit",
                    0.02,
                    "Top-quartile videos share at least 30% of declared audience interests (association).",
                    {
                        "overlap_ratio": round(overlap, 3),
                        "interests": len(interests),
                        "top_videos": len(top),
                        "source": "local channel snapshot + report",
                    },
                )
            elif overlap == 0:
                adjust(
                    "audience_fit",
                    -0.01,
                    "No declared audience interest appeared in top-quartile video tags (association).",
                    {"overlap_ratio": 0.0, "interests": len(interests), "top_videos": len(top)},
                )
            notes.append(
                f"Top-quartile tag/interest overlap: {overlap:.0%} "
                f"({len(interests)} declared interests, {len(top)} videos). Association only."
            )
    elif not interests:
        notes.append("No declared audience interests; audience_fit was not adjusted.")

    # Volatility is a genuine property of the data (dispersion of per-video views), not an
    # artifact of the report's high/low extremes. Higher dispersion → weight idea risk more.
    if len(videos) >= 3:
        view_values = [float(v.get("views") or 0) for v in videos]
        mean_views = statistics.fmean(view_values)
        if mean_views > 0:
            cv = statistics.pstdev(view_values) / mean_views
            if cv >= 0.7:
                adjust(
                    "risk_inverse",
                    0.01,
                    "Per-video views are highly dispersed (CV >= 0.7) in this sample.",
                    {"views_cv": round(cv, 3), "sample_size": len(videos)},
                )

    if not proposals:
        notes.append("No evidence met the thresholds; scorer weights are unchanged.")

    return {
        "command": "analytics propose",
        "ok": True,
        "proposals": proposals,
        "weights": weights,
        "notes": notes,
        "needs_approval": bool(proposals),
        "kind": "hypothesis",
        "disclaimer": "Heuristic associations only. Not proof of cause and not a performance "
        "prediction. Nothing changes until a creator explicitly approves it.",
    }


def stage(ws, proposal: dict) -> dict:
    """Stage proposed weights into profile memory. This has NO effect until approved."""
    if not proposal.get("proposals"):
        raise ValidationError("there are no proposals to stage")
    reason = "; ".join(p["reason"] for p in proposal["proposals"])[:400]
    return ProfileManager(ws).stage_memory_change(
        "editing_preferences", "scorer_weights", proposal["weights"], reason
    )


def pending(ws) -> list[dict]:
    pm = ProfileManager(ws)
    if not pm.exists():
        return []
    return [
        c
        for c in pm.pending_changes()
        if c.get("field") == "scorer_weights" and c.get("status") == "pending"
    ]


def apply(
    ws, change_id: str, *, approval_token: str | None = None, generate_approval: bool = False
) -> dict:
    """Apply an approved scorer-weight change to the config that `creator ideas` reads."""
    pm = ProfileManager(ws)
    staged = next((c for c in pm.pending_changes() if c["id"] == change_id), None)
    if staged is None:
        raise ValidationError(f"scorer change not found: {change_id}")
    if staged.get("field") != "scorer_weights":
        raise ValidationError(f"change {change_id} is not a scorer-weight change")

    applied = pm.apply_change(
        change_id, approval_token=approval_token, generate_approval=generate_approval
    )
    weights = applied["value"]
    if not isinstance(weights, dict) or not weights:
        raise ValidationError("staged scorer weights are invalid")
    payload = {
        "weights": weights,
        "source": "analytics.learning",
        "change_id": change_id,
        "applied_at": _now(),
        "reason": applied["reason"],
        "kind": "hypothesis",
        "note": "Explicitly approved heuristic adjustments; not a performance prediction.",
    }
    atomic_write_json(Path(ws.research) / SCORER_CONFIG, payload)
    AuditLog(ws.root).log("analytics.weights.applied", change_id=change_id, weights=weights)
    return {
        "command": "analytics apply",
        "ok": True,
        "change_id": change_id,
        "weights": weights,
        "config": str(Path(ws.research) / SCORER_CONFIG),
        "note": "Future `creator ideas` runs will use these weights.",
    }
