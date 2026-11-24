"""Read-only MCP JSON-RPC 2.0 stdio server (optional integration, Phase 10).

Design constraints (adversarial-first):
  * One fixed workspace, chosen when the server starts. Tool arguments never select a base path.
  * Curated, read-only tools only. No shell, no file writes, no network, no arbitrary paths.
  * Strict argument validation; unknown fields are rejected so a client cannot smuggle instructions.
  * Recursive redaction of every outgoing string (API keys, tokens, secrets).
  * Protocol errors are structured JSON-RPC errors; tool failures are `isError: true` results.
  * The CLI is the authority: this adapter only *reads* state the CLI already produced.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from creator_core import __version__ as CORE_VERSION
from creator_core.cli.errors import CreatorError
from creator_core.security.paths import sanitize_filename
from creator_core.security.secrets import redact_text

PROTOCOL_VERSION = "2025-06-18"
MAX_MESSAGE_CHARS = 1_000_000  # a single JSON-RPC request never legitimately needs more (M4)

# JSON-RPC 2.0 standard codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_TOOL_FNS: dict[str, Callable[[Path, dict], dict]] = {}


class McpError(Exception):
    """A structured JSON-RPC protocol error returned to the client."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _tool(name: str, schema: dict):
    """Register a curated tool implementation with its JSON schema."""

    def decorator(fn: Callable[[Path, dict], dict]):
        _TOOL_FNS[name] = fn
        fn.input_schema = schema  # type: ignore[attr-defined]
        return fn

    return decorator


def _require_args(args: dict, *, allowed: set[str], required: set[str] | None = None) -> None:
    if not isinstance(args, dict):
        raise McpError(INVALID_PARAMS, "tool arguments must be an object")
    unknown = set(args) - allowed
    if unknown:
        raise McpError(INVALID_PARAMS, f"unexpected tool arguments: {', '.join(sorted(unknown))}")
    missing = (required or set()) - set(args)
    if missing:
        raise McpError(
            INVALID_PARAMS, f"missing required tool arguments: {', '.join(sorted(missing))}"
        )


# --- Curated read-only tool implementations -------------------------------------------------


@_tool("health", {"type": "object", "properties": {}, "additionalProperties": False})
def _health(workspace: Path, args: dict) -> dict:
    _require_args(args, allowed=set())
    from creator_core.media.ffmpeg import locate as locate_ffmpeg

    ffmpeg_path, _ = locate_ffmpeg("ffmpeg")
    return {
        "ok": True,
        "name": "universal-creator-agent",
        "version": CORE_VERSION,
        "workspace_ready": (workspace / ".creator").is_dir(),
        "ffmpeg_available": ffmpeg_path is not None,
        "note": "MCP adapter is read-only; use the creator CLI for all mutations.",
    }


def _workspace_paths(workspace: Path):
    """Return WorkspacePaths without requiring an initialized workspace (read-only)."""
    from creator_core.workspace.layout import WorkspacePaths, workspace_path_for

    root = workspace_path_for(workspace)
    return WorkspacePaths(
        base=Path(workspace),
        root=root,
        config=root / "config.json",
        profiles=root / "profiles",
        projects=root / "projects",
        research=root / "research",
        analytics=root / "analytics",
        publishing=root / "publishing",
        logs=root / "logs",
        tmp=root / "tmp",
    )


@_tool("workspace_status", {"type": "object", "properties": {}, "additionalProperties": False})
def _workspace_status(workspace: Path, args: dict) -> dict:
    _require_args(args, allowed=set())
    ws = _workspace_paths(workspace)  # read-only; never creates anything
    projects = (
        sorted(p.name for p in ws.projects.iterdir() if p.is_dir()) if ws.projects.is_dir() else []
    )
    snapshots = (
        sorted(p.name for p in (ws.analytics / "snapshots").glob("snapshot_*.json"))
        if (ws.analytics / "snapshots").is_dir()
        else []
    )
    reports = (
        sorted(p.name for p in (ws.analytics / "reports").glob("report_*.md"))
        if (ws.analytics / "reports").is_dir()
        else []
    )
    return {
        "ok": True,
        "workspace": str(ws.base),
        "initialized": (ws.root).is_dir(),
        "config_present": ws.config.is_file(),
        "projects": projects[:50],
        "project_count": len(projects),
        "analytics_snapshot_count": len(snapshots),
        "analytics_report_count": len(reports),
    }


