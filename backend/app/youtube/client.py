"""Read-only client for the official YouTube Data API v3 (https://developers.google.com/youtube/v3).

Only list endpoints are used (channels, playlistItems, videos): each costs 1 quota unit (default
quota: 10,000 units/day). Authentication is either an API key (public data) or an OAuth access
token with the youtube.readonly scope. Nothing here can upload, edit or delete anything.
Error messages never contain the key or token.
"""

import re
from collections.abc import Callable
from typing import Any

import httpx

UNIT_COST = 1  # every list call used here
VIDEO_PARTS = "snippet,contentDetails,statistics,status,player"
EMBED_PROBE = "8192"  # asking for a tall embed makes the API report the video's aspect ratio (embedWidth/embedHeight)


class YouTubeError(Exception):
    code = "youtube_error"


class YouTubeNotConfigured(YouTubeError):
    code = "youtube_not_configured"


class YouTubeAuthError(YouTubeError):
    """Key/token invalid, revoked, or not allowed to use the YouTube Data API."""

    code = "youtube_auth"


class YouTubeQuotaError(YouTubeError):
    code = "youtube_quota"


class YouTubeNotFound(YouTubeError):
    code = "youtube_not_found"


class YouTubeUnavailable(YouTubeError):
    """Network failure, timeout or Google server error."""

    code = "youtube_unavailable"


UsageCallback = Callable[[str], None]


class YouTubeClient:
    def __init__(self, api_base: str, *, api_key: str | None = None, access_token: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, on_request: UsageCallback | None = None, timeout: float = 20.0) -> None:
        if not api_key and not access_token:
            raise YouTubeNotConfigured("YouTube is not connected")
        self.api_base = api_base.rstrip("/")
        self._key = api_key
        self._token = access_token
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0), transport=transport)
        self.on_request = on_request

    def __repr__(self) -> str:
        return f"YouTubeClient(api_base={self.api_base!r}, auth={'oauth' if self._token else 'api_key'})"

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- endpoints ---------------------------------------------------------------------------

    async def channels(self, *, ids: list[str] | None = None, handle: str | None = None, mine: bool = False) -> list[dict[str, Any]]:
        params = {"part": "snippet,statistics,contentDetails", "maxResults": "50"}
        if mine:
            params["mine"] = "true"
        elif handle:
            params["forHandle"] = handle if handle.startswith("@") else f"@{handle}"
        elif ids:
            params["id"] = ",".join(ids[:50])
        else:
            raise ValueError("channels(): ids, handle or mine")
        return (await self._get("channels", params)).get("items") or []

    async def playlist_items(self, playlist_id: str, page_token: str | None = None) -> dict[str, Any]:
        params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": "50"}
        if page_token:
            params["pageToken"] = page_token
        return await self._get("playlistItems", params)

    async def videos(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        params = {"part": VIDEO_PARTS, "id": ",".join(ids[:50]), "maxResults": "50", "maxHeight": EMBED_PROBE}
        return (await self._get("videos", params)).get("items") or []

    # --- transport ---------------------------------------------------------------------------

    async def _get(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        else:
            params = {**params, "key": self._key or ""}
        try:
            response = await self._client.get(f"{self.api_base}/{endpoint}", params=params, headers=headers)
        except httpx.TimeoutException:
            raise YouTubeUnavailable(f"YouTube did not respond in time ({endpoint})") from None
        except httpx.TransportError as exc:
            raise YouTubeUnavailable(f"Cannot reach YouTube ({type(exc).__name__})") from None
        if self.on_request is not None:
            self.on_request(endpoint)
        return self._parse(response, endpoint)

    @staticmethod
    def _parse(response: httpx.Response, endpoint: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = {}
        status = response.status_code
        if 200 <= status < 300:
            return body
        err = body.get("error") or {}
        reasons = {e.get("reason", "") for e in err.get("errors") or []}
        message = re.sub(r"key=[^&\s]+", "key=***", str(err.get("message") or response.reason_phrase or "error"))[:300]
        if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}:
            raise YouTubeQuotaError(f"YouTube API quota or rate limit reached ({endpoint}): {message}")
        if status == 401 or reasons & {"keyInvalid", "authError", "keyExpired"} or "API key not valid" in message:
            raise YouTubeAuthError(f"YouTube rejected the credentials ({status}): {message}")
        if status == 403:
            raise YouTubeAuthError(f"YouTube refused access ({endpoint}): {message}")
        if status == 404:
            raise YouTubeNotFound(f"Not found on YouTube ({endpoint}): {message}")
        if status >= 500:
            raise YouTubeUnavailable(f"YouTube server error {status} ({endpoint})")
        raise YouTubeError(f"YouTube rejected the request ({status}, {endpoint}): {message}")
