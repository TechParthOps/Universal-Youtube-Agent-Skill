"""OpportunityScorer (#8): transparent, configurable criteria. Never a virality prediction."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_WEIGHTS: dict[str, float] = {
    "audience_fit": 0.25,
    "evidence_strength": 0.15,
    "differentiation": 0.20,
    "feasibility": 0.10,
    "channel_consistency": 0.15,
    "trend_momentum": 0.10,
    "risk_inverse": 0.05,
}

CRITERIA_HELP = {
    "audience_fit": "topic overlap with declared audience interests / niche",
    "evidence_strength": "amount of verified or estimated supporting evidence",
    "differentiation": "topic is present in competitor/trend data but absent from own history",
    "feasibility": "inverse of estimated production difficulty",
    "channel_consistency": "overlap with creator's established categories",
    "trend_momentum": "current signal and delta from prior trend snapshot",
    "risk_inverse": "inverse of count of documented risks",
}


def load_weights(path: Path | None) -> dict[str, float]:
    if path is None or not Path(path).is_file():
        return dict(DEFAULT_WEIGHTS)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_WEIGHTS)
    weights = dict(DEFAULT_WEIGHTS)
    for key, value in (data.get("weights") or data).items():
        if key in weights:
            try:
                weights[key] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                continue
    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: round(v / total, 4) for k, v in weights.items()}


class OpportunityScorer:
    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or dict(DEFAULT_WEIGHTS)

    def score(
        self,
        *,
        audience_fit: float,
        evidence_strength: float,
        differentiation: float,
        feasibility: float,
        channel_consistency: float,
        trend_momentum: float,
        risk_inverse: float,
    ) -> dict:
        raw = {
            "audience_fit": audience_fit,
            "evidence_strength": evidence_strength,
            "differentiation": differentiation,
            "feasibility": feasibility,
            "channel_consistency": channel_consistency,
            "trend_momentum": trend_momentum,
            "risk_inverse": risk_inverse,
        }
        for key, value in raw.items():
            if not 0 <= value <= 5:
                raise ValueError(f"criterion {key} must be between 0 and 5")
        total = sum(self.weights.get(k, 0) * v for k, v in raw.items())
        label = (
            "strong opportunity"
            if total >= 4
            else "moderate opportunity"
            if total >= 3
            else "weak opportunity"
        )
        return {
            "score": round(total, 2),
            "score_scale": "0-5, weighted average of the criteria below",
            "label": label,
            "criteria": {
                k: {
                    "value": round(v, 2),
                    "weight": self.weights.get(k, 0),
                    "how_measured": CRITERIA_HELP[k],
                }
                for k, v in raw.items()
            },
            "disclaimer": "Opportunity assessment for planning only; NOT a prediction of "
            "views, virality, or guaranteed performance.",
        }
