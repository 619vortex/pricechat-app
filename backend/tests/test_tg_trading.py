"""Telegram trading on Hyperliquid (app/tradingos/tgtrade.py): the Banter-only gate, private chats only, preview then
Confirm, the member's own sizing held to their limits, liquidation estimates, stops and targets, failed orders, duplicates,
mainnet kept off, and keys never accepted in a chat. No network: the fake market and fake exchange from the trading tests."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.trading import TrIntent, TrOrder, TrPosition
from app.models.tradingbot import TbWallet
from app.models.tradingos import TsSignal
from app.tradingos import bot, execution, market, risk, signals, tgtrade

from tests.test_trading_execution import FakeExchange, active_signal, on_testnet
from tests.test_trading_os import FakeMD, FakeTG, kiwi, uptrend_pullback


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> FakeMD:
    f = FakeMD({"BTC": uptrend_pullback(), "SOL": uptrend_pullback(), "ETH": uptrend_pullback()})
    monkeypatch.setattr(market, "_shared", f)
    return f


@pytest.fixture(autouse=True)
def _no_hyperliquid(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: the referral recheck would ask Hyperliquid; tests never call the real API."""
    monkeypatch.setattr(tgtrade, "recheck_referral", lambda db, m: None)


class TG(FakeTG):
    """TEST FIXTURE: also keeps the buttons of each message."""

    def __init__(self) -> None:
        super().__init__()
        self.buttons: list[Any] = []

    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        self.buttons.append(buttons)
        return await super().send_message(chat_id, text, buttons, html, reply_to)


def banter(db: Session, tg: str, code: str | None = "BANTER", status: str = "verified") -> Any:
    m = kiwi(db, 4, tg)
    db.add(TbWallet(member_id=m.id, address="0x" + tg.rjust(40, "a")[-40:], verified_at=utcnow(), referral_status=status, referral_code_seen=code))
    db.commit()
    return m


def say(db: Session, tg: TG, uid: int, text: str, group: bool = False) -> str:
    asyncio.run(bot.handle_command(db, tg, -100 if group else uid, uid, text, group=group))
    return tg.sent[-1][2]


def press(db: Session, tg: TG, uid: int, data: str) -> str:
    asyncio.run(bot.handle_callback(db, tg, {"id": "x", "data": data, "from": {"id": uid}}))
    return tg.sent[-1][2]


def last_token(db: Session) -> str:
    return db.scalars(select(TrIntent).order_by(TrIntent.id.desc())).first().token


# ---------------------------------------------------------------- parsing
def test_open_reads_coin_side_margin_leverage_and_optional_stop_and_target() -> None:
    r = tgtrade.parse_open(["BTC", "LONG", "100", "5X"])
    assert isinstance(r, tgtrade.OpenRequest) and (r.coin, r.side, r.margin, r.leverage, r.stop, r.tp) == ("BTC", "LONG", 100.0, 5, None, None)
    r = tgtrade.parse_open(["$sol", "short", "$50", "3", "sl", "130.5", "tp", "110"])
    assert (r.coin, r.side, r.margin, r.leverage, r.stop, r.tp) == ("SOL", "SHORT", 50.0, 3, 130.5, 110.0)
    for bad in [[], ["BTC", "long", "100"], ["BTC", "up", "100", "5x"], ["BTC", "long", "-5", "5x"], ["BTC", "long", "100", "2.5x"],
                ["BTC", "long", "100", "5x", "sl"], ["BTC", "long", "100", "5x", "moon", "1"], ["B/TC", "long", "1", "1x"]]:
        assert isinstance(tgtrade.parse_open(bad), str), bad


def test_signal_numbers_come_from_the_command_or_the_card_replied_to() -> None:
    assert tgtrade.signal_from(["#012"]) == 12 and tgtrade.signal_from(["7"]) == 7
    assert tgtrade.signal_from([], "🟢 $SOL LONG · #012\n\n🎯 Entry 99 – 101") == 12
    assert tgtrade.signal_from([], "no number here") is None


# ---------------------------------------------------------------- liquidation and sizing
def test_liquidation_estimate_follows_hyperliquids_isolated_formula() -> None:
    long = tgtrade.liquidation_price("LONG", 100.0, 5, 50)
    assert long == pytest.approx(100 - (20 - 1) / (1 - 0.01))  # margin 20/unit, maintenance 1/unit at 50x max
    short = tgtrade.liquidation_price("SHORT", 100.0, 5, 50)
    assert short == pytest.approx(100 + 19 / 1.01) and short > 100 > long
    assert tgtrade.liquidation_price("LONG", 100.0, 10, 50) > long  # more leverage: liquidation closer


