"""`creator voice …` — VoiceDNA registration, inspection and provider testing (Phase 11D).

All commands emit contract JSON with `--json`. State-changing commands use the same one-time,
full-request-bound HMAC approval as publishing. Provider availability, licence and consent are
checked before any audio is generated, and no neural model is ever invoked implicitly.
"""

from __future__ import annotations

import json
from pathlib import Path

from creator_core.cli.errors import PathNotFoundError, ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.security.approval import require_approval
from creator_core.security.audit import AuditLog
from creator_core.voice import (
    PURPOSES,
    delete_profile,
    grant_consent,
    list_profiles,
    load_profile,
    register_voice,
    revoke_consent,
    verify_consent,
)
from creator_core.voice.consent import CONSENT_STATEMENT, load_consent
from creator_core.voice.paths import ensure_voice_dirs, owner_dir
from creator_core.voice.profile import profile_sha256
from creator_core.voice.providers import list_languages, list_providers, select
from creator_core.voice.providers.base import SpeechResult
from creator_core.voice.reference import RECORDING_GUIDANCE
from creator_core.workspace.layout import ensure_workspace, load_config

_DEFAULT_PROVIDER = "openvoice_v2"


def _require_approval(
    ws, action: str, target: str, token: str | None, *, generate: bool, request: dict
) -> dict:
    """Bind approval to the full voice request and enforce one-time use (post-11A policy)."""
    approval = require_approval(
        ws,
        action,
        target,
        token,
        generate=generate,
        payload=request,
        hint="rerun with --approve after confirming the exact request",
    )
    return {"fingerprint": approval["fingerprint"], "expires": approval["expires"]}


def _load_footage_report(ws, project_id: str, source: str | None, config: dict) -> dict:
    """Load a real media analysis report, or analyze the given source read-only."""
    import json

    from creator_core.media.analyze import analyze
    from creator_core.media.index import build_index
    from creator_core.workspace.layout import project_dir

    analysis_dir = project_dir(ws, project_id) / "analysis"
    if source:
        from creator_core.media.ingest import inspect

        info = inspect(source, ws, config)
        path = analysis_dir / f"{info['source_id']}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        report = analyze(source, ws, project_id=project_id, config=config)
        build_index(report, ws)
        return report

    candidates = sorted(analysis_dir.glob("*.json")) if analysis_dir.is_dir() else []
    if not candidates:
        raise ValidationError(
            f"no footage analysis found for project {project_id}",
            hint="run `creator media analyze SOURCE --project ID` or pass --source",
        )
    try:
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"footage analysis report is unreadable: {exc}") from exc


def _generation_payload(ws, owner: str, profile: str) -> dict:
    """Rebuild an align_segments-compatible payload from persisted generation state."""
    from creator_core.voice.synthesis import current_state, history

    state = current_state(ws, owner, profile)
    if not state:
        raise PathNotFoundError(
            f"no generated narration for profile {profile}",
            hint="run `creator voice generate` first",
        )
    entries = [e for e in history(ws, owner, profile) if e.get("status") == "ok"]
    latest = entries[-1] if entries else {}
    payload = dict(state)
    payload.update(
        {
            "owner_id": owner,
            "voice_profile_id": profile,
            "segments": list((state.get("segments") or {}).values()),
            "consent_fingerprint": latest.get("consent_fingerprint"),
            "script_sha256": latest.get("script_sha256"),
            "provider": latest.get("provider") or {},
        }
    )
    return payload


def _alignment_path(ws, owner: str, profile: str):
    from creator_core.voice.synthesis import generation_dir

    return generation_dir(ws, owner, profile) / "alignment.json"


