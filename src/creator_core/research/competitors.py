"""CompetitorAnalyzer (module #5): public-snapshot comparison (no scraping)."""

from __future__ import annotations

import re
from collections import Counter

from creator_core.research.provenance import ESTIMATED, VERIFIED, datum
from creator_core.youtube.channel import STOPWORDS


class CompetitorAnalyzer:
    def analyze(self, own_snapshot: dict, competitor_snapshots: list[dict]) -> dict:
        all_videos = [v for snap in competitor_snapshots for v in snap.get("videos", [])]
        counts: Counter[str] = Counter()
        verified = all(bool(s.get("verified")) for s in competitor_snapshots) and bool(
            competitor_snapshots
        )
        source = "competitor_snapshots"
        for v in all_videos:
            counts.update(set(re.findall(r"[a-z0-9+#]{3,}", v["title"].lower())) - STOPWORDS)
            for tag in v.get("tags") or []:
                if isinstance(tag, str) and len(tag) > 2:
                    counts[tag.lower()] += 1

        own_terms = set()
        for v in own_snapshot.get("videos", []):
            own_terms.update(re.findall(r"[a-z0-9+#]{3,}", v["title"].lower()))
        gaps = [(term, n) for term, n in counts.most_common(30) if n >= 2 and term not in own_terms]

        return {
            "module": "CompetitorAnalyzer",
            "competitors": [s.get("channel", {}).get("title", "") for s in competitor_snapshots],
            "videos_sampled": len(all_videos),
            "frequent_topics": datum(
                counts.most_common(20),
                label=VERIFIED if verified else ESTIMATED,
                source=source,
                method="title/tag frequency across snapshots",
            ),
            "topics_absent_from_own_history": datum(
                gaps,
                label=ESTIMATED,
                source=source,
                method="competitor terms absent from own title terms",
            ),
            "note": "Public metadata only; competitor intent and results are not observable.",
            "method_note": "Gaps are competitor terms not present in own title terms, "
            "seen at least twice in the sample.",
        }