def man(**kw: Any) -> tgtrade.Manual:
    req = kw.pop("req", tgtrade.OpenRequest("BTC", "LONG", 100.0, 3))
    base = dict(mark=100.0, market_max_lev=20, sz_decimals=2, funding_h=0.00001, equity=1000.0, acc_max_leverage=3, max_loss_usd=5.0,
                max_allocation_pct=10.0, max_positions=2, open_positions=0, daily_pnl=0.0, daily_loss_limit=15.0, paused=False, global_paused=False)
    return tgtrade.manual_plan(req, **{**base, **kw})


def test_the_members_own_size_within_every_limit_with_a_stop_at_their_max_loss() -> None:
    m = man()
    p = m.plan
    assert p.ok and p.leverage == 3 and p.margin == pytest.approx(100, rel=0.01) and p.notional == pytest.approx(300, rel=0.01)
    assert p.max_loss_usd == pytest.approx(5.0, abs=0.05) and p.stop < 100  # default stop: the loss reaches the max per trade
    assert p.tp1 == pytest.approx(100 + 2 * (100 - p.stop), rel=0.001) and p.tp2 > p.tp1  # twice the risk, then 1.5x further
    assert m.liq < p.stop and p.est_fees == pytest.approx(300 * 2 * risk.TAKER_FEE, rel=0.01)
    cases = [(dict(req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 5)), "over your limit of 3x"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 150.0, 3)), "over your limit of 10%"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 2.0, 3)), "minimum"),
             (dict(open_positions=2), "positions open"), (dict(daily_pnl=-15.0), "daily loss"), (dict(paused=True), "STOP NEW TRADES"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 3, stop=101.0)), "below the price"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 3, stop=60.0)), "past the liquidation"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 3, stop=90.0)), "over your $5.00 max per trade"),
             (dict(req=tgtrade.OpenRequest("BTC", "SHORT", 100.0, 3, tp=105.0)), "below the price")]
    for kw, why in cases:
        r = man(**kw).plan
        assert not r.ok and any(why in x for x in r.reasons), (kw, r.reasons)
    big = man(acc_max_leverage=5, max_loss_usd=500.0, req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 5)).plan
    assert big.ok and big.stop > tgtrade.liquidation_price("LONG", 100.0, 5, 20)  # a max loss past liquidation: the stop sits just before it


# ---------------------------------------------------------------- keys never in a chat
def test_private_keys_and_recovery_phrases_are_recognised() -> None:
    key = "0x" + "ab12" * 16
    assert tgtrade.looks_secret(f"here is my key {key}") and tgtrade.looks_secret("ab12" * 16)
    assert not tgtrade.looks_secret(f"tx {key}", private=False), "in a group the same shape is a transaction hash"
    phrase = "abandon ability able about above absent absorb abstract absurd abuse access accident"
    assert tgtrade.looks_secret(phrase) and tgtrade.looks_secret(phrase, private=False)
    assert not tgtrade.looks_secret(" ".join(phrase.split()[:11]))
    assert not tgtrade.looks_secret("I think bitcoin will go up a lot this week because the market looks strong and buyers are back")


# ---------------------------------------------------------------- who may trade
def test_only_the_banter_referral_opens_new_trades(db: Session, md: FakeMD) -> None:
    tg = TG()
    kiwi(db, 4, "801")  # no verified wallet
    assert "verify your Hyperliquid wallet" in say(db, tg, 801, "/open BTC long 50 3x")
    banter(db, "802", code="SOMEONE")
    assert "Banter group's referral code" in say(db, tg, 802, "/open BTC long 50 3x")
    banter(db, "803", code="KLGXFMNI")  # the code Hyperliquid records for the same referrer is accepted
    say(db, tg, 803, "/open BTC long 50 3x")
    assert db.scalar(select(TrIntent)) is not None
    sig = active_signal(db, md)
    assert "referral" in press(db, tg, 802, f"sig:{sig.id}:enter")  # the signal ENTER button too
    assert not db.scalars(select(TrIntent).where(TrIntent.signal_id == sig.id)).all()


