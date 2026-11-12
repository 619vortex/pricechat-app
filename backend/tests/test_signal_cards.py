"""/signal cards (signalcard.py): the verdict only from the rules, exact WAIT triggers, evidence-based confidence, the buttons
(Chart, Alert Me, Refresh, Trade, Details), setup alerts, and the record of every card (WAIT and failed setups included).
No network: StudyMD fakes Hyperliquid; the trade path stops at tgtrade's own preview (PAPER), never an order without Confirm."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.trading import TrIntent, TrPosition
from app.models.tradingos import TsPriceAlert, TsSignal, TsSignalCard, TsSignalIdea
from app.tradingos import alerts, bot, execution, market, record, risk, signalcard, signals, tgtrade, weekly
from app.tradingos.market import Candle

from tests.test_bot_commands import StudyMD
from tests.test_tg_trading import TG, _no_hyperliquid, banter  # noqa: F401  (the autouse fixture: no real Hyperliquid reads)
from tests.test_trading_os import flat, uptrend_pullback


class CardTG(TG):
    """TEST FIXTURE: keeps buttons, edits in place and chart replies."""

    async def edit_message_text(self, chat_id: Any, message_id: int, text: str, html: bool = False, buttons: Any = None) -> dict[str, Any]:
        self.sent.append(("edit", message_id, text, buttons))
        return {}

    async def send_photo(self, chat_id: Any, png: bytes, caption: str, buttons: Any = None, reply_to: Any = None, html: bool = False) -> dict[str, Any]:
        self.replies.append(reply_to)
        return await super().send_photo(chat_id, png, caption, buttons, reply_to, html)


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> StudyMD:
    f = StudyMD({"BTC": uptrend_pullback(400), "SOL": uptrend_pullback(400, 20.0), "FLAT": flat(400)})
    monkeypatch.setattr(market, "_shared", f)
    weekly._cache.clear()
    signalcard._recent.clear()
    return f


def ask(db: Session, tg: CardTG, text: str, chat: int = -5, uid: int = 9) -> TsSignalCard:
    asyncio.run(bot.handle_command(db, tg, chat, uid, text, group=chat < 0, reply_to=3))
    return db.scalars(select(TsSignalCard).order_by(TsSignalCard.updated_at.desc(), TsSignalCard.id.desc())).first()


def press(db: Session, tg: CardTG, card: TsSignalCard, action: str, uid: int = 9) -> None:
    asyncio.run(bot.handle_callback(db, tg, {"id": "c", "from": {"id": uid}, "data": f"sc:{card.id}:{action}",
                                              "message": {"chat": {"id": -5}, "message_id": 77}}))


def labels(btns: Any) -> list[str]:
    return [b["text"] for row in btns for b in row]


# ---------------------------------------------------------------- the card
def test_a_setup_card_has_the_whole_plan_from_the_rules(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal SOL")
    a = signals.analyze(md, "SOL", "1h")
    assert c.verdict == a.decision == "LONG" and tg.sent[-1][0] == "msg"
    p, text = c.plan, tg.sent[-1][2]
    mid = (p["lo"] + p["hi"]) / 2
    assert (p["lo"], p["hi"], p["stop"], p["tp1"], p["rr1"]) == (min(a.entry), max(a.entry), a.stop, a.target, a.rr)
    assert p["tp2"] == pytest.approx(risk.tp2_of(mid, a.target)) and p["rr2"] > p["rr1"]  # TP2 exactly as execution places it
    for part in ("🟢 <b>LONG  $SOLUSD</b>", "Entry", "SL", "TP1", "TP2", "Lev", "⛔ Invalid if price trades", "15m", "1h", "4h", "RSI", "MACD",
                 "EMA20", "📊 Setup record", "Not financial advice"):
        assert part in text, part
    assert f"rules {len(a.checks)}/{len(a.checks)}" in text  # every rule passed, counted from the analysis itself
    assert labels(tg.buttons[-1]) == ["📈 Chart", "🔔 Alert Me", "🔄 Refresh", "⚡ Trade", "📋 Details"]
    assert len(text) <= 1024 and "@botpricechat" not in text  # compact, no promotional footer
    idea = db.get(TsSignalIdea, c.idea_id)
    assert idea.full and idea.stop == a.stop and idea.target == a.target  # graded on real candles like every plan
    assert db.scalar(select(TsSignal)) is None  # never a published signal


def test_confidence_is_timeframe_agreement_and_says_so() -> None:
    assert signalcard.confidence("LONG", {"15m": "up", "1h": "up", "4h": "up"}) == ("High", 3)
    assert signalcard.confidence("LONG", {"15m": "down", "1h": "up", "4h": "up"}) == ("Medium", 2)
    assert signalcard.confidence("SHORT", {"15m": "down", "1h": "flat", "4h": None}) == ("Low", 1)


def test_a_wait_card_names_the_exact_conditions_each_side_still_needs(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal FLAT")
    text = tg.sent[-1][2]
    assert c.verdict == "WAIT" and c.plan is None and c.confidence == "" and c.idea_id is None
    assert "⏸ <b>WAIT" in text and ("▲ <b>LONG when</b>" in text or "▼ <b>SHORT when</b>" in text)
    for banned in ("Entry", "SL ", "TP1", "Confidence", "⚡"):  # no plan and no confidence on a WAIT
        assert banned not in text
    assert "⚡ Trade" not in labels(tg.buttons[-1]) and "🔔 Alert Me" in labels(tg.buttons[-1])
    met = c.data["met"]
    assert f"LONG {met['LONG'][0]}/{met['LONG'][1]}" in text and met["LONG"][0] < met["LONG"][1] and met["SHORT"][0] < met["SHORT"][1]
    assert "plan check: resistance at 100.50 within 0.5 ATR" in text  # all of a side's conditions met, but a level is in the way


def test_side_conditions_are_the_rules_with_their_values() -> None:
    x = {"price": 110.0, "ema20": 105.0, "ema50": 100.0, "ema50_prev": 99.0, "rsi": 71.0, "hist": 0.5, "hist_prev": 0.4, "atr": 2.0}
    long = {c.rule: c for c in signalcard.side_conditions("LONG", x, "1h", funding=0.00002, btc=(80_000.0, 79_000.0))}
    assert all(long[r].ok for r in ("trend", "macd", "funding", "btc"))
    assert not long["pullback"].ok and long["pullback"].need == "price back into 103.80–106.20 (EMA20 ±0.6 ATR, now 2.5 ATR away)"
    assert not long["rsi"].ok and long["rsi"].need == "RSI 40–65 (now 71)"
    short = signalcard.side_conditions("SHORT", x, "1h", funding=0.00002, btc=(80_000.0, 79_000.0))
    need = [c.need for c in short if not c.ok]
    assert need[:2] == ["EMA20 below EMA50 (now 105.00 vs 100.00)", "1h close below EMA50 100.00"]
    assert any(n.startswith("BTC below its 1h EMA50 79,000") and "(now 80,000" in n for n in need)


def test_market_blockers_stop_both_sides(db: Session, md: StudyMD) -> None:
    s = signals.settings(db)
    s.min_volume_usd = 1e15  # no market trades that much
    db.commit()
    tg = CardTG()
    c = ask(db, tg, "/signal SOL")
    assert c.verdict == "WAIT" and "⛔ Blocked both ways: 24h volume" in tg.sent[-1][2]


# ---------------------------------------------------------------- buttons
def test_details_and_back_edit_the_card_in_place(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal SOL")
    press(db, tg, c, "d")
    kind, mid, text, btns = next(x for x in reversed(tg.sent) if x[0] == "edit")
    assert mid == 77 and "the full read" in text and "Rules (all must pass)" in text and "◀ Card" in labels(btns)
    assert text.count("✅") == len(c.data["checks"])
    press(db, tg, c, "s")
    kind, mid, text, btns = next(x for x in reversed(tg.sent) if x[0] == "edit")
    assert "Entry" in text and "📋 Details" in labels(btns)


def test_refresh_rereads_and_is_rate_limited(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal FLAT")
    press(db, tg, c, "r")
    assert any(x[0] == "edit" and "WAIT" in x[2] for x in tg.sent) and "Updated: WAIT" in tg.sent[-1][2]
    n = len(tg.sent)
    press(db, tg, c, "r")
    assert len(tg.sent) == n + 1 and "Just refreshed" in tg.sent[-1][2]


def test_chart_button_draws_the_plan_once_per_card(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal SOL")
    press(db, tg, c, "c")
    photo = next(x for x in reversed(tg.sent) if x[0] == "photo")
    assert "entry, SL, TP1, TP2" in photo[2] and tg.replies[-1] == 77  # under the card
    press(db, tg, c, "c")
    assert "just posted above" in tg.sent[-1][2] and sum(x[0] == "photo" for x in tg.sent) == 1
    w = ask(db, tg, "/signal FLAT")
    press(db, tg, w, "c")
    assert "levels that would make it a setup" in tg.sent[-1][2]


# ---------------------------------------------------------------- Trade
def test_trade_is_tgtrades_own_preview_never_an_order(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal SOL")
    args = signalcard.trade_args(c)
    assert args[:3] == ["SOL", "long", f"{c.plan['lev'][0]}x"] and args[3] == "sl" and args[5] == "tp" and args[7] == "limit"
    assert float(args[4]) == pytest.approx(c.plan["stop"]) and float(args[6]) == pytest.approx(c.plan["tp1"]) and float(args[8]) == pytest.approx(c.plan["hi"])
    sized = signalcard.trade_args(c, 5.0)  # the margin that risks at most 95% of a $5 max loss at the stop
    lev, worst = c.plan["lev"][0], c.plan["hi"]
    loss = float(sized[2]) * lev / worst * (worst - c.plan["stop"] + worst * (2 * risk.TAKER_FEE + 2 * risk.SLIPPAGE))
    assert sized[3] == f"{lev}x" and 4.5 < loss <= 4.75 + 1e-6
    press(db, tg, c, "t", uid=4401)  # not linked: nothing but how to link
    assert "Kiwi Passport" in tg.sent[-1][2] and db.scalar(select(TrIntent)) is None
    banter(db, "4402")
    press(db, tg, c, "t", uid=4402)
    dm = [x for x in tg.sent if x[0] == "msg" and x[1] == 4402]
    assert dm and "PAPER" in dm[-1][2] and "Confirm within 5 minutes" in dm[-1][2]
    assert tg.buttons[-1][0][0]["callback_data"].startswith("mo:")  # Confirm / Cancel: nothing placed yet
    assert db.scalar(select(TrPosition)) is None and "Nothing is placed until you Confirm" in tg.sent[-1][2]


def test_trade_keeps_the_mainnet_switch_and_the_emergency_stop(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal SOL")
    m = banter(db, "4403")
    execution.account(db, m).mode = "LIVE"
    db.commit()
    press(db, tg, c, "t", uid=4403)
    dm = [x for x in tg.sent if x[0] == "msg" and x[1] == 4403]
    assert "MAINNET PREVIEW" in dm[-1][2] and tg.buttons[-1] is None and db.scalar(select(TrIntent)) is None  # read-only, no Confirm
    tgtrade.set_switches(db, "owner", kill=True)
    banter(db, "4404")
    press(db, tg, c, "t", uid=4404)
    dm = [x for x in tg.sent if x[0] == "msg" and x[1] == 4404]
    assert tgtrade.KILLED[:40] in dm[-1][2] and db.scalar(select(TrIntent)) is None


def test_trade_refused_after_the_entry_window(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal SOL")
    c.plan = {**c.plan, "expires": (utcnow() - timedelta(minutes=1)).isoformat()}
    db.commit()
    banter(db, "4405")
    press(db, tg, c, "t", uid=4405)
    assert "entry window has passed" in tg.sent[-1][2] and db.scalar(select(TrIntent)) is None


# ---------------------------------------------------------------- alerts
def test_alert_on_a_setup_is_the_entry_zone_alert(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal SOL")
    press(db, tg, c, "a")
    a = db.scalar(select(TsPriceAlert))
    assert a.kind == "zone" and a.plan["side"] == "LONG"  # the button carries 6 significant figures (Telegram's 64 bytes)
    for got, want in ((a.price, c.plan["lo"]), (a.price_hi, c.plan["hi"]), (a.plan["stop"], c.plan["stop"]), (a.plan["target"], c.plan["tp1"])):
        assert got == pytest.approx(want, rel=1e-5)


def test_a_wait_alert_fires_once_when_the_market_becomes_a_setup(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal FLAT")
    press(db, tg, c, "a")
    a = db.scalar(select(TsPriceAlert))
    assert a.kind == "setup" and a.plan["tf"] == "1h" and "becomes a valid LONG or SHORT setup" in [x for x in tg.sent if x[1] == 9][-1][2]
    assert "becomes a valid setup" in alerts.listing(db, 9)
    press(db, tg, c, "a")
    assert "Already watching" in tg.sent[-1][2]
    asyncio.run(alerts.check(db, tg, md))  # still flat: checked, nothing sent
    assert a.status == "active" and a.plan["checked"] > 0
    n = len(tg.sent)
    asyncio.run(alerts.check(db, tg, md))  # the same candle: not read again
    assert len(tg.sent) == n
    md.series["FLAT"] = uptrend_pullback(400, 1.0)  # the market turns into a setup on the next candle
    a.plan = {**a.plan, "checked": 0}
    db.commit()
    asyncio.run(alerts.check(db, tg, md))
    fired = [x for x in tg.sent if x[0] == "msg" and x[1] == 9 and "is now a valid LONG setup" in x[2]]
    assert len(fired) == 1 and "Entry" in fired[0][2] and a.status == "fired" and "⚡ Trade" in labels(tg.buttons[-1])
    asyncio.run(alerts.check(db, tg, md))
    assert len([x for x in tg.sent if x[0] == "msg" and "is now a valid" in x[2]]) == 1  # once


# ---------------------------------------------------------------- the record
def _wait_card(db: Session, price: float = 100.0, hours_ago: float = 30) -> TsSignalCard:
    c = TsSignalCard(venue="hyperliquid", coin="BTC", tag="$BTCUSD", timeframe="1h", verdict="WAIT", price=price, data={"inds": {"atr": 1.0}},
                     chat_type="group", created_at=utcnow() - timedelta(hours=hours_ago))
    db.add(c)
    db.commit()
    return c


def test_wait_cards_are_graded_on_the_candles_that_followed(db: Session) -> None:
    c = _wait_card(db)
    t0 = int(c.created_at.timestamp() * 1000) + 60_000
    cs = [Candle(t0 + i * 3_600_000, 100, 100 + i * 0.25, 100 - i * 0.1, 100 + i * 0.2, 1) for i in range(26)]
    assert signalcard.grade_wait(c, cs, utcnow())
    assert c.status == "GRADED" and c.result["candles"] == 24 and c.result["high_pct"] == pytest.approx(5.75) and c.result["low_pct"] == pytest.approx(-2.3)
    young = _wait_card(db, hours_ago=3)
    assert not signalcard.grade_wait(young, cs, utcnow()) and young.status == "OPEN"


def test_the_record_shows_every_card_losses_and_unfilled_included(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    c = ask(db, tg, "/signal SOL")
    for status, filled, r in (("STOP", True, -1.0), ("EXPIRED", False, None), ("TARGET", True, 2.0)):
        x = TsSignalIdea(venue="hyperliquid", kind="perp", coin="SOL", tag="$SOLUSD", timeframe="1h", side="LONG", entry_lo=1, entry_hi=2, stop=0.5,
                         target=3, idea="passes every rule", family="full", full=True, chat_type="group", status=status,
                         filled_at=utcnow() if filled else None, r=r)
        db.add(x)
        db.flush()
        db.add(TsSignalCard(venue="hyperliquid", coin="SOL", tag="$SOLUSD", timeframe="1h", verdict="LONG", price=1.5, plan=c.plan, data={},
                            idea_id=x.id, chat_type="group"))
    g = _wait_card(db)
    g.status, g.result = "GRADED", {"high_pct": 3.0, "low_pct": -1.0, "close_pct": 1.0, "candles": 24, "atr_pct": 1.0}
    db.commit()
    s = signalcard.setup_stats(db)
    assert (s["shown"], s["graded"], s["tp1"], s["sl"], s["unfilled"], s["open"]) == (4, 3, 1, 1, 1, 1) and s["avg_r"] == pytest.approx(0.5)
    text = signalcard.record_text(db)
    assert "every card, nothing removed" in text and "SL        1" in text and "unfill    1" in text and "+0.50R" in text
    assert "<b>WAIT</b> · 1 shown · 1 graded" in text and "+3.0% up" in text
    assert "📊 Setup record: 3 graded · 1 TP1 · 1 SL · 1 never filled · avg +0.50R" in signalcard.record_line(db)


def test_the_same_card_within_half_an_hour_is_stored_once(db: Session, md: StudyMD) -> None:
    tg = CardTG()
    ask(db, tg, "/signal FLAT")
    ask(db, tg, "/signal FLAT", chat=1, uid=1)
    ask(db, tg, "/signal SOL")
    ask(db, tg, "/signal SOL", chat=1, uid=1)
    assert len(db.scalars(select(TsSignalCard)).all()) == 2 and len(db.scalars(select(TsSignalIdea)).all()) == 1


def test_signal_without_a_coin_names_the_closest_markets_when_nothing_is_valid(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    f = StudyMD({"BTC": flat(400), "FLAT": flat(400)})
    monkeypatch.setattr(market, "_shared", f)
    tg = CardTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal", group=True))
    text = tg.sent[-1][2]
    assert "⏸ <b>WAIT</b> · none of the" in text and "Entry" not in text and db.scalar(select(TsSignalCard)) is None


def test_card_text_and_timing_helpers() -> None:
    assert signalcard.tf_row({"15m": "up", "1h": "down", "4h": None}) == "15m ▲ │ 1h ▼ │ 4h ·"
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(second=0, microsecond=0)
    assert signalcard._when(soon.isoformat()) == soon.strftime("%H:%M UTC")
    assert record.FILL_WITHIN == 24  # the card's "isn't filled by" is the record's own fill window
