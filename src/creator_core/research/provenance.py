"""Provenance labels for every research datum (ADR-013)."""

from __future__ import annotations

from datetime import UTC, datetime

VERIFIED = "verified"  # directly observed via API or explicit local fixture
ESTIMATED = "estimated"  # computed from verified data
HYPOTHESIS = "hypothesis"  # interpretation / generated content

_LABELS = {VERIFIED, ESTIMATED, HYPOTHESIS}


def datum(value, *, label: str, source: str, method: str, retrieved_at: str | None = None) -> dict:
    if label not in _LABELS:
        raise ValueError(f"unknown provenance label: {label}")
    return {
        "value": value,
        "label": label,
        "source": source,
        "method": method,
        "retrieved_at": retrieved_at or datetime.now(UTC).isoformat(timespec="seconds"),
    }


def label_of(item: dict) -> str:
    return item.get("label", HYPOTHESIS)


def strongest_label(items: list[dict]) -> str:
    """Return the strongest evidence label present in a list of data/evidence items."""
    labels = {label_of(i) for i in items}
    for candidate in (VERIFIED, ESTIMATED, HYPOTHESIS):
        if candidate in labels:
            return candidate
    return HYPOTHESIS
