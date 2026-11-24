"""Voice provider interface and result types (Phase 11D).

Every adapter is lazy and capability-tagged. Nothing imports a neural runtime at module import
time, nothing downloads weights implicitly, and unsupported controls are rejected rather than
silently ignored. `SpeechResult.mocked` is always explicit: a deterministic tone or a stub is never
presented as model inference.
"""

from __future__ import annotations

import abc
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from creator_core.cli.errors import MissingDependencyError, ProviderError, ValidationError

# Maximum narration text accepted by any provider. Long scripts are split into segments by the
# script layer; a single oversized request is rejected to bound memory and provider time.
MAX_TEXT_CHARS = 2000
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def apply_pronunciation(text: str, overrides: dict | None) -> str:
    """Rewrite creator pronunciation preferences as deterministic text substitutions.

    This is applied before synthesis and works with every provider, including non-neural ones.
    It is deliberately *not* advertised as a provider control: no bundled adapter implements an
    acoustic pronunciation feature, and claiming one would be false. Longest keys are applied
    first so ``"MC"`` does not shadow ``"MC Minecraft"``. Replacements are literal, word-bounded
    and case-insensitive; values are capped to prevent unbounded script growth.
    """
    result = str(text)
    for source, replacement in sorted((overrides or {}).items(), key=lambda kv: -len(str(kv[0]))):
        source, replacement = str(source).strip(), str(replacement).strip()[:100]
        if not source:
            continue
        result = re.sub(
            r"(?<!\w)" + re.escape(source) + r"(?!\w)",
            lambda _m, value=replacement: value,
            result,
            flags=re.IGNORECASE,
        )
    return result


@dataclass
class SpeechResult:
    """One generated audio segment with honest provenance metadata."""

    audio_path: str
    duration_seconds: float
    sample_rate: int
    model_provider: str
    model_name: str
    model_version: str
    mocked: bool = False
    word_timestamps: list[dict] = field(default_factory=list)
    sentence_timestamps: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    audio_sha256: str | None = None
    language: str = ""

    def to_dict(self) -> dict:
        return {
            "audio_path": self.audio_path,
            "duration_seconds": round(float(self.duration_seconds), 3),
            "sample_rate": int(self.sample_rate),
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "mocked": bool(self.mocked),
            "word_timestamps": self.word_timestamps,
            "sentence_timestamps": self.sentence_timestamps,
            "notes": list(self.notes),
            "audio_sha256": self.audio_sha256,
            "language": self.language,
        }


