"""Transcript-coverage validation for forced alignment (fail-closed).

Valid timestamps alone do NOT prove a forced aligner covered the whole input
transcript. This layer compares the tokens the aligner returned against the tokens
of the original transcript, so a partial result can never be labelled a complete
measured alignment. It never deletes missing tokens and never invents timestamps.

Measured classification requires ALL of: full transcript coverage, valid token
mapping, no unresolved missing/duplicate/unexpected tokens, and every returned word
timestamp passing ordering/duration validation.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

_TOKEN_RE = re.compile(r"\S+")
_EDGE_PUNCT = " \t\r\n.,;:!?\"'`“”‘’()[]{}<>…—–-"
_COLLAPSE_RE = re.compile(r"['’`\-]")

# Default acceptance threshold: complete coverage. A partially covered transcript is
# never promoted to "measured" just because the returned words have valid timestamps.
DEFAULT_COVERAGE_THRESHOLD = 1.0


def _norm(token: str) -> str:
    """Lowercased token with edge punctuation removed; internal marks preserved."""
    return str(token).strip().lower().strip(_EDGE_PUNCT)


def _collapse(token: str) -> str:
    """Contraction/compound-tolerant match key (drops intra-word apostrophes/hyphens)."""
    return _COLLAPSE_RE.sub("", _norm(token))


def tokenize(text: str) -> list[str]:
    return [m.group(0) for m in _TOKEN_RE.finditer(str(text or ""))]


@dataclass
class CoverageReport:
    expected_count: int = 0
    aligned_count: int = 0
    matched: int = 0
    missing: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    coverage: float = 0.0
    timestamp_invalid: int = 0
    unordered: int = 0
    overlaps: int = 0
    out_of_duration: int = 0
    fully_measured: bool = False
    partial: bool = False
    notes: list[str] = field(default_factory=list)
    # aligned words annotated with token->source mapping + per-word validity
    annotated: list[dict] = field(default_factory=list)


def _num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _word_field(word, key: str):
    if isinstance(word, dict):
        return word.get(key)
    return getattr(word, key, None)


def validate_alignment(
    text: str,
    words: list,
    *,
    duration: float,
    tolerance: float = 0.05,
    threshold: float = DEFAULT_COVERAGE_THRESHOLD,
) -> CoverageReport:
    """Compare aligned words against the input transcript and validate timestamps.

    `duration` is the audio duration used for boundary checks. `threshold` is the
    minimum transcript coverage required to classify a result as fully measured.
    """
    expected = tokenize(text)
    aligned = list(words or [])
    rep = CoverageReport(expected_count=len(expected), aligned_count=len(aligned))

    expected_keys = [_collapse(t) for t in expected]
    expected_set = set(expected_keys)
    aligned_tokens = [str(_word_field(w, "word") or "") for w in aligned]
    aligned_keys = [_collapse(t) for t in aligned_tokens]

    matcher = SequenceMatcher(a=expected_keys, b=aligned_keys, autojunk=False)
    matched_expected: set[int] = set()
    matched_aligned: set[int] = set()
    matched_value_counts: Counter[str] = Counter()
    source_of: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for off in range(block.size):
            ei, ai = block.a + off, block.b + off
            matched_expected.add(ei)
            matched_aligned.add(ai)
            source_of[ai] = ei
            matched_value_counts[expected_keys[ei]] += 1
    rep.matched = len(matched_expected)
    rep.missing = [expected[i] for i in range(len(expected)) if i not in matched_expected]
    for j in range(len(aligned)):
        if j in matched_aligned:
            continue
        if aligned_keys[j] in expected_set:
            # A returned token that matches an expected token but had no free slot:
            # an extra occurrence of a transcript word -> duplicate.
            rep.duplicates.append(aligned_tokens[j])
        else:
            rep.unexpected.append(aligned_tokens[j])
    rep.coverage = round(rep.matched / max(1, len(expected)), 4)

    # Timestamp validation (independent of transcript coverage).
    prev_end = -math.inf
    duration = float(duration)
    for j, word in enumerate(aligned):
        s = _num(_word_field(word, "start"))
        e = _num(_word_field(word, "end"))
        conf = _num(_word_field(word, "confidence"))
        valid = True
        if s is None or e is None:
            rep.timestamp_invalid += 1
            valid = False
        else:
            if s < -tolerance or e < -tolerance or e <= s + 1e-9:
                rep.timestamp_invalid += 1
                valid = False
            if duration and (s > duration + tolerance or e > duration + tolerance):
                rep.out_of_duration += 1
                valid = False
            if prev_end != -math.inf and s < prev_end - tolerance:
                if e > s:
                    rep.overlaps += 1
                else:
                    rep.unordered += 1
                valid = False
            if e > s:
                prev_end = max(prev_end, e)
        rep.annotated.append(
            {
                "aligned_index": j,
                "word": aligned_tokens[j],
                "source_index": source_of.get(j),
                "start": s,
                "end": e,
                "confidence": conf,
                "timestamp_valid": valid,
            }
        )

    complete = (
        len(expected) > 0
        and len(aligned) > 0
        and not rep.missing
        and not rep.duplicates
        and not rep.unexpected
        and rep.timestamp_invalid == 0
        and rep.unordered == 0
        and rep.overlaps == 0
        and rep.out_of_duration == 0
        and rep.coverage >= threshold
    )
    rep.fully_measured = complete
    rep.partial = len(aligned) > 0 and not complete
    notes = [
        (
            f"coverage={rep.coverage:.3f} of {len(expected)} expected tokens; "
            f"missing={len(rep.missing)}; duplicates={len(rep.duplicates)}; "
            f"unexpected={len(rep.unexpected)}; "
            f"timestamp_invalid={rep.timestamp_invalid}; overlaps={rep.overlaps}; "
            f"unordered={rep.unordered}; out_of_duration={rep.out_of_duration}"
        )
    ]
    if not complete:
        if len(expected) == 0:
            notes.append("empty transcript: nothing to align; not a measured alignment")
        elif len(aligned) == 0:
            notes.append("aligner returned no words; result is not measured")
        else:
            notes.append(
                "alignment did not cover the full transcript with fully valid timestamps; "
                "classified PARTIAL, not measured. No missing words were deleted and no "
                "timestamps were invented."
            )
    rep.notes = notes
    return rep
