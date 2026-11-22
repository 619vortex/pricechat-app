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
from app.tradingos import exchange as xch

from tests.test_trading_execution import FakeExchange, active_signal, on_testnet
from tests.test_trading_os import FakeMD, FakeTG, kiwi, uptrend_pullback


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> FakeMD:
    f = FakeMD({"BTC": uptrend_pullback(), "SOL": uptrend_pullback(), "ETH": uptrend_pullback()})
    monkeypatch.setattr(market, "_shared", f)
    return f


class FakeReader:
    """TEST FIXTURE: stands in for exchange.ReadOnlyExecutor (the key-less mainnet reader): a balance, no positions, and every
    order-shaped call refused exactly like the real one."""

    read_only = True
    built: list[str] = []

    def __init__(self, mode: str, master: str, transport: Any = None) -> None:
        self.mode, self.master = mode, master
        FakeReader.built.append(mode)

    def equity(self) -> float:
        return 1000.0

    def state(self) -> dict[str, Any]:
        return {"assetPositions": []}

    def mid_px(self, coin: str) -> float:
        return 100.0

    def _refuse(self, *a: Any, **k: Any) -> Any:
        raise xch.MainnetBlocked(xch.MAINNET_BLOCKED)

    set_leverage = market = trigger = cancel = cancel_by_cloid = _refuse


