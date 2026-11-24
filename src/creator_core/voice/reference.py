"""Reference-audio validation and secure single-copy storage (Phase 11C).

Validation is real: FFprobe supplies container metadata, and a bounded FFmpeg PCM decode supplies
level/clipping/silence/noise statistics. Nothing is fabricated. Speaker count is a labelled
heuristic unless the optional pyannote diarizer is explicitly installed and used elsewhere; a
single-speaker assumption is never presented as verified identity detection.

The validated sample is copied exactly once into the owner's reference directory. Re-registering
the same bytes is idempotent (hash-compared), so we never accumulate unnecessary copies.
"""

from __future__ import annotations

import array
import json
import math
import os
import re
import shutil
import uuid
import wave
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.media.ingest import (
    AUDIO_EXTENSIONS,
    ffmpeg_binary,
    sha256_file,
    validate_media_path,
)
from creator_core.research.provenance import ESTIMATED
from creator_core.security import subprocess as ssub
from creator_core.voice.paths import ensure_voice_dirs, owner_dir, validate_owner_id

RECORDING_GUIDANCE = [
    "Record 30-120 seconds of clean, uninterrupted speech in a quiet room.",
    "Use one speaker only, close to a decent microphone (15-25 cm), at a steady volume.",
    "Speak naturally at a conversational pace; include a few different sentences.",
    "Avoid music, echo, background noise, heavy compression and clipping (no red peaks).",
    "Prefer WAV/FLAC at 44.1 kHz or 48 kHz mono; MP3/M4A/OGG/AAC are also accepted.",
    "Do not read from a noisy phone speaker or a shared call with other people talking.",
]

_MIN_DURATION_S = 15.0
_IDEAL_DURATION_S = 30.0
_MAX_ANALYSIS_S = 60.0  # bounded PCM decode prevents memory exhaustion on hostile inputs
_ANALYSIS_RATE = 16000
_FRAME = 320  # 20 ms at 16 kHz
_SILENCE_ABS = 220  # ~-43 dBFS for s16
_SILENCE_DB = -42.0


def _db(rms: float) -> float:
    return round(20.0 * math.log10(max(rms, 1e-9) / 32768.0), 2)


