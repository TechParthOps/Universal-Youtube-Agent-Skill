"""Provider registry, capability discovery and fail-closed selection (Phase 11D).

The registry is the single enforcement point for licence gating, tests-only isolation and external
provider permission. Importing this module never imports a neural runtime: providers are imported
lazily by factory only when explicitly requested.
"""

from __future__ import annotations

from collections.abc import Callable

from creator_core.cli.errors import AuthRequiredError, ValidationError
from creator_core.voice.providers.base import VoiceProvider

# provider_id → lazy factory. Importing the factories does not import the adapters themselves.
_FACTORIES: dict[str, Callable[[], VoiceProvider]] = {
    "system_sapi": lambda: __import__(
        "creator_core.voice.providers.system_sapi", fromlist=["SystemSapiProvider"]
    ).SystemSapiProvider(),
    "test_tone": lambda: __import__(
        "creator_core.voice.providers.test_tone", fromlist=["TestToneProvider"]
    ).TestToneProvider(),
    "openvoice_v2": lambda: __import__(
        "creator_core.voice.providers.openvoice", fromlist=["OpenVoiceProvider"]
    ).OpenVoiceProvider(),
    "chatterbox": lambda: __import__(
        "creator_core.voice.providers.chatterbox", fromlist=["ChatterboxProvider"]
    ).ChatterboxProvider(),
    "f5_tts": lambda: __import__(
        "creator_core.voice.providers.gated", fromlist=["F5TTSProvider"]
    ).F5TTSProvider(),
    "xtts_v2": lambda: __import__(
        "creator_core.voice.providers.gated", fromlist=["XTTSv2Provider"]
    ).XTTSv2Provider(),
    "fish_speech": lambda: __import__(
        "creator_core.voice.providers.gated", fromlist=["FishSpeechProvider"]
    ).FishSpeechProvider(),
}

# Static metadata for CLI display without instantiating adapters. Kept in sync with each adapter's
# class attributes; the adapters remain the authoritative source when instantiated.
STATIC_INFO: dict[str, dict] = {
    "openvoice_v2": {
        "display_name": "OpenVoice v2",
        "role": "default neural",
        "weight_licence": "MIT code; checkpoints/base speakers UNVERIFIED",
        "commercial_ok": False,
        "clones_voice": True,
    },
    "chatterbox": {
        "display_name": "Chatterbox Multilingual",
        "role": "secondary (retired archive)",
        "weight_licence": "MIT",
        "commercial_ok": True,
        "clones_voice": True,
    },
    "system_sapi": {
        "display_name": "Windows SAPI",
        "role": "real local fallback",
        "weight_licence": "OS voices",
        "commercial_ok": True,
        "clones_voice": False,
    },
    "test_tone": {
        "display_name": "Deterministic test tone",
        "role": "tests only",
        "weight_licence": "n/a",
        "commercial_ok": True,
        "clones_voice": False,
    },
    "f5_tts": {
        "display_name": "F5-TTS",
        "role": "licence-gated",
        "weight_licence": "CC-BY-NC-4.0",
        "commercial_ok": False,
        "clones_voice": True,
    },
    "xtts_v2": {
        "display_name": "XTTS-v2",
        "role": "licence-gated",
        "weight_licence": "CPML 1.0.0",
        "commercial_ok": False,
        "clones_voice": True,
    },
    "fish_speech": {
        "display_name": "Fish Speech",
        "role": "licence-gated/conditional",
        "weight_licence": "Fish Audio Community License v1.0",
        "commercial_ok": False,
        "clones_voice": True,
    },
}


def provider_ids() -> list[str]:
    return sorted(_FACTORIES)


def get_provider(provider_id: str) -> VoiceProvider:
    factory = _FACTORIES.get(provider_id)
    if factory is None:
        raise ValidationError(
            f"unknown voice provider: {provider_id}",
            hint="available: " + ", ".join(provider_ids()),
        )
    return factory()


def list_providers() -> list[dict]:
    """Describe all providers. Availability checks are cheap and never load model weights."""
    out: list[dict] = []
    for provider_id in provider_ids():
        provider = get_provider(provider_id)
        info = provider.info()
        static = STATIC_INFO.get(provider_id, {})
        info["role"] = static.get("role", "")
        out.append(info)
    return out


def list_languages(provider_id: str | None = None) -> dict:
    """Union (or provider-specific set) of advertised narration languages."""
    ids = [provider_id] if provider_id else provider_ids()
    per_provider: dict[str, list[str]] = {}
    for pid in ids:
        per_provider[pid] = list(get_provider(pid).supported_languages())
    union = sorted({lang for langs in per_provider.values() for lang in langs})
    return {
        "languages": union,
        "by_provider": per_provider,
        "note": "Language support is advertised by adapters; neural inference is not run here.",
    }


def select(
    profile: dict,
    *,
    purpose: str = "narration",
    allow_external: bool = False,
    allow_noncommercial: bool = False,
    allow_test_provider: bool = False,
    acknowledgement: str | None = None,
    provider_id: str | None = None,
    language: str | None = None,
    controls: dict | None = None,
) -> VoiceProvider:
    """Return a usable provider or fail closed.

    Enforcement order matters: identity → tests-only → licence → external → availability → model →
    language/controls. No provider method that touches audio is called before consent has been
    checked by the synthesis layer.
    """
    chosen_id = provider_id or (profile.get("model_provider") if profile else None)
    if not chosen_id:
        raise ValidationError("voice profile has no model provider")
    provider = get_provider(str(chosen_id))

    if provider.tests_only and not allow_test_provider:
        raise ValidationError(
            f"provider {chosen_id} is for automated tests only",
            hint="choose a production provider for real narration",
        )

    licence = getattr(provider, "validate_licence", None)
    if callable(licence):
        licence(allow_noncommercial=allow_noncommercial, acknowledgement=acknowledgement)
    elif not provider.commercial_ok and not allow_noncommercial:
        raise ValidationError(
            f"provider {chosen_id} does not permit commercial use",
            hint="pass allow_noncommercial=True only for genuine personal/non-commercial use",
        )

    if provider.external and not allow_external:
        raise AuthRequiredError(
            f"provider {chosen_id} sends reference audio externally",
            hint="pass allow_external=True to explicitly authorise external processing",
        )

    # Validate caller input before dependencies: an unsupported language/control is always a bad
    # request (exit 2), independent of whether optional weights happen to be installed.
    if language:
        provider.validate_language(language)
    if controls is not None:
        provider.validate_controls(controls)

    provider.validate_model()
    return provider
