"""Analytics ingestion transports (Phase 9): bundled fixture by default, optional live API.

Live access is opt-in (`--live` or config `analytics.transport = "live"`) and uses the official
Google client libraries behind the `[youtube]` extra. It is not exercised against a real account
in CI; every payload records which transport produced it.
"""

from __future__ import annotations

import json
from pathlib import Path

from creator_core.cli.errors import (
    AuthRequiredError,
    MissingDependencyError,
    ProviderError,
    ValidationError,
)
from creator_core.security.audit import AuditLog
from creator_core.security.secrets import get_secret

TOKEN_KEY = "youtube.oauth_token"
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
DEFAULT_METRICS = (
    "views,impressions,impressionsClickThroughRate,averageViewDuration,averageViewPercentage,"
    "estimatedMinutesWatched,subscribersGained,likes,comments,shares"
)
BUNDLED_FIXTURE = Path(__file__).resolve().parents[3] / "examples" / "analytics_fixture.json"


class AnalyticsTransport:
    name = "base"

    def connected(self) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def fetch(
        self,
        *,
        source_path: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError


class FixtureTransport(AnalyticsTransport):
    """Offline transport: reads a local JSON file (bundled example by default)."""

    name = "fixture"

    def connected(self) -> bool:
        return True

    def fetch(
        self,
        *,
        source_path: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        path = Path(source_path) if source_path else BUNDLED_FIXTURE
        if not path.is_file():
            raise ValidationError(
                f"analytics fixture not found: {path}",
                hint="provide --from-file or copy examples/analytics_fixture.json",
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValidationError(f"analytics fixture is unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise ValidationError("analytics fixture must be a JSON object")
        return data


class GoogleAnalyticsTransport(AnalyticsTransport):
    """Optional live transport using the official YouTube Analytics API client."""

    name = "youtube_analytics_api"

    def __init__(self, ws):
        self.ws = ws
        self.audit = AuditLog(ws.root)

    def _creds(self):
        token = get_secret(TOKEN_KEY)
        if not token:
            raise AuthRequiredError(
                "no stored YouTube OAuth credentials",
                hint="run `creator youtube connect --live --client-secrets FILE` first",
            )
        # Validate the stored token before importing vendor libraries so scope/auth errors
        # are reported clearly even when the optional extra is not installed.
        try:
            info = json.loads(token) if isinstance(token, str) else dict(token)
            scopes = list(info.get("scopes") or [])
        except (TypeError, ValueError) as exc:
            raise AuthRequiredError(f"stored OAuth token is invalid: {exc}") from exc
        if ANALYTICS_SCOPE not in scopes:
            raise AuthRequiredError(
                "stored OAuth token lacks the YouTube Analytics scope",
                hint="reconnect with --live and grant yt-analytics.readonly",
            )
        try:
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise MissingDependencyError(
                "google-auth is required for live analytics",
                hint="pip install 'universal-creator-agent[youtube]'",
            ) from exc
        return Credentials(info, scopes=[ANALYTICS_SCOPE])

    def connected(self) -> bool:
        return bool(get_secret(TOKEN_KEY))

    def fetch(
        self,
        *,
        source_path: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        creds = self._creds()
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise MissingDependencyError(
                "google-api-python-client is required for live analytics",
                hint="pip install 'universal-creator-agent[youtube]'",
            ) from exc
        if not start_date or not end_date:
            raise ValidationError(
                "live analytics requires --start-date and --end-date (YYYY-MM-DD)"
            )
        try:
            service = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
            response = (
                service.reports()
                .query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics=DEFAULT_METRICS,
                    dimensions="video",
                    maxResults=200,
                    sort="-views",
                )
                .execute()
            )
        except Exception as exc:  # provider clients raise many vendor-specific types
            raise ProviderError(
                f"YouTube Analytics query failed: {exc}",
                hint="check scopes, quota and the requested date range",
            ) from exc
        self.audit.log(
            "analytics.fetch.live",
            metrics=DEFAULT_METRICS,
            start_date=start_date,
            end_date=end_date,
        )
        payload = dict(response or {})
        payload.update({"retrieved_at": _utc_now(), "start_date": start_date, "end_date": end_date})
        return payload


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def get_transport(ws, config: dict, *, prefer: str | None = None) -> AnalyticsTransport:
    choice = (prefer or (config.get("analytics") or {}).get("transport") or "fixture").lower()
    if choice == "fixture":
        return FixtureTransport()
    if choice in ("live", "youtube_analytics_api", "google"):
        return GoogleAnalyticsTransport(ws)
    raise ProviderError(f"unknown analytics transport: {choice}", hint="use 'fixture' or 'live'")
