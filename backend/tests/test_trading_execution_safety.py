"""Trade execution under exchange trouble: a stop-loss call that raises, market data or the API wallet failing before the entry,
a retry after an unfilled confirm, one AUTO account's surprise not skipping the others, and the Telegram footer never cutting
HTML. No network: the fake market and fake exchange of test_trading_execution (TEST FIXTURES)."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trading import TrOrder, TrPosition
from app.tradingos import accounts, bot, execution, market, risk
from app.tradingos import exchange as xch

from tests.test_trading_execution import FakeExchange, _no_live_balances, active_signal, md, on_testnet  # noqa: F401 - fixtures
from tests.test_trading_os import FakeMD, FakeTG, kiwi


class RaisingStop(FakeExchange):
    """TEST FIXTURE: the SDK raises (timeout / HTTP error) on the stop-loss, or on every trigger."""

    def __init__(self, *a: Any, kinds: tuple[str, ...] = ("sl",), **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.kinds = kinds

    def trigger(self, coin: str, is_buy: bool, size: float, trigger_px: float, kind: str, cloid: str) -> xch.Result:
        if kind in self.kinds:
            self.orders.append({"type": kind, "coin": coin, "raised": True})
            raise TimeoutError("read timed out")
        return super().trigger(coin, is_buy, size, trigger_px, kind, cloid)


def test_a_stop_loss_call_that_raises_closes_at_once_and_pauses_the_account(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "901")
    acc = on_testnet(db, m)
    ex = RaisingStop(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "CLOSED" and pos.close_reason == "emergency_close" and "TimeoutError" in pos.error
    assert ex.orders[-1]["reduce"] and "SOL" not in ex.held and acc.paused
    assert db.scalar(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "sl")).status == "ERROR"  # on record
    assert not db.scalars(select(TrPosition).where(TrPosition.status == "OPENING")).all()


def test_no_price_for_the_emergency_close_still_closes_off_the_entry(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "902")
    acc = on_testnet(db, m)
    ex = RaisingStop(mark=md.series["SOL"][-1].c)
    it = execution.preview(db, m, sig, ex)

    def no_price(*a: Any, **k: Any) -> float:
        raise market.MarketError("429")

    monkeypatch.setattr(execution, "ref_px", no_price)
    pos = execution.confirm(db, m, it.token, ex)
    assert pos.status == "CLOSED" and acc.paused and "SOL" not in ex.held


def test_a_raising_take_profit_keeps_the_stop_and_the_position(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "903")
    acc = on_testnet(db, m)
    ex = RaisingStop(mark=md.series["SOL"][-1].c, kinds=("tp",))
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "OPEN" and not acc.paused and ex.held["SOL"] > 0
    kinds = {o.kind: o.status for o in db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id))}
    assert kinds["sl"] == "RESTING" and kinds["tp1"] == "ERROR"


def test_market_data_or_the_api_wallet_failing_before_the_entry_leaves_no_row(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "904")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    plan = risk.Plan(**execution.preview(db, m, sig, ex).plan)

    def limited(*a: Any, **k: Any) -> Any:
        raise market.MarketError("429 Too Many Requests")

    with monkeypatch.context() as mp:
        mp.setattr(md, "market", limited)
        with pytest.raises(market.MarketError):
            execution.open_position(db, acc, sig, plan, "enter", ex)
    acc.testnet_agent_key = None  # no API wallet key: the executor can't be built
    db.commit()
    with pytest.raises(execution.TradeError, match="API wallet"):
        execution.open_position(db, acc, sig, plan, "enter")
    assert not db.scalars(select(TrPosition)).all() and not ex.orders


def test_a_retry_after_an_unfilled_confirm_enters_with_a_new_order_id(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "905")
    on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c, no_fill=True)
    with pytest.raises(execution.TradeError, match="Not filled"):
        execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    ex.no_fill = False
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "OPEN" and len(db.scalars(select(TrPosition)).all()) == 1  # the FAILED row, retried in place
    first, second = [o["cloid"] for o in ex.orders if o["type"] == "market"]
    assert first == xch.cloid_for(pos.account_id, sig.id, "entry") and second != first  # Hyperliquid never sees a reused id
    with pytest.raises(execution.TradeError):  # a filled position is never entered twice
        execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert len([o for o in ex.orders if o["type"] == "market"]) == 2


def test_confirm_prices_the_entry_at_the_current_mark_not_the_previews(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "906")
    on_testnet(db, m)
    mark = md.series["SOL"][-1].c
    ex = FakeExchange(mark=mark)
    it = execution.preview(db, m, sig, ex)
    it.plan = {**it.plan, "entry": mark * 0.5}  # a stale preview price
    db.commit()
    execution.confirm(db, m, it.token, ex)
    assert ex.orders[0]["px"] == pytest.approx(mark * (1 + 2 * risk.SLIPPAGE), rel=1e-3)


def test_one_auto_accounts_exchange_surprise_does_not_skip_the_others(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = kiwi(db, 4, "907"), kiwi(db, 4, "908")
    for m in (a, b):
        accounts.update(db, m, {"auto_enabled": True, "auto_hours": 2})
    bad = execution.account(db, a).id
    real = execution.open_position

    def flaky(db_: Session, acc: Any, *rest: Any, **kw: Any) -> Any:
        if acc.id == bad:
            raise RuntimeError("SDK exploded")
        return real(db_, acc, *rest, **kw)

    monkeypatch.setattr(execution, "open_position", flaky)
    assert execution.run_auto(db, active_signal(db, md)) == [execution.account(db, b).id]


def test_the_footer_is_dropped_never_sliced_into_the_html() -> None:
    tg = FakeTG()
    foot = "\n\n📢 Bot channel: @botpricechat\n🎁 Trade on Hyperliquid · 4% off fees with our link: https://app.hyperliquid.xyz/join/PRICECHAT"
    c = bot._Footed(tg, foot)
    png = b"\x89PNG\r\n\x1a\n"
    near = "x" * 1000 + "<b>a &amp; b</b>"
    asyncio.run(c.send_photo(1, png, near))
    asyncio.run(c.send_photo(1, png, "<b>short</b>"))
    long = "\n".join(["<b>line</b> " + "y" * 30] * 100)  # ~4100: over the message limit by itself
    asyncio.run(c.send_message(1, long))
    assert tg.sent[0][2] == near  # whole, without the footer
    assert tg.sent[1][2] == "<b>short</b>" + foot
    sent = tg.sent[2][2]
    assert len(sent) <= 4096 and long.startswith(sent) and sent.endswith("y") and sent.count("<b>") == sent.count("</b>")
