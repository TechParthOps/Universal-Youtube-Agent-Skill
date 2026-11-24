"""Shorts generation and approval workflow (Phase 7).

Generation produces local drafts only. It never calls a YouTube API, never publishes, and keeps
every finding auditable. Approval is a separate, explicitly authorized action; the publishing
flow re-checks it (and the parent video state) immediately before upload and publish.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError
from creator_core.media.analyze import analyze
from creator_core.media.ingest import ffmpeg_binary, inspect, register_source, sha256_file
from creator_core.profiles.profile import ProfileManager, default_profile
from creator_core.qa.checks import run_qa
from creator_core.security import subprocess as ssub
from creator_core.security.approval import require_approval
from creator_core.security.audit import AuditLog
from creator_core.shorts.candidates import rank_candidates
from creator_core.shorts.captions import captions_from_transcript, write_short_captions
from creator_core.shorts.hooks import generate_hooks
from creator_core.shorts.reframe import OUT_H, OUT_W, plan_reframe
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import load_config, project_dir

SHORT_ID_RE = re.compile(r"^short_\d{3}$")
FPS = 30.0
PUBLISH_GATE = {
    "creator_approved": False,
    "parent_must_be_published": True,
    "fresh_upload_token_required": True,
    "note": "Publishing re-checks approval and parent state before upload and publish.",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load_profile(ws) -> dict:
    try:
        return ProfileManager(ws, AuditLog(ws.root)).load()
    except ValidationError:
        return default_profile()  # used for relevance only; never persisted without consent


def _load_analysis(ws, project: Path, info: dict, *, transcribe: bool, config: dict) -> dict:
    cached = project / "analysis" / f"{info['source_id']}.json"
    if cached.is_file():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return analyze(
        Path(info["path"]), ws, project_id=project.name, transcribe=transcribe, config=config
    )


def _reserve_output(directory: Path, short_id: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1000):
        output = directory / f"preview_r{number:03d}.mp4"
        try:
            with open(output, "xb"):
                pass
            return output
        except FileExistsError:
            continue
    from creator_core.cli.errors import WorkspaceIOError

    raise WorkspaceIOError(f"could not reserve an output for {short_id}")


def _build_command(
    video_filter: str,
    audio_filter: str,
    *,
    source: Path,
    start: float,
    duration: float,
    silent: bool,
    config: dict,
) -> list[str]:
    """Input-seek to the window, then cap the *output* so both streams stay aligned."""
    cmd = [
        ffmpeg_binary(config, "ffmpeg"),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
    ]
    if silent:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += [
        "-t",
        f"{duration:.3f}",
        "-filter_complex",
        f"{video_filter};{audio_filter}",
        "-map",
        "[vcap]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        "-r",
        f"{FPS:g}",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
    ]
    return cmd


def generate(
    ws,
    source: Path | str,
    *,
    project_id: str | None = None,
    config: dict | None = None,
    target_duration: float = 30.0,
    limit: int = 1,
    focus_mode: str = "auto",
    provider_enabled: bool = True,
    transcribe: bool = False,
) -> dict:
    """Rank candidate moments, render each into a 9:16 draft, and write QA + timeline files."""
    config = config or load_config(ws)
    audit = AuditLog(ws.root)
    info = inspect(source, ws, config)
    registration = register_source(ws, info, project_id)
    project = Path(registration["project_dir"])
    report = _load_analysis(ws, project, info, transcribe=transcribe, config=config)
    profile = _load_profile(ws)
    ranking = rank_candidates(
        report,
        target_duration=target_duration,
        max_candidates=limit,
        profile=profile,
        source_duration=info["duration_seconds"],
        source_path=info["path"],
    )
    shorts_root = project / "shorts"
    shorts_root.mkdir(parents=True, exist_ok=True)
    rendered: list[dict] = []
    video = info.get("video") or {}
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if width <= 0 or height <= 0:
        raise ValidationError("source has no readable video stream")

    for candidate in ranking["candidates"]:
        short_id = candidate["short_id"]
        short_dir = shorts_root / short_id
        short_dir.mkdir(parents=True, exist_ok=True)
        start, end = float(candidate["start"]), float(candidate["end"])
        duration = round(end - start, 3)
        if duration < 1.0:
            continue

        hooks = generate_hooks(candidate, profile, config, enabled=provider_enabled, count=2)
        first_hook = hooks["hooks"][0]["value"] if hooks["hooks"] else "Short draft"
        plan = plan_reframe(
            Path(info["path"]),
            start=start,
            duration=duration,
            width=width,
            height=height,
            ws=ws,
            config=config,
            focus_mode=focus_mode,
        )

        cues = captions_from_transcript(
            candidate.get("transcript_segments") or [], offset=start, end=duration
        )
        caption_source = "transcript"
        if not cues:
            cues = [{"start": 0.2, "end": round(min(3.5, duration - 0.15), 3), "text": first_hook}]
            caption_source = "hook template (no transcript available)"
        caption_file = write_short_captions(short_dir, cues)

        reframe_filter = plan["filter"].removesuffix("[vout]")
        vf = (
            f"[0:v]trim=start=0:end={duration:.3f},setpts=PTS-STARTPTS[fbase];"
            f"[fbase]{reframe_filter}[vout]"
        )
        if caption_file is not None:
            vf += f";[vout]subtitles=filename={caption_file.name}[vcap]"
        else:
            vf += ";[vout]null[vcap]"
        silent = not bool(info.get("audio_streams"))
        audio_input = "1:a" if silent else "0:a"
        af = (
            f"[{audio_input}]atrim=start=0:end={duration:.3f},asetpts=PTS-STARTPTS,"
            f"alimiter=limit=0.95,aresample=48000[aout]"
        )
        cmd = _build_command(
            vf,
            af,
            source=Path(info["path"]),
            start=start,
            duration=duration,
            silent=silent,
            config=config,
        )
        output = _reserve_output(short_dir, short_id)
        cmd.append(str(output))
        try:
            result = ssub.run(
                cmd,
                timeout=float((config.get("limits") or {}).get("subprocess_timeout_s", 600)),
                cwd=str(short_dir),
                max_output=16_000_000,
            )
            if result.exit_code != 0 or not output.is_file() or output.stat().st_size == 0:
                raise ValidationError(
                    f"Short render failed for {short_id}: {result.stderr[-700:]}",
                    hint="check the source and focus plan; the original was not changed",
                )
        except Exception:
            output.unlink(missing_ok=True)
            raise

        qa_path = short_dir / f"{output.stem}_qa.json"
        qa = run_qa(
            output,
            {"render": {"width": OUT_W, "height": OUT_H, "fps": FPS}, "tracks": {"captions": cues}},
            config=config,
            expected_duration=duration,
            report_path=qa_path,
        )
        timeline = {
            "schema_version": 1,
            "short_id": short_id,
            "project_id": project.name,
            "created_at": _now(),
            "source": {
                "path": info["path"],
                "sha256": info["sha256"],
                "source_id": info["source_id"],
            },
            "window": {
                "start": start,
                "end": end,
                "duration": duration,
                "target_duration": float(target_duration),
            },
            "candidate": {k: v for k, v in candidate.items() if k != "transcript_segments"},
            "hooks": hooks,
            "reframe": plan,
            "captions": {
                "file": caption_file.name if caption_file else None,
                "source": caption_source,
                "cues": cues,
            },
            "render": {
                "output": str(output),
                "width": OUT_W,
                "height": OUT_H,
                "fps": FPS,
                "filter_complex": f"{vf};{af}",
                "sha256": sha256_file(output),
            },
            "qa": {"report": str(qa_path), "ok": qa["ok"], "needs_approval": qa["needs_approval"]},
            "status": "draft",
            "approval": None,
            "publish_gate": dict(PUBLISH_GATE),
            "limitations": [
                "Candidate scores are heuristics, not virality predictions.",
                "Creator must review the rendered preview before approval.",
            ],
        }
        atomic_write_json(short_dir / "timeline.json", timeline)
        atomic_write_json(
            short_dir / "manifest.json",
            {
                "schema_version": 1,
                "short_id": short_id,
                "ffmpeg_args": cmd,
                "output": str(output),
                "qa_report": str(qa_path),
                "created_at": _now(),
            },
        )
        audit.log(
            "shorts.generated",
            project_id=project.name,
            short_id=short_id,
            focus_mode=plan["mode"],
            qa_ok=qa["ok"],
        )
        rendered.append(
            {
                "short_id": short_id,
                "output": str(output),
                "duration_seconds": qa["duration_seconds"],
                "width": OUT_W,
                "height": OUT_H,
                "qa_ok": qa["ok"],
                "qa_report": str(qa_path),
                "timeline": str(short_dir / "timeline.json"),
                "focus_mode": plan["mode"],
                "hook": first_hook,
                "hook_provider": hooks["provider"],
                "captions_burned": caption_file is not None,
                "score": candidate["score"],
                "status": "draft",
            }
        )

    payload = {
        "command": "shorts generate",
        "ok": True,
        "project_id": project.name,
        "source": info["path"],
        "analysis_cached": (project / "analysis" / f"{info['source_id']}.json").is_file(),
        "candidate_count": ranking["count"],
        "count": len(rendered),
        "shorts": rendered,
        "candidates": ranking["candidates"],
        "needs_approval": bool(rendered),
        "approval_hint": "review the preview, then `creator shorts review <project> --short "
        "<id> --approve`",
        "limitations": ranking["limitations"],
    }
    return payload


def approval_payload(project_id: str, short_id: str, timeline_path: Path, data: dict) -> dict:
    """Canonical request bound to the exact draft being approved (S-01).

    Exposed so a pre-issued `--approval-token` can be built from the same deterministic inputs;
    any edit to the timeline (including its rendering/QA fields) invalidates the token.
    """
    return {
        "project_id": project_id,
        "short_id": short_id,
        "timeline_sha256": hashlib.sha256(timeline_path.read_bytes()).hexdigest(),
        "status": data.get("status"),
        "window": data.get("window"),
        "qa": data.get("qa"),
        "render": data.get("render"),
        "candidate": data.get("candidate"),
    }


def review(
    ws,
    project_id: str,
    *,
    short_id: str | None = None,
    approval_token: str | None = None,
    generate_approval: bool = False,
    config: dict | None = None,
) -> dict:
    """List drafts, or record explicit creator approval for one draft Short."""
    project = project_dir(ws, project_id)
    shorts_root = project / "shorts"
    if not shorts_root.is_dir():
        raise ValidationError(
            f"no Shorts found for project {project_id}",
            hint="run `creator shorts generate <source> --project <id>` first",
        )
    timelines = sorted(shorts_root.glob("short_*/timeline.json"))
    items: list[dict] = []
    for path in timelines:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items.append(
            {
                "short_id": data.get("short_id"),
                "status": data.get("status"),
                "duration_seconds": (data.get("window") or {}).get("duration"),
                "qa_ok": (data.get("qa") or {}).get("ok"),
                "output": (data.get("render") or {}).get("output"),
                "score": (data.get("candidate") or {}).get("score"),
                "timeline": str(path),
            }
        )
    if short_id is None:
        return {
            "command": "shorts review",
            "ok": True,
            "project_id": project_id,
            "count": len(items),
            "shorts": items,
            "needs_approval": any(i["status"] == "draft" for i in items),
        }
    if not SHORT_ID_RE.fullmatch(short_id):
        raise ValidationError(f"invalid short id: {short_id}", hint="use short_001 style ids")
    timeline_path = shorts_root / short_id / "timeline.json"
    if not timeline_path.is_file():
        raise ValidationError(f"short {short_id} not found in project {project_id}")
    data = json.loads(timeline_path.read_text(encoding="utf-8"))

    target = f"{project_id}/{short_id}"
    payload = approval_payload(project_id, short_id, timeline_path, data)
    approval = require_approval(
        ws,
        "shorts.approve",
        target,
        approval_token,
        generate=generate_approval,
        payload=payload,
        hint="rerun with --approve after reviewing this exact draft Short",
    )
    data["status"] = "approved"
    data["approval"] = {
        "approved_at": _now(),
        "by": "creator",
        "token_fingerprint": approval["fingerprint"],
        "token_expires": approval["expires"],
    }
    data["publish_gate"] = {**PUBLISH_GATE, "creator_approved": True}
    atomic_write_json(timeline_path, data)
    AuditLog(ws.root).log(
        "shorts.approved",
        project_id=project_id,
        short_id=short_id,
        token_fingerprint=data["approval"]["token_fingerprint"],
    )
    return {
        "command": "shorts review",
        "ok": True,
        "project_id": project_id,
        "short_id": short_id,
        "status": "approved",
        "publish_gate": data["publish_gate"],
        "timeline": str(timeline_path),
        "needs_approval": False,
    }