def test_trading_commands_stay_out_of_groups(db: Session, md: FakeMD) -> None:
    banter(db, "811")
    tg = TG()
    assert "privately" in say(db, tg, 811, "/open BTC long 50 3x", group=True)
    assert "privately" in say(db, tg, 811, "/balance", group=True)
    assert db.scalar(select(TrIntent)) is None
    assert "Funding" in say(db, tg, 811, "/market BTC", group=True)  # market numbers are for anyone


# ---------------------------------------------------------------- /open: preview, confirm, duplicates (paper)
def test_open_previews_then_trades_once_on_confirm(db: Session, md: FakeMD) -> None:
    banter(db, "821")
    tg = TG()
    text = say(db, tg, 821, "/open BTC long 50 3x")
    for part in ("PAPER", "Margin $", "3x isolated", "Stop", "TP1", "Liquidation ≈", "Fees ≈", "funding", "Confirm within 5 minutes"):
        assert part in text, part
    assert tg.buttons[-1][0][0]["callback_data"].startswith("mo:") and not db.scalars(select(TrPosition)).all()
    token = last_token(db)
    done = press(db, tg, 821, f"mo:{token}")
    pos = db.scalar(select(TrPosition))
    assert "filled" in done and pos.status == "OPEN" and pos.mode == "PAPER" and pos.via == "manual" and pos.leverage == 3
    assert sorted(o.kind for o in db.scalars(select(TrOrder))) == ["entry", "sl", "tp1", "tp2"]  # protected at once
    anchor = db.get(TsSignal, pos.signal_id)
    assert anchor.status == "MANUAL" and anchor.number < 0 and anchor.published_at is None
    assert "Already handled" in press(db, tg, 821, f"mo:{token}")  # single use
    assert "already have a bot position in BTC" in say(db, tg, 821, "/open BTC long 50 3x")
    assert db.scalar(select(TrPosition.id).where(TrPosition.id != pos.id)) is None


