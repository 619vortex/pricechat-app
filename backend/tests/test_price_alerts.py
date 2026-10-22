"""Price alerts: /alert parsing, the 10-alert limit, /alerts and removal, the job (fires once, only on a crossing, expiry,
blocked users), the /signal "Alert me at entry" button and the group redirect. No network: fake markets and a fake Telegram."""

import asyncio
import time
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsPriceAlert
from app.telegram.errors import TelegramRateLimitError, TelegramRequestError
from app.tradingos import alerts, bot, market
from app.tradingos.market import Market

from tests.test_trading_os import FakeMD, FakeTG, uptrend_pullback


class MarksMD:
    """TEST FIXTURE: just markets(), with marks the test moves."""

    def __init__(self, **marks: float) -> None:
        self.marks, self.reads = dict(marks), 0

    def markets(self) -> tuple[float, dict[str, Market]]:
        self.reads += 1
        return time.time(), {c: Market(coin=c, mark=px, mid=px, oracle=px, prev_day=px, funding=0.0, open_interest=1.0, day_volume=1e9,
                                       max_leverage=20, impact_bid=px, impact_ask=px, sz_decimals=2) for c, px in self.marks.items()}


class BlockedTG(FakeTG):
    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        if chat_id == 9:
            raise TelegramRequestError("Telegram rejected sendMessage: Forbidden: bot was blocked by the user")
        return await super().send_message(chat_id, text, buttons, html, reply_to)


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> MarksMD:
    f = MarksMD(BTC=80_000.0, ETH=3_000.0, KPEPE=0.01)
    monkeypatch.setattr(market, "_shared", f)
    return f


def say(db: Session, text: str, tg: FakeTG | None = None, uid: int = 9, group: bool = False) -> str:
    tg = tg or FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5 if group else uid, uid, text, group=group))
    return tg.sent[-1][2]


def mine(db: Session, uid: int = 9) -> list[TsPriceAlert]:
    return list(db.scalars(select(TsPriceAlert).where(TsPriceAlert.telegram_user_id == uid).order_by(TsPriceAlert.id)))


def run(db: Session, tg: FakeTG, md: MarksMD, now: Any = None) -> dict[str, int]:
    return asyncio.run(alerts.check(db, tg, md, now))


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize("text,kind,price", [
    ("/alert BTC 85000", "above", 85_000), ("/alert btc 75,000", "below", 75_000), ("/alert $BTC 85k", "above", 85_000),
    ("/alert BTC above 70000", "above", 70_000), ("/alert BTC below 90000", "below", 90_000), ("/alert BTCUSDT > 81000", "above", 81_000),
    ("/alert ETH -5%", "below", 2_850), ("/alert ETH +5%", "above", 3_150), ("/alert pepe 0.012", "above", 0.012),
])
def test_alert_parsing(db: Session, md: MarksMD, text: str, kind: str, price: float) -> None:
    reply = say(db, text)
    a = mine(db)[-1]
    assert (a.kind, round(a.price, 6), a.status) == (kind, price, "active") and f"Alert #{a.id}" in reply
    assert a.expires_at - utcnow() > timedelta(days=29)


@pytest.mark.parametrize("text,needle", [("/alert", "Usage"), ("/alert BTC", "Usage"), ("/alert BTC lots", "Usage"),
                                         ("/alert NOPE 5", "Hyperliquid perps"), ("/alert BTC binance 90000", "Hyperliquid perps"),
                                         ("/alert ETH 5%", "sign"), ("/alert BTC perp", "Usage")])
def test_alert_bad_input_explains(db: Session, md: MarksMD, text: str, needle: str) -> None:
    assert needle in say(db, text) and not mine(db)


def test_limit_list_and_delete(db: Session, md: MarksMD) -> None:
    for i in range(10):
        say(db, f"/alert BTC {81_000 + i}")
    assert "10 active alerts" in say(db, "/alert BTC 95000") and len(mine(db)) == 10
    listed = say(db, "/alerts")
    ids = [a.id for a in mine(db)]
    assert "(10/10)" in listed and f"#{ids[0]} BTC above 81,000" in listed
    say(db, "/alert ETH 3500", uid=10)  # someone else's alert can't be removed by number
    other = mine(db, 10)[0]
    assert "No such alert" in say(db, f"/alert off {other.id}") and other.status == "active"
    assert "Removed" in say(db, f"/alert off {ids[0]}") and "Removed" in say(db, f"/unalert {ids[1]}")
    assert "(8/10)" in say(db, "/alerts")
    assert "Removed 8 alerts" in say(db, "/alert off all")
    assert "No active alerts" in say(db, "/alerts") and mine(db, 10)[0].status == "active"


def test_group_redirects_privately(db: Session, md: MarksMD) -> None:
    assert "Alerts are set privately" in say(db, "/alert BTC 85000", group=True) and not mine(db)
    assert "Alerts are set privately" in say(db, "/alerts", group=True)


# ---------------------------------------------------------------- the job
def test_fires_once_only_on_crossing(db: Session, md: MarksMD) -> None:
    say(db, "/alert BTC 85000")
    say(db, "/alert BTC above 70000")  # already above: waits for the next cross
    tg = FakeTG()
    assert run(db, tg, md)["fired"] == 0 and not tg.sent
    md.marks["BTC"] = 85_120.0
    out = run(db, tg, md)
    assert out["fired"] == 1 and md.reads >= 1
    assert tg.sent == [("msg", 9, "🔔 BTC crossed above 85,000 (now 85,120) · /chart BTC 1h")]
    md.marks["BTC"] = 84_000.0
    run(db, tg, md)
    md.marks["BTC"] = 86_000.0
    run(db, tg, md)
    assert len(tg.sent) == 1  # fired once
    md.marks["BTC"] = 69_000.0
    run(db, tg, md)
    md.marks["BTC"] = 71_000.0
    run(db, tg, md)
    assert len(tg.sent) == 2 and "crossed above 70,000" in tg.sent[-1][2]
    assert [a.status for a in mine(db)] == ["fired", "fired"]


