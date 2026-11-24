"""QA engine: objective output checks with honest needs_approval items.

Every finding is data. No check claims semantic quality; A/V sync is explicitly deferred to
human review because automated onset correlation is unreliable on synthetic/gameplay audio.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from creator_core.editing.edl import edl_duration
from creator_core.media.ingest import ffmpeg_binary, normalize_probe, probe, sha256_file
from creator_core.security import subprocess as ssub

_BLACK_START = re.compile(r"black_start:([\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
_PEAK = re.compile(r"Peak level dB:\s*([-\d.]+)")


def _check(name: str, ok: bool, detail: str, *, severity: str = "warning") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "severity": severity}


def _scan_once(path: Path, config: dict, *, kind: str, chain: str) -> ssub.RunResult:
    """Run one signal scan. Video (-vf) and audio (-af) filters cannot share a graph."""
    cmd = [
        ffmpeg_binary(config, "ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-v",
        "info",
        "-i",
        os.fspath(path),
        f"-{kind}",
        chain,
        "-f",
        "null",
        os.devnull,
    ]
    return ssub.run(
        cmd,
        timeout=float((config.get("limits") or {}).get("subprocess_timeout_s", 600)),
        max_output=16_000_000,
    )


def _scan_filters(path: Path, config: dict, *, has_audio: bool) -> tuple[dict, dict]:
    """Run video and audio scans separately so neither silently disables the other.

    FFmpeg rejects an audio filter inside a video filter chain (and vice versa), so a
    combined `-vf ...astats...` graph fails to link and every signal check passes
    vacuously. Each scan is now independent, and its exit code is surfaced in the report.
    """
    video = _scan_once(
        path,
        config,
        kind="vf",
        chain="blackdetect=d=0.5:pix_th=0.10:pic_th=0.98,freezedetect=n=-55dB:d=1",
    )
    audio = (
        _scan_once(path, config, kind="af", chain="astats=metadata=0:reset=0")
        if has_audio
        else None
    )

    def _floats(pattern, text) -> list[float]:
        values = []
        for raw in pattern.findall(text):
            try:
                values.append(float(raw))
            except ValueError:  # "-" / "inf" from astats on silent streams
                continue
        return values

    signals = {
        "black_starts": _floats(_BLACK_START, video.stderr),
        "freeze_starts": _floats(_FREEZE_START, video.stderr),
        "peak_db": _floats(_PEAK, audio.stderr) if audio else [],
    }
    meta = {
        "video_exit_code": video.exit_code,
        "video_stderr_tail": video.stderr[-500:],
        "audio_exit_code": audio.exit_code if audio else None,
        "audio_stderr_tail": audio.stderr[-500:] if audio else "",
        "audio_skipped": audio is None,
    }
    return signals, meta


def _caption_checks(edl: dict) -> list[dict]:
    cues = sorted((edl.get("tracks") or {}).get("captions") or [], key=lambda c: float(c["start"]))
    checks = []
    overlap = []
    fast = []
    for prev, cur in pairwise(cues):
        if float(cur["start"]) < float(prev["end"]) - 0.001:
            overlap.append((prev["cue_id"], cur["cue_id"]))
    for cue in cues:
        span = float(cue["end"]) - float(cue["start"])
        if span > 0 and len(str(cue.get("text", ""))) / span > 22:
            fast.append(cue["cue_id"])
    checks.append(
        _check(
            "caption_no_overlap",
            not overlap,
            f"overlapping cues: {overlap}" if overlap else "no overlaps",
        )
    )
    checks.append(
        _check(
            "caption_read_speed",
            not fast,
            f"cues above 22 chars/sec: {fast}" if fast else "all cues readable",
        )
    )
    return checks


def run_qa(
    output: Path,
    edl: dict,
    *,
    config: dict,
    expected_duration: float | None = None,
    report_path: Path | None = None,
) -> dict:
    info = normalize_probe(probe(output, config), output, sha256_file(output))
    checks: list[dict] = []

    expected = float(expected_duration if expected_duration is not None else edl_duration(edl))
    actual = info["duration_seconds"]
    checks.append(
        _check(
            "duration",
            abs(actual - expected) <= max(0.6, expected * 0.03),
            f"expected {expected:.3f}s, got {actual:.3f}s",
            severity="error",
        )
    )

    render = edl["render"]
    checks.append(
        _check(
            "resolution",
            info["video"]["width"] == int(render["width"])
            and info["video"]["height"] == int(render["height"]),
            f"expected {render['width']}x{render['height']}, got "
            f"{info['video']['width']}x{info['video']['height']}",
        )
    )
    checks.append(
        _check(
            "fps",
            abs(info["video"]["fps"] - float(render["fps"])) <= 1.0,
            f"expected {render['fps']} fps, got {info['video']['fps']}",
        )
    )
    checks.append(
        _check(
            "audio_present",
            bool(info["audio_streams"]),
            f"audio streams: {len(info['audio_streams'])}",
            severity="error",
        )
    )

    decode = ssub.run(
        [
            ffmpeg_binary(config, "ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            os.fspath(output),
            "-f",
            "null",
            os.devnull,
        ],
        timeout=float((config.get("limits") or {}).get("subprocess_timeout_s", 600)),
        max_output=4_000_000,
    )
    checks.append(
        _check(
            "full_decode",
            decode.exit_code == 0,
            "decoded without errors" if decode.exit_code == 0 else decode.stderr[-400:],
            severity="error",
        )
    )

    signals, scan_meta = _scan_filters(output, config, has_audio=bool(info["audio_streams"]))
    scan_video_ok = scan_meta["video_exit_code"] == 0
    scan_audio_ok = scan_meta["audio_skipped"] or scan_meta["audio_exit_code"] == 0
    checks.append(
        _check(
            "signal_scan_video",
            scan_video_ok,
            "video signal scan completed"
            if scan_video_ok
            else f"video scan failed (exit {scan_meta['video_exit_code']}): "
            f"{scan_meta['video_stderr_tail'][-200:]}",
            severity="error",
        )
    )
    checks.append(
        _check(
            "signal_scan_audio",
            scan_audio_ok,
            "audio signal scan completed"
            if scan_audio_ok
            else "audio scan skipped (no audio streams)"
            if scan_meta["audio_skipped"]
            else f"audio scan failed (exit {scan_meta['audio_exit_code']}): "
            f"{scan_meta['audio_stderr_tail'][-200:]}",
            severity="error",
        )
    )
    checks.append(
        _check(
            "black_frames",
            scan_video_ok and not signals["black_starts"],
            "video scan unavailable"
            if not scan_video_ok
            else f"black_start timestamps: {signals['black_starts'][:10]}",
        )
    )
    checks.append(
        _check(
            "frozen_frames",
            scan_video_ok and not signals["freeze_starts"],
            "video scan unavailable"
            if not scan_video_ok
            else f"freeze_start timestamps: {signals['freeze_starts'][:10]}",
        )
    )
    peaks = [p for p in signals["peak_db"] if p > -0.5]
    checks.append(
        _check(
            "audio_clipping",
            scan_audio_ok and not peaks,
            "audio scan unavailable"
            if not scan_audio_ok
            else f"peaks above -0.5 dBFS: {peaks[:10]}",
        )
    )

    checks.extend(_caption_checks(edl))
    checks.append(
        _check(
            "av_sync",
            False,
            "Automated onset correlation is not reliable for gameplay/music mixes. "
            "Watch the rendered output at cut boundaries before publishing.",
            severity="review",
        )
    )

    failed_errors = [c for c in checks if not c["ok"] and c["severity"] == "error"]
    report = {
        "schema_version": 1,
        "ok": not failed_errors,
        "output": str(output),
        "sha256": info["sha256"],
        "duration_seconds": actual,
        "checks": checks,
        "auto_fixed": [],
        "needs_approval": [c for c in checks if not c["ok"]],
        "scan": scan_meta,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if report_path is not None:
        from creator_core.workspace.atomic import atomic_write_json

        atomic_write_json(report_path, report)
    return report
