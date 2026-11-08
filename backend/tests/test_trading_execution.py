"""Trade execution: the risk engine, ENTER → private preview → confirm, fills and exchange-side protection, the emergency
policy, STOP NEW TRADES, closing, paper tracking, reconciliation, AUTO, per-mode results, API-wallet authorization and the
LIVE gates. No network: a fake Hyperliquid market and a fake exchange (TEST FIXTURES)."""

import asyncio
import time
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.trading import TrAccount, TrIntent, TrOrder, TrPosition
from app.models.tradingbot import TbWallet
from app.tradingos import accounts, bot, execution, market, risk, signals
from app.tradingos import exchange as xch
from app.tradingos.market import Candle

from tests.test_trading_os import FakeMD, FakeTG, kiwi, uptrend_pullback


@pytest.fixture(autouse=True)
def _banter_member(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: these tests are about the trading flow; the Banter referral gate on Telegram entries has its own tests
    (test_tg_trading.py)."""
    from app.tradingos import tgtrade

    monkeypatch.setattr(tgtrade, "entry_gate", lambda db, m: None)


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> FakeMD:
    f = FakeMD({"BTC": uptrend_pullback(), "SOL": uptrend_pullback(), "ETH": uptrend_pullback()})
    monkeypatch.setattr(market, "_shared", f)
    return f


@pytest.fixture(autouse=True)
def _no_live_balances(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: the connection status reads balances from Hyperliquid; tests never call the real API."""
    monkeypatch.setattr(xch, "balances", lambda mode, address, *a, **k: {"perps": 0.0, "spot_usdc": 9.99, "unified": False, "trading": 0.0})
    monkeypatch.setattr(xch, "is_unified", lambda *a, **k: False)
    accounts._money.clear()


class FakeExchange:
    """TEST FIXTURE: answers like Hyperliquid would; records every order. `stop_fails` / `no_fill` simulate refusals."""

    mode = "TESTNET"

    def __init__(self, equity: float = 1000.0, mark: float = 120.0, stop_fails: bool = False, no_fill: bool = False,
                 held: dict[str, float] | None = None) -> None:
        self.eq, self.mark, self.stop_fails, self.no_fill = equity, mark, stop_fails, no_fill
        self.close_fails = False
        self.orders: list[dict[str, Any]] = []
        self.cancelled: list[int] = []
        self.leverage: list[tuple[str, int]] = []
        self.held = dict(held or {})
        self.fills: list[dict[str, Any]] = []
        self._oid = 1000

    def equity(self) -> float:
        return self.eq

    def state(self) -> dict[str, Any]:
        return {"assetPositions": [{"position": {"coin": c, "szi": str(s)}} for c, s in self.held.items()]}

    def set_leverage(self, coin: str, lev: int) -> None:
        self.leverage.append((coin, lev))

    def market(self, coin: str, is_buy: bool, size: float, limit_px: float, cloid: str, reduce_only: bool = False) -> xch.Result:
        self._oid += 1
        self.orders.append({"type": "market", "coin": coin, "buy": is_buy, "size": size, "px": limit_px, "cloid": cloid, "reduce": reduce_only})
        if self.no_fill and not reduce_only:
            return xch.Result("REJECTED", error="Order could not immediately match")
        if self.close_fails and reduce_only:
            return xch.Result("REJECTED", error="Order could not immediately match")
        if reduce_only:  # like Hyperliquid: a reduce-only order never flips the position
            size = min(size, abs(self.held.get(coin, 0.0)))
        self.held[coin] = self.held.get(coin, 0.0) + (size if is_buy else -size)
        if abs(self.held[coin]) < 1e-12:
            del self.held[coin]
        return xch.Result("FILLED", size, self.mark, self._oid)

    def trigger(self, coin: str, is_buy: bool, size: float, trigger_px: float, kind: str, cloid: str) -> xch.Result:
        self._oid += 1
        self.orders.append({"type": kind, "coin": coin, "buy": is_buy, "size": size, "px": trigger_px, "cloid": cloid, "reduce": True})
        if kind == "sl" and self.stop_fails:
            return xch.Result("REJECTED", error="Invalid TP/SL price")
        return xch.Result("RESTING", oid=self._oid)

    def cancel(self, coin: str, oid: int | None) -> None:
        self.cancelled.append(oid or 0)

    def fills_since(self, start_ms: int) -> list[dict[str, Any]]:
        return self.fills


def active_signal(db: Session, md: FakeMD, coin: str = "SOL") -> Any:
    sig = signals.create(db, signals.analyze(md, coin), "1h", "ACTIVE", "test")
    sig.published_at = utcnow()
    db.commit()
    return sig


def on_testnet(db: Session, m: Any) -> Any:
    acc = execution.account(db, m)
    acc.mode, acc.master_address = "TESTNET", "0x" + "1" * 40
    acc.testnet_agent, acc.testnet_agent_key, acc.testnet_approved_at = "0x" + "2" * 40, "unused-in-tests", utcnow()
    db.commit()
    return acc


# ---------------------------------------------------------------- the risk engine
def plan(**kw: Any) -> risk.Plan:
    base = dict(coin="SOL", side="LONG", mark=100.0, entry_zone=(99.0, 101.0), stop=98.0, target=104.0, sig_lev_hi=5, market_max_lev=20,
                sz_decimals=2, equity=1000.0, max_leverage=3, max_loss_usd=5.0, max_allocation_pct=10.0, max_positions=2, open_positions=0,
                daily_pnl=0.0, daily_loss_limit=15.0, allowed=[], paused=False, global_paused=False)
    return risk.plan_trade(**{**base, **kw})


def test_size_comes_from_the_loss_to_the_stop_within_every_cap() -> None:
    p = plan()
    assert p.ok and p.leverage == 3  # the owner's 3x, not the signal's 5x
    assert p.max_loss_usd <= 5.0 and p.size * 2 <= 5.0  # $2 to the stop per unit: ~2.4 units incl. fees and slippage
    assert p.tp1 == 104.0 and p.tp2 == pytest.approx(106.0)
    small = plan(max_allocation_pct=1.0)  # the margin allocation caps the size: 1% of $1000 at 3x = $30
    assert small.ok and small.notional <= 30.0
    assert not plan(equity=20.0, max_allocation_pct=10.0).ok  # $6 notional: under Hyperliquid's $10 minimum
    for kw, why in ((dict(paused=True), "STOP NEW TRADES"), (dict(global_paused=True), "paused for everyone"),
                    (dict(open_positions=2), "positions open"), (dict(daily_pnl=-15.0), "daily loss"), (dict(mark=110.0), "entry zone"),
                    (dict(allowed=["BTC"]), "allowed"), (dict(stop=101.0), "wrong side")):
        r = plan(**kw)
        assert not r.ok and any(why in x for x in r.reasons), (kw, r.reasons)
    assert not plan(open_positions=3, max_positions=9).ok  # the hard cap (3) whatever the setting
    assert plan(max_leverage=50, sig_lev_hi=50).leverage == risk.HARD_MAX_LEVERAGE


# ---------------------------------------------------------------- ENTER → preview → confirm (paper)
def test_enter_sends_a_private_preview_and_only_confirm_trades(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    kiwi(db, 4, "701")
    tg = FakeTG()
    asyncio.run(bot.handle_callback(db, tg, {"id": "a", "data": f"sig:{sig.id}:enter", "from": {"id": 701}}))
    dm = [x for x in tg.sent if x[0] == "msg" and x[1] == 701][-1]
    assert "PAPER" in dm[2] and "Stop" in dm[2] and "TP1" in dm[2] and "TP2" in dm[2] and "Max planned loss" in dm[2]
    assert not db.scalars(select(TrPosition)).all()  # nothing traded yet
    token = db.scalar(select(TrIntent)).token
    asyncio.run(bot.handle_callback(db, tg, {"id": "b", "data": f"cf:{token}", "from": {"id": 701}}))
    pos = db.scalar(select(TrPosition))
    assert pos.status == "OPEN" and pos.mode == "PAPER" and pos.size > 0 and pos.entry_px
    kinds = sorted(o.kind for o in db.scalars(select(TrOrder)))
    assert kinds == ["entry", "sl", "tp1", "tp2"]
    assert "filled" in tg.sent[-2][2] or "filled" in " ".join(str(x) for x in tg.sent[-3:])
    asyncio.run(bot.handle_callback(db, tg, {"id": "c", "data": f"cf:{token}", "from": {"id": 701}}))
    assert "Already handled" in tg.sent[-1][2]  # single use
    assert signals.participation(db, sig).get("enter") == 1
    asyncio.run(bot.handle_callback(db, tg, {"id": "d", "data": f"sig:{sig.id}:enter", "from": {"id": 701}}))
    assert "already have a bot position" in tg.sent[-1][2]  # never twice


def test_confirmations_expire_and_belong_to_one_member(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    a, b = kiwi(db, 4, "711"), kiwi(db, 4, "712")
    it = execution.preview(db, a, sig)
    with pytest.raises(execution.TradeError, match="isn't yours"):
        execution.confirm(db, b, it.token)
    it.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(execution.TradeError, match="expired"):
        execution.confirm(db, a, it.token)
    it2 = execution.preview(db, a, sig)
    execution.account(db, a).paused = True  # STOP NEW TRADES between the preview and the confirm
    db.commit()
    with pytest.raises(execution.TradeError, match="stopped"):
        execution.confirm(db, a, it2.token)
    assert not db.scalars(select(TrPosition)).all()


# ---------------------------------------------------------------- real-exchange path (fake exchange)
def test_fill_then_exchange_side_stop_and_two_targets(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "721")
    on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    it = execution.preview(db, m, sig, ex)
    pos = execution.confirm(db, m, it.token, ex)
    assert pos.status == "OPEN" and pos.mode == "TESTNET" and ex.leverage == [("SOL", pos.leverage)]
    entry, sl, tp1, tp2 = ex.orders
    assert entry["type"] == "market" and entry["buy"] and not entry["reduce"]
    assert sl["type"] == "sl" and not sl["buy"] and sl["reduce"] and sl["size"] == pos.size  # the stop covers everything
    assert tp1["size"] + tp2["size"] == pytest.approx(pos.size) and tp1["reduce"] and tp2["reduce"]
    assert entry["cloid"] == xch.cloid_for(pos.account_id, sig.id, "entry")  # deterministic: a retry can't double-enter


def test_no_fill_means_no_position_and_no_protective_orders(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "731")
    on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c, no_fill=True)
    it = execution.preview(db, m, sig, ex)
    with pytest.raises(execution.TradeError, match="Not filled"):
        execution.confirm(db, m, it.token, ex)
    assert db.scalar(select(TrPosition)).status == "FAILED" and len(ex.orders) == 1


def test_a_stop_that_cannot_be_placed_closes_the_position_and_stops_new_trades(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "741")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c, stop_fails=True)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    assert pos.status == "CLOSED" and pos.close_reason == "emergency_close" and "stop-loss not placed" in pos.error
    assert ex.orders[-1]["reduce"] and "SOL" not in ex.held  # flat again
    assert acc.paused and acc.paused_reason.startswith("Protection failed")
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, 741, 741, "/resume"))
    assert "safety check" in tg.sent[-1][2] and acc.paused  # can't be waved away from Telegram


