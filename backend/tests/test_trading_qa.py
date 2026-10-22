"""Earn / trading bot QA (2026-10-20): the gaps found in a review of opening, protecting, closing and reconciling positions.
Each test reproduces a failure that was possible before the fix. No network (TEST FIXTURES)."""

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trading import TrOrder, TrPosition
from app.tradingos import accounts, bot, execution
from app.tradingos import exchange as xch

from tests.test_trading_execution import FakeExchange, _testnet_position, active_signal, md, on_testnet, plan  # noqa: F401 - md is a fixture
from tests.test_trading_os import FakeMD, FakeTG, kiwi


def test_a_confirm_rechecks_what_changed_since_its_preview(db: Session, md: FakeMD) -> None:  # noqa: F811
    first, second = active_signal(db, md), active_signal(db, md)  # two SOL signals: two previews can be open at once
    m = kiwi(db, 4, "1101")
    on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    a, b = execution.preview(db, m, first, ex), execution.preview(db, m, second, ex)
    assert execution.confirm(db, m, a.token, ex).status == "OPEN"
    n = len(ex.orders)
    with pytest.raises(execution.TradeError, match="already have a bot position in SOL"):
        execution.confirm(db, m, b.token, ex)  # before: a second SOL position, netted with the first on Hyperliquid
    assert len(ex.orders) == n
    third = active_signal(db, md, "ETH")
    c = execution.preview(db, m, third, FakeExchange(mark=md.series["ETH"][-1].c))
    third.status = "STOP_HIT"
    db.commit()
    with pytest.raises(execution.TradeError, match="stop hit now"):
        execution.confirm(db, m, c.token, ex)


class LostAnswer(FakeExchange):
    """The entry reaches Hyperliquid and fills, but the answer never arrives (a timeout)."""

    def market(self, coin: str, is_buy: bool, size: float, limit_px: float, cloid: str, reduce_only: bool = False) -> xch.Result:
        super().market(coin, is_buy, size, limit_px, cloid, reduce_only)
        if not reduce_only:
            raise httpx.ReadTimeout("timed out")
        return xch.Result("FILLED", size, self.mark, self._oid)


def test_an_entry_whose_answer_was_lost_is_found_on_the_exchange_and_protected(db: Session, md: FakeMD) -> None:  # noqa: F811
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1111")
    on_testnet(db, m)
    ex = LostAnswer(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "OPEN" and pos.size == pytest.approx(ex.held["SOL"])  # before: FAILED, with a real position and no stop
    sl = db.scalar(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "sl"))
    assert sl is not None and sl.status == "RESTING" and sl.size == pytest.approx(pos.size)


class NeverSent(FakeExchange):
    def market(self, coin: str, is_buy: bool, size: float, limit_px: float, cloid: str, reduce_only: bool = False) -> xch.Result:
        raise httpx.ConnectError("down")


def test_an_entry_that_never_reached_the_exchange_fails_and_says_where_to_check(db: Session, md: FakeMD) -> None:  # noqa: F811
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1121")
    on_testnet(db, m)
    ex = NeverSent(mark=md.series["SOL"][-1].c)
    with pytest.raises(execution.TradeError, match="Check app.hyperliquid.xyz"):
        execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert db.scalar(select(TrPosition)).status == "FAILED"


def test_positions_are_reconciled_whatever_mode_the_account_is_in_now(db: Session, md: FakeMD) -> None:  # noqa: F811
    m, acc, pos, ex = _testnet_position(db, md, "1131")
    acc.mode = "PAPER"  # e.g. a key change switched the account (before: TESTNET / LIVE positions were never checked again)
    db.commit()
    ex.held.clear()  # its stop fired on the exchange
    assert execution.reconcile(db, acc, ex) == 1 and pos.status == "CLOSED"


def test_a_position_whose_stop_failed_is_still_reconciled(db: Session, md: FakeMD) -> None:  # noqa: F811
    m, acc, pos, ex = _testnet_position(db, md, "1141")
    pos.status = "PROTECTION_FAILED"
    db.commit()
    assert execution.reconcile(db, acc, ex) == 0 and pos.status == "PROTECTION_FAILED"  # still on the exchange: left alone
    ex.held.clear()  # closed on Hyperliquid (by the member, or liquidated)
    assert execution.reconcile(db, acc, ex) == 1 and pos.status == "CLOSED"  # before: stuck, blocking the coin forever


def test_closing_a_position_the_exchange_already_closed_books_it_instead_of_failing(db: Session, md: FakeMD) -> None:  # noqa: F811
    m, acc, pos, ex = _testnet_position(db, md, "1151")
    ex.held.clear()  # the stop fired a moment ago; reconciliation hasn't run yet
    out = execution.close_position(db, m, pos.id, ex)
    assert out.status == "CLOSED" and out.close_reason == "exchange"  # before: "Close not filled", over and over


def test_a_target_the_price_already_passed_is_refused() -> None:
    p = plan(mark=104.5, entry_zone=(103.0, 106.0), target=104.0)  # LONG, TP1 below the price: it would fire at once
    assert not p.ok and any("passed the target" in r for r in p.reasons)
    assert plan(side="SHORT", mark=100.0, entry_zone=(99.0, 101.0), stop=102.0, target=96.0).ok


def test_a_new_api_wallet_or_removing_one_never_strands_open_positions(db: Session, md: FakeMD) -> None:  # noqa: F811
    m, acc, pos, ex = _testnet_position(db, md, "1161")
    with pytest.raises(accounts.AccountError, match="Close your open testnet bot positions"):
        accounts.agent_start(db, m, "TESTNET", "0x66eee")  # the old key would be replaced by one not approved yet
    acc.mode = "PAPER"
    db.commit()
    with pytest.raises(accounts.AccountError, match="Close your open bot positions"):
        accounts.revoke_local(db, m, "TESTNET")  # before: allowed when the account was in another mode
    assert acc.testnet_agent_key


class ButtonsTG(FakeTG):
    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        self.sent.append(("msg", chat_id, text, buttons))
        return {"message_id": len(self.sent)}


def test_telegram_close_offers_a_position_whose_stop_failed(db: Session, md: FakeMD) -> None:  # noqa: F811
    m, acc, pos, ex = _testnet_position(db, md, "1171")
    pos.status = "PROTECTION_FAILED"
    db.commit()
    tg = ButtonsTG()
    asyncio.run(bot._trading_command(db, tg, 1171, m, "/close", []))
    buttons = [b for x in tg.sent if x[0] == "msg" for row in (x[3] or []) for b in row]
    assert any(b["callback_data"] == f"cl:{pos.id}" for b in buttons)  # before: "No open bot positions."




def test_auto_waiting_for_the_entry_zone_logs_its_reason_once_not_every_minute(db: Session, md: FakeMD) -> None:  # noqa: F811
    from datetime import timedelta

    from app.db.base import utcnow
    from app.models.tradingos import KbEvent

    m = kiwi(db, 4, "1181")
    accounts.update(db, m, {"auto_enabled": True, "auto_hours": 2})
    sig = active_signal(db, md)
    sig.entry_lo, sig.entry_hi = sig.entry_lo * 1.2, sig.entry_hi * 1.2  # the price is well below the zone
    sig.published_at = utcnow() - timedelta(hours=1)
    db.commit()
    for _ in range(3):  # the trade cycle looks again every minute
        assert execution.run_auto(db, sig) == []
    rows = db.scalars(select(KbEvent).where(KbEvent.member_id == m.id, KbEvent.kind == "risk")).all()
    assert len(rows) == 1 and "outside the entry zone" in rows[0].title  # before: one identical line a minute
