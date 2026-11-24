"""`creator captions …` — deterministic, provenance-aware multilingual caption export (P10)."""

from __future__ import annotations

from creator_core.captions import formats
from creator_core.captions import language as languages
from creator_core.captions import translate as translations
from creator_core.cli.errors import ProviderUnavailableError, ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.editing.edl import load_edl
from creator_core.profiles.profile import ProfileManager
from creator_core.security.audit import AuditLog
from creator_core.security.paths import resolve_within, sanitize_filename
from creator_core.workspace.layout import ensure_workspace, load_config


def _print_export(payload: dict) -> None:
    print(
        f"captions export — {payload['cue_count']} cue(s), format={payload['format']}, "
        f"language={payload['language']['code'] or 'unknown'}"
    )
    print(f"  file: {payload['path']}")
    print(f"  {payload['note']}")


def _print_languages(payload: dict) -> None:
    print(f"captions languages — {payload['count']} known code(s)")
    for item in payload["languages"]:
        direction = "rtl" if item["rtl"] else "ltr"
        print(f"  {item['code']:<4} {item['name']:<12} {direction}")


def _resolve_language(value: str | None, *, source: str, label: str) -> dict:
    info = languages.language_info(value)
    if value and not info["known"]:
        raise ValidationError(
            f"unknown language code: {value}",
            hint="run `creator captions languages` to list supported codes",
        )
    info.update({"source": source, "label": label})
    return info


def run(ctx: CliContext, args) -> int:
    if getattr(args, "captions_command", None) == "languages":
        payload = {
            "command": "captions languages",
            "ok": True,
            "count": len(languages.LANGUAGES),
            "languages": languages.list_languages(),
            "note": "Codes are a fixed catalog; unknown codes are never silently accepted.",
        }
        if ctx.json_mode:
            emit_json(payload)
        else:
            _print_languages(payload)
        return 0

    ws = ensure_workspace(ctx.workspace)
    project = sanitize_filename(args.project)
    edl = load_edl(ws, project)
    cues = formats.cues_from_edl(edl)
    if not cues:
        raise ValidationError(
            "the timeline has no valid caption cues",
            hint="create an edit from footage with a transcript first",
        )

    profile = ProfileManager(ws).load() if ProfileManager(ws).exists() else {}
    if args.lang:
        source_lang = _resolve_language(args.lang, source="cli.--lang", label="declared")
        source_lang["note"] = "Creator-specified source caption language."
    else:
        source_lang = languages.detect_language(
            profile, (edl.get("provenance") or {}).get("transcript")
        )

    # Validate the target *before* it can reach a remote provider prompt (M6).
    target_code = languages.normalize_language(args.to_lang or "en")
    if target_code is None:
        raise ValidationError(
            f"unknown target language code: {args.to_lang}",
            hint="run `creator captions languages` to list supported codes",
        )

    if args.translations:
        # Every file path must stay inside the workspace, including config-supplied ones (H1).
        provider = translations.FileTranslator(resolve_within(ws.base, args.translations))
    elif args.translate:
        config = load_config(ws)
        translation_cfg = (config.get("providers") or {}).get("translation") or {}
        if str(translation_cfg.get("kind", "")).lower() == "file" and translation_cfg.get("path"):
            translation_cfg = dict(translation_cfg)
            translation_cfg["path"] = str(resolve_within(ws.base, translation_cfg["path"]))
            config = dict(config)
            config["providers"] = dict(config["providers"])
            config["providers"]["translation"] = translation_cfg
        provider = translations.get_provider(config)
        if not provider.available():
            raise ProviderUnavailableError(
                "no translation provider is configured or usable",
                hint="use --translations FILE, or configure providers.translation in config.json",
            )
    else:
        provider = translations.NullTranslator()

    result = provider.translate(
        cues, target_language=target_code, source_language=source_lang.get("code")
    )
    translated = formats.validate_cues(result.get("cues") or [])
    final_cues = translated or cues
    if translated:
        # The language code is creator-declared; the translation *text* label is separate and
        # remains in `translation.label`. Never conflate them (L9).
        output_lang = _resolve_language(
            target_code,
            source="cli.--to-lang" if args.to_lang else result.get("source") or provider.name,
            label="declared" if args.to_lang else source_lang.get("label", "declared"),
        )
        output_lang["note"] = result["note"]
    else:
        output_lang = source_lang

    out_dir = (
        resolve_within(ws.base, args.out)
        if args.out
        else resolve_within(ws.base, "exports") / project
    )
    path = formats.export_captions(
        out_dir, final_cues, format=args.format, language=output_lang, stem=f"{project}_captions"
    )
    summary = formats.cue_summary(final_cues, language=output_lang)
    payload = {
        "command": "captions export",
        "ok": True,
        "project": project,
        "format": args.format,
        "path": str(path),
        "cue_count": summary["cue_count"],
        "caption_seconds": summary["caption_seconds"],
        "language": output_lang,
        "translation": result,
        "rtl": summary["rtl"],
        "note": (
            result["note"]
            if translated
            else "No translation was applied; captions use the source transcript language."
        ),
    }
    AuditLog(ws.root).log(
        "captions.export",
        project=project,
        format=args.format,
        cue_count=summary["cue_count"],
        translated=bool(translated),
        provider=result.get("provider"),
    )
    if ctx.json_mode:
        emit_json(payload)
    else:
        _print_export(payload)
    return 0
