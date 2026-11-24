"""Deterministic offline tone provider — automated tests only (Phase 11D).

This adapter emits a reproducible tone burst pattern, NOT speech. It exists so the whole script →
generate → align → mix → render pipeline can be exercised without a neural model or a host TTS
engine. Every result is flagged `mocked = True`, and the registry refuses to select it for
production generation unless an explicit `allow_test_provider=True` is supplied by tests.
"""

from __future__ import annotations

import array
import hashlib
import math
import uuid
import wave
from pathlib import Path

from creator_core.media.ingest import sha256_file
from creator_core.voice.providers.base import SpeechResult, VoiceProvider


class TestToneProvider(VoiceProvider):
    provider_id = "test_tone"
    display_name = "Deterministic test tone (tests only, not speech)"
    weight_licence = "n/a — locally generated tone, no model or weights"
    commercial_ok = True
    clones_voice = False
    external = False
    tests_only = True
    install_hint = "test_tone is built in; it is only selectable from automated tests"

    # Any language is accepted because no linguistic content is synthesised.
    _LANGUAGES = ["en", "es", "fr", "de", "it", "pt", "zh", "ja", "ko", "hi", "ar"]  # noqa: RUF012 - immutable constant on test-only/provider class

    def supported_languages(self) -> list[str]:
        return list(self._LANGUAGES)

    def supported_controls(self) -> list[str]:
        return ["seconds_per_character"]

    def is_available(self) -> bool:
        return True  # stdlib only

    def validate_model(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "available": True,
            "note": "Deterministic tone generator for tests; this is NOT speech.",
        }

    def generate_speech(
        self, text: str, *, language: str, voice: dict, controls: dict | None = None
    ) -> SpeechResult:
        clean = self.clean_text(text)
        lang = self.normalize_language(language)
        checked = self.validate_controls(controls)
        seconds_per_char = checked.get("seconds_per_character", 0.08)
        try:
            seconds_per_char = max(0.01, min(0.5, float(seconds_per_char)))
        except (TypeError, ValueError) as exc:
            raise ValueError("seconds_per_character must be a number") from exc

        ws = voice.get("workspace")
        if ws is None:
            from creator_core.cli.errors import ProviderError

            raise ProviderError("test_tone requires a workspace for its output")
        out_dir = Path(ws.tmp) / "voice" / "tone"
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / f"tone_{uuid.uuid4().hex}.wav"

        # Deterministic seed from text: identical text → identical audio bytes.
        seed = int.from_bytes(hashlib.sha256(clean.encode("utf-8")).digest()[:4], "big")
        rate = 16000
        duration = max(0.2, min(30.0, len(clean) * seconds_per_char))
        total = int(rate * duration)
        base_hz = 180 + (seed % 120)
        samples = array.array("h")
        for i in range(total):
            # Speech-like cadence: 340 ms tone, 160 ms gap, with a deterministic seed jitter.
            phase = i % int(rate * 0.5)
            amplitude = 6000 if phase < int(rate * 0.34) else 0
            samples.append(int(amplitude * math.sin(2 * math.pi * base_hz * i / rate)))
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(samples.tobytes())

        return SpeechResult(
            audio_path=str(output),
            duration_seconds=duration,
            sample_rate=rate,
            model_provider=self.provider_id,
            model_name="deterministic-tone",
            model_version="1",
            mocked=True,
            audio_sha256=sha256_file(output),
            language=lang,
            notes=["MOCKED: tone bursts, not intelligible speech. Never use for publication."],
        )