def _latest_mix_payload(ws, project_id: str):
    import json

    from creator_core.workspace.layout import project_dir

    renders = project_dir(ws, project_id) / "renders"
    if not renders.is_dir():
        return None
    manifests = sorted(renders.glob("voice_v*_mix_manifest.json"))
    if not manifests:
        return None
    try:
        return json.loads(manifests[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _print_generate(payload: dict) -> None:
    print(
        f"voice generate — {len(payload['segments'])} segment(s), "
        f"provider={payload['provider']['id']}"
    )
    for segment in payload["segments"]:
        print(
            f"  {segment['segment_id']} v{segment['version']} "
            f"{segment['duration_seconds']}s mocked={segment['mocked']}"
        )
    print(f"  {payload['note']}")


def _print_regenerate(payload: dict) -> None:
    print(
        f"voice regenerate — {payload['segment_id']} "
        f"v{payload['previous_version']} → v{payload['version']}"
    )
    print(f"  preserved siblings: {', '.join(payload['preserved_siblings']) or '(none)'}")


def _print_align(payload: dict) -> None:
    print(
        f"voice align — {len(payload['segments'])} segment(s), "
        f"timeline {payload['timeline_start']}s–{payload['timeline_end']}s"
    )
    print(f"  timing: {payload['label']} (duration measured, boundaries estimated)")
    print(f"  saved: {payload['alignment_path']}")


def _print_compose(payload: dict) -> None:
    voice = payload["voice"]
    print(
        f"voice compose — timeline v{payload['timeline_version']}, "
        f"{len(voice['segments'])} segment(s), {len(voice['caption_cues'])} narration caption(s)"
    )
    print(f"  disclosure: {voice['disclosure']['assessment']}")
    print(f"  {voice['note']}")


def _print_mix(payload: dict) -> None:
    print(
        f"voice mix — v{payload['version']}, {payload['audio_segments']} narration "
        f"input(s), {len(payload['duck_windows'])} duck window(s)"
    )
    print(f"  output: {payload['output'] or '(dry run)'}")
    print(f"  {payload['note']}")


def _print_preview(payload: dict) -> None:
    if payload.get("segment"):
        print(
            f"voice preview — {payload['segment']['segment_id']} "
            f"v{payload['segment']['version']}, {len(payload['candidates'])} candidate(s)"
        )
        for candidate in payload["candidates"]:
            print(
                f"  v{candidate['version']} {'current' if candidate['is_current'] else 'previous'}"
            )
    else:
        print(
            f"voice preview — {len(payload['segments'])} segment(s) in project "
            f"{payload['project_id']}"
        )
        for segment in payload["segments"]:
            print(
                f"  {segment['segment_id']} v{segment['version']} "
                f"{segment['status']} mocked={segment['mocked']}"
            )


def _print_qa(payload: dict) -> None:
    print(
        f"voice qa — ok={payload['ok']}, "
        f"{sum(1 for c in payload['checks'] if c['ok'])}/{len(payload['checks'])} checks passed"
    )
    for item in payload["needs_approval"]:
        print(f"  NEEDS APPROVAL: {item}")
    print(f"  disclosure: {payload['disclosure']['assessment']}")


def _print_hybrid(payload: dict) -> None:
    print(f"voice hybrid — {len(payload['sections'])} section(s)")
    for section in payload["sections"]:
        print(
            f"  {section['section_id']} {section['type']:<16} "
            f"{section['start']}–{section['end']}s action={section['action']}"
        )
    if payload.get("timeline_saved"):
        print(f"  saved to timeline v{payload['timeline_version']}")


def _print_disclosure(payload: dict) -> None:
    print(f"voice disclosure — {payload['assessment']}, reviewed={payload['creator_reviewed']}")
    for reason in payload["reasons"]:
        print(f"  {reason}")


def _print(payload: dict) -> None:
    command = payload.get("command", "voice")
    print(f"{command} — ok={payload.get('ok')}")
    if payload.get("note"):
        print(f"  {payload['note']}")


def _print_providers(payload: dict) -> None:
    print("voice providers — provider / licence / commercial / clones / available")
    for item in payload["providers"]:
        print(
            f"  {item['provider_id']:<15} {item['weight_licence'][:42]:<42} "
            f"commercial={item['commercial_ok']!s:<5} clones={item['clones_voice']!s:<5} "
            f"available={item['available']}"
        )
    print(f"  {payload['note']}")


def _print_languages(payload: dict) -> None:
    print("voice languages — " + ", ".join(payload["languages"]))
    print(f"  {payload['note']}")


def _print_profile(payload: dict) -> None:
    print(
        f"voice {payload['command']} — {payload['voice_profile_id']} "
        f"({payload['display_name']}, provider={payload['model_provider']})"
    )
    print(f"  consent: {payload['consent_status']}, purposes: {', '.join(payload['purposes'])}")
    print(f"  {payload['note']}")


def _print_test(payload: dict) -> None:
    result = payload["result"]
    print(
        f"voice test — {result['model_provider']}/{result['model_name']} "
        f"{result['duration_seconds']}s @ {result['sample_rate']}Hz"
    )
    print(f"  audio: {result['audio_path']}")
    for note in result.get("notes", []):
        print(f"  {note}")


def run(ctx: CliContext, args) -> int:
    sub = args.voice_command
    # Informational commands work without a workspace; all others require one.
    if sub in ("providers", "languages"):
        ws, config = None, {}
    else:
        ws = ensure_workspace(ctx.workspace)
        config = load_config(ws)

    # ---- informational commands (no state change, no workspace writes) ---------------------
    if sub == "providers":
        payload = {
            "ok": True,
            "command": "voice providers",
            "providers": list_providers(),
            "note": (
                "Licence flags are adapter declarations; neural weights are never "
                "downloaded or executed implicitly."
            ),
        }
        printer = _print_providers
    elif sub == "languages":
        payload = {
            "ok": True,
            "command": "voice languages",
            **list_languages(getattr(args, "provider", None)),
        }
        printer = _print_languages
    elif sub == "profiles":
        owner = getattr(args, "owner", None)
        payload = {
            "ok": True,
            "command": "voice profiles",
            "profiles": list_profiles(ws, owner),
            "owner_id": owner or "<redacted>",
            "note": "Owner ids are redacted unless a specific --owner is supplied.",
        }
        printer = lambda p: (
            print(f"voice profiles — {len(p['profiles'])}"),
            [
                print(
                    f"  {i['voice_profile_id']} {i['display_name']} consent={i['consent_status']}"
                )
                for i in p["profiles"]
            ],
        )
    elif sub == "inspect":
        profile = load_profile(ws, args.owner, args.profile)
        consent = load_consent(ws, args.owner, args.profile)
        provider = None
        try:
            from creator_core.voice.providers import get_provider

            provider = get_provider(profile["model_provider"]).info()
        except ValidationError:
            provider = None
        payload = {
            "ok": True,
            "command": "voice inspect",
            "profile": profile,
            "voice_profile_id": profile["voice_profile_id"],
            "display_name": profile["display_name"],
            "model_provider": profile["model_provider"],
            "owner_id": args.owner,
            "consent_status": (consent or {}).get("status", "none"),
            "purposes": (consent or {}).get("purposes", []),
            "provider": provider,
            "profile_sha256": profile_sha256(profile),
            "note": "Sensitive reference audio is never printed; only its metadata is shown.",
        }
        printer = _print_profile

    # ---- registration ---------------------------------------------------------------------
    elif sub in ("setup", "register"):
        purposes = list(getattr(args, "purpose", None) or ["narration"])
        if getattr(args, "purpose", None):
            unknown = sorted(set(purposes) - set(PURPOSES))
            if unknown:
                raise ValidationError(
                    f"unknown consent purpose(s): {', '.join(unknown)}",
                    hint="supported: " + ", ".join(PURPOSES),
                )
        if not getattr(args, "confirm_voice_ownership", False):
            raise ValidationError(
                "voice ownership/authorisation must be explicitly confirmed",
                hint=f"rerun with --confirm-voice-ownership to confirm: {CONSENT_STATEMENT}",
            )
        reference = Path(args.reference)
        from creator_core.media.ingest import sha256_file, validate_media_path

        checked_ref = validate_media_path(reference, ws, config)
        # S-03: resolve EVERY field that ends up in the immutable profile before approval, so a
        # token holder cannot swap the model, version or languages after signing.
        model_provider = str(getattr(args, "provider", None) or _DEFAULT_PROVIDER)
        model_name = getattr(args, "model_name", None)
        if not model_name and model_provider == "system_sapi":
            # A SAPI profile needs an actual installed voice name, not a provider id.
            try:
                from creator_core.voice.providers import get_provider

                available = get_provider("system_sapi").validate_model()["voices"]
                model_name = available[0]["name"] if available else "system_sapi"
            except Exception:  # noqa: BLE001 - SAPI/COM failures vary by Windows installation
                model_name = "system_sapi"
        model_name = model_name or model_provider
        model_version = "1"
        languages = list(getattr(args, "languages", None) or ["en"])
        request = {
            "owner_id": args.owner,
            "reference_name": checked_ref.name,
            "reference_sha256": sha256_file(checked_ref),
            "display_name": args.display_name,
            "purposes": purposes,
            "provider": model_provider,
            "model_name": model_name,
            "model_version": model_version,
            "supported_languages": languages,
            "consent_statement": CONSENT_STATEMENT,
            "confirm_voice_ownership": True,
        }
        approval = _require_approval(
            ws,
            "voice.register",
            f"{args.owner}/{checked_ref.name}",
            getattr(args, "approval_token", None),
            generate=bool(getattr(args, "approve", False)),
            request=request,
        )
        payload = register_voice(
            ws,
            owner_id=args.owner,
            reference=checked_ref,
            display_name=args.display_name,
            purposes=purposes,
            model_provider=model_provider,
            model_name=model_name,
            model_version=model_version,
            supported_languages=languages,
            consent_statement=CONSENT_STATEMENT,
        )
        if sub == "setup":
            payload["recording_guidance"] = RECORDING_GUIDANCE
        payload["approval"] = approval
        printer = _print_profile

    # ---- provider test --------------------------------------------------------------------
    elif sub == "test":
        profile = load_profile(ws, args.owner, args.profile)
        verify_consent(ws, args.owner, args.profile, "narration")
        provider = select(
            profile,
            purpose="narration",
            provider_id=getattr(args, "provider", None),
            allow_test_provider=bool(getattr(args, "allow_test_provider", False)),
            allow_noncommercial=bool(getattr(args, "allow_noncommercial", False)),
            acknowledgement=getattr(args, "licence_ack", None),
            language=getattr(args, "language", None)
            or (profile.get("supported_languages") or ["en"])[0],
        )
        voice_info = dict(profile)
        voice_info["workspace"] = ws
        voice_info["config"] = config
        if getattr(args, "voice_name", None):
            voice_info["model_name"] = args.voice_name
        controls = {}
        if getattr(args, "rate", None) is not None:
            controls["rate"] = int(args.rate)
        if getattr(args, "volume", None) is not None:
            controls["volume"] = int(args.volume)
        result: SpeechResult = provider.generate_speech(
            args.text,
            language=getattr(args, "language", None) or "en",
            voice=voice_info,
            controls=controls,
        )
        # Move the test result into the owner's export tree so it survives tmp cleanup.
        exports = owner_dir(ensure_voice_dirs(ws), args.owner, "exports") / args.profile / "samples"
        exports.mkdir(parents=True, exist_ok=True)
        dest = exports / f"test_{Path(result.audio_path).name}"
        Path(result.audio_path).replace(dest)
        result.audio_path = str(dest)
        payload = {
            "ok": True,
            "command": "voice test",
            "result": result.to_dict(),
            "note": (
                "Real local speech synthesis."
                if not result.mocked
                else "MOCKED tone — not speech, tests only."
            ),
        }
        printer = _print_test

    # ---- footage-grounded generation / targeted regeneration ------------------------------
    elif sub == "generate":
        from creator_core.voice import build_voice_script, generate_narration
        from creator_core.voice.synthesis import generation_dir

        profile = load_profile(ws, args.owner, args.profile)
        report = _load_footage_report(ws, args.project, getattr(args, "source", None), config)
        script = build_voice_script(
            report,
            args.concept,
            profile,
            language=getattr(args, "language", None),
            max_segments=getattr(args, "max_segments", 8),
        )
        segments = script["segments"]
        request = {
            "owner_id": args.owner,
            "profile_id": args.profile,
            "project_id": args.project,
            "concept": script["concept"],
            "script_sha256": script["script_sha256"],
            "source_id": script.get("source_id"),
            "language": script["language"],
            "segments": [
                {
                    "segment_id": s["segment_id"],
                    "text": s["text"],
                    "footage_start": s["footage_start"],
                    "footage_end": s["footage_end"],
                }
                for s in segments
            ],
            "purpose": "narration",
        }
        approval = _require_approval(
            ws,
            "voice.generate",
            f"{args.owner}/{args.profile}/{args.project}",
            getattr(args, "approval_token", None),
            generate=bool(getattr(args, "approve", False)),
            request=request,
        )
        for segment in segments:
            segment["status"] = "approved"
        generated = generate_narration(
            ws,
            args.owner,
            args.profile,
            segments,
            purpose="narration",
            script_sha256=script["script_sha256"],
            project_id=args.project,
            config=config,
            allow_external=bool(getattr(args, "allow_external", False)),
            allow_noncommercial=bool(getattr(args, "allow_noncommercial", False)),
            allow_test_provider=bool(getattr(args, "allow_test_provider", False)),
            acknowledgement=getattr(args, "licence_ack", None),
        )
        scripts_dir = generation_dir(ws, args.owner, args.profile) / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / f"script_{script['script_sha256'][:12]}.json"
        from creator_core.workspace.atomic import atomic_write_json

        atomic_write_json(script_path, script)
        generated.update(
            {
                "script": script,
                "script_path": str(script_path),
                "approval": approval,
                "recording": {
                    "source_duration_seconds": script["source_duration_seconds"],
                    "grounding": script["grounding_summary"],
                },
                "note": "Footage-grounded, approval-bound narration. Text wording is a hypothesis.",
            }
        )
        payload, printer = generated, _print_generate

    elif sub == "regenerate":
        from creator_core.voice import regenerate_segment
        from creator_core.voice.synthesis import current_state

        state = current_state(ws, args.owner, args.profile) or {}
        existing = state.get("segments", {}).get(args.segment)
        if existing is None:
            raise PathNotFoundError(
                f"segment {args.segment} has no generated audio for profile {args.profile}",
                hint="run `creator voice generate` first",
            )
        request = {
            "owner_id": args.owner,
            "profile_id": args.profile,
            "segment_id": args.segment,
            "text": getattr(args, "text", None) or existing.get("text", ""),
            "previous_version": existing.get("version"),
            "previous_audio_sha256": existing.get("audio_sha256"),
        }
        approval = _require_approval(
            ws,
            "voice.regenerate",
            f"{args.owner}/{args.profile}/{args.segment}",
            getattr(args, "approval_token", None),
            generate=bool(getattr(args, "approve", False)),
            request=request,
        )
        result = regenerate_segment(
            ws,
            args.owner,
            args.profile,
            args.segment,
            text=getattr(args, "text", None),
            config=config,
            allow_external=bool(getattr(args, "allow_external", False)),
            allow_noncommercial=bool(getattr(args, "allow_noncommercial", False)),
            allow_test_provider=bool(getattr(args, "allow_test_provider", False)),
            acknowledgement=getattr(args, "licence_ack", None),
        )
        result["approval"] = approval
        align_path = _alignment_path(ws, args.owner, args.profile)
        if align_path.is_file():
            from creator_core.voice import align_segments
            from creator_core.workspace.atomic import atomic_write_json

            try:
                aligned = align_segments(
                    ws, _generation_payload(ws, args.owner, args.profile), config=config
                )
                atomic_write_json(
                    align_path,
                    {
                        **aligned,
                        "alignment_path": str(align_path),
                        "invalidated_by_regenerate": args.segment,
                    },
                )
                result["alignment_invalidated"] = True
                result["alignment_recomputed"] = True
                result["note"] = (
                    "Only this segment was regenerated; siblings are byte-for-byte unchanged. "
                    "Alignment was recomputed for the full current generation."
                )
            except Exception as exc:  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
                result["alignment_invalidated"] = True
                result["alignment_recomputed"] = False
                result["alignment_error"] = str(exc)
                try:
                    stale = json.loads(align_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
                    stale = {}
                stale.update(
                    {
                        "stale": True,
                        "invalidated_by_regenerate": args.segment,
                        "alignment_error": str(exc),
                    }
                )
                atomic_write_json(align_path, stale)
                result["note"] = (
                    "Only this segment was regenerated; siblings are byte-for-byte unchanged. "
                    "Existing alignment is stale and recomputation failed; rerun `creator voice align`."
                )
        else:
            result["alignment_invalidated"] = False
            result["note"] = (
                "Only this segment was regenerated; siblings are byte-for-byte unchanged."
            )
        payload, printer = result, _print_regenerate

    # ---- alignment / composition / mix / preview / QA / hybrid / disclosure ----------------
    elif sub == "align":
        from creator_core.voice import align_segments
        from creator_core.workspace.atomic import atomic_write_json

        aligned = align_segments(
            ws, _generation_payload(ws, args.owner, args.profile), config=config
        )
        align_path = _alignment_path(ws, args.owner, args.profile)
        atomic_write_json(align_path, aligned)
        payload = {**aligned, "alignment_path": str(align_path)}
        printer = _print_align

    elif sub == "compose":
        import json

        from creator_core.editing.edl import derive, load_edl, save_edl
        from creator_core.voice import build_voice_edl

        align_path = _alignment_path(ws, args.owner, args.profile)
        if not align_path.is_file():
            raise PathNotFoundError(
                f"no alignment for profile {args.profile}",
                hint="run `creator voice align` first",
            )
        aligned = json.loads(align_path.read_text(encoding="utf-8"))
        edl = load_edl(ws, args.project)
        composed = build_voice_edl(edl, aligned, owner_id=args.owner)
        child = derive(
            composed,
            decisions=[
                {
                    "op": "voice.compose",
                    "profile_id": args.profile,
                    "generation_id": aligned.get("generation_id"),
                    "disclosure": composed["voice"]["disclosure"]["assessment"],
                }
            ],
        )
        saved = save_edl(ws, child)
        payload = {
            "ok": True,
            "command": "voice compose",
            "project_id": args.project,
            "timeline_version": saved["version"],
            "voice": composed["voice"],
            "note": "Additive EDL v1 metadata; base tracks and renders are untouched.",
        }
        printer = _print_compose

    elif sub == "mix":
        from creator_core.editing.edl import load_edl
        from creator_core.voice import render_voice_mix

        edl = load_edl(ws, args.project)
        payload = render_voice_mix(
            ws,
            args.project,
            edl,
            config=config,
            source_render=getattr(args, "source_render", None),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        printer = _print_mix

    elif sub == "preview":
        from creator_core.voice import preview_data, segment_preview

        if getattr(args, "segment", None):
            payload = segment_preview(ws, args.project, args.segment)
        else:
            payload = preview_data(ws, args.project, version=getattr(args, "version", None))
        printer = _print_preview

    elif sub == "qa":
        from creator_core.editing.edl import load_edl
        from creator_core.voice import run_voice_qa

        edl = load_edl(ws, args.project)
        payload = run_voice_qa(ws, args.project, edl, _latest_mix_payload(ws, args.project))
        printer = _print_qa

    elif sub == "hybrid":
        import json

        from creator_core.editing.edl import derive, load_edl, save_edl
        from creator_core.voice import apply_section_actions, classify_sections

        report = _load_footage_report(ws, args.project, getattr(args, "source", None), config)
        sections = classify_sections(report, window_seconds=getattr(args, "window", 2.0))
        timeline_saved = None
        if getattr(args, "actions", None):
            actions = json.loads(args.actions)
            sections = apply_section_actions(sections, actions)
            edl = load_edl(ws, args.project)
            voice = edl.get("voice")
            if not isinstance(voice, dict):
                raise ValidationError(
                    f"project {args.project} has no voice EDL to update",
                    hint="run `creator voice compose` first",
                )
            voice.setdefault("mix", {})["hybrid_sections"] = sections["sections"]
            child = derive(
                edl,
                decisions=[
                    {
                        "op": "voice.hybrid",
                        "sections": len(sections["sections"]),
                    }
                ],
            )
            saved = save_edl(ws, child)
            timeline_saved = saved["version"]
        sections["timeline_saved"] = timeline_saved
        sections["timeline_version"] = timeline_saved
        printer = _print_hybrid
        payload = sections

    elif sub == "disclosure":
        from creator_core.editing.edl import derive, load_edl, save_edl
        from creator_core.voice import assess_disclosure

        edl = load_edl(ws, args.project, getattr(args, "version", None))
        assessment = assess_disclosure(edl.get("voice") or {})
        if getattr(args, "approve", False):
            token_request = {
                "project_id": args.project,
                "assessment": assessment["assessment"],
                "reasons": assessment["reasons"],
            }
            approval = _require_approval(
                ws,
                "voice.disclosure",
                args.project,
                getattr(args, "approval_token", None),
                generate=True,
                request=token_request,
            )
            assessment["approval"] = approval
            child = derive(
                edl,
                decisions=[
                    {
                        "op": "voice.disclosure.review",
                        "assessment": assessment["assessment"],
                    }
                ],
            )
            assessment["creator_reviewed"] = True
            child["voice"]["disclosure"] = dict(assessment)
            saved = save_edl(ws, child)
            assessment["timeline_version"] = saved["version"]
        assessment.update({"ok": True, "command": "voice disclosure", "project_id": args.project})
        printer = _print_disclosure
        payload = assessment

    # ---- consent / deletion ---------------------------------------------------------------
    elif sub == "consent":
        profile = load_profile(ws, args.owner, args.profile)
        action = args.consent_command
        purposes = list(getattr(args, "purpose", None) or ["narration"])
        if action == "grant" and not getattr(args, "confirm_voice_ownership", False):
            raise ValidationError(
                "voice ownership/authorisation must be explicitly confirmed before granting consent",
                hint=f"rerun with --confirm-voice-ownership to confirm: {CONSENT_STATEMENT}",
            )
        request = {
            "owner_id": args.owner,
            "profile_id": args.profile,
            "action": action,
            "purposes": purposes,
        }
        if action == "grant":
            request["consent_statement"] = CONSENT_STATEMENT
            request["confirm_voice_ownership"] = True
        approval = _require_approval(
            ws,
            f"voice.consent.{action}",
            f"{args.owner}/{args.profile}",
            getattr(args, "approval_token", None),
            generate=bool(getattr(args, "approve", False)),
            request=request,
        )
        audit = AuditLog(ws.root)
        if action == "grant":
            record = grant_consent(
                ws, args.owner, args.profile, purposes, statement=CONSENT_STATEMENT, audit=audit
            )
            payload = {
                "ok": True,
                "command": "voice consent grant",
                "consent": record,
                "approval": approval,
                "statement": CONSENT_STATEMENT,
                "note": "Purpose-scoped consent recorded and audit-logged.",
            }
        elif action == "revoke":
            record = revoke_consent(ws, args.owner, args.profile, audit=audit)
            payload = {
                "ok": True,
                "command": "voice consent revoke",
                "consent": record,
                "approval": approval,
                "note": "Consent revoked; all future generation attempts will fail.",
            }
        else:  # pragma: no cover - argparse restricts choices
            raise ValidationError(f"unknown consent command: {action}")
        printer = _print

    elif sub == "delete":
        profile = load_profile(ws, args.owner, args.profile)
        request = {
            "owner_id": args.owner,
            "profile_id": args.profile,
            "profile_sha256": profile_sha256(profile),
        }
        approval = _require_approval(
            ws,
            "voice.delete",
            f"{args.owner}/{args.profile}",
            getattr(args, "approval_token", None),
            generate=bool(getattr(args, "approve", False)),
            request=request,
        )
        payload = delete_profile(ws, args.owner, args.profile, audit=AuditLog(ws.root))
        payload.update({"command": "voice delete", "approval": approval})
        printer = _print

    elif sub == "history":
        profile = load_profile(ws, args.owner, args.profile)
        from creator_core.voice.synthesis import history

        payload = {
            "ok": True,
            "command": "voice history",
            "history": history(ws, args.owner, args.profile),
            "voice_profile_id": args.profile,
            "note": "Immutable generation history (no raw audio, no secrets).",
        }
        printer = lambda p: (
            print(f"voice history — {len(p['history'])} generation(s)"),
            [
                print(f"  {h.get('generation_id')} v{h.get('version')} {h.get('status')}")
                for h in p["history"]
            ],
        )

    else:  # pragma: no cover - argparse restricts choices
        raise ValidationError(f"unknown voice command: {sub}")

    if ctx.json_mode:
        emit_json(payload)
    else:
        printer(payload)
    return 0
