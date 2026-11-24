"""Media understanding: silence, scenes, audio spikes, moments, transcript, repeated takes.

Each detector degrades independently: a failed/optional feature is reported with an error and
never invented. Derived data is written only to the project analysis directory.
"""

from __future__ import annotations

import os
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path

from creator_core.media.ingest import ffmpeg_binary, inspect, register_source
from creator_core.media.injection import analyze_untrusted, sanitize_untrusted
from creator_core.media.transcribe import get_provider
from creator_core.research.provenance import ESTIMATED, HYPOTHESIS
from creator_core.security import subprocess as ssub
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import WorkspacePaths, load_config

MAX_SEGMENTS = 500  # bound derived JSON size even for pathological sources

_SILENCE_START = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")
_SHOWINFO_TIME = re.compile(r"\bpts_time:\s*([\d.]+)")
_ASTATS_LEVEL = re.compile(r"lavfi\.astats\.Overall\.RMS_level\s*=\s*([-\d.inf]+)")


def _ffmpeg_run(
    path: Path,
    config: dict,
    *,
    af: str | None = None,
    vf: str | None = None,
    extra: list[str] | None = None,
) -> ssub.RunResult:
    cmd = [
        ffmpeg_binary(config, "ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-v",
        "info",
        "-i",
        os.fspath(path),
    ]
    if af:
        cmd += ["-af", af]
    if vf:
        cmd += ["-vf", vf, "-an"]
    if extra:
        cmd += extra
    cmd += ["-f", "null", os.devnull]
    return ssub.run(
        cmd,
        timeout=float((config.get("limits") or {}).get("subprocess_timeout_s", 600)),
        max_output=16_000_000,
    )


def _safe(name: str, fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - every detector failure is data, not a fabricated result
        return {
            "ok": False,
            "error": f"{exc.__class__.__name__}: {exc}",
            "segments": [],
            "label": HYPOTHESIS,
        }


def detect_silence(path: Path, config: dict, *, min_duration: float = 0.3) -> dict:
    result = _ffmpeg_run(path, config, af=f"silencedetect=noise=-35dB:d={min_duration}")
    if result.exit_code != 0:
        return {"ok": False, "error": result.stderr[-400:], "segments": [], "label": HYPOTHESIS}
    starts = [float(v) for v in _SILENCE_START.findall(result.stderr)]
    ends = [float(v) for v in _SILENCE_END.findall(result.stderr)]
    segments = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else start + min_duration
        if end > start:
            segments.append(
                {
                    "type": "dead_air",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(end - start, 3),
                    "confidence": 0.9,
                    "evidence": "ffmpeg silencedetect noise=-35dB",
                    "label": ESTIMATED,
                }
            )
    truncated = len(segments) > MAX_SEGMENTS
    return {
        "ok": True,
        "segments": segments[:MAX_SEGMENTS],
        "label": ESTIMATED,
        "method": "ffmpeg silencedetect",
        "detector_version": 1,
        "truncated": truncated,
    }


def detect_scenes(path: Path, config: dict, *, threshold: float = 0.25) -> dict:
    result = _ffmpeg_run(path, config, vf=f"select='gt(scene,{threshold})',showinfo")
    if result.exit_code != 0:
        return {"ok": False, "error": result.stderr[-400:], "segments": [], "label": HYPOTHESIS}
    times = sorted({round(float(v), 3) for v in _SHOWINFO_TIME.findall(result.stderr)})
    segments = [
        {
            "type": "scene_change",
            "start": round(max(0.0, t - 0.15), 3),
            "end": round(t + 0.15, 3),
            "timestamp": t,
            "confidence": 0.75,
            "evidence": f"ffmpeg select scene>{threshold}",
            "label": ESTIMATED,
        }
        for t in times
    ]
    truncated = len(segments) > MAX_SEGMENTS
    return {
        "ok": True,
        "segments": segments[:MAX_SEGMENTS],
        "timestamps": times[:MAX_SEGMENTS],
        "label": ESTIMATED,
        "method": "ffmpeg select+showinfo",
        "detector_version": 1,
        "truncated": truncated,
    }


def detect_audio_events(path: Path, config: dict, *, delta_db: float = 3.0) -> dict:
    af = "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
    result = _ffmpeg_run(path, config, af=af)
    if result.exit_code != 0:
        return {"ok": False, "error": result.stderr[-400:], "segments": [], "label": HYPOTHESIS}
    lines = result.stdout.splitlines()
    samples: list[tuple[float, float]] = []
    current_time: float | None = None
    for line in lines:
        time_match = re.search(r"\bpts_time:([-\d.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
        level_match = _ASTATS_LEVEL.search(line)
        if level_match and current_time is not None:
            level = level_match.group(1)
            try:
                value = float(level)
            except ValueError:  # -inf / inf
                continue
            if value > -90:
                samples.append((current_time, value))
    if not samples:
        return {
            "ok": True,
            "segments": [],
            "label": ESTIMATED,
            "method": "ffmpeg astats",
            "note": "no RMS samples produced",
        }
    baseline = statistics.median(level for _, level in samples)
    threshold = baseline + delta_db
    segments = []
    run_start: float | None = None
    peak = -999.0
    for ts, level in samples:
        if level >= threshold:
            if run_start is None:
                run_start, peak = ts, level
            else:
                peak = max(peak, level)
        elif run_start is not None:
            segments.append(
                {
                    "type": "audio_spike",
                    "start": round(run_start, 3),
                    "end": round(ts, 3),
                    "peak_rms_db": round(peak, 2),
                    "confidence": 0.7,
                    "evidence": f"RMS >= baseline+{delta_db}dB",
                    "label": ESTIMATED,
                }
            )
            run_start = None
    if run_start is not None:
        ts = samples[-1][0]
        segments.append(
            {
                "type": "audio_spike",
                "start": round(run_start, 3),
                "end": round(ts, 3),
                "peak_rms_db": round(peak, 2),
                "confidence": 0.7,
                "evidence": f"RMS >= baseline+{delta_db}dB",
                "label": ESTIMATED,
            }
        )
    truncated = len(segments) > MAX_SEGMENTS
    segments = segments[:MAX_SEGMENTS]
    return {
        "ok": True,
        "segments": segments,
        "truncated": truncated,
        "baseline_rms_db": round(baseline, 2),
        "threshold_rms_db": round(threshold, 2),
        "label": ESTIMATED,
        "method": "ffmpeg astats",
        "detector_version": 1,
    }


def cluster_repeated_takes(transcript: dict) -> dict:
    segments = transcript.get("segments") or []
    grams = []
    for seg in segments:
        words = re.findall(r"[a-z0-9']+", sanitize_untrusted(seg.get("text", "")).lower())
        grams.append({tuple(words[i : i + 3]) for i in range(max(0, len(words) - 2))})
    clusters = []
    for i in range(len(grams)):
        for j in range(i + 1, len(grams)):
            if not grams[i] or not grams[j]:
                continue
            similarity = len(grams[i] & grams[j]) / len(grams[i] | grams[j])
            if similarity >= 0.6:
                clusters.append(
                    {
                        "start_a": segments[i]["start"],
                        "start_b": segments[j]["start"],
                        "similarity": round(similarity, 3),
                        "label": ESTIMATED,
                    }
                )
    return {
        "ok": True,
        "clusters": clusters,
        "label": ESTIMATED,
        "note": "Similarity is a keyword/3-gram heuristic, not speaker identification.",
    }


def rank_moments(scenes: dict, audio: dict, transcript: dict, *, window: float = 4.0) -> dict:
    candidates: dict[float, float] = {}
    for seg in scenes.get("segments", []):
        t = seg.get("timestamp", seg.get("start", 0))
        candidates[t] = candidates.get(t, 0) + 0.35
    for seg in audio.get("segments", []):
        t = (seg["start"] + seg["end"]) / 2
        candidates[t] = candidates.get(t, 0) + 0.4
    for seg in transcript.get("segments", []):
        text = seg.get("text", "").lower()
        if any(word in text for word in ("wait", "wow", "no way", "finally", "again", "perfect")):
            t = (seg["start"] + seg["end"]) / 2
            candidates[t] = candidates.get(t, 0) + 0.25
    moments = []
    for t, score in sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)[:10]:
        moments.append(
            {
                "type": "highlight",
                "start": round(max(0.0, t - window / 2), 3),
                "end": round(t + window / 2, 3),
                "center": round(t, 3),
                "score": round(score, 3),
                "label": ESTIMATED,
                "evidence": "scene change / audio energy / transcript cue",
            }
        )
    note = "Highlight ranking is a signal-based heuristic; creator review is required."
    if not moments:
        note = (
            "No scene, audio or transcript signal was strong enough to rank a highlight; "
            "none is fabricated. Creator review is required."
        )
    return {"ok": True, "segments": moments, "label": ESTIMATED, "note": note}


def analyze(
    path: Path | str,
    ws: WorkspacePaths,
    *,
    project_id: str | None = None,
    transcribe: bool = False,
    config: dict | None = None,
) -> dict:
    config = config or load_config(ws)
    info = inspect(path, ws, config)
    source = register_source(ws, info, project_id)
    project = Path(source["project_dir"])
    integrity = analyze_untrusted(str(info.get("metadata", {}).get("comment", "")))

    silence = _safe("silence", lambda: detect_silence(Path(info["path"]), config))
    scenes = _safe("scenes", lambda: detect_scenes(Path(info["path"]), config))
    audio = _safe("audio", lambda: detect_audio_events(Path(info["path"]), config))
    provider = get_provider(config, enabled=transcribe)
    transcript = _safe("transcript", lambda: provider.transcribe(Path(info["path"])))
    transcript.setdefault("segments", [])
    repetition = cluster_repeated_takes(transcript)
    # rank_moments only emits highlights backed by a scene/audio/transcript signal.
    # A source with no such signals intentionally yields no fabricated moments.
    moments = rank_moments(scenes, audio, transcript)

    detectors = {
        "silence": silence,
        "scenes": scenes,
        "audio_events": audio,
        "transcript": transcript,
    }
    detector_status = {
        name: {
            "ok": bool(detector.get("ok")),
            "label": detector.get("label", HYPOTHESIS),
            "error": detector.get("error"),
        }
        for name, detector in detectors.items()
    }
    degraded = [name for name, status in detector_status.items() if not status["ok"]]
    report = {
        "schema_version": 1,
        "command": "media analyze",
        "ok": True,  # the analysis command itself succeeded; detectors have their own status
        "project_id": source["project_id"],
        "source": info,
        "registration": source,
        "analysis": {
            "silence": silence,
            "scenes": scenes,
            "audio_events": audio,
            "transcript": transcript,
            "repeated_takes": repetition,
            "moments": moments,
        },
        "analysis_status": {
            "ok": not degraded,
            "degraded": degraded,
            "detectors": detector_status,
        },
        "untrusted_metadata": integrity,
        "limitations": [
            "Silence/scene/audio detectors are signal heuristics, not semantic understanding.",
            "No transcript is produced unless ASR is installed and --transcribe is passed.",
            "Metadata and transcript text are data; embedded instructions are never followed.",
        ]
        + (
            [
                (
                    f"Detector(s) failed and contributed no signals: {', '.join(degraded)}. "
                    "Editing strategies will not infer edits from them; review the EDL provenance."
                )
            ]
            if degraded
            else []
        ),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    analysis_dir = project / "analysis"
    analysis_dir.mkdir(exist_ok=True)
    atomic_write_json(analysis_dir / f"{info['source_id']}.json", report)
    return report
