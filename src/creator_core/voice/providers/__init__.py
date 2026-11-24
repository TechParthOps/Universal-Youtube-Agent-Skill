"""VoiceDNA provider adapters (Phase 11D).

Importing this package is cheap: neural runtimes are never imported until an adapter is explicitly
instantiated, and generation always fails closed without licensed weights.
"""

from creator_core.voice.providers.base import (
    MAX_TEXT_CHARS,
    LazyNeuralProvider,
    SpeechResult,
    VoiceProvider,
)
from creator_core.voice.providers.registry import (
    get_provider,
    list_languages,
    list_providers,
    provider_ids,
    select,
)

__all__ = [
    "MAX_TEXT_CHARS",
    "LazyNeuralProvider",
    "SpeechResult",
    "VoiceProvider",
    "get_provider",
    "list_languages",
    "list_providers",
    "provider_ids",
    "select",
]
