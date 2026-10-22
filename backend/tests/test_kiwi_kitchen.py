"""Kiwi Kitchen: the 15m / 30m strategy's state machine (SEARCHING · COOKING · READY · BURNT) on closed candles, its
proposals through the normal signal pipeline, the event stream, members' trading style (frequency, cooldown, 15m opt-in)
and the ORDER UP / KIWI FINISHED COOKING announcements. No network: fake Hyperliquid candles (TEST FIXTURES)."""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import service as members
from app.models.trading import TrPosition
from app.models.tradingos import KbEvent, TsSignal
from app.tradingos import accounts, bot, execution, kitchen, market, risk, signals
from app.tradingos import intraday as ix
from app.tradingos.market import Candle, Market

from tests.test_trading_os import FakeTG, kiwi

M15, M30 = kitchen.M15, kitchen.M30
BASE = 1_790_000_000_000 // M30 * M30  # a 30m boundary


def ramp(step: int, last_open: int, n: int, start: float = 100.0, slope: float = 0.02) -> list[Candle]:
    """TEST FIXTURE: a steady uptrend, oldest first, the last candle opening at `last_open`."""
    out = []
    for k in range(n):
        t = last_open - (n - 1 - k) * step
        o = start + slope * k
        out.append(Candle(t, o, o + 0.05, o - 0.03, o + slope * 0.9, 1000 + k))
    return out


class KitchenMD(market.MarketData):
    """TEST FIXTURE: candles per interval up to the last candle CLOSED at `now_ms`, plus a tight, liquid market."""

    def __init__(self, now_ms: int, funding: float = 0.00001) -> None:
        self.now_ms, self.funding = now_ms, funding
        self._cache = {}

    def markets(self) -> tuple[float, dict[str, Market]]:
        px = 110.0
        return time.time(), {c: Market(coin=c, mark=px, mid=px, oracle=px, prev_day=px, funding=self.funding, open_interest=1e6, day_volume=5e8,
                                       max_leverage=20, impact_bid=px * (1 - 0.00005), impact_ask=px * (1 + 0.00005), sz_decimals=2)
                             for c in ("BTC", "ETH", "SOL")}

    def candles(self, coin: str, interval: str, count: int = 200) -> list[Candle]:
        step = market.INTERVALS[interval] * 1000
        last_open = self.now_ms // step * step - step  # the last CLOSED candle
        return ramp(step, last_open, count)


def a_setup(f: ix.Series, i: int, *a: Any, **k: Any) -> tuple[list[ix.Setup], list[str]]:
    """TEST FIXTURE: a 15m trend pullback LONG on every candle (the entry models themselves are tested in test_intraday_research)."""
    c = f.c[i]
    return [ix.Setup("A_pullback", "LONG", c, c * 0.99, 1.0, 2.0, 180, ["Uptrend, pulled back to EMA20 and closed back above it"],
                     {"htf": 1.0, "regime": 1.0, "relvol": 1.0, "ext": 1.0})], []


@pytest.fixture(autouse=True)
def no_ml_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The kitchen's rules are tested without the shipped Kiwi ML model (its own tests are in test_kiwi_ml)."""
    monkeypatch.setattr(kitchen.ml, "model", lambda: None)


@pytest.fixture
def setups(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {"fn": a_setup}
    monkeypatch.setattr(kitchen.ix, "setups", lambda *a, **k: box["fn"](*a, **k))
    return box


def series(md: KitchenMD) -> tuple[ix.Series, ...]:
    return tuple(ix.series(md.candles("BTC", tf, n), market.INTERVALS[tf] * 1000) for tf, n in (("15m", 300), ("30m", 120), ("1h", 260), ("4h", 80)))


def at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def kitchen_on(db: Session, publish: bool = True, auto_publish: bool = False, mode: str = "standard") -> None:
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "intraday_15m_v1": publish, "intraday": {"coins": ["BTC"], "mode": mode}}
    s.auto_publish = auto_publish
    db.commit()