def test_public_signal_numbers_ignore_manual_trades(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    banter(db, "825")
    tg = TG()
    say(db, tg, 825, "/open ETH long 50 3x")
    press(db, tg, 825, f"mo:{last_token(db)}")
    nxt = signals.create(db, signals.analyze(md, "BTC"), "1h", "ACTIVE", "test")
    assert nxt.number == sig.number + 1
    assert "#-" not in say(db, tg, 825, "/signals")


def test_cancelled_and_expired_previews_never_trade(db: Session, md: FakeMD) -> None:
    banter(db, "831")
    tg = TG()
    say(db, tg, 831, "/open BTC long 50 3x")
    assert "Nothing was traded" in press(db, tg, 831, f"mn:{last_token(db)}")
    say(db, tg, 831, "/open BTC long 50 3x")
    it = db.scalars(select(TrIntent).order_by(TrIntent.id.desc())).first()
    it.expires_at = utcnow().replace(year=2000)
    db.commit()
    assert "expired" in press(db, tg, 831, f"mo:{it.token}")
    say(db, tg, 831, "/open BTC long 50 3x")
    assert "Dropped 1" in say(db, tg, 831, "/cancel")
    assert not db.scalars(select(TrPosition)).all()


def test_someone_elses_confirmation_does_nothing(db: Session, md: FakeMD) -> None:
    banter(db, "841")
    banter(db, "842")
    tg = TG()
    say(db, tg, 841, "/open BTC long 50 3x")
    assert "isn't yours" in press(db, tg, 842, f"mo:{last_token(db)}")
    assert not db.scalars(select(TrPosition)).all()


def test_limits_refuse_before_any_preview(db: Session, md: FakeMD) -> None:
    banter(db, "851")
    tg = TG()
    assert "over your limit of 3x" in say(db, tg, 851, "/open BTC long 50 10x")
    assert "over your limit of 10%" in say(db, tg, 851, "/open BTC long 500 3x")
    assert "Usage" in say(db, tg, 851, "/open BTC long")
    assert "isn't a Hyperliquid perpetual" in say(db, tg, 851, "/open NOPE long 50 3x")
    assert db.scalar(select(TrIntent)) is None


# ---------------------------------------------------------------- testnet: failures and protection
def test_testnet_order_refused_leaves_no_position_and_a_failed_stop_closes_at_once(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    m = banter(db, "861")
    on_testnet(db, m)
    ex = FakeExchange(mark=uptrend_pullback()[-1].c, no_fill=True)
    monkeypatch.setattr(execution, "executor", lambda *a, **k: ex)
    tg = TG()
    assert "TESTNET" in say(db, tg, 861, "/open BTC long 50 3x")
    assert "Not filled" in press(db, tg, 861, f"mo:{last_token(db)}")
    assert db.scalar(select(TrPosition)).status == "FAILED" and not [o for o in ex.orders if o["type"] == "sl"]
    ex.no_fill, ex.stop_fails = False, True
    say(db, tg, 861, "/open BTC long 50 3x")
    msg = press(db, tg, 861, f"mo:{last_token(db)}")
    pos = db.scalars(select(TrPosition).order_by(TrPosition.id.desc())).first()
    assert pos.status != "OPEN" and "stopped for safety" in msg  # no stop: closed at market, new trades stopped
    assert execution.account(db, m).paused


def test_mainnet_orders_from_telegram_stay_off_until_the_owner_turns_them_on(db: Session, md: FakeMD) -> None:
    m = banter(db, "871")
    acc = execution.account(db, m)
    acc.mode = "LIVE"
    db.commit()
    tg = TG()
    assert "aren't switched on" in say(db, tg, 871, "/open BTC long 50 3x")
    assert db.scalar(select(TrIntent)) is None
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "telegram_trading": {"live": True}}
    db.commit()
    assert tgtrade.telegram_live(db) and tgtrade.mode_gate(db, acc) is None


# ---------------------------------------------------------------- managing positions
def open_paper(db: Session, tg: TG, uid: int, coin: str = "BTC") -> TrPosition:
    say(db, tg, uid, f"/open {coin} long 50 3x")
    press(db, tg, uid, f"mo:{last_token(db)}")
    return db.scalars(select(TrPosition).where(TrPosition.coin == coin).order_by(TrPosition.id.desc())).first()


def test_positions_orders_balance_and_settings(db: Session, md: FakeMD) -> None:
    banter(db, "881")
    tg = TG()
    pos = open_paper(db, tg, 881)
    text = say(db, tg, 881, "/positions")
    assert f"#{pos.id}" in text and "BTC" in text and "uPnL" in text and "liq ≈" in text
    assert "stop-loss" in say(db, tg, 881, "/orders") or "No open orders" in tg.sent[-1][2]  # paper stops are simulated, not resting orders
    assert "PAPER balance" in say(db, tg, 881, "/balance")
    assert "Bot results" in say(db, tg, 881, "/pnl")
    assert "Max leverage 3x" in say(db, tg, 881, "/settings")
    assert "capped at" in say(db, tg, 881, "/settings leverage 9")
    assert "Saved" in say(db, tg, 881, "/settings leverage 2") and "Max leverage 2x" in tg.sent[-1][2]
    assert "testnet API wallet" in say(db, tg, 881, "/settings mode testnet") or "Close your open" in tg.sent[-1][2]
    assert "Live is switched on at the website" in say(db, tg, 881, "/settings mode live")


def test_stop_moves_only_closer_and_targets_move_after_confirm(db: Session, md: FakeMD) -> None:
    banter(db, "891")
    tg = TG()
    pos = open_paper(db, tg, 891)
    closer = round((pos.entry_px + pos.stop) / 2, 1)
    text = say(db, tg, 891, f"/sl {pos.id} {closer}")
    assert "Stop-loss" in text and tg.buttons[-1][0][0]["callback_data"].startswith("pr:")
    assert db.get(TrPosition, pos.id).stop != closer  # not before Confirm
    assert "Done" in press(db, tg, 891, tg.buttons[-1][0][0]["callback_data"])
    db.expire_all()
    assert db.get(TrPosition, pos.id).stop == pytest.approx(closer, rel=1e-4)
    farther = round(pos.stop * 0.9, 1)
    assert "Done" not in say(db, tg, 891, f"/sl {pos.id} {farther}")  # never more risk
    new_tp = round(pos.tp1 * 1.01, 1)
    say(db, tg, 891, f"/tp {new_tp}")  # the only position: no number needed
    assert "Done" in press(db, tg, 891, tg.buttons[-1][0][0]["callback_data"])


def test_stops_cant_be_cancelled_and_testnet_targets_can(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    m = banter(db, "901")
    on_testnet(db, m)
    ex = FakeExchange(mark=uptrend_pullback()[-1].c)
    monkeypatch.setattr(execution, "executor", lambda *a, **k: ex)
    tg = TG()
    pos = open_paper(db, tg, 901)
    assert pos.mode == "TESTNET" and pos.status == "OPEN"
    orders = {o.kind: o for o in db.scalars(select(TrOrder).where(TrOrder.position_id == pos.id))}
    assert "can't be cancelled" in say(db, tg, 901, f"/cancel {orders['sl'].id}")
    assert "Cancelled order" in say(db, tg, 901, f"/cancel {orders['tp2'].id}") and orders["tp2"].oid in ex.cancelled
    assert "stop-loss" in say(db, tg, 901, "/orders")


def test_closeall_asks_first_then_closes_everything(db: Session, md: FakeMD) -> None:
    m = banter(db, "911")
    acc = execution.account(db, m)
    acc.max_positions = 3
    db.commit()
    tg = TG()
    open_paper(db, tg, 911, "BTC")
    open_paper(db, tg, 911, "ETH")
    assert "Close all 2" in say(db, tg, 911, "/closeall")
    assert db.scalar(select(TrPosition.id).where(TrPosition.status == "CLOSED")) is None  # not before Confirm
    assert "Nothing was closed" in press(db, tg, 911, "ca:no")
    assert "out of date" in press(db, tg, 911, "ca:all:1")  # an old button (a 1970 stamp) never closes anything
    fresh = next(b[0][0]["callback_data"] for b in reversed(tg.buttons) if b and b[0][0]["callback_data"].startswith("ca:all:"))
    assert "Closed 2 of 2" in press(db, tg, 911, fresh)
    assert "No open bot positions" in say(db, tg, 911, "/closeall")


def test_closing_works_even_without_the_referral(db: Session, md: FakeMD) -> None:
    m = banter(db, "921")
    tg = TG()
    pos = open_paper(db, tg, 921)
    w = db.scalar(select(TbWallet).where(TbWallet.member_id == m.id))
    w.referral_status, w.referral_code_seen = "not_referred", None  # e.g. the record changed later
    db.commit()
    assert "referral" in say(db, tg, 921, "/open ETH long 50 3x")
    say(db, tg, 921, f"/close {pos.id}")
    assert db.get(TrPosition, pos.id).status == "CLOSED"


# ---------------------------------------------------------------- /enter
def test_enter_by_number_or_reply_sends_the_signal_preview_privately(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    banter(db, "931")
    tg = TG()
    text = say(db, tg, 931, f"/enter #{sig.number:03d}")
    assert f"#{sig.number:03d}" in text and "Stop" in text and tg.buttons[-1][0][0]["callback_data"].startswith("cf:")
    assert "no published signal" in say(db, tg, 931, "/enter 999")
    assert "Reply to a Price Chat signal" in say(db, tg, 931, "/enter")
    asyncio.run(bot.handle_command(db, tg, -100, 931, f"/enter #{sig.number}", group=True, reply_to=5))
    assert tg.sent[-1][1] == -100 and "private preview" in tg.sent[-1][2]
    assert tg.sent[-2][1] == 931  # the preview went to the member's own chat


def test_enter_reply_text_reaches_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.telegram.parser import parse_update

    upd = {"update_id": 1, "message": {"message_id": 9, "date": 1_790_000_000, "chat": {"id": 931, "type": "private"},
                                       "from": {"id": 931, "first_name": "K"}, "text": "/enter",
                                       "reply_to_message": {"message_id": 5, "caption": "🟢 $SOL LONG · #012\n\n🎯 Entry"}}}
    parsed = parse_update(upd)
    assert parsed.reply_text.startswith("🟢 $SOL LONG · #012")
    assert tgtrade.signal_from([], parsed.reply_text) == 12


def test_buttons_expire_and_prices_and_targets_read_as_typed(db: Session, md: FakeMD) -> None:
    banter(db, "941")
    tg = TG()
    pos = open_paper(db, tg, 941)
    t1, t2 = round(pos.tp1 * 1.0123, 1), round(pos.tp1 * 1.0456, 1)
    say(db, tg, 941, f"/tp {t1} {t2}")  # two targets on the only position, not "position {t1}"
    data = tg.buttons[-1][0][0]["callback_data"]
    assert f":{pos.id}:t:" in data and data.endswith(f"{t1:.12g}:{t2:.12g}")
    stale = data.split(":")
    stale[1] = "1"  # the same button, issued long ago
    assert "out of date" in press(db, tg, 941, ":".join(stale))
    assert "Done" in press(db, tg, 941, data)
    db.expire_all()
    assert db.get(TrPosition, pos.id).tp1 == pytest.approx(t1, rel=1e-4)
