"""Secret handling: redaction filters + credential storage via the OS keyring.

Rules (ADR-011, security tests §5):
- Secrets are stored via the OS keyring when available (Windows Credential Manager/DPAPI…).
- A plaintext file fallback exists ONLY with explicit opt-in via the environment variable
  CREATOR_ALLOW_PLAINTEXT_CREDENTIALS=1 — warned and audit-logged each time it is used.
- `redact_text`/`redact_mapping` are applied by the audit log and CLI error emitter so
  tokens never reach logs, JSON output, or LLM prompts.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from creator_core.cli.errors import MissingDependencyError, WorkspaceIOError

SECRET_ENV_OPTIN = "CREATOR_ALLOW_PLAINTEXT_CREDENTIALS"

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ya29\.[A-Za-z0-9_\-]+"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{16,}"),
    re.compile(
        r"(?i)[\"']?(access_token|refresh_token|id_token|client_secret|api_key|password)"
        r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.]{8,}"
    ),
]

_REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    """Replace token-shaped substrings with [REDACTED]. Applied before anything is logged."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(_REDACTED, out)
    return out


def _redact_value(value):
    """Recursively redact strings inside mappings, lists and tuples without mutating input."""
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_mapping(mapping: dict) -> dict:
    """Deep-redact every string value, including those nested in lists/tuples (S-06)."""
    return {key: _redact_value(value) for key, value in mapping.items()}


def _keyring_module():
    import importlib.util

    if importlib.util.find_spec("keyring") is None:
        raise MissingDependencyError(
            "keyring is required for credential storage",
            hint="pip install 'universal-creator-agent[youtube]'  (installs keyring)",
        )
    import keyring

    return keyring


_SERVICE = "universal-creator-agent"


def store_secret(key: str, value: str) -> str:
    """Store `key`→`value` in the OS keyring. Returns the storage backend used."""
    kr = _keyring_module()
    try:
        kr.set_password(_SERVICE, key, value)
        return "keyring"
    except Exception as exc:  # keyring backend failures (headless, no daemon…)
        if os.environ.get(SECRET_ENV_OPTIN) != "1":
            raise WorkspaceIOError(
                f"keyring backend failed ({exc.__class__.__name__}); refusing to store a secret "
                "in plaintext without explicit opt-in",
                hint=f"set {SECRET_ENV_OPTIN}=1 to allow an encrypted-at-rest-effort plaintext "
                "file fallback (not recommended), or configure a working keyring backend",
            ) from exc
    return _store_plaintext(key, value)


def get_secret(key: str) -> str | None:
    kr = _keyring_module()
    try:
        value = kr.get_password(_SERVICE, key)
    except Exception:  # noqa: BLE001 - any keyring backend error means the secret is unavailable
        value = None
    if value is not None:
        return value
    if os.environ.get(SECRET_ENV_OPTIN) == "1":
        return _load_plaintext().get(key)
    return None


def _plaintext_path() -> Path:
    # Lives under the user profile, never inside a project, never committed.
    base = Path(os.environ.get("CREATOR_HOME", Path.home() / ".creator")).expanduser()
    return base / "secrets" / "plaintext.json"


def _store_plaintext(key: str, value: str) -> str:
    path = _plaintext_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_plaintext()
    data[key] = value
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - POSIX semantics on some filesystems
            pass
    except OSError as exc:
        raise WorkspaceIOError(f"could not write plaintext secret store: {exc}") from exc
    return "plaintext-file"


def _load_plaintext() -> dict:
    path = _plaintext_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
