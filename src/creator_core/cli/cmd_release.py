"""`creator release …` — local packaging checks, source manifest, sdist/wheel build (P10).

No command here uploads, publishes or contacts a package index. Artifacts are local only.
"""

from __future__ import annotations

from creator_core.cli.errors import ValidationError
from creator_core.cli.main import CliContext, emit_json
from creator_core.release import build as release_build
from creator_core.release import checker


def _print_check(p: dict) -> None:
    print(f"release check — version {p['version'] or 'unknown'}, {'OK' if p['ok'] else 'FAILED'}")
    for source in p["versions"]["sources"]:
        mark = "ok" if source["version"] == p["version"] and not source["error"] else "MISMATCH"
        print(
            f"  {source['label']:<12} {source['version'] or source['error'] or 'missing':<14} {mark}"
        )
    if p["files"]["missing"]:
        print(f"  missing files: {', '.join(p['files']['missing'])}")
    artifacts = p["artifacts"]
    print(
        f"  wheel{' ' + artifacts['wheel'] if artifacts['wheel'] else '':<12} "
        f"{'OK' if artifacts['ok'] else 'STALE/MISSING'}"
    )
    print(
        f"  artifacts checked: {artifacts['checked']}, mismatches: "
        f"{artifacts.get('mismatch_count', len(artifacts['mismatches']))}"
    )
    print(f"  {artifacts['note']}")
    print(f"  {p['note']}")


def _print_manifest(p: dict) -> None:
    print(f"release manifest — {p['count']} source file(s) hashed")
    for item in p["files"][:8]:
        print(f"  {item['sha256'][:12]}  {item['path']}")
    if p["count"] > 8:
        print(f"  ... {p['count'] - 8} more")
    print("  " + p["note"])


def _print_build(p: dict) -> None:
    print(f"release build — {len(p['artifacts'])} artifact(s) in {p['out']}")
    for item in p["artifacts"]:
        print(f"  {item['name']}  sha256={item['sha256'][:16]}...")
    print(f"  manifest: {p['manifest']}")
    print("  " + p["note"])


def _emit(ctx: CliContext, payload: dict, printer, exit_code: int = 0) -> int:
    if ctx.json_mode:
        emit_json(payload)
    else:
        printer(payload)
    return exit_code


def run(ctx: CliContext, args) -> int:
    sub = args.release_command
    root = ctx.workspace

    if sub == "check":
        payload = checker.check(root)
        return _emit(ctx, payload, _print_check, 0 if payload["ok"] else 2)

    if sub == "manifest":
        payload = checker.file_manifest(root)
        return _emit(ctx, payload, _print_manifest, 0 if payload["ok"] else 2)

    if sub == "build":
        payload = release_build.build_artifacts(root, args.out)
        if ctx.json_mode:
            emit_json(payload)
        else:
            _print_build(payload)
        return 0

    raise ValidationError(f"unknown release subcommand: {sub}")
