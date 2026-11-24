"""Chatterbox adapter (MIT code and weights, commercial OK; retired research archive).

Secondary/opt-in neural provider. The upstream project was retired in 2026 and is no longer
maintained; this adapter is lazy, fail-closed and never claims real inference. Self-hosted output
carries the upstream PerTh watermark when the creator runs the real model.
"""

from __future__ import annotations

from creator_core.voice.providers.base import LazyNeuralProvider


class ChatterboxProvider(LazyNeuralProvider):
    provider_id = "chatterbox"
    display_name = "Chatterbox Multilingual (MIT, archived research)"
    weight_licence = "MIT (archived research release; PerTh watermark on self-hosted output)"
    licence_url = "https://github.com/resemble-ai/chatterbox"
    commercial_ok = True
    clones_voice = True
    external = False
    import_module = "chatterbox"
    model_env_var = "CREATOR_CHATTERBOX_CHECKPOINT"
    install_hint = (
        "install the optional `voice-neural` extra and licensed Chatterbox checkpoints at "
        "CREATOR_CHATTERBOX_CHECKPOINT; the upstream project is retired, so review before use"
    )

    def supported_languages(self) -> list[str]:
        # Subset of the documented 23+ languages; unsupported codes are rejected, not guessed.
        return [
            "en",
            "es",
            "fr",
            "de",
            "it",
            "pt",
            "zh",
            "ja",
            "ko",
            "ar",
            "hi",
            "nl",
            "pl",
            "ru",
            "tr",
            "uk",
            "vi",
        ]

    def supported_controls(self) -> list[str]:
        return ["exaggeration", "cfg_weight", "paralinguistic_tags"]

    def validate_model(self) -> dict:
        result = super().validate_model()
        result["note"] += (
            " Chatterbox is retired research with no upstream support; "
            "verify output watermark and quality before publication."
        )
        return result