# ---------------------------------------------------------------- the pure evaluation
def test_ready_only_when_the_30m_candle_confirms_and_every_check_passes(setups: dict[str, Any]) -> None:
    cfg = dict(kitchen.DEFAULTS)
    # the 15m candle is the SECOND half of a 30m candle: both closed together, so the confirmation is there
    md = KitchenMD(BASE + 60_000)
    ev = kitchen.evaluate("BTC", *series(md), md.market("BTC"), md.now_ms, cfg)
    assert ev.state == "READY", ev.reason
    assert {c["id"]: c["status"] for c in ev.checks} == {k: "skip" if k in ("ml", "risk_team") else "pass" for k, _ in kitchen.CHECKS} and ev.passed == 7
    assert next(c for c in ev.checks if c["id"] == "ml")["detail"] == "no model trained yet"
    assert ev.setup and ev.setup["side"] == "LONG" and ev.setup["cost_r"] < 0.25 and 0 < (ev.strength or 0) <= 100
    # the FIRST half: the 30m candle is still forming, so it waits (no guess)
    md = KitchenMD(BASE + M15 + 60_000)
    ev = kitchen.evaluate("BTC", *series(md), md.market("BTC"), md.now_ms, cfg)
    assert ev.state == "COOKING" and next(c for c in ev.checks if c["id"] == "confirm_30m")["status"] == "pending"
    assert "waiting for the 30m candle" in ev.reason


def test_burnt_says_which_check_failed_and_searching_says_why(setups: dict[str, Any]) -> None:
    cfg = dict(kitchen.DEFAULTS)
    md = KitchenMD(BASE + 60_000, funding=0.0005)  # longs paying 0.05%/h: crowded
    ev = kitchen.evaluate("BTC", *series(md), md.market("BTC"), md.now_ms, cfg)
    assert ev.state == "BURNT" and ev.reason.startswith("Funding & open interest") and "crowded" in ev.reason
    md = KitchenMD(BASE + 60_000)
    ev = kitchen.evaluate("BTC", *series(md), md.market("BTC"), md.now_ms, cfg, capacity=(False, "trading is paused for everyone"))
    assert ev.state == "BURNT" and "paused" in ev.reason
    ev = kitchen.evaluate("BTC", *series(md), md.market("BTC"), md.now_ms, {**cfg, "min_quality": 101})
    assert ev.state == "BURNT" and ev.reason.startswith("Signal quality")
    setups["fn"] = lambda *a, **k: ([], ["A: no pullback-and-resume", "D: no clean momentum breakout"])
    ev = kitchen.evaluate("BTC", *series(md), md.market("BTC"), md.now_ms, cfg)
    assert ev.state == "SEARCHING" and ev.reason.startswith("Kiwi is looking for ingredients") and "no pullback-and-resume" in ev.reason
    assert ev.passed == 0 and ev.checks[0]["status"] == "fail" and all(c["status"] == "skip" for c in ev.checks[1:])


def test_confirm_30m_rules() -> None:
    up = ix.series(ramp(M30, BASE, 60), M30)
    assert kitchen.confirm_30m(up, 59, "LONG", "A_pullback")[0]
    assert not kitchen.confirm_30m(up, 59, "SHORT", "D_momentum")[0]
    assert kitchen.confirm_30m(up, 59, "LONG", "C_sweep")[0]  # reversal models: the 30m candle closed up
    assert not kitchen.confirm_30m(up, 10, "LONG", "A_pullback")[0]  # not enough history


def test_aggregate_builds_only_complete_30m_candles() -> None:
    cs = ramp(M15, BASE + M15, 5)  # opens BASE-3*M15 ... BASE+M15: one lone first piece, then two complete pairs
    out = kitchen.aggregate(cs, M30)
    assert [c.t for c in out] == [BASE - M30, BASE]
    assert out[0].o == cs[1].o and out[0].c == cs[2].c and out[0].h == max(cs[1].h, cs[2].h) and out[0].v == cs[1].v + cs[2].v