@_tool("profile_show", {"type": "object", "properties": {}, "additionalProperties": False})
def _profile_show(workspace: Path, args: dict) -> dict:
    _require_args(args, allowed=set())
    from creator_core.profiles.profile import ProfileManager

    pm = ProfileManager(_workspace_paths(workspace))
    if not pm.exists():
        return {"ok": True, "exists": False, "note": "No creator profile has been created."}
    return {
        "ok": True,
        "exists": True,
        "profile": pm.load(),
        "note": "Profile memory is creator-authored context, not verified audience data.",
    }


@_tool("list_strategies", {"type": "object", "properties": {}, "additionalProperties": False})
def _list_strategies(workspace: Path, args: dict) -> dict:
    _require_args(args, allowed=set())
    from creator_core.editing.strategies import list_strategies

    return {"ok": True, "strategies": list_strategies()}


@_tool("list_languages", {"type": "object", "properties": {}, "additionalProperties": False})
def _list_languages(workspace: Path, args: dict) -> dict:
    _require_args(args, allowed=set())
    from creator_core.captions.language import list_languages

    return {"ok": True, "languages": list_languages()}


@_tool("analytics_status", {"type": "object", "properties": {}, "additionalProperties": False})
def _analytics_status(workspace: Path, args: dict) -> dict:
    _require_args(args, allowed=set())
    from creator_core.analytics import ingest, learning
    from creator_core.analytics import report as reporting

    ws = _workspace_paths(workspace)
    snapshot = ingest.latest_snapshot(ws)
    report = reporting.latest_report(ws)
    return {
        "ok": True,
        "snapshot": str(snapshot) if snapshot else None,
        "report": str(report) if report else None,
        "pending_changes": len(learning.pending(ws)),
        "note": "Fixtures are estimated; only youtube_analytics_api sources are verified.",
    }


@_tool(
    "analytics_report",
    {
        "type": "object",
        "properties": {
            "prior_snapshot": {
                "type": "string",
                "description": "optional workspace-relative prior snapshot path",
            },
        },
        "additionalProperties": False,
    },
)
def _analytics_report(workspace: Path, args: dict) -> dict:
    _require_args(args, allowed={"prior_snapshot"})
    from creator_core.analytics import ingest
    from creator_core.analytics import report as reporting
    from creator_core.profiles.profile import ProfileManager
    from creator_core.security.paths import resolve_within

    ws = _workspace_paths(workspace)
    snapshot_path = ingest.latest_snapshot(ws)
    if not snapshot_path:
        return {
            "ok": False,
            "note": "No analytics snapshot found; run `creator analytics refresh`.",
        }
    snapshot = ingest.load_snapshot(snapshot_path)
    prior = None
    if args.get("prior_snapshot"):
        prior = ingest.load_snapshot(resolve_within(ws.base, args["prior_snapshot"]))
    profile = ProfileManager(ws).load() if ProfileManager(ws).exists() else None
    data = reporting.build(snapshot, prior=prior, profile=profile)
    return {
        "ok": True,
        "report": data,
        "note": "Correlation only; not causal or predictive. No data was written.",
    }


@_tool(
    "media_search",
    {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "project id with a media index"},
            "query": {"type": "string", "description": "search terms"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["project", "query"],
        "additionalProperties": False,
    },
)
def _media_search(workspace: Path, args: dict) -> dict:
    _require_args(args, allowed={"project", "query", "limit"}, required={"project", "query"})
    project = args["project"]
    query = args["query"]
    if not isinstance(project, str) or not isinstance(query, str):
        raise McpError(INVALID_PARAMS, "project and query must be strings")
    project = sanitize_filename(project)
    if not project or project != args["project"]:
        raise McpError(INVALID_PARAMS, "project id contains invalid characters")
    limit = args.get("limit", 10)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise McpError(INVALID_PARAMS, "limit must be an integer between 1 and 50")
    from creator_core.media.index import load_index, search

    hits = search(load_index(_workspace_paths(workspace), project), query, limit=limit)
    return {
        "ok": True,
        "project": project,
        "query": query,
        "hits": hits,
        "note": "Search results are hypotheses derived from indexed signals.",
    }


TOOL_DEFINITIONS: list[dict] = [
    {
        "name": name,
        "description": (fn.__doc__ or name).strip(),
        "inputSchema": getattr(fn, "input_schema", {"type": "object"}),
    }
    for name, fn in sorted(_TOOL_FNS.items())
]


