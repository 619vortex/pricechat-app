"""Live market figures, re-read right before ANY post carrying them goes out, on every platform (owner, 2026-11-20).

Production 2026-11-20: the silver data post (event #880) was built from a 01:40 UTC price, waited for the 04:00 UTC slot, and Telegram's
final check refused it as 140 minutes old; X's own-text path had no such check and published the same 2h15m-old "so far today: -6.7%".

The rule now, for measured market posts (a live move, level or extreme from app/newsdesk/moves; a one-day anomaly from
app/newsdesk/datapost while its session is trading):

  refresh(db, ev, now)  re-reads the live price and rebuilds the figures, the percentage change, the timestamp, the headline, the summary
                        and the told text from it, the same generators that made the post. Called by Telegram (telegram.tick) and X
                        (engine.deliver_x) right before each writes its post.
                        "ok"     the move still stands: the event now carries the fresh figures (facts.live says when they were read)
                        "hold"   no fresh price could be read (a feed down, a quote older than LIVE_PRICE_MAX): nothing goes out now; the
                                 next run tries again, until the event's delivery window closes (then it is missed and the owner told)
                        "faded"  the price was read and the move no longer stands (back inside its alert line, reversed, no longer a
                                 one-day anomaly): it is not published at all
  problem(ev, now)      the one freshness check every sending path runs (Telegram's final check, X's final editorial hold): the figures
                        were re-read within CHECK_MAX and the price they carry is within LIVE_PRICE_MAX. Never a network call.

Nothing here invents a figure: when the price can't be read, the post waits; it is never sent with the old one.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.newsdesk import datapost, headline, moves, sources, urgency

logger = logging.getLogger(__name__)

LIVE_PRICE_MAX = timedelta(seconds=moves.FRESH)  # 20 minutes: the price a market post prints, when it goes out
CHECK_MAX = timedelta(minutes=10)  # the figures were re-read this recently, when the post goes out
REUSE_FOR = timedelta(minutes=2)  # X following Telegram a moment later prints the figures Telegram just read: both say the same thing
MARKET_KINDS = ("move", "wide", "high", "low", "milestone")
LIVE_DATA = ("intraday", "utc_day")
OK, HOLD, FADED = "ok", "hold", "faded"
# a price the desk itself read within SEEN_FOR seconds (this process, this tick: events.decide remembers what it read) is the re-read: the
# same Yahoo answer, seconds old; anything older is fetched again
SEEN_FOR = 90.0
_seen_quotes: dict[str, tuple[float, sources.Quote]] = {}
_seen_daily: dict[str, tuple[float, list[tuple[datetime, float]], Any]] = {}


def remember(quotes: dict[str, sources.Quote] | None = None, daily: dict[str, list[tuple[datetime, float]]] | None = None) -> None:
    """What the desk just read (events.decide calls it), so a post made from it seconds later doesn't read Yahoo again."""
    t = time.monotonic()
    for sym, q in (quotes or {}).items():
        _seen_quotes[sym] = (t, q)
    for sym, series in (daily or {}).items():
        if series:
            _seen_daily[sym] = (t, series, datapost._last.get(sym))


def forget() -> None:
    _seen_quotes.clear()
    _seen_daily.clear()


async def _fetch_quote(symbol: str, transport: Any) -> sources.Quote | None:
    async with httpx.AsyncClient(transport=transport, timeout=15) as client:
        return await sources.fetch_quote(client, symbol)


async def _fetch_daily(symbol: str, transport: Any) -> tuple[list[tuple[datetime, float]], Any]:
    async with httpx.AsyncClient(transport=transport, timeout=15) as client:
        series = await datapost.fetch_daily(client, symbol, max_age=60)
    return series, datapost._last.get(symbol)


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return _utc(t).isoformat()


def needs_live(ev: Any) -> bool:
    """A post whose figures are a live price we measured: re-read before it goes out."""
    f = ev.facts or {}
    if ev.kind in ("market", "update") and f.get("symbol") and f.get("kind") in MARKET_KINDS:
        return True
    if ev.kind == "data":
        d = f.get("data") or {}
        return d.get("kind") == "anomaly" and ((d.get("last") or {}).get("kind") in LIVE_DATA)
    return False