# ---------------------------------------------------------------- the live state machine
def kitchen_events(db: Session) -> list[KbEvent]:
    return list(db.scalars(select(KbEvent).where(KbEvent.source == "kitchen").order_by(KbEvent.id)))


def test_shadow_streams_but_proposes_nothing_then_publishing_creates_a_reviewed_signal(db: Session, setups: dict[str, Any]) -> None:
    kitchen_on(db, publish=False)
    md = KitchenMD(BASE + 60_000)
    out = kitchen.tick(db, md, at(md.now_ms))
    assert out["changed"] == {"BTC": "READY"} and out["shadow"]
    ev = kitchen_events(db)[-1]
    k = ev.data["kitchen"]
    assert k["state"] == "READY" and k["shadow"] and k["proposal"]["status"] == "SHADOW" and k["symbol"] == "BTC" and k["timeframe"] == "15m"
    assert k["passed"] == 8 and k["total"] == 9 and {c["id"] for c in k["risk"]} == {"crowding", "costs", "risk_team", "capacity"} and "SHADOW" in k["reason"]
    assert db.scalars(select(TsSignal)).all() == []
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {}  # the same candle is never judged twice: no duplicate events
    assert len(kitchen_events(db)) == 1
    # owner switches it on: the next candle's READY becomes an ordinary signal, held for the owner's approval
    kitchen_on(db, publish=True)
    md.now_ms += M30
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "READY"}
    sig = db.scalar(select(TsSignal))
    assert sig and sig.strategy == kitchen.STRATEGY and sig.timeframe == "15m" and sig.status == "PENDING_REVIEW" and sig.side == "LONG"
    assert sig.analysis["max_hold_min"] == 180 and sig.analysis["model"] == "A_pullback" and sig.stop < sig.entry_lo
    k = kitchen_events(db)[-1].data["kitchen"]
    assert k["proposal"]["number"] == sig.number and k["proposal"]["approval_required"] and not k["shadow"]
    # while it's open the kitchen follows it, and doesn't propose another on the same market
    md.now_ms += M30
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {}
    # the reviewer cancels it: burnt, with that reason
    sig.status = "CANCELLED"
    db.commit()
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "BURNT"}
    assert "cancelled by the reviewer" in kitchen_events(db)[-1].data["kitchen"]["reason"]


def test_cooking_waits_for_its_own_30m_candle_then_serves_it(db: Session, setups: dict[str, Any]) -> None:
    kitchen_on(db, publish=True, auto_publish=True)
    md = KitchenMD(BASE + M15 + 60_000)  # first half of the 30m candle
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "COOKING"}
    cooking_t = kitchen_events(db)[-1].data["kitchen"]["candle_t"]
    assert kitchen.tick(db, md, at(md.now_ms + 30_000))["changed"] == {}  # still pending: nothing new to say
    md.now_ms = BASE + M30 + 60_000  # the 30m candle closed
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "READY"}
    k = kitchen_events(db)[-1].data["kitchen"]
    assert k["candle_t"] == cooking_t  # the same candidate, now confirmed
    assert db.scalar(select(TsSignal)).status == "WAITING"  # auto-publish on: no review step


def test_finished_signal_returns_the_kitchen_to_searching_and_cools_down(db: Session, setups: dict[str, Any]) -> None:
    kitchen_on(db, publish=True, auto_publish=True)
    md = KitchenMD(BASE + 60_000)
    kitchen.tick(db, md, at(md.now_ms))
    sig = db.scalar(select(TsSignal))
    signals.settle(sig, "TARGET_HIT", sig.target)
    db.commit()
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "SEARCHING"}
    assert "TARGET HIT" in kitchen_events(db)[-1].data["kitchen"]["reason"]
    md.now_ms += M30  # a fresh setup right after: refused by the cooldown, and that's the reason shown
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "BURNT"}
    assert "cooling down" in kitchen_events(db)[-1].data["kitchen"]["reason"]


