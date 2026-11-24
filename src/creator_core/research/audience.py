"""AudienceAnalyzer (module #3): honest analysis from declared profile + public signals.

No demographic facts are invented: direct audience demographics would require a dedicated
Audience API report, which is not ingested here. The Phase 9 analytics module covers video
performance, not demographics. Every conclusion here is labeled estimated/hypothesis.
"""

from __future__ import annotations

from creator_core.research.provenance import ESTIMATED, HYPOTHESIS, datum


class AudienceAnalyzer:
    def analyze(self, profile: dict, snapshot: dict | None = None) -> dict:
        declared = profile.get("audience", {})
        interests = list(declared.get("interests") or [])
        _niche = profile.get("channel", {}).get("niche", "")
        snapshot = snapshot or {}
        titles = " ".join(v.get("title", "") for v in snapshot.get("videos", [])).lower()

        inferred = [i for i in interests if i.lower().split()[0] in titles]
        segments = []
        if interests:
            segments.append(
                {
                    "segment": "declared interest cluster",
                    "interests": interests,
                    "label": HYPOTHESIS,
                    "basis": "creator profile declaration",
                }
            )
        if inferred:
            segments.append(
                {
                    "segment": "signals visible in recent titles",
                    "interests": inferred,
                    "label": ESTIMATED,
                    "basis": "title keyword overlap (public video metadata)",
                }
            )

        return {
            "module": "AudienceAnalyzer",
            "language": datum(
                declared.get("language", ""),
                label=HYPOTHESIS,
                source="creator profile",
                method="declared by creator",
            ),
            "age_range": datum(
                declared.get("age_range", ""),
                label=HYPOTHESIS,
                source="creator profile",
                method="declared by creator",
            ),
            "segments": segments,
            "segments_with_public_signal": len(inferred),
            "note": "No measured audience demographics are produced here; the analytics module "
            "covers video performance, not demographics. Declared and inferred signals "
            "are not measurements.",
        }
