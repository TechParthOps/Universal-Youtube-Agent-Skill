"""`creator` — the portable CLI contract between AI agents and the Creator Intelligence Core.

Contract v1.0 (ARCHITECTURE.md §8):
- Every command supports --json for structured stdout output.
- Errors are JSON on stderr: {"error": {"code", "message", "hint"}} (secret-redacted).
- Exit codes: 0 ok · 1 unexpected · 2 validation · 3 missing dependency ·
  4 auth required · 5 approval required · 6 I/O · 7 provider error.

Parser design note: global flags use argparse.SUPPRESS defaults and are attached to the main
parser AND every subparser (fresh copies via _make_common). This makes `creator --json doctor`
and `creator doctor --json` both work, without subparser defaults clobbering main-parsed
values. Argument errors are emitted as contract JSON (exit 2) instead of argparse usage text.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from creator_core import CONTRACT_VERSION, __version__
from creator_core.cli.errors import CreatorError
from creator_core.security.secrets import redact_text


class ContractParser(argparse.ArgumentParser):
    """ArgumentParser whose errors follow the CLI JSON contract (exit code 2)."""

    def error(self, message: str):
        payload = json.dumps(
            {
                "error": {
                    "code": "validation_error",
                    "message": message,
                    "hint": "run `creator --help`",
                }
            },
            ensure_ascii=False,
        )
        sys.stderr.write(redact_text(payload) + "\n")
        raise SystemExit(2)


@dataclass(frozen=True)
class CliContext:
    json_mode: bool
    debug: bool
    workspace: Path


def emit_json(payload: dict) -> None:
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def emit_error(exc: CreatorError) -> None:
    payload = json.dumps(exc.payload(), ensure_ascii=False)
    sys.stderr.write(redact_text(payload) + "\n")


def _make_common() -> argparse.ArgumentParser:
    """Fresh global-flag parser (SUPPRESS defaults → absent flags never clobber)."""
    p = ContractParser(add_help=False)
    p.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit structured JSON output"
    )
    p.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show tracebacks for unexpected errors",
    )
    p.add_argument(
        "--workspace",
        type=Path,
        default=argparse.SUPPRESS,
        help="workspace base directory (default: current directory)",
    )
    return p


def build_parser() -> tuple[argparse.ArgumentParser, dict]:
    parser = ContractParser(
        prog="creator",
        description="Universal YouTube Creator Agent — creator automation toolkit.",
        parents=[_make_common()],
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"creator {__version__} (contract {CONTRACT_VERSION})",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    from creator_core.cli import (
        cmd_analytics,
        cmd_analyze,
        cmd_audit,
        cmd_captions,
        cmd_channel,
        cmd_doctor,
        cmd_edit,
        cmd_mcp,
        cmd_media,
        cmd_profile,
        cmd_release,
        cmd_research,
        cmd_shorts,
        cmd_voice,
        cmd_workspace,
        cmd_youtube,
    )

    p_profile = sub.add_parser(
        "profile", parents=[_make_common()], help="creator profile: init, show, set, changes, apply"
    )
    profile_sub = p_profile.add_subparsers(dest="profile_command", required=True)
    pp_init = profile_sub.add_parser("init", parents=[_make_common()], help="create the profile")
    pp_init.add_argument("--file", help="optional JSON file with initial profile fields")
    pp_init.add_argument("--force", action="store_true", help="overwrite an existing profile")
    pp_show = profile_sub.add_parser("show", parents=[_make_common()], help="show the profile")
    pp_show.add_argument("--version", type=int, help="show a numbered historical version")
    pp_set = profile_sub.add_parser("set", parents=[_make_common()], help="set profile fields")
    pp_set.add_argument("assignments", nargs="+", help="KEY=VALUE pairs (dotted paths allowed)")
    profile_sub.add_parser("changes", parents=[_make_common()], help="list pending memory changes")
    pp_apply = profile_sub.add_parser(
        "apply", parents=[_make_common()], help="apply a staged change (requires approval)"
    )
    pp_apply.add_argument("change_id")
    pp_apply.add_argument("--approve", action="store_true", help="explicitly approve now")
    pp_apply.add_argument("--approval-token", help="pre-issued HMAC approval token")

    p_channel = sub.add_parser(
        "channel",
        parents=[_make_common()],
        help="channel connect/status (fixture snapshots; live OAuth via youtube connect --live)",
    )
    channel_sub = p_channel.add_subparsers(dest="channel_command", required=True)
    pp_connect = channel_sub.add_parser("connect", parents=[_make_common()])
    pp_connect.add_argument("--from-file", help="import a local channel snapshot JSON fixture")
    channel_sub.add_parser("status", parents=[_make_common()])

    p_youtube = sub.add_parser(
        "youtube",
        parents=[_make_common()],
        help="publish to YouTube (stub-first; private-first approvals)",
    )
    youtube_sub = p_youtube.add_subparsers(dest="youtube_command", required=True)
    pp_yt_connect = youtube_sub.add_parser(
        "connect", parents=[_make_common()], help="connect OAuth (live) or the local stub"
    )
    pp_yt_connect.add_argument("--client-secrets", help="OAuth client JSON (required for --live)")
    pp_yt_connect.add_argument(
        "--live",
        action="store_true",
        help="use the real YouTube Data API instead of the offline stub",
    )
    pp_yt_status = youtube_sub.add_parser(
        "status", parents=[_make_common()], help="list publishing states and the quota ledger"
    )
    pp_yt_status.add_argument("--video-id", help="show only this video")
    youtube_sub.add_parser("quota", parents=[_make_common()], help="show the UTC quota ledger")
    pp_yt_upload = youtube_sub.add_parser(
        "upload", parents=[_make_common()], help="upload a render as private (never public)"
    )
    pp_yt_upload.add_argument("--project", required=True, help="project id")
    pp_yt_upload.add_argument("--source", required=True, help="rendered media file (read-only)")
    pp_yt_upload.add_argument("--title", required=True, help="video title (max 100 chars)")
    pp_yt_upload.add_argument("--description", default="", help="video description")
    pp_yt_upload.add_argument("--tags", nargs="+", help="video tags")
    pp_yt_upload.add_argument("--category", default="22", help="YouTube category id (default 22)")
    pp_yt_upload.add_argument(
        "--privacy",
        choices=("private", "unlisted"),
        default="private",
        help="initial privacy; public requires `youtube publish --approve`",
    )
    pp_yt_upload.add_argument("--publish-at", help="schedule time (ISO-8601, UTC; private only)")
    pp_yt_upload.add_argument("--thumbnail", help="thumbnail image path (max 2 MB)")
    pp_yt_upload.add_argument("--parent", help="parent video id when uploading an approved Short")
    pp_yt_upload.add_argument("--short", help="approved short id from `creator shorts review`")
    pp_yt_upload.add_argument(
        "--approve", action="store_true", help="explicitly approve now (fresh token issued)"
    )
    pp_yt_upload.add_argument("--approval-token", help="pre-issued HMAC approval token")
    pp_yt_upload.add_argument(
        "--live",
        action="store_true",
        help="use the real YouTube Data API instead of the offline stub",
    )
    pp_yt_verify = youtube_sub.add_parser(
        "verify", parents=[_make_common()], help="check remote processing status"
    )
    pp_yt_verify.add_argument("--video-id", required=True)
    pp_yt_publish = youtube_sub.add_parser(
        "publish", parents=[_make_common()], help="make a verified private video public"
    )
    pp_yt_publish.add_argument("--video-id", required=True)
    pp_yt_publish.add_argument(
        "--approve", action="store_true", help="explicitly approve now (fresh token issued)"
    )
    pp_yt_publish.add_argument("--approval-token", help="pre-issued HMAC approval token")
    pp_yt_publish.add_argument(
        "--live",
        action="store_true",
        help="use the real YouTube Data API instead of the offline stub",
    )
    pp_yt_thumb = youtube_sub.add_parser(
        "thumbnail", parents=[_make_common()], help="set a video thumbnail (requires approval)"
    )
    pp_yt_thumb.add_argument("--video-id", required=True)
    pp_yt_thumb.add_argument("--image", required=True, help="thumbnail image path (max 2 MB)")
    pp_yt_thumb.add_argument(
        "--approve", action="store_true", help="explicitly approve now (fresh token issued)"
    )
    pp_yt_thumb.add_argument("--approval-token", help="pre-issued HMAC approval token")

    p_analytics = sub.add_parser(
        "analytics",
        parents=[_make_common()],
        help="analytics ingestion, cited reports and explicit learning",
    )
    analytics_sub = p_analytics.add_subparsers(dest="analytics_command", required=True)
    pp_an_refresh = analytics_sub.add_parser(
        "refresh",
        parents=[_make_common()],
        help="ingest an analytics report (bundled fixture by default)",
    )
    pp_an_refresh.add_argument("--from-file", help="API-shaped or simplified JSON report")
    pp_an_refresh.add_argument(
        "--live", action="store_true", help="use the real YouTube Analytics API"
    )
    pp_an_refresh.add_argument("--start-date", help="live report start (YYYY-MM-DD)")
    pp_an_refresh.add_argument("--end-date", help="live report end (YYYY-MM-DD)")
    pp_an_report = analytics_sub.add_parser(
        "report", parents=[_make_common()], help="build a written report with cited metrics"
    )
    pp_an_report.add_argument("--from-file", help="snapshot file (default: latest)")
    pp_an_report.add_argument("--prior", help="earlier snapshot for baseline comparison")
    pp_an_learn = analytics_sub.add_parser(
        "learn", parents=[_make_common()], help="propose and stage scorer feedback explicitly"
    )
    pp_an_learn.add_argument("--from-file", help="snapshot file (default: latest)")
    pp_an_learn.add_argument("--report", help="report JSON file (default: latest)")
    pp_an_learn.add_argument(
        "--apply", action="store_true", help="apply the staged weights (requires approval)"
    )
    pp_an_learn.add_argument(
        "--approve", action="store_true", help="explicitly approve now (fresh token issued)"
    )
    pp_an_learn.add_argument("--approval-token", help="pre-issued HMAC approval token")
    analytics_sub.add_parser(
        "status",
        parents=[_make_common()],
        help="list snapshots, reports and pending learning changes",
    )

    p_captions = sub.add_parser(
        "captions", parents=[_make_common()], help="multilingual caption export (SRT/WebVTT)"
    )
    captions_sub = p_captions.add_subparsers(dest="captions_command", required=True)
    pc_export = captions_sub.add_parser(
        "export", parents=[_make_common()], help="export timeline captions to SRT or WebVTT"
    )
    pc_export.add_argument("--project", required=True, help="project id with a saved timeline")
    pc_export.add_argument("--format", default="vtt", choices=("srt", "vtt"))
    pc_export.add_argument("--lang", help="source caption language code (default: detected)")
    pc_export.add_argument("--to-lang", default="en", help="translation target code (default: en)")
    pc_export.add_argument(
        "--translate", action="store_true", help="translate via the configured optional provider"
    )
    pc_export.add_argument("--translations", help="JSON file with creator-supplied translations")
    pc_export.add_argument("--out", help="output directory inside the workspace")
    captions_sub.add_parser(
        "languages",
        parents=[_make_common()],
        help="list supported language codes and text direction",
    )

    p_voice = sub.add_parser(
        "voice",
        parents=[_make_common()],
        help="VoiceDNA: consented narration, providers and voice timeline",
    )
    voice_sub = p_voice.add_subparsers(dest="voice_command", required=True)
    voice_sub.add_parser(
        "providers",
        parents=[_make_common()],
        help="list voice providers with licence/capability flags",
    )
    pp_vlang = voice_sub.add_parser(
        "languages", parents=[_make_common()], help="list supported narration languages"
    )
    pp_vlang.add_argument("--provider", help="show only one provider's languages")
    for name, help_text in (
        ("setup", "guided registration with recording advice"),
        ("register", "register a validated voice reference with consent"),
    ):
        pp_vreg = voice_sub.add_parser(name, parents=[_make_common()], help=help_text)
        pp_vreg.add_argument("--owner", required=True, help="voice owner id")
        pp_vreg.add_argument(
            "--reference", required=True, help="clean reference audio (workspace file)"
        )
        pp_vreg.add_argument("--display-name", required=True, help="label for this voice")
        pp_vreg.add_argument("--provider", default="openvoice_v2", help="provider id")
        pp_vreg.add_argument("--model-name", help="provider model/voice name")
        pp_vreg.add_argument(
            "--languages", nargs="+", help="supported language codes (default: en)"
        )
        pp_vreg.add_argument("--purpose", nargs="+", help="consent purposes (default: narration)")
        pp_vreg.add_argument(
            "--confirm-voice-ownership",
            action="store_true",
            help="explicitly confirm the exact voice ownership/authorisation statement",
        )
        pp_vreg.add_argument(
            "--approve",
            action="store_true",
            help="explicitly approve this registration (fresh token issued)",
        )
        pp_vreg.add_argument("--approval-token", help="pre-issued HMAC approval token")
    pp_vinspect = voice_sub.add_parser(
        "inspect", parents=[_make_common()], help="inspect a voice profile, consent and provider"
    )
    pp_vinspect.add_argument("--owner", required=True)
    pp_vinspect.add_argument("--profile", required=True)
    pp_vtest = voice_sub.add_parser(
        "test", parents=[_make_common()], help="generate one consented narration sample"
    )
    pp_vtest.add_argument("--owner", required=True)
    pp_vtest.add_argument("--profile", required=True)
    pp_vtest.add_argument("--text", required=True, help="text to narrate")
    pp_vtest.add_argument("--provider", help="override the profile's provider for this test")
    pp_vtest.add_argument("--voice-name", help="override the model/voice name (e.g. SAPI voice)")
    pp_vtest.add_argument("--language", help="narration language code")
    pp_vtest.add_argument("--rate", type=int, help="provider rate control where supported")
    pp_vtest.add_argument("--volume", type=int, help="provider volume control where supported")
    pp_vtest.add_argument(
        "--allow-test-provider",
        action="store_true",
        help="tests only: allow the deterministic tone provider",
    )
    pp_vtest.add_argument(
        "--allow-noncommercial",
        action="store_true",
        help="acknowledge a non-commercial licence for a gated provider",
    )
    pp_vtest.add_argument("--licence-ack", help="recorded acknowledgement of a gated model licence")
    pp_vgen = voice_sub.add_parser(
        "generate",
        parents=[_make_common()],
        help="build an approved, footage-grounded narration and generate it",
    )
    pp_vgen.add_argument("--owner", required=True)
    pp_vgen.add_argument("--profile", required=True)
    pp_vgen.add_argument(
        "--project", required=True, help="project with a real media analysis report"
    )
    pp_vgen.add_argument(
        "--concept", required=True, help="creator's narration concept (untrusted text)"
    )
    pp_vgen.add_argument("--source", help="source media to analyze if the project has no report")
    pp_vgen.add_argument("--language", help="override narration language")
    pp_vgen.add_argument("--max-segments", type=int, default=8, help="maximum narration segments")
    pp_vgen.add_argument(
        "--approve",
        action="store_true",
        help="explicitly approve this exact script (fresh token issued)",
    )
    pp_vgen.add_argument("--approval-token", help="pre-issued HMAC approval token")
    pp_vgen.add_argument(
        "--allow-test-provider",
        action="store_true",
        help="tests only: allow the deterministic tone provider",
    )
    pp_vgen.add_argument(
        "--allow-noncommercial",
        action="store_true",
        help="acknowledge a non-commercial licence for a gated provider",
    )
    pp_vgen.add_argument("--licence-ack", help="recorded acknowledgement of a gated model licence")
    pp_vregen = voice_sub.add_parser(
        "regenerate",
        parents=[_make_common()],
        help="regenerate one segment without touching its siblings",
    )
    pp_vregen.add_argument("--owner", required=True)
    pp_vregen.add_argument("--profile", required=True)
    pp_vregen.add_argument("--segment", required=True, help="segment id, e.g. seg_003")
    pp_vregen.add_argument("--text", help="replacement narration text (default: keep existing)")
    pp_vregen.add_argument(
        "--approve",
        action="store_true",
        help="explicitly approve this regeneration (fresh token issued)",
    )
    pp_vregen.add_argument("--approval-token", help="pre-issued HMAC approval token")
    pp_vregen.add_argument(
        "--allow-test-provider",
        action="store_true",
        help="tests only: allow the deterministic tone provider",
    )
    pp_vprofiles = voice_sub.add_parser(
        "profiles", parents=[_make_common()], help="list voice profiles (owner redacted by default)"
    )
    pp_vprofiles.add_argument("--owner", help="show only this owner's profiles")
    pp_vhist = voice_sub.add_parser(
        "history", parents=[_make_common()], help="list immutable generation history for a profile"
    )
    pp_vhist.add_argument("--owner", required=True)
    pp_vhist.add_argument("--profile", required=True)
    p_vconsent = voice_sub.add_parser(
        "consent", parents=[_make_common()], help="grant or revoke purpose-scoped voice consent"
    )
    consent_sub = p_vconsent.add_subparsers(dest="consent_command", required=True)
    for action in ("grant", "revoke"):
        pp_vc = consent_sub.add_parser(
            action, parents=[_make_common()], help=f"{action} voice consent (requires approval)"
        )
        pp_vc.add_argument("--owner", required=True)
        pp_vc.add_argument("--profile", required=True)
        pp_vc.add_argument("--purpose", nargs="+", help="consent purposes (default: narration)")
        if action == "grant":
            pp_vc.add_argument(
                "--confirm-voice-ownership",
                action="store_true",
                help="explicitly confirm the exact voice ownership/authorisation statement",
            )
        pp_vc.add_argument(
            "--approve",
            action="store_true",
            help="explicitly approve this change (fresh token issued)",
        )
        pp_vc.add_argument("--approval-token", help="pre-issued HMAC approval token")
    pp_vdelete = voice_sub.add_parser(
        "delete",
        parents=[_make_common()],
        help="permanently delete a voice profile and all its data",
    )
    pp_vdelete.add_argument("--owner", required=True)
    pp_vdelete.add_argument("--profile", required=True)
    pp_vdelete.add_argument(
        "--approve", action="store_true", help="explicitly approve deletion (fresh token issued)"
    )
    pp_vdelete.add_argument("--approval-token", help="pre-issued HMAC approval token")
    pp_valign = voice_sub.add_parser(
        "align",
        parents=[_make_common()],
        help="align generated narration to measured footage timing",
    )
    pp_valign.add_argument("--owner", required=True)
    pp_valign.add_argument("--profile", required=True)
    pp_vcompose = voice_sub.add_parser(
        "compose",
        parents=[_make_common()],
        help="append additive voice metadata and captions to the EDL",
    )
    pp_vcompose.add_argument("--owner", required=True)
    pp_vcompose.add_argument("--profile", required=True)
    pp_vcompose.add_argument("--project", required=True)
    pp_vmix = voice_sub.add_parser(
        "mix",
        parents=[_make_common()],
        help="render narration over the base render (non-destructive)",
    )
    pp_vmix.add_argument("--project", required=True)
    pp_vmix.add_argument(
        "--source-render", help="base render path (default: latest project render)"
    )
    pp_vmix.add_argument(
        "--dry-run", action="store_true", help="build and validate the FFmpeg plan only"
    )
    pp_vpreview = voice_sub.add_parser(
        "preview", parents=[_make_common()], help="list narration segments or inspect one segment"
    )
    pp_vpreview.add_argument("--project", required=True)
    pp_vpreview.add_argument("--segment", help="inspect this segment and all its versions")
    pp_vpreview.add_argument("--version", type=int, help="EDL timeline version to preview")
    pp_vqa = voice_sub.add_parser(
        "qa", parents=[_make_common()], help="run objective VoiceDNA checks on a composed EDL"
    )
    pp_vqa.add_argument("--project", required=True)
    pp_vhybrid = voice_sub.add_parser(
        "hybrid",
        parents=[_make_common()],
        help="classify original/gameplay/silent sections from footage",
    )
    pp_vhybrid.add_argument("--project", required=True)
    pp_vhybrid.add_argument("--source", help="source media to analyze if the project has no report")
    pp_vhybrid.add_argument(
        "--window", type=float, default=2.0, help="classification window in seconds (default: 2.0)"
    )
    pp_vhybrid.add_argument(
        "--actions", help="JSON map of section_id to preserve/insert/replace/silence"
    )
    pp_vdisclosure = voice_sub.add_parser(
        "disclosure",
        parents=[_make_common()],
        help="assess and record YouTube synthetic-content disclosure",
    )
    pp_vdisclosure.add_argument("--project", required=True)
    pp_vdisclosure.add_argument("--version", type=int, help="EDL timeline version")
    pp_vdisclosure.add_argument(
        "--approve",
        action="store_true",
        help="explicitly approve the disclosure review (fresh token issued)",
    )
    pp_vdisclosure.add_argument("--approval-token", help="pre-issued HMAC approval token")

    p_analyze = sub.add_parser(
        "analyze", parents=[_make_common()], help="analyze channel/content/audience data"
    )
    analyze_sub = p_analyze.add_subparsers(dest="analyze_command", required=True)
    analyze_sub.add_parser("channel", parents=[_make_common()])

    p_media = sub.add_parser(
        "media",
        parents=[_make_common()],
        help="inspect/analyze/search media (FFmpeg + optional ASR)",
    )
    media_sub = p_media.add_subparsers(dest="media_command", required=True)
    pp_inspect = media_sub.add_parser(
        "inspect", parents=[_make_common()], help="ffprobe + hash a media file (read-only)"
    )
    pp_inspect.add_argument("path")
    pp_analyze = media_sub.add_parser(
        "analyze", parents=[_make_common()], help="detect silence, scenes, audio events, moments"
    )
    pp_analyze.add_argument("path")
    pp_analyze.add_argument("--project", help="project id (default: derived from filename)")
    pp_analyze.add_argument(
        "--transcribe", action="store_true", help="use optional ASR provider if installed"
    )
    pp_search = media_sub.add_parser(
        "search", parents=[_make_common()], help="search a project's media index"
    )
    pp_search.add_argument("query")
    pp_search.add_argument("--project", default="default")
    pp_search.add_argument("--limit", type=int, default=10)
    pp_diarize = media_sub.add_parser(
        "diarize", parents=[_make_common()], help="label speaker turns from an analysis report"
    )
    pp_diarize.add_argument(
        "--from-file", required=True, help="analysis report JSON inside the workspace"
    )
    pp_diarize.add_argument("--provider", default="auto", choices=("auto", "heuristic", "pyannote"))
    pp_diarize.add_argument("--audio", help="audio file required by model-based providers")
    pp_diarize.add_argument("--out", help="output JSON path inside the workspace")
    p_root = media_sub.add_parser(
        "root", parents=[_make_common()], help="register/list allowed media roots"
    )
    root_sub = p_root.add_subparsers(dest="root_command", required=True)
    root_sub.add_parser("list", parents=[_make_common()])
    pp_root_add = root_sub.add_parser("add", parents=[_make_common()])
    pp_root_add.add_argument("path")

    p_edit = sub.add_parser(
        "edit", parents=[_make_common()], help="build versioned EDLs and render with FFmpeg"
    )
    edit_sub = p_edit.add_subparsers(dest="edit_command", required=True)
    pp_new = edit_sub.add_parser(
        "new", parents=[_make_common()], help="create timeline v1 from media + analysis signals"
    )
    pp_new.add_argument("--source", required=True, help="source media file (read-only)")
    pp_new.add_argument(
        "--strategy", default="gaming", help="editing strategy (gaming/commentary/...)"
    )
    pp_new.add_argument("--concept", default="", help="creator concept for provenance")
    pp_new.add_argument("--project", help="project id (default: generated)")
    pp_render = edit_sub.add_parser(
        "render", parents=[_make_common()], help="render an EDL version to MP4 + QA report"
    )
    pp_render.add_argument("project")
    pp_render.add_argument("--version", type=int, help="timeline version (default: latest)")
    pp_versions = edit_sub.add_parser(
        "versions", parents=[_make_common()], help="list immutable timeline versions"
    )
    pp_versions.add_argument("project")
    pp_status = edit_sub.add_parser(
        "status", parents=[_make_common()], help="project summary and render history"
    )
    pp_status.add_argument("project")
    pp_show = edit_sub.add_parser("show", parents=[_make_common()], help="print the full EDL")
    pp_show.add_argument("project")
    pp_show.add_argument("--version", type=int)
    pp_revise = edit_sub.add_parser(
        "revise", parents=[_make_common()], help="parse a natural-language request into an EDL diff"
    )
    pp_revise.add_argument("project")
    pp_revise.add_argument(
        "request", nargs="?", default="", help="e.g. 'make the first 20s more exciting'"
    )
    pp_revise.add_argument("--version", type=int, help="parent timeline version (default: latest)")
    pp_revise.add_argument(
        "--plan-only",
        action="store_true",
        help="show the proposed diff without saving a new version",
    )
    pp_revise.add_argument(
        "--queue",
        action="store_true",
        help="save the parsed request as pending without applying it",
    )
    pp_revise.add_argument("--pending", help="apply a pending revision id from the preview UI")
    pp_revisions = edit_sub.add_parser(
        "revisions", parents=[_make_common()], help="list pending revision requests"
    )
    pp_revisions.add_argument("project")
    pp_exports = edit_sub.add_parser(
        "exports", parents=[_make_common()], help="list renders and export readiness"
    )
    pp_exports.add_argument("project")
    pp_preview = edit_sub.add_parser(
        "preview", parents=[_make_common()], help="start the loopback-only preview workspace"
    )
    pp_preview.add_argument("project")
    pp_preview.add_argument("--port", type=int, default=8765)
    pp_preview.add_argument("--host", default="127.0.0.1")

    p_shorts = sub.add_parser(
        "shorts", parents=[_make_common()], help="rank moments, draft 9:16 Shorts and approve them"
    )
    shorts_sub = p_shorts.add_subparsers(dest="shorts_command", required=True)
    pp_gen = shorts_sub.add_parser(
        "generate", parents=[_make_common()], help="generate 9:16 Short drafts from a source video"
    )
    pp_gen.add_argument("source", help="source media file (never modified)")
    pp_gen.add_argument("--project", help="project id (default: derived from filename)")
    pp_gen.add_argument(
        "--target", type=float, default=30.0, help="target Short duration in seconds (6-60)"
    )
    pp_gen.add_argument("--limit", type=int, default=3, help="maximum Shorts to render (1-5)")
    pp_gen.add_argument(
        "--focus",
        choices=("auto", "center"),
        default="auto",
        help="motion-centred crop or centered blur-pad fallback",
    )
    pp_gen.add_argument(
        "--transcribe", action="store_true", help="use optional ASR for transcript-backed captions"
    )
    pp_gen.add_argument(
        "--no-provider",
        action="store_true",
        help="force deterministic template hooks (no configured LLM)",
    )
    pp_review = shorts_sub.add_parser(
        "review", parents=[_make_common()], help="list drafts or approve one Short explicitly"
    )
    pp_review.add_argument("project")
    pp_review.add_argument("--short", help="short id, e.g. short_001")
    pp_review.add_argument(
        "--approve", action="store_true", help="explicitly approve now (fresh token issued)"
    )
    pp_review.add_argument("--approval-token", help="pre-issued HMAC approval token")

    p_ideas = sub.add_parser(
        "ideas", parents=[_make_common()], help="generate evidence-backed content ideas"
    )
    p_ideas.add_argument("--limit", type=int, default=10, help="maximum ideas to return (1-50)")

    p_trends = sub.add_parser(
        "trends", parents=[_make_common()], help="analyze topic signals from timestamped snapshots"
    )
    p_trends.add_argument("--from-file", help="import a trend snapshot JSON fixture")

    p_competitors = sub.add_parser(
        "competitors", parents=[_make_common()], help="compare public competitor snapshots"
    )
    p_competitors.add_argument("--from-file", help="import a competitor snapshot JSON fixture")

    p_plan = sub.add_parser(
        "plan", parents=[_make_common()], help="schedule ideas using creator publishing preferences"
    )
    p_plan.add_argument("--limit", type=int, default=5, help="maximum plan entries (1-20)")

    p_script = sub.add_parser(
        "script", parents=[_make_common()], help="draft a script from a generated idea"
    )
    script_sub = p_script.add_subparsers(dest="script_command", required=True)
    pp_draft = script_sub.add_parser(
        "draft", parents=[_make_common()], help="draft a beat-structured script"
    )
    pp_draft.add_argument("--idea", required=True, help="idea_id from `creator ideas`")

    p_doctor = sub.add_parser(
        "doctor",
        parents=[_make_common()],
        help="check the environment (Python, FFmpeg, optional deps)",
    )
    p_doctor.add_argument(
        "--check-workspace", action="store_true", help="also report whether a workspace exists here"
    )

    p_init = sub.add_parser(
        "init", parents=[_make_common()], help="create a .creator workspace in the target directory"
    )
    p_init.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="directory for the workspace (default: current directory)",
    )

    p_audit = sub.add_parser("audit", parents=[_make_common()], help="security/audit log access")
    audit_sub = p_audit.add_subparsers(dest="audit_command", metavar="<subcommand>", required=True)
    p_audit_show = audit_sub.add_parser(
        "show", parents=[_make_common()], help="print recent audit events"
    )
    p_audit_show.add_argument("--limit", type=int, default=50)

    p_mcp = sub.add_parser(
        "mcp", parents=[_make_common()], help="optional read-only MCP JSON-RPC stdio adapter"
    )
    mcp_sub = p_mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser(
        "tools", parents=[_make_common()], help="list the curated read-only MCP tools"
    )
    mcp_sub.add_parser(
        "serve", parents=[_make_common()], help="serve JSON-RPC 2.0 over stdin/stdout (blocking)"
    )

    p_release = sub.add_parser(
        "release",
        parents=[_make_common()],
        help="local release checks, source manifest, sdist/wheel build",
    )
    release_sub = p_release.add_subparsers(dest="release_command", required=True)
    release_sub.add_parser(
        "check", parents=[_make_common()], help="verify versions agree and packaged files exist"
    )
    release_sub.add_parser(
        "manifest", parents=[_make_common()], help="print SHA-256 hashes of required source files"
    )
    pp_release_build = release_sub.add_parser(
        "build", parents=[_make_common()], help="build local sdist+wheel and a SHA-256 manifest"
    )
    pp_release_build.add_argument(
        "--out", default="dist", help="output directory inside the checkout (default: dist)"
    )

    handlers = {
        "doctor": cmd_doctor.run,
        "init": cmd_workspace.run_init,
        "audit": cmd_audit.run,
        "mcp": cmd_mcp.run,
        "release": cmd_release.run,
        "profile": cmd_profile.run,
        "channel": cmd_channel.run,
        "youtube": cmd_youtube.run,
        "analytics": cmd_analytics.run,
        "captions": cmd_captions.run,
        "analyze": cmd_analyze.run,
        "media": cmd_media.run_media,
        "edit": cmd_edit.run_edit,
        "shorts": cmd_shorts.run,
        "voice": cmd_voice.run,
        "ideas": cmd_research.run_ideas,
        "trends": cmd_research.run_trends,
        "competitors": cmd_research.run_competitors,
        "plan": cmd_research.run_plan,
        "script": cmd_research.run_script,
    }
    return parser, handlers


def _configure_streams() -> None:
    """Emit UTF-8 regardless of the host console code page (Windows cp1252 by default)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - unusual test streams
                pass


def main(argv: list[str] | None = None) -> int:
    _configure_streams()
    parser, handlers = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "command", None) is None:
        parser.print_help()
        return 0

    handler = handlers[args.command]
    base = (getattr(args, "workspace", None) or Path.cwd()).resolve()
    ctx = CliContext(
        json_mode=getattr(args, "json", False),
        debug=getattr(args, "debug", False),
        workspace=base,
    )

    try:
        return handler(ctx, args)
    except CreatorError as exc:
        emit_error(exc)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 - unexpected errors need a structured CLI response
        if getattr(args, "debug", False):
            traceback.print_exc()
        err = CreatorError(
            f"unexpected error: {exc.__class__.__name__}: {exc}",
            hint="rerun with --debug for a traceback; please report this",
        )
        emit_error(err)
        return err.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
