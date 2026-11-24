"""Loopback-only preview server: project APIs, HTTP Range video, revision/render triggers.

Security posture (ADR-010):
- Binds to 127.0.0.1 only; a non-loopback host is rejected before bind.
- Serves only files under the selected project directory, plus a fixed static UI directory.
- No shell, eval or template rendering. All JSON keys are allowlisted.
- POST bodies are size-capped; revision text is sanitized before it is stored or echoed.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from creator_core.cli.errors import ValidationError
from creator_core.editing.edl import list_versions, load_edl
from creator_core.editing.edl import summary as edl_summary
from creator_core.editing.render import render, render_history
from creator_core.editing.revisions import (
    list_pending,
    normalize_request,
    parse_request,
    save_pending,
)
from creator_core.media.ingest import validate_media_path
from creator_core.security.audit import AuditLog
from creator_core.security.paths import resolve_within
from creator_core.workspace.layout import WorkspacePaths, load_config, project_dir

MAX_POST_BYTES = 64 * 1024
# Above the application body limit but below this ceiling we fully consume the
# announced body so a well-behaved client still receives a clean 4xx instead of a
# TCP reset caused by unread bytes left in the keep-alive stream.
MAX_DRAIN_BYTES = 8 * 1024 * 1024
RANGE_CHUNK = 256 * 1024
STATIC_DIR = Path(__file__).resolve().parents[3] / "web" / "preview"
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(host: str) -> bool:
    return host.lower() in _LOOPBACK


def _json_bytes(payload: dict, status: int = 200) -> tuple[int, bytes, str]:
    return (
        status,
        json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def _error(message: str, status: int = 400, *, hint: str | None = None) -> tuple[int, bytes, str]:
    return _json_bytes(
        {"ok": False, "error": {"code": "preview_error", "message": message, "hint": hint}}, status
    )


def _renders_for_version(pdir: Path, version: int | None) -> list[dict]:
    history = render_history(pdir)
    if version is not None:
        history = [r for r in history if r["timeline_version"] == version]
    return history


def _qa_path_for(output: str) -> Path | None:
    path = Path(output)
    qa = path.with_name(path.stem + "_qa.json")
    return qa if qa.is_file() else None


def make_handler(ws: WorkspacePaths, project_id: str):
    """Build a request handler bound to one workspace/project."""
    pdir = project_dir(ws, project_id)
    audit = AuditLog(ws.root)

    class PreviewHandler(BaseHTTPRequestHandler):
        server_version = "CreatorPreview/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            # Keep the terminal quiet; mutating requests are recorded in the audit log instead.
            return

        # ---- response helpers -------------------------------------------------
        def _send(
            self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)

        def _json(self, result: tuple[int, bytes, str]) -> None:
            code, body, ctype = result
            self._send(code, body, ctype)

        def _check_headers(self) -> None:
            """Reject DNS-rebinding / cross-origin mutating requests (MEDIUM audit fix)."""
            allowed_hosts = {"127.0.0.1", "localhost", "::1"}
            host = (self.headers.get("Host") or "").strip()
            host_name = host.rsplit(":", 1)[0].strip("[]").lower().rstrip(".")
            if host and host_name not in allowed_hosts:
                raise ValidationError("Host header is not allowed for the loopback preview")
            origin = self.headers.get("Origin")
            if origin:
                origin_host = (urlsplit(origin).hostname or "").lower().rstrip(".")
                if origin_host not in allowed_hosts:
                    raise ValidationError("Origin header is not allowed for the loopback preview")
            if self.command in ("POST", "PUT", "PATCH", "DELETE"):
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    raise ValidationError(
                        "mutating requests require Content-Type: application/json"
                    )

        def _drain_body(self, length: int) -> None:
            """Consume a request body we refuse to process so the socket stays usable.

            Without draining, leftover bytes remain in the keep-alive stream and the
            next request loop misreads them as a new request line, which resets the
            connection and hides our 4xx response from the client (observed on Windows
            as WinError 10053). If the peer stops sending early we drop the connection.
            """
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    self.close_connection = True
                    break
                remaining -= len(chunk)

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                raise ValidationError("invalid Content-Length")
            if length > MAX_POST_BYTES:
                # Reject the body, but keep the connection clean so the client gets
                # a real HTTP error instead of an aborted TCP connection.
                if length <= MAX_DRAIN_BYTES:
                    self._drain_body(length)
                else:
                    self.close_connection = True
                raise ValidationError(f"POST body must be 1–{MAX_POST_BYTES} bytes")
            if length <= 0:
                raise ValidationError(f"POST body must be 1–{MAX_POST_BYTES} bytes")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError(f"POST body is not valid JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise ValidationError("POST body must be a JSON object")
            return data

        def _query(self) -> dict[str, list[str]]:
            return parse_qs(urlsplit(self.path).query, keep_blank_values=False)

        def _first(self, key: str, default: str | None = None) -> str | None:
            values = self._query().get(key)
            return values[0] if values else default

        def _version(self) -> int | None:
            raw = self._first("version")
            if raw is None:
                return None
            if not raw.isdigit():
                raise ValidationError("version must be a positive integer")
            return int(raw)

        # ---- API --------------------------------------------------------------
        def api_project(self) -> tuple[int, bytes, str]:
            versions = list_versions(ws, project_id)
            latest = load_edl(ws, project_id) if versions else None
            return _json_bytes(
                {
                    "ok": True,
                    "project_id": project_id,
                    "latest_version": max((v["version"] for v in versions), default=None),
                    "summary": edl_summary(latest) if latest else None,
                    "versions": versions,
                    "renders": render_history(pdir),
                }
            )

        def api_timeline(self) -> tuple[int, bytes, str]:
            edl = load_edl(ws, project_id, self._version())
            return _json_bytes({"ok": True, "edl": edl, "summary": edl_summary(edl)})

        def api_qa(self) -> tuple[int, bytes, str]:
            version = self._version()
            renders = _renders_for_version(pdir, version)
            if not renders:
                return _error(
                    "no render found for this version",
                    404,
                    hint="run `creator edit render <project>` first",
                )
            render_no = self._first("render")
            chosen = next((r for r in renders if str(r["render_number"]) == render_no), renders[-1])
            path = _qa_path_for(chosen["output"])
            if path is None:
                return _error("QA report not found", 404)
            return 200, path.read_bytes(), "application/json; charset=utf-8"

        def api_exports(self) -> tuple[int, bytes, str]:
            exports = []
            for manifest in sorted((pdir / "renders").glob("*_manifest.json")):
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                exports.append(
                    {
                        "manifest": str(manifest),
                        "output": data.get("output"),
                        "timeline_version": data.get("timeline_version"),
                        "render_number": data.get("render_number"),
                        "qa_ok": data.get("qa_ok"),
                        "ready_for_export": bool(data.get("qa_ok")),
                        "note": "A/V sync still needs human review before publishing.",
                    }
                )
            return _json_bytes({"ok": True, "exports": exports})

        def api_revisions_get(self) -> tuple[int, bytes, str]:
            return _json_bytes({"ok": True, "revisions": list_pending(ws, project_id)})

        def api_revisions_post(self) -> tuple[int, bytes, str]:
            data = self._read_json()
            request_text = normalize_request(str(data.get("request") or ""))
            version = self._version() or data.get("version")
            parent = load_edl(ws, project_id, int(version) if version else None)
            plan = parse_request(request_text, parent)
            saved = save_pending(ws, project_id, plan)
            audit.log(
                "preview.revision.requested",
                project_id=project_id,
                intent=plan["intent"],
                revision_id=saved["id"],
            )
            return _json_bytes({"ok": True, "revision": saved, "question": plan["questions"]}, 201)

        def api_render_post(self) -> tuple[int, bytes, str]:
            data = self._read_json() if self.headers.get("Content-Length") else {}
            raw_version = data.get("version") or self._first("version")
            version = int(raw_version) if raw_version and str(raw_version).isdigit() else None
            edl = load_edl(ws, project_id, version)
            actual = version or max(v["version"] for v in list_versions(ws, project_id))
            result = render(ws, edl, project=pdir, version=actual, config=load_config(ws))
            audit.log(
                "preview.render.triggered",
                project_id=project_id,
                version=actual,
                qa_ok=result["qa_ok"],
            )
            return _json_bytes(result, 201)

        def _serve_file(self, path: Path, content_type: str) -> None:
            if not path.is_file():
                self._send(404, b'{"error":"not found"}', "application/json")
                return
            size = path.stat().st_size
            start, end = 0, size - 1
            extra: dict[str, str] = {}
            header = self.headers.get("Range")
            if header:
                match = _RANGE_RE.fullmatch(header.strip())
                if not match or (not match.group(1) and not match.group(2)):
                    self._send(
                        416,
                        b'{"error":"unsatisfiable range"}',
                        "application/json",
                        {"Content-Range": f"bytes */{size}"},
                    )
                    return
                if match.group(1):  # bytes=start[-end]
                    start = int(match.group(1))
                    if match.group(2):
                        end = int(match.group(2))
                    if start >= size or start > end:
                        self._send(
                            416,
                            b'{"error":"unsatisfiable range"}',
                            "application/json",
                            {"Content-Range": f"bytes */{size}"},
                        )
                        return
                    end = min(end, size - 1)
                else:  # bytes=-suffix
                    suffix = int(match.group(2))
                    if suffix <= 0:
                        self._send(
                            416,
                            b'{"error":"unsatisfiable range"}',
                            "application/json",
                            {"Content-Range": f"bytes */{size}"},
                        )
                        return
                    start = max(0, size - suffix)
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            status = 206 if header else 200
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = fh.read(min(RANGE_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def api_video(self) -> None:
            renders = _renders_for_version(pdir, self._version())
            render_no = self._first("render")
            if render_no:
                renders = [r for r in renders if str(r["render_number"]) == render_no]
            if not renders:
                self._send(404, b'{"error":"no render found"}', "application/json")
                return
            path = resolve_within(pdir, renders[-1]["output"])
            self._serve_file(path, "video/mp4")

        def api_source(self) -> None:
            source_id = self._first("id", "src1")
            edl = load_edl(ws, project_id)
            src = next((s for s in edl["sources"] if s["id"] == source_id), None)
            if src is None:
                self._send(404, b'{"error":"unknown source id"}', "application/json")
                return
            self._serve_file(validate_media_path(src["path"], ws), "video/mp4")

        def api_static(self, name: str) -> None:
            allowed = {
                "index.html": "text/html; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8",
                "style.css": "text/css; charset=utf-8",
            }
            if name not in allowed:
                self._send(404, b'{"error":"not found"}', "application/json")
                return
            self._serve_file(STATIC_DIR / name, allowed[name])

        # ---- routing ----------------------------------------------------------
        def _route(self) -> None:
            parts = urlsplit(self.path)
            path = unquote(parts.path).rstrip("/") or "/"
            try:
                self._check_headers()
                if path == "/" and self.command in ("GET", "HEAD"):
                    return self.api_static("index.html")
                if path in ("/app.js", "/style.css") and self.command in ("GET", "HEAD"):
                    return self.api_static(path.lstrip("/"))
                if path == "/api/project" and self.command in ("GET", "HEAD"):
                    return self._json(self.api_project())
                if path == "/api/timeline" and self.command in ("GET", "HEAD"):
                    return self._json(self.api_timeline())
                if path == "/api/qa" and self.command in ("GET", "HEAD"):
                    return self._send(*self.api_qa())
                if path == "/api/exports" and self.command in ("GET", "HEAD"):
                    return self._json(self.api_exports())
                if path == "/api/revisions":
                    if self.command in ("GET", "HEAD"):
                        return self._json(self.api_revisions_get())
                    if self.command == "POST":
                        return self._json(self.api_revisions_post())
                if path == "/api/render" and self.command == "POST":
                    return self._json(self.api_render_post())
                if path == "/api/video" and self.command in ("GET", "HEAD"):
                    return self.api_video()
                if path == "/api/video/source" and self.command in ("GET", "HEAD"):
                    return self.api_source()
                return self._json(_error("unknown endpoint", 404))
            except ValidationError as exc:
                return self._json(_error(exc.message, 400, hint=exc.hint))
            except Exception as exc:  # never leak a traceback to the browser  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
                return self._json(
                    _error(
                        f"preview error: {exc.__class__.__name__}",
                        500,
                        hint="rerun the CLI with --debug for details",
                    )
                )

        def do_GET(self) -> None:
            self._route()

        def do_HEAD(self) -> None:
            self._route()

        def do_POST(self) -> None:
            self._route()

    return PreviewHandler


class PreviewServer:
    """Start/stop a loopback server. `port=0` selects an ephemeral port (used in tests)."""

    def __init__(
        self, ws: WorkspacePaths, project_id: str, *, host: str = "127.0.0.1", port: int = 8765
    ):
        if not _is_loopback(host):
            raise ValidationError(
                "preview server must bind to a loopback address", hint="use 127.0.0.1 (the default)"
            )
        self.ws, self.project_id, self.host, self.port = ws, project_id, host, port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("server is not running")
        return f"http://{self.host}:{self._server.server_address[1]}/"

    def start(self) -> str:
        handler = make_handler(self.ws, self.project_id)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="creator-preview"
        )
        self._thread.start()
        return self.url

    def wait(self) -> None:
        """Block until the server is stopped (Ctrl+C in the CLI)."""
        if self._thread is not None:
            self._thread.join()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def serve_forever(self) -> None:
        handler = make_handler(self.ws, self.project_id)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        try:
            self._server.serve_forever()
        finally:
            self._server.server_close()
            self._server = None
