"""Real local Windows SAPI speech provider (Phase 11D).

This adapter drives the installed SAPI.SpVoice COM engine directly through pywin32. It synthesises
intelligible speech with the machine's *system* voices (e.g. Microsoft David/Zira), measured with
real FFprobe after generation. It explicitly does **not** clone the creator's registered voice
(`clones_voice = False`) and is a pipeline/fallback provider, not a neural cloning model.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from creator_core.cli.errors import (
    MissingDependencyError,
    ProviderError,
    ProviderUnavailableError,
    ValidationError,
)
from creator_core.media.ingest import sha256_file
from creator_core.voice.providers.base import SpeechResult, VoiceProvider


class SystemSapiProvider(VoiceProvider):
    """Local, non-cloning speech provider backed by the Windows SAPI 5 COM engine."""

    provider_id = "system_sapi"
    display_name = "Windows SAPI (local system voices)"
    weight_licence = "OS-provided system voices (review platform licence for redistribution)"
    commercial_ok = True
    clones_voice = False
    external = False
    install_hint = (
        "system_sapi requires Windows with SAPI voices and pywin32 "
        "(pip install pywin32); no model download is used"
    )

    def supported_languages(self) -> list[str]:
        # David/Zira are English voices; other installed SAPI voices are reported dynamically.
        return ["en"]

    def supported_controls(self) -> list[str]:
        # Real SAPI properties: Rate (-10..10), Volume (0..100), plus voice selection.
        return ["rate", "volume", "voice"]

    # ---- availability / model validation --------------------------------------------------

    def is_available(self) -> bool:
        if os.name != "nt":
            return False
        try:
            import importlib.util

            if importlib.util.find_spec("win32com.client") is None:
                return False
            return bool(self._installed_voices())
        except Exception:  # pragma: no cover - COM failures surface in validate_model()  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
            return False

    @staticmethod
    def _com_dispatch(prog_id: str):
        import win32com.client  # lazy: only imported on Windows when actually used

        return win32com.client.Dispatch(prog_id)

    def _installed_voices(self) -> list[dict]:
        if os.name != "nt":
            return []
        try:
            speaker = self._com_dispatch("SAPI.SpVoice")
            tokens = speaker.GetVoices()
            voices: list[dict] = []
            for index in range(tokens.Count):
                token = tokens.Item(index)
                description = str(token.GetDescription())
                voices.append(
                    {
                        "name": description.split(" - ")[0].strip(),
                        "description": description,
                        "id": str(token.Id),
                    }
                )
            return voices
        except Exception as exc:  # pragma: no cover - depends on host COM state
            raise ProviderUnavailableError(
                f"could not enumerate SAPI voices: {self.safe_error(exc)}",
                hint=self.install_hint,
            ) from exc

    def validate_model(self) -> dict:
        if os.name != "nt":
            raise MissingDependencyError(
                "system_sapi is only available on Windows",
                hint="use another provider, or run this pipeline on Windows for local speech",
            )
        import importlib.util

        if importlib.util.find_spec("win32com.client") is None:
            raise MissingDependencyError(
                "pywin32 is not installed",
                hint="pip install pywin32 to use the local Windows SAPI provider",
            )
        voices = self._installed_voices()
        if not voices:
            raise ProviderUnavailableError(
                "no SAPI voices are installed on this system",
                hint="install a SAPI 5 voice (e.g. Microsoft David/Zira) or choose another provider",
            )
        return {
            "provider_id": self.provider_id,
            "available": True,
            "voices": voices,
            "note": "System voices are used; the creator's registered voice is NOT cloned.",
        }

    def validate_reference(self, meta: dict) -> dict:
        # SAPI does not consume the reference audio; it still requires a valid profile asset so
        # consent/provenance are meaningful, but no embedding is created from it.
        result = super().validate_reference(meta)
        result["reference_used_for_cloning"] = False
        return result

    # ---- generation -----------------------------------------------------------------------

    def generate_speech(
        self, text: str, *, language: str, voice: dict, controls: dict | None = None
    ) -> SpeechResult:
        model = self.validate_model()
        clean = self.clean_text(text)
        lang = self.validate_language(language)
        checked = self.validate_controls(controls)

        configured = voice.get("model_name") or voice.get("voice")
        voice_name = str(configured or checked.get("voice") or model["voices"][0]["name"])
        available = model["voices"]
        match = next((v for v in available if v["name"].lower() == voice_name.lower()), None)
        if match is None:
            match = next(
                (v for v in available if v["name"].lower().startswith(voice_name.lower())), None
            )
        if match is None:
            raise ProviderUnavailableError(
                f"SAPI voice {voice_name!r} is not installed",
                hint="voices: " + ", ".join(v["name"] for v in available),
            )

        rate = self._bounded_int(checked.get("rate", 0), -10, 10, "rate")
        volume = self._bounded_int(checked.get("volume", 100), 0, 100, "volume")

        ws = voice.get("workspace")
        if ws is None:
            raise ProviderError(
                "system_sapi generation requires a workspace for its output",
                hint="call through creator_core.voice.synthesis, which supplies one",
            )
        out_dir = Path(ws.tmp) / "voice" / "sapi"
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / f"sapi_{uuid.uuid4().hex}.wav"

        try:
            speaker = self._com_dispatch("SAPI.SpVoice")
            tokens = speaker.GetVoices()
            token = next(
                tokens.Item(i)
                for i in range(tokens.Count)
                if str(tokens.Item(i).GetDescription()).split(" - ")[0].strip().lower()
                == match["name"].lower()
            )
            speaker.Voice = token
            speaker.Rate = rate
            speaker.Volume = volume
            stream = self._com_dispatch("SAPI.SpFileStream")
            stream.Open(str(output), 3)  # SSFMCreateForWrite
            speaker.AudioOutputStream = stream
            try:
                speaker.Speak(clean)
            finally:
                stream.Close()
        except Exception as exc:
            output.unlink(missing_ok=True)
            raise ProviderError(
                f"SAPI synthesis failed: {self.safe_error(exc)}",
                hint="check that the voice is installed and the COM engine is reachable",
            ) from exc

        duration, sample_rate = self.measure_audio(output, config=voice.get("config") or {})
        return SpeechResult(
            audio_path=str(output),
            duration_seconds=duration,
            sample_rate=sample_rate,
            model_provider=self.provider_id,
            model_name=match["name"],
            model_version="system",
            mocked=False,
            audio_sha256=sha256_file(output),
            language=lang,
            notes=[f"Real Windows SAPI speech using {match['name']}; no neural cloning."],
        )

    @staticmethod
    def _bounded_int(value, low: int, high: int, name: str) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{name} must be an integer between {low} and {high}") from exc
        if not low <= number <= high:
            raise ValidationError(f"{name} must be between {low} and {high}")
        return number
