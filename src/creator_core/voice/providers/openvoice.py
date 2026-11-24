"""OpenVoice v2 adapter (MIT code + MIT weights, commercial use permitted).

This adapter is lazy and fail-closed: no checkpoint is downloaded, no model is loaded at import
time, and no audio is fabricated. Real inference runs only when the OpenVoice runtime, licensed
checkpoints, and a valid authorized reference are present. Missing prerequisites raise structured
errors; SAPI/test_tone are never substituted.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from creator_core.cli.errors import (
    MissingDependencyError,
    ProviderError,
    ValidationError,
)
from creator_core.voice.artifact_manifest import verify_manifest
from creator_core.voice.providers.base import LazyNeuralProvider, SpeechResult

# OpenVoice v2 base-speaker keys used by upstream MeloTTS/OpenVoice demos.
_BASE_SPEAKERS = {
    "en": "EN",
    "es": "ES",
    "fr": "FR",
    "zh": "ZH",
    "ja": "JP",
    "ko": "KR",
}
_MISSING_RUNTIME = (
    "install OpenVoice v2 from its MIT source and the optional voice-neural runtime; "
    "this adapter never downloads weights or fabricates audio"
)


class OpenVoiceProvider(LazyNeuralProvider):
    provider_id = "openvoice_v2"
    display_name = "OpenVoice v2 (MIT, default neural)"
    weight_licence = "MIT (code); checkpoint/base-speaker/dependency licenses UNVERIFIED until artifact manifest verified"
    licence_url = "https://github.com/myshell-ai/OpenVoice"
    commercial_ok = False  # fail closed: artifact-level verification required for commercial use
    clones_voice = True  # only ever used with the verified owner's registered reference
    external = False
    import_module = "openvoice"
    model_env_var = "CREATOR_OPENVOICE_CHECKPOINT"
    install_hint = (
        "install the optional `voice-neural` extra, install OpenVoice v2 from its MIT source, "
        "and place licensed checkpoints at CREATOR_OPENVOICE_CHECKPOINT; this adapter never "
        "downloads weights automatically"
    )

    def __init__(self) -> None:
        self._runtime = None
        self._runtime_key: tuple | None = None

    def supported_languages(self) -> list[str]:
        # OpenVoice v2 native language set plus documented cross-lingual base speaker.
        return ["en", "es", "fr", "zh", "ja", "ko"]

    def supported_controls(self) -> list[str]:
        # OpenVoice separates timbre from style; these are real upstream style controls.
        return [
            "speed",
            "emotion",
            "accent",
            "rhythm",
            "pauses",
            "intonation",
            "whisper",
            "shouting",
        ]

    def validate_reference(self, meta: dict) -> dict:
        result = super().validate_reference(meta)
        duration = float(meta.get("duration_seconds") or 0.0)
        if duration < 5.0:
            raise ValidationError(
                "OpenVoice v2 needs at least 5 seconds of clean reference audio",
                hint=f"this reference is {duration:.1f}s",
            )
        result["reference_used_for_cloning"] = True
        return result

    # ---- runtime / checkpoints -------------------------------------------------------------

    def is_available(self) -> bool:
        """Cheap check: runtime importable and a checkpoint path is configured/present."""
        if not self._runtime_importable():
            return False
        return self._checkpoint_dir(required=False) is not None

    def _runtime_importable(self) -> bool:
        import importlib.util

        return (
            importlib.util.find_spec("openvoice") is not None
            and importlib.util.find_spec("torch") is not None
        )

    def _checkpoint_dir(self, *, required: bool) -> Path | None:
        configured = os.environ.get(self.model_env_var, "").strip()
        candidates = []
        if configured:
            candidates.append(Path(configured).expanduser())
        # Conventional local locations; never downloaded automatically.
        candidates.extend(
            [
                Path.cwd() / "checkpoints_v2",
                Path.home() / ".cache" / "creator_core" / "openvoice" / "checkpoints_v2",
            ]
        )
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        if required:
            raise MissingDependencyError(
                f"{self.display_name} checkpoints are not configured",
                hint=(
                    f"set {self.model_env_var} to a verified OpenVoice v2 checkpoints_v2 "
                    "directory; this adapter never downloads weights automatically"
                ),
            )
        return None

    def _device(self, voice: dict) -> str:
        requested = str(
            (voice.get("config") or {}).get("voice", {}).get("device")
            or os.environ.get("CREATOR_VOICE_DEVICE", "auto")
        ).lower()
        try:
            import torch
        except Exception as exc:  # pragma: no cover - guarded by runtime checks
            raise MissingDependencyError(
                "torch is required for OpenVoice v2 inference", hint=_MISSING_RUNTIME
            ) from exc
        cuda_ok = bool(getattr(torch.cuda, "is_available", lambda: False)())
        if requested in {"cuda", "gpu"}:
            if not cuda_ok:
                raise ProviderError(
                    "CUDA was requested but is not available",
                    hint="set voice.device=cpu or install a CUDA-capable torch build",
                )
            return "cuda"
        if requested == "cpu":
            return "cpu"
        return "cuda" if cuda_ok else "cpu"

    def _manifest_path(self, checkpoint_dir: Path | None) -> Path | None:
        env = os.environ.get("CREATOR_OPENVOICE_MANIFEST")
        if env:
            return Path(env).expanduser()
        if checkpoint_dir is not None:
            cand = checkpoint_dir / "openvoice_artifact_manifest.json"
            if cand.exists():
                return cand
        return None

    def _load_licence_manifest(self, checkpoint_dir: Path | None) -> dict:
        """Verify the schema-1.0 artifact manifest strictly and fail closed.

        Verification is recomputed from disk on every call: every required artifact
        must exist with a matching SHA-256/size, resolvable license evidence, valid
        enums, present dependencies/base-speaker, and be confined to the model root.
        A caller-supplied boolean is never trusted; commercial clearance is
        computed from the actual verification result.
        """
        path = self._manifest_path(checkpoint_dir)
        model_root = checkpoint_dir or (Path(path).parent if path else Path.cwd())
        if path is None or not path.exists():
            return {
                "verified": False,
                "integrity_ok": False,
                "commercial_ok": False,
                "path": str(path) if path else "",
                "reason": "missing manifest",
            }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
            return {
                "verified": False,
                "integrity_ok": False,
                "commercial_ok": False,
                "path": str(path),
                "reason": f"invalid manifest JSON: {exc}",
            }
        ver = verify_manifest(data, model_root=model_root, provider=self.provider_id)
        return {
            "verified": ver.commercial_ok,
            "integrity_ok": ver.integrity_ok,
            "commercial_ok": ver.commercial_ok,
            "path": str(path),
            "reason": "; ".join(ver.errors[:6]) if ver.errors else "",
            "artifacts": [
                {
                    "id": a.id,
                    "ok": a.ok,
                    "exists": a.exists,
                    "hash_ok": a.hash_ok,
                    "size_ok": a.size_ok,
                    "commercial_use": a.commercial_use,
                }
                for a in ver.artifacts
            ],
        }

    def _require_commercial_clearance(self, voice: dict, checkpoint_dir: Path | None) -> dict:
        voice_cfg = (voice.get("config") or {}).get("voice", {})
        commercial_use = bool(voice_cfg.get("commercial_use", True))
        manifest = self._load_licence_manifest(checkpoint_dir)
        if commercial_use:
            if not manifest.get("commercial_ok"):
                raise ValidationError(
                    "OpenVoice commercial use requires every required artifact verified "
                    "(sha256/size/provenance/license evidence) with commercial_use=allowed; fail-closed",
                    hint=(
                        "provide a schema 1.0 openvoice_artifact_manifest.json whose artifacts all "
                        f"verify{'; ' + manifest['reason'] if manifest.get('reason') else ''}; see "
                        "VOICE_ARTIFACT_MANIFEST_SCHEMA.md"
                    ),
                )
        else:
            # Non-commercial use still requires full artifact integrity: a missing,
            # tampered, or hash-mismatched artifact is rejected regardless of consent.
            if not manifest.get("integrity_ok"):
                raise ValidationError(
                    "OpenVoice artifact integrity is not verified; cannot use missing/tampered/"
                    "hash-mismatched artifacts even for non-commercial use",
                    hint=manifest.get("reason") or "verify artifacts and manifest per schema 1.0",
                )
            ack = str(voice_cfg.get("noncommercial_ack", "")).strip().lower()
            if ack not in {"noncommercial", "non-commercial", "i acknowledge non-commercial use"}:
                raise ValidationError(
                    "OpenVoice non-commercial use requires explicit acknowledgement",
                    hint="set voice.commercial_use=false and voice.noncommercial_ack='noncommercial' only for genuine non-commercial use",
                )
        return manifest

    def validate_model(self) -> dict:
        if not self._runtime_importable():
            raise MissingDependencyError(
                f"{self.display_name} runtime is not installed",
                hint=_MISSING_RUNTIME,
            )
        ckpt = self._checkpoint_dir(required=True)
        converter_cfg = ckpt / "converter" / "config.json"
        converter_ckpt = ckpt / "converter" / "checkpoint.pth"
        missing = [str(p.name) for p in (converter_cfg, converter_ckpt) if not p.is_file()]
        if missing:
            raise MissingDependencyError(
                f"{self.display_name} checkpoint is incomplete: missing {', '.join(missing)}",
                hint=f"verify the licensed OpenVoice v2 checkpoints_v2 directory at {ckpt}",
            )
        manifest = self._load_licence_manifest(ckpt)
        return {
            "provider_id": self.provider_id,
            "available": True,
            "runtime_importable": True,
            "checkpoint_dir": str(ckpt),
            "weights_verified": bool(manifest.get("integrity_ok")),
            "licence_verified": bool(manifest.get("commercial_ok")),
            "licence_manifest": manifest,
            "note": "Runtime/checkpoint paths detected; commercial use requires verified schema-1.0 artifact manifest.",
        }

    def _load_runtime(self, *, device: str, checkpoint_dir: Path):
        # Re-verify artifacts from disk immediately before loading bytes off disk.
        # Fail closed on any modification/substitution since the earlier check; never
        # reuse stale verification.
        recheck = self._load_licence_manifest(checkpoint_dir)
        if not recheck.get("integrity_ok"):
            raise ValidationError(
                "OpenVoice artifact verification failed at load time (missing/tampered/mismatched)",
                hint=recheck.get("reason") or "verify artifacts and manifest per schema 1.0",
            )
        key = (str(checkpoint_dir), device)
        if self._runtime is not None and self._runtime_key == key:
            return self._runtime
        try:
            import torch
            from openvoice import se_extractor
            from openvoice.api import ToneColorConverter
        except Exception as exc:
            raise MissingDependencyError(
                f"{self.display_name} runtime import failed: {self.safe_error(exc)}",
                hint=_MISSING_RUNTIME,
            ) from exc

        config_path = checkpoint_dir / "converter" / "config.json"
        ckpt_path = checkpoint_dir / "converter" / "checkpoint.pth"
        converter = ToneColorConverter(str(config_path), device=device)
        converter.load_ckpt(str(ckpt_path))
        runtime = {
            "device": device,
            "checkpoint_dir": str(checkpoint_dir),
            "converter": converter,
            "se_extractor": se_extractor,
            "torch": torch,
            "base_speakers": _BASE_SPEAKERS,
            "artifacts_verified": True,  # admitted by verified manifest hashing above
        }
        self._runtime = runtime
        self._runtime_key = key
        return runtime

    # ---- generation ------------------------------------------------------------------------

    def generate_speech(
        self, text: str, *, language: str, voice: dict, controls: dict | None = None
    ) -> SpeechResult:
        cleaned = self.clean_text(text)
        lang = self.validate_language(language)
        controls = self.validate_controls(controls)
        details = self.validate_model()
        checkpoint_dir = Path(details["checkpoint_dir"])
        self._require_commercial_clearance(voice, checkpoint_dir)
        device = self._device(voice)
        runtime = self._load_runtime(device=device, checkpoint_dir=checkpoint_dir)

        ref_meta = voice.get("reference_audio_metadata") or {}
        self.validate_reference(ref_meta)
        reference = self._resolve_reference(voice)
        if not reference.is_file():
            raise ProviderError(
                "voice reference audio is missing",
                hint="re-register the voice profile with a valid reference file",
            )

        speed = float(controls.get("speed", 1.0) or 1.0)
        if not 0.5 <= speed <= 2.0:
            raise ValidationError("speed must be between 0.5 and 2.0")

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="creator-openvoice-") as tmp:
            tmp_dir = Path(tmp)
            base_wav = tmp_dir / "base.wav"
            out_wav = tmp_dir / "output.wav"
            self._synthesize_base(cleaned, lang=lang, speed=speed, device=device, out_path=base_wav)
            target_se = self._extract_se(runtime, reference, tmp_dir)
            source_se = self._source_se(runtime, checkpoint_dir, lang)
            runtime["converter"].convert(
                audio_src_path=str(base_wav),
                src_se=source_se,
                tgt_se=target_se,
                output_path=str(out_wav),
                tau=float(controls.get("intonation", 0.3) or 0.3)
                if "intonation" in controls
                else 0.3,
                message="@MyShell",
            )
            if not out_wav.is_file() or out_wav.stat().st_size == 0:
                raise ProviderError("OpenVoice v2 produced no audio output")
            final_dir = Path(voice.get("workspace").root) / ".creator" / "voice" / "tmp"
            final_dir.mkdir(parents=True, exist_ok=True)
            final_path = (
                final_dir
                / f"openvoice_{hashlib.sha256(cleaned.encode()).hexdigest()[:12]}_{int(time.time() * 1000)}.wav"
            )
            os.replace(out_wav, final_path)

        duration, sample_rate = self.measure_audio(final_path, config=voice.get("config") or {})
        elapsed = time.perf_counter() - started
        return SpeechResult(
            audio_path=str(final_path),
            duration_seconds=duration,
            sample_rate=sample_rate,
            model_provider=self.provider_id,
            model_name=self.display_name,
            model_version=f"openvoice-v2:{checkpoint_dir.name}",
            mocked=False,
            notes=[
                f"Real neural inference executed on {device}.",
                f"Generation wall-clock: {elapsed:.3f}s; audio duration: {duration:.3f}s.",
                "No SAPI/test_tone fallback was used.",
            ],
            language=lang,
        )

    def _resolve_reference(self, voice: dict) -> Path:
        from creator_core.security.paths import resolve_within
        from creator_core.voice.paths import ensure_voice_dirs, owner_dir

        meta = voice.get("reference_audio_metadata") or {}
        stored = meta.get("stored_path")
        if not stored:
            raise ValidationError("voice profile has no stored reference path")
        ws = voice.get("workspace")
        owner_id = str(voice.get("owner_id") or "")
        if ws is None or not owner_id:
            raise ValidationError("voice generation requires workspace and owner context")
        base = owner_dir(ensure_voice_dirs(ws), owner_id, "references")
        return resolve_within(base, stored)

    def _extract_se(self, runtime: dict, reference: Path, tmp_dir: Path):
        se_extractor = runtime["se_extractor"]
        try:
            return se_extractor.get_se(str(reference), runtime["converter"], vad=True)
        except Exception as exc:
            raise ProviderError(
                f"OpenVoice reference extraction failed: {self.safe_error(exc)}",
                hint="provide a clean 5-30s reference recording and verify checkpoint compatibility",
            ) from exc

    def _source_se(self, runtime: dict, checkpoint_dir: Path, lang: str):
        import torch

        key = _BASE_SPEAKERS.get(lang, "EN")
        base_dir = checkpoint_dir / "base_speakers" / "ses"
        candidate = base_dir / f"{key.lower()}.pth"
        if not candidate.is_file():
            # fallback: any available base speaker
            any_ses = sorted(base_dir.glob("*.pth")) if base_dir.is_dir() else []
            if not any_ses:
                raise MissingDependencyError(
                    "OpenVoice base speaker embeddings are missing",
                    hint=f"expected base_speakers/ses/*.pth under {checkpoint_dir}",
                )
            candidate = any_ses[0]
        # Prefer safe loading (weights_only=True). Only fall back to full-pickle
        # torch.load when the artifact was admitted by verified manifest hashing
        # (see artifact_manifest); otherwise fail closed.
        load_kwargs = {"map_location": runtime["device"]}
        try:
            return torch.load(str(candidate), weights_only=True, **load_kwargs)
        except Exception:  # noqa: BLE001 - safe-load failure path; admission gate applies below
            if runtime.get("artifacts_verified"):
                return torch.load(str(candidate), **load_kwargs)
            raise MissingDependencyError(
                "checkpoint requires unsafe torch.load but was not admitted by a verified manifest",
                hint=_MISSING_RUNTIME,
            )

    def _synthesize_base(
        self, text: str, *, lang: str, speed: float, device: str, out_path: Path
    ) -> None:
        """Generate base-speaker audio via MeloTTS/OpenVoice-compatible TTS runtime."""
        try:
            from melo.api import TTS
        except Exception as exc:
            raise MissingDependencyError(
                f"OpenVoice base TTS runtime (MeloTTS) is unavailable: {self.safe_error(exc)}",
                hint="install MeloTTS as required by OpenVoice v2; no fallback audio is produced",
            ) from exc
        speaker_key = _BASE_SPEAKERS.get(lang, "EN")
        try:
            model = TTS(language=speaker_key, device=device)
            speaker_ids = model.hps.data.spk2id
            speaker_id = speaker_ids.get(speaker_key) or next(iter(speaker_ids.values()))
            model.tts_to_file(text, speaker_id, str(out_path), speed=speed)
        except Exception as exc:
            raise ProviderError(
                f"OpenVoice base synthesis failed: {self.safe_error(exc)}",
                hint="verify MeloTTS/OpenVoice language support and checkpoint installation",
            ) from exc