class VoiceProvider(abc.ABC):
    """Abstract voice provider. Subclasses declare capabilities as class attributes."""

    provider_id: str = "base"
    display_name: str = "Base provider"
    weight_licence: str = "unspecified"
    licence_url: str = ""
    commercial_ok: bool = False
    clones_voice: bool = False
    external: bool = False
    tests_only: bool = False
    install_hint: str = ""

    # ---- capability / availability --------------------------------------------------------

    def is_available(self) -> bool:  # pragma: no cover - abstract default
        """Cheap, side-effect-free availability check. Must never import heavy runtimes."""
        return False

    def validate_model(self) -> dict:
        """Return model details or raise MissingDependencyError / ProviderError."""
        if not self.is_available():
            raise MissingDependencyError(
                f"voice provider {self.provider_id} is not available",
                hint=self.install_hint or "install the optional dependency and model weights",
            )
        return {"provider_id": self.provider_id, "available": True}

    def validate_reference(self, meta: dict) -> dict:
        """Check stored reference metadata before a provider accepts a profile."""
        if not isinstance(meta, dict) or not meta.get("sha256"):
            raise ValidationError("voice profile has no validated reference audio")
        if meta.get("suitable") is False:
            raise ValidationError(
                "voice profile reference failed quality validation",
                hint="register a cleaner reference recording",
            )
        return {"ok": True, "sha256": meta["sha256"]}

    def register_voice(self, profile: dict, reference: Path | None = None) -> dict:
        """Return a provider-specific voice descriptor. No neural work happens by default."""
        self.validate_reference(profile.get("reference_audio_metadata") or {})
        return {
            "provider_id": self.provider_id,
            "profile_id": profile.get("voice_profile_id"),
            "model_name": self.model_name_for(profile),
            "reference_sha256": (profile.get("reference_audio_metadata") or {}).get("sha256"),
            "embedding_created": False,
            "note": "No neural speaker embedding was created by this adapter.",
        }

    # ---- languages and controls -----------------------------------------------------------

    def supported_languages(self) -> list[str]:
        return []

    def supported_controls(self) -> list[str]:
        return []

    def normalize_language(self, language: str) -> str:
        """Map a locale (en-US / en_US) onto the closest language this provider advertises."""
        value = str(language or "").strip().lower().replace("_", "-")
        supported = set(self.supported_languages())
        if value in supported:
            return value
        base = value.split("-")[0]
        if base in supported:
            return base
        for candidate in supported:  # e.g. provider advertises only "en-US"
            if candidate.split("-")[0] == base:
                return candidate
        return value

    def validate_language(self, language: str) -> str:
        normalized = self.normalize_language(language)
        supported = set(self.supported_languages())
        if normalized not in supported:
            raise ValidationError(
                f"provider {self.provider_id} does not support language {language!r}",
                hint="supported: " + (", ".join(sorted(supported)) or "(none)"),
            )
        return normalized

    def validate_controls(self, controls: dict | None) -> dict:
        """Reject unknown or unsupported controls; never silently ignore them."""
        controls = controls or {}
        if not isinstance(controls, dict):
            raise ValidationError("voice controls must be an object")
        allowed = set(self.supported_controls())
        unknown = sorted(set(controls) - allowed)
        if unknown:
            raise ValidationError(
                f"unsupported voice control(s) for {self.provider_id}: {', '.join(unknown)}",
                hint="supported: " + (", ".join(sorted(allowed)) or "(none)"),
            )
        return dict(controls)

    # ---- generation -----------------------------------------------------------------------

    @abc.abstractmethod
    def generate_speech(
        self, text: str, *, language: str, voice: dict, controls: dict | None = None
    ) -> SpeechResult:
        """Generate one WAV file. Must be overridden with real behaviour or fail closed."""

    def generate_segment(
        self, segment: dict, *, language: str | None = None, controls: dict | None = None
    ) -> SpeechResult:
        """Convenience wrapper used by the synthesis layer."""
        text = str(segment.get("text") or "")
        seg_controls = dict(segment.get("controls") or {})
        if controls:
            seg_controls.update(controls)
        # Creator pronunciation preferences are applied to the script text before synthesis.
        # They are not a provider control: no bundled adapter implements acoustic pronunciation,
        # so forwarding them would make every registered provider reject the segment.
        overrides = seg_controls.pop("pronunciation_overrides", None)
        if overrides is None:
            overrides = segment.get("pronunciation_overrides")
        text = apply_pronunciation(text, overrides if isinstance(overrides, dict) else None)
        # Neutral script/profiling defaults are metadata, not creator requests: passing them to a
        # provider that does not advertise the control would be a false "unsupported control"
        # error. A non-default value IS a request and is still rejected by validate_controls().
        defaults = {"speed": 1.0, "emotion": "neutral", "accent": "default"}
        for key in ("speed", "emotion", "accent"):
            value = segment.get(key)
            if value is None or value == defaults.get(key) or key in seg_controls:
                continue
            seg_controls[key] = value
        return self.generate_speech(
            text,
            language=language or segment.get("language") or "en",
            voice=segment.get("voice") or {},
            controls=seg_controls,
        )

    # ---- lifecycle ------------------------------------------------------------------------

    def delete_voice_data(self, profile_id: str) -> dict:
        """Delete provider-owned data for a profile. Local-only adapters hold none."""
        return {
            "ok": True,
            "provider_id": self.provider_id,
            "profile_id": profile_id,
            "deleted": [],
            "note": "This adapter stores no provider-side voice data.",
        }

    def model_name_for(self, profile: dict) -> str:
        return str(profile.get("model_name") or self.display_name)

    def info(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "weight_licence": self.weight_licence,
            "licence_url": self.licence_url,
            "commercial_ok": bool(self.commercial_ok),
            "clones_voice": bool(self.clones_voice),
            "external": bool(self.external),
            "tests_only": bool(self.tests_only),
            "available": self.is_available(),
            "supported_languages": self.supported_languages(),
            "supported_controls": self.supported_controls(),
            "install_hint": self.install_hint,
        }

    # ---- shared helpers -------------------------------------------------------------------

    @staticmethod
    def clean_text(text: str) -> str:
        cleaned = unicodedata.normalize("NFC", str(text or "")).strip()
        cleaned = _CONTROL_CHARS.sub("", cleaned).replace("\r\n", "\n").replace("\r", "\n")
        if not cleaned:
            raise ValidationError("narration text is empty")
        if len(cleaned) > MAX_TEXT_CHARS:
            raise ValidationError(
                f"narration text is {len(cleaned)} characters; the limit is {MAX_TEXT_CHARS}",
                hint="split the script into shorter segments",
            )
        return cleaned

    @staticmethod
    def measure_audio(path: Path | str, *, config: dict | None = None) -> tuple[float, int]:
        """Measure a generated WAV with real FFprobe. Never estimate when a probe is possible."""
        from creator_core.media.ingest import probe

        p = Path(path)
        if not p.is_file():
            raise ProviderError(f"provider did not produce audio at {p}")
        cfg = config or {}
        raw = probe(p, cfg)
        fmt = raw.get("format") or {}
        audio = next((s for s in raw.get("streams", []) if s.get("codec_type") == "audio"), {})
        try:
            duration = float(fmt.get("duration") or audio.get("duration") or 0.0)
            rate = int(audio.get("sample_rate") or 0)
        except (TypeError, ValueError) as exc:
            raise ProviderError(f"generated audio has invalid metadata: {exc}") from exc
        if duration <= 0 or rate <= 0:
            # Some WAV writers omit format.duration; compute from stream duration or reject.
            raise ProviderError("generated audio has no measurable duration/sample rate")
        return duration, rate

    @staticmethod
    def safe_error(exc: BaseException) -> str:
        """Redact provider errors before they reach JSON, logs or history."""
        from creator_core.security.secrets import redact_text

        return redact_text(f"{exc.__class__.__name__}: {exc}")[:400]

    def describe(self) -> dict:
        return json.loads(json.dumps(self.info(), default=str))


