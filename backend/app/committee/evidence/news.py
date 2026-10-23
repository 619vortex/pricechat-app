"""News section: headlines from public RSS / Atom feeds (CoinDesk, Cointelegraph, Decrypt by default; COMMITTEE_NEWS_FEEDS
overrides with a comma list of URLs). Keyless, read-only.

Rules:
- An item counts only if its own publication time is known, not after as_of and at most 48 h before it.
- Coin items match the ticker as a whole word in capitals (or $TICKER) or the coin's name as a whole word (any case);
  macro items match Fed / FOMC / SEC / ETF / CPI / tariff / rates / Treasury words. The analyst sees the title and at most
  300 characters of the feed's own summary (HTML stripped), never the article body.
- Bodies over 2 MB and any document with a DTD / entity declaration are refused (XML bomb safety; stdlib parser only).
- All feeds down -> status "unavailable"; some down -> "partial". Backtest -> "unavailable": RSS has no history.
- Lean is conservative: bullish only when at least 2 coin headlines carry bullish words and none bearish ones (and
  mirrored); otherwise neutral (headlines exist) or unknown (none). Score is -1, 0 or 1 at most."""

from __future__ import annotations

import asyncio
import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.committee.evidence.common import FetchError, cache_get, cache_put, iso, mark, read_capped

DEFAULT_FEEDS = ("https://www.coindesk.com/arc/outboundfeeds/rss/", "https://cointelegraph.com/rss", "https://decrypt.co/feed")
SOURCE_NAMES = {"coindesk.com": "CoinDesk", "cointelegraph.com": "Cointelegraph", "decrypt.co": "Decrypt"}
MAX_AGE_H = 48
FEED_TTL = 300.0
SUMMARY_CHARS = 300
MAX_COIN_ITEMS = 15
MAX_MACRO_ITEMS = 8
MACRO_CS = re.compile(r"\b(Fed|FOMC|SEC|ETFs?|CPI|PPI|Powell|Treasury|GDP)\b")
MACRO_CI = re.compile(r"\b(tariffs?|inflation|interest rates?|rate (cut|hike)s?|jobs report|payrolls|recession|federal reserve)\b", re.I)
BULL = re.compile(r"\b(approv\w*|inflows?|record high|all-time high|surg\w*|rall\w*|soar\w*|partnership|adoption|upgrade|launch\w*)\b", re.I)
BEAR = re.compile(r"\b(hack\w*|exploit\w*|lawsuit|sues|sued|ban\w*|outflows?|crash\w*|plung\w*|liquidat\w*|delist\w*|investigat\w*|probe|"
                  r"drain\w*|tumbl\w*|slump\w*)\b", re.I)


def feeds() -> list[str]:
    raw = os.environ.get("COMMITTEE_NEWS_FEEDS", "").strip()
    return [u.strip() for u in raw.split(",") if u.strip()] if raw else list(DEFAULT_FEEDS)


def source_name(url: str) -> str:
    host = (urlparse(url).hostname or url).removeprefix("www.")
    return SOURCE_NAMES.get(host, host)


def _text(el: ET.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, *names: str) -> ET.Element | None:
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _when(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)  # RSS: RFC 822
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))  # Atom: RFC 3339
        except ValueError:
            return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse(body: bytes, source: str) -> list[dict[str, Any]]:
    """RSS 2.0 <item> or Atom <entry> -> [{title, source, url, published (datetime), summary}]. Raises FetchError."""
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in body.lower():
        raise FetchError(f"{source} feed refused (DTD / entity declarations)")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise FetchError(f"{source} feed isn't valid XML") from e
    out = []
    for el in root.iter():
        kind = _local(el.tag)
        if kind not in ("item", "entry"):
            continue
        title = _clean(_text(_child(el, "title")))
        link_el = _child(el, "link")
        url = (link_el.get("href") if link_el is not None and link_el.get("href") else _text(link_el)) or None
        when = _when(_text(_child(el, "pubDate", "published", "updated", "date")))
        summary = _clean(_text(_child(el, "description", "summary")))[:SUMMARY_CHARS]
        if title and when:
            out.append({"title": title[:300], "source": source, "url": url, "published": when, "summary": summary})
    return out


