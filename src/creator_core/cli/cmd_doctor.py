"""`creator doctor` — environment preflight (Python, FFmpeg/FFprobe, optional deps)."""

from __future__ import annotations

import importlib.util
import sys

from creator_core.cli.errors import EXIT_MISSING_DEPENDENCY, EXIT_OK
from creator_core.cli.main import CliContext, emit_json
from creator_core.media.ffmpeg import locate

REQUIRED_PYTHON = (3, 11)

OPTIONAL_MODULES = {
    "transcribe": ("faster_whisper", "pip install 'universal-creator-agent[transcribe]'"),
    "youtube": ("googleapiclient", "pip install 'universal-creator-agent[youtube]'"),
    "keyring": ("keyring", "pip install 'universal-creator-agent[youtube]'"),
}


def _module_check(spec_key: str) -> dict:
    module, hint = OPTIONAL_MODULES[spec_key]
    return {"module": module, "ok": importlib.util.find_spec(module) is not None, "hint": hint}


def run(ctx: CliContext, args) -> int:
    checks: dict = {}

    py_ok = sys.version_info[:2] >= REQUIRED_PYTHON
    checks["python"] = {
        "ok": py_ok,
        "version": ".".join(map(str, sys.version_info[:3])),
        "required": ">=" + ".".join(map(str, REQUIRED_PYTHON)),
    }

    for tool in ("ffmpeg", "ffprobe"):
        path, version = locate(tool)
        checks[tool] = {
            "ok": path is not None,
            "path": str(path) if path else None,
            "version": version,
            "hint": None
            if path
            else (
                "install FFmpeg (winget install Gyan.FFmpeg — or choco/brew/apt) "
                "or set CREATOR_FFMPEG / CREATOR_FFPROBE"
            ),
        }

    checks["requests"] = {
        "ok": importlib.util.find_spec("requests") is not None,
        "hint": "pip install requests",
    }

    checks["optional"] = {key: _module_check(key) for key in OPTIONAL_MODULES}

    ws_report = {"checked": False}
    if getattr(args, "check_workspace", False):
        exists = (ctx.workspace / ".creator").is_dir()
        ws_report = {
            "checked": True,
            "path": str(ctx.workspace / ".creator"),
            "ok": exists,
            "hint": None if exists else "run `creator init`",
        }

    required_ok = (
        checks["python"]["ok"]
        and checks["ffmpeg"]["ok"]
        and checks["ffprobe"]["ok"]
        and checks["requests"]["ok"]
    )
    payload = {"command": "doctor", "ok": required_ok, "checks": checks, "workspace": ws_report}

    if ctx.json_mode:
        emit_json(payload)
    else:
        _print_human(payload)
    return EXIT_OK if required_ok else EXIT_MISSING_DEPENDENCY


def _print_human(payload: dict) -> None:
    checks = payload["checks"]
    print(f"creator doctor — overall: {'OK' if payload['ok'] else 'MISSING DEPENDENCIES'}")
    for name in ("python", "ffmpeg", "ffprobe", "requests"):
        c = checks[name]
        status = "ok" if c["ok"] else "MISSING"
        extra = f" ({c.get('version') or c.get('path') or ''})".rstrip(" ()")
        print(f"  {name:10} {status}{extra}")
    for key, c in checks["optional"].items():
        print(
            f"  optional   {key:10} {'installed' if c['ok'] else 'not installed'}  [{c['module']}]"
        )
    ws = payload["workspace"]
    if ws.get("checked"):
        print(f"  workspace  {'ok' if ws['ok'] else 'missing'}  {ws['path']}")
    if not payload["ok"]:
        for name in ("python", "ffmpeg", "ffprobe", "requests"):
            c = checks[name]
            if not c["ok"] and c.get("hint"):
                print(f"  hint: {name}: {c['hint']}")