class LazyNeuralProvider(VoiceProvider):
    """Base for neural adapters that never import weights eagerly and fail closed.

    These adapters are intentionally inert until a real, licensed inference backend is installed
    and configured by the creator. `generate_speech()` never returns fabricated audio: it raises
    a structured missing-dependency or licence error instead.
    """

    import_module: str = ""
    commercial_ok: bool = False
    external: bool = False
    model_env_var: str = ""

    def is_available(self) -> bool:
        """Only check importability — never instantiate a model or download weights."""
        import importlib.util

        return bool(self.import_module) and importlib.util.find_spec(self.import_module) is not None

    def validate_model(self) -> dict:
        if not self.is_available():
            raise MissingDependencyError(
                f"{self.display_name} is not installed",
                hint=self.install_hint
                or f"install the optional extra providing {self.import_module}",
            )
        return {
            "provider_id": self.provider_id,
            "available": True,
            "runtime_importable": True,
            "weights_verified": False,
            "note": "Runtime is importable; model weights were not loaded or verified by this check.",
        }

    def generate_speech(
        self, text: str, *, language: str, voice: dict, controls: dict | None = None
    ) -> SpeechResult:
        self.clean_text(text)
        self.validate_model()  # raises when the real runtime is absent
        raise MissingDependencyError(
            f"real {self.display_name} inference is not configured in this environment",
            hint=(
                "install and configure licensed model weights plus the inference extra; "
                "this adapter never fabricates or downloads audio"
            ),
        )


def require_fields(data: dict, *fields: str) -> None:
    missing = [f for f in fields if not data.get(f)]
    if missing:
        raise ValidationError("missing voice generation field(s): " + ", ".join(missing))


def write_silence(path: Path, seconds: float, *, rate: int = 24000) -> Path:
    """Write a deterministic silent WAV (used only by the test provider)."""
    import array
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(array.array("h", [0] * int(rate * seconds)).tobytes())
    return path
