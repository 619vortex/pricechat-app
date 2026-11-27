"""The market brief: what a scheduled slot carries when no story clears the desk's bar (app/newsdesk/events, schedule "slots").

Written as a short news item, not a price alert (owner, 2026-11-09): a headline that says what the market did, measured from the data
("Crypto slips, Ethereum down 4.3% in 24 hours"), the moves in one or two lines, what positioning on Hyperliquid says (funding, with a
one-line explanation when it has flipped negative), and the stories the desk verified. No alert labels, no siren emoji.

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
from app.newsdesk import checks, markethours, sources

logger = logging.getLogger(__name__)

CRYPTO = (("BTC", "Bitcoin", "₿", "BTC-USD"), ("ETH", "Ethereum", "🔷", "ETH-USD"), ("HYPE", "HYPE", "🟢", None), ("SOL", "Solana", "🟣", "SOL-USD"))
MACRO = (("^GSPC", "S&P 500", "📊"), ("^IXIC", "Nasdaq", "📊"), ("GC=F", "Gold", "🥇"), ("SI=F", "Silver", "🥈"), ("CL=F", "Oil (WTI)", "🛢️"))
MIN_ASSETS = 3
STORY_HOURS = 4
STORY_MIN = 40  # a story named under the prices must be worth a line: a priority subject scoring at least this
TG_MAX = 900


def _price(v: float) -> str:
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"


def _pct(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}{abs(v):.1f}%"


# a second, independent price for the macro movers (owner: gold and silver had only Yahoo's own figures): Hyperliquid's trade.xyz markets
XYZ = {"Gold": "xyz:GOLD", "Silver": "xyz:SILVER", "Oil (WTI)": "xyz:CL", "Brent": "xyz:BRENTOIL"}


async def xyz_change(name: str, transport: Any = None) -> float | None:
    """24-hour change of the trade.xyz market for this macro name (mark vs the price 24 hours ago), or None."""
    coin = XYZ.get(name)
    if not coin:
        return None
    import httpx

    try:
        async with httpx.AsyncClient(transport=transport, timeout=8) as c:
            r = await c.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs", "dex": "xyz"})
            r.raise_for_status()
            meta, ctxs = r.json()
        for a, x in zip(meta["universe"], ctxs):
            if a.get("name") == coin:
                mark, prev = float(x.get("markPx") or 0), float(x.get("prevDayPx") or 0)
                return (mark / prev - 1) * 100 if mark > 0 and prev > 0 else None
    except Exception as e:  # noqa: BLE001 - no second source: the gate falls back to the first source's own summary
        logger.info("newsdesk recap: trade.xyz %s: %s", coin, type(e).__name__)
    return None


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
        out.append({"tag": f"${coin}", "name": name, "emoji": emoji, "price": m.mark, "pct": (m.mark - m.prev_day) / m.prev_day * 100, "source": "Hyperliquid",
                    "funding": getattr(m, "funding", None)})
    for symbol, name, emoji in MACRO:
        q = quotes.get(symbol)
        change = q.change("24h", now) if q is not None else None
        if q is not None and symbol.startswith("^"):  # a US index: its session against the official previous close, never a rolling 24 h
            change = (q.price - q.previous_close) / q.previous_close * 100 if q.previous_close and markethours.fresh(q.as_of, now) else None
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


def headline(lines: list[dict[str, Any]]) -> str:
    """What the market did, in words measured from the numbers: the direction of Bitcoin and Ethereum, and the biggest move if it's big."""
    by = {r["tag"]: r for r in lines}
    avg = (by["$BTC"]["pct"] + by["$ETH"]["pct"]) / 2
    verb = ("Crypto slides" if avg <= -2.5 else "Crypto slips" if avg <= -0.5 else "Crypto rallies" if avg >= 2.5 else "Crypto edges higher" if avg >= 0.5 else "Crypto holds steady")
    lead = max(lines, key=lambda r: abs(r["pct"]))
    if abs(lead["pct"]) >= 2:
        return f"{verb}, {lead['name']} {'down' if lead['pct'] < 0 else 'up'} {abs(lead['pct']):.1f}% in 24 hours"
    return f"{verb} over the last 24 hours"


def _funding(lines: list[dict[str, Any]]) -> str:
    """Hyperliquid's hourly funding for Bitcoin and Ethereum, explained only when it says something (negative: shorts pay longs)."""
    got = [(r["name"], r["funding"]) for r in lines if r["tag"] in ("$BTC", "$ETH") and isinstance(r.get("funding"), (int, float))]
    if not got:
        return ""
    text = "Funding on Hyperliquid: " + ", ".join(f"{n} {f * 100:+.4f}% an hour" for n, f in got) + "."
    if any(f < 0 for _, f in got):
        text += " Negative funding means traders betting on a fall are paying those betting on a rise."
    return text


