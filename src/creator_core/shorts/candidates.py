"""Rank Short candidate windows from measured analysis signals.

Scores are transparent, deterministic heuristics. They never claim virality and never invent a
signal: a missing transcript, scene detector or audio detector simply contributes zero with an
explicit evidence note. Every emitted score is `estimated`; underlying detector values keep their
own provenance labels from `media analyze`.
"""

from __future__ import annotations

import re
from pathlib import Path

from creator_core.research.provenance import ESTIMATED, HYPOTHESIS, VERIFIED, datum

DEFAULT_TARGET_S = 30.0
MIN_WINDOW_S = 6.0
MAX_WINDOW_S = 60.0
MAX_CANDIDATES = 5

SCORE_WEIGHTS = {
    "audio_energy": 0.30,
    "visual_change": 0.20,
    "narrative_completeness": 0.20,
    "creator_relevance": 0.15,
    "duration_fit": 0.15,
}
SILENCE_PENALTY_WEIGHT = 0.25
_WORD = re.compile(r"[a-z0-9']+")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _analysis(report: dict) -> dict:
    return report.get("analysis", report) if isinstance(report, dict) else {}


def _fit_window(center: float, target: float, duration: float) -> tuple[float, float]:
    start = max(0.0, center - target / 2.0)
    end = start + target
    if duration > 0 and end > duration:
        end = min(duration, end)
        start = max(0.0, end - target)
    return round(start, 3), round(end, 3)


def _profile_terms(profile: dict | None) -> set[str]:
    if not profile:
        return set()
    channel = profile.get("channel") or {}
    audience = profile.get("audience") or {}
    content = profile.get("content") or {}
    voice = profile.get("voice") or {}
    raw: list[str] = []
    for value in (channel.get("niche"), channel.get("category")):
        if isinstance(value, str):
            raw.append(value)
    for key in ("sub_niches",):
        raw += [str(v) for v in (channel.get(key) or [])]
    for key in ("interests",):
        raw += [str(v) for v in (audience.get(key) or [])]
    for key in ("categories", "series"):
        raw += [str(v) for v in (content.get(key) or [])]
    raw += [str(v) for v in (voice.get("signature_phrases") or [])]
    return {word for value in raw for word in _WORD.findall(value.lower()) if len(word) > 2}


