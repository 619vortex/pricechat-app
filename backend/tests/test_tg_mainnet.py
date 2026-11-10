"""Telegram trading, mainnet readiness (feat/tg-mainnet): the exchange-level backstop that keeps every signed mainnet action
out of Telegram while the owner's switch is off, read-only mainnet previews and readiness, the emergency stop, single-use
confirmations, limit prices, per-user rate limits, keys kept out of logs, and the Admin switches and monitoring.
No network: fake markets, fake exchanges and fake mainnet readers (tests/test_tg_trading.py's FakeReader)."""

import asyncio
import io
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.trading import TrIntent, TrPosition
from app.models.tradingbot import TbWallet
from app.models.tradingos import TsAudit, TsSignal
from app.security import redact
from app.tradingos import bot, execution, signals, tgtrade
from app.tradingos import exchange as xch

from tests.test_tg_trading import TG, FakeReader, banter, last_token, md, press, say  # noqa: F401 - md is a fixture
from tests.test_trading_execution import FakeExchange, active_signal, on_testnet
from tests.test_trading_os import FakeMD

KEY = "0x" + "ab12" * 16  # a throwaway, made-up 32-byte value: never a real key


@pytest.fixture(autouse=True)
def _no_hyperliquid(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: as in test_tg_trading: no call ever reaches api.hyperliquid.xyz."""
    monkeypatch.setattr(tgtrade, "recheck_referral", lambda db, m: None)
    FakeReader.built = []
    monkeypatch.setattr(xch, "ReadOnlyExecutor", FakeReader)
    monkeypatch.setattr(xch, "user_fees", lambda mode, a, transport=None: {"taker": 0.00045, "maker": 0.00015, "referral_discount": 0.04})
    monkeypatch.setattr(xch, "agent_record", lambda mode, master, agent, transport=None: {"address": agent, "validUntil": None})
    monkeypatch.setattr(xch, "balances", lambda mode, a, transport=None: {"perps": 1000.0, "spot_usdc": 0.0, "unified": False, "trading": 1000.0})


def live_member(db: Session, tg: str) -> Any:
    m = banter(db, tg)
    acc = execution.account(db, m)
    acc.mode, acc.master_address = "LIVE", db.scalar(select(TbWallet.address).where(TbWallet.member_id == m.id))
    acc.live_enabled_at = utcnow()
    acc.mainnet_agent, acc.mainnet_agent_key, acc.mainnet_approved_at = "0x" + "4" * 40, xch.enc(KEY), utcnow()
    db.commit()
    return m


def switches(db: Session, **cfg: Any) -> None:
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "telegram_trading": cfg}
    db.commit()


# ---------------------------------------------------------------- the backstop in exchange.py
class Boom:
    """TEST FIXTURE: an SDK Exchange that fails the test if anything is ever signed through it."""

    def __getattr__(self, name: str) -> Any:
        pytest.fail(f"the SDK was asked to {name}")


def real_executor(mode: str) -> xch.HyperliquidExecutor:
    ex = object.__new__(xch.HyperliquidExecutor)  # no SDK, no network: only the guard is under test
    ex.mode, ex.master, ex.ex = mode, "0x" + "5" * 40, Boom()
    return ex


def test_no_signed_mainnet_action_can_leave_a_telegram_request_while_mainnet_is_off() -> None:
    live = real_executor("LIVE")
    with xch.surface("telegram", mainnet_allowed=False):
        for call in (lambda: live.set_leverage("BTC", 3), lambda: live.market("BTC", True, 1.0, 100.0, "0x" + "0" * 32),
                     lambda: live.trigger("BTC", False, 1.0, 90.0, "sl", "0x" + "0" * 32), lambda: live.cancel("BTC", 5),
                     lambda: live.cancel_by_cloid("BTC", "0x" + "0" * 32)):
            with pytest.raises(xch.MainnetBlocked):
                call()

        async def in_a_worker_thread() -> None:  # asyncio.to_thread carries the rule into the worker
            with pytest.raises(xch.MainnetBlocked):
                await asyncio.to_thread(live.market, "BTC", True, 1.0, 100.0, "0x" + "0" * 32)

        asyncio.run(in_a_worker_thread())
        assert not xch.mainnet_blocked("TESTNET") and not xch.mainnet_blocked("PAPER")  # test funds are untouched
    assert not xch.mainnet_blocked("LIVE")  # outside Telegram (website, AUTO, reconciliation): unchanged
    with xch.surface("telegram", mainnet_allowed=True):
        assert not xch.mainnet_blocked("LIVE")


def test_inside_telegram_the_executor_for_mainnet_is_a_keyless_reader(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    m = live_member(db, "2001")
    acc = execution.account(db, m)
    decrypted: list[str] = []
    real_dec = xch.dec
    monkeypatch.setattr(xch, "dec", lambda blob: decrypted.append(blob) or real_dec(blob))
    monkeypatch.setattr(xch, "HyperliquidExecutor", lambda *a, **k: "signing executor")
    with tgtrade.telegram_surface(db):
        ex = execution.executor(db, acc, "BTC")
        assert isinstance(ex, FakeReader) and not decrypted  # the API wallet key is never even decrypted
        with pytest.raises(xch.MainnetBlocked):
            ex.market("BTC", True, 1.0, 100.0, "0x" + "0" * 32)
    assert execution.executor(db, acc, "BTC") == "signing executor"  # the website's path is unchanged
    switches(db, live=True)
    with tgtrade.telegram_surface(db):
        assert execution.executor(db, acc, "BTC") == "signing executor"
    switches(db, live=True, kill=True)  # the emergency stop wins over the live switch
    with tgtrade.telegram_surface(db):
        assert isinstance(execution.executor(db, acc, "BTC"), FakeReader)


def test_the_live_switch_reads_only_a_real_true(db: Session) -> None:
    for v in ("true", 1, "yes", None):
        switches(db, live=v)
        assert not tgtrade.telegram_live(db)
    switches(db, live=True)
    assert tgtrade.telegram_live(db) and tgtrade.mainnet_allowed(db)


# ---------------------------------------------------------------- mainnet previews and readiness
def test_preview_sizes_a_mainnet_order_read_only_and_leaves_nothing_to_confirm(db: Session, md: FakeMD) -> None:
    m = live_member(db, "2002")
    execution.account(db, m).mode = "PAPER"  # /preview works from any mode: it always reads mainnet
    db.commit()
    tg = TG()
    out = say(db, tg, 2002, "/preview BTC long 50 3x")
    assert "MAINNET PREVIEW" in out and "nothing can be sent" in out and "balance $1,000.00" in out
    assert "taker rate 0.0450%" in out and "referral discount 4%" in out and "Every check passes" in out
    assert tg.buttons[-1] is None  # no Confirm
    assert not db.scalars(select(TrIntent)).all() and not db.scalars(select(TsSignal).where(TsSignal.status == "MANUAL")).all()
    row = db.scalar(select(TsAudit).where(TsAudit.action == "tg_mainnet_preview"))
    assert row is not None and row.details["via"] == "telegram" and row.details["refused"] == []
    # what would stop it is listed, not hidden
    out = say(db, tg, 2002, "/preview BTC long 900 3x")
    assert "Would be refused" in out and "margin is over your limit" in out
    assert "/preview BTC long 100 5x" in say(db, tg, 2002, "/preview BTC")  # usage, named after the command


def test_preview_lists_every_missing_mainnet_step(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    m = banter(db, "2003", code="OTHER", status="not_referred")
    acc = execution.account(db, m)
    acc.master_address = db.scalar(select(TbWallet.address).where(TbWallet.member_id == m.id))
    db.commit()
    out = say(db, TG(), 2003, "/preview SOL short 50 2x")
    assert "LIVE isn't enabled" in out and "no mainnet API wallet" in out and "Price Chat's Hyperliquid referral isn't verified" in out


def test_mainnet_readiness_reads_everything_and_signs_nothing(db: Session, md: FakeMD) -> None:
    live_member(db, "2004")
    out = say(db, TG(), 2004, "/mainnet")
    for line in ("Hyperliquid mainnet reachable", "trading balance $1,000.00", "API wallet approved and listed", "LIVE enabled",
                 "Price Chat referral verified", "taker 0.0450%", "Telegram mainnet execution: OFF"):
        assert line in out, line
    assert set(FakeReader.built) <= {"LIVE"}


def test_live_account_open_and_enter_become_previews_and_the_live_switch_restores_confirm(db: Session, md: FakeMD,
                                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    sig = active_signal(db, md)
    live_member(db, "2005")
    monkeypatch.setattr(xch, "HyperliquidExecutor", lambda *a, **k: pytest.fail("a real Hyperliquid executor was built"))
    tg = TG()
    assert "MAINNET PREVIEW" in say(db, tg, 2005, "/open SOL long 50 2x") and tg.buttons[-1] is None
    assert "MAINNET PREVIEW" in say(db, tg, 2005, f"/enter {sig.number}") and tg.buttons[-1] is None
    assert not db.scalars(select(TrIntent)).all()
    switches(db, live=True)  # the owner's switch: a normal preview with Confirm (no order is placed in this test)
    ex = FakeExchange(mark=md.market("SOL").mark)
    monkeypatch.setattr(execution, "executor", lambda *a, **k: ex)
    say(db, tg, 2005, "/open SOL long 50 2x")
    assert tg.buttons[-1] and tg.buttons[-1][0][0]["callback_data"].startswith("mo:") and "REAL MONEY" in tg.sent[-1][2]
    assert not ex.orders


# ---------------------------------------------------------------- the emergency stop
def test_emergency_stop_blocks_new_telegram_trades_but_never_closing(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    m = banter(db, "2006")
    tg = TG()
    say(db, tg, 2006, "/open BTC long 50 3x")
    press(db, tg, 2006, f"mo:{last_token(db)}")
    pos = db.scalar(select(TrPosition))
    assert pos.status == "OPEN" and pos.mode == "PAPER"
    say(db, tg, 2006, "/open ETH long 50 3x")  # a preview waiting for Confirm when the stop goes on
    waiting = last_token(db)
    out = tgtrade.set_switches(db, "admin:test", kill=True)
    assert out["switches"]["kill"] and not out["switches"]["mainnet_execution"]
    assert db.scalar(select(TrIntent).where(TrIntent.token == waiting)).status == "CANCELLED"
    assert "emergency stop" in say(db, tg, 2006, "/open SOL long 50 3x")
    assert "emergency stop" in say(db, tg, 2006, f"/enter {sig.number}")
    assert "emergency stop" in press(db, tg, 2006, f"sig:{sig.id}:enter")
    assert "emergency stop" in press(db, tg, 2006, "cf:" + "9" * 16)
    assert "Already handled" in press(db, tg, 2006, f"mo:{waiting}")
    assert len(db.scalars(select(TrPosition)).all()) == 1
    assert "MAINNET PREVIEW" in say(db, tg, 2006, "/preview BTC long 50 3x")  # reading stays available
    assert "Closed #" in say(db, tg, 2006, f"/close {pos.id}")  # getting out always works
    db.refresh(pos)
    assert pos.status == "CLOSED"
    tgtrade.set_switches(db, "admin:test", kill=False)
    assert "PAPER" in say(db, tg, 2006, "/open SOL long 50 3x")
    assert m is not None


def test_the_live_switch_needs_the_phrase_and_no_emergency_stop(db: Session) -> None:
    with pytest.raises(tgtrade.SwitchError, match="Type"):
        tgtrade.set_switches(db, "admin:test", live=True, phrase="yes")
    tgtrade.set_switches(db, "admin:test", kill=True)
    with pytest.raises(tgtrade.SwitchError, match="emergency stop off"):
        tgtrade.set_switches(db, "admin:test", live=True, phrase=tgtrade.LIVE_PHRASE)
    tgtrade.set_switches(db, "admin:test", kill=False)
    out = tgtrade.set_switches(db, "admin:test", live=True, phrase=tgtrade.LIVE_PHRASE.lower())
    assert out["switches"]["live"] and out["switches"]["mainnet_execution"]
    assert tgtrade.set_switches(db, "admin:test", live=False)["switches"]["live"] is False
    acts = [a.details for a in db.scalars(select(TsAudit).where(TsAudit.action == "telegram_trading_switch"))]
    assert {"kill": True} in acts and {"live": True} in acts and {"live": False} in acts


def test_admin_switches_and_monitoring_over_the_api(client: TestClient, db: Session, md: FakeMD) -> None:
    banter(db, "2007")
    tg = TG()
    say(db, tg, 2007, "/open BTC long 50 3x")
    press(db, tg, 2007, f"mo:{last_token(db)}")
    st = client.get("/api/tradingos/admin/telegram-trading").json()
    assert st["switches"] == {"live": False, "kill": False, "killed_at": None, "gate_signal_enter": True, "global_paused": False,
                              "mainnet_execution": False, "live_phrase": tgtrade.LIVE_PHRASE}
    assert st["referral"]["codes"][0] == "BANTER" and st["referral"]["url"].startswith("https://app.hyperliquid.xyz/join/")
    assert st["activity"]["manual_preview"] == 1 and st["activity"]["manual_open"] == 1 and st["open_positions"] == {"PAPER": 1}
    assert client.put("/api/tradingos/admin/telegram-trading", json={"live": True, "phrase": "no"}).status_code == 400
    assert client.put("/api/tradingos/admin/telegram-trading", json={"kill": True}).json()["switches"]["kill"] is True
    assert client.put("/api/tradingos/admin/telegram-trading", json={"live": True, "phrase": tgtrade.LIVE_PHRASE}).status_code == 400
    assert client.put("/api/tradingos/admin/telegram-trading", json={"bogus": 1}).status_code == 422


# ---------------------------------------------------------------- duplicates and limit prices
def test_a_confirmation_is_used_once_even_when_pressed_twice(db: Session, md: FakeMD) -> None:
    banter(db, "2008")
    tg = TG()
    say(db, tg, 2008, "/open BTC long 50 3x")
    token = last_token(db)
    assert "filled" in press(db, tg, 2008, f"mo:{token}")
    assert "Already handled" in press(db, tg, 2008, f"mo:{token}")
    assert len(db.scalars(select(TrPosition)).all()) == 1
    # two presses racing: the second holds a stale PENDING row; the conditional update still lets only one through
    say(db, tg, 2008, "/open ETH long 50 3x")
    it = db.scalar(select(TrIntent).where(TrIntent.token == last_token(db)))
    execution.claim_intent(db, it)
    it.status = "PENDING"  # what a second session that read the row before the first commit still sees
    with pytest.raises(execution.TradeError, match="Already handled"):
        execution.claim_intent(db, it)


def test_limit_is_the_worst_accepted_price_filled_now_or_not_at_all(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    r = tgtrade.parse_open(["BTC", "LONG", "50", "3X", "LIMIT", "125.5"])
    assert isinstance(r, tgtrade.OpenRequest) and r.limit == 125.5
    mark = md.market("BTC").mark
    banter(db, "2009")
    tg = TG()
    assert "wouldn't fill now" in say(db, tg, 2009, f"/open BTC long 50 3x limit {mark * 0.99:.4f}")
    assert "more than 5%" in say(db, tg, 2009, f"/open BTC long 50 3x limit {mark * 1.2:.4f}")
    lim = round(mark * 1.002, 4)
    out = say(db, tg, 2009, f"/open BTC long 50 3x limit {lim}")
    assert "or better (limit" in out
    assert "filled" in press(db, tg, 2009, f"mo:{last_token(db)}")
    pos = db.scalar(select(TrPosition))
    assert pos.entry_px <= lim  # paper: never worse than the limit
    # testnet: the IOC entry goes to the exchange with the member's limit as its price
    m2 = banter(db, "2010")
    on_testnet(db, m2)
    ex = FakeExchange(mark=mark)
    monkeypatch.setattr(execution, "executor", lambda *a, **k: ex)
    say(db, tg, 2010, f"/open BTC long 50 3x limit {lim}")
    press(db, tg, 2010, f"mo:{last_token(db)}")
    entry = next(o for o in ex.orders if o["type"] == "market")
    assert entry["px"] == pytest.approx(lim, rel=1e-4) and entry["reduce"] is False


# ---------------------------------------------------------------- rate limits
def test_trading_commands_and_buttons_are_rate_limited_per_user(db: Session, md: FakeMD) -> None:
    banter(db, "2011")
    tg = TG()
    for _ in range(6):
        asyncio.run(bot.handle_command(db, tg, 2011, 2011, "/orders", throttle=True))
    asyncio.run(bot.handle_command(db, tg, 2011, 2011, "/open BTC long 50 3x", throttle=True))
    assert tg.sent[-1][2] == bot.TRADE_LIMIT_TEXT and not db.scalars(select(TrIntent)).all()
    asyncio.run(bot.handle_command(db, tg, 2012, 2012, "/orders", throttle=True))  # someone else isn't affected
    assert tg.sent[-1][2] != bot.TRADE_LIMIT_TEXT
    cq = {"id": "x", "data": "cf:" + "1" * 16, "from": {"id": 2011}}
    hits = [asyncio.run(bot.trade_button_throttled(tg, cq)) for _ in range(8)]
    assert hits[:6] == [False] * 6 and hits[-1] is True
    assert asyncio.run(bot.trade_button_throttled(tg, {"id": "y", "data": "fc:1h:btc", "from": {"id": 2011}})) is False


# ---------------------------------------------------------------- keys never in logs, chats or the database in the clear
def _capture() -> tuple[logging.Logger, io.StringIO]:
    buf = io.StringIO()
    lg = logging.getLogger("test.redact")
    lg.handlers, lg.propagate = [logging.StreamHandler(buf)], False
    redact.install(lg)
    return lg, buf


def test_private_keys_and_recovery_phrases_never_reach_a_log_line() -> None:
    lg, buf = _capture()
    phrase = "abandon ability able about above absent absorb abstract absurd abuse access accident"
    lg.warning("member pasted %s in a chat", KEY)
    lg.warning("bare %s", KEY[2:])
    lg.warning("words: %s", phrase)
    try:
        raise ValueError(f"bad key {KEY}")
    except ValueError:
        lg.exception("failed")
    lg.warning("bot token 123456789:%s", "A" * 35)
    text = buf.getvalue()
    assert KEY[2:] not in text and "abandon ability" not in text and "A" * 35 not in text
    assert text.count(redact.REDACTED_KEY) >= 3 and redact.REDACTED_PHRASE in text and "Traceback" in text
    assert redact.redact_secrets("BTC 64000 long 5x") == "BTC 64000 long 5x"  # ordinary lines are untouched


def test_root_logging_carries_the_redactor() -> None:
    from app.config.logging import configure_logging

    configure_logging("INFO")
    assert all(any(isinstance(f, redact.SecretRedactor) for f in h.filters) for h in logging.getLogger().handlers)


def test_agent_keys_are_stored_sealed_and_a_pasted_key_is_never_answered(db: Session, md: FakeMD, caplog: pytest.LogCaptureFixture) -> None:
    m = live_member(db, "2013")
    sealed = execution.account(db, m).mainnet_agent_key
    assert KEY not in sealed and KEY[2:] not in sealed and KEY[2:].upper() not in sealed and xch.dec(sealed) == KEY
    caplog.set_level(logging.DEBUG)
    tg = TG()
    say(db, tg, 2013, "/mainnet")
    say(db, tg, 2013, "/preview BTC long 50 3x")
    assert KEY[2:] not in caplog.text and all(KEY[2:] not in x[2] for x in tg.sent)
    for row in db.scalars(select(TsAudit)):
        assert KEY[2:] not in str(row.details)
    assert tgtrade.looks_secret(f"here {KEY}") and tgtrade.looks_secret(KEY[2:])
