"""Optional MCP (Model Context Protocol) adapter.

The `creator` CLI remains the authoritative, portable execution contract. This adapter is a thin,
read-only JSON-RPC 2.0 stdio facade over the same core functions — it cannot execute shell commands,
write files, or read arbitrary paths. Every request is scoped to one fixed workspace chosen at
server start, and every response is redacted before it leaves the process.
"""

from creator_core.mcp.server import TOOL_DEFINITIONS, handle_request, serve

__all__ = ["TOOL_DEFINITIONS", "handle_request", "serve"]
