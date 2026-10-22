"""CDN caching (Vercel's edge, in front of /api) for public answers that are the same for every visitor.

The website's API calls cross the world to the server (~0.5 s each); a cached answer comes from the edge near the visitor.
Only exact paths listed here, only GET 200s, and never a response that sets a cookie: an edge cache ignores cookies, so
anything that depends on who is asking (a member's own place on a leaderboard, the owner's dashboard) must never be here.
Browsers still revalidate every time (max-age=0); only the CDN keeps a copy (s-maxage), refreshed in the background."""

from starlette.requests import Request
from starlette.responses import Response

# path -> seconds the edge may serve its copy (then it refreshes it in the background while still answering)
EDGE_SECONDS = {
    "/api/website/home": 60,
    "/api/website/latest": 60,
    "/api/website/content": 120,
    "/api/website/chat": 15,
    "/api/members/ranks": 300,
    "/api/members/rules": 300,
    "/api/members/auth/providers": 300,
    "/api/members/trading-bot/track-record": 60,
}
SEO_PREFIX = "/api/website/seo/"  # sitemaps and link previews (app.api.routes.seo)


def header(seconds: int) -> str:
    return f"public, max-age=0, s-maxage={seconds}, stale-while-revalidate={seconds * 5}"


def apply(request: Request, response: Response) -> Response:
    if request.method not in ("GET", "HEAD") or response.status_code != 200 or "set-cookie" in response.headers:
        return response
    path = request.url.path.rstrip("/")
    seconds = EDGE_SECONDS.get(path) or (3600 if path.startswith(SEO_PREFIX) else None)
    if seconds:
        response.headers["Cache-Control"] = header(seconds)
    return response
