"""WhisperX-based forced alignment backend (optional, fail-closed).

This backend only runs when whisperx and its alignment model dependencies are installed. It
produces measured word timestamps; if unavailable or alignment fails, callers must fall back to
explicitly estimated alignment.
"""

from __future__ import annotations

import math
from pathlib import Path

from .base import AlignerResult, WordAligner, WordAlignment
from .coverage import validate_alignment


class WhisperXAligner(WordAligner):
    id = "whisperx"
    display_name = "WhisperX forced alignment"

    def __init__(self) -> None:
        self._model = None
        self._model_lang = None
        self._model_device = None

    def is_available(self) -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec("whisperx") is not None
        except Exception:  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
            return False

    def align(
        self, audio_path: Path, text: str, *, language: str = "en", device: str | None = None
    ) -> AlignerResult:
        try:
            import torch
            import whisperx  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"whisperx unavailable: {exc}") from exc

        requested = (device or "auto").lower()
        cuda_ok = bool(getattr(torch.cuda, "is_available", lambda: False)())
        if requested in {"cuda", "gpu"}:
            if not cuda_ok:
                raise RuntimeError("CUDA requested for alignment but unavailable")
            resolved_device = "cuda"
        elif requested == "cpu":
            resolved_device = "cpu"
        else:
            resolved_device = "cuda" if cuda_ok else "cpu"

        audio = whisperx.load_audio(str(audio_path))
        duration = float(len(audio) / 16000.0)
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError("invalid audio duration for alignment")
        segments = [{"text": text, "start": 0.0, "end": duration}]
        model_a, metadata = whisperx.load_align_model(
            language_code=language, device=resolved_device
        )
        aligned = whisperx.align(
            segments, model_a, metadata, audio, resolved_device, return_char_alignments=False
        )

        raw_words = []
        for seg in aligned.get("segments", []):
            for w in seg.get("words", []):
                word = str(w.get("word", "")).strip()
                if not word:
                    continue
                raw_words.append(w)
        if not raw_words:
            raise RuntimeError("whisperx produced no word alignments")

        words: list[WordAlignment] = []
        invalid = 0
        prev_end = -1.0
        tol = 0.05
        for w in raw_words:
            word = str(w.get("word", "")).strip()
            start = w.get("start")
            end = w.get("end")
            conf = w.get("score")
            confidence = (
                float(conf)
                if conf is not None
                and isinstance(conf, (int, float))
                and math.isfinite(float(conf))
                else None
            )
            if start is None or end is None:
                invalid += 1
                words.append(
                    WordAlignment(
                        word=word,
                        start=0.0,
                        end=0.0,
                        confidence=confidence,
                        status="missing_timestamp",
                        method=self.id,
                    )
                )
                continue
            try:
                s = float(start)
                e = float(end)
            except Exception:  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
                invalid += 1
                words.append(
                    WordAlignment(
                        word=word,
                        start=0.0,
                        end=0.0,
                        confidence=confidence,
                        status="invalid",
                        method=self.id,
                    )
                )
                continue
            if (
                not (math.isfinite(s) and math.isfinite(e))
                or s < -tol
                or e < -tol
                or e <= s
                or s > duration + tol
                or e > duration + tol
                or s < prev_end - tol
            ):
                invalid += 1
                words.append(
                    WordAlignment(
                        word=word,
                        start=max(0.0, s if math.isfinite(s) else 0.0),
                        end=max(0.0, e if math.isfinite(e) else 0.0),
                        confidence=confidence,
                        status="invalid",
                        method=self.id,
                    )
                )
                continue
            s = max(0.0, min(duration, s))
            e = max(0.0, min(duration, e))
            if e <= s:
                invalid += 1
                words.append(
                    WordAlignment(
                        word=word,
                        start=s,
                        end=e,
                        confidence=confidence,
                        status="invalid",
                        method=self.id,
                    )
                )
                continue
            words.append(
                WordAlignment(
                    word=word,
                    start=s,
                    end=e,
                    confidence=confidence,
                    status="measured",
                    method=self.id,
                )
            )
            prev_end = e

        # Transcript-coverage validation: prove the aligner covered the WHOLE input
        # transcript, not just that the words it returned have valid timestamps.
        cov = validate_alignment(text, words, duration=duration, tolerance=tol)
        measured_count = sum(1 for w in words if w.status == "measured")
        notes = [
            "Measured word timestamps produced by WhisperX alignment model.",
            "; ".join(cov.notes),
            f"returned_words={len(words)}; measured_words={measured_count}; device={resolved_device}",
        ]
        if cov.fully_measured:
            notes.append("Full transcript coverage with valid ordered timestamps.")
        else:
            notes.append(
                "Alignment did not fully cover the transcript with valid timestamps; "
                "classified PARTIAL (not measured). Missing words are retained and no "
                "timestamps were fabricated for them."
            )
        return AlignerResult(
            words=words,
            method=self.id,
            model="whisperx-align",
            checkpoint=str(getattr(metadata, "get", lambda *_: "")("language") or language),
            measured=cov.fully_measured,
            notes=notes,
            coverage={
                "expected_words": cov.expected_count,
                "aligned_words": cov.aligned_count,
                "matched": cov.matched,
                "coverage": cov.coverage,
                "missing": cov.missing,
                "duplicates": cov.duplicates,
                "unexpected": cov.unexpected,
                "timestamp_invalid": cov.timestamp_invalid,
                "unordered": cov.unordered,
                "overlaps": cov.overlaps,
                "out_of_duration": cov.out_of_duration,
                "fully_measured": cov.fully_measured,
                "partial": cov.partial,
                "annotated": cov.annotated,
            },
        )