def test_never_touches_a_position_the_member_already_holds(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "751")
    on_testnet(db, m)
    with pytest.raises(execution.TradeError, match="won't touch"):
        execution.preview(db, m, sig, FakeExchange(held={"SOL": -3.0}))


def test_stop_new_trades_keeps_protection_and_close_cancels_only_its_own_orders(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "761")
    on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, 761, 761, "/pause"))
    assert "NEW TRADES STOPPED" in tg.sent[-1][2] and ex.cancelled == []  # protective orders stay
    with pytest.raises(execution.TradeError, match="STOP NEW TRADES"):
        execution.preview(db, m, active_signal(db, md, "ETH"), ex)
    own = {o.oid for o in db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind.in_(("sl", "tp1", "tp2"))))}
    execution.close_position(db, m, pos.id, ex)
    assert set(ex.cancelled) == own and pos.status == "CLOSED" and "SOL" not in ex.held


def test_reconcile_books_exchange_closes_from_fills(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "771")
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c)
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    sl = db.scalar(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "sl"))
    ex.held.clear()  # the stop fired on the exchange
    ex.fills = [{"oid": sl.oid, "dir": "Close Long", "px": str(pos.stop), "closedPnl": "-4.2", "fee": "0.05"}]
    assert execution.reconcile(db, acc, ex) == 1
    assert pos.status == "CLOSED" and pos.realized_pnl == pytest.approx(-4.25) and pos.exit_px == pos.stop
    res = execution.results(db, acc)
    assert res["TESTNET"]["trades"] == 1 and res["PAPER"]["trades"] == 0 and res["LIVE"]["trades"] == 0  # never mixed


# ---------------------------------------------------------------- paper tracking and AUTO
def test_paper_takes_tp1_then_stops_the_rest(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "781")
    pos = execution.confirm(db, m, execution.preview(db, m, sig).token)
    full = pos.size
    now = int(time.time() * 1000) + 120_000
    md.candles = lambda coin, interval, count=200: [Candle(now, pos.tp1, pos.tp1 + 0.01, pos.tp1 - 0.01, pos.tp1, 1)]  # type: ignore[method-assign]
    execution.track_paper(db)
    assert pos.tp1_done and pos.size == pytest.approx(full / 2) and pos.realized_pnl > 0
    md.candles = lambda coin, interval, count=200: [Candle(now + 60_000, pos.stop, pos.stop, pos.stop - 1, pos.stop - 1, 1)]  # type: ignore[method-assign]
    execution.track_paper(db)
    assert pos.status == "CLOSED" and pos.close_reason == "stop"