@pytest.fixture(autouse=True)
def _no_hyperliquid(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST FIXTURE: the referral recheck, mainnet reads (balance, fee rate, API wallet listing) would ask Hyperliquid; tests
    never call the real API."""
    monkeypatch.setattr(tgtrade, "recheck_referral", lambda db, m: None)
    FakeReader.built = []
    monkeypatch.setattr(xch, "ReadOnlyExecutor", FakeReader)
    monkeypatch.setattr(xch, "user_fees", lambda mode, a, transport=None: {"taker": 0.00045, "maker": 0.00015, "referral_discount": 0.04})
    monkeypatch.setattr(xch, "agent_record", lambda mode, master, agent, transport=None: {"address": agent, "validUntil": None})
    monkeypatch.setattr(xch, "balances", lambda mode, a, transport=None: {"perps": 1000.0, "spot_usdc": 0.0, "unified": False, "trading": 1000.0})


class TG(FakeTG):
    """TEST FIXTURE: also keeps the buttons of each message."""

    def __init__(self) -> None:
        super().__init__()
        self.buttons: list[Any] = []
        self.replies: list[Any] = []

    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        self.buttons.append(buttons)
        self.replies.append(reply_to)
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


def button(tg: TG, prefix: str, text: str | None = None) -> str:
    """The callback data of the last message's button starting with prefix (and labelled text, if given)."""
    for row in tg.buttons[-1] or []:
        for b in row:
            if b.get("callback_data", "").startswith(prefix) and (text is None or b["text"] == text):
                return b["callback_data"]
    raise AssertionError(f"no {prefix} button under: {tg.sent[-1][2]!r} {tg.buttons[-1]!r}")


def opn(db: Session, tg: TG, uid: int, text: str) -> str:
    """A trading command, then its card's Preview: the preview with Confirm (or what refused it)."""
    say(db, tg, uid, text)
    return press(db, tg, uid, button(tg, "op:"))


# ---------------------------------------------------------------- parsing
def req_of(text: str) -> tuple[Any, ...]:
    r = tgtrade.parse_open(text.upper().split())
    assert isinstance(r, tgtrade.OpenRequest), (text, r)
    return (r.coin, r.side, r.margin, r.leverage)


def test_open_reads_coin_side_margin_leverage_and_optional_stop_and_target() -> None:
    r = tgtrade.parse_open(["BTC", "LONG", "100", "5X"])
    assert isinstance(r, tgtrade.OpenRequest) and (r.coin, r.side, r.margin, r.leverage, r.stop, r.tp) == ("BTC", "LONG", 100.0, 5, None, None)
    r = tgtrade.parse_open(["$sol", "short", "$50", "3x", "sl", "130.5", "tp", "110"])
    assert (r.coin, r.side, r.margin, r.leverage, r.stop, r.tp) == ("SOL", "SHORT", 50.0, 3, 130.5, 110.0)
    for bad in [[], ["BTC", "long", "-5", "5x"], ["BTC", "long", "100", "2.5x"], ["BTC", "long", "100", "5x", "sl"],
                ["BTC", "long", "100", "5x", "moon", "1"], ["B/TC", "long", "1", "1x"], ["BTC", "long", "inf", "3x"], ["BTC", "long", "nan"],
                ["BTC", "long", "1e9", "3x"], ["BTC", "long", "100", "0x"], ["BTC", "long", "100", "500x"]]:
        assert isinstance(tgtrade.parse_open(bad), str), bad


def test_every_command_format_reads_the_same_in_any_order_and_case() -> None:
    """Owner, 2026-11-10: /open long btc 10 10x gave a usage error. The words may come in any order and any case; a plain
    number is the amount (USD margin), a number with x the leverage."""
    for text in ("long btc 10 10x", "BTC long 10 10x", "btc LONG 10x 10", "10 10x long $btc", "buy BTC $10 10X"):
        assert req_of(text) == ("BTC", "LONG", 10.0, 10), text
    assert req_of("long btc") == ("BTC", "LONG", None, None)  # /long BTC: both asked for with buttons
    assert req_of("short eth") == ("ETH", "SHORT", None, None)  # /short ETH
    assert req_of("long btc 10x") == ("BTC", "LONG", None, 10)  # /long BTC 10x: the amount is asked for
    assert req_of("long btc 25") == ("BTC", "LONG", 25.0, None)  # a bare number is never a leverage
    assert req_of("sell sol 1.5k 2x") == ("SOL", "SHORT", 1500.0, 2)
    r = tgtrade.parse_open("LONG BTC TP 79K 10 SL 60K 3X LIMIT 64.5K".split())  # price pairs anywhere after the side
    assert (r.margin, r.leverage, r.tp, r.stop, r.limit) == (10.0, 3, 79000.0, 60000.0, 64500.0)


def test_ambiguous_input_gets_a_short_specific_message_never_a_guess() -> None:
    cases = {"long btc 10 10": "Two amounts", "long btc eth 10 3x": "Two coins", "long short btc 10 3x": "not both",
             "long btc 10 3x 5x": "Two leverages", "long btc 10 3x moon": "Two coins", "long btc 10 3x wh@t": "isn't a coin, an amount",
             "btc 10 3x": "Long or short", "long 10 3x": "Which coin", "long btc 10 3x sl": "needs a price", "long btc 10 3x sl 1 sl 2": "twice"}
    for text, why in cases.items():
        out = tgtrade.parse_open(text.upper().split())
        assert isinstance(out, str) and why in out and len(out) < 160 and "Usage" not in out, (text, out)
    assert "Usage" in tgtrade.parse_open([])


def test_a_missing_amount_or_leverage_is_never_guessed() -> None:
    m = man(req=tgtrade.OpenRequest("BTC", "LONG", None, 3))  # the old "no amount = full margin limit" is gone
    assert not m.plan.ok and m.plan.size == 0 and any("amount and a leverage" in r for r in m.plan.reasons)
    assert not man(req=tgtrade.OpenRequest("BTC", "LONG", 50.0, None)).plan.ok


def test_no_banter_group_wording_and_long_short_run_the_real_open_flow(db: Session, md: FakeMD) -> None:
    from app.tradingbot import affiliate

    tg = TG()
    kiwi(db, 4, "851")  # no wallet yet: one short line and Connect Hyperliquid / Connect wallet buttons, no pitch
    out = say(db, tg, 851, "/open btc long 20 3x")
    assert "No wallet is connected" in out and "Kiwi Passport" in out and "anter" not in out.lower() and len(out) < 200
    assert tg.buttons[-1] == [[{"text": "🔗 Connect Hyperliquid", "url": "https://pricechat.live/passport#trade-title"}],
                              [{"text": "Connect wallet", "url": "https://pricechat.live/passport#account-wallet"}]]
    assert "4% off" not in out and "@botpricechat" not in out and "/api/r/" not in out
    banter(db, "852")
    out = say(db, tg, 852, "/long btc 3x")  # /long BTC 3x: the amount is asked for with buttons, never guessed
    assert "How much margin" in out and "leverage 3x" in out and not db.scalars(select(TrIntent)).all()
    assert [b["text"] for b in tg.buttons[-1][0]] == ["$10", "$25", "$50", "$100"]  # equity 1000 x 10%: up to $100
    out = press(db, tg, 852, button(tg, "oa:", "$50"))  # the full card
    assert "BTC LONG" in out and "Amount $50.00 · 3x" in out and not db.scalars(select(TrIntent)).all()
    out = press(db, tg, 852, button(tg, "op:"))  # Preview: the preview with Confirm, as before
    it = db.scalars(select(TrIntent).order_by(TrIntent.id.desc())).first()
    assert it is not None and it.plan["side"] == "LONG" and it.plan["request"]["margin"] == 50.0 and it.plan["leverage"] == 3
    assert button(tg, "mo:") == f"mo:{it.token}" and "Confirm within 5 minutes" in out
    opn(db, tg, 852, "/short eth 50 2x")
    assert db.scalars(select(TrIntent).order_by(TrIntent.id.desc())).first().plan["side"] == "SHORT"
    owner = kiwi(db, 4, "853")  # the referral's own wallet can't be "referred" by itself: the owner trades too
    db.add(TbWallet(member_id=owner.id, address=affiliate.WALLET, verified_at=utcnow(), referral_status="unverified", referral_code_seen=None))
    db.commit()
    assert tgtrade.entry_gate(db, owner) is None
    for text in (tgtrade.HELP, bot.HELP_TEXT if hasattr(bot, "HELP_TEXT") else ""):
        assert "Banter group" not in text


def test_open_takes_the_side_before_the_coin_too() -> None:
    for words in (["long", "btc", "10", "5x"], ["LONG", "BTC", "10", "5X"], ["btc", "long", "10", "5x"]):
        r = tgtrade.parse_open(words)
        assert isinstance(r, tgtrade.OpenRequest) and (r.coin, r.side, r.margin, r.leverage) == ("BTC", "LONG", 10.0, 5), words
    r = tgtrade.parse_open(["short", "eth", "20", "3x", "tp", "2k"])
    assert (r.coin, r.side, r.tp) == ("ETH", "SHORT", 2000.0)


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
    cases = [(dict(req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 25), market_max_lev=40), "over Price Chat's cap of 20x"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 15), market_max_lev=10), "over BTC's maximum on Hyperliquid of 10x"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 150.0, 3)), "over your limit of 10%"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 2.0, 3)), "minimum"),
             (dict(open_positions=2), "positions open"), (dict(daily_pnl=-15.0), "daily loss"), (dict(paused=True), "STOP NEW TRADES"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 3, stop=101.0)), "below the price"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 3, stop=60.0)), "past the liquidation"),
             (dict(req=tgtrade.OpenRequest("BTC", "LONG", 100.0, 3, stop=90.0)), "$5.00 max per trade"),
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
    assert "No wallet is connected" in say(db, tg, 801, "/open BTC long 50 3x")
    banter(db, "802", code="SOMEONE")
    out = say(db, tg, 802, "/open BTC long 50 3x")
    assert "made with our sign-up link" in out and "Hyperliquid's record" in out and "4% off" not in out and len(out) < 220
    assert tg.buttons[-1][0][0]["text"] == "Sign up on Hyperliquid" and "/api/r/gate" in tg.buttons[-1][0][0]["url"]  # the tracked link
    assert tg.buttons[-1][1][0]["url"] == "https://pricechat.live/passport#account-wallet"
    banter(db, "803", code="KLGXFMNI")  # the code Hyperliquid records for the same referrer is accepted
    opn(db, tg, 803, "/open BTC long 50 3x")
    assert db.scalar(select(TrIntent)) is not None
    sig = active_signal(db, md)
    assert "sign-up link" in press(db, tg, 802, f"sig:{sig.id}:enter")  # the signal ENTER button too
    assert not db.scalars(select(TrIntent).where(TrIntent.signal_id == sig.id)).all()


def gsay(db: Session, tg: TG, uid: int, text: str, msg_id: int) -> str:
    asyncio.run(bot.handle_command(db, tg, -100, uid, text, group=True, reply_to=msg_id))
    return tg.sent[-1][2]


