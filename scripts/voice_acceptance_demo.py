#!/usr/bin/env python3
"""Final VoiceDNA acceptance demo (Phase 11J) — local, no upload, no neural weights.

Runs the full additive chain on real synthetic media:

    registration → silent gameplay footage → grounded script → approval → narration
    → alignment → captions → mix → render → preview → segment regeneration
    → revision → QA → untouched originals → consent revocation/deletion → hybrid narration

On Windows a real `system_sapi` voice is used when available. Anywhere else (or when no SAPI
voice is installed) the deterministic `test_tone` provider is used and every generated segment is
labelled `mocked=true`; such output is NEVER suitable for publication.

    python scripts/voice_acceptance_demo.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from creator_core.editing.edl import derive, empty_edl, load_edl, save_edl
from creator_core.editing.render import render
from creator_core.media.analyze import analyze
from creator_core.media.ingest import ffmpeg_binary, inspect, probe, sha256_file
from creator_core.security import subprocess as ssub
from creator_core.security.approval import create_token, verify_token
from creator_core.security.audit import AuditLog
from creator_core.voice import (
    align_segments,
    apply_section_actions,
    assess_disclosure,
    build_voice_edl,
    build_voice_script,
    classify_sections,
    delete_profile,
    generate_narration,
    preview_data,
    regenerate_segment,
    register_voice,
    render_voice_mix,
    revoke_consent,
    run_voice_qa,
    segment_preview,
)
from creator_core.voice.consent import CONSENT_STATEMENT
from creator_core.workspace.layout import ensure_workspace, load_config, project_dir

PROJECT = "prj_voice_acceptance"
OWNER = "owner_a"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_reference(path: Path) -> Path:
    """20s clean speech-like reference tone (PCM WAV, no external model)."""
    import math
    import struct
    import wave

    rate = 44100
    period = int(rate * 0.4)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = bytearray()
        for i in range(rate * 20):
            value = (
                int(8000 * math.sin(2 * math.pi * 180 * i / rate))
                if (i % period) < int(period * 0.67)
                else 120
            )
            frames.extend(struct.pack("<h", max(-32768, min(32767, value))))
        wav.writeframes(bytes(frames))
    return path


def _write_gameplay(path: Path) -> Path:
    """12s silent three-scene video (real FFmpeg colour sources + luma cuts)."""
    cmd = [
        ffmpeg_binary(),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=320x180:r=15:d=4",
        "-f",
        "lavfi",
        "-i",
        "color=c=gray:s=320x180:r=15:d=4",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x180:r=15:d=4",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "3:a",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    result = ssub.run(cmd, timeout=180.0, max_output=4_000_000)
    if result.exit_code != 0:
        raise RuntimeError(f"FFmpeg gameplay fixture failed: {result.stderr[-1200:]}")
    return path


def run_demo(base: Path | None = None) -> dict:
    base = base or Path(tempfile.mkdtemp(prefix="voice_acceptance_"))
    ws = ensure_workspace(base, create=True)
    config = load_config(ws)
    audit = AuditLog(ws.root)
    trace: list[dict] = []

    def step(name: str, **detail) -> None:
        trace.append({"step": len(trace) + 1, "name": name, "at": _now(), **detail})

    # 1. Registration — prefer a real local voice; fall back to the labelled tone provider.
    provider_id, model_name, allow_test = "test_tone", "tone", True
    try:
        from creator_core.voice.providers import get_provider

        voices = get_provider("system_sapi").validate_model()["voices"]
        if voices:
            provider_id, model_name, allow_test = "system_sapi", voices[0]["name"], False
    except Exception:  # noqa: BLE001, S110 - SAPI absence is data; the demo falls back honestly
        pass

    reference = _write_reference(base / "reference.wav")
    profile_id = register_voice(
        ws,
        owner_id=OWNER,
        reference=reference,
        display_name="Acceptance creator",
        purposes=["narration"],
        model_provider=provider_id,
        model_name=model_name,
        model_version="1",
        supported_languages=["en"],
        consent_statement=CONSENT_STATEMENT,
    )["voice_profile_id"]
    step("registration", profile_id=profile_id, provider=provider_id, mocked_fallback=allow_test)

    # 2. Silent gameplay footage + real media analysis.
    video = _write_gameplay(base / "gameplay_silent.mp4")
    report = analyze(video, ws, project_id=PROJECT, config=config)
    source = inspect(video, ws, config)
    step(
        "silent_gameplay",
        source_sha256=source["sha256"],
        duration_seconds=report["source"]["duration_seconds"],
        detector_types=sorted(report.get("analysis", {}).keys()),
    )

    # 3. Footage-grounded script + 4. HMAC approval bound to the exact request.
    from creator_core.voice import load_profile

    profile = load_profile(ws, OWNER, profile_id)
    script = build_voice_script(report, "Describe the level transition", profile, max_segments=2)
    token = create_token(
        ws,
        "voice.generate",
        f"{OWNER}/{profile_id}/{PROJECT}",
        payload={"script_sha256": script["script_sha256"]},
    )
    claims = verify_token(
        ws,
        token,
        "voice.generate",
        f"{OWNER}/{profile_id}/{PROJECT}",
        payload={"script_sha256": script["script_sha256"]},
        consume=True,
    )
    step(
        "script_approval",
        script_sha256=script["script_sha256"],
        anchors=script["grounding_summary"]["anchor_count"],
        token_consumed=bool(claims),
    )

    # 5. Narration (consent checked inside generate_narration).
    for segment in script["segments"]:
        segment["status"] = "approved"
    generated = generate_narration(
        ws,
        OWNER,
        profile_id,
        script["segments"],
        purpose="narration",
        script_sha256=script["script_sha256"],
        project_id=PROJECT,
        allow_test_provider=allow_test,
    )
    step(
        "narration",
        segments=len(generated["segments"]),
        mocked=generated["mocked"],
        generation_id=generated["generation_id"],
    )

    # 6. Alignment + 7. captions via additive EDL composition.
    aligned = align_segments(ws, generated)
    base_edl = empty_edl(PROJECT, source, strategy="manual", concept="acceptance")
    duration = float(source["duration_seconds"])
    base_edl["tracks"]["video"].append(
        {
            "clip_id": "v1",
            "source": "src1",
            "source_in": 0.0,
            "source_out": round(duration, 3),
            "timeline_in": 0.0,
            "speed": 1.0,
            "effects": [],
        }
    )
    base_edl["tracks"]["audio"].append(
        {
            "clip_id": "a1",
            "source": "src1",
            "source_in": 0.0,
            "source_out": round(duration, 3),
            "timeline_in": 0.0,
            "gain_db": 0.0,
        }
    )
    save_edl(ws, base_edl)
    composed = build_voice_edl(load_edl(ws, PROJECT), aligned, owner_id=OWNER)
    composed_child = derive(composed, decisions=[{"op": "voice.compose"}])
    saved_composed = save_edl(ws, composed_child)
    step(
        "alignment_captions",
        timeline_version=saved_composed["version"],
        narration_captions=len(composed["voice"]["caption_cues"]),
        timing_label=aligned["label"],
    )

    # 8. Base render (existing editing stack).
    project_root = project_dir(ws, PROJECT, create=True)
    base_render = render(
        ws, composed, project=project_root, version=saved_composed["version"], config=config
    )
    renders = project_root / "renders"
    source_render_path = Path(
        base_render["output"]
        if isinstance(base_render, dict)
        else getattr(base_render, "output", "")
    )
    if not source_render_path.is_file():
        candidates = sorted(renders.glob("edit_v*_r*.mp4"))
        if not candidates:
            raise RuntimeError("base render did not produce an MP4")
        source_render_path = candidates[-1]
    base_render_sha = sha256_file(source_render_path)
    step("base_render", output=str(source_render_path), sha256=base_render_sha)

    # 9. Voice mix (non-destructive second pass).
    mix = render_voice_mix(
        ws, PROJECT, load_edl(ws, PROJECT), config=config, source_render=str(source_render_path)
    )
    mix_info = probe(mix["output"], config)
    step(
        "voice_mix",
        output=mix["output"],
        version=mix["version"],
        duration_seconds=float((mix_info.get("format") or {}).get("duration") or 0),
        duck_windows=len(mix["duck_windows"]),
    )

    # 10. Preview + 11. segment regeneration.
    preview = preview_data(ws, PROJECT)
    segment_id = preview["segments"][0]["segment_id"]
    detail = segment_preview(ws, PROJECT, segment_id)
    regenerated = regenerate_segment(
        ws,
        OWNER,
        profile_id,
        segment_id,
        text="A revised opening narration line.",
        allow_test_provider=allow_test,
    )
    step(
        "preview_regeneration",
        segment_id=segment_id,
        candidates=len(detail["candidates"]),
        previous_version=regenerated["previous_version"],
        new_version=regenerated["version"],
        preserved_siblings=regenerated["preserved_siblings"],
    )

    # 12. Revision via a new immutable EDL version + re-mix.
    current = load_edl(ws, PROJECT)
    revised = derive(
        current, decisions=[{"op": "voice.segment.revision", "segment_id": segment_id}]
    )
    for segment in revised["voice"]["segments"]:
        if segment["segment_id"] == segment_id:
            segment["status"] = "revised"
    saved_revision = save_edl(ws, revised)
    re_mix = render_voice_mix(
        ws, PROJECT, load_edl(ws, PROJECT), config=config, source_render=str(source_render_path)
    )
    step("revision", timeline_version=saved_revision["version"], remix_version=re_mix["version"])

    # 13. QA + disclosure assessment.
    qa = run_voice_qa(ws, PROJECT, load_edl(ws, PROJECT), re_mix)
    disclosure = assess_disclosure(load_edl(ws, PROJECT)["voice"])
    step(
        "qa", ok=qa["ok"], needs_approval=qa["needs_approval"], disclosure=disclosure["assessment"]
    )

    # 14. Untouched originals — source and base render hashes are unchanged.
    untouched = (
        sha256_file(video) == source["sha256"]
        and sha256_file(source_render_path) == base_render_sha
    )
    step("untouched_originals", verified=untouched)

    # 15. Consent revocation + deletion.
    revoke_consent(ws, OWNER, profile_id, audit=audit)
    deleted = delete_profile(ws, OWNER, profile_id, audit=audit)
    step(
        "consent_revocation_deletion",
        deleted=deleted.get("ok", True),
        removed=deleted.get("removed_paths", []),
    )

    # 16. Hybrid narration classification (reads real signals, non-destructive actions).
    sections = classify_sections(report)
    first = sections["sections"][0]["section_id"]
    actions = apply_section_actions(sections, {first: "silence"})
    step(
        "hybrid_narration",
        sections=len(sections["sections"]),
        types=sorted({s["type"] for s in sections["sections"]}),
        first_action=actions["sections"][0]["action"],
    )

    return {
        "ok": untouched,
        "command": "voice acceptance demo",
        "started_at": trace[0]["at"],
        "finished_at": _now(),
        "workspace": str(base),
        "provider": {"id": provider_id, "model": model_name, "mocked": allow_test},
        "publication_ready": (not generated["mocked"]) and qa["ok"],
        "note": (
            "Real local speech synthesis was used."
            if not allow_test
            else "MOCKED tone audio — not speech, not suitable for publication."
        ),
        "trace": trace,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="reuse a workspace directory (default: temp)")
    parser.add_argument("--json", action="store_true", help="emit the full JSON trace")
    args = parser.parse_args(argv)
    result = run_demo(Path(args.workspace) if args.workspace else None)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"VoiceDNA acceptance demo — ok={result['ok']}, provider={result['provider']['id']}")
        for item in result["trace"]:
            print(f"  {item['step']:>2}. {item['name']}")
        print(f"  {result['note']}")
        print(f"  publication_ready={result['publication_ready']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