def test_auto_enters_once_within_limits_and_test_signals_never_trade_real_money(db: Session, md: FakeMD) -> None:
    m = kiwi(db, 4, "791")
    accounts.update(db, m, {"auto_enabled": True, "auto_hours": 2})
    sig = active_signal(db, md)
    assert execution.run_auto(db, sig) and execution.run_auto(db, sig) == []  # once per signal
    assert db.scalar(select(TrPosition)).via == "auto"
    t = signals.test_signal(db, md, "ETH", "admin")
    t.status = "ACTIVE"
    acc = on_testnet(db, m)
    acc.auto_enabled, acc.auto_until = True, utcnow() + timedelta(hours=1)
    db.commit()
    assert execution.run_auto(db, t) == []  # a TEST signal never trades testnet or live money


def test_a_manual_signal_is_real_auto_enters_it_on_testnet_and_it_counts(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner's "Push a signal now" is a real signal: AUTO enters it in the member's own mode (here TESTNET), it says
    Manual setup on the card, and it's in the track record (a pipeline test stays out of both)."""
    m = kiwi(db, 4, "795")
    acc = on_testnet(db, m)
    acc.auto_enabled, acc.auto_until = True, utcnow() + timedelta(hours=1)
    db.commit()
    ex = FakeExchange(mark=md.series["SOL"][-1].c, equity=100_000)
    monkeypatch.setattr(execution, "executor", lambda *a, **k: ex)  # testnet, faked: nothing leaves the test
    sig = signals.manual_signal(db, md, "SOL", "admin")
    assert sig.strategy == "manual" and "Manual setup" in signals.card(sig)
    sig.status = "ACTIVE"
    sig.published_at = utcnow()
    db.commit()
    assert execution.run_auto(db, sig) == [acc.id]
    assert signals.stats(db)["total"] >= 1  # counted, unlike a test


def test_auto_runs_up_to_180_days_or_until_turned_off(db: Session, md: FakeMD) -> None:
    m = kiwi(db, 4, "796")
    accounts.update(db, m, {"auto_enabled": True, "auto_hours": 24 * 180})
    acc = db.scalar(select(TrAccount).where(TrAccount.member_id == m.id))
    assert acc.auto_enabled and acc.auto_until is not None
    with pytest.raises(accounts.AccountError, match="180 days"):
        accounts.update(db, m, {"auto_enabled": True, "auto_hours": 24 * 181})
    accounts.update(db, m, {"auto_enabled": True, "auto_hours": 0})  # until I turn it off
    db.refresh(acc)
    assert acc.auto_enabled and acc.auto_until is None
    sig = active_signal(db, md)
    assert execution.run_auto(db, sig) == [acc.id]  # no end date: still on
    accounts.update(db, m, {"auto_enabled": False})
    db.refresh(acc)
    assert not acc.auto_enabled


# ---------------------------------------------------------------- settings, authorization and the LIVE gates
def test_limits_are_bounded_and_live_needs_the_allowlist_checks_and_a_typed_confirmation(db: Session, md: FakeMD) -> None:
    m = kiwi(db, 4, "801")
    with pytest.raises(accounts.AccountError):
        accounts.update(db, m, {"max_leverage": 25})
    with pytest.raises(accounts.AccountError, match="testnet API wallet"):
        accounts.update(db, m, {"mode": "TESTNET"})
    with pytest.raises(accounts.AccountError, match="isn't available"):
        accounts.update(db, m, {"mode": "LIVE"})
    with pytest.raises(accounts.AccountError, match="verify your wallet"):
        accounts.agent_start(db, m, "LIVE", "0x66eee")  # not eligible yet: the first unmet requirement is named
    signals.settings(db).live_allowlist = m.username
    db.commit()
    checks = accounts.live_checks(db, m)
    assert not checks["passed"] and not checks["items"]["authorization"]["ok"] and checks["items"]["emergency_stop"]["ok"]
    with pytest.raises(accounts.AccountError, match="ENABLE LIVE"):
        accounts.enable_live(db, m, "yes")
    with pytest.raises(accounts.AccountError, match="pre-live checks"):
        accounts.enable_live(db, m, "ENABLE LIVE")
    assert execution.account(db, m).mode == "PAPER"


def test_api_wallet_approval_must_be_signed_by_the_verified_wallet(db: Session) -> None:
    import eth_account

    m = kiwi(db, 4, "811")
    owner = eth_account.Account.create()
    db.add(TbWallet(member_id=m.id, address=owner.address.lower(), verified_at=utcnow()))
    db.commit()
    start = accounts.agent_start(db, m, "TESTNET", "0x66eee")
    assert start["typed_data"]["message"]["agentAddress"] == start["agent"] and "key" not in str(start).lower().replace("primarytype", "")
    stranger = eth_account.Account.create()
    from eth_account.messages import encode_typed_data

    bad = stranger.sign_message(encode_typed_data(full_message=start["typed_data"])).signature.hex()
    with pytest.raises(accounts.AccountError, match="verified wallet"):
        accounts.agent_finish(db, m, "TESTNET", start["nonce"], "0x66eee", "0x" + bad.removeprefix("0x"))
    good = owner.sign_message(encode_typed_data(full_message=start["typed_data"])).signature.hex()
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        seen.append(body)
        if req.url.path == "/exchange":
            return httpx.Response(200, json={"status": "ok", "response": {"type": "default"}})
        return httpx.Response(200, json=[{"address": start["agent"], "name": "PriceChat", "validUntil": None}])

    out = accounts.agent_finish(db, m, "TESTNET", start["nonce"], "0x66eee", "0x" + good.removeprefix("0x"), httpx.MockTransport(handler))
    assert out["approved"] and execution.account(db, m).testnet_approved_at
    assert seen[0]["action"]["type"] == "approveAgent" and seen[0]["action"]["hyperliquidChain"] == "Testnet"
    accounts.update(db, m, {"mode": "TESTNET"})
    assert execution.account(db, m).mode == "TESTNET"


# ---------------------------------------------------------------- the connected trader's terminal: closes, protection, overview
def _testnet_position(db: Session, md: FakeMD, tg: str) -> tuple[Any, Any, Any, FakeExchange]:
    sig = active_signal(db, md)
    m = kiwi(db, 4, tg)
    acc = on_testnet(db, m)
    ex = FakeExchange(mark=md.series["SOL"][-1].c, equity=100_000)
    accounts.update(db, m, {"max_loss_per_trade_usd": 100, "max_allocation_pct": 50})
    pos = execution.confirm(db, m, execution.preview(db, m, sig, ex).token, ex)
    return m, acc, pos, ex


def test_partial_close_is_reduce_only_and_resizes_the_stop_before_cancelling_the_old_one(db: Session, md: FakeMD) -> None:
    m, acc, pos, ex = _testnet_position(db, md, "821")
    full, old_sl = pos.size, db.scalar(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "sl"))
    n = len(ex.orders)
    execution.close_position(db, m, pos.id, ex, 0.5)
    close, new_sl = ex.orders[n], ex.orders[n + 1]
    assert close["type"] == "market" and close["reduce"] and close["size"] == pytest.approx(risk.round_size(full / 2, 2))
    assert new_sl["type"] == "sl" and new_sl["size"] == pytest.approx(pos.size) and new_sl["px"] == risk.round_px(pos.stop, 2)
    assert old_sl.oid in ex.cancelled and old_sl.status == "CANCELLED"  # cancelled only after the new stop rested
    assert pos.status == "OPEN" and pos.size == pytest.approx(full - close["size"]) and ex.held["SOL"] == pytest.approx(pos.size)
    resting = db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.status == "RESTING")).all()
    assert sorted(o.kind for o in resting) == ["sl", "tp1", "tp2"] and sum(o.size for o in resting if o.kind != "sl") == pytest.approx(pos.size)
    with pytest.raises(execution.TradeError, match="25%, 50% or 100%"):
        execution.close_position(db, m, pos.id, ex, 0.3)


