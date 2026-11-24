"""FFmpeg filter-graph builder from an EDL. No shell strings; every path is validated."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.editing.edl import clip_duration, edl_duration, validate_edl
from creator_core.media.ingest import sha256_file, validate_media_path
from creator_core.workspace.layout import WorkspacePaths


def _f(value: float) -> str:
    return f"{float(value):.3f}"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    rest = seconds - hours * 3600 - minutes * 60
    return f"{hours}:{minutes:02d}:{rest:05.2f}"


def _escape_ass(text: str) -> str:
    return (
        sanitize_untrusted_plain(text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    )


def sanitize_untrusted_plain(text: str) -> str:
    from creator_core.media.injection import sanitize_untrusted

    return sanitize_untrusted(text).replace("\n", " ").replace("\r", " ")


def write_captions(edl: dict, project: Path, *, width: int, height: int) -> Path | None:
    cues = (edl.get("tracks") or {}).get("captions") or []
    if not cues:
        return None
    digest = hashlib.sha256(
        json.dumps(cues, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    path = project / f"captions_{digest}.ass"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            "Style: Default,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            "-1,0,0,0,100,100,0,0,1,2,1,2,20,20,40,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        end = float(cue["end"])
        start = float(cue["start"])
        text = _escape_ass(str(cue.get("text", "")))
        # ASS uses \N for hard line breaks; our captions are single-line by construction.
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,40,,{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _scaled_video(label: str, out_label: str, render: dict) -> str:
    w, h, fps = int(render["width"]), int(render["height"]), float(render["fps"])
    return (
        f"{label}scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={_f(fps)},"
        f"format={render.get('pixel_format', 'yuv420p')}[{out_label}]"
    )


def _zoom_chain(label: str, effects: list[dict], out_label: str, render: dict) -> str:
    fps = float(render["fps"])
    w, h = int(render["width"]), int(render["height"])
    zoom = effects[0]
    factor = min(2.0, max(1.01, float(zoom.get("factor", 1.15))))
    start = max(0.0, float(zoom.get("start_at", 0.0)))
    end = max(start + 0.1, float(zoom.get("end_at", start + 1.0)))
    a, b = start * fps, end * fps
    expr = (
        f"if(lt(on,{_f(a)}),1,if(lt(on,{_f(b)}),"
        f"1+({_f(factor)}-1)*(on-{_f(a)})/({_f(b)}-{_f(a)}),{_f(factor)}))"
    )
    return (
        f"{label}zoompan=z='{expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={w}x{h}:fps={_f(fps)}[{out_label}]"
    )


def _silent_track(project: Path, duration: float, config: dict) -> Path:
    """Create a bounded silent WAV so audio-less sources can still render predictably."""
    from creator_core.media.ingest import ffmpeg_binary
    from creator_core.security import subprocess as ssub

    seconds = max(1.0, min(3600.0, float(duration) + 1.0))
    path = project / f"silence_{seconds:.2f}s.wav"
    if not path.is_file():
        cmd = [
            ffmpeg_binary(config, "ffmpeg"),
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            f"{seconds:.3f}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
        result = ssub.run(cmd, timeout=120.0, max_output=1_000_000)
        if result.exit_code != 0:
            raise ValidationError(f"could not create silent audio track: {result.stderr[-300:]}")
    return path


def build_graph(
    edl: dict, ws: WorkspacePaths, *, project: Path, config: dict | None = None
) -> dict:
    """Return FFmpeg args components for rendering this EDL."""
    validate_edl(edl)
    render = edl["render"]
    from creator_core.workspace.layout import load_config

    config = config or load_config(ws)
    sources = {s["id"]: i for i, s in enumerate(edl["sources"])}
    media_paths: list[Path] = []
    for src in edl["sources"]:
        path = validate_media_path(src["path"], ws)
        # Never render a source that changed after analysis: the EDL's cuts refer to
        # exact source times, so a replaced file would silently produce the wrong edit.
        actual = sha256_file(path)
        if actual != src["sha256"]:
            raise ValidationError(
                f"source {src['id']} changed since the EDL was created: "
                f"expected {src['sha256'][:12]}, got {actual[:12]}",
                hint="re-run `creator media analyze` and `creator edit new` for this file",
            )
        media_paths.append(path)

    inputs: list[str] = []
    audio_input: dict[int, int] = {}
    total_duration = edl_duration(edl)
    for index, src in enumerate(edl["sources"]):
        inputs.append(str(media_paths[index]))
        if src.get("audio_streams"):
            audio_input[index] = len(inputs) - 1
        else:
            inputs.append(str(_silent_track(project, total_duration, config)))
            audio_input[index] = len(inputs) - 1

    filters: list[str] = []
    video_clips = edl["tracks"]["video"]
    audio_clips = edl["tracks"]["audio"]
    if len(audio_clips) != len(video_clips):
        raise ValidationError("audio and video clips must be paired 1:1")

    vlabels: list[str] = []
    alabels: list[str] = []
    unsupported_effects: set[str] = set()
    for i, (vclip, aclip) in enumerate(zip(video_clips, audio_clips), start=1):
        src_idx = sources[vclip["source"]]
        sin, sout = _f(vclip["source_in"]), _f(vclip["source_out"])
        speed = float(vclip.get("speed", 1.0) or 1.0)
        if not 0.5 <= speed <= 2.0:
            raise ValidationError("clip speed must be between 0.5 and 2.0 for audio compatibility")

        base_v = f"v{i}base"
        filters.append(f"[{src_idx}:v]trim=start={sin}:end={sout},setpts=PTS-STARTPTS[{base_v}]")
        if speed != 1.0:
            sped_v = f"v{i}spd"
            filters.append(f"[{base_v}]setpts=PTS/{_f(speed)}[{sped_v}]")
            base_v = sped_v
        effects = [e for e in vclip.get("effects") or [] if isinstance(e, dict)]
        zooms = [e for e in effects if e.get("type") == "zoom"]
        if len(zooms) > 1:
            raise ValidationError("only one zoom effect per video clip is supported in EDL v1")
        unsupported_effects.update(
            str(e["type"]) for e in effects if e.get("type") and e["type"] != "zoom"
        )
        final_v = f"v{i}final"
        if zooms:
            # zoompan already emits exactly the render size and frame rate. Re-running the
            # full scale/pad/fps chain afterwards forces a second per-frame scaling pass and
            # is ~60x slower (measured: 6.88s -> 0.11s for a 6s 320x180 clip). Only the
            # pixel format and sample aspect ratio still need normalising here.
            zoomed = f"v{i}zoom"
            filters.append(_zoom_chain(f"[{base_v}]", zooms, zoomed, render))
            filters.append(
                f"[{zoomed}]setsar=1,format={render.get('pixel_format', 'yuv420p')}[{final_v}]"
            )
        else:
            filters.append(_scaled_video(f"[{base_v}]", final_v, render))
        vlabels.append(f"[{final_v}]")

        base_a = f"a{i}base"
        a_idx = audio_input[src_idx]
        filters.append(f"[{a_idx}:a]atrim=start={sin}:end={sout},asetpts=PTS-STARTPTS[{base_a}]")
        if speed != 1.0:
            sped_a = f"a{i}spd"
            filters.append(f"[{base_a}]atempo={_f(speed)}[{sped_a}]")
            base_a = sped_a
        gain = float(aclip.get("gain_db", 0.0) or 0.0)
        final_a = f"a{i}final"
        filters.append(
            f"[{base_a}]volume={_f(gain)}dB,aformat=sample_fmts=fltp,aresample=48000[{final_a}]"
        )
        alabels.append(f"[{final_a}]")

    transitions = edl.get("transitions") or []
    fades = [t for t in transitions if t.get("type", "cut") != "cut"]
    if fades and len(fades) != len(vlabels) - 1:
        raise ValidationError("fade transitions must be specified for every clip boundary")

    if fades:
        durations = [clip_duration(c) for c in video_clips]
        vcur, acur = vlabels[0], alabels[0]
        accum = durations[0]
        for i, trans in enumerate(fades, start=1):
            fdur = min(1.0, max(0.1, float(trans.get("duration", 0.4))))
            kind = trans.get("type", "fade")
            offset = accum - fdur
            if offset < 0:
                raise ValidationError("transition is longer than the preceding clip")
            vout, aout = f"xf{i}", f"af{i}"
            filters.append(
                f"{vcur}{vlabels[i]}xfade=transition={kind}:duration={_f(fdur)}:"
                f"offset={_f(offset)}[{vout}]"
            )
            filters.append(f"{acur}{alabels[i]}acrossfade=d={_f(fdur)}:c1=tri:c2=tri[{aout}]")
            vcur, acur = f"[{vout}]", f"[{aout}]"
            accum += durations[i] - fdur
        vfinal, afinal = vcur, acur
    else:
        vfinal, afinal = "[catv]", "[cata]"
        interleaved = [label for pair in zip(vlabels, alabels) for label in pair]
        filters.append("".join(interleaved) + f"concat=n={len(vlabels)}:v=1:a=1[catv][cata]")

    caption_file = write_captions(
        edl, project, width=int(render["width"]), height=int(render["height"])
    )
    if caption_file is not None:
        # Relative name: ffmpeg runs with cwd=project, so no drive-letter escaping is needed.
        # The hash suffix prevents concurrent EDLs in one project from clobbering each other.
        vout = "[vout]"
        filters.append(f"{vfinal}subtitles=filename={caption_file.name}[vout]")
    else:
        vout = "[vout]"
        if vfinal != vout:
            filters.append(f"{vfinal}null[vout]")

    music = edl["tracks"].get("music") or []
    if len(music) > 1:
        raise ValidationError("only one music bed is supported in EDL v1")
    if music:
        bed = music[0]
        # Music beds are user-supplied EDL content: confine them like any other media,
        # including when the EDL supplies an absolute path (MEDIUM audit fix).
        asset = validate_media_path(bed["asset"], ws)
        if not asset.is_file():
            raise ValidationError(f"music asset not found: {asset}")
        inputs.append(str(asset))
        m_idx = len(inputs) - 1
        start = max(0.0, float(bed.get("start", 0.0)))
        end = max(start + 0.1, float(bed.get("end", edl_duration(edl))))
        gain = float(bed.get("gain_db", -18.0))
        filters.append(
            f"[{m_idx}:a]atrim=start={_f(start)}:end={_f(end)},asetpts=PTS-STARTPTS,"
            f"adelay={int(start * 1000)}|{int(start * 1000)},volume={_f(gain)}dB,apad[music]"
        )
        duck = float(bed.get("duck_to_db", -28.0))
        duck_threshold = 10 ** (duck / 20.0)
        filters.append(
            f"[music]{afinal}sidechaincompress=threshold={duck_threshold:.4f}:"
            f"ratio=4:attack=20:release=250[ducked]"
        )
        filters.append(
            f"{afinal}[ducked]amix=inputs=2:duration=first:dropout_transition=0,"
            f"aresample=48000[aout]"
        )
    else:
        filters.append(f"{afinal}anull[aout]")

    return {
        "inputs": inputs,
        "filter_complex": ";".join(filters),
        "video_map": vout,
        "audio_map": "[aout]",
        "expected_duration": round(edl_duration(edl), 3),
        "caption_file": str(caption_file) if caption_file else None,
        "cwd": str(project),
        "output_fps": float(render["fps"]),
        "limitations": (
            (
                ["sources with multiple audio streams: only the first stream is used"]
                if any(len(s.get("audio_streams") or []) > 1 for s in edl["sources"])
                else []
            )
            + (
                [
                    f"unsupported effect type(s) were ignored: {', '.join(sorted(unsupported_effects))}"
                ]
                if unsupported_effects
                else []
            )
        ),
    }
