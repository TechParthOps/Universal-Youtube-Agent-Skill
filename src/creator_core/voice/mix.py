"""Non-destructive voice mix pass (Phase 11G/11I).

The base render produced by the existing editing stack is never modified. This pass reads its
video stream, ducks its original audio during narration windows, delays each narration WAV to its
aligned timeline position and writes a *new* render with an additive audio bed. Hybrid sections
can preserve, insert, replace or silence original passages via deterministic volume automation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import PathNotFoundError, ValidationError
from creator_core.media.ingest import ffmpeg_binary, probe, sha256_file
from creator_core.security import subprocess as ssub
from creator_core.security.paths import resolve_within
from creator_core.voice.paths import voice_paths
from creator_core.voice.synthesis import generation_dir
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import load_config, project_dir

_SCHEMA_VERSION = 1
_PROFILE_RE = re.compile(r"^vpr_[A-Za-z0-9_]+$")
_RENDER_RE = re.compile(r"^edit_v(\d{3})_r(\d{3})\.mp4$")


def find_profile_owner(ws, profile_id: str) -> str:
    """Locate the single owner that holds a profile id (paths stay inside the voice tree)."""
    if not _PROFILE_RE.fullmatch(str(profile_id)):
        raise ValidationError(f"invalid voice profile id: {profile_id!r}")
    paths = voice_paths(ws)
    matches = []
    for owner_dir in sorted(paths.profiles.iterdir()):
        if not owner_dir.is_dir() or not (owner_dir / profile_id / "voice_latest.json").is_file():
            continue
        matches.append(owner_dir.name)
    if len(matches) != 1:
        raise PathNotFoundError(
            f"could not resolve a unique owner for voice profile {profile_id}",
            hint="register the voice or pass the correct profile id",
        )
    return matches[0]


def _gain_db(db: float) -> float:
    return 10.0 ** (float(db) / 20.0)


def _duck_expression(windows: list[tuple[float, float, float]]) -> str:
    """Build a real FFmpeg volume expression; commas are escaped for filter parsing."""
    expression = "1"
    for start, end, factor in windows:
        expression = f"if(between(t,{start:.3f},{end:.3f}),{factor:.6f},{expression})"
    return expression.replace(",", "\\,")


def _locate_base_render(ws, project_id: str, source_render: str | None) -> Path:
    if source_render:
        path = Path(source_render)
        if not path.is_file():
            raise PathNotFoundError(f"base render not found: {source_render}")
        return path
    renders = project_dir(ws, project_id) / "renders"
    candidates = []
    for path in renders.glob("edit_v*_r*.mp4") if renders.is_dir() else []:
        match = _RENDER_RE.match(path.name)
        if match:
            candidates.append((int(match.group(1)), int(match.group(2)), path))
    if not candidates:
        raise PathNotFoundError(
            f"no base render found for project {project_id}",
            hint="run `creator edit render` first, or pass --source-render",
        )
    return max(candidates)[2]


def build_mix_plan(ws, edl: dict, *, source_render: str | None = None) -> dict:
    """Build (and validate) the FFmpeg plan without executing it."""
    voice = edl.get("voice") if isinstance(edl, dict) else None
    if not isinstance(voice, dict) or not voice.get("segments"):
        raise ValidationError(
            "EDL has no voice segments to mix",
            hint="run `creator voice generate` and compose the EDL first",
        )
    project_id = str(edl.get("project_id") or "")
    if not project_id:
        raise ValidationError("EDL has no project_id")
    base = _locate_base_render(ws, project_id, source_render)
    owner = find_profile_owner(ws, str(voice.get("profile_id") or ""))
    root = generation_dir(ws, owner, str(voice["profile_id"]))
    mix_config = voice.get("mix") or {}

    audio_inputs: list[dict] = []
    windows: list[tuple[float, float, float]] = []
    for segment in voice["segments"]:
        rel = segment.get("audio")
        if not rel:
            raise ValidationError(f"voice segment {segment.get('segment_id')} has no audio")
        audio_path = resolve_within(root, str(rel))
        if not audio_path.is_file():
            raise PathNotFoundError(f"voice audio is missing: {rel}")
        start = float(segment["timeline_start"])
        end = float(segment["timeline_end"])
        if end <= start:
            raise ValidationError(f"voice segment {segment['segment_id']} has invalid timing")
        audio_inputs.append(
            {
                "segment_id": segment["segment_id"],
                "path": str(audio_path),
                "sha256": segment.get("audio_sha256") or sha256_file(audio_path),
                "delay_ms": round(start * 1000),
            }
        )
        windows.append(
            (
                start,
                end,
                _gain_db(
                    mix_config.get("duck_to_db", -28.0) - mix_config.get("gameplay_db", -16.0)
                ),
            )
        )

    for section in mix_config.get("hybrid_sections") or []:
        action = section.get("action")
        if action not in ("preserve", "insert", "replace", "silence"):
            raise ValidationError(f"invalid hybrid section action: {action!r}")
        start, end = float(section["start"]), float(section["end"])
        if end <= start:
            raise ValidationError("hybrid section has invalid timing")
        if action in ("replace", "silence"):
            windows.append(
                (
                    start,
                    end,
                    0.0001
                    if action == "silence"
                    else _gain_db(
                        mix_config.get("duck_to_db", -28.0) - mix_config.get("gameplay_db", -16.0)
                    ),
                )
            )
    windows.sort(key=lambda w: (w[0], w[1]))

    narration_gain = _gain_db(mix_config.get("narration_db", 0.0))
    bed_gain = _gain_db(mix_config.get("gameplay_db", -16.0))
    duck_expr = _duck_expression(windows)
    filters = [
        f"[0:a]asetpts=PTS-STARTPTS,volume={bed_gain:.6f},volume='{duck_expr}':eval=frame[bed]"
    ]
    for index, audio in enumerate(audio_inputs, start=1):
        filters.append(
            f"[{index}:a]asetpts=PTS-STARTPTS,adelay={audio['delay_ms']}|{audio['delay_ms']},"
            f"volume={narration_gain:.6f}[n{index}]"
        )
    labels = "[bed]" + "".join(f"[n{i}]" for i in range(1, len(audio_inputs) + 1))
    filters.append(
        f"{labels}amix=inputs={len(audio_inputs) + 1}:duration=first:normalize=0[voiceout]"
    )
    return {
        "schema_version": _SCHEMA_VERSION,
        "project_id": project_id,
        "profile_id": voice["profile_id"],
        "owner_id": "<redacted>",
        "base_render": str(base),
        "base_render_sha256": sha256_file(base),
        "audio_inputs": audio_inputs,
        "duck_windows": [
            {"start": round(s, 3), "end": round(e, 3), "factor": round(f, 6)} for s, e, f in windows
        ],
        "filter_complex": ";".join(filters),
        "mix_config": mix_config,
        "note": (
            "Music levels belong to the base EDL and are not re-rendered here; the original "
            "audio bed is never modified on disk."
        ),
    }


def render_voice_mix(
    ws,
    project_id: str,
    edl: dict,
    *,
    config: dict | None = None,
    source_render: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Render the voice mix as a new file and verify it with real FFprobe."""
    config = config or load_config(ws)
    plan = build_mix_plan(ws, edl, source_render=source_render)
    if str(plan["project_id"]) != project_id:
        raise ValidationError("EDL project_id does not match the requested project")
    renders = project_dir(ws, project_id) / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    existing = [
        int(m.group(1))
        for p in renders.glob("voice_v*_*.mp4")
        if (m := re.match(r"voice_v(\d{3})_", p.name))
    ]
    version = max(existing, default=0) + 1
    output = renders / f"voice_v{version:03d}_mix.mp4"
    manifest_path = renders / f"voice_v{version:03d}_mix_manifest.json"

    base_info = probe(plan["base_render"], config)
    fmt = base_info.get("format") or {}
    video = next((s for s in base_info.get("streams", []) if s.get("codec_type") == "video"), {})
    fps = float(video.get("r_frame_rate", "0/1").split("/")[0] or 0) or 25.0
    cmd = [
        ffmpeg_binary(config, "ffmpeg"),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        plan["base_render"],
    ]
    for audio in plan["audio_inputs"]:
        cmd += ["-i", audio["path"]]
    cmd += [
        "-filter_complex",
        plan["filter_complex"],
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-map",
        "[voiceout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-r",
        str(int(fps) if fps.is_integer() else round(fps, 3)),
        "-movflags",
        "+faststart",
        str(output),
    ]

    ffprobe_result = None
    if not dry_run:
        result = ssub.run(
            cmd,
            timeout=float(config.get("limits", {}).get("subprocess_timeout_s", 600)),
            max_output=4_000_000,
        )
        if result.exit_code != 0:
            output.unlink(missing_ok=True)
            from creator_core.cli.errors import ProviderError

            raise ProviderError(f"voice mix render failed: {result.stderr[-1200:]}")
        ffprobe_result = probe(output, config)
        fmt2 = ffprobe_result.get("format") or {}
        if float(fmt2.get("duration") or 0) <= 0:
            raise ProviderError("voice mix render produced no measurable duration")

    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "command": "voice mix",
        "ok": not dry_run,
        "project_id": project_id,
        "profile_id": plan["profile_id"],
        "version": version,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "output": None if dry_run else str(output),
        "dry_run": bool(dry_run),
        "source_render": plan["base_render"],
        "source_render_sha256": plan["base_render_sha256"],
        "source_duration_seconds": float(fmt.get("duration") or 0.0),
        "audio_inputs": plan["audio_inputs"],
        "duck_windows": plan["duck_windows"],
        "filter_complex": plan["filter_complex"],
        "ffmpeg_args": cmd,
        "ffmpeg_output": None if dry_run else str(output),
        "note": plan["note"],
    }
    atomic_write_json(manifest_path, manifest)
    return {
        "ok": not dry_run,
        "command": "voice mix",
        "project_id": project_id,
        "version": version,
        "output": None if dry_run else str(output),
        "duration_seconds": float((ffprobe_result or {}).get("format", {}).get("duration") or 0.0),
        "audio_segments": len(plan["audio_inputs"]),
        "duck_windows": plan["duck_windows"],
        "manifest": str(manifest_path),
        "dry_run": bool(dry_run),
        "note": plan["note"],
    }
