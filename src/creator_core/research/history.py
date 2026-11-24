"""ContentHistoryAnalyzer (module #4): performance vs channel baseline, repeated topics."""

from __future__ import annotations

import re
from collections import Counter

from creator_core.research.provenance import ESTIMATED, VERIFIED, datum
from creator_core.youtube.channel import STOPWORDS


class ContentHistoryAnalyzer:
    def analyze(self, snapshot: dict) -> dict:
        videos = snapshot.get("videos", [])
        baseline = snapshot.get("baseline", {})
        median = baseline.get("median_views", 0) or 1
        source = snapshot.get("source", "unknown")
        label = VERIFIED if snapshot.get("verified") else ESTIMATED

        above = [v for v in videos if v["views"] >= median]
        below = [v for v in videos if v["views"] < median]
        term_counts: Counter[str] = Counter()
        for v in videos:
            term_counts.update(set(re.findall(r"[a-z0-9+#]{3,}", v["title"].lower())) - STOPWORDS)
        durations = [v["duration_seconds"] for v in videos if v["duration_seconds"] > 0]
        fmt = Counter("short" if d <= 60 else "long_form" for d in durations)

        return {
            "module": "ContentHistoryAnalyzer",
            "sample_size": len(videos),
            "median_views": datum(
                baseline.get("median_views", 0),
                label=label,
                source=source,
                method="median(video.views)",
            ),
            "above_baseline": [v["id"] for v in above],
            "below_baseline": [v["id"] for v in below],
            "repeated_topics": datum(
                term_counts.most_common(20),
                label=ESTIMATED,
                source=source,
                method="title term frequency",
            ),
            "format_mix": datum(
                dict(fmt), label=label, source=source, method="duration<=60s ⇒ short"
            ),
            "note": "History describes past samples; it is not a prediction of future results.",
        }