def gpress(db: Session, tg: TG, uid: int, data: str, owner: int | None, cmd_id: int = 77) -> str:
    msg = {"chat": {"id": -100, "type": "supergroup"}, "reply_to_message": {"message_id": cmd_id, "from": {"id": owner}} if owner else {}}
    asyncio.run(bot.handle_callback(db, tg, {"id": "x", "data": data, "from": {"id": uid}, "message": msg}))
    return tg.sent[-1][2] if tg.sent else ""


def test_trading_works_in_groups_and_its_buttons_only_for_whoever_asked(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    banter(db, "811")
    banter(db, "812")
    tg = TG()
    answered: list[str] = []

    async def note(client: Any, cq: Any, uid: int, text: str, alert: bool = False, dm: bool = True) -> None:
        answered.append(text)
    monkeypatch.setattr(bot, "_answer", note)
    out = gsay(db, tg, 811, "/long btc", msg_id=77)  # the amount card, in the group, replying to the command
    assert "How much margin" in out and tg.sent[-1][1] == -100 and tg.replies[-1] == 77
    pick = button(tg, "oa:", "$50")
    n = len(tg.sent)
    gpress(db, tg, 812, pick, owner=811)  # another member presses 811's choice
    assert "for the member who asked" in answered[-1] and len(tg.sent) == n
    assert "which leverage" in gpress(db, tg, 811, pick, owner=811) and tg.sent[-1][1] == -100 and tg.replies[-1] == 77
    gpress(db, tg, 811, button(tg, "oa:", "5x"), owner=811)
    assert "Amount $50.00 · 5x" in tg.sent[-1][2] and tg.replies[-1] == 77  # the full card replies to the command too
    preview = button(tg, "op:")
    gpress(db, tg, 812, preview, owner=811)  # someone else's Preview: refused, no intent
    assert "for the member who asked" in answered[-1] and not db.scalars(select(TrIntent)).all()
    gpress(db, tg, 812, button(tg, "ox:"), owner=811)
    assert "for the member who asked" in answered[-1]
    say_ = gpress(db, tg, 811, preview, owner=811)
    assert "Confirm within 5 minutes" in say_ and tg.sent[-1][1] == -100
    assert tg.replies[-1] == 77, "the preview replies to the member's own command, so its buttons know whose they are"
    token = last_token(db)
    n = len(tg.sent)
    gpress(db, tg, 812, f"mo:{token}", owner=811)  # another member presses 811's Confirm
    assert answered and "for the member who asked" in answered[-1] and len(tg.sent) == n
    assert db.scalar(select(TrIntent).where(TrIntent.token == token)).status == "PENDING", "nothing confirmed"
    gsay(db, tg, 811, "/open BTC long 50 3x", msg_id=81)
    assert tg.replies[-1] == 81 and "Preview" in str(tg.buttons[-1])
    gpress(db, tg, 812, "ca:all:1", owner=None)  # a group button with no known owner works for nobody
    assert "for the member who asked" in answered[-1]
    assert "privately" not in gsay(db, tg, 811, "/balance", msg_id=78)
    assert "privately" in gsay(db, tg, 811, "/connect", msg_id=79), "wallet linking stays private"
    assert "Funding" in gsay(db, tg, 811, "/market BTC", msg_id=80)  # market numbers are for anyone


# ---------------------------------------------------------------- /open: preview, confirm, duplicates (paper)
def test_open_previews_then_trades_once_on_confirm(db: Session, md: FakeMD) -> None:
    banter(db, "821")
    tg = TG()
    text = opn(db, tg, 821, "/open BTC long 50 3x")
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
    opn(db, tg, 825, "/open ETH long 50 3x")
    press(db, tg, 825, f"mo:{last_token(db)}")
    nxt = signals.create(db, signals.analyze(md, "BTC"), "1h", "ACTIVE", "test")
    assert nxt.number == sig.number + 1
    assert "#-" not in say(db, tg, 825, "/signals")


def test_cancelled_and_expired_previews_never_trade(db: Session, md: FakeMD) -> None:
    banter(db, "831")
    tg = TG()
    opn(db, tg, 831, "/open BTC long 50 3x")
    assert "Nothing was traded" in press(db, tg, 831, f"mn:{last_token(db)}")
    opn(db, tg, 831, "/open BTC long 50 3x")
    it = db.scalars(select(TrIntent).order_by(TrIntent.id.desc())).first()
    it.expires_at = utcnow().replace(year=2000)
    db.commit()
    assert "expired" in press(db, tg, 831, f"mo:{it.token}")
    opn(db, tg, 831, "/open BTC long 50 3x")
    assert "Dropped 1" in say(db, tg, 831, "/cancel")
    assert not db.scalars(select(TrPosition)).all()


def test_someone_elses_confirmation_does_nothing(db: Session, md: FakeMD) -> None:
    banter(db, "841")
    banter(db, "842")
    tg = TG()
    opn(db, tg, 841, "/open BTC long 50 3x")
    assert "isn't yours" in press(db, tg, 842, f"mo:{last_token(db)}")
    assert not db.scalars(select(TrPosition)).all()


def test_limits_refuse_before_any_preview(db: Session, md: FakeMD) -> None:
    banter(db, "851")
    tg = TG()
    assert "over Price Chat's cap of 20x" in say(db, tg, 851, "/open BTC long 50 25x")
    assert "over your limit of 10%" in say(db, tg, 851, "/open BTC long 500 3x")
    assert "How much margin" in say(db, tg, 851, "/open BTC long")  # asked with buttons, never guessed
    assert "isn't a Hyperliquid perpetual" in say(db, tg, 851, "/open NOPE long 50 3x")
    assert db.scalar(select(TrIntent)) is None


# ---------------------------------------------------------------- testnet: failures and protection
def test_testnet_order_refused_leaves_no_position_and_a_failed_stop_closes_at_once(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    m = banter(db, "861")
    on_testnet(db, m)
    ex = FakeExchange(mark=uptrend_pullback()[-1].c, no_fill=True)
    monkeypatch.setattr(execution, "executor", lambda *a, **k: ex)
    tg = TG()
    assert "TESTNET" in opn(db, tg, 861, "/open BTC long 50 3x")
    assert "Not filled" in press(db, tg, 861, f"mo:{last_token(db)}")
    assert db.scalar(select(TrPosition)).status == "FAILED" and not [o for o in ex.orders if o["type"] == "sl"]
    ex.no_fill, ex.stop_fails = False, True
    opn(db, tg, 861, "/open BTC long 50 3x")
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
    card = say(db, tg, 871, "/open BTC long 50 3x")  # a LIVE account: the card says Preview is read-only...
    assert "LIVE" in card and "Preview is read-only" in card
    out = press(db, tg, 871, button(tg, "op:"))  # ...and Preview is the read-only mainnet preview, never a Confirm
    assert "MAINNET PREVIEW" in out and "OFF until" in out and tg.buttons[-1] is None
    assert db.scalar(select(TrIntent)) is None
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "telegram_trading": {"live": True}}
    db.commit()
    assert tgtrade.telegram_live(db) and tgtrade.mode_gate(db, acc) is None


# ---------------------------------------------------------------- managing positions
def open_paper(db: Session, tg: TG, uid: int, coin: str = "BTC") -> TrPosition:
    opn(db, tg, uid, f"/open {coin} long 50 3x")
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
    assert "capped at 20x" in say(db, tg, 881, "/settings leverage 25")
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
    assert "sign-up link" in say(db, tg, 921, "/open ETH long 50 3x")
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


# ---------------------------------------------------------------- audit 2026-11-08 (TR-4, TR-5, TR-6, TR-11, TR-13, TR-14, TR-16)
def _live(db: Session, m: Any, enabled: bool) -> Any:
    acc = execution.account(db, m)
    acc.mode, acc.master_address = "LIVE", db.scalar(select(TbWallet.address).where(TbWallet.member_id == m.id))
    acc.live_enabled_at = utcnow() if enabled else None
    acc.mainnet_agent, acc.mainnet_agent_key, acc.mainnet_approved_at = "0x" + "2" * 40, "unused", utcnow()
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "telegram_trading": {"live": True}}
    db.commit()
    return acc


