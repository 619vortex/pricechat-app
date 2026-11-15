"""TEST FIXTURES for Trending Topics: made-up headlines, a made-up Google Trends feed, made-up Reddit listings and X answers, served by
httpx.MockTransport. No real service is contacted."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.newsdesk import NdStory


def google_rss(items: list[dict[str, Any]]) -> str:
    out = []
    for it in items:
        news = "".join(f"<ht:news_item><ht:news_item_title>{n['title']}</ht:news_item_title><ht:news_item_url>{n['url']}</ht:news_item_url>"
                       f"<ht:news_item_source>{n['source']}</ht:news_item_source></ht:news_item>" for n in it.get("news", []))
        out.append(f"<item><title>{it['query']}</title><ht:approx_traffic>{it.get('traffic', '20,000+')}</ht:approx_traffic>"
                   f"<pubDate>{format_datetime(datetime.now(timezone.utc) - timedelta(hours=1))}</pubDate>{news}</item>")  # always fresh
    return ('<?xml version="1.0" encoding="UTF-8"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>'
            f"<title>Daily Search Trends</title>{''.join(out)}</channel></rss>")


def reddit_listing(posts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    return {"data": {"children": [{"data": {"name": f"t3_{i}", "title": p["title"], "permalink": f"/r/x/comments/{i}/", "score": p.get("score", 1000),
                                             "num_comments": p.get("comments", 300), "created_utc": (now - timedelta(minutes=30)).timestamp(), "stickied": False,
                                             "over_18": False}} for i, p in enumerate(posts)]}}


class Web:
    """Google Trends, Reddit and X, mocked. status: force an HTTP status per host."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.google: list[dict[str, Any]] = []
        self.reddit: dict[str, list[dict[str, Any]]] = {}
        self.x_trends: list[dict[str, Any]] = []
        self.status: dict[str, int] = {}
        self.calls: list[str] = []
        self.posted: list[bytes] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.calls.append(f"{request.method} {host}{path}")
        if host in self.status:
            return httpx.Response(self.status[host], json={"title": "Forbidden" if self.status[host] == 403 else "error", "detail": "test"})
        if host == "trends.google.com":
            return httpx.Response(200, text=google_rss(self.google))
        if host in ("www.reddit.com", "oauth.reddit.com"):
            sub = path.split("/")[2]
            return httpx.Response(200, json=reddit_listing(self.reddit.get(sub, []), self.now))
        if host == "api.x.com" and path.startswith("/2/trends/by/woeid"):
            return httpx.Response(200, json={"data": self.x_trends})
        if host == "api.x.com" and path == "/2/tweets" and request.method == "POST":
            self.posted.append(request.content)
            return httpx.Response(201, json={"data": {"id": "1990000000000000777", "text": "t"}})
        return httpx.Response(404, json={"title": "Not Found"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    @property
    def runtime(self) -> Any:
        return type("Runtime", (), {"transport": self.transport})()


def story(db: Session, title: str, source: str, n: int, now: datetime, minutes_ago: int = 30, category: str | None = "crypto", tier: int = 1) -> NdStory:
    from app.newsdesk import checks

    at = now - timedelta(minutes=minutes_ago)
    row = NdStory(link=f"https://{source.lower().replace(' ', '')}.test/{n}", title=title, summary="", source=source, tier=tier, category=category,
                  published_at=at, fingerprint=checks.fingerprint(title), score=1.0, created_at=at, updated_at=at)
    db.add(row)
    db.commit()
    return row