def compose(lines: list[dict[str, Any]], stories: list[NdEvent], slot_local: str, as_of: datetime) -> dict[str, Any] | None:
    """Both platforms' texts, checked. None if the data is too thin or a check fails."""
    if len(lines) < MIN_ASSETS or not {"$BTC", "$ETH"} <= {r["tag"] for r in lines}:
        return None
    import re
    from html import escape

    names = list(dict.fromkeys(r["source"] for r in lines))
    head = headline(lines)
    crypto = [r for r in lines if r["source"] == "Hyperliquid"]
    macro = [r for r in lines if r["source"] != "Hyperliquid"]
    fund = _funding(lines)
    told = (" ".join(f"{_price(r['price'])} {_pct(r['pct'])} {abs(r['pct']):.1f}%" for r in lines) + " " + " ".join(f"{e.headline} {e.title}" for e in stories)
            + f" {fund} {slot_local} {as_of:%H:%M} 24"
            # the assets measured here (their own verified prices) establish their tickers for the ticker check
            + " " + " ".join(f"{r['name']} {r.get('tag') or ''}" for r in lines))
    tg = [f"<b>{escape(head)}</b>", "", " · ".join(f"{escape(r['name'])} ${_price(r['price'])} ({_pct(r['pct'])})" for r in crypto)]
    if macro:
        tg.append(" · ".join(f"{escape(r['name'])} {_price(r['price'])} ({_pct(r['pct'])})" for r in macro))
    if fund:
        tg += ["", escape(fund)]
    if stories:
        tg += ["", "Also verified today:"]
        for e in stories:
            src = e.sources[0]
            tg.append(f"• {escape(e.headline)} (<a href=\"{escape(src['url'], quote=True)}\">{escape(src['name'])}</a>)")
    tg += ["", f"<i>24-hour changes. Data: {escape(', '.join(names))}, {as_of:%H:%M} UTC</i>"]
    html = "\n".join(tg)
    plain = re.sub(r"<[^>]+>", "", html)
    # X: the headline and the moves, no links (a link costs $0.20 a post)
    moves_x = [f"{r['tag'] if r['source'] == 'Hyperliquid' else r['name']} {_pct(r['pct'])}" for r in lines]
    x = f"{head}.\n\n" + ", ".join(moves_x) + f"\n\nData: {', '.join(names)}"
    while len(x) > checks.MAX_LEN and len(moves_x) > 2:
        moves_x.pop()
        x = f"{head}.\n\n" + ", ".join(moves_x) + f"\n\nData: {', '.join(names)}"
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
    return NdEvent(key=f"recap:{slot_at:%Y-%m-%dT%H:%M}", kind="recap", title=made["headline"], headline=made["headline"], summary="", topic="recap", label="MARKET BRIEF",
                   evidence="price data", category="crypto", virality=0.0, parts={}, sources=made["sources"], first_seen=now,
                   facts={"recap": True, "slot_at": slot_at.isoformat(), "telegram_html": made["telegram_html"], "telegram_plain": made["telegram_plain"], "x_text": made["x_text"],
                          "recap_assets": made["assets"], "stories": made["stories"], "told": made["told"], "as_of": now.isoformat()})


# --- the visual format (owner, 2026-11-09): the slot without a story carries the day's biggest real move as a chart, or nothing ---------

VISUAL = True  # tests of the older written brief set this False


def _subject(r: dict[str, Any]) -> str:
    from app.newsdesk import editorial

    return editorial.canon(r["tag"] if r["source"] == "Hyperliquid" else r["name"])