def problem(ev: Any, now: datetime) -> str:
    """Why a market post can't go out now ("" = it can): the same rule on every platform and every path."""
    if not needs_live(ev):
        return ""
    live = (ev.facts or {}).get("live") or {}
    if not live.get("checked_at"):
        return "its live price was not re-read before sending"
    checked = _utc(datetime.fromisoformat(str(live["checked_at"])))
    if now - checked > CHECK_MAX:
        return f"its live price was re-read {int((now - checked).total_seconds() // 60)} min ago, not just before sending"
    at = _utc(datetime.fromisoformat(str(live["at"])))
    if now - at > LIVE_PRICE_MAX:
        return f"its live price is from {at:%H:%M} UTC, {int((now - at).total_seconds() // 60)} min ago"
    return ""


# --- the daily context (app/newsdesk/urgency) -------------------------------------------------------------------------------


def daily_series(symbol: str) -> tuple[list[tuple[datetime, float]], bool | None]:
    """The daily closes datapost last fetched for symbol (its cache; no network) and whether the last one is the live session."""
    sym = urgency.LEAD.get(symbol, symbol)
    hit = datapost._cache.get(f"{sym}:2y")
    last = datapost._last.get(sym)
    return (hit[1] if hit else []), (last.kind in LIVE_DATA if last is not None else None)


def assess(symbol: str, change_pct: float, ratio: float, now: datetime, daily: dict[str, Any] | None = None) -> urgency.Urgency:
    """The move's urgency against the asset's own daily closes (`daily`, as datapost.fetch_all returns them, else datapost's cache)."""
    series, live = daily_series(symbol)
    sym = urgency.LEAD.get(symbol, symbol)
    if daily and daily.get(sym):
        series = daily[sym]
    return urgency.assess(change_pct, ratio, series, now, live)


def since_line(m: moves.Move, u: urgency.Urgency) -> str:
    if m.kind not in ("move", "wide"):
        return ""
    name = str(m.item.get("name") or m.symbol)
    return urgency.since_sentence(name if m.kind == "move" else name.lower(), u, m.change)


def market_told(m: moves.Move, head: str, extra: str = "") -> str:
    stamp = m.as_of.strftime("%d %b %H:%M")
    levels = " ".join(f"{f['name']} {headline._price(m.item, float(f['price']))} {headline._price(m.item, float(f['ref']))} {float(f['change']):.2f}"
                      for f in m.figures if f.get("ref"))
    return (f"{head} {stamp} {m.price} {m.change} {m.level} " + " ".join(f"{n} {c:.1f}" for n, c in m.members) + (f" {levels}" if levels else "")
            + (f" {m.ref}" if m.ref else "") + (f" {extra}" if extra else ""))


def _close_name(at: datetime | None) -> str:
    """ "Thursday's close": the weekday of the previous session's close in New York (where the session is)."""
    if at is None:
        return "the previous close"
    from zoneinfo import ZoneInfo

    return f"{_utc(at).astimezone(ZoneInfo('America/New_York')):%A}'s close"


def reference_line(m: moves.Move) -> str:
    """What the change is measured from, with the level, so the reader can check every figure (owner, 2026-11-21: Telegram 203 gave
    "Nasdaq -4.6%, S&P 500 -3.1%" with no level at all). Empty when the move has no reference price (a level or an extreme)."""
    if m.kind not in ("move", "wide"):
        return ""
    figs = m.figures if m.kind == "wide" else ([moves.figure(m)] if m.ref else [])
    figs = [f for f in figs if f.get("ref")]
    if not figs:
        return ""
    if m.window == "day":
        frm = f"Change from {_close_name(m.ref_at)}"
    elif m.window == "24h":
        frm = "Change from 24 hours earlier"
    else:
        start = m.ref_at or (m.as_of - timedelta(seconds=moves.WINDOWS.get(m.window, 0)))
        frm = f"Change from {_utc(start):%H:%M} UTC"
    if m.kind == "wide":
        return f"{frm}: " + ", ".join(f"{f['name']} {headline._price(m.item, float(f['ref']))}" for f in figs) + "."
    return f"{frm}, {headline._price(m.item, float(figs[0]['ref']))}."


