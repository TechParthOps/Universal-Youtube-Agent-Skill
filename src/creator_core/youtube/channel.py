"""ChannelAnalyzer — pure analysis of channel snapshots (module #1).

No network calls here. `creator channel connect --from-file` imports a local JSON fixture;
`creator youtube connect --live` fetches basic channel identity but does not yet produce a
ChannelAnalyzer snapshot. All numeric claims are labeled by the source of the snapshot (verified
only when sourced from the API).
"""

from __future__ import annotations

import re
import statistics
from collections import Counter

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "how",
    "why",
    "what",
    "with",
    "for",
    "to",
    "of",
    "in",
    "on",
    "at",
    "is",
    "are",
    "my",
    "i",
    "we",
    "you",
    "this",
    "that",
    "it",
    "from",
}


def normalize_snapshot(raw: dict, *, source: str = "local_fixture") -> dict:
    """Normalize either an API-shaped response or a simplified local fixture.

    Expected simplified shape:
      {"channel": {"id","title","description","subscribers"},
       "videos": [{"id","title","views","duration_seconds","published_at","tags"}]}
    """
    if not isinstance(raw, dict):
        raise ValueError("snapshot must be an object")  # noqa: TRY004 - preserve existing ValidationError contract for callers
    channel_raw = raw.get("channel") or {}
    videos_raw = raw.get("videos") or []
    videos = []
    for v in videos_raw:
        snippet = v.get("snippet") or {}
        stats = v.get("statistics") or {}
        content = v.get("contentDetails") or {}
        videos.append(
            {
                "id": v.get("id") or v.get("video_id") or "",
                "title": v.get("title") or snippet.get("title") or "",
                "views": int(v.get("views") or stats.get("viewCount") or 0),
                "duration_seconds": int(
                    v.get("duration_seconds")
                    or _parse_iso_duration(content.get("duration", ""))
                    or 0
                ),
                "published_at": v.get("published_at") or snippet.get("publishedAt") or "",
                "tags": v.get("tags") or snippet.get("tags") or [],
            }
        )

    views = [v["views"] for v in videos] or [0]
    snapshot = {
        "source": source,
        "verified": source == "youtube_data_api",
        "retrieved_at": raw.get("retrieved_at"),
        "channel": {
            "id": channel_raw.get("id") or channel_raw.get("channelId") or "",
            "title": channel_raw.get("title") or channel_raw.get("name") or "",
            "description": channel_raw.get("description") or "",
            "subscribers": int(
                channel_raw.get("subscribers") or channel_raw.get("subscriberCount") or 0
            ),
        },
        "videos": videos,
        "baseline": {
            "video_count": len(videos),
            "median_views": statistics.median(views),
            "mean_views": round(statistics.mean(views), 1),
            "max_views": max(views),
        },
    }
    return snapshot


def _parse_iso_duration(text: str) -> int:
    """ISO-8601 duration (PT1H2M3S) → seconds; 0 when absent/unparseable."""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text or "")
    if not match:
        return 0
    h, m, s = (int(x or 0) for x in match.groups())
    return h * 3600 + m * 60 + s


def analyze(profile: dict, snapshot: dict) -> dict:
    """Evidence-backed channel analysis. Interpretations are labeled hypotheses."""
    videos = snapshot["videos"]
    baseline = snapshot["baseline"]
    median = baseline["median_views"] or 1
    outliers = [v for v in videos if v["views"] >= 2 * median]
    repeated = _repeated_topics(videos)
    top = sorted(videos, key=lambda v: v["views"], reverse=True)[:5]

    format_counts = Counter()
    for v in videos:
        fmt = "short" if 0 < v["duration_seconds"] <= 60 else "long_form"
        format_counts[fmt] += 1

    findings = [
        {
            "id": "baseline",
            "kind": "verified" if snapshot["verified"] else "estimated",
            "statement": f"Channel baseline median views: {baseline['median_views']:.0f} "
            f"across {baseline['video_count']} sampled videos.",
            "evidence": {
                "median_views": baseline["median_views"],
                "sample_size": baseline["video_count"],
                "source": snapshot["source"],
            },
        },
        {
            "id": "outliers",
            "kind": "verified" if snapshot["verified"] else "estimated",
            "statement": f"{len(outliers)} videos reached at least 2x the channel median.",
            "evidence": {"video_ids": [v["id"] for v in outliers[:20]], "threshold": 2 * median},
        },
        {
            "id": "format_mix",
            "kind": "verified" if snapshot["verified"] else "estimated",
            "statement": "Format mix in sample: "
            + ", ".join(f"{k}={n}" for k, n in sorted(format_counts.items()))
            + ".",
            "evidence": dict(format_counts),
        },
    ]
    if repeated:
        findings.append(
            {
                "id": "repeated_topics",
                "kind": "hypothesis",
                "statement": "Frequently appearing title terms: "
                + ", ".join(f"{term} ({n})" for term, n in repeated[:8])
                + ".",
                "evidence": {"term_counts": repeated[:20]},
            }
        )

    return {
        "command": "analyze channel",
        "ok": True,
        "profile_id": profile.get("profile_id"),
        "snapshot_source": snapshot["source"],
        "verified": snapshot["verified"],
        "top_videos": top,
        "outlier_videos": outliers[:20],
        "findings": findings,
        "note": "Findings describe the sample; they are not predictions of future performance.",
    }


def _repeated_topics(videos: list[dict], *, min_count: int = 2) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for v in videos:
        terms = set(re.findall(r"[a-z0-9+#]{3,}", v["title"].lower())) - STOPWORDS
        counts.update(terms)
        for tag in v.get("tags") or []:
            if isinstance(tag, str) and len(tag) > 2:
                counts[tag.lower()] += 1
    return [(term, n) for term, n in counts.most_common() if n >= min_count]