def test_TR4_live_manual_opens_go_through_the_same_live_checks_as_every_entry(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    m = banter(db, "991")
    _live(db, m, enabled=False)  # e.g. after connect_api_key with open LIVE positions: mode stays LIVE, live_enabled_at cleared
    ex = FakeExchange(mark=md.market("BTC").mark)
    with pytest.raises(execution.TradeError, match="LIVE isn't enabled"):
        tgtrade.preview_open(db, m, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3), ex)
    acc = _live(db, m, enabled=True)
    it = tgtrade.preview_open(db, m, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3), ex)
    acc.live_enabled_at = None  # LIVE withdrawn between the preview and the Confirm
    db.commit()
    with pytest.raises(execution.TradeError, match="LIVE isn't enabled"):
        tgtrade.confirm_open(db, m, it.token, ex)
    assert not ex.orders and not db.scalars(select(TrPosition)).all()
    # the real exchange path also asks Hyperliquid whether the API wallet is still listed for the current verified wallet
    _live(db, m, enabled=True)
    asked: list[int] = []

    def revoked(db_: Any, m_: Any, a_: Any) -> None:
        asked.append(a_.id)
        raise execution.TradeError("Kiwi's trading permission isn't active on Hyperliquid anymore")

    monkeypatch.setattr(execution, "check_live_permission", revoked)
    monkeypatch.setattr(execution, "executor", lambda *a, **k: ex)
    with pytest.raises(execution.TradeError, match="permission isn't active"):
        tgtrade.preview_open(db, m, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3))
    assert asked and not ex.orders


class NetMD(FakeMD):
    """TEST FIXTURE: a market whose prices, szDecimals and max leverage differ (Hyperliquid testnet vs mainnet)."""

    def __init__(self, series: Any, factor: float, szd: int, max_lev: int) -> None:
        super().__init__(series)
        self.factor, self.szd, self.max_lev = factor, szd, max_lev

    def markets(self) -> Any:
        t, out = super().markets()
        for k, x in out.items():
            px = x.mark * self.factor
            out[k] = market.Market(coin=x.coin, mark=px, mid=px, oracle=px, prev_day=x.prev_day * self.factor, funding=x.funding,
                                   open_interest=x.open_interest, day_volume=x.day_volume, max_leverage=self.max_lev,
                                   impact_bid=px * 0.99995, impact_ask=px * 1.00005, sz_decimals=self.szd)
        return t, out


def test_TR6_testnet_trades_are_priced_and_sized_from_testnets_own_market(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    test_md = NetMD({"BTC": uptrend_pullback()}, factor=1.06, szd=1, max_lev=2)  # testnet: 6% higher, 1 decimal, 2x max
    monkeypatch.setattr(market, "testnet", lambda: test_md)
    main_px, test_px = md.market("BTC").mark, test_md.market("BTC").mark
    m = banter(db, "992")
    on_testnet(db, m)
    ex = FakeExchange(mark=test_px)
    with pytest.raises(execution.TradeError, match="BTC's maximum on Hyperliquid of 2x"):  # testnet's max leverage, not mainnet's 20x
        tgtrade.preview_open(db, m, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3), ex)
    it = tgtrade.preview_open(db, m, tgtrade.OpenRequest("BTC", "LONG", 50.0, 2), ex)
    assert it.plan["entry"] == pytest.approx(test_px) and it.plan["entry"] != pytest.approx(main_px)
    assert it.plan["size"] == risk.round_size(100.0 / test_px, 1)  # rounded to testnet's szDecimals
    pos = tgtrade.confirm_open(db, m, it.token, ex)
    entry = next(o for o in ex.orders if o["type"] == "market")
    assert pos.status == "OPEN" and entry["px"] == pytest.approx(test_px * (1 + 2 * risk.SLIPPAGE), rel=1e-3)  # IOC limit off testnet's price
    assert pos.stop < test_px and pos.tp1 > test_px
    # paper keeps simulating on mainnet's real prices
    p = banter(db, "993")
    assert tgtrade.preview_open(db, p, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3)).plan["entry"] == pytest.approx(main_px)


