"""Idea generation: every idea carries all mandated fields (prompt §4).

Deterministic templates + evidence from research modules. An optional LLM can refine ideas in
Phase 10, but no AI provider is required for usable output here.
"""

from __future__ import annotations

import hashlib
import re

from creator_core.research.provenance import ESTIMATED, HYPOTHESIS, VERIFIED, datum

STRUCTURES = {
    "gaming": ["Hook", "Context", "Build-up", "Escalation", "Climax", "Payoff"],
    "commentary": ["Hook", "Setup", "Argument", "Evidence", "Counterpoint", "Takeaway"],
    "educational": ["Hook", "Prior knowledge", "Concept", "Example", "Recap"],
    "tutorial": ["Hook", "Objective", "Steps", "Demo", "Common mistakes", "Recap"],
    "documentary": ["Hook", "Context", "Investigation", "Revelation", "Payoff"],
    "vlog": ["Hook", "Context", "Event", "Reaction", "Reflection"],
}

_TIME_MINUTES = {1: 30, 2: 60, 3: 120, 4: 240, 5: 480}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#]{3,}", str(text).lower()))


def _idea_id(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40] or "topic"
    # SHA1 is used only to derive a short, stable, non-secret id suffix. It is not a
    # security control, so the non-security use is declared explicitly (B324).
    return (
        f"idea_{slug}_{hashlib.sha1(topic.encode('utf-8'), usedforsecurity=False).hexdigest()[:6]}"
    )


class IdeaGenerator:
    def generate(
        self,
        *,
        profile: dict,
        history: dict,
        audience: dict,
        competitors: dict,
        trends: dict,
        gaps: list[dict],
        scorer,
        limit: int = 10,
    ) -> list[dict]:
        channel = profile.get("channel", {})
        niche = channel.get("niche") or "gaming"
        own_topics = {t.lower() for t, _ in history.get("repeated_topics", {}).get("value", [])}
        categories = {str(c).lower() for c in profile.get("content", {}).get("categories", [])}
        interests = {str(i).lower() for i in profile.get("audience", {}).get("interests", [])}
        signature = list(profile.get("voice", {}).get("signature_phrases") or [])
        trend_map = {d["value"]["name"].lower(): d["value"] for d in trends.get("topics", [])}
        momentum = trends.get("momentum", {}).get("value", {})

        ideas = []
        for gap in gaps[: max(limit * 3, 30)]:
            topic = gap["topic"]
            topic_words = _words(topic)
            evidence = gap.get("support", {}).get("value", [])
            audience_fit = 5 * min(1.0, len(topic_words & (interests | {niche.lower()})) / 2)
            consistency = 5 * min(1.0, len(topic_words & (own_topics | categories)) / 2)
            evidence_strength = min(
                5.0,
                len(evidence) * 2.0
                + (1 if any(e.get("label") == VERIFIED for e in evidence) else 0),
            )
            differentiation = min(5.0, float(gap.get("gap_score", 0)) / 2)
            difficulty = 3 if any(w in topic_words for w in ("ranking", "guide", "tutorial")) else 2
            feasibility = 5 - difficulty
            trend_signal = float(trend_map.get(topic, {}).get("signal", 0))
            trend_momentum = min(5.0, trend_signal / 2 + max(float(momentum.get(topic, 0)), 0))
            risks = []
            if len(evidence) == 1:
                risks.append("Limited corroborating evidence for this topic")
            if "horror" in topic_words or "boss" in topic_words:
                risks.append("Requires capturing rare or unpredictable gameplay moments")
            if trend_momentum > 3:
                risks.append("Interest may be temporary; verify before production")
            risk_inverse = max(0.0, 3 - len(risks)) * 5 / 3

            score = scorer.score(
                audience_fit=audience_fit,
                evidence_strength=evidence_strength,
                differentiation=differentiation,
                feasibility=feasibility,
                channel_consistency=consistency,
                trend_momentum=trend_momentum,
                risk_inverse=risk_inverse,
            )
            fmt = "tutorial" if "guide" in topic_words or "tutorial" in topic_words else niche
            hook = (
                f"{signature[0]} — {topic.title()}."
                if signature
                else f"{topic.title()}: here's what actually happens."
            )
            ideas.append(
                {
                    "idea_id": _idea_id(topic),
                    "concept": datum(
                        f"A {niche} video centered on '{topic}', built from the "
                        f"creator's established {', '.join(sorted(categories)[:2]) or niche} "
                        "style.",
                        label=HYPOTHESIS,
                        source="research synthesis",
                        method="deterministic template over research evidence",
                    ),
                    "suggested_title": datum(
                        f"{topic.title()}: {hook.rstrip('.')}",
                        label=HYPOTHESIS,
                        source="idea generator",
                        method="topic + creator voice",
                    ),
                    "target_audience": datum(
                        audience.get("segments", [{}])[0].get("segment", "declared audience")
                        if audience.get("segments")
                        else "declared audience",
                        label=HYPOTHESIS,
                        source="creator profile",
                        method="profile.audience",
                    ),
                    "content_format": fmt,
                    "opening_hook": datum(
                        hook,
                        label=HYPOTHESIS,
                        source="idea generator",
                        method="creator signature phrase or topic framing",
                    ),
                    "storytelling_structure": STRUCTURES.get(fmt, STRUCTURES["gaming"]),
                    "differentiation": datum(
                        f"Appears in competitor/trend signals but not in {channel.get('name') or 'your'} "
                        "recent titles.",
                        label=ESTIMATED,
                        source="gap analysis",
                        method="topic exclusion",
                    ),
                    "production_difficulty": difficulty,
                    "difficulty_scale": "1 (easy) to 5 (complex)",
                    "estimated_production_time_minutes": _TIME_MINUTES[difficulty],
                    "supporting_evidence": [
                        {
                            "source": e.get("source"),
                            "signal": e.get("signal"),
                            "label": e.get("label"),
                        }
                        for e in evidence
                    ],
                    "risks": risks,
                    "suggested_thumbnail_concept": datum(
                        f"Large {topic.title()} text, one expressive reaction frame, high-contrast "
                        f"brand colors, no more than 4 words.",
                        label=HYPOTHESIS,
                        source="idea generator",
                        method="branding + topic",
                    ),
                    "opportunity": score,
                    "provenance": {
                        "generated_by": "IdeaGenerator/v1",
                        "inputs": [
                            "profile",
                            "history",
                            "audience",
                            "competitors",
                            "trends",
                            "gaps",
                        ],
                    },
                }
            )
        ideas.sort(key=lambda i: i["opportunity"]["score"], reverse=True)
        return ideas[:limit]