def _redact(value: Any) -> Any:
    """Recursively redact every outgoing string; never mutate the source object."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        # Creator-editable dicts can carry secret-shaped *keys* too, not just values (M3).
        return {redact_text(str(k)): _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def _ok(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": _redact(result)}


def _err(request_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": _redact(message)},
    }


def _initialize(request_id: Any, params: dict) -> dict:
    if params and not isinstance(params, dict):
        raise McpError(INVALID_PARAMS, "initialize params must be an object")
    return _ok(
        request_id,
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "universal-creator-agent", "version": CORE_VERSION},
            "instructions": (
                "Read-only creator workspace access. Use the `creator` CLI for all mutations. "
                "Research labels are verified/estimated/hypothesis; never claim guaranteed virality."
            ),
        },
    )


def _call_tool(request_id: Any, params: dict, workspace: Path) -> dict:
    if not isinstance(params, dict):
        raise McpError(INVALID_PARAMS, "tools/call params must be an object")
    unknown = set(params) - {"name", "arguments"}
    if unknown:
        raise McpError(
            INVALID_PARAMS, f"unexpected tools/call params: {', '.join(sorted(map(str, unknown)))}"
        )
    name = params.get("name")
    if not isinstance(name, str) or name not in _TOOL_FNS:
        # The method exists; it is the tool *name* that is invalid (M8).
        raise McpError(INVALID_PARAMS, f"unknown tool: {name}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise McpError(INVALID_PARAMS, "tool arguments must be an object")
    try:
        result = _TOOL_FNS[name](workspace, args)
    except McpError:
        raise
    except CreatorError as exc:
        return _ok(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"ok": False, "error": exc.payload()["error"]}, default=str
                        ),
                    }
                ],
                "isError": True,
            },
        )
    except Exception as exc:  # never leak tracebacks or secrets to a client  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
        return _ok(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "ok": False,
                                "error": {
                                    "code": "tool_error",
                                    "message": f"{type(exc).__name__}: tool failed",
                                },
                            }
                        ),
                    }
                ],
                "isError": True,
            },
        )
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    return _ok(
        request_id,
        {"content": [{"type": "text", "text": text}], "isError": result.get("ok") is False},
    )


def handle_request(message: str | dict, workspace: Path) -> dict | None:
    """Handle one JSON-RPC message. Returns None for notifications (no response)."""
    if isinstance(message, str):
        if len(message) > MAX_MESSAGE_CHARS:
            return _err(None, INVALID_REQUEST, "request exceeds the maximum allowed size")
        try:
            request = json.loads(message)
        except (ValueError, TypeError, RecursionError):
            return _err(None, PARSE_ERROR, "parse error: invalid JSON")
    else:
        request = message
    if (
        not isinstance(request, dict)
        or request.get("jsonrpc") != "2.0"
        or not isinstance(request.get("method"), str)
    ):
        return _err(None, INVALID_REQUEST, "invalid JSON-RPC 2.0 request")
    method = request["method"]
    request_id = request.get("id")
    # Notifications by definition never receive a response, even if a client sends an id (L10).
    if method.startswith("notifications/"):
        return None
    try:
        if method == "initialize":
            response = _initialize(request_id, request.get("params") or {})
        elif method == "ping":
            if request.get("params"):
                raise McpError(INVALID_PARAMS, "ping takes no parameters")
            response = _ok(request_id, {})
        elif method == "tools/list":
            if request.get("params"):
                raise McpError(INVALID_PARAMS, "tools/list takes no parameters")
            response = _ok(request_id, {"tools": TOOL_DEFINITIONS})
        elif method == "tools/call":
            response = _call_tool(request_id, request.get("params") or {}, workspace)
        else:
            raise McpError(METHOD_NOT_FOUND, f"method not found: {method}")
    except McpError as exc:
        response = _err(request_id, exc.code, exc.message)
    return response


def serve(workspace: Path, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Blocking newline-delimited JSON-RPC loop over stdio. Returns 0 on clean EOF."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        if len(line) > MAX_MESSAGE_CHARS:  # bound memory before parsing (M4)
            response = _err(None, INVALID_REQUEST, "request exceeds the maximum allowed size")
            stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            stdout.flush()
            continue
        response = handle_request(line, Path(workspace))
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        stdout.flush()
    return 0