def data_label(m: moves.Move, verified: str) -> str:
    """The source line every market post ends with: "Market data: Yahoo Finance, 20:03 UTC, intraday (US market open)." The time is when
    the printed price was struck (Yahoo's regularMarketTime), and a US stock or index says whether its session is still trading."""
    stamp = f"{_utc(m.as_of):%H:%M} UTC"
    src = ("Yahoo Finance, matched against Hyperliquid's trade.xyz market" if "trade.xyz" in verified and "apart" in verified
           else "Yahoo Finance, matched against Hyperliquid" if "Hyperliquid" in verified and "apart" in verified else "Yahoo Finance")
    when = {"intraday": ", intraday (US market open)", "close": ", at the close"}.get(m.session, "")
    return f"Market data: {src}, {stamp}{when}."


def market_summary(m: moves.Move, verified: str, extra: str = "") -> str:
    return " ".join(x for x in (reference_line(m), data_label(m, verified), extra) if x).strip()


def move_facts(m: moves.Move, verified: str = "", extra: str = "") -> dict[str, Any]:
    """The figures a market post prints, kept on the event: each member's level and reference, what the change is measured from, whether
    the US session was open, and the caption's data note (the reference and the "Market data:" line every style prints). The picture's
    number check and X's re-read use the same ones."""
    note = " ".join(x for x in (reference_line(m), extra, data_label(m, verified)) if x)  # the "Market data:" line last: X keeps it when short
    return {"figures": [dict(f) for f in m.figures], "ref": m.ref, "ref_at": _iso(m.ref_at) if m.ref_at else None, "session": m.session, "data_note": note}


def stat_of(m: moves.Move) -> str:
    if m.kind in ("move", "wide"):
        return f"{'+' if m.change > 0 else '-'}{abs(m.change):.1f}%"
    return headline._money(m.item, m.price)


# --- refreshing ---------------------------------------------------------------------------------------------------------------


def _forget_old_figures(ev: Any, before: str) -> None:
    """The figures changed: the caption Telegram's package wrote and any picture that prints figures are dropped, so neither platform can
    show the old numbers (a photograph prints none and is kept)."""
    if (ev.headline or "") == before:
        return
    f = dict(ev.facts or {})
    f.pop("package", None)
    f.pop("visual_headline", None)
    if (f.get("visual") or {}).get("kind") != "photo":
        f.pop("visual", None)
    ev.facts = f


def _stamp(ev: Any, now: datetime, price: float, prev: float | None, at: datetime, change: float, source: str, **extra: Any) -> None:
    ev.facts = {**(ev.facts or {}), "live": {"checked_at": _iso(now), "at": _iso(at), "price": price, "prev": prev, "change_pct": round(change, 2),
                                             "source": source, **extra}}


async def _refresh_data(ev: Any, now: datetime, transport: Any) -> tuple[str, str]:
    f = ev.facts or {}
    d = f.get("data") or {}
    sym = (d.get("symbols") or [""])[0]
    if not sym:
        return HOLD, "the data post names no instrument"
    seen = _seen_daily.get(sym)
    if seen is not None and time.monotonic() - seen[0] <= SEEN_FOR and seen[2] is not None:
        series, last = seen[1], seen[2]
    else:
        series, last = await _fetch_daily(sym, transport)
    if not series or last is None:
        return HOLD, f"no fresh daily price for {sym} could be read"
    if last.kind in LIVE_DATA and now - _utc(last.at) > LIVE_PRICE_MAX:
        return HOLD, f"the newest {sym} price is from {_utc(last.at):%H:%M} UTC, {int((now - _utc(last.at)).total_seconds() // 60)} min ago"
    post = datapost.anomaly(sym, series, now, last)
    if post is None:
        return FADED, f"{sym} is no longer an unusual one-day move ({last.change:+.1f}% now)"
    before = ev.headline or ""
    ev.title = ev.headline = post.headline[:300]
    ev.summary = " ".join(post.lines)[:600]
    ev.facts = {**f, "data": post.out(), "told": post.told}
    _forget_old_figures(ev, before)
    _stamp(ev, now, last.price, last.prev, _utc(last.at), last.change, "Yahoo Finance daily", kind=last.kind)
    return OK, f"{sym} {last.change:+.2f}% at {_utc(last.at):%H:%M} UTC"


