"""Audit fixes (2026-11-08 audit, Trading): TR-1 a position left without a stop-loss alerts the member and the owner and
reconciliation keeps retrying the stop; TR-3 reduce-only closes get room to fill in a fast market; TR-8 take-profits under
Hyperliquid's $10 minimum; TR-12 exchange fills are paged. No network: the fake market and fake exchange of
test_trading_execution (TEST FIXTURES); no Telegram message is really sent."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import alerts as owner_alerts
from app.models.alerts import OwnerAlertSettings
from app.models.trading import TrOrder, TrPosition
from app.tradingos import bot, execution, risk
from app.tradingos import exchange as xch

from tests.test_trading_execution import FakeExchange, _no_live_balances, active_signal, md, on_testnet  # noqa: F401 - fixtures
from tests.test_trading_os import FakeMD, FakeTG, kiwi


def _orders(db: Session, pos: TrPosition, kind: str) -> list[TrOrder]:
    return list(db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == kind)))


# ---------------------------------------------------------------- TR-1
def test_a_position_left_without_a_stop_alerts_member_and_owner_once_and_the_stop_is_retried(db: Session, md: FakeMD,
                                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    owner_msgs: list[str] = []

    async def fake_send(settings: Any, chat: str, text: str, transport: Any = None) -> None:  # TEST FIXTURE: no real Telegram
        owner_msgs.append(text)

    monkeypatch.setattr(owner_alerts, "send_text", fake_send)
    db.add(OwnerAlertSettings(id=1, telegram_chat="424242", enabled=True))
    db.commit()
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1301")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c, stop_fails=True)
    ex.close_fails = True  # the stop is refused AND the emergency close isn't filled
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "PROTECTION_FAILED" and ex.held["SOL"] > 0
    pos.mode = "LIVE"  # the same failure with real money (the fake exchange stands in for mainnet)
    db.commit()

    tg = FakeTG()
    out = asyncio.run(bot.announce_unprotected(db, tg))
    dms = [x for x in tg.sent if x[1] == 1301]
    assert out["members_told"] == 1 and len(dms) == 1 and "NO STOP-LOSS" in dms[0][2] and "app.hyperliquid.xyz" in dms[0][2]
    assert out["owner_alerts"] == 1 and len(owner_msgs) == 1 and "LIVE position without a stop-loss" in owner_msgs[0]
    asyncio.run(bot.announce_unprotected(db, tg))  # the next minute: deduplicated
    assert len([x for x in tg.sent if x[1] == 1301]) == 1 and len(owner_msgs) == 1

    # each reconciliation tries the stop again (and the bounded emergency close); once the closes are used up, the stop alone
    stops_before = len(_orders(db, pos, "sl"))
    for _ in range(execution.EMERGENCY_RETRIES + 3):
        execution.reconcile(db, acc, ex)
    assert pos.status == "PROTECTION_FAILED" and execution._closes(db, pos) == execution.EMERGENCY_RETRIES
    assert len(_orders(db, pos, "sl")) == stops_before + execution.EMERGENCY_RETRIES + 3  # a stop attempt every run
    assert len({o.cloid for o in db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id))}) == len(_orders(db, pos, "sl")) + len(
        db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind != "sl")).all())  # fresh client order ids
    asyncio.run(bot.announce_unprotected(db, tg))
    dms = [x for x in tg.sent if x[1] == 1301]
    assert len(dms) == 2 and "stopped trying to close" in dms[1][2]  # once more when the closes are used up

    ex.stop_fails = False  # the exchange takes the stop now
    execution.reconcile(db, acc, ex)
    assert pos.status == "OPEN" and "placed by reconciliation" in pos.error and acc.paused  # protected; new trades stay stopped
    sl = [o for o in _orders(db, pos, "sl") if o.status == "RESTING"]
    assert len(sl) == 1 and sl[0].size == pytest.approx(pos.size)
    assert bot.unprotected_positions(db) == []
    out = asyncio.run(bot.announce_unprotected(db, tg))
    assert out["owner_recovered"] == 1 and "recovered" in owner_msgs[-1] and len([x for x in tg.sent if x[1] == 1301]) == 2


def test_a_testnet_position_without_a_stop_tells_the_member_but_not_the_owner(db: Session, md: FakeMD,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    owner_msgs: list[str] = []

    async def fake_send(settings: Any, chat: str, text: str, transport: Any = None) -> None:
        owner_msgs.append(text)

    monkeypatch.setattr(owner_alerts, "send_text", fake_send)
    db.add(OwnerAlertSettings(id=1, telegram_chat="424242", enabled=True))
    db.commit()
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1302")
    on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c, stop_fails=True)
    ex.close_fails = True
    execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    tg = FakeTG()
    asyncio.run(bot.announce_unprotected(db, tg))
    assert len([x for x in tg.sent if x[1] == 1302]) == 1 and owner_msgs == []


# ---------------------------------------------------------------- TR-3
class GapExchange(FakeExchange):
    """TEST FIXTURE: the book has moved `gap` against the position since the mark: an IOC close only fills if its limit reaches it."""

    gap = 0.0

    def market(self, coin: str, is_buy: bool, size: float, limit_px: float, cloid: str, reduce_only: bool = False) -> xch.Result:
        if reduce_only and self.gap:
            book = self.mark * (1 + self.gap if is_buy else 1 - self.gap)
            if (is_buy and limit_px < book) or (not is_buy and limit_px > book):
                self._oid += 1
                self.orders.append({"type": "market", "coin": coin, "buy": is_buy, "size": size, "px": limit_px, "cloid": cloid, "reduce": True})
                return xch.Result("REJECTED", error="Order could not immediately match against any resting orders")
        return super().market(coin, is_buy, size, limit_px, cloid, reduce_only)


def test_a_manual_close_during_a_1pct_gap_fills(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1311")
    on_testnet(db, m)
    mark = md.series["SOL"][-1].c
    ex = GapExchange(mark=mark)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "OPEN"
    ex.gap = 0.01  # a fast move: 1% through the mark
    out = execution.close_position(db, m, pos.id, ex)
    assert out.status == "CLOSED" and "SOL" not in ex.held  # before: "Close not filled" (limit only 0.24% from the mark)
    close = [o for o in ex.orders if o["type"] == "market" and o["reduce"]][-1]
    assert close["px"] == pytest.approx(risk.round_px(mark * 0.97, 2), rel=1e-6) and not close["buy"]  # 3% default, reduce-only


def test_an_emergency_close_during_a_1pct_gap_fills(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1312")
    on_testnet(db, m)
    ex = GapExchange(mark=md.series["SOL"][-1].c, stop_fails=True)
    ex.gap = 0.01
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "CLOSED" and pos.close_reason == "emergency_close" and "SOL" not in ex.held


# ---------------------------------------------------------------- TR-8
def _small(db: Session, md: FakeMD, tg: str, size: float) -> tuple[Any, TrPosition]:
    sig = active_signal(db, md)
    m = kiwi(db, 4, tg)
    acc = on_testnet(db, m)
    pos = TrPosition(account_id=acc.id, signal_id=sig.id, mode="TESTNET", coin="SOL", side="LONG", status="OPENING", leverage=3, size=size,
                     entry_px=120.0, stop=115.0, tp1=130.0, tp2=140.0, max_loss_usd=1, via="enter")
    db.add(pos)
    db.commit()
    return acc, pos


def test_a_small_position_gets_one_take_profit_not_two_under_the_minimum(db: Session, md: FakeMD) -> None:
    acc, pos = _small(db, md, "1321", 0.1)  # $12: halves would be $6.50 and $7
    ex = FakeExchange(mark=120.0)
    execution.protect(db, acc, pos, ex, 2)
    tps = [o for o in ex.orders if o["type"] == "tp"]
    assert pos.status == "OPEN" and len(tps) == 1 and tps[0]["size"] == pytest.approx(0.1) and tps[0]["px"] == pytest.approx(130.0)
    assert all(o["size"] * o["px"] >= risk.MIN_NOTIONAL for o in tps) and "One take-profit at TP1" in (pos.error or "")


def test_a_position_too_small_for_any_take_profit_says_so_and_keeps_its_stop(db: Session, md: FakeMD) -> None:
    acc, pos = _small(db, md, "1322", 0.07)  # TP1 for everything would be $9.10
    ex = FakeExchange(mark=120.0)
    execution.protect(db, acc, pos, ex, 2)
    assert pos.status == "OPEN" and [o["type"] for o in ex.orders] == ["sl"] and "No take-profit order" in (pos.error or "")


def test_a_refused_take_profit_is_noted_on_the_position(db: Session, md: FakeMD) -> None:
    class NoTP(FakeExchange):
        def trigger(self, coin: str, is_buy: bool, size: float, trigger_px: float, kind: str, cloid: str) -> xch.Result:
            r = super().trigger(coin, is_buy, size, trigger_px, kind, cloid)
            return xch.Result("REJECTED", error="Order must have minimum value of $10") if kind == "tp" else r

    acc, pos = _small(db, md, "1323", 1.0)
    execution.protect(db, acc, pos, NoTP(mark=120.0), 2)
    assert pos.status == "OPEN" and "TP1 take-profit not placed" in pos.error and "TP2 take-profit not placed" in pos.error


def test_a_normal_position_still_gets_two_targets(db: Session, md: FakeMD) -> None:
    acc, pos = _small(db, md, "1324", 1.0)
    ex = FakeExchange(mark=120.0)
    execution.protect(db, acc, pos, ex, 2)
    assert [o["size"] for o in ex.orders if o["type"] == "tp"] == [0.5, 0.5] and not pos.error


# ---------------------------------------------------------------- TR-12
def test_fills_are_paged_past_hyperliquids_2000_per_answer() -> None:
    class Info:  # TEST FIXTURE: answers userFillsByTime like Hyperliquid (oldest first, at most 2000 per answer)
        def __init__(self) -> None:
            self.fills = [{"tid": i, "time": 1000 + i // 3, "coin": "SOL"} for i in range(4500)]
            self.calls: list[int] = []

        def user_fills_by_time(self, user: str, start: int) -> list[dict[str, Any]]:
            self.calls.append(start)
            return [f for f in self.fills if f["time"] >= start][: xch.FILLS_PAGE]

    ex = xch.HyperliquidExecutor.__new__(xch.HyperliquidExecutor)
    ex.master, ex.info = "0x" + "1" * 40, Info()
    got = ex.fills_since(1000)
    assert len(got) == 4500 and len({f["tid"] for f in got}) == 4500 and len(ex.info.calls) == 3  # before: 2000 (one call)