def test_snapshot_and_event_stream(client: TestClient, db: Session, setups: dict[str, Any]) -> None:
    kitchen_on(db, publish=False)
    md = KitchenMD(BASE + 60_000)
    kitchen.tick(db, md, at(md.now_ms))
    snap = client.get("/api/members/trading-bot/kitchen").json()
    assert snap["timeframe"] == "15m" and snap["confirm_timeframe"] == "30m" and snap["shadow"] and not snap["live"]
    assert snap["markets"][0]["symbol"] == "BTC" and snap["markets"][0]["state"] == "READY" and snap["events"][-1]["state"] == "READY"
    assert client.get("/api/members/trading-bot/kitchen/stream?once=1").status_code in (401, 403)  # members only
    m = members.create_member(db)
    client.cookies.set("pc_session", members.new_session(db, m, 7))
    first = kitchen_events(db)[0].id
    r = client.get(f"/api/members/trading-bot/kitchen/stream?after={first - 1}&once=1")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert f"id: {first}\nevent: kitchen\n" in r.text and '"state": "READY"' in r.text and '"symbol": "BTC"' in r.text
    r = client.get(f"/api/members/trading-bot/kitchen/stream?after={first}&once=1")
    assert "event: kitchen" not in r.text  # nothing newer


# ---------------------------------------------------------------- the risk engine and members' trading style
def plan(**kw: Any) -> risk.Plan:
    base = dict(coin="SOL", side="LONG", mark=100.0, entry_zone=(99.0, 101.0), stop=98.0, target=104.0, sig_lev_hi=5, market_max_lev=20,
                sz_decimals=2, equity=1000.0, max_leverage=3, max_loss_usd=5.0, max_allocation_pct=10.0, max_positions=2, open_positions=0,
                daily_pnl=0.0, daily_loss_limit=15.0, allowed=[], paused=False, global_paused=False)
    return risk.plan_trade(**{**base, **kw})


def test_risk_engine_daily_trade_cap_and_cooldown() -> None:
    assert plan().ok
    p = plan(trades_today=6, max_trades_today=6)
    assert not p.ok and "6 bot trades opened today (limit 6 a day)" in p.reasons[0]
    assert not plan(trades_today=12, max_trades_today=50).ok  # the hard cap wins over any setting
    p = plan(cooldown_left_min=12.4)
    assert not p.ok and "cooling down on SOL: 12 min left" in p.reasons[0]


def test_trading_style_defaults_keep_existing_accounts_unchanged_and_15m_money_needs_opt_in(db: Session) -> None:
    m = kiwi(db, 4, "5151")
    acc = execution.account(db, m)
    st = accounts.state(db, m)["style"]
    assert st["timeframe"] == "1h" and st["frequency"] == "standard" and not st["intraday_live"] and not st["intraday_live_available"]
    assert st["effective"] == {"trades_per_day": execution.STANDARD_TRADES_PER_DAY, "cooldown_minutes": 0}
    with pytest.raises(accounts.AccountError):
        accounts.update(db, m, {"max_trades_per_day": 50})
    with pytest.raises(accounts.AccountError):
        accounts.update(db, m, {"cooldown_minutes": 5})
    accounts.update(db, m, {"timeframe": "15m", "frequency": "active", "max_trades_per_day": 8, "cooldown_minutes": 20})
    assert accounts.state(db, m)["style"]["effective"] == {"trades_per_day": 8, "cooldown_minutes": 20}
    sig = TsSignal(number=1, coin="BTC", side="LONG", timeframe="15m", strategy=kitchen.STRATEGY, entry_lo=1, entry_hi=2, stop=0.5, target=3,
                   lev_lo=1, lev_hi=3, rr=1, status="ACTIVE", expires_at=utcnow())
    hourly = TsSignal(number=2, coin="BTC", side="LONG", timeframe="1h", strategy="trend_pullback_v1", entry_lo=1, entry_hi=2, stop=0.5,
                      target=3, lev_lo=1, lev_hi=3, rr=1, status="ACTIVE", expires_at=utcnow())
    assert execution.wants(acc, sig) and not execution.wants(acc, hourly)
    assert execution.intraday_allowed(db, acc, sig) is None  # PAPER: always fine
    acc.mode = "TESTNET"
    # since 2026-10-21 15m signals follow the same execution path as other timeframes: no PAPER-only gate, no opt-in gate
    assert execution.intraday_allowed(db, acc, sig) is None and execution.intraday_allowed(db, acc, hourly) is None
    s = signals.settings(db)
    s.strategies = {**s.strategies, "intraday_live": True}
    db.commit()
    assert execution.intraday_allowed(db, acc, sig) is None  # still no opt-in needed
    with pytest.raises(accounts.AccountError):
        accounts.update(db, m, {"intraday_live": True, "intraday_phrase": "yes"})
    accounts.update(db, m, {"intraday_live": True, "intraday_phrase": "enable 15m"})
    assert execution.intraday_allowed(db, acc, sig) is None and accounts.state(db, m)["style"]["intraday_live"]
    accounts.update(db, m, {"intraday_live": False})
    assert execution.account(db, m).intraday_live_at is None


