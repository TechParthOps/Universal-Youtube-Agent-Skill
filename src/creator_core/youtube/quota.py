"""Central YouTube quota accounting (Phase 13E).

Single accounting boundary backed by SQLite for atomic reserve/settle/reconcile. All YouTube API
operations that consume quota units must be recorded through this ledger. The ledger is internal
accounting only - it does not claim authoritative Google quota balance.

Legacy `check()`/`charge()` are preserved for compatibility but implemented atomically.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from creator_core.cli.errors import ProviderError, ValidationError
from creator_core.security.audit import AuditLog
from creator_core.workspace.atomic import atomic_write_json

DEFAULT_DAILY_LIMIT = 10_000
DEFAULT_SAFETY_MARGIN = 0
DEFAULT_MAX_OPERATION_COST = 2_000
DEFAULT_MAX_RETRIES = 3
DEFAULT_RESERVATION_EXPIRY_S = 3600

# Documented YouTube Data API v3 costs (units per request). Verify against official docs when
# adding operations; this ledger records documented costs, not authoritative Google billing.
COSTS: dict[str, int] = {
    "channels.list": 1,
    "videos.list": 1,
    "videos.insert": 1_600,
    "videos.update": 50,
    "thumbnails.set": 50,
    "search.list": 100,
    "commentThreads.list": 1,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    date_utc TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    service TEXT NOT NULL,
    operation TEXT NOT NULL,
    cost_units INTEGER NOT NULL,
    status TEXT NOT NULL,
    request_sent INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1,
    retry_of TEXT,
    resumable_session_id TEXT,
    detail TEXT,
    error TEXT,
    settled_at TEXT,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_date ON requests(date_utc);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(date_utc, status);
"""


@dataclass
class QuotaReservation:
    request_id: str
    operation: str
    cost_units: int
    date_utc: str


def _now() -> datetime:
    return datetime.now(UTC)


def _today() -> str:
    return _now().date().isoformat()


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


