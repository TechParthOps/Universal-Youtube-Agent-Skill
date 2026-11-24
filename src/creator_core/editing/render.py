"""Render an EDL to MP4 through safe subprocess calls; write reproducible manifests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from creator_core.cli.errors import ValidationError, WorkspaceIOError
from creator_core.editing.edl import validate_edl
from creator_core.editing.graph import build_graph
from creator_core.media.ingest import ffmpeg_binary
from creator_core.qa.checks import run_qa
from creator_core.security import subprocess as ssub
from creator_core.workspace.atomic import atomic_write_json
from creator_core.workspace.layout import WorkspacePaths, load_config


def _reserve_render(renders_dir: Path, version: int) -> tuple[int, Path]:
    """Exclusively reserve an output filename so concurrent renders cannot overwrite."""
    renders_dir.mkdir(parents=True, exist_ok=True)
    for number in range(1, 10_000):
        output = renders_dir / f"edit_v{version:03d}_r{number:03d}.mp4"
        try:
            with open(output, "xb"):
                pass
            return number, output
        except FileExistsError:
            continue
    raise WorkspaceIOError("could not reserve a render output filename")


def _describe_exit(code: int) -> str:
    """Distinguish a normal failure from an external signal kill (e.g. container OOM)."""
    if code >= 0:
        return f"exit {code}"
    signal_number = -code
    names = {
        9: "SIGKILL (usually an OOM killer or external watchdog)",
        15: "SIGTERM",
        11: "SIGSEGV",
        6: "SIGABRT",
    }
    name = names.get(signal_number, f"signal {signal_number}")
    return f"exit {code} ({name})"


def _cleanup_caption(project: Path, caption_file: str | None) -> None:
    """Remove a generated caption file only if no successful render still references it."""
    if not caption_file:
        return
    for manifest in (project / "renders").glob("*_manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("caption_file") == caption_file:
            return
    Path(caption_file).unlink(missing_ok=True)


def render(
    ws: WorkspacePaths, edl: dict, *, project: Path, version: int, config: dict | None = None
) -> dict:
    """Render the EDL, run QA, and persist the manifest. Returns a JSON-safe payload."""
    validate_edl(edl)
    config = config or load_config(ws)
    limits = config.get("limits") or {}
    threads = max(1, min(8, int(limits.get("render_threads", 2) or 2)))
    max_output_mb = float(limits.get("max_output_mb", 64))
    if max_output_mb <= 0:
        raise ValidationError("limits.max_output_mb must be positive")
    project.mkdir(parents=True, exist_ok=True)
    graph = build_graph(edl, ws, project=project, config=config)
    renders_dir = project / "renders"
    number, output = _reserve_render(renders_dir, version)
    qa_path = renders_dir / f"edit_v{version:03d}_r{number:03d}_qa.json"
    manifest_path = renders_dir / f"edit_v{version:03d}_r{number:03d}_manifest.json"

    cmd = [
        ffmpeg_binary(config, "ffmpeg"),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-filter_threads",
        str(threads),
        "-filter_complex_threads",
        str(threads),
    ]
    for source in graph["inputs"]:
        cmd += ["-threads", str(threads), "-i", source]
    cmd += [
        "-filter_complex",
        graph["filter_complex"],
        "-map",
        graph["video_map"],
        "-map",
        graph["audio_map"],
        "-threads",
        str(threads),
        "-c:v",
        str(edl["render"]["video_codec"]),
        "-preset",
        str(edl["render"].get("preset", "veryfast")),
        "-crf",
        str(int(edl["render"]["crf"])),
        "-pix_fmt",
        str(edl["render"].get("pixel_format", "yuv420p")),
        "-c:a",
        str(edl["render"]["audio_codec"]),
        "-b:a",
        str(edl["render"]["audio_bitrate"]),
        "-movflags",
        "+faststart",
    ]
    if edl["tracks"].get("music"):
        cmd.append("-shortest")
    cmd.append(str(output))

    timeout = float(limits.get("subprocess_timeout_s", 600))
    try:
        result = ssub.run(cmd, timeout=timeout, cwd=graph["cwd"], max_output=16_000_000)
        if result.exit_code != 0 or not output.is_file() or output.stat().st_size == 0:
            killed = result.exit_code < 0
            raise ValidationError(
                f"ffmpeg render failed ({_describe_exit(result.exit_code)}): "
                f"{result.stderr[-800:]}",
                hint=(
                    "the renderer was killed externally; reduce "
                    "limits.render_threads or raise the container memory limit"
                    if killed
                    else "check the EDL and source media; originals were not modified"
                ),
            )
        output_bytes = output.stat().st_size
        max_bytes = int(max_output_mb * 1024 * 1024)
        if output_bytes > max_bytes:
            raise ValidationError(
                f"rendered output is {output_bytes / 1048576:.1f} MiB, above "
                f"limits.max_output_mb={max_output_mb:g} MiB",
                hint="lower the EDL crf/resolution or raise limits.max_output_mb explicitly; "
                "quality was not silently reduced",
            )
    except Exception:
        output.unlink(missing_ok=True)  # failed renders are never left as valid-looking artifacts
        _cleanup_caption(project, graph.get("caption_file"))
        raise

    try:
        qa = run_qa(
            output,
            edl,
            config=config,
            expected_duration=graph["expected_duration"],
            report_path=qa_path,
        )
    except Exception:
        output.unlink(missing_ok=True)
        qa_path.unlink(missing_ok=True)
        _cleanup_caption(project, graph.get("caption_file"))
        raise
    manifest = {
        "schema_version": 1,
        "project_id": edl["project_id"],
        "timeline_version": version,
        "render_number": number,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "output": str(output),
        "output_bytes": output_bytes,
        "max_output_bytes": int(max_output_mb * 1024 * 1024),
        "render_threads": threads,
        "limitations": graph.get("limitations", []),
        "sources": [
            {"id": s["id"], "path": s["path"], "sha256": s["sha256"]} for s in edl["sources"]
        ],
        "render_settings": edl["render"],
        "filter_complex": graph["filter_complex"],
        "ffmpeg_args": cmd,
        "expected_duration": graph["expected_duration"],
        "qa_report": str(qa_path),
        "qa_ok": qa["ok"],
        "caption_file": graph["caption_file"],
    }
    atomic_write_json(manifest_path, manifest)
    return {
        "command": "edit render",
        "ok": True,
        "project_id": edl["project_id"],
        "timeline_version": version,
        "render_number": number,
        "output": str(output),
        "output_bytes": output_bytes,
        "duration_seconds": qa["duration_seconds"],
        "qa_ok": qa["ok"],
        "qa_report": str(qa_path),
        "manifest": str(manifest_path),
        "needs_approval": qa["needs_approval"],
    }


def render_history(project: Path) -> list[dict]:
    out = []
    for manifest in sorted((project / "renders").glob("*_manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "render_number": data.get("render_number"),
                "timeline_version": data.get("timeline_version"),
                "output": data.get("output"),
                "qa_ok": data.get("qa_ok"),
                "manifest": str(manifest),
            }
        )
    return sorted(out, key=lambda r: r["render_number"] or 0)
