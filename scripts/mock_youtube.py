"""Local mock of the YouTube Data API v3 (read endpoints only) for development and demos. NOT used in production.

Serves one demo channel (@pricechat_demo) with a few Shorts and long-form videos through the same
endpoints Price Chat calls (channels, playlistItems, videos), plus simple SVG thumbnails, so the
YouTube → website pipeline can be exercised without a Google account or any quota.
POST /_demo/upload adds a new Short (to watch it appear on the website after a sync).

Run:  backend\\.venv\\Scripts\\python.exe scripts\\mock_youtube.py   (listens on 127.0.0.1:8083)
Then start a backend with YOUTUBE_API_BASE=http://127.0.0.1:8083/youtube/v3 (see scripts\\run-demo.ps1),
and on the YouTube page connect with the demo key below and channel @pricechat_demo.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

DEMO_KEY = "AIzaMockYouTubeKeyForLocalDemoOnly000000"
CHANNEL = "UCpricechatDemo000000000"
BASE = "http://127.0.0.1:8083"
COLORS = ["#0f3d2e", "#1c2a44", "#3a2414", "#2b1d3d", "#123b3b", "#3b1220"]

# id, title, seconds, (w, h), hours ago
VIDEOS: list[tuple[str, str, int, tuple[int, int], float]] = [
    ("demoShort03", "Is Bitcoin about to break out?", 48, (1080, 1920), 3),
    ("demoShort02", "Funding rates explained in 45 seconds", 45, (1080, 1920), 30),
    ("demoLong001", "The Dubai property market right now", 1281, (1920, 1080), 54),
    ("demoShort01", "Gold vs Bitcoin: the 30-second version", 33, (1080, 1920), 80),
]
app = FastAPI(title="Mock YouTube Data API v3")
NOW = datetime.now(timezone.utc)


def _iso(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _video(i: int, v: tuple[str, str, int, tuple[int, int], float]) -> dict[str, Any]:
    vid, title, secs, (w, h), ago = v
    m, s = divmod(secs, 60)
    return {
        "id": vid,
        "snippet": {"channelId": CHANNEL, "title": title, "description": "Demo video #shorts" if h > w else "Demo video",
                    "publishedAt": _iso(ago), "tags": ["pricechat"],
                    "thumbnails": {"high": {"url": f"{BASE}/thumb/{vid}.svg", "width": 480, "height": 270}}},
        "contentDetails": {"duration": f"PT{m}M{s}S" if m else f"PT{s}S"},
        "status": {"privacyStatus": "public", "uploadStatus": "processed"},
        "statistics": {"viewCount": str(1200 + 731 * i), "likeCount": str(40 + 9 * i), "commentCount": str(3 + i)},
        "player": {"embedWidth": str(int(8192 * w / h)), "embedHeight": "8192"},
    }


def _bad_key(request: Request) -> JSONResponse | None:
    if request.query_params.get("key") != DEMO_KEY:
        return JSONResponse({"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "errors": [{"reason": "badRequest"}]}}, status_code=400)
    return None


@app.get("/youtube/v3/channels")
def channels(request: Request) -> JSONResponse:
    if (bad := _bad_key(request)) is not None:
        return bad
    q = request.query_params
    if q.get("forHandle", "").lower() not in ("@pricechat_demo", "") or (q.get("id") and CHANNEL not in q.get("id", "")):
        return JSONResponse({"items": []})
    return JSONResponse({"items": [{"id": CHANNEL, "snippet": {"title": "Price Chat (demo)", "customUrl": "@pricechat_demo", "thumbnails": {}},
                                    "statistics": {"subscriberCount": "2480", "videoCount": str(len(VIDEOS)), "viewCount": "48210", "hiddenSubscriberCount": False},
                                    "contentDetails": {"relatedPlaylists": {"uploads": "UU" + CHANNEL[2:]}}}]})


@app.get("/youtube/v3/playlistItems")
def playlist_items(request: Request) -> JSONResponse:
    if (bad := _bad_key(request)) is not None:
        return bad
    return JSONResponse({"items": [{"contentDetails": {"videoId": v[0]}} for v in VIDEOS]})


@app.get("/youtube/v3/videos")
def videos(request: Request) -> JSONResponse:
    if (bad := _bad_key(request)) is not None:
        return bad
    ids = set(request.query_params.get("id", "").split(","))
    return JSONResponse({"items": [_video(i, v) for i, v in enumerate(VIDEOS) if v[0] in ids]})


@app.get("/thumb/{vid}.svg")
def thumb(vid: str) -> Response:
    idx = next((i for i, v in enumerate(VIDEOS) if v[0] == vid), 0)
    title = next((v[1] for v in VIDEOS if v[0] == vid), "Price Chat")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 270"><rect width="480" height="270" fill="{COLORS[idx % len(COLORS)]}"/>'
           f'<text x="24" y="240" fill="#f4f5f3" font-family="Arial" font-size="22" font-weight="700">{title[:34]}</text></svg>')
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "max-age=3600"})


@app.post("/_demo/upload")
def demo_upload() -> dict[str, str]:
    n = sum(1 for v in VIDEOS if v[0].startswith("demoNew")) + 1
    vid = f"demoNew{n:04d}"
    VIDEOS.insert(0, (vid, f"Brand new Short #{n}", 41, (1080, 1920), 0.01))
    return {"uploaded": vid}


if __name__ == "__main__":
    print(f"Mock YouTube on {BASE}  (key: {DEMO_KEY}, channel: @pricechat_demo)")
    uvicorn.run(app, host="127.0.0.1", port=8083, log_level="warning")