class QuotaLedger:
    def __init__(self, ws, *, audit: AuditLog | None = None):
        self.ws = ws
        self.root = Path(ws.publishing)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "quota.db"
        self.path = self.root / "quota.json"  # legacy compatibility/read-only export
        self.audit = audit or AuditLog(ws.root)
        self._ensure_db()
        self._migrate_legacy_json()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def _ensure_db(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    def _migrate_legacy_json(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
            return
        if not isinstance(data, dict):
            return
        # Do not re-import our own export; quota.db is authoritative.
        if data.get("note") == "Legacy compatibility export; quota.db is authoritative.":
            return
        day = str(data.get("date") or "")
        ops = data.get("operations") or []
        if not day or not isinstance(ops, list):
            return
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE date_utc=? AND detail LIKE 'legacy:%'",
                (day,),
            ).fetchone()[0]
            if existing:
                return
            conn.execute("BEGIN IMMEDIATE")
            try:
                for op in ops:
                    if not isinstance(op, dict):
                        continue
                    rid = f"legacy_{uuid.uuid4().hex[:12]}"
                    at = str(op.get("at") or _iso())
                    operation = str(op.get("operation") or "unknown")
                    units = int(op.get("units") or COSTS.get(operation, 0) or 0)
                    detail = "legacy:" + str(op.get("detail") or "")[:140]
                    conn.execute(
                        """INSERT OR IGNORE INTO requests
                           (id,date_utc,created_at,updated_at,service,operation,cost_units,status,
                            request_sent,completed,http_status,attempt,detail,settled_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            rid,
                            day,
                            at,
                            at,
                            "youtube",
                            operation,
                            units,
                            "settled",
                            1,
                            1,
                            200,
                            1,
                            detail,
                            at,
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _export_legacy(self, day: str | None = None, status: dict | None = None) -> None:
        day = day or _today()
        if status is None:
            status = self.status()
        payload = {
            "date": status["date"],
            "used": status.get("settled_units", status.get("used", 0)),
            "reserved": status.get("reserved_units", 0),
            "operations": status.get("operations", []),
            "note": "Legacy compatibility export; quota.db is authoritative.",
        }
        if "reset_from" in status:
            payload["reset_from"] = status["reset_from"]
        try:
            atomic_write_json(self.path, payload)
        except Exception:  # noqa: BLE001, S110 - intentional no-op: non-fatal best-effort path
            pass

    def load(self) -> dict:
        """Compatibility loader returning legacy-shaped data for the current UTC day."""
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValidationError(
                    f"quota ledger is unreadable: {exc}",
                    hint="fix or delete .creator/publishing/quota.json",
                ) from exc
            # Legacy manual reset: old-format file with a previous date.
            if isinstance(raw, dict) and raw.get("date") != _today() and "note" not in raw:
                data = {
                    "date": _today(),
                    "used": 0,
                    "operations": [],
                    "reset_from": raw.get("date"),
                }
                return data
        status = self.status()
        data = {
            "date": status["date"],
            "used": status["settled_units"],
            "operations": status["operations"],
        }
        if "reset_from" in status:
            data["reset_from"] = status["reset_from"]
        return data

    def _config(self) -> dict:
        cfg = {}
        try:
            from creator_core.workspace.layout import load_config

            cfg = load_config(self.ws) or {}
        except Exception:  # noqa: BLE001 - broad catch: boundary/cleanup path; narrowing requires behavior audit
            cfg = {}
        return cfg.get("youtube") or {}

    def _limits(self, daily_limit: int | None = None) -> dict:
        cfg = self._config()
        cfg_limit = cfg.get("daily_units", DEFAULT_DAILY_LIMIT)
        return {
            "daily_limit": int(cfg_limit if daily_limit is None else daily_limit),
            "safety_margin": int(cfg.get("safety_margin_units", DEFAULT_SAFETY_MARGIN)),
            "max_operation_cost": int(cfg.get("max_operation_cost", DEFAULT_MAX_OPERATION_COST)),
            "max_retries": int(cfg.get("max_retries", DEFAULT_MAX_RETRIES)),
            "reservation_expiry_s": int(
                cfg.get("reservation_expiry_s", DEFAULT_RESERVATION_EXPIRY_S)
            ),
        }

    def _reconcile_locked(self, conn, day: str) -> int:
        now = _now()
        _expiry = _iso(now - timedelta(seconds=DEFAULT_RESERVATION_EXPIRY_S))
        rows = conn.execute(
            """SELECT id, operation, cost_units, request_sent, expires_at, detail FROM requests
               WHERE date_utc=? AND status='reserved'""",
            (day,),
        ).fetchall()
        reconciled = 0
        for rid, operation, cost, sent, expires_at, detail in rows:
            expired = bool(expires_at and expires_at < _iso(now))
            if not expired:
                continue
            # Conservative: if request was sent or outcome unknown, mark uncertain; never silently drop.
            if int(sent):
                status = "uncertain"
                error = "reservation expired after request was sent; conservative reconciliation"
            else:
                status = "reconciled"
                error = "reservation expired before request was sent"
            conn.execute(
                """UPDATE requests SET status=?, completed=?, updated_at=?, error=?,
                   settled_at=CASE WHEN ?='reconciled' THEN ? ELSE settled_at END
                   WHERE id=? AND status='reserved'""",
                (
                    status,
                    1 if status == "reconciled" else 0,
                    _iso(now),
                    error,
                    status,
                    _iso(now),
                    rid,
                ),
            )
            reconciled += 1
            self.audit.log(
                "youtube.quota_reconciled",
                request_id=rid,
                operation=operation,
                units=int(cost),
                status=status,
                detail=str(detail or ""),
            )
        return reconciled

    def status(self, *, daily_limit: int | None = None) -> dict:
        limits = self._limits(daily_limit)
        day = _today()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                reconciled = self._reconcile_locked(conn, day)
                rows = conn.execute(
                    """SELECT status, COALESCE(SUM(cost_units),0), COUNT(*) FROM requests
                       WHERE date_utc=? GROUP BY status""",
                    (day,),
                ).fetchall()
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        by_status = {str(s): {"units": int(u), "count": int(c)} for s, u, c in rows}
        settled = int(by_status.get("settled", {}).get("units", 0))
        reserved = int(by_status.get("reserved", {}).get("units", 0))
        uncertain = int(by_status.get("uncertain", {}).get("units", 0))
        failed = int(by_status.get("failed", {}).get("units", 0))
        effective_limit = max(0, int(limits["daily_limit"]) - int(limits["safety_margin"]))
        # failed-sent requests consumed quota; they are not released back to budget.
        consumed = settled + reserved + uncertain + failed
        remaining = max(0, effective_limit - consumed)
        ops = self._recent_ops(day)
        result = {
            "ok": True,
            "date": day,
            "used": settled,  # compatibility
            "settled_units": settled,
            "reserved_units": reserved,
            "uncertain_units": uncertain,
            "failed_units": failed,
            "consumed_units": consumed,
            "daily_limit": int(limits["daily_limit"]),
            "safety_margin": int(limits["safety_margin"]),
            "effective_limit": effective_limit,
            "remaining": remaining,
            "operations": ops,
            "costs": dict(COSTS),
            "reconciled": reconciled,
            "authoritative": False,
            "note": "Internal accounting only; not an authoritative Google quota balance.",
        }
        if self.path.is_file():
            try:
                legacy = json.loads(self.path.read_text(encoding="utf-8"))
                if (
                    isinstance(legacy, dict)
                    and legacy.get("date") not in (None, day)
                    and "note" not in legacy
                ):
                    result["used"] = 0
                    result["settled_units"] = 0
                    result["reserved_units"] = 0
                    result["uncertain_units"] = 0
                    result["failed_units"] = 0
                    result["remaining"] = result["effective_limit"]
                    result["operations"] = []
                    result["reset_from"] = legacy.get("date")
            except Exception:  # noqa: BLE001, S110 - intentional no-op: non-fatal best-effort path
                pass
        self._export_legacy(day, result)
        return result

    def _recent_ops(self, day: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT created_at, operation, cost_units, status, detail, id, request_sent, attempt
                   FROM requests WHERE date_utc=? ORDER BY created_at DESC, rowid DESC LIMIT 20""",
                (day,),
            ).fetchall()
        ops = []
        for at, op, units, status, detail, rid, sent, attempt in reversed(rows):
            ops.append(
                {
                    "at": at,
                    "operation": op,
                    "units": int(units),
                    "status": status,
                    "detail": str(detail or "")[:160],
                    "request_id": rid,
                    "request_sent": bool(sent),
                    "attempt": int(attempt),
                }
            )
        return ops

    def check(self, operation: str, *, daily_limit: int | None = None) -> int:
        if operation not in COSTS:
            raise ValidationError(
                f"unknown YouTube operation: {operation}",
                hint=f"known operations: {', '.join(sorted(COSTS))}",
            )
        cost = COSTS[operation]
        limits = self._limits(daily_limit)
        if cost > limits["max_operation_cost"]:
            raise ProviderError(
                f"operation {operation} cost {cost} exceeds max_operation_cost {limits['max_operation_cost']}",
                hint="raise youtube.max_operation_cost only with explicit review",
            )
        status = self.status(daily_limit=limits["daily_limit"])
        projected = (
            status["settled_units"]
            + status["reserved_units"]
            + status["uncertain_units"]
            + status.get("failed_units", 0)
            + cost
        )
        if projected > status["effective_limit"]:
            raise ProviderError(
                f"YouTube quota would be exceeded: {status['settled_units']} settled + "
                f"{status['reserved_units']} reserved + {status['uncertain_units']} uncertain + "
                f"{status.get('failed_units', 0)} failed-sent + "
                f"{cost} for {operation} > {status['effective_limit']} effective limit",
                hint="wait for UTC reset, reconcile uncertain requests, or review youtube quota config",
            )
        return cost

    def reserve(
        self,
        operation: str,
        *,
        detail: str = "",
        daily_limit: int | None = None,
        attempt: int = 1,
        retry_of: str | None = None,
        resumable_session_id: str | None = None,
    ) -> QuotaReservation:
        if operation not in COSTS:
            raise ValidationError(
                f"unknown YouTube operation: {operation}",
                hint=f"known operations: {', '.join(sorted(COSTS))}",
            )
        cost = COSTS[operation]
        limits = self._limits(daily_limit)
        if cost > limits["max_operation_cost"]:
            raise ProviderError(f"operation {operation} exceeds max_operation_cost")
        day = _today()
        now = _now()
        rid = f"q_{uuid.uuid4().hex[:16]}"
        expires = _iso(now + timedelta(seconds=int(limits["reservation_expiry_s"])))
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._reconcile_locked(conn, day)
                rows = conn.execute(
                    """SELECT status, COALESCE(SUM(cost_units),0) FROM requests
                       WHERE date_utc=? AND status IN ('settled','reserved','uncertain','failed')
                       GROUP BY status""",
                    (day,),
                ).fetchall()
                totals = {s: int(u) for s, u in rows}
                projected = (
                    totals.get("settled", 0)
                    + totals.get("reserved", 0)
                    + totals.get("uncertain", 0)
                    + totals.get("failed", 0)
                    + cost
                )
                effective = max(0, limits["daily_limit"] - limits["safety_margin"])
                if projected > effective:
                    raise ProviderError(
                        f"YouTube quota would be exceeded: projected {projected} > {effective} effective limit for {operation}",
                        hint="wait for UTC reset or reconcile uncertain requests",
                    )
                conn.execute(
                    """INSERT INTO requests
                       (id,date_utc,created_at,updated_at,service,operation,cost_units,status,
                        request_sent,completed,attempt,retry_of,resumable_session_id,detail,expires_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rid,
                        day,
                        _iso(now),
                        _iso(now),
                        "youtube",
                        operation,
                        cost,
                        "reserved",
                        0,
                        0,
                        int(attempt),
                        retry_of,
                        resumable_session_id,
                        str(detail)[:160],
                        expires,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        self.audit.log(
            "youtube.quota_reserved",
            request_id=rid,
            operation=operation,
            units=cost,
            detail=str(detail)[:160],
        )
        self._export_legacy(day)
        return QuotaReservation(request_id=rid, operation=operation, cost_units=cost, date_utc=day)

    def mark_sent(
        self,
        reservation: QuotaReservation | str,
        *,
        http_status: int | None = None,
        resumable_session_id: str | None = None,
    ) -> None:
        rid = (
            reservation.request_id
            if isinstance(reservation, QuotaReservation)
            else str(reservation)
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """UPDATE requests SET request_sent=1, updated_at=?, http_status=COALESCE(?,http_status),
                       resumable_session_id=COALESCE(?,resumable_session_id)
                       WHERE id=? AND status='reserved'""",
                    (_iso(), http_status, resumable_session_id, rid),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def settle(
        self,
        reservation: QuotaReservation | str,
        *,
        detail: str | None = None,
        http_status: int | None = None,
    ) -> dict:
        rid = (
            reservation.request_id
            if isinstance(reservation, QuotaReservation)
            else str(reservation)
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT operation,cost_units,detail FROM requests WHERE id=?", (rid,)
                ).fetchone()
                if not row:
                    raise ValidationError(f"unknown quota reservation: {rid}")
                op, units, old_detail = row
                conn.execute(
                    """UPDATE requests SET status='settled', completed=1, request_sent=1,
                       updated_at=?, settled_at=?, http_status=COALESCE(?,http_status),
                       detail=COALESCE(?,detail)
                       WHERE id=? AND status IN ('reserved','uncertain')""",
                    (_iso(), _iso(), http_status, str(detail)[:160] if detail else None, rid),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        self.audit.log(
            "youtube.quota_settled",
            request_id=rid,
            operation=str(op),
            units=int(units),
            detail=str(detail or old_detail or "")[:160],
        )
        self._export_legacy()
        return self.status()

    def fail(
        self,
        reservation: QuotaReservation | str,
        *,
        error: str = "",
        http_status: int | None = None,
        request_sent: bool | None = None,
    ) -> dict:
        rid = (
            reservation.request_id
            if isinstance(reservation, QuotaReservation)
            else str(reservation)
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT operation,cost_units,request_sent FROM requests WHERE id=?", (rid,)
                ).fetchone()
                if not row:
                    raise ValidationError(f"unknown quota reservation: {rid}")
                op, units, sent = row
                sent_flag = bool(sent) if request_sent is None else bool(request_sent)
                # If request was sent, keep units accounted as failed-sent (not returned to budget).
                # If not sent, release reservation as reconciled (not charged).
                status = "failed" if sent_flag else "reconciled"
                conn.execute(
                    """UPDATE requests SET status=?, completed=1, request_sent=?, updated_at=?,
                       settled_at=?, http_status=COALESCE(?,http_status), error=?
                       WHERE id=? AND status IN ('reserved','uncertain')""",
                    (
                        status,
                        1 if sent_flag else 0,
                        _iso(),
                        _iso(),
                        http_status,
                        str(error)[:400],
                        rid,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        self.audit.log(
            "youtube.quota_failed",
            request_id=rid,
            operation=str(op),
            units=int(units),
            status=status,
            request_sent=sent_flag,
            error=str(error)[:160],
        )
        self._export_legacy()
        return self.status()

    def mark_unknown(
        self,
        reservation: QuotaReservation | str,
        *,
        error: str = "",
        http_status: int | None = None,
    ) -> dict:
        rid = (
            reservation.request_id
            if isinstance(reservation, QuotaReservation)
            else str(reservation)
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """UPDATE requests SET status='uncertain', request_sent=1, updated_at=?,
                       http_status=COALESCE(?,http_status), error=?
                       WHERE id=? AND status='reserved'""",
                    (_iso(), http_status, str(error)[:400], rid),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        self.audit.log("youtube.quota_uncertain", request_id=rid, error=str(error)[:160])
        self._export_legacy()
        return self.status()

    def charge(self, operation: str, *, detail: str = "", daily_limit: int | None = None) -> dict:
        """Compatibility wrapper: atomic reserve+settle for a request that was actually sent."""
        reservation = self.reserve(operation, detail=detail, daily_limit=daily_limit)
        self.mark_sent(reservation)
        status = self.settle(reservation, detail=detail)
        # Compatibility audit event name expected by existing tests/tooling.
        self.audit.log(
            "youtube.quota_charged",
            operation=operation,
            units=COSTS[operation],
            detail=str(detail)[:160],
            request_id=reservation.request_id,
        )
        self._export_legacy()
        return status

    def reconcile(self) -> dict:
        day = _today()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                count = self._reconcile_locked(conn, day)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        self._export_legacy(day)
        return {"ok": True, "reconciled": count, "status": self.status()}

    def call(
        self,
        operation: str,
        fn,
        *,
        detail: str = "",
        daily_limit: int | None = None,
        attempt: int = 1,
        retry_of: str | None = None,
        resumable_session_id: str | None = None,
    ):
        """Central accounting boundary: reserve -> mark sent -> fn() -> settle/fail.

        Use this for any YouTube Data API request that consumes quota units. The wrapped
        callable is executed only after a reservation is recorded. Failures after send are
        conservatively accounted as sent/failed; failures before send release the reservation.
        """
        reservation = self.reserve(
            operation,
            detail=detail,
            daily_limit=daily_limit,
            attempt=attempt,
            retry_of=retry_of,
            resumable_session_id=resumable_session_id,
        )
        try:
            self.mark_sent(reservation, resumable_session_id=resumable_session_id)
            result = fn()
        except Exception as exc:
            self.fail(reservation, error=str(exc), request_sent=True)
            raise
        self.settle(reservation, detail=detail)
        return result
