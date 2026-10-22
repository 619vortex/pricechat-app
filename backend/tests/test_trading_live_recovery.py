"""Live-money execution under the failures that actually happen: a process restart between the entry fill and its stop, an
emergency close that didn't go through, a malformed account answer, a cancel Hyperliquid refused, a stop whose placement
timed out but may have landed, two entries racing for one coin, a loss the daily limit couldn't see (unrealized, or closed by
hand on Hyperliquid). No network: the fake market and fake exchange of test_trading_execution (TEST FIXTURES)."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.trading import TrOrder, TrPosition
from app.tradingos import accounts, execution, risk
from app.tradingos import exchange as xch

from tests.test_trading_execution import FakeExchange, _no_live_balances, active_signal, md, on_testnet  # noqa: F401 - fixtures
from tests.test_trading_os import FakeMD, kiwi


class Crash(BaseException):
    """TEST FIXTURE: the process stops (a deploy restart, an OOM kill): not an Exception, so nothing in the entry path catches it."""


class CrashOnStop(FakeExchange):
    def trigger(self, coin: str, is_buy: bool, size: float, trigger_px: float, kind: str, cloid: str) -> xch.Result:
        if kind == "sl" and not getattr(self, "recovered", False):
            raise Crash()
        return super().trigger(coin, is_buy, size, trigger_px, kind, cloid)


def _age(db: Session, pos: TrPosition, minutes: int = 10) -> None:
    db.execute(update(TrPosition).where(TrPosition.id == pos.id).values(updated_at=utcnow() - timedelta(minutes=minutes)))
    db.commit()
    db.refresh(pos)


# ---------------------------------------------------------------- an entry interrupted between its fill and its stop
def test_a_restart_between_the_fill_and_the_stop_is_recovered_and_protected(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1201")
    acc = on_testnet(db, m)
    ex = CrashOnStop(mark=md.series["SOL"][-1].c)
    it = execution.preview(db, m, sig, ex)
    with pytest.raises(Crash):
        execution.confirm(db, m, it.token, ex)
    db.rollback()
    pos = db.scalar(select(TrPosition))
    assert pos.status == "OPENING" and ex.held["SOL"] > 0  # a real position, no stop: before, it stayed like this forever
    execution.reconcile(db, acc, ex)
    assert pos.status == "OPENING"  # still in flight as far as anyone can tell: left alone
    _age(db, pos)
    ex.recovered = True
    execution.reconcile(db, acc, ex)
    assert pos.status == "OPEN" and pos.size == pytest.approx(ex.held["SOL"])
    sl = db.scalar(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "sl"))
    assert sl.status == "RESTING" and sl.size == pytest.approx(pos.size) and sl.cloid != xch.cloid_for(acc.id, sig.id, "sl")


def test_a_stale_opening_row_with_nothing_on_the_exchange_fails_and_frees_the_signal(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1202")
    acc = on_testnet(db, m)
    pos = TrPosition(account_id=acc.id, signal_id=sig.id, mode="TESTNET", coin="SOL", side="LONG", status="OPENING", leverage=3,
                     stop=sig.stop, tp1=sig.target, tp2=sig.target, max_loss_usd=5, via="enter")
    db.add(pos)
    db.commit()
    _age(db, pos)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    execution.reconcile(db, acc, ex)
    assert pos.status == "FAILED" and pos.size == 0 and not ex.orders
    assert execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex).status == "OPEN"  # retried in place


def test_an_interrupted_paper_entry_does_not_stay_opening(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1203")
    acc = execution.account(db, m)
    pos = TrPosition(account_id=acc.id, signal_id=sig.id, mode="PAPER", coin="SOL", side="LONG", status="OPENING", leverage=3, size=0.5,
                     entry_px=md.series["SOL"][-1].c, stop=sig.stop, tp1=sig.target, tp2=sig.target, max_loss_usd=5, via="enter")
    db.add(pos)
    db.commit()
    _age(db, pos)
    md.candles = lambda coin, interval, count=200: []  # type: ignore[method-assign]
    execution.track_paper(db)
    assert pos.status == "OPEN"


# ---------------------------------------------------------------- the emergency policy, kept
def test_an_emergency_close_that_failed_is_retried_until_flat(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1204")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c, stop_fails=True)
    ex.close_fails = True  # e.g. "price too far from oracle" on the first try
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "PROTECTION_FAILED" and ex.held["SOL"] > 0 and acc.paused and "not filled" in pos.error
    ex.close_fails = False
    execution.reconcile(db, acc, ex)
    assert pos.status == "CLOSED" and "SOL" not in ex.held
    closes = [o["cloid"] for o in ex.orders if o["type"] == "market" and o["reduce"]]
    assert len(closes) == 2 and closes[0] != closes[1]  # a fresh client order id per attempt (the column is unique too)


def test_emergency_retries_are_bounded(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1205")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c, stop_fails=True)
    ex.close_fails = True
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    for _ in range(execution.EMERGENCY_RETRIES + 3):
        execution.reconcile(db, acc, ex)
    assert pos.status == "PROTECTION_FAILED"
    assert len([o for o in ex.orders if o["type"] == "market" and o["reduce"]]) == execution.EMERGENCY_RETRIES


class TimedOutStop(FakeExchange):
    """TEST FIXTURE: the stop-loss request times out after Hyperliquid accepted it (it rests there, unknown to us)."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.cancelled_cloids: list[str] = []

    def trigger(self, coin: str, is_buy: bool, size: float, trigger_px: float, kind: str, cloid: str) -> xch.Result:
        super().trigger(coin, is_buy, size, trigger_px, kind, cloid)
        raise TimeoutError("read timed out")

    def cancel_by_cloid(self, coin: str, cloid: str) -> None:
        self.cancelled_cloids.append(cloid)


