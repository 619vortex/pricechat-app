"""Free, documented discovery sources the Terminal adds to Trending's signal store (owner, 2026-11-18: "continuously discover major
developments ... reliable public feeds, official incident sources ... no unauthorized scraping or invented engagement metrics").

Every source is a published, keyless API or feed, read at a low rate with a generic User-Agent:

- usgs: the US Geological Survey's real-time earthquake feeds (earthquake.usgs.gov GeoJSON). Official: it may back a factual claim about
  the quake it lists (magnitude, place, time), nothing more. Only magnitude 6+, a PAGER alert of orange/red, or a tsunami flag.
- gdacs: the UN/EC Global Disaster Alert and Coordination System RSS (gdacs.org). Official for the alert it lists. Orange and red only.
- gdelt: the GDELT Project's DOC 2.0 API (api.gdeltproject.org), which indexes news sites worldwide every 15 minutes. One request per read,
  well inside its one-request-per-5-seconds rule. NEVER reliable: an early-web signal (a story spreading on local or niche sites before
  the major outlets have it), shown in the Terminal and counted as momentum, never as evidence.
- hn: Hacker News' official Firebase API. Technology and cyber momentum: points and comments as Hacker News reports them. Never reliable.

Each fetcher returns (items, problem, meta) like app/trending/sources. Nothing here writes to the database.
"""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

UA = "server:pricechat-terminal:1.0"
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
GDACS = "https://www.gdacs.org/xml/rss.xml"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&maxrecords={n}&timespan={span}&sort=datedesc&format=json"
HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"

# what GDELT is asked for: the hard-event vocabulary of a breaking desk (attacks, disasters, outages, hacks, crashes, halts)
GDELT_QUERY = ('(explosion OR attack OR airstrike OR missile OR earthquake OR outage OR hacked OR exploit OR "trading halted" OR '
               '"bank run" OR crash OR killed OR evacuated OR "state of emergency" OR coup OR sanctions OR depeg OR bankruptcy) sourcelang:english')
HARD = re.compile(r"\b(explosion|blast|attack\w*|air ?strikes?|missiles?|drones?|earthquake|tsunami|hurricane|typhoon|wildfire|flood\w*|outage\w*|"
                  r"down|hack\w*|exploit\w*|drained|halt\w*|bank run|crash\w*|plunge\w*|collapse\w*|killed|dead|evacuat\w*|emergency|coup|"
                  r"sanction\w*|depeg\w*|bankrupt\w*|seized|arrested|shot|strike on|invasion|grounded|ground stop)\b", re.I)
GDACS_KIND = {"EQ": "earthquake", "TC": "tropical cyclone", "FL": "flood", "VO": "volcano eruption", "DR": "drought", "WF": "wildfire", "TS": "tsunami"}
DEFAULTS: dict[str, dict[str, Any]] = {
    "usgs": {"on": True, "min_magnitude": 6.0},
    "gdacs": {"on": True},
    "gdelt": {"on": True, "max_records": 75, "timespan": "60min"},
    "hn": {"on": True, "min_points": 150, "top": 30},
}
NAMES = {"usgs": "USGS earthquakes", "gdacs": "GDACS disaster alerts", "gdelt": "GDELT early web reports", "hn": "Hacker News"}
INTERVAL = {"usgs": 5, "gdacs": 15, "gdelt": 10, "hn": 15}  # minutes between reads

Item = dict[str, Any]
Result = tuple[list[Item], str | None, dict[str, Any]]


def _get_headers() -> dict[str, str]:
    return {"User-Agent": UA, "Accept": "application/json, application/xml, text/xml, */*"}


# --- USGS ------------------------------------------------------------------------------------------------------------------------------


