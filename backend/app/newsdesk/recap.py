"""The market check: what a scheduled slot carries when no story clears the desk's bar (app/newsdesk/events, schedule "slots").

Only measured numbers, never news: crypto from Hyperliquid's own market data (cross-checked against Yahoo Finance when Yahoo has a fresh
price; an asset the two disagree on by more than CROSS_CHECK_PCT is left out), stocks and commodities from Yahoo Finance, and only while
their market is open (a closed market's number isn't today's move). Below MIN_ASSETS verified prices, or without Bitcoin and Ethereum,
there is no recap at all: the slot stays empty and the owner is told, rather than anything being made up.

Below the prices it may name up to two stories the desk verified in the last few hours (they passed the evidence rules but didn't score
enough for a post of their own), each with its outlet. Every figure in the text is checked against the data it came from.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent
from app.newsdesk import checks, sources

logger = logging.getLogger(__name__)

CRYPTO = (("BTC", "Bitcoin", "₿", "BTC-USD"), ("ETH", "Ethereum", "🔷", "ETH-USD"), ("HYPE", "HYPE", "🟢", None), ("SOL", "Solana", "🟣", "SOL-USD"))
MACRO = (("^GSPC", "S&P 500", "📊"), ("^IXIC", "Nasdaq", "📊"), ("GC=F", "Gold", "🥇"), ("CL=F", "Oil (WTI)", "🛢️"))
MIN_ASSETS = 3
STORY_HOURS = 4
STORY_MIN = 40  # a story named under the prices must be worth a line: a priority subject scoring at least this
TG_MAX = 900


def _price(v: float) -> str:
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"


def _pct(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}{abs(v):.1f}%"


async def _hl_markets(market: Any) -> dict[str, Any]:
    if market is None:
        from app.tradingos.market import MarketData

        market = MarketData()
    out = {}
    for coin, *_ in CRYPTO:
        try:
            out[coin] = await asyncio.to_thread(market.market, coin)
        except Exception as e:  # noqa: BLE001 - a coin Hyperliquid can't give is left out
            logger.info("newsdesk recap: Hyperliquid %s: %s", coin, type(e).__name__)
    return out


def rows(hl: dict[str, Any], quotes: dict[str, sources.Quote], now: datetime) -> list[dict[str, Any]]:
    """The verified lines: [{tag, name, emoji, price, pct, source}]."""
    out = []
    for coin, name, emoji, yahoo in CRYPTO:
        m = hl.get(coin)
        if m is None or not getattr(m, "mark", None) or not getattr(m, "prev_day", None):
            continue
        q = quotes.get(yahoo) if yahoo else None
        if q is not None and (now - q.as_of).total_seconds() <= sources.FRESH_SECONDS and abs(q.price - m.mark) / m.mark * 100 > sources.CROSS_CHECK_PCT:
            logger.info("newsdesk recap: %s left out: Hyperliquid %s vs Yahoo %s", coin, m.mark, q.price)
            continue
        out.append({"tag": f"${coin}", "name": name, "emoji": emoji, "price": m.mark, "pct": (m.mark - m.prev_day) / m.prev_day * 100, "source": "Hyperliquid"})
    for symbol, name, emoji in MACRO:
        q = quotes.get(symbol)
        change = q.change("24h", now) if q is not None else None
        if q is None or change is None:  # closed or late: not today's move
            continue
        out.append({"tag": name, "name": name, "emoji": emoji, "price": q.price, "pct": change, "source": "Yahoo Finance"})
    return out


def _stories(db: Session, now: datetime, limit: int = 2) -> list[NdEvent]:
    """Stories the desk verified lately that didn't get a post of their own (news only, with a named outlet)."""
    since = now - timedelta(hours=STORY_HOURS)
    got = db.scalars(select(NdEvent).where(NdEvent.kind == "news", NdEvent.status.in_(("candidate", "missed")), NdEvent.created_at >= since)
                     .order_by(NdEvent.virality.desc(), NdEvent.id))
    from app.newsdesk import events

    p = events.policy({})
    return [e for e in got if e.sources and str(e.sources[0].get("url", "")).startswith("https://") and not checks.NOT_NEWS.search(e.headline)
            and e.virality >= STORY_MIN and events.priority(e, p)][:limit]