def test_one_markets_read_and_expiry(db: Session, md: MarksMD) -> None:
    for t in ("/alert BTC 85000", "/alert ETH -5%", "/alert BTC 75000"):
        say(db, t)
    md.reads = 0
    assert run(db, FakeTG(), md)["active"] == 3 and md.reads == 1
    assert run(db, FakeTG(), md, utcnow() + timedelta(days=31))["expired"] == 3
    assert {a.status for a in mine(db)} == {"expired"}


def test_blocked_user_disables_alert(db: Session, md: MarksMD) -> None:
    say(db, "/alert BTC 85000")
    say(db, "/alert BTC 85000", uid=10)
    md.marks["BTC"] = 86_000.0
    tg = BlockedTG()
    out = run(db, tg, md)
    assert out == {"active": 0, "fired": 1, "expired": 0, "blocked": 1}
    assert mine(db)[0].status == "blocked" and mine(db, 10)[0].status == "fired"


class SlowTG(FakeTG):
    """TEST FIXTURE: the second message never finishes (the job's timeout cancels the run)."""

    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        if self.sent:
            raise asyncio.CancelledError
        return await super().send_message(chat_id, text, buttons, html, reply_to)


class RateLimitedTG(FakeTG):
    def __init__(self) -> None:
        super().__init__()
        self.tries = 0

    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        self.tries += 1
        raise TelegramRateLimitError("Telegram rate limit; retry after 30s", 30)


def test_run_cut_short_never_resends_what_went_out(db: Session, md: MarksMD) -> None:
    say(db, "/alert BTC 85000")
    say(db, "/alert BTC 85000", uid=10)
    md.marks["BTC"] = 86_000.0
    with pytest.raises(asyncio.CancelledError):
        run(db, SlowTG(), md)
    db.rollback()
    assert mine(db)[0].status == "fired" and mine(db, 10)[0].status == "active"
    tg = FakeTG()
    run(db, tg, md)
    assert [s[1] for s in tg.sent] == [10]  # only the one that never went out


def test_rate_limit_stops_sending_until_next_run(db: Session, md: MarksMD) -> None:
    for uid in (9, 10, 11):
        say(db, "/alert BTC 85000", uid=uid)
    md.marks["BTC"] = 86_000.0
    tg = RateLimitedTG()
    assert run(db, tg, md)["fired"] == 0 and tg.tries == 1
    ok = FakeTG()
    assert run(db, ok, md)["fired"] == 3 and len(ok.sent) == 3  # the crossing is kept for the next run


# ---------------------------------------------------------------- the /signal button
def test_signal_button_creates_zone_alert(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    f = FakeMD({"BTC": uptrend_pullback()})
    monkeypatch.setattr(market, "_shared", f)
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal BTC", group=True, reply_to=3))
    kind, _, _, btns = tg.sent[-1]
    data = btns[0][0]["callback_data"]
    assert kind == "photo" and btns[0][0]["text"] == "🔔 Alert me at entry" and data.startswith("pa:BTC:") and len(data.encode()) <= 64
    asyncio.run(bot.handle_callback(db, tg, {"id": "c1", "from": {"id": 9}, "data": data}))
    a = mine(db)[0]
    _, plan, lo, hi = alerts.parse_button(data)  # type: ignore[misc]
    assert (a.kind, a.price, a.price_hi, a.plan) == ("zone", lo, hi, plan) and plan["side"] in ("LONG", "SHORT")
    assert tg.sent[-2][1] == 9 and "entry zone" in tg.sent[-2][2] and tg.sent[-1][0] == "answer"
    # the price enters the zone from outside: the plan comes with the alert, once
    mid = (lo + hi) / 2
    a.last_price = hi * 1.05 if plan["side"] == "LONG" else lo * 0.95
    db.commit()
    mm = MarksMD(BTC=mid)
    run(db, tg, mm)
    run(db, tg, mm)
    fired = [s for s in tg.sent if s[0] == "msg" and "entered the" in s[2]]
    assert len(fired) == 1 and "stop" in fired[0][2] and "target" in fired[0][2] and a.status == "fired"


def test_button_needs_start_in_the_bot(db: Session, md: MarksMD) -> None:
    plan = type("P", (), {"side": "LONG", "entry": (79_000.0, 79_500.0), "stop": 77_000.0, "target": 84_000.0})()
    data = alerts.button("BTC", plan, "1h")[0][0]["callback_data"]  # type: ignore[index]
    tg = BlockedTG()
    asyncio.run(bot.handle_callback(db, tg, {"id": "c1", "from": {"id": 9}, "data": data}))
    assert not mine(db) and "press Start" in tg.sent[-1][2]


def test_zone_jump_and_button_size() -> None:
    a = TsPriceAlert(kind="zone", price=100.0, price_hi=101.0, last_price=99.0)
    assert alerts.crossed(a, 102.0) and alerts.crossed(a, 100.5) and not alerts.crossed(a, 99.5)
    a.last_price = 100.5  # inside already: needs to leave first
    assert not alerts.crossed(a, 100.7)
    plan = type("P", (), {"side": "SHORT", "entry": (0.0000123456, 0.0000124567), "stop": 0.0000131234, "target": 0.0000101234})()
    data = alerts.button("KNEIRO", plan, "15m")[0][0]["callback_data"]  # type: ignore[index]
    assert len(data.encode()) <= 64 and alerts.parse_button(data)[1]["tf"] == "15m"  # type: ignore[index]
