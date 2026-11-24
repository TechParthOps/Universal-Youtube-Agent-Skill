"""9:16 reframe planning: motion-centroid focus tracking with blur-pad fallback.

Focus is measured, not guessed: a low-resolution grayscale sample of the chosen window is
decoded with real FFmpeg, consecutive-frame differences locate moving subject matter, and a
linear trajectory is fitted for an animated crop. When no reliable motion exists the plan
falls back to a centered foreground with a blurred expanded background (never a naive crop).
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path

from creator_core.media.ingest import ffmpeg_binary
from creator_core.research.provenance import ESTIMATED, HYPOTHESIS
from creator_core.security import subprocess as ssub

SAMPLE_FPS = 2.0
GRID_W, GRID_H = 64, 36
OUT_W, OUT_H = 1080, 1920
_MIN_MOTION = 180.0  # summed weighted pixel change below this is treated as noise
_MIN_SPREAD = 0.015  # normalized centroid variance below this is not a trajectory
_THRESHOLD = 12  # ignore compression noise in frame differences


def _even(value: float) -> int:
    result = round(float(value))
    return result if result % 2 == 0 else result + 1


def _decode_samples(source: Path, start: float, duration: float, dest: Path, config: dict) -> bool:
    timeout = float((config.get("limits") or {}).get("subprocess_timeout_s", 600))
    cmd = [
        ffmpeg_binary(config, "ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"fps={SAMPLE_FPS:g},scale={GRID_W}:{GRID_H},format=gray",
        "-f",
        "rawvideo",
        "-y",
        str(dest),
    ]
    result = ssub.run(cmd, timeout=timeout, max_output=2_000_000)
    return result.exit_code == 0 and dest.is_file() and dest.stat().st_size >= GRID_W * GRID_H


def _centroids(raw: bytes) -> list[dict]:
    size = GRID_W * GRID_H
    frames = [raw[i : i + size] for i in range(0, len(raw) - size + 1, size)]
    points: list[dict] = []
    for index in range(1, len(frames)):
        prev, cur = frames[index - 1], frames[index]
        total = weight_x = weight_y = 0.0
        for idx, (before, after) in enumerate(zip(prev, cur)):
            delta = abs(after - before)
            if delta < _THRESHOLD:
                continue
            total += delta
            weight_x += (idx % GRID_W) * delta
            weight_y += (idx // GRID_W) * delta
        if total < _MIN_MOTION:
            continue
        points.append(
            {
                "t": round(index / SAMPLE_FPS, 3),
                "x_norm": round(_clamp(weight_x / total / (GRID_W - 1)), 4),
                "y_norm": round(_clamp(weight_y / total / (GRID_H - 1)), 4),
                "confidence": round(min(1.0, total / (GRID_W * GRID_H * 64.0)), 4),
            }
        )
    return points


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _fit(points: list[dict], key: str) -> tuple[float, float]:
    n = len(points)
    mean_t = sum(p["t"] for p in points) / n
    mean_v = sum(p[key] for p in points) / n
    denom = sum((p["t"] - mean_t) ** 2 for p in points)
    if denom <= 1e-9:
        return 0.0, mean_v
    slope = sum((p["t"] - mean_t) * (p[key] - mean_v) for p in points) / denom
    return slope, mean_v - slope * mean_t


def _spread(points: list[dict], key: str) -> float:
    if len(points) < 2:
        return 0.0
    values = [p[key] for p in points]
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _crop_plan(
    width: int,
    height: int,
    slope: float,
    intercept: float,
    y_slope: float,
    y_intercept: float,
    portrait_source: bool,
) -> dict:
    target = OUT_W / OUT_H
    if width / height > target:
        crop_w, crop_h = min(width, _even(height * target)), height
        x_expr = f"clip(({slope:.6f}*t+{intercept:.6f})*iw-{crop_w / 2:.3f},0,iw-ow)"
        y_expr = "0"
        window_x = _clamp(intercept) * width - crop_w / 2.0
        window_y = 0.0
    else:
        crop_w, crop_h = width, min(height, _even(width / target))
        x_expr = "0"
        y_expr = f"clip(({y_slope:.6f}*t+{y_intercept:.6f})*ih-{crop_h / 2:.3f},0,ih-oh)"
        window_x = 0.0
        window_y = _clamp(y_intercept) * height - crop_h / 2.0
    window_x = max(0.0, min(width - crop_w, window_x))
    window_y = max(0.0, min(height - crop_h, window_y))
    filt = (
        f"crop={crop_w}:{crop_h}:x='{x_expr}':y='{y_expr}',"
        f"scale={OUT_W}:{OUT_H},setsar=1,format=yuv420p[vout]"
    )
    return {
        "mode": "crop_track",
        "filter": filt,
        "focus_window": {
            "x": round(window_x, 2),
            "y": round(window_y, 2),
            "width": crop_w,
            "height": crop_h,
            "source_width": width,
            "source_height": height,
        },
        "model": {
            "x_slope": round(slope, 6),
            "x_intercept": round(intercept, 6),
            "y_slope": round(y_slope, 6),
            "y_intercept": round(y_intercept, 6),
        },
        "reason": "animated crop follows a measured motion centroid",
        "label": ESTIMATED,
    }


def _blur_pad_plan(width: int, height: int, reason: str) -> dict:
    filt = (
        f"split=2[bg][fg];"
        f"[bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},gblur=sigma=20[bg2];"
        f"[fg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,setsar=1[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,format=yuv420p[vout]"
    )
    return {
        "mode": "blur_pad",
        "filter": filt,
        "focus_window": {
            "x": 0.0,
            "y": 0.0,
            "width": width,
            "height": height,
            "source_width": width,
            "source_height": height,
        },
        "model": None,
        "reason": reason,
        "label": HYPOTHESIS,
    }


def plan_reframe(
    source: Path,
    *,
    start: float,
    duration: float,
    width: int,
    height: int,
    ws,
    config: dict,
    focus_mode: str = "auto",
) -> dict:
    """Return a reframe plan for a window. `width`/`height` come from FFprobe, not user text."""
    if focus_mode not in ("auto", "center"):
        from creator_core.cli.errors import ValidationError

        raise ValidationError("focus_mode must be 'auto' or 'center'")
    if focus_mode == "center":
        return _blur_pad_plan(
            width, height, "center focus requested: full frame kept with blur pad"
        )

    sample = Path(ws.tmp) / f"focus_{uuid.uuid4().hex[:12]}.gray"
    try:
        ok = _decode_samples(Path(source), start, duration, sample, config)
        points = _centroids(sample.read_bytes()) if ok else []
    finally:
        sample.unlink(missing_ok=True)

    trajectory = [
        {
            "t": p["t"],
            "x_norm": p["x_norm"],
            "y_norm": p["y_norm"],
            "confidence": p["confidence"],
            "label": ESTIMATED,
        }
        for p in points
    ]
    if len(points) < 2 or _spread(points, "x_norm") < _MIN_SPREAD:
        plan = _blur_pad_plan(
            width,
            height,
            "no reliable motion centroid in this window; full frame preserved "
            "with blurred background",
        )
    else:
        x_slope, x_intercept = _fit(points, "x_norm")
        y_slope, y_intercept = _fit(points, "y_norm")
        plan = _crop_plan(
            width,
            height,
            x_slope,
            x_intercept,
            y_slope,
            y_intercept,
            width / height <= OUT_W / OUT_H,
        )
    plan["trajectory"] = trajectory
    plan["sample_fps"] = SAMPLE_FPS
    plan["output"] = {"width": OUT_W, "height": OUT_H}
    return plan
