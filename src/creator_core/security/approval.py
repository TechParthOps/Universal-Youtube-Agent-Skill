"""Explicit authorization tokens (HMAC) for state-changing actions.

Used for profile-memory changes, Short approvals and YouTube publish/upload/thumbnail authorizations.
`--approve` in the CLI creates a fresh short-lived token at the moment of the user's action;
`--approval-token TOKEN` accepts only a valid HMAC (forged tokens are rejected). Audit records
store only the token's id/fingerprint, never the token itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from creator_core.cli.errors import ApprovalRequiredError, CreatorError

DEFAULT_TTL_S = 15 * 60

# One-time-use ledger. Only nonce fingerprints are stored, never the token itself.
_USED_FILENAME = ".approval_used.json"
# S-05: the ledger is append-only. Trimming it re-opened a replay window once 1000 approvals
# had been recorded, so no marker is ever evicted; 24 bytes per approval is negligible.
_LOCK_TIMEOUT_S = 5.0
_LOCK_STALE_S = 30.0
_LOCK_POLL_S = 0.01


def _payload_digest(payload: dict | None) -> str | None:
    """Canonical hash of the full request a token authorises (HIGH audit fix)."""
    if payload is None:
        return None
    body = json.dumps(
        payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _used_path(ws) -> Path:
    return Path(ws.logs) / _USED_FILENAME


@contextmanager
def _nonce_lock(path: Path):
    """Cross-process O_EXCL lock with bounded wait and stale-lock recovery (S-05).

    `atomic_write_json` alone prevents torn writes but not a read/check/write race between two
    processes presenting the same token. A lock file created with O_CREAT|O_EXCL serialises the
    whole check-and-append sequence; a lock older than 30 s is treated as abandoned.
    """
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # O_EXCL contention: the lock existed at open time. The holder may unlink it
            # before we stat it — that is still contention, so retry; never re-raise
            # (re-raising here crashed racing verifiers with an unhandled OSError).
            pass
        except PermissionError:
            # Windows reports O_EXCL contention as PermissionError. Only treat it as lock
            # contention when the lock file actually exists; genuine directory permission
            # errors must still propagate instead of silently looping until the deadline.
            if not lock.exists():
                raise
        else:
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            break
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            age = 0.0
        if age > _LOCK_STALE_S:
            try:
                lock.unlink()
            except OSError:
                pass
            continue
        if time.monotonic() >= deadline:
            raise ApprovalError("approval ledger is busy", hint="retry the command in a moment")
        time.sleep(_LOCK_POLL_S)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _consume_once(ws, nonce: str) -> None:
    """Reject replayed tokens by recording their nonce fingerprint under an exclusive lock."""
    path = _used_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _nonce_lock(path):
        used: list[str] = []
        if path.is_file():
            try:
                used = json.loads(path.read_text(encoding="utf-8")).get("nonces", [])
            except (OSError, json.JSONDecodeError, AttributeError):
                used = []
        marker = hashlib.sha256(nonce.encode("ascii")).hexdigest()[:24]
        if marker in used:
            raise ApprovalError(
                "approval token has already been used", hint="run the command again with --approve"
            )
        from creator_core.workspace.atomic import atomic_write_json

        atomic_write_json(path, {"nonces": [*used, marker]})


class ApprovalError(CreatorError):
    exit_code = 5
    code = "approval_invalid"


def _key_path(ws) -> Path:
    return Path(ws.logs) / ".approval_key"


def get_or_create_key(ws) -> bytes:
    path = _key_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(secrets.token_bytes(32))
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - POSIX semantics
            pass
    return path.read_bytes()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def create_token(
    ws, action: str, target: str, *, ttl_seconds: int = DEFAULT_TTL_S, payload: dict | None = None
) -> str:
    """Create an HMAC token bound to action, target, expiry and the full request payload."""
    key = get_or_create_key(ws)
    claims = {
        "action": action,
        "target": target,
        "exp": int(time.time()) + ttl_seconds,
        "nonce": secrets.token_hex(8),
        "payload_sha256": _payload_digest(payload),
    }
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_token(
    ws, token: str, action: str, target: str, *, payload: dict | None = None, consume: bool = False
) -> dict:
    """Verify action/target/expiry/payload; raises ApprovalError for anything invalid.

    `consume=True` enforces one-time use for state-changing actions. It is opt-in so read-only
    verification tests and legacy callers keep working, while all publish paths use it.
    """
    if not isinstance(token, str) or token.count(".") != 1:
        raise ApprovalError("invalid approval token", hint="use --approve for a fresh token")
    body, signature = token.split(".", 1)
    expected = _b64(hmac.new(get_or_create_key(ws), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise ApprovalError(
            "approval token signature mismatch", hint="use --approve for a fresh token"
        )
    try:
        padded = body + "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception as exc:
        raise ApprovalError("approval token is unreadable") from exc
    if claims.get("action") != action or claims.get("target") != target:
        raise ApprovalError("approval token does not match this action/target")
    if claims.get("payload_sha256") != _payload_digest(payload):
        raise ApprovalError(
            "approval token does not match this request's details",
            hint="re-run with --approve after confirming the exact request",
        )
    if int(claims.get("exp", 0)) < int(time.time()):
        raise ApprovalError("approval token has expired", hint="rerun with --approve")
    if consume:
        _consume_once(ws, str(claims.get("nonce", "")))
    return claims


def require_approval(
    ws,
    action: str,
    target: str,
    token: str | None,
    *,
    generate: bool,
    payload: dict | None = None,
    hint: str = "rerun with --approve after confirming the exact request",
    missing_error: type[CreatorError] = ApprovalRequiredError,
) -> dict:
    """Single entry point for every state-changing approval (S-01).

    It always binds the full canonical request and always consumes the token. Callers must not
    reimplement create/verify locally: forgetting either guarantee was the root cause of S-01.
    """
    if generate:
        token = create_token(ws, action, target, payload=payload)
    if not token:
        raise missing_error(f"explicit approval required to {action} for {target}", hint=hint)
    claims = verify_token(ws, token, action, target, payload=payload, consume=True)
    return {"fingerprint": token_fingerprint(token), "expires": claims.get("exp"), "claims": claims}


def token_fingerprint(token: str) -> str:
    """Short non-reversible id for audit logs (never store the token itself)."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()[:12]