def _item_for(symbol: str, watchlist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = moves.watch_items(watchlist)
    if symbol == "CRYPTO":
        return [x for x in items if x.get("group") in ("crypto_major", "crypto_alt")]
    if symbol == "US-STOCKS":
        return [x for x in items if x.get("group") == "index"]
    return [x for x in items if x.get("symbol") == symbol]


async def _fetch(items: list[dict[str, Any]], transport: Any) -> dict[str, sources.Quote]:
    out: dict[str, sources.Quote] = {}
    for it in items:
        seen = _seen_quotes.get(it["symbol"])
        if seen is not None and time.monotonic() - seen[0] <= SEEN_FOR:
            q = seen[1]
        else:
            q = await _fetch_quote(it["symbol"], transport)
            if q is not None:  # the picture's number check (visual.verify_quote_move) moments later reads this same answer
                _seen_quotes[it["symbol"]] = (time.monotonic(), q)
        if q is not None:
            out[it["symbol"]] = q
    return out


async def _refresh_market(ev: Any, now: datetime, transport: Any, watchlist: list[dict[str, Any]], fetch: Any = None) -> tuple[str, str]:
    f = ev.facts or {}
    sym, kind = str(f.get("symbol")), str(f.get("kind"))
    items = _item_for(sym, watchlist)
    if not items:
        return HOLD, f"{sym} is not on the watchlist any more"
    quotes = await (fetch or _fetch)(items, transport)
    fresh = [q for q in quotes.values() if now - _utc(q.as_of) <= LIVE_PRICE_MAX]
    if not fresh or (sym not in ("CRYPTO", "US-STOCKS") and sym not in {q.symbol for q in fresh}):
        newest = max((_utc(q.as_of) for q in quotes.values()), default=None)
        return HOLD, (f"the newest {sym} price is from {newest:%H:%M} UTC" if newest else f"no live {sym} price could be read")
    found = [m for m in moves.detect(quotes, items, now) if m.symbol == sym]
    down = bool(f.get("falling"))
    if kind in ("move", "wide"):
        found = [m for m in found if m.kind in ("move", "wide") and (m.change < 0) == down]
    elif kind == "milestone":
        found = [m for m in found if m.kind == "milestone" and float(m.level) == float(f.get("level") or 0)]
    else:
        found = [m for m in found if m.kind == kind]
    if not found:
        return FADED, f"{sym}'s {kind} no longer stands at {now:%H:%M} UTC (re-measured from the live price)"
    m = max(found, key=lambda x: x.ratio)
    disagree = await second_market(m, items)
    if disagree:  # the re-read price and an independent market disagree now: it waits; never sent on one side of a disagreement
        return HOLD, disagree
    u = assess(sym, m.change, m.ratio, now) if kind in ("move", "wide") else None
    extra = since_line(m, u) if u is not None else ""
    head = headline.market(m)
    before = ev.headline or ""
    ev.title = ev.headline = head
    ev.summary = market_summary(m, str(f.get("verified") or ""), extra)
    nf = {**f, "change_pct": round(m.change, 2), "ratio": round(m.ratio, 2), "price": m.price, "as_of": m.as_of.isoformat(), "window": m.window,
          "members": m.members, "spark": m.spark or f.get("spark") or [], "stat": stat_of(m), "told": market_told(m, head, extra), **move_facts(m, str(f.get("verified") or ""), extra)}
    if u is not None:
        nf["urgency"] = u.out()
    ev.facts = nf
    _forget_old_figures(ev, before)
    q = quotes.get(sym) or max(fresh, key=lambda x: x.as_of)
    _stamp(ev, now, m.price, None, m.as_of if kind != "milestone" else _utc(q.as_of), m.change, "Yahoo Finance 5-minute", window=m.window)
    return OK, f"{head} ({m.as_of:%H:%M} UTC)"


SECOND = sources.second_opinion  # the independent second market (tests stub it: no network)


def drop_stale_tag(ev: Any, now: datetime) -> str:
    """A news story's price tag was measured when the story broke (events.score_story): past markethours.TAG_MAX it is no longer the
    market now, so it leaves the headline (never re-measured or guessed here). Returns what was done ("" = nothing)."""
    from app.newsdesk import markethours

    f = ev.facts or {}
    tag = str(f.get("price_tag") or "")
    if not tag or getattr(ev, "kind", "") not in ("news", "update") or not f.get("published_at"):
        return ""
    if now - _utc(datetime.fromisoformat(str(f["published_at"]))) <= markethours.TAG_MAX:
        return ""
    before = ev.headline or ""
    ev.headline = headline.news(ev.title or "")
    ev.facts = {**f, "price_tag": "", "stat": "" if str(f.get("stat") or "") in tag else f.get("stat"), "tag_dropped": tag}
    _forget_old_figures(ev, before)
    return f"price tag {tag} dropped: measured over {int(markethours.TAG_MAX.total_seconds() // 60)} min ago"


async def second_market(m: moves.Move, items: list[dict[str, Any]]) -> str:
    """The re-read figures against an independent market, at the moment of sending (app/newsdesk/sources.second_opinion: Hyperliquid for
    crypto, trade.xyz for the S&P 500, large stocks and commodities). "" when they agree or no second market can say; else why not."""
    from app.newsdesk import safety

    by_symbol = {it["symbol"]: it for it in items}
    figs = m.figures if m.kind == "wide" else [moves.figure(m)]
    for fig in figs:
        it = by_symbol.get(str(fig["symbol"])) or {"symbol": fig["symbol"]}
        if not sources.has_second(it):
            continue
        try:
            ok, detail = await SECOND(it, float(fig["price"]))
        except Exception as e:  # noqa: BLE001 - unreadable: no disagreement to report
            logger.info("livedata: second market for %s unreadable (%s)", fig["symbol"], type(e).__name__)
            continue
        if not ok and "apart" in detail:
            safety.note("second_source", str(fig["symbol"]), detail, price=fig["price"])
            return f"{fig['name']} disagrees with an independent market: {detail}"
    return ""


async def refresh(db: Any, ev: Any, now: datetime, *, transport: Any = None, fetch: Any = None) -> tuple[str, str]:
    """(OK | HOLD | FADED, what was read): see the module docstring. Events that print no live price are OK untouched."""
    if not needs_live(ev):
        dropped = drop_stale_tag(ev, now)
        return OK, dropped
    live = (ev.facts or {}).get("live") or {}
    if live.get("checked_at") and now - _utc(datetime.fromisoformat(str(live["checked_at"]))) <= REUSE_FOR and not problem(ev, now):
        return OK, f"re-read at {live['checked_at'][11:19]} UTC"
    try:
        if ev.kind == "data":
            result = await _refresh_data(ev, now, transport)
        else:
            watchlist: list[dict[str, Any]] = sources.DEFAULT_WATCHLIST
            if db is not None:
                from app.newsdesk import engine

                watchlist = engine.settings_row(db).watchlist or sources.DEFAULT_WATCHLIST
            result = await _refresh_market(ev, now, transport, watchlist, fetch)
    except Exception as e:  # noqa: BLE001 - a refresh that fails is a hold, never a send with the old figures
        logger.warning("livedata: event %s refresh failed (%s): held", getattr(ev, "id", None), type(e).__name__)
        return HOLD, f"the live price couldn't be re-read ({type(e).__name__})"
    logger.info("livedata: event %s %s: %s", getattr(ev, "id", None), result[0], result[1])
    return result


__all__ = ["CHECK_MAX", "FADED", "HOLD", "LIVE_PRICE_MAX", "OK", "assess", "forget", "needs_live", "problem", "refresh", "remember"]