async def fetch_feed(http: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    hit = cache_get(f"rss:{url}", FEED_TTL)
    if hit is not None:
        return hit
    name = source_name(url)
    body = await read_capped(http, "GET", url, what=name)
    return cache_put(f"rss:{url}", parse(body, name))


def _matcher(symbol: str, name: str | None) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    sym = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    sym_re = re.compile(rf"(?<![A-Za-z0-9])\$?{re.escape(sym)}(?![A-Za-z0-9])") if len(sym) >= 2 else None
    nm = (name or "").strip()
    name_re = re.compile(rf"\b{re.escape(nm)}\b", re.I) if len(nm) >= 4 and nm.upper() != sym else None
    return sym_re, name_re


async def collect(http: httpx.AsyncClient, symbol: str, name: str | None, as_of: datetime) -> dict[str, Any]:
    """{"status", "limitations", "items", "feeds": [{"source","url","ok","items","error"}]}."""
    urls = feeds()
    results = await asyncio.gather(*(fetch_feed(http, u) for u in urls), return_exceptions=True)
    sym_re, name_re = _matcher(symbol, name)
    lo = as_of - timedelta(hours=MAX_AGE_H)
    coin: list[dict[str, Any]] = []
    macro: list[dict[str, Any]] = []
    feed_rows = []
    seen: set[str] = set()
    for url, res in zip(urls, results):
        if isinstance(res, BaseException):
            err = str(res) if isinstance(res, FetchError) else f"{source_name(url)}: {type(res).__name__}"
            feed_rows.append({"source": source_name(url), "url": url, "ok": False, "items": 0, "error": err})
            continue
        feed_rows.append({"source": source_name(url), "url": url, "ok": True, "items": len(res), "error": None})
        for it in res:
            if not (lo <= it["published"] <= as_of):
                continue  # never newer than as_of, never older than 48 h
            key = (it["url"] or it["title"]).lower()
            if key in seen:
                continue
            text = f"{it['title']} {it['summary']}"
            match = "symbol" if sym_re and sym_re.search(text) else "name" if name_re and name_re.search(text) else None
            if match is None and (MACRO_CS.search(it["title"]) or MACRO_CI.search(it["title"])):
                match = "macro"
            if match is None:
                continue
            seen.add(key)
            row = {"title": it["title"], "source": it["source"], "url": it["url"], "published": iso(it["published"]),
                   "age_h": round((as_of - it["published"]).total_seconds() / 3600, 1), "match": match, "summary": it["summary"]}
            (macro if match == "macro" else coin).append(row)
    ok = [f for f in feed_rows if f["ok"]]
    mark("news", bool(ok), f"{len(ok)}/{len(feed_rows)} feeds answered" + "".join(f"; {f['error']}" for f in feed_rows if not f["ok"]))
    lim = [f"{f['error']}" for f in feed_rows if not f["ok"]]
    status = "unavailable" if not ok else "partial" if lim else "ok"
    if not ok:
        lim.append("every news feed failed: no headlines")
    coin.sort(key=lambda r: r["age_h"])
    macro.sort(key=lambda r: r["age_h"])
    return {"status": status, "limitations": lim, "items": coin[:MAX_COIN_ITEMS] + macro[:MAX_MACRO_ITEMS], "feeds": feed_rows}


def lean(items: list[dict[str, Any]]) -> tuple[str, int | None, list[str]]:
    coin = [i for i in items if i["match"] != "macro"]
    if not coin:
        return "unknown", None, []
    bull = [i for i in coin if BULL.search(i["title"])]
    bear = [i for i in coin if BEAR.search(i["title"])]
    why = [f"{len(coin)} headline(s) about the coin in the last {MAX_AGE_H} h; {len(bull)} with bullish words, {len(bear)} with bearish words"]
    if len(bull) >= 2 and not bear:
        return "bullish", 1, why
    if len(bear) >= 2 and not bull:
        return "bearish", -1, why
    return "neutral", 0, why