def test_a_stop_that_may_have_landed_is_cancelled_once_the_emergency_close_is_flat(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1206")
    acc = on_testnet(db, m)
    ex = TimedOutStop(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "CLOSED" and acc.paused
    assert ex.cancelled_cloids == [xch.cloid_for(acc.id, sig.id, "sl")]  # a stale reduce-only stop can't hit the next SOL position


# ---------------------------------------------------------------- reconciliation reads Hyperliquid carefully
def test_a_malformed_account_answer_closes_nothing_and_cancels_no_stop(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1207")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    ex.state = lambda: {"error": "rate limited"}  # type: ignore[method-assign]
    with pytest.raises(xch.ExchangeError):
        execution.reconcile(db, acc, ex)
    db.rollback()
    assert pos.status == "OPEN" and ex.cancelled == []


def test_a_position_on_the_other_side_is_not_the_bots(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1208")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    ex.held["SOL"] = -ex.held["SOL"]  # the long was closed and the member opened a short by hand
    assert execution.reconcile(db, acc, ex) == 1 and pos.status == "CLOSED"


def test_a_cancel_hyperliquid_refused_is_not_booked_as_cancelled(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1209")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)

    def refuse(coin: str, oid: int | None) -> None:
        raise xch.ExchangeError("Hyperliquid refused the cancel")

    ex.cancel = refuse  # type: ignore[method-assign]
    ex.held.clear()
    execution.reconcile(db, acc, ex)
    left = db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.status == "RESTING")).all()
    assert pos.status == "CLOSED" and left and "open orders" in pos.error


def test_hyperliquid_cancel_and_status_answers_are_read_strictly() -> None:
    xch.check_cancel({"status": "ok", "response": {"type": "cancel", "data": {"statuses": ["success"]}}})
    xch.check_cancel({"status": "ok", "response": {"data": {"statuses": [{"error": "Order was never placed, already canceled, or filled."}]}}})
    for bad in ({"status": "err", "response": "User or API Wallet does not exist."}, None,
                {"status": "ok", "response": {"data": {"statuses": [{"error": "Rate limited"}]}}}):
        with pytest.raises(xch.ExchangeError):
            xch.check_cancel(bad)
    # a bare string status must never be read as a fill by a substring match
    assert xch.parse_status({"status": "ok", "response": {"data": {"statuses": ["filled"]}}}).status == "ERROR"


def test_the_sdk_is_built_with_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import hyperliquid.exchange
    import hyperliquid.info

    seen: dict[str, Any] = {}

    class Info:
        def __init__(self, *a: Any, **kw: Any) -> None:
            seen["info"] = kw.get("timeout")

    class Exchange:
        def __init__(self, *a: Any, **kw: Any) -> None:
            seen["exchange"] = kw.get("timeout")

    monkeypatch.setattr(hyperliquid.info, "Info", Info)
    monkeypatch.setattr(hyperliquid.exchange, "Exchange", Exchange)
    xch.HyperliquidExecutor("TESTNET", "0x" + "11" * 32, "0x" + "1" * 40)
    assert seen == {"info": xch.SDK_TIMEOUT, "exchange": xch.SDK_TIMEOUT} and xch.SDK_TIMEOUT > 0


# ---------------------------------------------------------------- limits that hold under races and unbooked losses
def test_a_second_signal_in_the_same_coin_cannot_slip_past_the_preview(db: Session, md: FakeMD) -> None:
    """Two previews in one coin (two tabs, AUTO and ENTER, a 1h and a 15m signal) both passed before either filled."""
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1210")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    plan = risk.Plan(**execution.preview(db, m, sig, ex).plan)
    other = active_signal(db, md, "ETH")
    db.add(TrPosition(account_id=acc.id, signal_id=other.id, mode="TESTNET", coin="SOL", side="LONG", status="OPEN", leverage=3, size=1,
                      entry_px=plan.entry, stop=plan.stop, tp1=plan.tp1, tp2=plan.tp2, max_loss_usd=5, via="auto"))
    db.commit()
    with pytest.raises(execution.TradeError, match="already have a bot position in SOL"):
        execution.open_position(db, acc, sig, plan, "enter", ex)
    assert not ex.orders


def test_an_unprotected_position_counts_as_open(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1211")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c, stop_fails=True)
    ex.close_fails = True
    execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert execution.open_count(db, acc) == 1
    with pytest.raises(accounts.AccountError, match="Close your open bot positions"):
        accounts.update(db, m, {"mode": "PAPER"})  # switching away would leave it unmanaged


def test_the_daily_loss_limit_sees_open_losses(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1212")
    acc = on_testnet(db, m)
    acc.daily_loss_limit_usd = 15
    eth = active_signal(db, md, "ETH")
    mark = md.series["ETH"][-1].c
    db.add(TrPosition(account_id=acc.id, signal_id=eth.id, mode="TESTNET", coin="ETH", side="LONG", status="OPEN", leverage=3, size=1,
                      entry_px=mark + 20, stop=mark - 50, tp1=mark + 50, tp2=mark + 60, max_loss_usd=5, via="auto"))
    db.commit()  # $20 under water, nothing realized today
    assert execution.daily_pnl(db, acc) == 0 and execution.day_loss_basis(db, acc) == pytest.approx(-20)
    with pytest.raises(execution.TradeError, match="daily loss limit"):
        execution.preview(db, m, sig, FakeExchange(mark=md.series["SOL"][-1].c))


def test_a_position_closed_by_hand_on_hyperliquid_books_its_real_loss(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1213")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    entry = db.scalar(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "entry"))
    size = pos.size
    ex.held.clear()
    ex.fills = [{"coin": "SOL", "oid": entry.oid, "time": 1, "dir": "Open Long", "px": "100", "sz": str(size), "startPosition": "0",
                 "closedPnl": "0", "fee": "0.05"},
                {"coin": "SOL", "oid": 999_999, "time": 2, "dir": "Close Long", "px": "95", "sz": str(size), "startPosition": str(size),
                 "closedPnl": "-7.5", "fee": "0.05"},  # the member's own market close on app.hyperliquid.xyz
                {"coin": "SOL", "oid": 999_998, "time": 3, "dir": "Open Long", "px": "96", "sz": "1", "startPosition": "0",
                 "closedPnl": "0", "fee": "0.04"}]  # a later position of their own: not the bot's
    execution.reconcile(db, acc, ex)
    assert pos.status == "CLOSED" and pos.realized_pnl == pytest.approx(-7.6) and pos.exit_px == 95
    assert execution.daily_pnl(db, acc) == pytest.approx(-7.6)


class NoAvgPx(FakeExchange):
    def market(self, coin: str, is_buy: bool, size: float, limit_px: float, cloid: str, reduce_only: bool = False) -> xch.Result:
        r = super().market(coin, is_buy, size, limit_px, cloid, reduce_only)
        r.avg_px = None
        return r


def test_a_fill_without_an_average_price_still_gets_its_stop(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1214")
    on_testnet(db, m)
    ex = NoAvgPx(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "OPEN" and pos.entry_px and any(o["type"] == "sl" for o in ex.orders)


class RaisingTriggers(FakeExchange):
    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.raise_triggers = False

    def trigger(self, coin: str, is_buy: bool, size: float, trigger_px: float, kind: str, cloid: str) -> xch.Result:
        if self.raise_triggers:
            raise TimeoutError("read timed out")
        return super().trigger(coin, is_buy, size, trigger_px, kind, cloid)


def test_a_partial_close_stays_booked_when_resizing_the_stop_times_out(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "1215")
    on_testnet(db, m)
    ex = RaisingTriggers(mark=md.series["SOL"][-1].c, equity=100_000)
    acc = execution.account(db, m)
    acc.max_loss_per_trade_usd, acc.max_allocation_pct = 100.0, 50.0
    db.commit()
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    full = pos.size
    ex.raise_triggers = True
    execution.close_position(db, m, pos.id, ex, 0.5)
    db.rollback()  # whatever the route does next: the fill must already be on record
    db.refresh(pos)
    assert pos.size < full and pos.status == "OPEN" and "previous stop-loss stays" in (pos.error or "")