def _seed_windows(
    analysis: dict, *, target: float, duration: float, source_name: str
) -> list[dict]:
    """Create candidate windows from whichever detectors actually produced data."""
    seeds: dict[tuple[float, float], dict] = {}

    def add(start: float, end: float, kind: str, ref: float) -> None:
        if end - start < min(MIN_WINDOW_S, max(1.0, target / 3.0)):
            return
        key = (round(start, 1), round(end, 1))
        entry = seeds.setdefault(key, {"start": start, "end": end, "signals": []})
        if kind not in entry["signals"]:
            entry["signals"].append(kind)
        entry.setdefault("anchors", {})[kind] = round(ref, 3)

    for seg in (analysis.get("audio_events") or {}).get("segments", []):
        try:
            center = (float(seg["start"]) + float(seg["end"])) / 2.0
        except (KeyError, TypeError, ValueError):
            continue
        add(*_fit_window(center, target, duration), "audio_spike", center)

    for seg in (analysis.get("scenes") or {}).get("segments", []):
        try:
            center = float(seg.get("timestamp", seg.get("start", 0)))
        except (TypeError, ValueError):
            continue
        add(*_fit_window(center, target, duration), "scene_cut", center)

    for seg in (analysis.get("moments") or {}).get("segments", []):
        try:
            center = float(seg.get("center", (float(seg["start"]) + float(seg["end"])) / 2.0))
        except (KeyError, TypeError, ValueError):
            continue
        add(*_fit_window(center, target, duration), "ranked_moment", center)

    transcript = sorted(
        (s for s in (analysis.get("transcript") or {}).get("segments", []) if isinstance(s, dict)),
        key=lambda s: float(s.get("start", 0)),
    )
    # Group contiguous transcript segments up to `target`, respecting sentence boundaries.
    group: list[dict] = []
    for seg in transcript:
        try:
            float(seg["start"])
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if group and (end - float(group[0]["start"])) > target:
            add(
                float(group[0]["start"]),
                float(group[-1]["end"]),
                "transcript",
                float(group[0]["start"]),
            )
            group = []
        group.append(seg)
    if group:
        add(
            float(group[0]["start"]),
            float(group[-1]["end"]),
            "transcript",
            float(group[0]["start"]),
        )

    if not seeds and duration > 0:
        # Uniform sampling is a *suggestion*, not a detected highlight. It is labelled
        # hypothesis and carries no fabricated signal evidence.
        count = max(1, min(MAX_CANDIDATES, int(duration // target) or 1))
        for i in range(count):
            center = min(duration, (i + 0.5) * duration / count)
            add(*_fit_window(center, min(target, duration), duration), "uniform_sample", center)
        if source_name:
            for entry in seeds.values():
                entry["source_name"] = source_name
    return list(seeds.values())


def _score_window(window: dict, analysis: dict, *, profile_terms: set[str], target: float) -> dict:
    start, end = float(window["start"]), float(window["end"])
    span = max(0.001, end - start)
    audio_segs = [
        s
        for s in (analysis.get("audio_events") or {}).get("segments", [])
        if _overlap(start, end, float(s.get("start", 0)), float(s.get("end", 0))) > 0
    ]
    peaks = [float(s.get("peak_rms_db", -90)) for s in audio_segs]
    audio = _clamp((max(peaks) + 40.0) / 40.0) if peaks else 0.0

    cuts = [
        s
        for s in (analysis.get("scenes") or {}).get("segments", [])
        if start <= float(s.get("timestamp", s.get("start", 0))) <= end
    ]
    visual = _clamp(len(cuts) / 3.0)

    transcript = sorted(
        (
            s
            for s in (analysis.get("transcript") or {}).get("segments", [])
            if isinstance(s, dict)
            and _overlap(start, end, float(s.get("start", 0)), float(s.get("end", 0))) > 0
        ),
        key=lambda s: float(s.get("start", 0)),
    )
    if transcript:
        covered = sum(_overlap(start, end, float(s["start"]), float(s["end"])) for s in transcript)
        align_start = min(abs(float(s["start"]) - start) for s in transcript)
        align_end = min(abs(float(s["end"]) - end) for s in transcript)
        narrative = (
            0.6 * _clamp(covered / span)
            + 0.4 * (_clamp(1.0 - align_start / 0.5) + _clamp(1.0 - align_end / 0.5)) / 2.0
        )
    else:
        narrative = 0.0

    text = " ".join(str(s.get("text", "")) for s in transcript).lower()
    text_words = set(_WORD.findall(text)) | set(
        _WORD.findall(str(window.get("source_name", "")).lower())
    )
    relevance = _clamp(len(profile_terms & text_words) / 3.0) if profile_terms else 0.0

    fit = _clamp(1.0 - abs(span - target) / max(target, 1.0))
    silence_segs = [
        s
        for s in (analysis.get("silence") or {}).get("segments", [])
        if _overlap(start, end, float(s.get("start", 0)), float(s.get("end", 0))) > 0
    ]
    silent = sum(_overlap(start, end, float(s["start"]), float(s["end"])) for s in silence_segs)
    penalty = SILENCE_PENALTY_WEIGHT * _clamp(silent / span)

    parts = {
        "audio_energy": round(audio, 3),
        "visual_change": round(visual, 3),
        "narrative_completeness": round(narrative, 3),
        "creator_relevance": round(relevance, 3),
        "duration_fit": round(fit, 3),
        "silence_penalty": round(penalty, 3),
    }
    score = sum(SCORE_WEIGHTS[k] * parts[k] for k in SCORE_WEIGHTS) - penalty
    return {
        "score": round(_clamp(score), 3),
        "score_parts": parts,
        "transcript_segments": [dict(s) for s in transcript],
    }


def rank_candidates(
    report: dict,
    *,
    target_duration: float = DEFAULT_TARGET_S,
    max_candidates: int = MAX_CANDIDATES,
    profile: dict | None = None,
    source_duration: float | None = None,
    source_path: str | Path | None = None,
) -> dict:
    """Return ranked Short windows. `report` is a media-analyze report or its `analysis` dict."""
    try:
        target = float(target_duration)
    except (TypeError, ValueError) as exc:
        from creator_core.cli.errors import ValidationError

        raise ValidationError("target_duration must be a number", hint="use 15-60 seconds") from exc
    if not MIN_WINDOW_S <= target <= MAX_WINDOW_S:
        from creator_core.cli.errors import ValidationError

        raise ValidationError(
            f"target_duration must be between {MIN_WINDOW_S:g} and {MAX_WINDOW_S:g} seconds"
        )
    limit = max(1, min(MAX_CANDIDATES, int(max_candidates)))
    analysis = _analysis(report)
    info = (report or {}).get("source") or {}
    duration = float(
        source_duration if source_duration is not None else info.get("duration_seconds", 0.0) or 0.0
    )
    name = Path(str(source_path or info.get("path") or "")).name
    profile_terms = _profile_terms(profile)

    seeds = _seed_windows(analysis, target=target, duration=duration, source_name=name)
    ranked = []
    for seed in seeds:
        scored = _score_window(seed, analysis, profile_terms=profile_terms, target=target)
        signal_backed = any(s != "uniform_sample" for s in seed["signals"])
        signals = []
        for kind in seed["signals"]:
            if kind == "uniform_sample":
                signals.append(
                    datum(
                        kind,
                        label=HYPOTHESIS,
                        source="uniform sampling",
                        method="no detector signal available; evenly spaced suggestion",
                    )
                )
            else:
                signals.append(
                    datum(
                        kind,
                        label=VERIFIED,
                        source="media analyze",
                        method=f"{kind} detector window anchor",
                    )
                )
        signals.append(
            datum(
                scored["score"],
                label=ESTIMATED,
                source="candidate ranking",
                method="transparent weighted heuristic (see score_parts)",
            )
        )
        reasons = []
        if scored["score_parts"]["audio_energy"] > 0.1:
            reasons.append("measured audio energy")
        if scored["score_parts"]["visual_change"] > 0.1:
            reasons.append("scene cuts")
        if not scored["score_parts"]["narrative_completeness"]:
            reasons.append("no transcript available")
        if scored["score_parts"]["silence_penalty"] > 0.05:
            reasons.append("overlaps detected silence")
        ranked.append(
            {
                "start": seed["start"],
                "end": seed["end"],
                "duration": round(seed["end"] - seed["start"], 3),
                "score": scored["score"],
                "score_parts": scored["score_parts"],
                "signals": signals,
                "label": ESTIMATED,
                "signal_backed": signal_backed,
                "reason": "; ".join(reasons) or "uniform suggestion only",
                "transcript_segments": scored["transcript_segments"],
            }
        )

    ranked.sort(key=lambda c: (-c["score"], c["start"]))
    # Suppress near-duplicate windows so one signal does not consume every slot.
    chosen: list[dict] = []
    for candidate in ranked:
        if any(
            _overlap(candidate["start"], candidate["end"], c["start"], c["end"])
            > 0.8 * min(candidate["duration"], c["duration"])
            for c in chosen
        ):
            continue
        chosen.append(candidate)
        if len(chosen) == limit:
            break
    for i, candidate in enumerate(chosen, start=1):
        candidate["short_id"] = f"short_{i:03d}"

    return {
        "ok": True,
        "target_duration": target,
        "count": len(chosen),
        "candidates": chosen,
        "label": ESTIMATED,
        "limitations": [
            "Scores are deterministic heuristics, not predictions of views or virality.",
            "No transcript is invented: narrative completeness is zero without ASR output.",
            "Uniform suggestions are labelled hypothesis and carry no detector evidence.",
        ],
    }
