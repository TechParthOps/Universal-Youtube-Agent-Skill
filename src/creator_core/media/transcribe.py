"""Optional transcription providers (ADR-005).

The core is fully functional without ASR: when faster-whisper is absent or fails, the report
says so and carries no fabricated transcript. Optional providers are selected by config:
  config["providers"]["asr"] = {"kind": "faster_whisper", "model": "small", "language": "en"}
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from creator_core.media.injection import analyze_untrusted, sanitize_untrusted


class TranscriptionProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def transcribe(self, audio_path: Path) -> dict: ...


class NullTranscriber:
    name = "none"

    def __init__(self, hint: str = "transcription not requested or provider unavailable"):
        self.hint = hint

    def available(self) -> bool:
        return False

    def transcribe(self, audio_path: Path) -> dict:
        return {
            "available": False,
            "provider": self.name,
            "segments": [],
            "hint": self.hint,
            "note": "No transcript was produced; nothing is inferred.",
        }


class FasterWhisperTranscriber:
    name = "faster_whisper"

    def __init__(self, model: str = "small", language: str | None = None):
        self.model_name = model
        self.language = language
        self._model = None

    def available(self) -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec("faster_whisper") is not None
        except (ImportError, ValueError):
            return False

    def transcribe(self, audio_path: Path) -> dict:
        if not self.available():
            return NullTranscriber(
                "faster-whisper is not installed "
                "(pip install 'universal-creator-agent[transcribe]')"
            ).transcribe(audio_path)
        try:
            from faster_whisper import WhisperModel

            if self._model is None:
                self._model = WhisperModel(self.model_name, device="auto", compute_type="int8")
            segments, info = self._model.transcribe(str(audio_path), language=self.language)
            result = []
            for seg in segments:
                text = seg.text.strip()
                result.append(
                    {
                        "start": round(float(seg.start), 3),
                        "end": round(float(seg.end), 3),
                        "text": text,
                        "display_text": sanitize_untrusted(text),
                        "injection": analyze_untrusted(text),
                        "confidence": round(float(getattr(seg, "avg_logprob", 0.0)), 4),
                    }
                )
            return {
                "available": True,
                "provider": self.name,
                "model": self.model_name,
                "language": getattr(info, "language", self.language),
                "segments": result,
                "note": "Timestamps come from the ASR model; verify before publishing captions.",
            }
        except Exception as exc:  # provider failure must degrade, never fabricate  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
            return {
                "available": False,
                "provider": self.name,
                "segments": [],
                "error": f"{exc.__class__.__name__}: {exc}",
                "note": "Transcription failed; no transcript was produced.",
            }


def get_provider(config: dict, *, enabled: bool = False) -> TranscriptionProvider:
    if not enabled:
        return NullTranscriber("transcription not requested (pass --transcribe)")
    settings = (config.get("providers") or {}).get("asr") or {}
    kind = settings.get("kind", "faster_whisper")
    if kind == "none":
        return NullTranscriber("ASR disabled in configuration")
    if kind == "faster_whisper":
        return FasterWhisperTranscriber(settings.get("model", "small"), settings.get("language"))
    return NullTranscriber(f"unknown ASR provider: {kind}")