def test_settings_api_takes_the_trading_style(client: TestClient, db: Session) -> None:
    m = kiwi(db, 4, "5252")
    client.cookies.set("pc_session", members.new_session(db, m, 7))
    r = client.put("/api/members/trading/settings", json={"timeframe": "both", "frequency": "active", "max_trades_per_day": 10})
    assert r.status_code == 200, r.text
    assert r.json()["style"]["timeframe"] == "both" and r.json()["style"]["effective"]["trades_per_day"] == 10
    assert client.put("/api/members/trading/settings", json={"timeframe": "5m"}).status_code == 422


# ---------------------------------------------------------------- announcements
def a_position(db: Session, m: Any, mode: str = "PAPER") -> TrPosition:
    acc = execution.account(db, m)
    sig = TsSignal(number=7, coin="BTC", side="SHORT", timeframe="15m", strategy=kitchen.STRATEGY, entry_lo=99, entry_hi=101, stop=103,
                   target=96, lev_lo=1, lev_hi=3, rr=1, status="ACTIVE", expires_at=utcnow(), analysis={"model": "D_momentum"})
    db.add(sig)
    db.flush()
    pos = TrPosition(account_id=acc.id, signal_id=sig.id, mode=mode, coin="BTC", side="SHORT", status="OPEN", size=0.01, entry_px=100.0,
                     leverage=3, stop=103.0, tp1=96.0, tp2=94.0, max_loss_usd=5.0, via="auto")
    db.add(pos)
    db.commit()
    return pos


def test_order_up_and_finished_cooking_are_sent_once_with_real_numbers(db: Session) -> None:
    m = kiwi(db, 4, "6161")
    pos = a_position(db, m)
    due = bot.due_position_announcements(db)
    assert [k for _, k, _ in due] == ["open"]
    text = due[0][2]
    assert text.startswith("🥝 ORDER UP! $BTC SHORT is served!") and "PAPER · simulated" in text and "Stop 103" in text
    assert "Momentum continuation" in text and "/earn" in text
    assert bot.due_position_announcements(db) == []  # never twice
    pos.status, pos.closed_at, pos.exit_px, pos.realized_pnl, pos.fees, pos.close_reason = "CLOSED", utcnow(), 97.0, 2.95, 0.05, "tp1"
    db.commit()
    due = bot.due_position_announcements(db)
    assert [k for _, k, _ in due] == ["close"]
    assert due[0][2].startswith("🍽️ KIWI FINISHED COOKING!") and "Realized P&L after fees (simulated): $+2.95" in due[0][2]
    assert bot.due_position_announcements(db) == []