def compose(lines: list[dict[str, Any]], stories: list[NdEvent], slot_local: str, as_of: datetime) -> dict[str, Any] | None:
    """Both platforms' texts, checked. None if the data is too thin or a check fails."""
    if len(lines) < MIN_ASSETS or not {"$BTC", "$ETH"} <= {r["tag"] for r in lines}:
        return None
    from html import escape

    names = list(dict.fromkeys(r["source"] for r in lines))
    mover = max(lines, key=lambda r: abs(r["pct"]))
    told = " ".join(f"{_price(r['price'])} {_pct(r['pct'])}" for r in lines) + " " + " ".join(f"{e.headline} {e.title}" for e in stories) + f" {slot_local} {as_of:%H:%M} 24h"
    head = f"📊 MARKET CHECK · {slot_local} Dubai"
    tg = [f"<b>{escape(head)}</b>", ""]
    tg += [f"{r['emoji']} {escape(r['name'])} ${_price(r['price'])} ({_pct(r['pct'])} 24h)" for r in lines]
    tg += ["", f"Biggest mover: {escape(mover['name'])} {_pct(mover['pct'])}."]
    for e in stories:
        src = e.sources[0]
        tg.append(f"📰 {escape(e.headline)} (<a href=\"{escape(src['url'], quote=True)}\">{escape(src['name'])}</a>)")
    tg += ["", f"<i>Prices: {escape(', '.join(names))}, {as_of:%H:%M} UTC.</i>"]
    html = "\n".join(tg)
    plain = "\n".join(t.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "") for t in tg)
    import re

    plain = re.sub(r"<a [^>]*>|</a>", "", plain)
    # X: short, no links (a link costs $0.20 a post), no story lines
    x_lines = [f"📊 Market check · {slot_local} Dubai", *[f"{r['tag']} {_price(r['price'])} ({_pct(r['pct'])})" for r in lines[:6]], f"Prices: {', '.join(names)}"]
    x = "\n".join(x_lines)
    while len(x) > checks.MAX_LEN and len(x_lines) > 4:
        x_lines.pop(-2)
        x = "\n".join(x_lines)
    rx = checks.check_post(x, source_text=told, source_names=names[:1])
    rt = checks.check_post(plain, source_text=told, source_names=names[:1], max_len=TG_MAX)
    if not rx.ok or not rt.ok:
        logger.info("newsdesk recap: refused by the checks: X %s / Telegram %s", rx.reason, rt.reason)
        return None
    return {"headline": head, "telegram_html": html, "telegram_plain": plain, "x_text": x, "sources": [{"name": n, "url": "https://app.hyperliquid.xyz" if n == "Hyperliquid" else "https://finance.yahoo.com"} for n in names],
            "assets": [r["tag"] for r in lines], "stories": [e.id for e in stories], "told": told}


async def build(db: Session, now: datetime, slot_at: datetime, slot_local: str, quotes: dict[str, sources.Quote], market: Any = None) -> NdEvent | None:
    """The recap event for this slot (not yet released), or None when the data can't support one."""
    hl = await _hl_markets(market)
    lines = rows(hl, quotes, now)
    stories = _stories(db, now)
    made = compose(lines, stories, slot_local, now)
    if made is None:
        logger.warning("newsdesk recap: no recap for the %s slot: %s verified prices (needs %s, with BTC and ETH)", slot_local, len(lines), MIN_ASSETS)
        return None
    return NdEvent(key=f"recap:{slot_at:%Y-%m-%dT%H:%M}", kind="recap", title=made["headline"], headline=made["headline"], summary="", topic="recap", label="📊 MARKET CHECK",
                   evidence="price data", category="crypto", virality=0.0, parts={}, sources=made["sources"], first_seen=now,
                   facts={"recap": True, "slot_at": slot_at.isoformat(), "telegram_html": made["telegram_html"], "telegram_plain": made["telegram_plain"], "x_text": made["x_text"],
                          "recap_assets": made["assets"], "stories": made["stories"], "told": made["told"], "as_of": now.isoformat()})
