"""Optional speaker-turn diarization with an honest no-speaker default.

The default never guesses who is speaking. The offline heuristic groups transcript segments into
*turns* using pause gaps and alternates neutral turn labels — these are turn indices, not speaker
identities. Optional vendor diarization is lazily imported and fails closed when unavailable.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Protocol

from creator_core.cli.errors import MissingDependencyError, ValidationError
from creator_core.media.injection import sanitize_untrusted
from creator_core.research.provenance import HYPOTHESIS


class DiarizationProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def diarize(self, audio_path: Path | str | None, analysis: dict) -> dict: ...


def _segments(analysis: dict) -> list[dict]:
    transcript = (analysis or {}).get("transcript") or {}
    return transcript.get("segments") or []


def _clean(text: object) -> str:
    return sanitize_untrusted(str(text or "")).replace("\n", " ").strip()


def heuristic_turns(segments: list[dict], *, pause_s: float = 0.8) -> list[dict]:
    """Group adjacent transcript segments into turns separated by a pause of `pause_s`."""
    ordered = []
    for seg in segments:
        try:
            start, end = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        ordered.append((start, end, _clean(seg.get("text"))))
    ordered.sort(key=lambda item: item[0])

    groups: list[dict] = []
    for start, end, text in ordered:
        if groups and start - groups[-1]["end"] >= pause_s:
            groups.append({"start": start, "end": end, "texts": [text], "split": "pause"})
        elif groups:
            groups[-1]["end"] = max(groups[-1]["end"], end)
            if text:
                groups[-1]["texts"].append(text)
        else:
            groups.append({"start": start, "end": end, "texts": [text], "split": "start"})

    turns = []
    for i, group in enumerate(groups, start=1):
        turns.append(
            {
                "start": round(group["start"], 3),
                "end": round(group["end"], 3),
                "speaker": f"speaker_turn_{(i - 1) % 2 + 1}",
                "label": HYPOTHESIS,
                "text": " ".join(t for t in group["texts"] if t),
                "evidence": (
                    f"pause gap >= {pause_s:.2f}s between transcript segments"
                    if group["split"] == "pause"
                    else "first continuous speech segment"
                ),
                "note": "Turn index only - not speaker identity or identification.",
            }
        )
    return turns


class NullDiarizer:
    """Always safe: explicitly produces no speaker turns."""

    name = "none"

    def available(self) -> bool:
        return False

    def diarize(self, audio_path: Path | str | None, analysis: dict) -> dict:
        return {
            "provider": self.name,
            "model": None,
            "available": False,
            "turns": [],
            "speaker_count": 0,
            "diarized_transcript": [],
            "label": None,
            "note": "No diarization was performed; no speakers or turns were inferred.",
        }


class HeuristicDiarizer:
    """Deterministic, offline turn segmentation from transcript pauses only."""

    name = "heuristic"

    def __init__(self, pause_s: float = 0.8):
        self.pause_s = float(pause_s)

    def available(self) -> bool:
        return True

    def diarize(self, audio_path: Path | str | None, analysis: dict) -> dict:
        source = _segments(analysis)
        turns = heuristic_turns(source, pause_s=self.pause_s)
        assigned: list[dict] = []
        for seg in source:
            try:
                start = float(seg["start"])
            except (KeyError, TypeError, ValueError):
                continue
            match = next((t for t in turns if t["start"] <= start <= t["end"]), None)
            item = {
                "start": seg["start"],
                "end": seg["end"],
                "text": _clean(seg.get("text")),
                "speaker": match["speaker"] if match else None,
                "label": HYPOTHESIS,
            }
            assigned.append(item)
        return {
            "provider": self.name,
            "model": None,
            "available": True,
            "turns": turns,
            "speaker_count": len({t["speaker"] for t in turns}),
            "diarized_transcript": assigned,
            "label": HYPOTHESIS,
            "pause_s": self.pause_s,
            "note": (
                "Pause-based speaker turns (hypothesis), not identity. "
                "This heuristic does not analyse voices and never names people."
            ),
        }


class PyannoteDiarizer:
    """Optional vendor diarization; imported only when explicitly selected."""

    name = "pyannote"

    def __init__(self, model: str = "pyannote/speaker-diarization-3.1"):
        self.model = model

    def available(self) -> bool:
        try:
            return importlib.util.find_spec("pyannote.audio") is not None
        except (ImportError, ValueError):  # parent package missing / malformed spec
            return False

    def diarize(self, audio_path: Path | str | None, analysis: dict) -> dict:
        if not self.available():
            raise MissingDependencyError(
                "pyannote.audio is required for model-based diarization",
                hint="pip install 'universal-creator-agent[diarize]' (model access is separate)",
            )
        if not audio_path:
            raise ValidationError("model diarization requires an audio file")
        try:
            from pyannote.audio import Pipeline

            pipeline = Pipeline.from_pretrained(self.model)
            output = pipeline(str(audio_path))
            turns = []
            for turn, _, speaker in output.itertracks(yield_label=True):
                turns.append(
                    {
                        "start": round(float(turn.start), 3),
                        "end": round(float(turn.end), 3),
                        "speaker": str(speaker),
                        "label": HYPOTHESIS,
                        "text": "",
                        "evidence": f"pyannote model {self.model}",
                        "note": "Model-produced speaker label; verify before publishing.",
                    }
                )
        except Exception as exc:
            raise ValidationError(f"diarization model failed: {type(exc).__name__}") from exc
        return {
            "provider": self.name,
            "model": self.model,
            "available": True,
            "turns": turns,
            "speaker_count": len({t["speaker"] for t in turns}),
            "diarized_transcript": [],
            "label": HYPOTHESIS,
            "note": "Model speaker labels are hypotheses, not verified identities.",
        }


def get_provider(config: dict | None, *, enabled: bool = False) -> DiarizationProvider:
    cfg = ((config or {}).get("providers") or {}).get("diarization")
    if not enabled or not cfg or not isinstance(cfg, dict):
        return NullDiarizer()
    kind = str(cfg.get("kind") or "").strip().lower()
    if kind == "heuristic":
        return HeuristicDiarizer(cfg.get("pause_s", 0.8))
    if kind == "pyannote":
        return PyannoteDiarizer(cfg.get("model") or "pyannote/speaker-diarization-3.1")
    if kind == "auto":
        candidate = PyannoteDiarizer(cfg.get("model") or "pyannote/speaker-diarization-3.1")
        return candidate if candidate.available() else HeuristicDiarizer(cfg.get("pause_s", 0.8))
    return NullDiarizer()