def biggest_move(lines: list[dict[str, Any]], covered: list[Any] | None = None, now: datetime | None = None, repeats: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """(the move this slot may carry, why each other one can't). Owner, 2026-11-10: only an EXCEPTIONAL move (at or past the desk's 24-hour
    alert line for its class: crypto majors 8%, other crypto 15%, indices 2.5%, commodities 5%), and never one the channel already showed
    unless it has escalated (app/newsdesk/editorial.judge). The old floor (3% crypto, 1.5% macro) posted routine days: Ethereum -6% twice,
    then WTI -4.4% and -6.0% four hours apart."""
    from app.newsdesk import editorial

    notes: list[str] = []
    ranked = []
    for r in lines:
        subj = _subject(r)
        cls = editorial.asset_class({subj})
        if not editorial.exceptional_pct(cls, r["pct"]):
            notes.append(f"{r['name']} {_pct(r['pct'])}: routine (under {editorial.TIERS[cls][0]:g}%)")
            continue
        ranked.append((editorial.tier(cls, r["pct"]), abs(r["pct"]) / editorial.TIERS[cls][0], r, subj, cls))
    for _, _, r, subj, cls in sorted(ranked, key=lambda x: (-x[0], -x[1])):
        sig = {"subjects": [subj], "type": "market", "direction": "down" if r["pct"] < 0 else "up", "magnitude": round(abs(r["pct"]), 2), "class": cls, "cause": False, "event": None}
        v = editorial.judge(sig, covered or [], now) if now is not None else editorial.Verdict(True)
        if v.ok:
            return r, notes
        notes.append(f"{r['name']} {_pct(r['pct'])}: {v.reason}")
        if repeats is not None:
            repeats.append({"subject": subj, "headline": f"{r['name']} {_pct(r['pct'])} in 24 hours", "reason": v.reason, "same_as": v.same_as})
    return None, notes


async def build_visual(db: Session, now: datetime, slot_at: datetime, slot_local: str, quotes: dict[str, sources.Quote], market: Any = None) -> NdEvent | None:
    from app.newsdesk import editorial, visual

    hl = await _hl_markets(market)
    lines = rows(hl, quotes, now)
    repeats: list[dict[str, Any]] = []
    mover, notes = biggest_move(lines, editorial.coverage(db, now), now, repeats)
    for x in repeats:  # turned away as a repeat: on the story's record, for both platforms
        editorial.record_skip(db, now, {**x, "slot": slot_at.isoformat()})
    if mover is None:
        logger.warning("newsdesk recap: the %s slot has no story and no exceptional new market move: nothing posted (%s)", slot_local, "; ".join(notes)[:600] or "no prices")
        return None
    points: list[tuple[int, float]] = []
    coin = mover["tag"].lstrip("$") if mover["source"] == "Hyperliquid" else ""
    if coin:
        try:
            points = await visual.candles_for(coin, market)
        except Exception as e:  # noqa: BLE001
            logger.info("newsdesk recap: candles for %s: %s", coin, type(e).__name__)
    else:
        sym = next((s for s, n, _ in MACRO if n == mover["name"]), "")
        q = quotes.get(sym)
        cut = now.timestamp() - 86400
        points = [(t, c) for t, c, _ in (q.bars if q else []) if t >= cut]
    if len(points) < 10:
        logger.warning("newsdesk recap: no price history to draw for %s: nothing posted", mover["name"])
        return None
    # the numbers gate: the headline's figure is the chart's own series; the summary figure (Hyperliquid's mark vs its previous day, or
    # Yahoo's 24-hour change) must agree with it; crypto is also checked against Yahoo when Yahoo has a fresh price
    series = (points[-1][1] - points[0][1]) / points[0][1] * 100
    yahoo = next((y for c, _, _, y in CRYPTO if c == coin), None) if coin else None
    yq = quotes.get(yahoo) if yahoo else None
    second = yq.change("24h", now) if yq is not None else None
    second_name = "Yahoo Finance" if second is not None else mover["source"]
    if second is None and not coin:
        second = await xyz_change(mover["name"])
        if second is not None:
            second_name = "Hyperliquid"  # no "trade.xyz": a domain-like name fails the X link check
    if second is None:
        second = mover["pct"]  # the source's own summary figure, from a different call than the series
    head = visual.move_headline(mover["name"], series)
    check = visual.verify_series(points, window="24H", headline=head, as_of=now, now=now, max_age_minutes=markethours.FRESH.total_seconds() / 60,
                                 second_pct=second, second_name=second_name)
    cls = editorial.asset_class({_subject(mover)})
    if not check.ok or not editorial.exceptional_pct(cls, series):
        why = "; ".join(check.reasons) or f"the chart's own series says {series:+.2f}%, not exceptional"
        logger.warning("newsdesk recap: the %s slot's %s chart failed the numbers gate: %s: nothing posted", slot_local, mover["name"], why)
        return None
    mover = {**mover, "pct": series}
    told = f"{abs(series):.1f}% {mover['name']} 24 hours"
    return NdEvent(key=f"recap:{slot_at:%Y-%m-%dT%H:%M}", kind="recap", title=head, headline=head, summary="", topic="move", label="", evidence="price data",
                   category="crypto" if coin else "commodities", virality=0.0, parts={}, sources=[{"name": mover["source"], "url": "https://app.hyperliquid.xyz" if coin else "https://finance.yahoo.com"}],
                   first_seen=now, facts={"recap": True, "slot_at": slot_at.isoformat(), "visual_headline": head, "points": [list(p) for p in points], "pct": mover["pct"],
                                          "name": mover["name"], "coin": coin or mover["name"].split()[0][:4].upper(), "data_source": mover["source"], "told": told,
                                          "recap_assets": [mover["tag"]], "as_of": now.isoformat(), "window": "24h",
                                          "number_check": check.out(), "second_pct": second, "second_source": second_name,
                                          "x_text": visual.x_text(head, mover["source"]), "telegram_html": visual.caption_html(head, mover["source"]),
                                          "telegram_plain": f"{head}\n\nSource: {mover['source']}"})