def _probe(path: Path, config: dict) -> dict:
    cmd = [
        ffmpeg_binary(config, "ffprobe"),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        os.fspath(path),
    ]
    result = ssub.run(
        cmd,
        timeout=float((config.get("limits") or {}).get("subprocess_timeout_s", 600)),
        max_output=8_000_000,
    )
    if result.exit_code != 0:
        raise ValidationError(f"ffprobe failed for {path.name}: {result.stderr[-300:]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"ffprobe returned invalid JSON for {path.name}: {exc}") from exc


def _decode_pcm(path: Path, config: dict, ws) -> array.array:
    """Decode a bounded mono 16 kHz s16 WAV into a workspace temp file, then read it."""
    ws.tmp.mkdir(parents=True, exist_ok=True)
    tmp = ws.tmp / f"voice_ref_{uuid.uuid4().hex}.wav"
    cmd = [
        ffmpeg_binary(config, "ffmpeg"),
        "-nostdin",
        "-y",
        "-hide_banner",
        "-v",
        "error",
        "-t",
        str(_MAX_ANALYSIS_S),
        "-i",
        os.fspath(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(_ANALYSIS_RATE),
        "-c:a",
        "pcm_s16le",
        os.fspath(tmp),
    ]
    try:
        result = ssub.run(
            cmd,
            timeout=float((config.get("limits") or {}).get("subprocess_timeout_s", 600)),
            max_output=8_000_000,
        )
        if result.exit_code != 0 or not tmp.is_file():
            raise ValidationError(
                f"could not decode {path.name} for analysis: {result.stderr[-300:]}"
            )
        with wave.open(os.fspath(tmp), "rb") as wav:
            frames = wav.readframes(wav.getnframes())
        samples = array.array("h")
        samples.frombytes(frames[: len(frames) - len(frames) % 2])
        if not samples:
            raise ValidationError(f"{path.name} contains no audio samples")
        return samples
    finally:
        tmp.unlink(missing_ok=True)


def analyze_reference(path: Path | str, ws, *, config: dict | None = None) -> dict:
    """Return a real, evidence-labelled quality assessment of one reference recording."""
    from creator_core.workspace.layout import load_config

    config = config or load_config(ws)
    resolved = validate_media_path(path, ws, config)
    suffix = resolved.suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise ValidationError(
            f"unsupported reference audio format: {suffix or '(none)'}",
            hint="supported: " + ", ".join(sorted(AUDIO_EXTENSIONS)),
        )

    info = _probe(resolved, config)
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise ValidationError(f"{resolved.name} has no audio stream")
    stream = streams[0]
    duration_s = float((info.get("format") or {}).get("duration") or stream.get("duration") or 0)
    sample_rate = int(stream.get("sample_rate") or 0)
    channels = int(stream.get("channels") or 0)
    codec = str(stream.get("codec_name") or "unknown")

    samples = _decode_pcm(resolved, config, ws)
    n = len(samples)
    peak = max(abs(int(s)) for s in samples)
    clipped = sum(1 for s in samples if abs(int(s)) >= 32700)
    clipping_ratio = clipped / n

    frame_rms: list[float] = []
    silent_frames = 0
    for i in range(0, n - _FRAME + 1, _FRAME):
        window = samples[i : i + _FRAME]
        sq = sum(int(s) * int(s) for s in window) / _FRAME
        rms = math.sqrt(sq)
        frame_rms.append(rms)
        if rms < _SILENCE_ABS:
            silent_frames += 1
    if not frame_rms:
        raise ValidationError(f"{resolved.name} is too short to analyse")
    ordered = sorted(frame_rms)
    noise_rms = ordered[max(0, int(len(ordered) * 0.2) - 1)]
    speech_frames = [r for r in frame_rms if r >= _SILENCE_ABS]
    speech_rms = sorted(speech_frames)[len(speech_frames) // 2] if speech_frames else 0.0
    silence_ratio = silent_frames / len(frame_rms)
    snr_db = round(_db(speech_rms) - _db(noise_rms), 2) if speech_frames else 0.0

    # Longest contiguous silent run (from the bounded PCM decode, not a claim of ASR).
    longest_silent_frames = run = 0
    for rms in frame_rms:
        if rms < _SILENCE_ABS:
            run += 1
            longest_silent_frames = max(longest_silent_frames, run)
        else:
            run = 0
    longest_silence_s = round(longest_silent_frames * _FRAME / _ANALYSIS_RATE, 2)

    # Speaker estimate is explicitly unverified: one clean mono sample is assumed unless the
    # stereo channels carry clearly different material (a real, measurable imbalance).
    speaker_evidence = "single clean mono sample assumed; identity/turn diarization not performed"
    speaker_estimate = 1
    if channels >= 2:
        cmd = [
            ffmpeg_binary(config, "ffmpeg"),
            "-nostdin",
            "-hide_banner",
            "-v",
            "info",
            "-t",
            str(_MAX_ANALYSIS_S),
            "-i",
            os.fspath(resolved),
            "-af",
            "astats=metadata=0:reset=0",
            "-f",
            "null",
            os.devnull,
        ]
        stats = ssub.run(
            cmd,
            timeout=float((config.get("limits") or {}).get("subprocess_timeout_s", 600)),
            max_output=16_000_000,
        )
        left = [
            float(x)
            for x in re.findall(r"Channel: 1[\s\S]*?RMS level dB:\s*([-\d.]+)", stats.stderr)
        ]
        right = [
            float(x)
            for x in re.findall(r"Channel: 2[\s\S]*?RMS level dB:\s*([-\d.]+)", stats.stderr)
        ]
        if left and right and abs(left[-1] - right[-1]) > 10:
            speaker_estimate = 2
            speaker_evidence = f"stereo channel RMS imbalance {abs(left[-1] - right[-1]):.1f} dB"

    checks = {
        "format": suffix in AUDIO_EXTENSIONS and codec != "unknown",
        "duration": duration_s >= _MIN_DURATION_S,
        "sample_rate": sample_rate >= 16000,
        "noise": snr_db >= 15.0,
        "clipping": clipping_ratio < 0.01,
        "silence": silence_ratio < 0.5 and longest_silence_s <= 10.0,
        "speaker_count": speaker_estimate == 1,
    }
    reasons: list[str] = []
    if not checks["duration"]:
        reasons.append(
            f"recording is {duration_s:.1f}s; at least {_MIN_DURATION_S:.0f}s is required"
        )
    if not checks["sample_rate"]:
        reasons.append(f"sample rate {sample_rate} Hz is below 16000 Hz")
    if not checks["noise"]:
        reasons.append(f"estimated SNR {snr_db} dB is below 15 dB (too much background noise)")
    if not checks["clipping"]:
        reasons.append(f"{clipping_ratio * 100:.1f}% of samples are clipped")
    if not checks["silence"]:
        reasons.append(
            f"{silence_ratio * 100:.1f}% silence with a {longest_silence_s}s continuous gap"
        )
    if not checks["speaker_count"]:
        reasons.append(
            "possibly more than one speaker/source detected; supply a single clean voice"
        )

    score = 0
    score += (
        25
        if duration_s >= 120
        else 18
        if duration_s >= _IDEAL_DURATION_S
        else 8
        if duration_s >= _MIN_DURATION_S
        else 0
    )
    score += (
        20
        if sample_rate >= 44100
        else 14
        if sample_rate >= 22050
        else 7
        if sample_rate >= 16000
        else 0
    )
    score += 25 if snr_db >= 30 else 18 if snr_db >= 22 else 10 if snr_db >= 15 else 0
    score += 15 if clipping_ratio < 0.0005 else 7 if clipping_ratio < 0.01 else 0
    score += 15 if silence_ratio < 0.2 else 8 if silence_ratio < 0.5 else 0

    return {
        "ok": True,
        "source_path": str(resolved),
        "sha256": sha256_file(resolved),
        "filename": resolved.name,
        "duration_seconds": round(duration_s, 2),
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "codec": codec,
        "peak_dbfs": _db(peak),
        "rms_dbfs": _db(speech_rms),
        "noise_floor_dbfs": _db(noise_rms),
        "snr_estimate_db": snr_db,
        "clipping_ratio": round(clipping_ratio, 6),
        "silence_ratio": round(silence_ratio, 4),
        "longest_silence_seconds": longest_silence_s,
        "speaker_count_estimate": {
            "value": speaker_estimate,
            "verified": False,
            "evidence": speaker_evidence,
        },
        "checks": checks,
        "quality_score": score,
        "suitable": all(checks.values()),
        "reasons": reasons,
        "guidance": RECORDING_GUIDANCE,
        "analysis_seconds": min(duration_s, _MAX_ANALYSIS_S),
        "label": ESTIMATED,
        "method": "ffprobe metadata + bounded 16 kHz PCM RMS/clipping/silence analysis",
    }


def store_reference(
    ws, owner_id: str, profile_id: str, source: Path | str, assessment: dict
) -> dict:
    """Copy one validated sample into owner-scoped storage; reuse identical bytes."""
    validate_owner_id(owner_id)
    src = Path(source)
    if not src.is_file():
        raise ValidationError(f"reference audio not found: {src}")
    paths = ensure_voice_dirs(ws)
    target_dir = owner_dir(paths, owner_id, "references") / profile_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"reference{src.suffix.lower()}"
    expected = assessment.get("sha256") or sha256_file(src)
    if target.is_file():
        actual = sha256_file(target)
        if actual != expected:
            raise ValidationError(
                "a different reference is already stored for this profile",
                hint="delete the profile before registering a new voice",
            )
    else:
        shutil.copy2(src, target)
        if sha256_file(target) != expected:
            target.unlink(missing_ok=True)
            raise ValidationError("reference copy failed integrity verification")
    relative = str(target.relative_to(owner_dir(paths, owner_id, "references"))).replace("\\", "/")
    return {
        "sha256": expected,
        "stored_path": relative,
        "filename": target.name,
        "duration_seconds": assessment.get("duration_seconds"),
        "sample_rate_hz": assessment.get("sample_rate_hz"),
        "channels": assessment.get("channels"),
        "codec": assessment.get("codec"),
        "snr_estimate_db": assessment.get("snr_estimate_db"),
        "clipping_ratio": assessment.get("clipping_ratio"),
        "silence_ratio": assessment.get("silence_ratio"),
        "quality_score": assessment.get("quality_score"),
        "suitable": bool(assessment.get("suitable")),
        "speaker_count_estimate": assessment.get("speaker_count_estimate"),
        "label": assessment.get("label"),
        "method": assessment.get("method"),
    }