def test_announcements_go_to_the_members_private_chat(db: Session) -> None:
    m = kiwi(db, 4, "6262")
    a_position(db, m, mode="LIVE")
    tg = FakeTG()
    assert asyncio.run(bot.announce_positions(db, tg)) == 1
    assert tg.sent[0][1] == 6262 and "LIVE · real money" in tg.sent[0][2]
    assert asyncio.run(bot.announce_positions(db, tg)) == 0


def test_kitchen_group_announcements() -> None:
    sig = TsSignal(number=12, coin="BTC", side="SHORT", timeframe="15m", strategy=kitchen.STRATEGY, entry_lo=99, entry_hi=101, stop=103,
                   target=96, lev_lo=1, lev_hi=3, rr=1, status="ACTIVE", expires_at=utcnow(), analysis={"model": "B_retest"}, last_price=100.2)
    text = bot.announcement(sig, "WAITING")
    assert text and text.startswith("🥝 ORDER UP! $BTC SHORT is served!") and "#012" in text and "Stop 103" in text and "Breakout retest" in text
    signals.settle(sig, "STOP_HIT", 103)
    text = bot.announcement(sig, "ACTIVE")
    assert text and text.startswith("🍽️ KIWI FINISHED COOKING! #012 $BTC SHORT · STOP HIT") and "Signal result: -3.09%" in text


# ---------------------------------------------------------------- degen mode (the default)
def test_degen_is_the_default_mode_and_standard_is_one_switch(db: Session) -> None:
    cfg = kitchen.config(signals.settings(db))
    assert cfg["mode"] == "degen" and cfg["max_open"] == 2 and not cfg["confirm_30m"] and cfg["market_entry"] and cfg["auto_publish"]
    assert "F_scalp" in cfg["models"] and len(cfg["coins"]) == 8
    kitchen_on(db, mode="standard")
    cfg = kitchen.config(signals.settings(db))
    assert cfg["mode"] == "standard" and cfg["confirm_30m"] and not cfg["market_entry"] and cfg["coins"] == ["BTC"]


def test_degen_serves_on_the_15m_close_publishes_itself_and_opens_at_market(db: Session, setups: dict[str, Any]) -> None:
    kitchen_on(db, publish=True, auto_publish=False, mode="degen")
    md = KitchenMD(BASE + M15 + 60_000)  # first half of a 30m candle: standard mode would wait (COOKING)
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "READY"}
    sig = db.scalar(select(TsSignal))
    assert sig.status == "WAITING"  # no review step, even with the global auto-publish off
    assert sig.analysis["market_entry"] and sig.analysis["mode"] == "degen"
    assert abs((sig.entry_lo + sig.entry_hi) / 2 - 110.0) < 1e-6  # the plan moved to the live mark
    risk_px = (sig.entry_lo + sig.entry_hi) / 2 - sig.stop
    assert risk_px > 0 and sig.target > sig.entry_hi
    k = kitchen_events(db)[-1].data["kitchen"]
    assert next(c for c in k["checks"] if c["id"] == "confirm_30m")["status"] == "skip"
    # not posted yet: the tracker must not open it (it would never get a card or an ORDER UP)
    assert signals.track(db, md) == [] and sig.status == "WAITING"
    sig.published_at, sig.tg_chat, sig.tg_message_id = utcnow(), "@g", 5
    db.commit()
    assert signals.track(db, md) == [sig] and sig.status == "ACTIVE"
    mid = (sig.entry_lo + sig.entry_hi) / 2
    assert abs(mid - sig.last_price) < 1e-6 and abs((mid - sig.stop) - risk_px) < 1e-6  # opened at market, same risk distance
    text = bot.announcement(sig, "WAITING")
    assert text and "Opened at market" in text and "15m degen" in text and "then closed at market" in text


