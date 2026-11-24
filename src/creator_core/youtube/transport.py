"""YouTube Data API transport: a stub-first interface plus an optional real Google client.

Everything above this layer works against the same methods. The stub is deterministic and offline
(it never contacts YouTube); the real adapter imports `googleapiclient`/`google-auth` lazily and is
only usable when the `[youtube]` extra and OAuth credentials are present. Both are honest about
which one produced a result so the CLI can never claim a live upload happened via the stub.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from creator_core.analytics.transport import ANALYTICS_SCOPE
from creator_core.cli.errors import AuthRequiredError, MissingDependencyError, ProviderError
from creator_core.security.audit import AuditLog
from creator_core.security.secrets import get_secret, store_secret
from creator_core.workspace.atomic import atomic_write_json

# C-02: the Analytics adapter rejects tokens without this scope, so the single OAuth connect
# flow must request all three or live Analytics can never be used from a stored token.
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    ANALYTICS_SCOPE,
)
TOKEN_KEY = "youtube.oauth_token"
CHUNK_BYTES = 8 * 1024 * 1024


class ResumableInterrupted(ProviderError):
    """A resumable upload paused after `uploaded_bytes`; retry with that offset."""

    def __init__(self, uploaded_bytes: int, resumable_uri: str = ""):
        super().__init__(
            f"resumable upload interrupted after {uploaded_bytes} bytes",
            hint="retry the same command; it will continue from this offset",
        )
        self.uploaded_bytes = uploaded_bytes
        self.resumable_uri = resumable_uri


class YouTubeTransport:
    name = "abstract"

    def connected(self) -> bool:
        raise NotImplementedError

    def connect(self, *, client_secrets_path: Path | None = None) -> dict:
        raise NotImplementedError

    def channel_info(self, *, ledger=None, daily_limit: int = 10_000) -> dict:
        raise NotImplementedError

    def insert_video(
        self, body: dict, media_path: Path, *, resume_at: int = 0, resumable_uri: str = ""
    ) -> dict:
        raise NotImplementedError

    def get_video(self, video_id: str) -> dict:
        raise NotImplementedError

    def update_video(self, video_id: str, body: dict) -> dict:
        raise NotImplementedError

    def set_thumbnail(self, video_id: str, image_path: Path) -> dict:
        raise NotImplementedError


class StubTransport(YouTubeTransport):
    """Local, deterministic stand-in for the Data API (no network, no real credentials)."""

    name = "stub"

    def __init__(self, ws, *, config: dict | None = None):
        self.ws = ws
        self.config = config or {}
        self.audit = AuditLog(ws.root)
        self.dir = Path(ws.publishing) / "stub"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.conn = self.dir / "connection.json"
        self.videos_dir = self.dir / "videos"

    def connected(self) -> bool:
        return self.conn.is_file()

    def connect(self, *, client_secrets_path: Path | None = None) -> dict:
        if client_secrets_path is not None and not Path(client_secrets_path).is_file():
            raise ProviderError(f"client secrets file not found: {client_secrets_path}")
        data = {
            "transport": self.name,
            "connected_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "channel": {"id": "UCSTUB000000000000000000", "title": "Stub Channel"},
            "note": "LOCAL STUB — not connected to YouTube",
        }
        atomic_write_json(self.conn, data)
        self.audit.log("youtube.connect", transport=self.name, live=False)
        return data | {"live": False}

    def channel_info(self, *, ledger=None, daily_limit: int = 10_000) -> dict:
        if not self.connected():
            raise AuthRequiredError("YouTube stub is not connected", hint="run `youtube connect`")
        # Stub is local/offline - no real YouTube quota consumed. We still record the
        # accounting event when a ledger is provided so callers can exercise the boundary.
        if ledger is not None:
            ledger.charge("channels.list", detail="channel_info (stub)", daily_limit=daily_limit)
        data = json.loads(self.conn.read_text(encoding="utf-8"))
        return {"id": data["channel"]["id"], "title": data["channel"]["title"], "live": False}

    def _path(self, video_id: str) -> Path:
        key = hashlib.sha256(video_id.encode("utf-8")).hexdigest()[:20]
        return self.videos_dir / f"{key}.json"

    def insert_video(
        self,
        body: dict,
        media_path: Path,
        *,
        resume_at: int = 0,
        resumable_uri: str = "",
        ledger=None,
        reservation=None,
        daily_limit: int = 10_000,
    ) -> dict:
        # Stub is offline (no real quota); publish owns accounting and passes the
        # ledger/reservation for boundary symmetry. They are intentionally ignored here.
        if not self.connected():
            raise AuthRequiredError("YouTube stub is not connected", hint="run `youtube connect`")
        media_path = Path(media_path)
        if not media_path.is_file():
            raise ProviderError(f"media file not found: {media_path}")
        seed = f"{body.get('snippet', {}).get('title', '')}|{media_path.name}".encode()
        video_id = "stub_" + hashlib.sha256(seed).hexdigest()[:12]
        total = media_path.stat().st_size
        fail_after = int((self.config.get("youtube") or {}).get("stub_fail_after_bytes", 0))
        uploaded = min(total, max(0, int(resume_at)))
        if fail_after and fail_after > uploaded and fail_after < total:
            self._store(video_id, body, media_path, fail_after, "draft")
            raise ResumableInterrupted(fail_after, f"stub://resume/{video_id}")
        uploaded = total
        record = self._store(video_id, body, media_path, uploaded, "processing")
        self.audit.log("youtube.upload", video_id=video_id, live=False, bytes=uploaded)
        return {
            "video_id": video_id,
            "uploaded_bytes": uploaded,
            "status": "processing",
            "live": False,
            "record": record,
        }

    def _store(
        self, video_id: str, body: dict, media_path: Path, uploaded: int, status: str
    ) -> dict:
        record = self._path(video_id)
        data = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
        data.update(
            {
                "video_id": video_id,
                "body": body,
                "media_path": str(media_path),
                "uploaded_bytes": uploaded,
                "youtube_status": status,
                "thumbnail": data.get("thumbnail"),
            }
        )
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(record, data)
        return data

    def get_video(self, video_id: str, *, ledger=None, daily_limit: int = 10_000) -> dict:
        if ledger is not None:
            ledger.charge("videos.list", detail=f"stub video {video_id}", daily_limit=daily_limit)
        path = self._path(video_id)
        if not path.is_file():
            raise ProviderError(f"stub video not found: {video_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        status = data.get("youtube_status", "processing")
        privacy = (data.get("body", {}).get("status") or {}).get("privacyStatus", "private")
        return {
            "video_id": video_id,
            "status": status,
            "privacy": privacy,
            "publish_at": (data.get("body", {}).get("status") or {}).get("publishAt"),
            "live": False,
        }

    def update_video(
        self, video_id: str, body: dict, *, ledger=None, daily_limit: int = 10_000
    ) -> dict:
        if ledger is not None:
            ledger.charge(
                "videos.update", detail=f"stub update {video_id}", daily_limit=daily_limit
            )
        path = self._path(video_id)
        if not path.is_file():
            raise ProviderError(f"stub video not found: {video_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("body", {}).update(body)
        atomic_write_json(path, data)
        self.audit.log("youtube.video_updated", video_id=video_id, live=False)
        return self.get_video(video_id, ledger=ledger, daily_limit=daily_limit)

    def set_thumbnail(
        self, video_id: str, image_path: Path, *, ledger=None, daily_limit: int = 10_000
    ) -> dict:
        if ledger is not None:
            ledger.charge(
                "thumbnails.set", detail=f"stub thumbnail {video_id}", daily_limit=daily_limit
            )
        path = self._path(video_id)
        if not path.is_file():
            raise ProviderError(f"stub video not found: {video_id}")
        image = Path(image_path)
        if not image.is_file():
            raise ProviderError(f"thumbnail not found: {image}")
        if image.stat().st_size > 2 * 1024 * 1024:
            raise ProviderError("thumbnail exceeds the 2 MB YouTube limit")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["thumbnail"] = str(image)
        atomic_write_json(path, data)
        self.audit.log("youtube.thumbnail", video_id=video_id, live=False)
        return {"video_id": video_id, "thumbnail": str(image), "live": False}


class GoogleApiTransport(YouTubeTransport):
    """Real Data API adapter. Requires the optional `[youtube]` extra and OAuth credentials."""

    name = "youtube_data_api"

    def __init__(self, ws, *, config: dict | None = None):
        self.ws = ws
        self.config = config or {}
        self.audit = AuditLog(ws.root)

    def _creds(self):
        try:
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise MissingDependencyError(
                "google-auth is required for YouTube publishing",
                hint="pip install 'universal-creator-agent[youtube]'",
            ) from exc
        raw = get_secret(TOKEN_KEY)
        if not raw:
            raise AuthRequiredError(
                "no YouTube OAuth credentials stored",
                hint="run `creator youtube connect --client-secrets FILE`",
            )
        try:
            creds = Credentials.from_authorized_user_info(json.loads(raw), list(SCOPES))
        except (json.JSONDecodeError, ValueError) as exc:
            raise AuthRequiredError(
                "stored YouTube credentials are invalid", hint="re-run `creator youtube connect`"
            ) from exc
        if creds.expired:
            if not creds.refresh_token:
                raise AuthRequiredError(
                    "stored YouTube OAuth token is expired and has no refresh token",
                    hint="re-run `creator youtube connect --live --client-secrets FILE`",
                )
            from google.auth.transport.requests import Request

            try:
                creds.refresh(Request())
            except Exception as exc:  # vendor/network refresh errors vary by installation
                raise AuthRequiredError(
                    "YouTube OAuth token refresh failed",
                    hint="re-run `creator youtube connect --live --client-secrets FILE`",
                ) from exc
            store_secret(TOKEN_KEY, creds.to_json())
        return creds

    def _service(self):
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise MissingDependencyError(
                "google-api-python-client is required for YouTube publishing",
                hint="pip install 'universal-creator-agent[youtube]'",
            ) from exc
        return build("youtube", "v3", credentials=self._creds(), cache_discovery=False)

    def connected(self) -> bool:
        return bool(get_secret(TOKEN_KEY))

    def connect(self, *, client_secrets_path: Path | None = None) -> dict:
        if not client_secrets_path or not Path(client_secrets_path).is_file():
            raise ProviderError(
                "--client-secrets FILE is required for live OAuth",
                hint="download the OAuth client JSON from Google Cloud Console",
            )
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise MissingDependencyError(
                "google-auth-oauthlib is required for YouTube OAuth",
                hint="pip install 'universal-creator-agent[youtube]'",
            ) from exc
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), list(SCOPES))
        creds = flow.run_local_server(port=0)
        store_secret(TOKEN_KEY, creds.to_json())
        self.audit.log("youtube.connect", transport=self.name, live=True)
        return {"transport": self.name, "live": True, "scopes": list(SCOPES)}

    def channel_info(self, *, ledger=None, daily_limit: int = 10_000) -> dict:
        def _call():
            response = self._service().channels().list(part="snippet", mine=True).execute()
            items = response.get("items") or []
            if not items:
                raise ProviderError("no YouTube channel found for these credentials")
            return {"id": items[0]["id"], "title": items[0]["snippet"]["title"], "live": True}

        if ledger is None:
            raise ProviderError(
                "live channels.list requires a quota ledger; accounting cannot be bypassed",
                hint="provide the workspace QuotaLedger from youtube.publish",
            )
        return ledger.call("channels.list", _call, detail="channel_info", daily_limit=daily_limit)

    def insert_video(
        self,
        body: dict,
        media_path: Path,
        *,
        resume_at: int = 0,
        resumable_uri: str = "",
        ledger=None,
        reservation=None,
        daily_limit: int = 10_000,
    ) -> dict:
        from googleapiclient.http import MediaFileUpload

        # A live videos.insert consumes quota; refuse to send without accounting.
        if ledger is None:
            raise ProviderError(
                "live videos.insert requires a quota ledger; accounting cannot be bypassed",
                hint="provide the workspace QuotaLedger from youtube.publish",
            )
        media_kwargs: dict[str, object] = {"chunksize": CHUNK_BYTES, "resumable": True}
        if resume_at > 0:
            # A resumed call without its session URI would start a *new* upload, silently
            # burning another 1600 quota units. Fail closed instead (live defect fix).
            if not resumable_uri:
                raise ProviderError(
                    "cannot resume upload: resumable session URI is missing",
                    hint="retry from the beginning only after confirming no draft exists",
                )
            media_kwargs.update({"resumable_uri": resumable_uri, "resumable_progress": resume_at})
        media = MediaFileUpload(str(media_path), **media_kwargs)
        request = (
            self._service()
            .videos()
            .insert(part="snippet,status,contentDetails", body=body, media_body=media)
        )
        # Mark sent at the actual transport boundary, immediately before transmission.
        if reservation is not None:
            ledger.mark_sent(reservation, resumable_session_id=resumable_uri or None)
        uploaded = resume_at
        response = None
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    uploaded = max(uploaded, int(status.resumable_progress))
            except Exception as exc:  # network interruption — keep the resumable URI
                uri = getattr(getattr(request, "resumable", None), "uri", "") or resumable_uri
                raise ResumableInterrupted(max(uploaded, resume_at), uri) from exc
        video_id = response["id"]
        self.audit.log("youtube.upload", video_id=video_id, live=True, bytes=uploaded)
        return {
            "video_id": video_id,
            "uploaded_bytes": uploaded,
            "status": response.get("status", "uploaded"),
            "live": True,
        }

    def get_video(self, video_id: str, *, ledger=None, daily_limit: int = 10_000) -> dict:
        def _call():
            response = self._service().videos().list(part="status,snippet", id=video_id).execute()
            items = response.get("items") or []
            if not items:
                raise ProviderError(f"video {video_id} not found on YouTube")
            status = items[0].get("status") or {}
            return {
                "video_id": video_id,
                "status": status.get("uploadStatus", "unknown"),
                "privacy": status.get("privacyStatus"),
                "publish_at": status.get("publishAt"),
                "live": True,
            }

        if ledger is None:
            raise ProviderError(
                "live videos.list requires a quota ledger; accounting cannot be bypassed",
                hint="provide the workspace QuotaLedger from youtube.publish",
            )
        return ledger.call(
            "videos.list", _call, detail=f"video {video_id}", daily_limit=daily_limit
        )

    def update_video(
        self, video_id: str, body: dict, *, ledger=None, daily_limit: int = 10_000
    ) -> dict:
        def _call():
            self._service().videos().update(part="status,snippet", body=body).execute()
            self.audit.log("youtube.video_updated", video_id=video_id, live=True)
            # get_video incurs its own quota cost; account separately when ledger provided.
            return self.get_video(video_id, ledger=ledger, daily_limit=daily_limit)

        if ledger is None:
            raise ProviderError(
                "live videos.update requires a quota ledger; accounting cannot be bypassed",
                hint="provide the workspace QuotaLedger from youtube.publish",
            )
        # Account videos.update; get_video inside will account videos.list separately.
        self._service()  # ensure deps present before reserving? no, reserve is cheap; keep simple.
        reservation = ledger.reserve(
            "videos.update", detail=f"update {video_id}", daily_limit=daily_limit
        )
        try:
            ledger.mark_sent(reservation)
            self._service().videos().update(part="status,snippet", body=body).execute()
            ledger.settle(reservation, detail=f"update {video_id}")
        except Exception as exc:
            ledger.fail(reservation, error=str(exc), request_sent=True)
            raise
        self.audit.log("youtube.video_updated", video_id=video_id, live=True)
        return self.get_video(video_id, ledger=ledger, daily_limit=daily_limit)

    def set_thumbnail(
        self, video_id: str, image_path: Path, *, ledger=None, daily_limit: int = 10_000
    ) -> dict:
        image = Path(image_path)
        if not image.is_file():
            raise ProviderError(f"thumbnail not found: {image}")
        if image.stat().st_size > 2 * 1024 * 1024:
            raise ProviderError("thumbnail exceeds the 2 MB YouTube limit")

        def _call():
            from googleapiclient.http import MediaFileUpload

            self._service().thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(str(image))
            ).execute()
            self.audit.log("youtube.thumbnail", video_id=video_id, live=True)
            return {"video_id": video_id, "thumbnail": str(image), "live": True}

        if ledger is None:
            raise ProviderError(
                "live thumbnails.set requires a quota ledger; accounting cannot be bypassed",
                hint="provide the workspace QuotaLedger from youtube.publish",
            )
        return ledger.call(
            "thumbnails.set", _call, detail=f"thumbnail {video_id}", daily_limit=daily_limit
        )


def get_transport(ws, config: dict, *, prefer: str | None = None) -> YouTubeTransport:
    """Select the transport. Stub is the default until live mode is explicitly requested."""
    choice = (prefer or (config.get("youtube") or {}).get("transport") or "stub").lower()
    if choice == "stub":
        return StubTransport(ws, config=config)
    if choice in ("live", "youtube_data_api", "google"):
        return GoogleApiTransport(ws, config=config)
    raise ProviderError(f"unknown YouTube transport: {choice}", hint="use 'stub' or 'live'")