def parse_usgs(body: dict[str, Any], min_magnitude: float = 6.0) -> list[Item]:
    out: list[Item] = []
    for f in (body or {}).get("features") or []:
        p = (f or {}).get("properties") or {}
        try:
            mag = float(p.get("mag") or 0)
        except (TypeError, ValueError):
            continue
        alert = str(p.get("alert") or "").lower()
        tsunami = bool(p.get("tsunami"))
        if mag < min_magnitude and alert not in ("orange", "red") and not (tsunami and mag >= 5.5):
            continue
        place = re.sub(r"\s+", " ", str(p.get("place") or "")).strip()[:160]
        if not place:
            continue
        when = datetime.fromtimestamp(int(p.get("time") or 0) / 1000, tz=timezone.utc) if p.get("time") else None
        title = f"Magnitude {mag:.1f} earthquake strikes {place}" + (", tsunami alert issued" if tsunami else "")
        detail = [f"USGS: magnitude {mag:.1f}, {place}"]
        if alert:
            detail.append(f"PAGER alert level {alert}")
        if tsunami:
            detail.append("tsunami flag set by the USGS")
        out.append({"source": "usgs", "external_id": f"usgs:{f.get('id') or p.get('code')}"[:300], "title": title[:300], "summary": "; ".join(detail),
                    "url": str(p.get("url") or "https://earthquake.usgs.gov/"), "outlet": "USGS", "reliable": True, "engagement": mag * 10,
                    "published_at": when, "category_hint": None,
                    "extra": {"official": True, "magnitude": mag, "alert": alert, "tsunami": tsunami, "kind": "earthquake"}})
    return out


async def usgs(client: httpx.AsyncClient, opts: dict[str, Any]) -> Result:
    try:
        r = await client.get(USGS, headers=_get_headers())
    except httpx.HTTPError as e:
        return [], f"USGS: {type(e).__name__}", {"requests": 1}
    if r.status_code != 200:
        return [], f"USGS: HTTP {r.status_code}", {"requests": 1}
    try:
        items = parse_usgs(r.json(), float(opts.get("min_magnitude") or 6.0))
    except ValueError:
        return [], "USGS: not JSON", {"requests": 1}
    return items, None, {"requests": 1, "idle": not items}


# --- GDACS -----------------------------------------------------------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_gdacs(xml_text: str, now: datetime, hours: int = 48) -> list[Item]:
    if len(xml_text) > 5_000_000 or "<!ENTITY" in xml_text:
        return []
    try:
        root = ET.fromstring(xml_text.lstrip("﻿"))
    except ET.ParseError:
        return []
    out: list[Item] = []
    for it in root.iter():
        if _local(it.tag) != "item":
            continue
        f = {_local(c.tag): (c.text or "").strip() for c in it}
        level = (f.get("alertlevel") or "").lower()
        if level not in ("orange", "red"):
            continue
        if (f.get("iscurrent") or "true").lower() == "false":
            continue
        when = None
        try:
            when = parsedate_to_datetime(f.get("pubDate") or f.get("fromdate") or "")
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
        if when is not None and now - when > timedelta(hours=hours):
            continue
        kind = GDACS_KIND.get((f.get("eventtype") or "").upper(), "disaster")
        title = re.sub(r"\s+", " ", f.get("title") or "").strip()
        if not title:
            continue
        out.append({"source": "gdacs", "external_id": f"gdacs:{f.get('eventtype')}:{f.get('eventid')}:{f.get('episodeid')}"[:300],
                    "title": title[:300], "summary": re.sub(r"<[^>]+>", " ", f.get("description") or "")[:600], "url": f.get("link") or "https://www.gdacs.org/",
                    "outlet": "GDACS", "reliable": True, "engagement": 100.0 if level == "red" else 50.0, "published_at": when, "category_hint": None,
                    "extra": {"official": True, "alert": level, "event": kind, "kind": kind, "country": f.get("country") or ""}})
    return out


async def gdacs(client: httpx.AsyncClient, opts: dict[str, Any], now: datetime) -> Result:
    try:
        r = await client.get(GDACS, headers=_get_headers())
    except httpx.HTTPError as e:
        return [], f"GDACS: {type(e).__name__}", {"requests": 1}
    if r.status_code != 200:
        return [], f"GDACS: HTTP {r.status_code}", {"requests": 1}
    items = parse_gdacs(r.text, now)
    return items, None, {"requests": 1, "idle": not items}


# --- GDELT -----------------------------------------------------------------------------------------------------------------------------


