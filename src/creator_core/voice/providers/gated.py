"""Licence-gated neural adapters: F5-TTS, XTTS-v2 and Fish Speech (Phase 11D).

These models are *not* used by default. Published checkpoints are non-commercial (F5-TTS,
XTTS-v2) or conditional (Fish Speech), so the adapters refuse commercial generation unless the
creator supplies a compliant weight licence and explicitly opts in. Nothing is downloaded and no
inference is performed here.
"""

from __future__ import annotations

from creator_core.cli.errors import ValidationError
from creator_core.voice.providers.base import LazyNeuralProvider, SpeechResult


class GatedNeuralProvider(LazyNeuralProvider):
    """Base that requires an explicit, recorded licence acknowledgement for non-commercial use."""

    commercial_ok = False
    clones_voice = True
    external = False
    requires_attestation = True
    licence_requirement = "review the checkpoint licence before use"

    def validate_licence(
        self, *, allow_noncommercial: bool = False, acknowledgement: str | None = None
    ) -> dict:
        if not allow_noncommercial:
            raise ValidationError(
                f"{self.display_name} weights do not permit commercial use by default",
                hint=(
                    f"{self.licence_requirement}. Pass allow_noncommercial=True only for "
                    "genuine non-commercial/personal use."
                ),
            )
        if self.requires_attestation and not (acknowledgement or "").strip():
            raise ValidationError(
                f"an explicit licence acknowledgement is required for {self.display_name}",
                hint="record the exact checkpoint licence you downloaded and accept responsibility",
            )
        return {
            "ok": True,
            "provider_id": self.provider_id,
            "acknowledgement": (acknowledgement or "").strip()[:500],
            "commercial_use": False,
        }

    def generate_speech(
        self, text: str, *, language: str, voice: dict, controls: dict | None = None
    ) -> SpeechResult:
        # Gated adapters never generate here — registry enforces the licence first, and even then
        # real inference requires a licensed runtime that is not bundled.
        self.clean_text(text)
        raise ValidationError(
            f"{self.display_name} cannot generate with bundled code",
            hint="supply licensed weights and a compatible inference extra; no audio is fabricated",
        )


class F5TTSProvider(GatedNeuralProvider):
    provider_id = "f5_tts"
    display_name = "F5-TTS (CC-BY-NC weights — gated)"
    weight_licence = "CC-BY-NC-4.0 (Emilia-trained pretrained weights; non-commercial)"
    licence_url = "https://github.com/SWivid/F5-TTS"
    import_module = "f5_tts"
    install_hint = "F5-TTS pretrained weights are CC-BY-NC; use only for non-commercial work"
    licence_requirement = "F5-TTS pretrained weights are CC-BY-NC-4.0 even after fine-tuning"

    def supported_languages(self) -> list[str]:
        return ["zh", "en"]

    def supported_controls(self) -> list[str]:
        return []


class XTTSv2Provider(GatedNeuralProvider):
    provider_id = "xtts_v2"
    display_name = "XTTS-v2 (Coqui Public Model License — gated, unlicensable commercially)"
    weight_licence = "Coqui Public Model License 1.0.0 (non-commercial; outputs restricted)"
    licence_url = "https://huggingface.co/coqui/XTTS-v2"
    import_module = "TTS"
    install_hint = (
        "XTTS-v2 uses CPML weights; no commercial licence is obtainable after Coqui's closure"
    )
    licence_requirement = (
        "CPML forbids commercial use and restricts outputs; do not use commercially"
    )

    def supported_languages(self) -> list[str]:
        return ["en", "es", "fr", "de", "it", "pt", "zh", "ja", "ko", "ar", "hi"]

    def supported_controls(self) -> list[str]:
        return []


class FishSpeechProvider(GatedNeuralProvider):
    provider_id = "fish_speech"
    display_name = "Fish Speech (conditional community licence — gated)"
    weight_licence = "Fish Audio Community License v1.0 (conditional, <USD 500k revenue)"
    licence_url = "https://huggingface.co/fishaudio/drama-3"
    import_module = "fish_speech"
    install_hint = (
        "Fish Speech requires revenue/commercial attestation; S2-Pro needs a separate "
        "written research licence"
    )
    licence_requirement = "Fish Audio Community License v1.0 conditional on revenue and use"

    def supported_languages(self) -> list[str]:
        return ["en", "zh", "ja", "ko", "es", "fr", "de", "pt", "ru"]

    def supported_controls(self) -> list[str]:
        return []
