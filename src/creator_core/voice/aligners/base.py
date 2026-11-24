"""Alignment backend interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WordAlignment:
    word: str
    start: float
    end: float
    confidence: float | None = None
    status: str = "measured"  # measured|estimated|missing|uncertain
    method: str = ""


@dataclass
class AlignerResult:
    words: list[WordAlignment] = field(default_factory=list)
    method: str = "unknown"
    model: str = ""
    checkpoint: str = ""
    measured: bool = False
    notes: list[str] = field(default_factory=list)
    # Structured transcript-coverage report (see aligners.coverage). `measured`
    # is only True when coverage.fully_measured is True; downstream consumers must
    # treat a partially covered result as partial, never measured.
    coverage: dict = field(default_factory=dict)


class WordAligner:
    id = "base"
    display_name = "base"

    def is_available(self) -> bool:
        return False

    def align(
        self, audio_path: Path, text: str, *, language: str = "en", device: str | None = None
    ) -> AlignerResult:
        raise NotImplementedError


def get_aligner(config: dict | None = None) -> WordAligner | None:
    cfg = (config or {}).get("voice", {}).get("alignment", {}) if isinstance(config, dict) else {}
    requested = str(cfg.get("aligner") or "auto").lower()
    backends: list[WordAligner] = []
    try:
        from .whisperx_backend import WhisperXAligner

        backends.append(WhisperXAligner())
    except Exception:  # noqa: BLE001, S110 - intentional no-op: non-fatal best-effort path
        pass
    if requested in {"none", "off", "disabled"}:
        return None
    if requested != "auto":
        for b in backends:
            if b.id == requested:
                return b if b.is_available() else None
        return None
    for b in backends:
        if b.is_available():
            return b
    return None