def test_TR11_manual_trades_keep_the_limits_signal_trades_have(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    m = banter(db, "994")
    acc = execution.account(db, m)
    acc.allowed_coins = "ETH, SOL"
    db.commit()
    with pytest.raises(execution.TradeError, match="allowed contracts"):
        tgtrade.preview_open(db, m, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3))
    acc.allowed_coins, acc.frequency, acc.max_trades_per_day, acc.max_positions = "", "active", 1, 3
    db.commit()
    tg = TG()
    open_paper(db, tg, 994, "ETH")
    with pytest.raises(execution.TradeError, match="limit 1 a day"):
        tgtrade.preview_open(db, m, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3))
    acc.frequency = "standard"
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "risk_v2": {"mode": "enforce"}}
    db.commit()
    monkeypatch.setattr(risk, "portfolio_checks", lambda cfg, **k: [{"status": "fail", "label": "Exposure", "detail": "too much"}])
    with pytest.raises(execution.TradeError, match="exposure: too much"):
        tgtrade.preview_open(db, m, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3))
    assert db.scalar(select(TrIntent).where(TrIntent.status == "PENDING")) is None


def test_TR13_manual_numbers_never_collide_and_unconfirmed_previews_dont_pile_up(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = banter(db, "995"), banter(db, "996")
    acc = execution.account(db, a)
    acc.max_positions = 3
    db.commit()
    first = tgtrade.preview_open(db, a, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3))
    taken = db.get(TsSignal, first.signal_id).number
    real = getattr(tgtrade, "_next_manual_number", None)
    calls = [taken, taken - 1]  # another preview took the number between the read and the insert
    monkeypatch.setattr(tgtrade, "_next_manual_number", lambda db_: calls.pop(0) if calls else real(db_), raising=False)
    other = tgtrade.preview_open(db, b, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3))
    assert db.get(TsSignal, other.signal_id).number == taken - 1
    tgtrade.cancel_previews(db, a)
    for _ in range(3):  # previews never confirmed reuse the member's spare row
        tgtrade.preview_open(db, a, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3))
        tgtrade.cancel_previews(db, a)
    mine = lambda: db.scalars(select(TsSignal).where(TsSignal.status == "MANUAL", TsSignal.approved_by == f"m:{a.id}")).all()  # noqa: E731
    assert len(mine()) == 1
    btc = tgtrade.preview_open(db, a, tgtrade.OpenRequest("BTC", "LONG", 50.0, 3))
    eth = tgtrade.preview_open(db, a, tgtrade.OpenRequest("ETH", "LONG", 50.0, 3))  # a preview still waiting keeps its own row
    assert btc.signal_id != eth.signal_id
    p1, p2 = tgtrade.confirm_open(db, a, btc.token), tgtrade.confirm_open(db, a, eth.token)
    assert p1.status == p2.status == "OPEN" and db.get(TsSignal, p1.signal_id).coin == "BTC" and db.get(TsSignal, p2.signal_id).coin == "ETH"
    nxt = tgtrade.preview_open(db, a, tgtrade.OpenRequest("SOL", "LONG", 50.0, 3))
    assert nxt.signal_id not in (p1.signal_id, p2.signal_id)  # a row with a position is never reused


def test_TR14_the_key_warning_doesnt_promise_what_hyperliquid_doesnt_state() -> None:
    assert "never withdraw" not in tgtrade.SECRET_WARNING and "can never" not in tgtrade.SECRET_WARNING
    assert "never sends withdrawals or transfers" in tgtrade.SECRET_WARNING and "revoke" in tgtrade.SECRET_WARNING


def test_TR5_the_banter_gate_on_signal_enter_is_an_owner_setting_on_by_default(db: Session, md: FakeMD) -> None:
    sig = active_signal(db, md)
    banter(db, "997", code="SOMEONE")
    tg = TG()
    assert tgtrade.gate_signal_enter(db) and "sign-up link" in press(db, tg, 997, f"sig:{sig.id}:enter")
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "telegram_trading": {"gate_signal_enter": False}}
    db.commit()
    press(db, tg, 997, f"sig:{sig.id}:enter")
    assert db.scalars(select(TrIntent).where(TrIntent.signal_id == sig.id)).all()  # ENTER works as before the branch
    assert "made with our sign-up link" in say(db, tg, 997, "/open BTC long 50 3x")  # manual /open stays gated


