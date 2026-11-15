"""Where trends come from. Official or documented access only, never HTML scraping:

- news: the News Desk's own stories (nd_stories, read from its curated RSS feeds every few minutes). No extra request. Reliable.
- google: Google Trends' official "Trending now" RSS feed (https://trends.google.com/trending/rss?geo=US). Google has no public Trends
  API; this feed is the published one. Each trend carries the news articles Google links it to: those from known outlets are reliable.
- reddit: Reddit's Data API. With REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (a free "script"/app-only OAuth app) it uses oauth.reddit.com;
  without them it stays off, unless the owner opts in to the public .json listings in Admin (sources.reddit.public_json), which Reddit
  rate-limits for unauthenticated clients. A generic User-Agent, one request per subreddit, at most every 30 minutes. Never reliable for facts.
- x_trends: X API v2 trends by WOEID ($0.010 a request on pay-per-use; off by default: a new cost the owner turns on). A plan without it answers 403: shown as "not available on your
  X plan" and not asked again for a day.
- x_accounts: recent posts from major financial accounts through X's recent search ($0.005 per post read). Off by default (cost).

Every fetcher returns (items, problem, meta). An item is a plain dict; nothing here writes to the database.
"""

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdStory

logger = logging.getLogger(__name__)

UA = "server:pricechat-trending-topics:1.0"  # generic: no personal details
GOOGLE_RSS = "https://trends.google.com/trending/rss?geo={geo}"
REDDIT_PUBLIC = "https://www.reddit.com/r/{sub}/hot.json?limit={n}&raw_json=1"
REDDIT_OAUTH = "https://oauth.reddit.com/r/{sub}/hot?limit={n}&raw_json=1"
REDDIT_TOKEN = "https://www.reddit.com/api/v1/access_token"
NAMES = {"news": "News Desk feeds", "google": "Google Trends", "reddit": "Reddit", "x_trends": "X trends", "x_accounts": "X major accounts"}
# minutes between two reads of a source (the scan itself runs every job_trending_seconds)
INTERVAL = {"news": 0, "google": 10, "reddit": 15, "x_trends": 60, "x_accounts": 60}  # Google's RSS is free; X reads are paid
# outlets whose reporting may back a factual claim (plus every News Desk feed's own name)
RELIABLE_OUTLETS = {"reuters", "associated press", "ap news", "ap", "bloomberg", "financial times", "ft", "wall street journal", "wsj",
                    "the wall street journal", "cnbc", "bbc", "bbc news", "the guardian", "new york times", "the new york times", "npr",
                    "al jazeera", "marketwatch", "barron's", "fortune", "forbes", "the economist", "axios", "politico", "cnn", "abc news",
                    "cbs news", "nbc news", "the washington post", "washington post", "yahoo finance", "coindesk", "the block", "decrypt",
                    "cointelegraph", "theblock", "techcrunch", "the verge", "wired", "ars technica", "nikkei asia", "south china morning post",
                    "federal reserve", "sec", "ecb", "white house", "dw", "france 24", "sky news", "the hill", "business insider", "investopedia"}
RELIABLE_X = {"reuters", "ap", "business", "cnbc", "ft", "wsj", "bbcbreaking", "markets", "bloombergtv", "reutersbiz"}
DEFAULT_SUBREDDITS = ["CryptoCurrency", "Bitcoin", "ethereum", "stocks", "investing", "wallstreetbets", "economics", "worldnews", "technology", "geopolitics"]
DEFAULT_ACCOUNTS = ["Reuters", "business", "CNBC", "WSJ", "FT", "AP", "BBCBreaking", "markets"]
DEFAULT_SOURCES: dict[str, dict[str, Any]] = {
    "news": {"on": True},
    "google": {"on": True, "geo": ["US", "GB"]},
    "reddit": {"on": True, "public_json": False, "subreddits": DEFAULT_SUBREDDITS, "per_sub": 15},
    "x_trends": {"on": False, "woeid": 1},  # $0.010 a request on X's pay-per-use: off until the owner turns it on
    "x_accounts": {"on": False, "accounts": DEFAULT_ACCOUNTS, "max_posts": 20},
}

Item = dict[str, Any]


def _clean(text: str, n: int = 400) -> str:
    t = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", t).strip()[:n]


