"""Browser security headers on every backend response, including calls straight to api.pricechat.live (the Kiwi VPN apps,
the browser extension, link previews), which never pass through Vercel's header rules.

The values are the ones frontend/vercel.json already sends for the website and for /api through pricechat.live, so a response
that goes through both carries one value, not two that disagree. A header a route set itself is left alone.
HSTS only in production: a local http server must not tell a browser to insist on https for localhost."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

BASE = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
)
HSTS = ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


class SecurityHeaders:
    def __init__(self, app: ASGIApp, hsts: bool) -> None:
        self.app = app
        self.headers = BASE + ((HSTS,) if hsts else ())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers:
                    if name not in headers:
                        headers.append(name, value)
            await send(message)

        await self.app(scope, receive, with_headers)