def test_TR16_a_different_account_key_is_refused_while_testnet_positions_are_open(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    import eth_account

    from app.tradingbot import service as earn
    from tests.test_trading_execution import _HL

    earn.reset_limits_for_tests()
    m = banter(db, "998")
    on_testnet(db, m)
    acc = execution.account(db, m)
    acc.master_address = db.scalar(select(TbWallet.address).where(TbWallet.member_id == m.id))
    ex = FakeExchange(mark=md.market("BTC").mark)
    monkeypatch.setattr(execution, "executor", lambda *a, **k: ex)
    tg = TG()
    assert open_paper(db, tg, 998).mode == "TESTNET"
    owner, api = eth_account.Account.create(), eth_account.Account.create()
    monkeypatch.setattr(xch, "agent_record", lambda *a, **k: {"name": "PriceChat", "address": api.address, "validUntil": None})
    monkeypatch.setattr(xch, "user_role", lambda *a, **k: {"role": "agent", "data": {"user": owner.address.lower()}})
    execution._agent_seen.clear()
    from app.tradingos import accounts

    with pytest.raises(accounts.AccountError, match="Close your open positions"):
        accounts.connect_api_key(db, m, None, api.key.hex(), True, _HL("BANTER", 50), replace=True)
    assert execution.account(db, m).master_address != owner.address.lower()


# ---------------------------------------------------------------- mainnet stays off by default (feat/tg-trading-v2)
def test_default_settings_keep_every_new_telegram_entry_off_mainnet(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the default settings (no `telegram_trading` in the strategies JSON) neither /open nor /enter (private, or replying
    in a group) can reach a LIVE (mainnet) order, even for a member whose account is fully set up for LIVE on the website."""
    assert "telegram_trading" not in (signals.settings(db).strategies or {})
    assert tgtrade.telegram_live(db) is False and tgtrade.gate_signal_enter(db) is True
    sig = active_signal(db, md)
    m = banter(db, "1001")
    acc = execution.account(db, m)
    acc.mode, acc.master_address = "LIVE", db.scalar(select(TbWallet.address).where(TbWallet.member_id == m.id))
    acc.live_enabled_at = utcnow()
    acc.mainnet_agent, acc.mainnet_agent_key, acc.mainnet_approved_at = "0x" + "2" * 40, "unused", utcnow()
    db.commit()
    asked: list[str] = []
    ex = FakeExchange(mark=md.market("BTC").mark)

    def guard(db_: Any, a_: Any, coin: str, mode: str | None = None) -> Any:
        asked.append(mode or a_.mode)
        return ex

    monkeypatch.setattr(execution, "executor", guard)
    monkeypatch.setattr(execution, "check_live_permission", lambda *a, **k: None)  # only the Telegram switch stands in the way
    monkeypatch.setattr(xch, "HyperliquidExecutor", lambda *a, **k: pytest.fail("a real Hyperliquid executor was built"))
    monkeypatch.undo()  # the real executor(): inside Telegram it hands out the key-less reader for LIVE
    monkeypatch.setattr(tgtrade, "recheck_referral", lambda db, m: None)
    monkeypatch.setattr(xch, "ReadOnlyExecutor", FakeReader)
    monkeypatch.setattr(xch, "user_fees", lambda mode, a, transport=None: {"taker": 0.00045, "maker": 0.00015, "referral_discount": 0})
    monkeypatch.setattr(xch, "agent_record", lambda mode, master, agent, transport=None: {"address": agent, "validUntil": None})
    monkeypatch.setattr(market, "_shared", md)
    monkeypatch.setattr(xch, "HyperliquidExecutor", lambda *a, **k: pytest.fail("a real Hyperliquid executor was built"))
    tg = TG()
    assert "MAINNET PREVIEW" in opn(db, tg, 1001, "/open BTC long 50 3x") and tg.buttons[-1] is None
    assert "MAINNET PREVIEW" in opn(db, tg, 1001, "/long BTC 50 3x") and tg.buttons[-1] is None
    assert "MAINNET PREVIEW" in say(db, tg, 1001, f"/enter #{sig.number:03d}") and tg.buttons[-1] is None
    asyncio.run(bot.handle_command(db, tg, -100, 1001, f"/enter #{sig.number}", group=True, reply_to=5))
    assert "MAINNET PREVIEW" in tg.sent[-2][2] and tg.sent[-2][1] == 1001  # the group /enter: the preview goes privately
    for token in ("mo:" + "0" * 16, "cf:" + "0" * 16):  # forged confirms find nothing to send
        press(db, tg, 1001, token)
    assert not db.scalars(select(TrIntent)).all() and not ex.orders and not asked
    assert FakeReader.built and set(FakeReader.built) == {"LIVE"}  # mainnet was only ever read, without a key
    assert not db.scalars(select(TrPosition)).all()


def test_no_telegram_path_reaches_mainnet_until_the_owner_switches_it_on(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner, 2026-11-09: every Telegram path that could execute on mainnet is off (the signal card's ENTER button, Confirm,
    Close, /close, /closeall, /sl, /tp, order /cancel, and their buttons) until live trading is approved. TESTNET keeps working."""
    sig = active_signal(db, md)
    m = banter(db, "1002")
    acc = execution.account(db, m)
    acc.mode, acc.master_address = "LIVE", db.scalar(select(TbWallet.address).where(TbWallet.member_id == m.id))
    acc.live_enabled_at = utcnow()
    acc.mainnet_agent, acc.mainnet_agent_key, acc.mainnet_approved_at = "0x" + "3" * 40, "unused", utcnow()
    db.commit()
    ex = FakeExchange(mark=md.market("BTC").mark)
    monkeypatch.setattr(execution, "executor", lambda *a, **k: ex)
    monkeypatch.setattr(execution, "check_live_permission", lambda *a, **k: None)
    monkeypatch.setattr(xch, "HyperliquidExecutor", lambda *a, **k: pytest.fail("a real Hyperliquid executor was built"))
    tg = TG()
    off = "aren't switched on"
    press(db, tg, 1002, f"sig:{sig.id}:enter")  # the existing ENTER button on a signal card: a read-only preview, privately
    assert "MAINNET PREVIEW" in tg.sent[-2][2] and tg.sent[-2][1] == 1002 and tg.buttons[-1] is None
    for data in ("cf:" + "1" * 16, "cl:77", "mo:" + "2" * 16, f"ca:all:{tgtrade._stamp()}", f"pr:{tgtrade._stamp()}:77:s:60000"):
        assert off in press(db, tg, 1002, data), data
    for text in ("/close 77", "/closeall", "/sl 77 60000", "/tp 77 70000", "/cancel 12345"):
        assert off in say(db, tg, 1002, text), text
    assert not ex.orders and not db.scalars(select(TrIntent)).all()
    # the owner's switch turns it back on: ENTER then gets as far as a private preview
    st = signals.settings(db)
    st.strategies = {**(st.strategies or {}), "telegram_trading": {"live": True}}
    db.commit()
    press(db, tg, 1002, f"sig:{sig.id}:enter")
    assert not any("MAINNET PREVIEW" in x[2] or off in x[2] for x in tg.sent[-2:])
    # TESTNET is untouched
    st.strategies = {**(st.strategies or {}), "telegram_trading": {}}
    acc.mode = "TESTNET"
    db.commit()
    assert off not in press(db, tg, 1002, f"sig:{sig.id}:enter")


# ---------------------------------------------------------------- the trading card (owner, 2026-11-10)
def test_the_card_shows_price_amount_leverage_size_fees_liquidation_and_stop(db: Session, md: FakeMD) -> None:
    banter(db, "1101")
    tg = TG()
    card = say(db, tg, 1101, "/open long btc 50 3x")  # the screenshot's word order
    mark = md.market("BTC").mark
    assert [b["text"] for b in tg.buttons[-1][0]] == ["👁 Preview", "Cancel"] and not db.scalars(select(TrIntent)).all()
    out = press(db, tg, 1101, button(tg, "op:"))
    p = db.scalars(select(TrIntent)).first().plan  # the same price in the fake market: the card's numbers are the preview's
    f = market.fmt_price
    for part in ("📝 <b>PAPER</b> · simulated, never sent to Hyperliquid\n🟢 <b>BTC LONG</b> · price", f"price {f(mark)}", "Amount $50.00 · 3x", f"position ${p['notional']:,.2f}",
                 f"({p['size']:g} BTC)", f"Fees ≈ ${p['est_fees']:,.2f}", f"liquidation ≈ {f(p['liq'])}", f"Stop {f(p['stop'])}",
                 "(default: ≈ $", f"TP {f(p['tp1'])}", "Nothing is sent yet"):
        assert part in card, (part, card)
    assert len(card.splitlines()) <= 6 and "Confirm within 5 minutes" in out
    card = say(db, tg, 1101, "/short ETH 50 3x sl 1e9")  # a stop typed wrong is a word, not a price
    assert "needs a price" in card
    eth = md.market("ETH").mark
    card = say(db, tg, 1101, f"/short ETH 50 3x sl {eth * 1.01:.2f} tp {eth * 0.97:.2f}")
    assert "(yours)" in card and "🔴 <b>ETH SHORT</b>" in card


def test_missing_pieces_are_asked_for_with_buttons_within_the_limits(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    m = banter(db, "1102")
    tg = TG()
    out = say(db, tg, 1102, "/long BTC")
    assert "How much margin? Up to $100.00 · then the leverage" in out and "price " in out
    assert [b["text"] for b in tg.buttons[-1][0]] == ["$10", "$25", "$50", "$100"] and tg.buttons[-1][1][0]["text"] == "Cancel"
    out = press(db, tg, 1102, button(tg, "oa:", "$25"))
    assert "Amount $25.00 · which leverage? Up to 20x" in out and [b["text"] for b in tg.buttons[-1][0]] == ["2x", "5x", "10x", "20x"]
    out = press(db, tg, 1102, button(tg, "oa:", "2x"))
    assert "Amount $25.00 · 2x" in out and button(tg, "op:")
    out = say(db, tg, 1102, "/long btc 40")  # an amount and no leverage
    assert "Amount $40.00 · which leverage" in out
    acc = execution.account(db, m)
    acc.max_allocation_pct = 3.0  # $30 margin at most
    db.commit()
    say(db, tg, 1102, "/short sol")
    assert [b["text"] for b in tg.buttons[-1][0]] == ["$10", "$25"]
    monkeypatch.setattr(market, "_shared", NetMD({"SOL": uptrend_pullback(), "ETH": uptrend_pullback()}, 1.0, 2, 3))  # a coin with a 3x maximum
    press(db, tg, 1102, button(tg, "oa:", "$10"))
    assert "Up to 3x" in tg.sent[-1][2] and [b["text"] for b in tg.buttons[-1][0]] == ["2x", "3x"]  # clamped to the coin's maximum
    say(db, tg, 1102, "/long eth 4")
    assert [b["text"] for b in tg.buttons[-1][0]] == ["3x"]  # $4 at 2x is under the $10 minimum
    acc.max_allocation_pct = 0.5  # $5: only $5 itself
    db.commit()
    say(db, tg, 1102, "/long eth")
    assert [b["text"] for b in tg.buttons[-1][0]] == ["$5"]
    acc.max_allocation_pct = 0.05  # $0.50: nothing reaches Hyperliquid's $10 minimum
    db.commit()
    out = say(db, tg, 1102, "/long eth")
    assert "under Hyperliquid's $10 minimum" in out and tg.buttons[-1] is None
    assert not db.scalars(select(TrIntent)).all()


def test_too_much_leverage_or_margin_asks_again_instead_of_refusing_or_guessing(db: Session, md: FakeMD) -> None:
    banter(db, "1103")
    tg = TG()
    out = say(db, tg, 1103, "/open long btc 10 10x")  # the owner's screenshot: a card, not a usage error
    assert "Amount $10.00 · 10x" in out and button(tg, "op:")
    out = say(db, tg, 1103, "/open long btc 10 25x")
    assert "25x is over Price Chat's cap of 20x" in out and "Amount $10.00 · which leverage" in out
    assert [b["text"] for b in tg.buttons[-1][0]] == ["2x", "5x", "10x", "20x"]
    out = say(db, tg, 1103, "/open BTC long 500 3x")
    assert "over your limit of 10%" in out and "How much margin" in out and not db.scalars(select(TrIntent)).all()


def test_forged_stale_or_out_of_limit_card_buttons_never_make_a_trade(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    banter(db, "1104")
    tg = TG()
    s = tgtrade._stamp()
    for data, why in ((f"op:{s}:BTC:L:50:25", "isn't valid"), (f"op:{s}:BTC:L:inf:3", "isn't valid"), (f"op:{s}:B-TC:L:50:3", "isn't valid"),
                      (f"op:{s}:BTC:X:50:3", "isn't valid"), (f"op:{s}:BTC:L:50:3:q1", "isn't valid"), (f"op:{s}:BTC:L:_:3", "isn't valid"),
                      ("op:1:BTC:L:50:3", "out of date"), (f"op:{s}:BTC", "isn't valid"), (f"op:{s}:BTC:L:50:3:s{'9' * 60}", "isn't valid"),
                      (f"op:{s}:NOPE:L:50:3", "isn't a Hyperliquid perpetual"), (f"op:{s}:BTC:L:5000:3", "over your limit of 10%"),
                      (f"oa:{s}:BTC:L:50:21", "isn't valid"), ("ox:BTC", "Cancelled")):
        assert why in press(db, tg, 1104, data), data
    assert not db.scalars(select(TrIntent)).all() and not db.scalars(select(TrPosition)).all()
    assert "over your limit of 10%" in press(db, tg, 1104, f"oa:{s}:BTC:L:5000:3")  # a forged choice is only asked again
    good = f"op:{s}:BTC:L:50:3"
    tgtrade.set_switches(db, "admin:test", kill=True)  # the emergency stop wins over any button
    assert "emergency stop" in press(db, tg, 1104, good)
    tgtrade.set_switches(db, "admin:test", kill=False)
    w = db.scalar(select(TbWallet).where(TbWallet.address.like("%1104")))
    w.referral_status, w.referral_code_seen = "not_referred", None  # the referral gate is asked again on the press
    db.commit()
    assert "made with our sign-up link" in press(db, tg, 1104, good) and tg.buttons[-1][0][0]["text"] == "Sign up on Hyperliquid"
    assert not db.scalars(select(TrIntent)).all()
    # the buttons are rate-limited like every trading button
    cq = {"id": "x", "data": good, "from": {"id": 1104}}
    assert [asyncio.run(bot.trade_button_throttled(tg, cq)) for _ in range(8)][-1] is True
    assert bot.trade_button_throttled is not None and {"oa:", "op:"} <= set(bot.TRADE_BUTTONS)
    assert {"oa:", "op:", "ox:"} <= set(bot.OWNED_BUTTONS)


def test_cancel_drops_the_card_and_preview_cancel_drops_the_intent(db: Session, md: FakeMD) -> None:
    banter(db, "1105")
    tg = TG()
    say(db, tg, 1105, "/long BTC 50 3x")
    assert "Cancelled. Nothing was sent" in press(db, tg, 1105, button(tg, "ox:"))
    assert not db.scalars(select(TrIntent)).all()
    opn(db, tg, 1105, "/long BTC 50 3x")
    assert "Nothing was traded" in press(db, tg, 1105, button(tg, "mn:"))
    assert db.scalars(select(TrIntent)).first().status == "CANCELLED" and not db.scalars(select(TrPosition)).all()


def test_trading_replies_carry_no_footer_or_referral_ad_and_other_commands_keep_it(db: Session, md: FakeMD) -> None:
    from app.tradingbot.service import settings as earn_settings

    assert earn_settings(db).referral_url  # a referral link is set: the footer would carry it
    banter(db, "1106")
    kiwi(db, 4, "1107")  # no wallet
    tg = TG()
    for uid, text in ((1106, "/open BTC long 50 3x"), (1106, "/long eth"), (1106, "/balance"), (1106, "/settings"), (1106, "/orders"),
                      (1106, "/positions"), (1106, "/market BTC"), (1106, "/long btc 10 10"), (1107, "/long BTC 50 3x"), (1106, "/status")):
        out = say(db, tg, uid, text)
        assert "@botpricechat" not in out and "4% off" not in out and "/api/r/footer" not in out, (text, out)
    out = press(db, tg, 1106, f"op:{tgtrade._stamp()}:BTC:L:50:3")
    assert "Confirm within 5 minutes" in out and "@botpricechat" not in out and "4% off" not in out
    asyncio.run(bot.handle_command(db, tg, -100, 1106, "/long BTC 50 3x", group=True, reply_to=9))
    assert "@botpricechat" not in tg.sent[-1][2]
    assert "@botpricechat" in say(db, tg, 1106, "/help") and "@botpricechat" in say(db, tg, 1106, "/signals")


def test_help_shows_the_new_formats() -> None:
    for text in (tgtrade.HELP, bot.HELP):
        assert "/long BTC 50 3x" in text and "/short ETH 25 2x" in text and "full margin" not in text
    assert "/long BTC 50 3x" in tgtrade.OPEN_USAGE and "any order" in tgtrade.OPEN_USAGE
    assert {"long", "short", "open"} <= {c for c, _ in bot.MENU_PRIVATE}


def test_every_card_and_preview_shows_the_account_mode_badge(db: Session, md: FakeMD, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner, 2026-11-10: PAPER (simulated, never sent to Hyperliquid) / TESTNET (testnet, test funds) / LIVE (real money;
    preview only while Telegram mainnet execution is OFF), on the card and on the preview."""
    paper, test, live = banter(db, "1201"), banter(db, "1202"), banter(db, "1203")
    tg = TG()
    assert say(db, tg, 1201, "/long BTC").startswith("📝 <b>PAPER</b> · simulated, never sent to Hyperliquid")
    assert opn(db, tg, 1201, "/long BTC 50 3x").startswith("📝 <b>PAPER</b> · simulated, never sent to Hyperliquid")
    on_testnet(db, test)
    real_executor = execution.executor
    monkeypatch.setattr(execution, "executor", lambda *a, **k: FakeExchange(mark=md.market("BTC").mark))
    assert say(db, tg, 1202, "/long BTC 50 3x").startswith("🧪 <b>TESTNET</b> · Hyperliquid testnet, test funds")
    assert press(db, tg, 1202, button(tg, "op:")).startswith("🧪 <b>TESTNET</b> · Hyperliquid testnet, test funds")
    monkeypatch.setattr(execution, "executor", real_executor)  # inside Telegram: the key-less reader for LIVE
    acc = execution.account(db, live)
    acc.mode, acc.master_address = "LIVE", db.scalar(select(TbWallet.address).where(TbWallet.member_id == live.id))
    db.commit()
    badge = "💰 <b>LIVE</b> · real money · preview only: Telegram mainnet execution is OFF"
    card = say(db, tg, 1203, "/long BTC 50 3x")
    assert card.startswith(badge) and "Preview is read-only" in card
    out = press(db, tg, 1203, button(tg, "op:"))
    assert badge in out and "MAINNET PREVIEW" in out and tg.buttons[-1] is None
    assert not db.scalars(select(TrIntent).where(TrIntent.mode == "LIVE")).all() and paper is not None


def test_no_hyperliquid_account_or_api_wallet_gets_a_connect_hyperliquid_button(db: Session, md: FakeMD) -> None:
    connect = [[{"text": "🔗 Connect Hyperliquid", "url": "https://pricechat.live/passport#trade-title"}]]
    m = banter(db, "1204")
    acc = on_testnet(db, m)
    acc.testnet_agent_key = None  # testnet without its API wallet
    db.commit()
    tg = TG()
    out = say(db, tg, 1204, "/long BTC 50 3x")
    assert "TESTNET" in out and "No Hyperliquid testnet API wallet" in out and tg.buttons[-1] == connect and len(out) < 200
    assert "TESTNET" in press(db, tg, 1204, f"op:{tgtrade._stamp()}:BTC:L:50:3") and tg.buttons[-1] == connect
    acc.mode, acc.mainnet_agent_key = "LIVE", None  # LIVE (mainnet switched on) with no mainnet API wallet
    s = signals.settings(db)
    s.strategies = {**(s.strategies or {}), "telegram_trading": {"live": True}}
    db.commit()
    out = say(db, tg, 1204, "/long BTC")
    assert "LIVE · REAL MONEY" in out and "No Hyperliquid API wallet" in out and tg.buttons[-1] == connect
    assert not db.scalars(select(TrIntent)).all()
