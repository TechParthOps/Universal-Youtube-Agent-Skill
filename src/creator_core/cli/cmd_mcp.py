"""`creator mcp …` — optional read-only MCP JSON-RPC adapter (Phase 10).

The CLI remains authoritative. `creator mcp serve` is a stdio transport and is never used for
interactive output; `creator mcp tools` prints the curated tool catalog for inspection.
"""

from __future__ import annotations

from pathlib import Path

from creator_core.cli.main import CliContext, emit_json
from creator_core.mcp import TOOL_DEFINITIONS, serve
from creator_core.mcp.server import PROTOCOL_VERSION
from creator_core.workspace.layout import ensure_workspace


def _print_tools(payload: dict) -> None:
    print(
        f"mcp tools — {payload['count']} read-only tool(s) (protocol {payload['protocol_version']})"
    )
    for tool in payload["tools"]:
        print(f"  {tool['name']:<18} {tool['description'].splitlines()[0]}")
    print("  All tools are read-only and scoped to one workspace; use the creator CLI to mutate.")


def run(ctx: CliContext, args) -> int:
    if getattr(args, "mcp_command", None) == "serve":
        # Verify the workspace exists before blocking on stdio; all tools stay read-only.
        ensure_workspace(ctx.workspace)
        return serve(Path(ctx.workspace))

    payload = {
        "command": "mcp tools",
        "ok": True,
        "protocol_version": PROTOCOL_VERSION,
        "count": len(TOOL_DEFINITIONS),
        "tools": TOOL_DEFINITIONS,
        "note": "Read-only, workspace-scoped MCP facade over the creator CLI contract.",
    }
    if ctx.json_mode:
        emit_json(payload)
    else:
        _print_tools(payload)
    return 0