def _gdelt_time(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_gdelt(body: dict[str, Any]) -> list[Item]:
    out: list[Item] = []
    seen: set[str] = set()
    for a in (body or {}).get("articles") or []:
        title = re.sub(r"\s+", " ", str(a.get("title") or "")).strip()
        url = str(a.get("url") or "")
        if not title or not url.startswith("http") or not HARD.search(title):
            continue
        key = re.sub(r"\W+", " ", title.lower()).strip()[:120]
        if key in seen:
            continue
        seen.add(key)
        domain = str(a.get("domain") or "").lower()[:80]
        out.append({"source": "gdelt", "external_id": url[:300], "title": title[:300], "summary": "", "url": url[:500], "outlet": domain or "web",
                    "reliable": False, "engagement": 1.0, "published_at": _gdelt_time(str(a.get("seendate") or "")), "category_hint": None,
                    "extra": {"domain": domain, "country": str(a.get("sourcecountry") or "")[:40], "kind": "early_web"}})
    return out[:40]


async def gdelt(client: httpx.AsyncClient, opts: dict[str, Any]) -> Result:
    url = GDELT.format(q=quote(GDELT_QUERY), n=int(opts.get("max_records") or 75), span=quote(str(opts.get("timespan") or "60min")))
    try:
        # 2026-11-24: from Railway every read failed to connect (ConnectTimeout, 82 of 82); from elsewhere GDELT answered a single request
        # 429 after a 14 s wait. A short connect timeout gives up fast; app/trending/engine._due rests the source after REST_AFTER misses.
        r = await client.get(url, headers=_get_headers(), timeout=httpx.Timeout(8, connect=4))
    except httpx.HTTPError as e:
        return [], f"GDELT: {type(e).__name__}", {"requests": 1, "idle": True}
    if r.status_code == 429:
        return [], "GDELT: rate limited (tries again next read)", {"requests": 1, "idle": True}
    if r.status_code != 200:
        return [], f"GDELT: HTTP {r.status_code}", {"requests": 1}
    try:
        body = r.json()
    except ValueError:  # GDELT answers a plain-text message for a bad or too-busy query
        return [], f"GDELT: {r.text[:120]!r}", {"requests": 1, "idle": True}
    items = parse_gdelt(body)
    return items, None, {"requests": 1, "idle": not items}


# --- Hacker News -----------------------------------------------------------------------------------------------------------------------


def parse_hn(item: dict[str, Any], min_points: int) -> Item | None:
    if not item or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
        return None
    pts = int(item.get("score") or 0)
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    if pts < min_points or not title or title.lower().startswith(("ask hn", "show hn", "tell hn", "launch hn")):
        return None
    when = datetime.fromtimestamp(int(item.get("time") or 0), tz=timezone.utc) if item.get("time") else None
    hid = int(item.get("id") or 0)
    return {"source": "hn", "external_id": f"hn:{hid}", "title": title[:300], "summary": "", "url": str(item.get("url") or f"https://news.ycombinator.com/item?id={hid}")[:500],
            "outlet": "Hacker News", "reliable": False, "engagement": float(pts), "published_at": when, "category_hint": None,
            "extra": {"points": pts, "comments": int(item.get("descendants") or 0), "thread": f"https://news.ycombinator.com/item?id={hid}", "kind": "hn"}}


async def hn(client: httpx.AsyncClient, opts: dict[str, Any]) -> Result:
    try:
        r = await client.get(HN_TOP, headers=_get_headers(), timeout=6)
        ids = [int(i) for i in (r.json() or [])[: int(opts.get("top") or 30)]] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError, TypeError) as e:
        return [], f"Hacker News: {type(e).__name__}", {"requests": 1}
    if r.status_code != 200:
        return [], f"Hacker News: HTTP {r.status_code}", {"requests": 1}
    if not ids:
        return [], None, {"requests": 1, "idle": True}
    sem = asyncio.Semaphore(6)

    async def one(i: int) -> dict[str, Any] | None:
        async with sem:
            try:
                x = await client.get(HN_ITEM.format(id=i), headers=_get_headers(), timeout=6)
                return x.json() if x.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                return None

    raw = await asyncio.gather(*(one(i) for i in ids))
    mp = int(opts.get("min_points") or 150)
    items = [it for it in (parse_hn(x or {}, mp) for x in raw) if it]
    return items, None, {"requests": 1 + len(ids), "idle": not items}


async def read(name: str, client: httpx.AsyncClient, opts: dict[str, Any], now: datetime) -> Result:
    if name == "usgs":
        return await usgs(client, opts)
    if name == "gdacs":
        return await gdacs(client, opts, now)
    if name == "gdelt":
        return await gdelt(client, opts)
    if name == "hn":
        return await hn(client, opts)
    return [], f"unknown source {name}", {}