def reliable_outlet(name: str, extra: set[str] | None = None) -> bool:
    n = (name or "").strip().lower()
    return bool(n) and (n in RELIABLE_OUTLETS or n in (extra or set()))


def merged(stored: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The stored source options over the defaults (a new option added here reaches an existing install)."""
    out = {k: dict(v) for k, v in DEFAULT_SOURCES.items()}
    for k, v in (stored or {}).items():
        if k in out and isinstance(v, dict):
            out[k].update(v)
    return out


# --- news (the News Desk's stories) ----------------------------------------------------------------------------------


# round-ups, archives and stock-picking lists are not a topic, whatever their words
NOT_A_TOPIC = re.compile(r"\b(what happened in|from the archives|week in review|round-?up|recap|newsletter|morning brief|daily brief|things to know|"
                         r"\d+ [\w ]{0,25}stocks\b|stocks? to (buy|watch)|could bounce back|an analyst says|deciphering)\b"
                         r"|^\d+ (big |key )?(questions|charts|things|takeaways|ways|lessons)\b", re.I)


def topical(title: str) -> bool:
    from app.newsdesk import checks

    return not (checks.NOT_NEWS.search(title) or checks.is_explainer(title) or NOT_A_TOPIC.search(title))


def news(db: Session, now: datetime, hours: int = 24) -> tuple[list[Item], str | None, dict[str, Any]]:
    rows = [r for r in db.scalars(select(NdStory).where(NdStory.created_at >= now - timedelta(hours=hours))) if topical(r.title)]
    items = [{"source": "news", "external_id": r.link[:300], "title": r.title, "summary": r.summary or "", "url": r.link, "outlet": r.source,
              "reliable": True, "engagement": {0: 3.0, 1: 2.0}.get(r.tier, 1.0), "published_at": r.published_at or r.created_at,
              "category_hint": r.category, "extra": {"tier": r.tier}} for r in rows]
    return items, (None if items else "no stories in the last 24 hours (is the News Desk tick running?)"), {"requests": 0, "idle": not items}


# --- Google Trends RSS ---------------------------------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if _local(c.tag) == name), None)


def _text(el: ET.Element, name: str) -> str:
    c = _child(el, name)
    return (c.text or "").strip() if c is not None else ""


def traffic(raw: str) -> float:
    """'200K+' -> 200000, '2,000+' -> 2000, '1M+' -> 1000000."""
    m = re.match(r"\s*([\d,.]+)\s*([KkMm]?)", raw or "")
    if not m:
        return 0.0
    n = float(m.group(1).replace(",", "") or 0)
    return n * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)


def parse_google(xml_text: str, geo: str, feed_outlets: set[str] | None = None) -> list[Item]:
    if len(xml_text) > 3_000_000 or "<!ENTITY" in xml_text or "<!DOCTYPE" in xml_text[:2000]:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[Item] = []
    for it in root.iter():
        if _local(it.tag) != "item":
            continue
        query = _clean(_text(it, "title"), 120)
        if not query:
            continue
        when = None
        try:
            when = parsedate_to_datetime(_text(it, "pubDate"))
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
        approx = _text(it, "approx_traffic")
        group = f"google:{geo}:{query.lower()}"
        news_items = [c for c in it if _local(c.tag) == "news_item"]
        articles = [{"title": _clean(_text(n, "news_item_title"), 300), "url": _text(n, "news_item_url"), "outlet": _clean(_text(n, "news_item_source"), 80)}
                    for n in news_items]
        articles = [a for a in articles if a["title"] and a["url"].startswith("https://")]
        out.append({"source": "google", "external_id": group[:300], "title": query, "summary": " | ".join(a["title"] for a in articles[:3])[:800],
                    "url": f"https://trends.google.com/trending?geo={geo}", "outlet": "Google Trends", "reliable": False, "engagement": traffic(approx),
                    "published_at": when, "extra": {"group": group, "approx_traffic": approx, "geo": geo, "query": query}})
        for a in articles[:4]:
            out.append({"source": "google", "external_id": a["url"][:300], "title": a["title"], "summary": "", "url": a["url"], "outlet": a["outlet"],
                        "reliable": reliable_outlet(a["outlet"], feed_outlets), "engagement": 0.0, "published_at": when,
                        "extra": {"group": group, "kind": "article", "query": query}})
    return out


async def google(client: httpx.AsyncClient, opts: dict[str, Any], feed_outlets: set[str] | None = None) -> tuple[list[Item], str | None, dict[str, Any]]:
    items: list[Item] = []
    problems = []
    geos = [g for g in opts.get("geo") or ["US"] if isinstance(g, str) and re.fullmatch(r"[A-Z]{2}", g)][:3]
    for geo in geos:
        try:
            r = await client.get(GOOGLE_RSS.format(geo=geo), headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml;q=0.9"})
        except httpx.HTTPError as e:
            problems.append(f"{geo}: {type(e).__name__}")
            continue
        if r.status_code != 200:
            problems.append(f"{geo}: HTTP {r.status_code}")
            continue
        got = parse_google(r.text, geo, feed_outlets)
        if not got:
            problems.append(f"{geo}: no trends in the feed")
        items += got
    return items, ("; ".join(problems) or None) if not items else None, {"requests": len(geos), "partial": "; ".join(problems) if items and problems else ""}


# --- Reddit --------------------------------------------------------------------------------------------------------------


def parse_reddit(body: dict[str, Any], sub: str) -> list[Item]:
    out: list[Item] = []
    for child in ((body.get("data") or {}).get("children") or []):
        d = child.get("data") or {}
        if d.get("stickied") or d.get("over_18") or not d.get("title") or not d.get("permalink"):
            continue
        score, comments = float(d.get("score") or 0), float(d.get("num_comments") or 0)
        created = d.get("created_utc")
        out.append({"source": "reddit", "external_id": str(d.get("name") or d.get("id"))[:300], "title": _clean(d["title"], 300),
                    "summary": "", "url": f"https://www.reddit.com{d['permalink']}"[:500], "outlet": f"r/{sub}", "reliable": False,
                    "engagement": score + 2 * comments, "published_at": datetime.fromtimestamp(float(created), tz=timezone.utc) if created else None,
                    "extra": {"score": score, "comments": comments, "ratio": d.get("upvote_ratio"), "domain": d.get("domain") or "",
                              "link": (d.get("url_overridden_by_dest") or "")[:500]}})
    return out


async def _reddit_token(client: httpx.AsyncClient, settings: Any) -> str | None:
    cid = (getattr(settings, "reddit_client_id", "") or "").strip()
    secret = getattr(settings, "reddit_client_secret", None)
    secret_value = secret.get_secret_value().strip() if secret is not None else ""
    if not cid or not secret_value:
        return None
    r = await client.post(REDDIT_TOKEN, data={"grant_type": "client_credentials"}, auth=(cid, secret_value), headers={"User-Agent": UA})
    if r.status_code != 200:
        raise RuntimeError(f"Reddit OAuth refused the app credentials (HTTP {r.status_code})")
    return str(r.json().get("access_token") or "") or None


async def reddit(client: httpx.AsyncClient, opts: dict[str, Any], settings: Any) -> tuple[list[Item], str | None, dict[str, Any]]:
    try:
        token = await _reddit_token(client, settings)
    except (httpx.HTTPError, RuntimeError, ValueError) as e:
        return [], str(e)[:200] if isinstance(e, RuntimeError) else f"Reddit OAuth: {type(e).__name__}", {"requests": 1}
    if token is None and not opts.get("public_json"):
        return [], "needs setup: add REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (Reddit OAuth app), or allow the public JSON listings in settings", {"requests": 0, "unavailable": True}
    subs = [s for s in opts.get("subreddits") or [] if isinstance(s, str) and re.fullmatch(r"[A-Za-z0-9_]{2,21}", s)][:10]
    n = max(5, min(25, int(opts.get("per_sub") or 15)))
    headers = {"User-Agent": UA, **({"Authorization": f"bearer {token}"} if token else {})}
    items: list[Item] = []
    problems: list[str] = []
    requests = 0
    for sub in subs:
        requests += 1
        try:
            r = await client.get((REDDIT_OAUTH if token else REDDIT_PUBLIC).format(sub=sub, n=n), headers=headers)
        except httpx.HTTPError as e:
            problems.append(f"r/{sub}: {type(e).__name__}")
            continue
        if r.status_code == 429:
            problems.append("rate limited by Reddit: stopped this scan")
            break
        if r.status_code != 200:
            problems.append(f"r/{sub}: HTTP {r.status_code}")
            continue
        try:
            items += parse_reddit(r.json(), sub)
        except ValueError:
            problems.append(f"r/{sub}: not JSON")
    meta = {"requests": requests, "auth": "oauth" if token else "public_json", "remaining": None, "partial": "; ".join(problems) if items else ""}
    return items, (None if items else ("; ".join(problems) or "no posts")), meta


# --- X -------------------------------------------------------------------------------------------------------------------


def parse_x_trends(body: dict[str, Any]) -> list[Item]:
    out = []
    for t in body.get("data") or []:
        name = _clean(str(t.get("trend_name") or ""), 120)
        if name:
            out.append({"source": "x_trends", "external_id": f"x:{name.lower()}"[:300], "title": name.lstrip("#"), "summary": "", "outlet": "X trends",
                        "url": "", "reliable": False, "engagement": float(t.get("tweet_count") or 0), "published_at": None, "extra": {"trend": name}})
    return out


def parse_x_posts(body: dict[str, Any]) -> list[Item]:
    users = {u["id"]: u for u in ((body.get("includes") or {}).get("users") or []) if u.get("id")}
    out = []
    for p in body.get("data") or []:
        u = users.get(p.get("author_id") or "") or {}
        handle = str(u.get("username") or "")
        m = p.get("public_metrics") or {}
        text = _clean(re.sub(r"https?://\S+", "", str(p.get("text") or "")), 300)
        if not text:
            continue
        at = None
        try:
            at = datetime.fromisoformat(str(p.get("created_at")).replace("Z", "+00:00")) if p.get("created_at") else None
        except ValueError:
            pass
        out.append({"source": "x_accounts", "external_id": f"xpost:{p.get('id')}", "title": text, "summary": "", "outlet": f"@{handle}" if handle else "X",
                    "url": f"https://x.com/{handle or 'i'}/status/{p.get('id')}", "reliable": handle.lower() in RELIABLE_X,
                    "engagement": float(m.get("like_count") or 0) + 2 * float(m.get("retweet_count") or 0) + float(m.get("reply_count") or 0),
                    "published_at": at, "extra": {"metrics": m, "handle": handle}})
    return out


async def x_source(kind: str, db: Session, settings: Any, runtime: Any, opts: dict[str, Any]) -> tuple[list[Item], str | None, dict[str, Any]]:
    """X trends or major accounts, through the shared X client (local budget guard, usage recorded). 403 = not on this plan."""
    from app.connectors.x.errors import XBudgetExceededError, XError, XForbiddenError, XNotConfiguredError, XRateLimitError
    from app.security import credentials
    from app.social.x_service import make_x_client

    if not credentials.x_credentials(settings).configured:
        return [], "X is not connected (X › Connection)", {"requests": 0, "unavailable": True}
    client = make_x_client(db, settings, runtime)
    meta: dict[str, Any] = {"requests": 1}
    try:
        if kind == "x_trends":
            body = await client.trends(int(opts.get("woeid") or 1))
            meta["cost_usd"] = 0.010
            items = parse_x_trends(body)
        else:
            handles = [h.lstrip("@") for h in opts.get("accounts") or [] if isinstance(h, str) and re.fullmatch(r"@?\w{1,15}", h)][:12]
            if not handles:
                return [], "no accounts configured", {"requests": 0}
            query = "(" + " OR ".join(f"from:{h}" for h in handles) + ") -is:retweet -is:reply"
            body = await client.search_recent(query, max_results=int(opts.get("max_posts") or 20))
            items = parse_x_posts(body)
            meta["cost_usd"] = round(0.005 * len(body.get("data") or []), 3)
        meta["rate_limits"] = dict(client.rate_limits)
        return items, (None if items else "X returned nothing"), meta
    except XForbiddenError:
        return [], "not available on your X plan (X answered 403)", {**meta, "unavailable": True, "plan": True}
    except XNotConfiguredError as e:
        return [], str(e)[:200], {**meta, "unavailable": True}
    except XBudgetExceededError as e:
        return [], f"stopped by the local X budget: {str(e)[:150]}", meta
    except XRateLimitError as e:
        return [], f"X rate limit: {str(e)[:150]}", meta
    except XError as e:
        return [], f"{type(e).__name__}: {str(e)[:150]}", meta
    finally:
        await client.aclose()
