"""Honest narration alignment and A/V validation (Phase 11F; Stage 12D waveform anchoring).

No bundled provider emits native word timings. Segment placement is anchored to the footage
moment selected by the script and the **measured** WAV duration. FFmpeg `silencedetect` measures
speech/pause windows directly from the waveform; sentence/word boundaries are allocated by
character count *inside those measured windows* and remain explicitly `estimated`. Provider
timestamps, when present, take precedence and are labelled `verified`. Nothing here claims
speech recognition, word-level measurement, or frame-accurate synchrony.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from creator_core.cli.errors import CreatorError, ValidationError
from creator_core.media.ingest import ffmpeg_binary
from creator_core.research.provenance import ESTIMATED, VERIFIED
from creator_core.security import subprocess as ssub
from creator_core.security.paths import resolve_within
from creator_core.voice.synthesis import generation_dir
from creator_core.workspace.layout import load_config

_SCHEMA_VERSION = 1
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")
_WORD_RE = re.compile(r"\S+")
_SILENCE_EVENT = re.compile(r"silence_(start|end):\s*([0-9.]+)")
_MAX_SPEECH_WINDOWS = 200
_MIN_WINDOW_S = 0.05
_DEFAULT_SILENCE_DB = -35.0
_DEFAULT_MIN_SILENCE_S = 0.1


def _alignment_options(config: dict | None) -> dict:
    """Read bounded alignment settings from config; never trust unbounded user values."""
    raw = ((config or {}).get("voice") or {}).get("alignment") or {}

    def number(key: str, default: float, low: float, high: float) -> float:
        try:
            value = float(raw.get(key, default))
        except (TypeError, ValueError):
            return default
        return min(high, max(low, value))

    def count(key: str, default: int) -> int:
        try:
            return max(1, min(_MAX_SPEECH_WINDOWS, int(raw.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "silence_db": number("silence_db", _DEFAULT_SILENCE_DB, -80.0, 0.0),
        "min_silence_s": number("min_silence_s", _DEFAULT_MIN_SILENCE_S, 0.02, 5.0),
        "max_windows": count("max_windows", _MAX_SPEECH_WINDOWS),
    }


def measure_speech_windows(
    path: Path, config: dict | None, *, duration: float, options: dict | None = None
) -> dict:
    """Measure speech/pause windows from a WAV with real FFmpeg `silencedetect`.

    This is waveform-level evidence, not speech recognition: it finds level-based pauses and
    their inverse (speech-like windows). Failures degrade to an empty result with an error string;
    they never produce fabricated windows. All times are relative to the start of the WAV.
    """
    opts = options or _alignment_options(config)
    metadata = {
        "method": "ffmpeg silencedetect",
        "detector_version": 1,
        "noise_db": opts["silence_db"],
        "min_silence_s": opts["min_silence_s"],
        "max_windows": opts["max_windows"],
    }
    cmd = [
        ffmpeg_binary(config or {}, "ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-v",
        "info",
        "-i",
        os.fspath(path),
        "-af",
        f"silencedetect=noise={opts['silence_db']:g}dB:d={opts['min_silence_s']:g}",
        "-f",
        "null",
        os.devnull,
    ]
    try:
        result = ssub.run(
            cmd,
            timeout=float(((config or {}).get("limits") or {}).get("subprocess_timeout_s", 600)),
            max_output=4_000_000,
        )
    except (CreatorError, OSError) as exc:
        message = exc.message if isinstance(exc, CreatorError) else str(exc)
        return {**metadata, "ok": False, "windows": [], "error": message[:400], "label": ESTIMATED}
    if result.exit_code != 0:
        return {
            **metadata,
            "ok": False,
            "windows": [],
            "error": result.stderr[-400:],
            "label": ESTIMATED,
        }

    events = [(m.group(1), float(m.group(2))) for m in _SILENCE_EVENT.finditer(result.stderr)]
    silences: list[tuple[float, float]] = []
    pending: float | None = None
    for kind, value in events:
        if kind == "start":
            pending = value
        elif pending is not None:
            if value > pending:
                silences.append((max(0.0, pending), min(duration, value)))
            pending = None
    if pending is not None and duration > pending:  # trailing silence runs to end of WAV
        silences.append((max(0.0, pending), duration))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(silences):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    windows: list[dict] = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor >= _MIN_WINDOW_S:
            windows.append(
                {
                    "start": round(cursor, 3),
                    "end": round(start, 3),
                    "status": VERIFIED,
                    "source": "waveform",
                }
            )
        cursor = max(cursor, end)
    if duration - cursor >= _MIN_WINDOW_S:
        windows.append(
            {
                "start": round(cursor, 3),
                "end": round(duration, 3),
                "status": VERIFIED,
                "source": "waveform",
            }
        )
    truncated = len(windows) > opts["max_windows"]
    if truncated:
        windows = windows[: opts["max_windows"]]
    return {
        **metadata,
        "ok": True,
        "windows": windows,
        "silences": [{"start": round(s, 3), "end": round(e, 3)} for s, e in merged],
        "truncated": truncated,
        "label": VERIFIED,
    }


def _segments_from(results) -> tuple[list[dict], dict]:
    if isinstance(results, dict):
        meta = {
            key: results.get(key)
            for key in (
                "owner_id",
                "profile_id",
                "provider",
                "consent_fingerprint",
                "generation_id",
                "project_id",
                "script_sha256",
            )
        }
        # generate_narration() emits `voice_profile_id`; a directly supplied alignment may use
        # `profile_id`. Accept both so the generation payload can be passed straight through.
        if not meta.get("profile_id"):
            meta["profile_id"] = results.get("voice_profile_id")
        return list(results.get("segments") or []), meta
    return list(results or []), {}


def _allocate(start: float, end: float, pieces: list[str]) -> list[dict]:
    """Split a measured interval across pieces by character count (explicitly estimated)."""
    total = sum(max(1, len(piece)) for piece in pieces) or 1
    out: list[dict] = []
    cursor = start
    for index, piece in enumerate(pieces):
        share = (end - start) * max(1, len(piece)) / total
        piece_end = end if index == len(pieces) - 1 else cursor + share
        out.append(
            {
                "text": piece.strip(),
                "start": round(cursor, 3),
                "end": round(piece_end, 3),
                "status": ESTIMATED,
            }
        )
        cursor = piece_end
    return out


def _allocate_with_windows(
    start: float, end: float, pieces: list[str], windows: list[dict]
) -> list[dict]:
    """Allocate pieces across measured speech windows; boundaries inside a window stay estimated.

    Piece-to-window assignment uses cumulative character weight, so long pieces are more likely to
    land in longer measured speech spans. The resulting timestamps never cross a measured pause.
    """
    placed: list[tuple[float, float, dict]] = []
    for window in windows:
        left = start + max(0.0, float(window.get("start") or 0.0))
        right = min(end, start + float(window.get("end") or 0.0))
        if right - left >= _MIN_WINDOW_S:
            placed.append((left, right, window))
    if not placed:
        return _allocate(start, end, pieces)

    speech_total = sum(right - left for left, right, _ in placed)
    weights = [max(1, len(piece)) for piece in pieces]
    char_total = sum(weights) or 1
    out: list[dict] = []
    piece_index = 0
    used_chars = 0.0
    running_speech = 0.0
    for window_index, (left, right, _window) in enumerate(placed):
        running_speech += right - left
        upper_chars = char_total * running_speech / speech_total
        group: list[str] = []
        while piece_index < len(pieces):
            midpoint = used_chars + weights[piece_index] / 2
            if window_index == len(placed) - 1 or midpoint <= upper_chars:
                group.append(pieces[piece_index])
                used_chars += weights[piece_index]
                piece_index += 1
            else:
                break
        for item in _allocate(left, right, group):
            item["anchor"] = "waveform_window"
            item["window_status"] = VERIFIED
            out.append(item)
    if piece_index < len(pieces):  # defensive: never drop text if weights skew allocation
        left, right, _ = placed[-1]
        for item in _allocate(left, right, pieces[piece_index:]):
            item["anchor"] = "waveform_window"
            item["window_status"] = VERIFIED
            out.append(item)
    return out


def align_segments(
    ws,
    results,
    segments: list[dict] | None = None,
    *,
    config: dict | None = None,
    gap_seconds: float = 0.0,
) -> dict:
    """Align generated narration to its footage anchors and derive honest timing metadata.

    `results` may be the payload returned by `generate_narration` or a raw list of segment
    records. `segments` is the optional approved script (used only for grounding metadata).
    """
    records, meta = _segments_from(results)
    if not records:
        raise ValidationError("no generated segments to align")
    config = config if config is not None else load_config(ws)
    options = _alignment_options(config)
    owner_id = meta.get("owner_id")
    profile_id = meta.get("profile_id")
    audio_root = generation_dir(ws, owner_id, profile_id) if (owner_id and profile_id) else None
    script_by_id = {s.get("segment_id"): s for s in (segments or []) if isinstance(s, dict)}

    ordered = sorted(
        records, key=lambda s: float(s.get("timeline_start") or s.get("footage_start") or 0.0)
    )
    aligned: list[dict] = []
    limitations: list[str] = []
    cursor: float | None = None
    for record in ordered:
        segment_id = str(record.get("segment_id") or "")
        if not segment_id:
            raise ValidationError("aligned segment has no segment_id")
        duration = float(record.get("duration_seconds") or 0.0)
        if duration <= 0:
            raise ValidationError(f"segment {segment_id} has no measured audio duration")
        script = script_by_id.get(segment_id, {})
        planned = float(
            record.get("timeline_start")
            if record.get("timeline_start") is not None
            else script.get("timeline_start")
            or record.get("footage_start")
            or script.get("footage_start")
            or 0.0
        )
        start = planned if cursor is None else max(planned, cursor + max(0.0, gap_seconds))
        end = round(start + duration, 3)
        cursor = end

        audio_rel = record.get("audio")
        audio_path = ""
        if audio_rel:
            if audio_root is not None:
                audio_path = str(resolve_within(audio_root, str(audio_rel)))
            else:
                candidate = Path(str(audio_rel))
                audio_path = str(
                    candidate if candidate.is_absolute() else Path(ws.base) / candidate
                )
            if not Path(audio_path).is_file():
                raise ValidationError(f"segment {segment_id} audio is missing: {audio_rel}")

        text = str(record.get("text") or "")
        sentences = [s for s in (part.strip() for part in _SENTENCE_RE.findall(text)) if s]
        words = [m.group(0) for m in _WORD_RE.finditer(text)]
        provider_sentences = list(record.get("sentence_timestamps") or [])
        provider_words = list(record.get("word_timestamps") or [])

        measurement = None
        if audio_path and Path(audio_path).is_file():
            measurement = measure_speech_windows(
                Path(audio_path), config, duration=duration, options=options
            )
        measured_windows = (measurement or {}).get("windows") or []

        forced_words = []
        forced_method = ""
        forced_measured = False
        if not provider_words and audio_path and Path(audio_path).is_file():
            try:
                from creator_core.voice.aligners import get_aligner

                aligner = get_aligner(config)
                if aligner is not None and aligner.is_available():
                    ar = aligner.align(
                        Path(audio_path),
                        text,
                        language=str(record.get("language") or "en"),
                        device=str((config or {}).get("voice", {}).get("device") or "auto"),
                    )
                    all_valid = bool(ar.words) and all(
                        getattr(w, "status", "") == "measured" for w in ar.words
                    )
                    cov = getattr(ar, "coverage", {}) or {}
                    coverage_complete = bool(cov.get("fully_measured", ar.measured))
                    if ar.measured and all_valid and coverage_complete:
                        for w in ar.words:
                            ws_ = max(start, start + float(w.start))
                            we_ = min(end, start + float(w.end))
                            if we_ <= ws_:
                                raise ValidationError(
                                    f"invalid measured alignment for {segment_id}: non-positive word duration"
                                )
                            forced_words.append(
                                {
                                    "text": w.word,
                                    "start": round(ws_, 3),
                                    "end": round(we_, 3),
                                    "status": VERIFIED,
                                    "confidence": w.confidence,
                                }
                            )
                        forced_measured = True
                        forced_method = f"forced alignment ({ar.method}; model={ar.model})"
                    else:
                        reason = "invalid/unmeasured timestamps"
                        if cov and not coverage_complete:
                            reason = (
                                f"incomplete transcript coverage "
                                f"({cov.get('matched', 0)}/{cov.get('expected_words', 0)} words; "
                                f"missing={len(cov.get('missing', []))}, "
                                f"unexpected={len(cov.get('unexpected', []))})"
                            )
                        limitations.append(
                            f"forced aligner returned {reason} for {segment_id}; "
                            f"falling back to estimated alignment (partial output is not labelled measured)"
                        )
            except Exception as exc:  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
                limitations.append(f"forced aligner unavailable/failed for {segment_id}: {exc}")

        if provider_sentences:
            sentence_timestamps = provider_sentences
        elif forced_words:
            # Derive sentence boundaries from measured words by cumulative char allocation over sentences.
            sentence_timestamps = _allocate(start, end, sentences or [text])
            # Best-effort: snap sentence ends to nearest measured word boundaries.
            for si, sent in enumerate(sentence_timestamps):
                sent_words = [m.group(0) for m in _WORD_RE.finditer(str(sent.get("text") or ""))]
                if not sent_words or not forced_words:
                    continue
                # approximate by proportional position in forced words
                idx = min(
                    len(forced_words) - 1,
                    int((si + 1) / max(1, len(sentence_timestamps)) * len(forced_words)) - 1,
                )
                if idx >= 0:
                    sent["end"] = forced_words[idx]["end"]
                    sent["status"] = VERIFIED
        elif measured_windows:
            sentence_timestamps = _allocate_with_windows(
                start, end, sentences or [text], measured_windows
            )
        else:
            sentence_timestamps = _allocate(start, end, sentences or [text])

        if provider_words:
            word_timestamps = provider_words
        elif forced_words:
            word_timestamps = forced_words
        elif measured_windows:
            word_timestamps = _allocate_with_windows(start, end, words, measured_windows)
        else:
            word_timestamps = _allocate(start, end, words)

        for item in sentence_timestamps:
            if item.get("status") == "measured":
                item["status"] = VERIFIED
            item.setdefault(
                "status", VERIFIED if (provider_sentences or forced_words) else ESTIMATED
            )
        for item in word_timestamps:
            if item.get("status") == "measured":
                item["status"] = VERIFIED
            item.setdefault(
                "status", VERIFIED if (provider_words or forced_measured) else ESTIMATED
            )

        if provider_words:
            word_source = VERIFIED
            boundary_method = "provider word timestamps"
        elif forced_words and forced_measured:
            word_source = VERIFIED
            boundary_method = forced_method or "forced alignment"
        elif measured_windows:
            word_source = ESTIMATED
            boundary_method = "character allocation within measured waveform speech windows"
        else:
            word_source = ESTIMATED
            boundary_method = "character-count allocation over measured WAV duration"
        measurement_report = measurement or {
            "ok": False,
            "windows": [],
            "label": ESTIMATED,
            "error": "no audio file available for waveform measurement",
        }

        timing_label = (
            VERIFIED if (word_source == VERIFIED and record.get("audio_sha256")) else ESTIMATED
        )
        aligned.append(
            {
                "segment_id": segment_id,
                "text": text,
                "version": record.get("version", 1),
                "status": record.get("status", "generated"),
                "mocked": bool(record.get("mocked")),
                "audio": audio_rel,
                "audio_path": audio_path,
                "audio_sha256": record.get("audio_sha256"),
                "language": record.get("language") or "en",
                "footage_start": float(
                    record.get("footage_start") or script.get("footage_start") or start
                ),
                "footage_end": float(record.get("footage_end") or script.get("footage_end") or end),
                "timeline_start": round(start, 3),
                "timeline_end": end,
                "duration_seconds": round(duration, 3),
                "timing": {
                    "label": timing_label,
                    "method": (
                        "placement anchored to script footage start; duration measured from WAV "
                        "with FFprobe; " + boundary_method + "; not frame-accurate"
                    ),
                    "audio_duration_source": VERIFIED if record.get("audio_sha256") else ESTIMATED,
                    "word_timing_source": word_source,
                    "speech_window_source": (
                        VERIFIED if measurement_report.get("ok") and measured_windows else ESTIMATED
                    ),
                    "boundary_method": boundary_method,
                    "measurement": measurement_report,
                },
                "sentence_timestamps": sentence_timestamps,
                "word_timestamps": word_timestamps,
                "grounding": script.get("grounding") or record.get("grounding"),
            }
        )

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "command": "voice align",
        "ok": True,
        "owner_id": owner_id or "<redacted>",
        "profile_id": profile_id,
        "project_id": meta.get("project_id"),
        "generation_id": meta.get("generation_id"),
        "provider": meta.get("provider"),
        "consent_fingerprint": meta.get("consent_fingerprint"),
        "script_sha256": meta.get("script_sha256"),
        "segments": aligned,
        "timeline_start": aligned[0]["timeline_start"],
        "timeline_end": aligned[-1]["timeline_end"],
        "timeline_duration_seconds": round(cursor or 0.0, 3),
        "label": ESTIMATED,
        "measurement": {
            "method": "ffmpeg silencedetect waveform windows + measured WAV duration",
            "segments_with_speech_windows": sum(
                1 for s in aligned if s["timing"]["speech_window_source"] == VERIFIED
            ),
            "segments_total": len(aligned),
            "label": VERIFIED
            if all(s["timing"]["speech_window_source"] == VERIFIED for s in aligned)
            else ESTIMATED,
        },
        "limitations": limitations
        + [
            (
                "Word/sentence boundaries remain character-count estimates inside measured speech "
                "windows unless a provider supplies native timings or a validated forced aligner."
            ),
            (
                "Speech/pause windows are level-based waveform measurements, not speech recognition; "
                "they do not identify individual words."
            ),
            "No frame-accurate A/V synchrony is claimed; use validate_av_sync for tolerance checks.",
            "Estimated timings may drift with punctuation, proper nouns and non-speech pauses.",
        ],
    }
    return payload


def validate_av_sync(
    alignment: dict, rendered_duration: float, *, tolerance_seconds: float = 0.25
) -> dict:
    """Compare a rendered duration with the narration timeline; never claim frame accuracy."""
    if not isinstance(alignment, dict) or not alignment.get("segments"):
        raise ValidationError("alignment payload has no segments")
    try:
        actual = float(rendered_duration)
    except (TypeError, ValueError) as exc:
        raise ValidationError("rendered_duration must be a number") from exc
    if actual <= 0:
        raise ValidationError("rendered_duration must be positive")
    tolerance = max(0.0, float(tolerance_seconds))
    timeline_end = float(alignment["timeline_end"])
    delta = round(actual - timeline_end, 3)
    ok = abs(delta) <= tolerance
    checks = []
    for segment in alignment["segments"]:
        end = float(segment["timeline_end"])
        checks.append(
            {
                "segment_id": segment["segment_id"],
                "within_render": end <= actual + tolerance,
                "status": VERIFIED if end <= actual + tolerance else ESTIMATED,
            }
        )
    return {
        "ok": ok,
        "label": VERIFIED,
        "method": "rendered duration compared with measured narration timeline end",
        "rendered_duration_seconds": round(actual, 3),
        "narration_timeline_end_seconds": round(timeline_end, 3),
        "delta_seconds": delta,
        "tolerance_seconds": tolerance,
        "within_tolerance": ok,
        "segment_checks": checks,
        "note": (
            "Duration tolerance check only — not a lip-sync or frame-accuracy assertion."
            if ok
            else "Narration timeline extends beyond the rendered duration."
        ),
    }