def test_degen_holds_at_most_two_open_signals(db: Session, setups: dict[str, Any]) -> None:
    kitchen_on(db, publish=True, mode="degen")
    for n, coin in ((1, "ETH"), (2, "SOL")):
        db.add(TsSignal(number=n, coin=coin, side="SHORT", timeframe="15m", strategy=kitchen.STRATEGY, entry_lo=1, entry_hi=2, stop=0.5,
                        target=3, lev_lo=1, lev_hi=3, rr=1, status="ACTIVE", expires_at=utcnow()))
    db.commit()
    md = KitchenMD(BASE + 60_000)
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "BURNT"}
    assert "2 signals already open (limit 2)" in kitchen_events(db)[-1].data["kitchen"]["reason"]


def test_auto_records_why_it_skipped_a_15m_signal(db: Session) -> None:
    from app.models.trading import TrIntent

    m = kiwi(db, 4, "5353")
    acc = execution.account(db, m)
    acc.auto_enabled, acc.timeframe_pref, acc.mode = True, "1h", "LIVE"
    sig = TsSignal(number=13, coin="ETH", side="SHORT", timeframe="15m", strategy=kitchen.STRATEGY, entry_lo=2470, entry_hi=2472, stop=2490,
                   target=2440, lev_lo=2, lev_hi=4, rr=1.5, status="ACTIVE", expires_at=utcnow(), published_at=utcnow())
    db.add(sig)
    db.commit()
    assert execution.run_auto(db, sig) == []
    it = db.scalar(select(TrIntent).where(TrIntent.account_id == acc.id, TrIntent.signal_id == sig.id))
    assert it.status == "SKIPPED" and "15m signal; your Trading style takes 1h signals only" in it.plan["skipped"]
    feed = db.scalar(select(KbEvent).where(KbEvent.member_id == m.id, KbEvent.signal_id == sig.id))
    assert feed is not None and "AUTO skipped" in feed.title  # the member sees it in Kiwi Brain
    assert execution.run_auto(db, sig) == [] and len(db.scalars(select(TrIntent).where(TrIntent.signal_id == sig.id)).all()) == 1  # once
    acc.timeframe_pref = "both"  # takes 15m: no timeframe skip, and on LIVE no paper-only gate any more
    sig2 = TsSignal(number=14, coin="ETH", side="SHORT", timeframe="15m", strategy=kitchen.STRATEGY, entry_lo=2470, entry_hi=2472, stop=2490,
                    target=2440, lev_lo=2, lev_hi=4, rr=1.5, status="ACTIVE", expires_at=utcnow(), published_at=utcnow())
    db.add(sig2)
    db.commit()
    execution.run_auto(db, sig2)  # goes on to sizing (market data here is fake/absent: it may stop there, never at the gates)
    skipped = db.scalar(select(TrIntent).where(TrIntent.account_id == acc.id, TrIntent.signal_id == sig2.id))
    assert skipped is None or not any(w in skipped.plan.get("skipped", "") for w in ("Trading style", "PAPER only", "opt-in"))


def test_members_hear_once_when_auto_skipped_a_signal(db: Session) -> None:
    from app.models.members import LinkedIdentity

    m = kiwi(db, 4, "5454")
    acc = execution.account(db, m)
    acc.auto_enabled, acc.timeframe_pref = True, "1h"
    sig = TsSignal(number=21, coin="ETH", side="SHORT", timeframe="15m", strategy=kitchen.STRATEGY, entry_lo=2470, entry_hi=2472, stop=2490,
                   target=2440, lev_lo=2, lev_hi=4, rr=1.5, status="ACTIVE", expires_at=utcnow(), published_at=utcnow())
    db.add(sig)
    db.commit()
    execution.run_auto(db, sig)
    ident = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == m.id, LinkedIdentity.platform == "telegram"))
    tg = FakeTG()
    assert asyncio.run(bot.announce_skips(db, tg)) == 1
    chat, text = tg.sent[-1][1], tg.sent[-1][2]
    assert str(chat) == str(ident.platform_user_id)  # privately, to that member
    assert "AUTO skipped $ETH SHORT #021 (15m)" in text and "your Trading style takes 1h signals only" in text and "/earn" in text
    assert asyncio.run(bot.announce_skips(db, tg)) == 0  # once only
