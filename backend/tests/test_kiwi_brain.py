"""Kiwi Brain, Milestone 1: the durable decision timeline and the market snapshot. TEST FIXTURES only (FakeMD, FakeExchange):
no real exchange, no real funds."""

import time
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import KbEvent
from app.tradingos import brain, execution, indicators as ind, market, signals
from app.tradingos.market import Candle
from tests.test_trading_execution import FakeExchange, active_signal
from tests.test_trading_os import FakeMD, flat, kiwi, uptrend_pullback


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> FakeMD:
    f = FakeMD({"BTC": uptrend_pullback(), "SOL": uptrend_pullback(), "FLAT": flat()})
    monkeypatch.setattr(market, "_shared", f)
    return f


def test_bollinger_and_vwap_are_the_textbook_formulas() -> None:
    mid, up, lo = ind.bollinger([1, 2, 3, 4, 5], period=5, k=2)
    assert mid == 3 and up == pytest.approx(3 + 2 * 2 ** 0.5) and lo == pytest.approx(3 - 2 * 2 ** 0.5)
    assert ind.bollinger([1, 2], period=5) is None  # not enough data: nothing invented
    assert ind.vwap([2, 4], [0, 2], [1, 3], [1, 3], period=2) == pytest.approx((1 * 1 + 3 * 3) / 4)
    assert ind.vwap([1], [1], [1], [0]) is None  # no volume: no VWAP


def test_market_snapshot_tags_every_group_with_its_source_and_time_and_flags_stale_data(md: FakeMD) -> None:
    snap = brain.market_snapshot(md, "BTC", "1h")
    for group in ("market", "book", "candles"):
        assert snap[group]["source"].startswith("hyperliquid:info") and snap[group]["at"] is not None
    assert not snap["stale"] and snap["candles"]["closed"] > 100
    i = snap["indicators"]
    assert i["rsi14"] is not None and i["ema20"] and i["ema50"] and i["atr14"] and i["vwap24"] and i["bb_upper"] > i["bb_lower"]
    assert i["ema200"] is not None  # 200 closed candles: enough
    md.series["BTC"] = uptrend_pullback(150)
    md._cache.clear()
    assert brain.market_snapshot(md, "BTC", "1h")["indicators"]["ema200"] is None  # fewer: absent, never made up
    old = [Candle(c.t - 30 * 86_400_000, c.o, c.h, c.l, c.c, c.v) for c in uptrend_pullback()]  # the newest candle a month old
    md.series["BTC"] = old
    md._cache.clear()
    assert brain.market_snapshot(md, "BTC", "1h")["stale"]


def test_the_scanner_records_every_evaluation_publicly(db: Session, md: FakeMD) -> None:
    signals.settings(db).allowed_coins = "SOL,FLAT"
    db.commit()
    signals.scan(db, md)
    evs = db.scalars(select(KbEvent).order_by(KbEvent.id)).all()
    strat = [e for e in evs if e.kind == "strategy"]
    assert any(e.coin == "FLAT" and e.data["decision"] == "NO TRADE" and e.data["reasons"] for e in strat)  # why not, with evidence
    prop = next(e for e in strat if e.coin == "SOL")
    assert prop.data["decision"] in ("LONG", "SHORT") and prop.data["indicators"]["rsi"] is not None and prop.signal_id
    assert any(e.kind == "signal" and e.signal_id == prop.signal_id for e in evs)
    assert all(e.member_id is None for e in evs)  # market / strategy / signal events are account-free


def test_risk_orders_exchange_answers_and_positions_are_on_the_members_own_timeline_only(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    a, b = kiwi(db, 4, "991"), kiwi(db, 4, "992")
    acc = execution.account(db, a)
    acc.paused = True
    db.commit()
    with pytest.raises(execution.TradeError):
        execution.preview(db, a, sig, FakeExchange(mark=md.series["SOL"][-1].c))
    rej = db.scalar(select(KbEvent).where(KbEvent.kind == "risk"))
    assert rej.member_id == a.id and rej.data["decision"] == "rejected" and "stopped" in rej.data["reason"]  # survives the rollback
    acc.paused = False
    db.commit()
    ex = FakeExchange(mark=md.series["SOL"][-1].c, equity=100_000)
    execution.confirm(db, a, execution.preview(db, a, sig, ex).token, ex)
    mine = brain.events_for(db, a.id, limit=200)
    kinds = {e.kind for e in mine if e.member_id == a.id}
    assert {"risk", "order", "exchange", "position"} <= kinds
    assert any(e.kind == "risk" and e.data["decision"] == "approved" and e.data["plan"]["max_loss_usd"] > 0 for e in mine)
    assert any(e.kind == "exchange" and e.data["kind"] == "sl" and e.data["status"] == "RESTING" for e in mine)  # the stop, as answered
    theirs = brain.events_for(db, b.id, limit=500)
    assert not any(e.member_id == a.id for e in theirs)  # never another member's events
    assert any(e.member_id == a.id for e in brain.events_for(db, None, admin=True, limit=500))  # admins see everything


def test_brain_page_status_candles_and_auth(client: Any, db: Session, md: FakeMD) -> None:
    from app.members import service as members

    s = signals.settings(db)
    s.strategies = {"trend_pullback_v1": False, "breakout_4h_v1": False}
    db.commit()
    assert client.get("/api/members/kiwi-brain/status").status_code == 401  # signed-in members only
    assert client.get("/api/members/kiwi-brain/candles?coin=SOL").status_code == 401
    sig = active_signal(db, md)
    a, b = kiwi(db, 4, "771"), kiwi(db, 4, "772")
    pos = execution.confirm(db, a, execution.preview(db, a, sig).token)
    client.cookies.set("pc_session", members.new_session(db, a, 30))
    st = client.get("/api/members/kiwi-brain/status").json()
    assert st["mode"] == "PAPER" and st["state"] == "Position open" and st["performance"]["open"] == 1
    assert [x["on"] for x in st["strategies"]] == [False, False] and st["scanner"]["next_at"] is None  # off: nothing scheduled
    execution.close_position(db, a, pos.id)
    st = client.get("/api/members/kiwi-brain/status").json()
    assert st["performance"]["paper"]["trades"] == 1 and st["performance"]["live"]["trades"] == 0  # never combined
    c = client.get("/api/members/kiwi-brain/candles?coin=SOL&timeframe=1h").json()
    assert len(c["candles"]) > 50 and c["source"].startswith("hyperliquid:info")
    client.cookies.set("pc_session", members.new_session(db, b, 30))
    other = client.get("/api/members/kiwi-brain/status").json()
    assert other["performance"]["paper"]["trades"] == 0 and other["state"] != "Position open"  # never another member's trades


def test_credentials_never_reach_the_timeline_and_old_events_are_pruned(db: Session) -> None:
    e = brain.log(db, "system", "x", data={"agent_key": "0xabc", "nested": {"private": "p", "ok": 1}, "price": 2}, commit=True)
    assert e.data == {"nested": {"ok": 1}, "price": 2}
    old_mkt = brain.log(db, "market", "old", commit=True)
    old_mkt.at = utcnow() - timedelta(days=8)
    recent_strat = brain.log(db, "strategy", "kept", commit=True)
    recent_strat.at = utcnow() - timedelta(days=8)
    db.commit()
    assert brain.prune(db) == 1
    left = {x.title for x in db.scalars(select(KbEvent))}
    assert "old" not in left and "kept" in left  # markets 7 days, decisions 180 days
    with pytest.raises(ValueError):
        brain.log(db, "thoughts", "an AI's inner monologue")  # only real event kinds
    assert time.time()  # (keeps the import used)
