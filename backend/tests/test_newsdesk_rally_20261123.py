"""A major US stock rally is detected, verified and published promptly (owner, 2026-11-23: "Verify major US stock rallies are detected and
published promptly using reliable market data"). Production missed the 11-23 rally (S&P 500 +5.4%, Nasdaq +7.6%, Dow +3.2%): market posts were
paused, and before that the move had no path that ranked it as major news.

The data: Yahoo's real bars of 2026-11-20 (tests/price_updates_yahoo_20261121.json, a -4.4% Nasdaq day), mirrored around the previous close
into a rally of the same size. No test reaches a real service."""

import copy
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent
from app.newsdesk import engine, events, importance, moves, sources
from tests.test_newsdesk_telegram import desk_running
from tests.test_price_updates_20261121 import INDICES, T203, payload_at


def rally_quotes(t: Any) -> dict[str, sources.Quote]:
    out = {}
    for sym in ("^IXIC", "^GSPC", "^DJI"):
        p = copy.deepcopy(payload_at(sym, t))
        r = p["chart"]["result"][0]
        prev = float(r["meta"]["previousClose"])
        q = r["indicators"]["quote"][0]
        for k in [k for k in ("open", "high", "low", "close") if k in q]:
            q[k] = [None if v is None else 2 * prev - v for v in q[k]]
        if "high" in q and "low" in q:
            q["high"], q["low"] = q["low"], q["high"]  # mirrored: the old low is the new high
        r["meta"]["regularMarketPrice"] = 2 * prev - float(r["meta"]["regularMarketPrice"])
        got = sources.parse_chart(sym, p)
        assert got is not None
        out[sym] = got
    return out


async def agrees(item: dict[str, Any], price: float, *a: Any, **kw: Any) -> tuple[bool, str]:
    assert item.get("symbol") == "^GSPC", item  # only the S&P 500 has a second market among the three indices
    return True, f"Hyperliquid trade.xyz SP500 {price:,.2f} vs Yahoo {price:,.2f}: 0.05% apart (tolerance 2.10%)"


async def offer(db: Session, second: Any = agrees) -> NdEvent:
    desk_running(db, T203)  # market posts on (the test suite's default)
    await events.decide(db, T203, quotes=rally_quotes(T203), watchlist=INDICES, categories={}, desk_state=engine.settings_row(db).state,
                        liquidations=False, second_opinion=second, daily={})
    rows = list(db.scalars(select(NdEvent).where(NdEvent.kind.in_(("market", "update")))))
    assert rows, "the rally was not detected"
    return rows[0]


def test_the_mirrored_day_is_a_rally_of_the_same_size() -> None:
    q = rally_quotes(T203)
    wide = [m for m in moves.detect(q, INDICES, T203) if m.kind == "wide"]
    assert wide and wide[0].up and wide[0].change > 2.5


async def test_a_us_rally_verified_on_the_sp500_is_major_and_goes_out_at_once(db: Session) -> None:
    ev = await offer(db)
    assert ev.facts["two_sources"] is True and "trade.xyz" in ev.facts["verified"]
    d = importance.judge_event(ev, 0)
    assert d.tier == importance.MAJOR, d.reason
    # between slots (T203 is 20:04 UTC, after the 20:00 slot was filled or not): released now, not at the next slot
    if ev.status != events.RELEASED:
        got = events.release(db, T203 + timedelta(minutes=1), events.policy({}))
        assert got is not None and got.id == ev.id, ev.why
    db.refresh(ev)
    assert ev.status == events.RELEASED and (ev.released_at - ev.first_seen if ev.first_seen else timedelta(0)) <= timedelta(minutes=10)


async def test_the_market_pause_holds_the_rally_and_says_why(db: Session) -> None:
    desk_running(db, T203)
    row = engine.settings_row(db)
    row.state = {**(row.state or {}), "market_pause": True, "market_pause_reason": "the owner's pause"}  # production today (owner: keep it on)
    db.commit()
    await events.decide(db, T203, quotes=rally_quotes(T203), watchlist=INDICES, categories={}, desk_state=engine.settings_row(db).state,
                        liquidations=False, second_opinion=agrees, daily={})
    events.release(db, T203 + timedelta(minutes=1), events.policy({}))
    rows = list(db.scalars(select(NdEvent).where(NdEvent.kind.in_(("market", "update")))))
    assert rows and all(e.status != events.RELEASED for e in rows), [(e.status, e.slot, e.why, events.market_paused(db), e.kind, e.topic) for e in rows]
    assert all("market-data posts are paused" in (e.why or "") for e in rows), [e.why for e in rows]


async def test_an_unverified_rally_is_never_published_automatically(db: Session) -> None:
    async def unreadable(item: dict[str, Any], price: float, *a: Any, **kw: Any) -> tuple[bool, str]:
        return False, "not verified: Hyperliquid trade.xyz couldn't be read (ReadTimeout)"

    desk_running(db, T203)
    await events.decide(db, T203, quotes=rally_quotes(T203), watchlist=INDICES, categories={}, desk_state=engine.settings_row(db).state,
                        liquidations=False, second_opinion=unreadable, daily={})
    assert not list(db.scalars(select(NdEvent).where(NdEvent.status == events.RELEASED)))


def test_the_nasdaq_and_the_dow_alone_have_no_second_source() -> None:
    for sym in ("^IXIC", "^DJI"):
        assert not sources.has_second(next(w for w in sources.DEFAULT_WATCHLIST if w["symbol"] == sym))
    assert sources.has_second(next(w for w in sources.DEFAULT_WATCHLIST if w["symbol"] == "^GSPC"))
