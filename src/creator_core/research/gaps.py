"""IdeaGapFinder (#7): locate underserved topics from own history, competitors, trends."""

from __future__ import annotations

from collections import defaultdict

from creator_core.research.provenance import ESTIMATED, datum


class IdeaGapFinder:
    def find(self, history: dict, competitors: dict, trends: dict) -> list[dict]:
        own = {t.lower() for t, _ in history.get("repeated_topics", {}).get("value", [])}
        own |= {
            t.lower()
            for _, item in (history.get("format_mix", {}).get("value") or {}).items()
            for t in [item]
            if isinstance(item, str)
        }
        competitor_topics = dict(competitors.get("frequent_topics", {}).get("value", []))
        trend_topics = {
            t["name"].lower(): t for t in [d["value"] for d in trends.get("topics", [])]
        }
        momentum = trends.get("momentum", {}).get("value", {})

        scores: dict[str, float] = defaultdict(float)
        evidence: dict[str, list[dict]] = defaultdict(list)
        for term, n in competitor_topics.items():
            if term in own:
                continue
            scores[term] += min(n, 10) * 0.4
            evidence[term].append(
                {
                    "source": "competitor_snapshots",
                    "signal": n,
                    "label": competitors.get("frequent_topics", {}).get("label"),
                }
            )
        for name, item in trend_topics.items():
            scores[name] += min(float(item.get("signal", 0)), 10) * 0.5
            evidence[name].append(
                {
                    "source": item.get("source") or "trend_snapshot",
                    "signal": item.get("signal"),
                    "label": "estimated",
                }
            )
            if name in momentum:
                scores[name] += max(float(momentum[name]), 0) * 0.1

        gaps = []
        for topic, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
            if score <= 0:
                continue
            gaps.append(
                {
                    "topic": topic,
                    "gap_score": round(score, 2),
                    "support": datum(
                        evidence[topic],
                        label=ESTIMATED,
                        source="multi-source research",
                        method="competitor frequency + trend signal, own-topic exclusion",
                    ),
                    "label": ESTIMATED,
                }
            )
        return gaps
