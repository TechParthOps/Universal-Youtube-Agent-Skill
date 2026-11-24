"""Analytics report ingestion and normalization (Phase 9).

No network calls here. This module converts either an API-shaped YouTube Analytics report
(`reports[].data.rows[]` with `columnHeaders`) or a simplified local fixture into a plain,
inspectable performance snapshot. Numeric claims are marked `verified` only when the source is the
live Analytics API; fixtures are always `estimated`. No performance is predicted.
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError

VERIFIED_SOURCE = "youtube_analytics_api"

ALIASES = {
    "video": "video_id",
    "videoid": "video_id",
    "video_id": "video_id",
    "title": "title",
    "tags": "tags",
    "views": "views",
    "viewcount": "views",
    "impressions": "impressions",
    "estimatedminuteswatched": "estimated_minutes_watched",
    "estimatedminuteswatchedperview": "minutes_watched_per_view",
    "averageviewduration": "average_view_duration_s",
    "averageviewpercentage": "avg_view_percentage",
    "impressionsclickthroughrate": "ctr_percent",
    "ctr": "ctr_percent",
    "subscribersgained": "subscribers_gained",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
}

METRIC_FIELDS = (
    "views",
    "impressions",
    "ctr_percent",
    "average_view_duration_s",
    "avg_view_percentage",
    "estimated_minutes_watched",
    "subscribers_gained",
    "likes",
    "comments",
    "shares",
    "engagement_rate",
    "watch_time_per_view_s",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _canonical(name: str) -> str | None:
    return ALIASES.get(re.sub(r"[^a-z0-9]", "", str(name).lower()))


def _number(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default  # reject NaN and +/-inf


def _video(row: dict) -> dict:
    views = max(0.0, _number(row.get("views")))
    likes = max(0.0, _number(row.get("likes")))
    comments = max(0.0, _number(row.get("comments")))
    shares = max(0.0, _number(row.get("shares")))
    minutes = max(0.0, _number(row.get("estimated_minutes_watched")))
    ctr = _number(row.get("ctr_percent"))
    if ctr <= 1.0:  # API reports a fraction (0.043); fixtures may already use percent (4.3)
        ctr *= 100.0
    avg_duration = _number(row.get("average_view_duration_s"))
    minutes_per_view = _number(row.get("minutes_watched_per_view"))
    if not minutes_per_view and views:
        minutes_per_view = minutes / views
    if not avg_duration and minutes_per_view:
        avg_duration = minutes_per_view * 60.0
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "video_id": str(row.get("video_id") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "tags": [str(t) for t in tags][:20],
        "views": int(views),
        "impressions": int(max(0.0, _number(row.get("impressions")))),
        "ctr_percent": round(max(0.0, min(100.0, ctr)), 3),
        "average_view_duration_s": round(max(0.0, avg_duration), 2),
        "avg_view_percentage": round(
            max(0.0, min(100.0, _number(row.get("avg_view_percentage")))), 2
        ),
        "estimated_minutes_watched": round(minutes, 1),
        "subscribers_gained": int(_number(row.get("subscribers_gained"))),
        "likes": int(likes),
        "comments": int(comments),
        "shares": int(shares),
        "engagement_rate": round((likes + comments + shares) / views * 100.0, 3) if views else 0.0,
        "watch_time_per_view_s": round(minutes * 60.0 / views, 2) if views else 0.0,
    }


def _api_rows(raw: dict) -> list[dict]:
    rows: list[dict] = []
    for report in raw.get("reports") or []:
        headers = []
        for header in report.get("columnHeaders") or []:
            canonical = _canonical(header.get("name", ""))
            headers.append(canonical)
        for values in (report.get("data") or {}).get("rows") or []:
            row = {key: value for key, value in zip(headers, values) if key}
            rows.append(row)
    return rows


def normalize_report(raw: dict, *, source: str = "local_fixture") -> dict:
    """Normalize an API-shaped or simplified analytics payload into a performance snapshot."""
    if not isinstance(raw, dict):
        raise ValidationError("analytics payload must be a JSON object")
    rows = _api_rows(raw)
    if not rows:
        for item in raw.get("videos") or []:
            if not isinstance(item, dict):
                continue
            row = {_canonical(k) or k: v for k, v in item.items()}
            rows.append(row)
    if not rows:
        raise ValidationError(
            "analytics payload contains no video rows",
            hint="provide an API-shaped report or a simplified {videos: [...]} fixture",
        )
    videos = [_video(row) for row in rows]
    videos = [v for v in videos if v["video_id"]]
    if not videos:
        raise ValidationError("every analytics row must have a video_id")

    baseline: dict[str, float] = {}
    for field in METRIC_FIELDS:
        values = [float(v[field]) for v in videos]
        baseline[field] = round(statistics.median(values), 3)
    return {
        "source": source,
        "verified": source == VERIFIED_SOURCE,
        "retrieved_at": raw.get("retrieved_at") or _now(),
        "start_date": raw.get("start_date"),
        "end_date": raw.get("end_date"),
        "metric_fields": list(METRIC_FIELDS),
        "videos": videos,
        "baseline": baseline,
        "totals": {
            "videos": len(videos),
            "views": sum(v["views"] for v in videos),
            "estimated_minutes_watched": round(
                sum(v["estimated_minutes_watched"] for v in videos), 1
            ),
            "subscribers_gained": sum(v["subscribers_gained"] for v in videos),
        },
        "note": (
            "Verified data from the YouTube Analytics API."
            if source == VERIFIED_SOURCE
            else "Local fixture: numbers are examples, not verified channel performance."
        ),
    }


def save_snapshot(ws, snapshot: dict) -> Path:
    from creator_core.workspace.atomic import atomic_write_json

    directory = Path(ws.analytics) / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", snapshot.get("retrieved_at") or _now())[:14] or "00000000000000"
    path = directory / f"snapshot_{stamp}.json"
    atomic_write_json(path, snapshot)
    return path


def load_snapshot(path: Path | str) -> dict:
    candidate = Path(path)
    if not candidate.is_file():
        raise ValidationError(f"analytics snapshot not found: {candidate}")
    try:
        import json

        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"analytics snapshot is unreadable: {exc}") from exc
    if not isinstance(data, dict) or "videos" not in data:
        raise ValidationError(f"not an analytics snapshot: {candidate}")
    return data


def latest_snapshot(ws) -> Path | None:
    directory = Path(ws.analytics) / "snapshots"
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("snapshot_*.json"))
    return files[-1] if files else None
