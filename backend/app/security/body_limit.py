"""Request-body size limits for the PUBLIC API routes (admin_auth.PUBLIC_*: member APIs, AI workers, the login).

Nothing else bounds a request body: uvicorn reads whatever arrives, FastAPI loads a JSON body into memory and spools a
multipart upload to a temp file BEFORE the route runs its sign-in check. So anyone, signed in or not, could send a
multi-GB body to e.g. /api/members/portfolio/scan and fill the server's memory or disk. Routes behind the owner login
are not limited here (the admin middleware refuses a request without the login before its body is read).

Over the limit: 413. A declared Content-Length is checked before anything is read; a chunked body is counted as it
arrives and refused at the first byte over.
"""

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

from app.security import admin_auth

MB = 1024 * 1024
DEFAULT = 1 * MB  # JSON bodies of the member and login APIs are a few KB
WORKER = 8 * MB  # AI workers' scripts, progress and media lists
UPLOAD = 16 * MB  # photo uploads (portfolio.photos.MAX_UPLOAD = 15 MB) + multipart overhead
_UPLOADS = re.compile(r"^/api/members/(portfolio/scan|portfolio/holdings/\d+/photos|market/listings/\d+/photos)/?$")
_WORKER_RESULT = re.compile(r"^/api/(brand-manager|short-market)/worker/jobs/\d+/result/?$")
_WORKER = ("/api/brand-manager/worker/", "/api/short-market/worker/", "/api/newsdesk-worker/")
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


def limit_for(method: str, path: str, video_mb: int) -> int | None:
    """Max body bytes for this request, or None for no limit here (not a public route, or no body)."""
    if method.upper() not in _BODY_METHODS or not path.startswith("/api/") or not admin_auth.is_public(method, path):
        return None
    if _WORKER_RESULT.match(path):
        return video_mb * MB + UPLOAD  # the finished video (settings.bm_max_video_mb) + its thumbnail
    if _UPLOADS.match(path):
        return UPLOAD
    if path.startswith(_WORKER):
        return WORKER
    return DEFAULT


class BodyLimit:
    """Pure ASGI middleware (no buffering): see the module notes."""

    def __init__(self, app: Any, video_mb: int) -> None:
        self.app = app
        self.video_mb = video_mb

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        cap = limit_for(scope.get("method", ""), scope.get("path", ""), self.video_mb) if scope["type"] == "http" else None
        if cap is None:
            await self.app(scope, receive, send)
            return
        declared = next((v for k, v in scope.get("headers", []) if k == b"content-length"), None)
        if declared is not None and (not declared.strip().isdigit() or int(declared) > cap):
            await _too_large(send, cap)
            return
        seen = 0

        async def counted() -> dict[str, Any]:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > cap:
                    raise HTTPException(413, _message(cap))
            return message

        await self.app(scope, counted, send)


def _message(cap: int) -> str:
    return f"Request too large (the limit here is {max(1, cap // MB)} MB)."


async def _too_large(send: Send, cap: int) -> None:
    body = json.dumps({"detail": _message(cap), "code": "too_large"}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})