def test_a_close_the_exchange_does_not_fill_leaves_the_position_and_its_stop(db: Session, md: FakeMD) -> None:
    m, acc, pos, ex = _testnet_position(db, md, "831")
    ex.close_fails = True
    with pytest.raises(execution.TradeError, match="unchanged"):
        execution.close_position(db, m, pos.id, ex)
    assert pos.status == "OPEN" and ex.cancelled == [] and ex.held["SOL"] == pytest.approx(pos.size)  # nothing claimed, stop still there
    other = kiwi(db, 4, "832")
    with pytest.raises(execution.TradeError, match="No such open position"):
        execution.close_position(db, other, pos.id, ex)  # never someone else's


class LockProbe(FakeExchange):
    """TEST FIXTURE: before answering, each exchange call checks whether a write transaction is open on the database (on
    SQLite that is the single write lock every other writer would wait for)."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.locked: list[str] = []
        self.calls: list[str] = []
        self.during_market: Any = None

    def _probe(self, what: str) -> None:
        import sqlite3

        from sqlalchemy.engine import make_url

        from app.config.settings import get_settings

        self.calls.append(what)
        c = sqlite3.connect(make_url(get_settings().database_url).database, timeout=0)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.rollback()
        except sqlite3.OperationalError:
            self.locked.append(what)
        finally:
            c.close()

    def market(self, *a: Any, **k: Any) -> xch.Result:
        self._probe("market")
        if self.during_market:
            hook, self.during_market = self.during_market, None
            hook()
        return super().market(*a, **k)

    def trigger(self, *a: Any, **k: Any) -> xch.Result:
        self._probe("trigger")
        return super().trigger(*a, **k)

    def cancel(self, *a: Any, **k: Any) -> None:
        self._probe("cancel")
        super().cancel(*a, **k)

    def fills_since(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
        self._probe("fills")
        return super().fills_since(*a, **k)


def test_closing_holds_no_write_lock_during_exchange_calls_and_a_second_close_is_refused(db: Session, md: FakeMD) -> None:
    from app.db.session import SessionLocal

    m, acc, pos, _ = _testnet_position(db, md, "851")
    ex = LockProbe(mark=md.series["SOL"][-1].c, equity=100_000, held={"SOL": pos.size})
    second: list[str] = []

    def close_again() -> None:  # Telegram's CLOSE arrives while the website's is waiting for Hyperliquid
        with SessionLocal() as other:
            member = other.get(type(m), m.id)
            try:
                execution.close_position(other, member, pos.id, ex)
            except execution.TradeError as e:
                second.append(str(e))

    ex.during_market = close_again
    n = len(ex.orders)
    execution.close_position(db, m, pos.id, ex, 0.5)  # partial: the close, then the stop and targets are resized
    assert second == ["This position is already being closed."] and ex.orders[n]["type"] == "market"
    assert sum(o["type"] == "market" for o in ex.orders[n:]) == 1  # only one close reached the exchange
    assert ex.locked == [] and {"market", "trigger", "cancel"} <= set(ex.calls)
    assert pos.status == "OPEN" and pos.close_reason is None  # the claim is released
    assert accounts.pos_out(pos)["close_reason"] is None
    ex.locked.clear()
    execution.adjust_protection(db, m, pos.id, stop=round(pos.stop + (pos.entry_px - pos.stop) / 2, 2), ex=ex)
    assert ex.locked == []
    execution.close_position(db, m, pos.id, ex)  # the rest: leftover orders cancelled, fills read
    assert ex.locked == [] and pos.status == "CLOSED" and pos.close_reason == "manual_close"


def test_a_failed_close_releases_its_claim(db: Session, md: FakeMD) -> None:
    m, acc, pos, ex = _testnet_position(db, md, "861")

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("socket closed")

    ex.cancel = boom  # type: ignore[method-assign]
    ex.close_fails = True
    with pytest.raises(execution.TradeError, match="unchanged"):
        execution.close_position(db, m, pos.id, ex)
    db.refresh(pos)
    assert pos.close_reason is None and pos.status == "OPEN"
    ex.close_fails = False
    p = execution.close_position(db, m, pos.id, ex)  # not "already being closed"
    assert p.status == "CLOSED"
    pos2 = db.get(TrPosition, pos.id)
    assert pos2 is not None and pos2.close_reason == "manual_close"
    pos2.status, pos2.size, pos2.close_reason = "OPEN", 1.0, f"{execution.CLOSE_LEASE}{int(time.time()) - execution.CLOSE_LEASE_SECONDS - 5}"
    db.commit()  # a claim left by a crash expires
    ex.held["SOL"] = 1.0
    assert execution.close_position(db, m, pos.id, ex).status == "CLOSED"


def test_close_all_reports_each_result_and_partial_failures(db: Session, md: FakeMD) -> None:
    m, acc, pos, ex = _testnet_position(db, md, "841")
    sig2 = active_signal(db, md, "ETH")
    ex2 = FakeExchange(mark=md.series["ETH"][-1].c, equity=100_000)
    pos2 = execution.confirm(db, m, execution.preview(db, m, sig2, ex2).token, ex2)
    ex2.close_fails = True
    out = execution.close_all(db, m, lambda p: ex if p.coin == "SOL" else ex2)
    assert out["requested"] == 2 and [c["coin"] for c in out["closed"]] == ["SOL"] and [f["coin"] for f in out["failed"]] == ["ETH"]
    assert pos.status == "CLOSED" and pos2.status == "OPEN" and "SOL" not in ex.held
    assert all(o["reduce"] for o in ex.orders[1:] + ex2.orders[1:])  # after each entry: only reduce-only orders


def test_the_stop_can_only_be_tightened_and_a_dry_run_sends_nothing(db: Session, md: FakeMD) -> None:
    m, acc, pos, ex = _testnet_position(db, md, "851")
    mark = md.series["SOL"][-1].c
    with pytest.raises(execution.TradeError, match="increase your risk"):
        execution.adjust_protection(db, m, pos.id, stop=pos.stop * 0.95, ex=ex)
    with pytest.raises(execution.TradeError, match="trigger at once"):
        execution.adjust_protection(db, m, pos.id, stop=mark * 1.01, ex=ex)
    tighter = risk.round_px((pos.stop + mark) / 2, 2)
    n = len(ex.orders)
    dry = execution.adjust_protection(db, m, pos.id, stop=tighter, dry_run=True, ex=ex)
    assert dry["dry_run"] and len(ex.orders) == n and "Stop-loss" in dry["changes"][0]
    old_sl = db.scalar(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "sl"))
    execution.adjust_protection(db, m, pos.id, stop=tighter, ex=ex)
    assert ex.orders[n]["type"] == "sl" and ex.orders[n]["size"] == pos.size and old_sl.oid in ex.cancelled and pos.stop == tighter
    ex.stop_fails = True  # the exchange refuses the next stop: the current one stays
    with pytest.raises(execution.TradeError, match="previous stop-loss is unchanged"):
        execution.adjust_protection(db, m, pos.id, stop=risk.round_px((tighter + mark) / 2, 2), ex=ex)
    assert pos.stop == tighter


def test_terminal_shows_exchange_numbers_separates_other_positions_and_signal_status(db: Session, md: FakeMD,
                                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tradingos import terminal

    m, acc, pos, ex = _testnet_position(db, md, "861")
    waiting = active_signal(db, md, "ETH")  # ACTIVE, but this member isn't in it
    sl = db.scalar(select(TrOrder).where(TrOrder.position_id == pos.id, TrOrder.kind == "sl"))
    snap = {"state": {"marginSummary": {"accountValue": "1500.5", "totalMarginUsed": "80", "totalNtlPos": "400"}, "withdrawable": "1300",
                      "assetPositions": [{"position": {"coin": "SOL", "szi": str(pos.size), "entryPx": str(pos.entry_px),
                                                       "positionValue": str(pos.size * 130), "unrealizedPnl": "12.5", "marginUsed": "40",
                                                       "liquidationPx": "90", "leverage": {"value": 3}, "cumFunding": {"sinceOpen": "0.12"}}},
                                         {"position": {"coin": "BTC", "szi": "-0.01", "entryPx": "60000", "positionValue": "600",
                                                       "unrealizedPnl": "-3", "marginUsed": "60", "leverage": {"value": 10}}}]},
            "orders": [{"oid": sl.oid}]}
    terminal._snaps.clear()
    monkeypatch.setattr(terminal, "FETCH", lambda mode, master: snap)
    t = terminal.terminal(db, m)
    assert t["overview"]["equity"] == 1500.5 and t["overview"]["available"] == 1300 and not t["overview"]["simulated"]
    p = t["positions"][0]
    assert p["coin"] == "SOL" and p["upnl"] == 12.5 and p["margin"] == 40 and p["upnl_pct"] == pytest.approx(31.25) and p["liq_px"] == 90
    assert p["sl_status"] == "ACTIVE" and p["tp1_status"] == "NOT_ON_EXCHANGE" and any("TP1" in w for w in p["warnings"])
    assert [o["coin"] for o in t["other_positions"]] == ["BTC"]  # the member's own position: shown apart, never managed
    assert t["signals"][pos.signal_id]["status"] == "POSITION_OPEN" and waiting.id not in t["signals"]  # not entered = no status
    assert all(x["signal_id"] != waiting.id for x in t["positions"])  # an active signal is never shown as a position

    def down(mode: str, master: str) -> Any:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(terminal, "FETCH", down)
    monkeypatch.setattr(terminal, "SNAP_TTL", 0.0)
    t2 = terminal.terminal(db, m)
    assert t2["sync"]["stale"] and "couldn't be read" in t2["sync"]["error"] and t2["overview"]["equity"] == 1500.5  # last good, flagged
    assert t2["positions"][0]["sl_status"] == "UNVERIFIED"
    terminal._snaps.clear()
    assert terminal.terminal(db, m)["overview"]["equity"] is None  # never a made-up balance


def test_paper_terminal_is_labelled_simulated_and_history_keeps_the_size(db: Session, md: FakeMD) -> None:
    from app.tradingos import terminal

    sig = active_signal(db, md)
    m = kiwi(db, 4, "871")
    pos = execution.confirm(db, m, execution.preview(db, m, sig).token)
    t = terminal.terminal(db, m)
    assert t["overview"]["simulated"] and t["positions"][0]["sl_status"] == "SIMULATED" and t["positions"][0]["mark"]
    size = pos.size
    execution.close_position(db, m, pos.id)
    h = terminal.history(db, execution.account(db, m))
    assert h[0]["size"] == pytest.approx(size) and h[0]["mode"] == "PAPER" and h[0]["reason"] == "manual_close" and h[0]["funding"] is None
    assert terminal.terminal(db, m)["signals"][sig.id]["status"] == "POSITION_CLOSED"


def test_owner_can_reset_one_members_earn_progress_and_it_can_be_done_and_paid_again(db: Session, md: FakeMD) -> None:
    from app.members import xp as ledger
    from app.tradingbot import service

    m = kiwi(db, 4, "891")
    xp0 = m.xp_total
    for t, ref in (("earn.joined_hyperliquid", "earn:hyperliquid"), ("earn.first_trade", "earn:first_trade")):
        assert ledger.award(db, m, t, ref, source="earn").awarded
    execution.account(db, m)
    db.commit()
    assert service.completed(db, m) and m.xp_total > xp0
    out = service.reset_earn(db, m, "admin", "re-test")
    assert out["events"] == 2 and out["trading_account"] and not service.completed(db, m) and m.xp_total == xp0
    assert ledger.award(db, m, "earn.joined_hyperliquid", "earn:hyperliquid", source="earn").awarded  # the step pays again


def test_an_earn_step_completes_even_when_its_xp_is_refused(db: Session) -> None:
    from app.members import xp as ledger
    from app.tradingbot import service

    m = kiwi(db, 4, "895")
    service.joined_hyperliquid(db, m)  # makes sure the catalog (and the rule) exist
    service.reset_earn(db, m, "admin", "re-test")
    ledger.get_rule(db, "earn.joined_hyperliquid").enabled = False  # any refusal: rule off, cap, cooldown...
    db.commit()
    out = service.joined_hyperliquid(db, m)
    assert out["done"] and out["xp"] == 0 and service.state(db, m)["flow"]["hyperliquid"]  # no "Open Hyperliquid" loop


def test_terminal_api_close_all_needs_the_count_the_member_saw_and_reject_is_recorded(client: Any, db: Session, md: FakeMD) -> None:
    from app.members import service as members

    sig = active_signal(db, md)
    other = active_signal(db, md, "ETH")
    m = kiwi(db, 4, "881")
    pos = execution.confirm(db, m, execution.preview(db, m, sig).token)
    client.cookies.set("pc_session", members.new_session(db, m, 30))
    t = client.get("/api/members/trading/terminal").json()
    assert t["overview"]["mode"] == "PAPER" and len(t["positions"]) == 1 and t["account"]["mode"] == "PAPER"
    assert client.post("/api/members/trading/positions/close-all", json={"confirm_count": 2}).status_code == 409
    assert client.post(f"/api/members/trading/signals/{other.id}/reject").json() == {"ok": True}
    assert client.get("/api/members/trading/terminal").json()["signals"][str(other.id)]["status"] == "REJECTED"
    r = client.post(f"/api/members/trading/positions/{pos.id}/close", json={"fraction": 1.0}).json()
    assert r["close"]["status"] == "CLOSED" and r["close"]["filled"] > 0
    stranger = kiwi(db, 4, "882")
    client.cookies.set("pc_session", members.new_session(db, stranger, 30))
    assert client.post(f"/api/members/trading/positions/{pos.id}/close", json={"fraction": 1.0}).status_code == 400


def test_a_late_button_answer_never_loses_the_result_or_the_order(db: Session, md: FakeMD) -> None:
    """Telegram refuses answers that come too late ("query is too old"): the result then arrives privately, and a refused
    "Sending the order…" never stops the order."""
    from app.telegram.errors import TelegramRequestError

    class LateTG(FakeTG):
        async def answer_callback(self, cid: str, text: str, alert: bool = False) -> None:
            raise TelegramRequestError("Bad Request: query is too old and response timeout expired or query ID is invalid")

    sig = active_signal(db, md)
    kiwi(db, 4, "731")
    tg = LateTG()
    asyncio.run(bot.handle_callback(db, tg, {"id": "a", "data": f"sig:{sig.id}:enter", "from": {"id": 731}}))
    assert "Max planned loss" in tg.sent[-1][2]  # the private preview still arrives
    token = db.scalar(select(TrIntent)).token
    asyncio.run(bot.handle_callback(db, tg, {"id": "b", "data": f"cf:{token}", "from": {"id": 731}}))
    assert db.scalar(select(TrPosition)).status == "OPEN" and any("filled" in str(x) for x in tg.sent)
    asyncio.run(bot.handle_callback(db, tg, {"id": "c", "data": f"sig:{sig.id}:enter", "from": {"id": 731}}))
    assert tg.sent[-1][1] == 731 and "already have a bot position" in tg.sent[-1][2]  # a refusal reaches them privately


# ---------------------------------------------------------------- Connect Hyperliquid (website): eligibility → referral → bot trading
def _wallet(db: Session, m: Any, referral: str = "verified", code: str | None = "BANTER", deposit: str = "verified") -> TbWallet:
    w = TbWallet(member_id=m.id, address="0x" + "a" * 40, verified_at=utcnow(), checked_at=utcnow(), referral_status=referral,
                 referral_code_seen=code, deposit_status=deposit, deposit_usd=25.0 if deposit == "verified" else 0.0)
    db.add(w)
    db.commit()
    return w


def test_referral_states_come_from_hyperliquids_record_and_no_longer_gate_bot_trading(db: Session) -> None:
    from app.tradingbot import service as earn

    es = earn.settings(db)
    assert es.min_rank_level == 1 and es.min_deposit_usd == 0  # open to every member by default (0072)
    m = kiwi(db, 4, "901")
    assert accounts.connection(db, m)["referral"]["status"] == "PENDING" and "wallet" in accounts.connection(db, m)["blocker"]
    w = _wallet(db, m, referral="not_referred", code=None)
    c = accounts.connection(db, m)
    assert c["referral"]["status"] == "NOT_VERIFIED" and c["eligible"] and c["wallet"]["address"] == w.address  # shown, not required
    w.referral_code_seen = "SOMEONEELSE"  # attributed to another referrer: shown as such, still eligible
    db.commit()
    assert accounts.connection(db, m)["referral"]["status"] == "EXISTING_ACCOUNT" and accounts.connection(db, m)["eligible"]
    # the old Admin rank / minimum-deposit settings no longer gate bot trading (execution.eligibility: removed 2026-10-21, the
    # owner's call; only a verified wallet is required). Shown in the connection status, never a blocker.
    es.min_rank_level, es.min_deposit_usd = 4, 10.0
    db.commit()
    w.referral_status, w.referral_code_seen, w.deposit_status, w.deposit_usd, w.other_inflow_usd = "verified", "BANTER", "insufficient", 0.0, 9.99
    db.commit()
    c = accounts.connection(db, m)
    assert c["eligible"] and c["blocker"] is None and c["deposit"]["min_usd"] == 10.0 and c["deposit"]["status"] != "verified"
    w.other_inflow_usd = 12.0  # sent in from another account: real money in the member's own account
    db.commit()
    assert accounts.connection(db, m)["eligible"]
    w.deposit_status = "verified"
    db.commit()
    c = accounts.connection(db, m)
    assert c["eligible"] and c["referral"]["status"] == "VERIFIED" and c["authorization"]["status"] == "NONE" and not c["connected"]
    start = accounts.agent_start(db, m, "LIVE", "0x1")  # no allowlist needed any more: eligibility decides
    assert start["agent"] and "key" not in str(start).lower().replace("primarytype", "")
    lower = kiwi(db, 1, "902")
    _w2 = TbWallet(member_id=lower.id, address="0x" + "b" * 40, verified_at=utcnow(), checked_at=utcnow(), referral_status="verified",
                   referral_code_seen="BANTER", deposit_status="verified", deposit_usd=50)
    db.add(_w2)
    db.commit()
    c = accounts.connection(db, lower)
    assert c["eligible"] and c["blocker"] is None and not c["rank"]["ok"]  # under the old rank setting: shown, not a gate


def test_enable_bot_trading_needs_consent_and_real_checks_then_goes_live_with_auto_off(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    m = kiwi(db, 4, "911")
    w = _wallet(db, m)
    acc = execution.account(db, m)
    acc.master_address, acc.mainnet_agent, acc.mainnet_agent_key, acc.mainnet_approved_at = w.address, "0x" + "c" * 40, "enc-key", utcnow()
    acc.auto_enabled = True
    db.commit()
    rec = {"name": "PriceChat", "address": acc.mainnet_agent, "validUntil": None}
    monkeypatch.setattr(xch, "agent_record", lambda *a, **k: rec)
    execution._agent_seen.clear()
    with pytest.raises(accounts.AccountError, match="understand"):
        accounts.activate(db, m, False)
    assert execution.account(db, m).mode != "LIVE" and not accounts.connection(db, m)["connected"]  # never "connected" early
    c = accounts.activate(db, m, True, FakeExchange(equity=0))  # an empty account connects too: it shows $0 and can't trade
    items = execution.account(db, m).live_checks["items"]
    assert not items["balance"]["ok"] and "trading balance" in items["balance"]["detail"]  # shown to the member, not a blocker
    acc = execution.account(db, m)
    assert acc.mode == "LIVE" and acc.live_enabled_at and not acc.auto_enabled  # AUTO stays off until turned on on purpose
    assert c["connected"] and c["authorization"]["status"] == "ACTIVE" and c["referral"]["status"] == "VERIFIED"
    assert not db.scalars(select(TrOrder)).all()  # connecting never places an order


def test_a_revoked_permission_or_a_changed_wallet_blocks_new_live_entries(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    m = kiwi(db, 4, "921")
    w = _wallet(db, m)
    acc = execution.account(db, m)
    acc.master_address, acc.mainnet_agent, acc.mainnet_agent_key, acc.mainnet_approved_at = w.address, "0x" + "d" * 40, "enc-key", utcnow()
    db.commit()
    execution._agent_seen.clear()
    monkeypatch.setattr(xch, "agent_record", lambda *a, **k: None)  # removed on Hyperliquid (or expired)
    with pytest.raises(execution.TradeError, match="revoked or expired"):
        execution.check_live_permission(db, m, acc)
    assert accounts.connection(db, m)["authorization"]["status"] == "REVOKED" and not accounts.connection(db, m)["connected"]

    def down(*a: Any, **k: Any) -> None:
        raise httpx.ConnectError("down")

    execution._agent_seen.clear()
    monkeypatch.setattr(xch, "agent_record", down)
    with pytest.raises(execution.TradeError, match="Couldn't confirm"):  # signer status unknown: no entry, no guess
        execution.check_live_permission(db, m, acc)
    w.address = "0x" + "e" * 40  # a different verified wallet now: the old authorization doesn't carry over
    db.commit()
    with pytest.raises(execution.TradeError, match="wallet changed"):
        execution.check_live_permission(db, m, acc)


class _HL:
    """TEST FIXTURE: Hyperliquid's public info API (referral + deposits) for one address."""

    def __init__(self, code: str | None, deposit: float) -> None:
        self.code, self.dep = code, deposit

    def referral(self, a: str) -> dict[str, Any]:
        return {"referredBy": {"referrer": "0xabc", "code": self.code} if self.code else None}

    def ledger(self, a: str) -> list[dict[str, Any]]:
        return [{"time": 1, "hash": "0x1", "delta": {"type": "deposit", "usdc": str(self.dep)}}] if self.dep else []

    def portfolio(self, a: str) -> dict[str, Any]:
        return {}


