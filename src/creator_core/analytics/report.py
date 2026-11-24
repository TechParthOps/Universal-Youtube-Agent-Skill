"""Performance reports (Phase 9): measured comparisons, cited metrics, correlation-only language.

Every finding quotes the metric, its baseline, the sample size and the snapshot provenance.
Interpretations are explicitly hypotheses — no claim that a change *caused* an outcome and no
prediction of future views or virality.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.workspace.atomic import atomic_write_text

COMPARISON_METRICS = (
    "views",
    "ctr_percent",
    "avg_view_percentage",
    "engagement_rate",
    "watch_time_per_view_s",
    "subscribers_gained",
)

METRIC_LABELS = {
    "views": "views",
    "ctr_percent": "thumbnail CTR (%)",
    "avg_view_percentage": "average view percentage",
    "engagement_rate": "engagement rate (%)",
    "watch_time_per_view_s": "watch time per view (s)",
    "subscribers_gained": "subscribers gained",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _pct(value: float, baseline: float) -> float | None:
    return (
        round((float(value) - float(baseline)) / float(baseline) * 100.0, 1) if baseline else None
    )


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:,}" if isinstance(value, int) else str(value)


def build(snapshot: dict, *, prior: dict | None = None, profile: dict | None = None) -> dict:
    """Build an evidence-backed report from one snapshot and an optional earlier baseline."""
    if not isinstance(snapshot, dict) or not snapshot.get("videos"):
        raise ValidationError("snapshot has no videos; run `creator analytics refresh` first")
    videos = snapshot["videos"]
    baseline = snapshot["baseline"]
    sample = len(videos)
    kind = "verified" if snapshot.get("verified") else "estimated"
    source = snapshot.get("source") or "unknown"

    findings = []
    if sample >= 3:
        for metric in COMPARISON_METRICS:
            ranked = sorted(videos, key=lambda v: float(v.get(metric) or 0), reverse=True)
            for label, video in (("high", ranked[0]), ("low", ranked[-1])):
                delta = _pct(video.get(metric, 0), baseline.get(metric, 0))
                if delta is None or abs(delta) < 20:
                    continue
                findings.append(
                    {
                        "id": f"{metric}_{label}_{video['video_id']}",
                        "kind": kind,
                        "metric": metric,
                        "video_id": video["video_id"],
                        "value": video.get(metric, 0),
                        "baseline": baseline.get(metric, 0),
                        "delta_pct": delta,
                        "sample_size": sample,
                        "statement": (
                            f"{METRIC_LABELS[metric]} for {video['video_id']}: "
                            f"{_fmt(video.get(metric, 0))} vs sample median "
                            f"{_fmt(baseline.get(metric, 0))} ({delta:+.1f}%, n={sample})."
                        ),
                        "interpretation_kind": "hypothesis",
                        "interpretation": "Association in this sample only; not evidence of causation.",
                        "evidence": {
                            "source": source,
                            "metric": metric,
                            "n": sample,
                            "value": video.get(metric, 0),
                            "baseline": baseline.get(metric, 0),
                        },
                    }
                )
    findings.sort(key=lambda f: abs(f["delta_pct"]), reverse=True)

    comparisons = []
    if prior is not None:
        for metric in COMPARISON_METRICS:
            current, earlier = baseline.get(metric, 0), (prior.get("baseline") or {}).get(metric)
            if earlier is None:
                continue
            comparisons.append(
                {
                    "metric": metric,
                    "current": current,
                    "prior": earlier,
                    "delta_pct": _pct(current, earlier),
                    "prior_source": prior.get("source"),
                    "kind": kind,
                }
            )
    else:
        hist = ((profile or {}).get("history") or {}).get("baseline") or {}
        if hist:
            for metric, key in (
                ("views", "views"),
                ("avg_view_percentage", "avg_percentage_viewed"),
            ):
                earlier = hist.get(key)
                if earlier:
                    comparisons.append(
                        {
                            "metric": metric,
                            "current": baseline.get(metric, 0),
                            "prior": earlier,
                            "delta_pct": _pct(baseline.get(metric, 0), earlier),
                            "prior_source": "creator.profile.history.baseline",
                            "kind": "estimated",
                        }
                    )

    return {
        "command": "analytics report",
        "ok": True,
        "generated_at": _now(),
        "source": source,
        "verified": snapshot.get("verified", False),
        "sample_size": sample,
        "baseline": baseline,
        "totals": snapshot.get("totals", {}),
        "findings": findings[:12],
        "channel_comparison": comparisons,
        "note": "Observed associations in the supplied sample. Not a prediction or proof of cause.",
        "disclaimer": "No claim of guaranteed views, virality, or revenue is made anywhere in this report.",
    }


def to_markdown(report: dict) -> str:
    """Render the structured report as a human-readable markdown document with metric citations."""
    kind = "verified" if report["verified"] else "estimated"
    lines = [
        f"# Performance report — {report['generated_at'][:10]}",
        "",
        f"- Source: `{report['source']}` (provenance: **{kind}**)",
        f"- Sample: {report['sample_size']} videos",
        f"- {report['note']}",
        "",
        "## Measured baseline (median per video)",
        "",
        "| Metric | Median |",
        "|---|---:|",
    ]
    for metric in COMPARISON_METRICS:
        if metric in report["baseline"]:
            lines.append(f"| {METRIC_LABELS[metric]} | {_fmt(report['baseline'][metric])} |")
    lines += ["", "## Findings (associations, not causes)", ""]
    if report["findings"]:
        for finding in report["findings"]:
            lines.append(
                f"- [{finding['kind']}] {finding['statement']} "
                f"[evidence: {finding['evidence']['source']}, "
                f"{finding['evidence']['metric']}={_fmt(finding['value'])} vs "
                f"{_fmt(finding['baseline'])}, n={finding['sample_size']}]. "
                f"Interpretation: {finding['interpretation_kind']} — {finding['interpretation']}"
            )
    else:
        lines.append("- No finding met the 20% difference threshold with at least 3 videos.")
    lines += ["", "## Baseline comparison", ""]
    if report["channel_comparison"]:
        for comp in report["channel_comparison"]:
            delta = comp["delta_pct"]
            text = "n/a" if delta is None else f"{delta:+.1f}%"
            lines.append(
                f"- {METRIC_LABELS.get(comp['metric'], comp['metric'])}: now "
                f"{_fmt(comp['current'])} vs {_fmt(comp['prior'])} ({text}, "
                f"prior={comp['prior_source']})."
            )
    else:
        lines.append("- No earlier snapshot or profile baseline was supplied.")
    lines += [
        "",
        "## Limits",
        "",
        f"- {report['disclaimer']}",
        "- Fixtures are examples, not verified channel data; live data requires explicit `--live`.",
        (
            "- Tag/format overlap requires a local channel snapshot; the Analytics API does not "
            "return tags (see `creator channel connect --from-file`)."
        ),
        "",
    ]
    return "\n".join(lines)


def save_report(ws, report: dict) -> Path:
    directory = Path(ws.analytics) / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", report.get("generated_at") or _now())[:14]
    path = directory / f"report_{stamp}.md"
    atomic_write_text(path, to_markdown(report))
    return path


def latest_report(ws) -> Path | None:
    directory = Path(ws.analytics) / "reports"
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("report_*.md"))
    return files[-1] if files else None