def test_connect_with_an_api_key_proves_the_account_through_hyperliquid_and_never_keeps_a_main_key(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import eth_account

    from app.tradingbot import service as earn

    earn.reset_limits_for_tests()
    m = kiwi(db, 4, "931")
    owner, api = eth_account.Account.create(), eth_account.Account.create()
    master = owner.address.lower()
    listed: dict[str, Any] = {}
    monkeypatch.setattr(xch, "agent_record", lambda mode, ms, ag, *a, **k: listed.get(ag.lower()) if ms.lower() == master else None)
    # Hyperliquid's userRole: the owner's address is an account; the API wallet is an agent of it only once authorized
    monkeypatch.setattr(xch, "user_role", lambda mode, ad, *a, **k: {"role": "user"} if ad.lower() == master
                        else {"role": "agent", "data": {"user": master}} if ad.lower() in listed else {"role": "missing"})
    execution._agent_seen.clear()
    with pytest.raises(accounts.AccountError, match="main wallet key"):  # the owner's own key: refused, never stored
        accounts.connect_api_key(db, m, None, owner.key.hex(), True, _HL("BANTER", 50))
    with pytest.raises(accounts.AccountError, match="doesn't know this API key"):  # not authorized on Hyperliquid: no proof
        accounts.connect_api_key(db, m, None, api.key.hex(), True, _HL("BANTER", 50))
    acc = execution.account(db, m)
    assert acc.mainnet_agent_key is None and earn.wallet_of(db, m) is None
    listed[api.address.lower()] = {"name": "PriceChat", "address": api.address, "validUntil": None}
    with pytest.raises(accounts.AccountError, match="not the address you entered"):
        accounts.connect_api_key(db, m, "0x" + "f" * 40, api.key.hex(), True, _HL("BANTER", 50))
    # the address field is optional; pasting the API wallet's own address (shown next to the key on Hyperliquid) is fine too
    c = accounts.connect_api_key(db, m, api.address, api.key.hex(), True, _HL("BANTER", 50), FakeExchange(equity=200))
    acc = execution.account(db, m)
    assert earn.wallet_of(db, m).address == master and acc.master_address == master  # account queries use the master address
    assert acc.mainnet_agent == api.address and api.key.hex().removeprefix("0x") not in (acc.mainnet_agent_key or "")  # stored encrypted
    assert xch.dec(acc.mainnet_agent_key).removeprefix("0x") == api.key.hex().removeprefix("0x")
    assert c["connected"] and acc.mode == "LIVE" and not acc.auto_enabled and "secret" not in str(c).lower()
    assert c["balances"]["spot_usdc"] == 9.99 and c["wallet"]["address"] == master  # the account's money, not the key's
    assert not db.scalars(select(TrOrder)).all()  # connecting never places an order
    other = kiwi(db, 4, "932")
    with pytest.raises(accounts.AccountError, match="another Kiwi Passport"):
        accounts.connect_api_key(db, other, master, api.key.hex(), True, _HL("BANTER", 50))


def test_api_key_connection_without_the_referral_goes_live(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import eth_account

    from app.tradingbot import service as earn

    earn.reset_limits_for_tests()
    m = kiwi(db, 4, "941")
    owner, api = eth_account.Account.create(), eth_account.Account.create()
    monkeypatch.setattr(xch, "agent_record", lambda *a, **k: {"name": "PriceChat", "address": api.address, "validUntil": None})
    monkeypatch.setattr(xch, "user_role", lambda *a, **k: {"role": "agent", "data": {"user": owner.address.lower()}})
    execution._agent_seen.clear()
    c = accounts.connect_api_key(db, m, None, api.key.hex(), True, _HL("SOMEONEELSE", 50))
    assert c["referral"]["status"] == "EXISTING_ACCOUNT" and c["authorization"]["status"] == "ACTIVE" and c["connected"]
    assert execution.account(db, m).mode == "LIVE"  # the referral isn't required any more (0072)


def test_an_empty_live_account_is_connected_but_every_entry_is_refused(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "951")
    acc = execution.account(db, m)
    acc.mode, acc.master_address, acc.live_enabled_at = "LIVE", "0x" + "1" * 40, utcnow()
    acc.mainnet_agent, acc.mainnet_agent_key, acc.mainnet_approved_at = "0x" + "2" * 40, "enc-key", utcnow()
    signals.settings(db).live_allowlist = m.username
    db.commit()
    with pytest.raises(execution.TradeError):
        execution.preview(db, m, sig, FakeExchange(equity=0, mark=md.series["SOL"][-1].c))
    assert not db.scalars(select(TrIntent)).all() and not db.scalars(select(TrOrder)).all()


def test_a_unified_account_trades_from_its_usdc_even_when_the_perps_value_reads_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hyperliquid "unifiedAccount": Spot USDC is the collateral and clearinghouseState's account value can be $0."""
    perp = {"marginSummary": {"accountValue": "0.0"}}
    monkeypatch.setattr(xch, "spot_usdc", lambda *a, **k: (11.5, 0.0))
    monkeypatch.setattr(xch, "is_unified", lambda *a, **k: True)
    assert xch.trading_equity("LIVE", "0x" + "1" * 40, perp) == 11.5
    monkeypatch.setattr(xch, "is_unified", lambda *a, **k: False)  # a classic account: only the Perps value is usable
    assert xch.trading_equity("LIVE", "0x" + "1" * 40, perp) == 0.0
    both = {"marginSummary": {"accountValue": "11.5"}}  # never counted twice
    monkeypatch.setattr(xch, "is_unified", lambda *a, **k: True)
    assert xch.trading_equity("LIVE", "0x" + "1" * 40, both) == 11.5


def test_default_limits_raise_a_tiny_order_to_the_exchange_minimum_but_never_past_the_max_loss_or_balance() -> None:
    kw = dict(coin="BTC", side="SHORT", mark=82265.0, entry_zone=(82170.0, 82360.0), stop=82980.0, target=80835.0, sig_lev_hi=5,
              market_max_lev=40, sz_decimals=5, equity=9.995, max_leverage=3, max_loss_usd=5.0, max_allocation_pct=10.0, max_positions=2,
              open_positions=0, daily_pnl=0.0, daily_loss_limit=15.0, allowed=[], paused=False, global_paused=False)
    small = risk.plan_trade(**kw)
    assert not small.ok and "under Hyperliquid's $10 minimum" in small.reasons[0]  # the user's own sizing: never bumped
    p = risk.plan_trade(**kw, min_bump=True)  # default limits: the bot sizes up to the minimum
    assert p.ok and 10 <= p.notional <= 11 and p.margin <= 9.995 * 0.9 and p.max_loss_usd <= 5 and "Raised to Hyperliquid's $10 minimum" in p.note
    tight = risk.plan_trade(**{**kw, "max_loss_usd": 0.05}, min_bump=True)  # the max loss still wins
    assert not tight.ok and "wouldn't fit" in tight.reasons[0]
    broke = risk.plan_trade(**{**kw, "equity": 2.0}, min_bump=True)  # and so does the balance
    assert not broke.ok


def test_connecting_defaults_to_paper_refuses_a_shared_key_and_never_switches_accounts_silently(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import eth_account

    from app.tradingbot import service as earn

    earn.reset_limits_for_tests()
    m = kiwi(db, 4, "961")
    owner, api = eth_account.Account.create(), eth_account.Account.create()
    master = owner.address.lower()
    monkeypatch.setattr(xch, "agent_record", lambda *a, **k: {"name": "chat", "address": api.address, "validUntil": 1807677623186})
    monkeypatch.setattr(xch, "user_role", lambda *a, **k: {"role": "agent", "data": {"user": master}})
    execution._agent_seen.clear()
    db.add(TbWallet(member_id=m.id, address="0x" + "9" * 40, verified_at=utcnow()))  # the Passport already has another account
    db.commit()
    with pytest.raises(accounts.AccountError, match="Switch my Passport"):
        accounts.connect_api_key(db, m, None, api.key.hex(), False, _HL("BANTER", 50))
    c = accounts.connect_api_key(db, m, None, api.key.hex(), False, _HL("BANTER", 50), replace=True)
    acc = execution.account(db, m)
    assert c["authorization"]["status"] == "ACTIVE" and c["authorization"]["valid_until"] == 1807677623186  # expiry shown
    assert acc.mode == "PAPER" and not acc.live_enabled_at and not c["connected"]  # paper until live is enabled explicitly
    assert earn.trading_choice(db, m)["api"] and not earn.trading_choice(db, m)["live"]
    other = kiwi(db, 4, "962")
    with pytest.raises(accounts.AccountError, match="already connected to another Kiwi Passport"):
        accounts.connect_api_key(db, other, None, api.key.hex(), False, _HL("BANTER", 50))
    assert not any("withdraw or transfer your funds" in p for p in c["permissions"])  # no unverified "can't withdraw" claim


def test_pressing_start_completes_the_telegram_step_and_paper_is_a_recorded_choice(db: Session) -> None:
    from app.tradingbot import service as earn

    m = kiwi(db, 4, "971")
    assert not earn._paid(db, m, "earn.bot_connected")
    asyncio.run(bot.handle_command(db, FakeTG(), 971, 971, "/start"))  # the private /start of the linked Telegram account
    assert earn._paid(db, m, "earn.bot_connected")  # no "Did you press Start?" confirm needed
    assert not earn.trading_choice(db, m)["paper"]
    signals.audit(db, f"m:{m.id}", "paper_chosen", None, via="website")
    db.commit()
    assert earn.trading_choice(db, m)["paper"]


def test_a_fresh_signal_accepts_a_market_entry_a_little_outside_its_zone() -> None:
    kw = dict(coin="ETH", side="SHORT", mark=2449.0, entry_zone=(2438.0, 2446.0), stop=2471.0, target=2384.0, sig_lev_hi=5, market_max_lev=25,
              sz_decimals=4, equity=500.0, max_leverage=3, max_loss_usd=5.0, max_allocation_pct=10.0, max_positions=2, open_positions=0,
              daily_pnl=0.0, daily_loss_limit=15.0, allowed=[], paused=False, global_paused=False)
    assert "outside the entry zone" in risk.plan_trade(**kw).reasons[0]  # 3 above the zone: refused normally
    p = risk.plan_trade(**kw, zone_tolerance=1.0)  # the first minutes after publication
    assert p.ok and p.entry == 2449.0 and p.stop == 2471.0 and p.max_loss_usd <= 5  # same stop; size from the real distance
