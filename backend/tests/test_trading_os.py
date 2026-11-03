"""Trading OS Phase 1-2: indicators, the deterministic strategy (incl. NO TRADE), scan → review → publish, tracking on real
candles, Telegram commands and signal buttons (access, duplicates), charts. No network: a fake Hyperliquid."""

import asyncio
import time
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.members import service as members
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.tradingos import TsSignal, TsSignalAction
from app.tradingos import bot, chart, indicators as ind, market, signals
from app.tradingos.market import Candle, Market


# ---------------------------------------------------------------- a fake Hyperliquid (TEST FIXTURES)
def uptrend_pullback(n: int = 200, start: float = 100.0) -> list[Candle]:
    """An orderly uptrend (up 0.35, down 0.12 alternately), a 4-candle dip back to EMA20, then a bounce candle:
    EMA20 > EMA50, price at EMA20, RSI mid-range, momentum turning back up."""
    t0, p, out = int(time.time() * 1000) - n * 3_600_000, start, []
    steps = [0.35 if i % 2 else -0.12 for i in range(n - 5)] + [-0.3] * 4 + [0.5]
    for i, d in enumerate(steps):
        o, c = p, p + d
        out.append(Candle(t0 + i * 3_600_000, o, max(o, c) + 0.3, min(o, c) - 0.3, c, 1000))
        p = c
    return out


def flat(n: int = 200, price: float = 100.0) -> list[Candle]:
    t0 = int(time.time() * 1000) - n * 3_600_000
    return [Candle(t0 + i * 3_600_000, price, price + 0.5, price - 0.5, price + (0.1 if i % 2 else -0.1), 1000) for i in range(n)]


class FakeMD(market.MarketData):
    def __init__(self, series: dict[str, list[Candle]], volume: float = 5e8, depth: float = 1e6, spread_bps: float = 1.0) -> None:
        self.series, self.volume, self.depth, self.spread = series, volume, depth, spread_bps
        self._cache = {}

    def markets(self) -> tuple[float, dict[str, Market]]:
        out = {}
        for coin, cs in self.series.items():
            px = cs[-1].c
            out[coin] = Market(coin=coin, mark=px, mid=px, oracle=px, prev_day=cs[-24].c, funding=0.00001, open_interest=1e6,
                               day_volume=self.volume, max_leverage=20, impact_bid=px * (1 - 0.00005), impact_ask=px * (1 + 0.00005), sz_decimals=2)
        return time.time(), out

    def candles(self, coin: str, interval: str, count: int = 200) -> list[Candle]:
        cs = self.series[self.market(coin).coin]
        if interval == "1m":  # the last hour as minutes, ending at the last close
            last = cs[-1]
            t0 = int(time.time() * 1000) - 60 * 60_000
            return [Candle(t0 + i * 60_000, last.c, last.c + 0.01, last.c - 0.01, last.c, 10) for i in range(60)]
        return cs[-count:]

    def book(self, coin: str) -> dict[str, float]:
        px = self.series[self.market(coin).coin][-1].c
        return {"bid": px, "ask": px * (1 + self.spread / 10_000), "mid": px, "spread_bps": self.spread,
                "bid_depth_usd": self.depth, "ask_depth_usd": self.depth}

    def positions(self, address: str) -> dict[str, Any]:
        return {"equity": 1234.5, "margin_used": 0, "positions": []}


class FakeTG:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
        self.sent.append(("msg", chat_id, text))
        return {"message_id": len(self.sent)}

    async def send_photo(self, chat_id: Any, png: bytes, caption: str, buttons: Any = None, reply_to: Any = None, html: bool = False) -> dict[str, Any]:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        self.sent.append(("photo", chat_id, caption, buttons))
        return {"message_id": 100 + len(self.sent)}

    async def edit_caption(self, chat_id: Any, message_id: int, caption: str, buttons: Any = None) -> None:
        self.sent.append(("edit", message_id, caption, buttons))

    async def answer_callback(self, cid: str, text: str, alert: bool = False) -> None:
        self.sent.append(("answer", cid, text))


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> FakeMD:
    f = FakeMD({"BTC": uptrend_pullback(), "SOL": uptrend_pullback(), "FLAT": flat()})
    monkeypatch.setattr(market, "_shared", f)
    return f


def kiwi(db: Session, rank: int, tg: str) -> Any:
    ensure_catalog(db)
    m = members.create_member(db)
    members.link_identity(db, m, "telegram", tg, "kiwi", None)
    need = next(r for r in ledger.ranks(db) if r.level == rank).min_xp
    ledger.adjust(db, m, need + 1, actor="test", reason="rank", ref=f"r{tg}")
    db.commit()
    return m


# ---------------------------------------------------------------- indicators
def test_indicators_known_values() -> None:
    assert ind.ema([1, 2, 3, 4, 5], 3)[-1] == pytest.approx(4.0)
    assert ind.rsi([float(i) for i in range(30)]) == 100.0  # only gains
    assert ind.rsi([float(30 - i) for i in range(30)]) == pytest.approx(0.0)
    assert ind.atr([2.0] * 20, [1.0] * 20, [1.5] * 20) == pytest.approx(1.0)
    assert ind.macd([1.0] * 40)[2] == pytest.approx(0.0)


# ---------------------------------------------------------------- strategy
def test_a_clean_uptrend_pullback_is_a_long_with_real_levels(md: FakeMD) -> None:
    a = signals.analyze(md, "SOL")
    assert a.decision == "LONG", a.reasons
    price = md.series["SOL"][-1].c
    assert a.entry[0] < price < a.entry[1] and a.stop < a.entry[0] and a.target > a.entry[1]
    assert a.rr >= 1.6 and a.lev[0] <= a.lev[1] <= 5


def test_poor_conditions_are_no_trade_with_reasons(md: FakeMD) -> None:
    a = signals.analyze(md, "FLAT")
    assert a.decision == "NO TRADE" and a.reasons
    thin = FakeMD({"SOL": uptrend_pullback(), "BTC": uptrend_pullback()}, volume=1e6, depth=10_000, spread_bps=20)
    b = signals.analyze(thin, "SOL")
    assert b.decision == "NO TRADE" and any("volume" in r for r in b.reasons) and any("spread" in r for r in b.reasons)
    with pytest.raises(market.MarketError):
        signals.analyze(md, "NOPE")  # not a Hyperliquid market: an error, never a made-up answer


# ---------------------------------------------------------------- scan → review → publish → track
def test_scan_review_publish_and_track(db: Session, md: FakeMD) -> None:
    s = signals.settings(db)
    s.allowed_coins = "SOL,FLAT"
    db.commit()
    out = signals.scan(db, md)
    assert out["created"] == [1] and "FLAT" in out["no_trade"]
    sig = db.scalar(select(TsSignal))
    assert sig.status == "PENDING_REVIEW" and sig.published_at is None  # nothing goes out without approval
    assert signals.scan(db, md)["created"] == []  # one open signal per market
    tg = FakeTG()
    asyncio.run(bot.publish(db, tg, sig, "admin:test"))
    asyncio.run(bot.publish(db, tg, sig, "admin:test"))  # idempotent: never posted twice
    photos = [x for x in tg.sent if x[0] == "photo"]
    assert len(photos) == 1 and photos[0][1] == "@botpricechat" and sig.status == "WAITING" and sig.tg_message_id
    card = photos[0][2]
    assert card.startswith("🟢 $SOL LONG · #001") and "🛑 Stop" in card and "📍 WAITING" in card and "TEST" not in card
    assert [b["text"] for b in photos[0][3][0]] == ["ENTER", "REJECT", "AUTO"]
    # the price is inside the entry zone -> ACTIVE; then the stop is hit (stop checked first: the conservative case)
    changed = signals.track(db, md)
    assert sig.status == "ACTIVE" and changed == [sig]
    md.series["SOL"].append(Candle(int(time.time() * 1000), sig.stop, sig.stop, sig.stop - 1, sig.stop - 1, 1))
    sig.last_checked_at = None
    assert signals.track(db, md) == [sig] and sig.status == "STOP_HIT" and sig.model_pnl_pct < 0  # losers count too
    tg2 = FakeTG()
    asyncio.run(bot.refresh(db, tg2, sig))
    closed_buttons = tg2.sent[-1][3]  # the card updates in place: ENTER / REJECT / AUTO removed, the Hyperliquid sign-up stays
    assert "📍 STOP HIT" in tg2.sent[-1][2] and [[b.get("url") is not None for b in row] for row in closed_buttons] == [[True]]


def test_settle_and_stats_count_losers_too(db: Session) -> None:
    from datetime import timedelta

    from app.db.base import utcnow

    def mk(n: int, status: str, exit_: float) -> TsSignal:
        s = TsSignal(number=n, coin="SOL", side="LONG", timeframe="1h", strategy="t", entry_lo=99, entry_hi=101, stop=95, target=110,
                     lev_lo=3, lev_hi=5, rr=2, status="ACTIVE", expires_at=utcnow() + timedelta(hours=1), published_at=utcnow())
        db.add(s)
        signals.settle(s, status, exit_)
        return s

    mk(1, "TARGET_HIT", 110)
    mk(2, "STOP_HIT", 95)
    db.commit()
    st = signals.stats(db)
    assert st["closed"] == 2 and st["win_rate"] == 50.0 and st["avg_loss"] < 0 < st["avg_gain"]
    assert "not executed trades" in st["label"]


# ---------------------------------------------------------------- Telegram
def test_commands_public_and_private(db: Session, md: FakeMD) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, 1, 555, "/price sol"))
    assert "SOL" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, 1, 555, "/chart BTC 5m"))
    assert tg.sent[-1][0] == "photo"
    asyncio.run(bot.handle_command(db, tg, 1, 555, "/risk"))
    assert "Link your Kiwi Passport" in tg.sent[-1][2]  # the private service needs a linked Passport
    kiwi(db, 1, "556")
    asyncio.run(bot.handle_command(db, tg, 2, 556, "/risk"))
    assert "Mode: PAPER" in tg.sent[-1][2]  # open to every member: Rank 1 can use it
    asyncio.run(bot.handle_command(db, tg, 1, 555, "/signal SOL"))
    assert tg.sent[-1][0] == "photo" and "not a published signal" in tg.sent[-1][2]  # analysis: anyone, no account needed
    kiwi(db, 4, "557")
    asyncio.run(bot.handle_command(db, tg, 3, 557, "/signal FLAT"))
    assert "NO TRADE" in tg.sent[-1][2] and "Entry" not in tg.sent[-1][2]  # no full setup: NO TRADE, never a made-up plan
    asyncio.run(bot.handle_command(db, tg, 3, 557, "/signal SOL"))
    assert tg.sent[-1][0] == "photo" and "LONG setup" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, 3, 557, "/auto"))
    assert "AUTO is off" in tg.sent[-1][2]  # nothing trades for anyone until they turn it on with their limits


def test_buttons_need_access_and_never_count_twice(db: Session, md: FakeMD) -> None:
    sig = signals.create(db, signals.analyze(md, "SOL"), "1h", "WAITING", "test")
    db.commit()
    tg = FakeTG()
    kiwi(db, 1, "601")
    asyncio.run(bot.handle_callback(db, tg, {"id": "a", "data": f"sig:{sig.id}:enter", "from": {"id": 601}}))
    assert "Preview sent" in tg.sent[-1][2] and not db.scalars(select(TsSignalAction)).all()  # Rank 1: open to every member
    kiwi(db, 4, "602")
    for cid in ("b", "c"):  # a double tap
        asyncio.run(bot.handle_callback(db, tg, {"id": cid, "data": f"sig:{sig.id}:reject", "from": {"id": 602}}))
    assert len(db.scalars(select(TsSignalAction)).all()) == 1 and tg.sent[-1][2] == "Already noted."
    assert signals.participation(db, sig) == {"reject": 1, "live": 0, "practice": 0}


def test_chart_is_a_real_png(md: FakeMD) -> None:
    png = chart.render(md.series["SOL"][-100:], "SOL · 1h", (10.0, 11.0), 9.0, 13.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000


def test_group_commands_answer_publicly_and_keep_accounts_private(db: Session, md: FakeMD) -> None:
    assert bot.is_ours("/price", "cryptopricechatbot") and bot.is_ours("/price@CryptoPriceChatBot BTC", "cryptopricechatbot")
    assert not bot.is_ours("/price@someotherbot", "cryptopricechatbot")  # another bot's command: ignored
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -100, 9, "/markets", group=True, reply_to=5))
    assert "market overview" in tg.sent[-1][2] and "Top" in tg.sent[-1][2]  # curated, not a dump
    kiwi(db, 4, "777")
    asyncio.run(bot.handle_command(db, tg, -100, 777, "/positions", group=True, reply_to=6))
    assert "privately" in tg.sent[-1][2]  # balances never in the group
    asyncio.run(bot.handle_command(db, tg, 1, 777, "/connect"))
    assert "Never share a private key" in tg.sent[-1][2]



def test_manual_signal_says_so_and_is_never_in_stats(db: Session, md: FakeMD) -> None:
    sig = signals.test_signal(db, md, "SOL", "admin:test")
    assert sig.strategy == "test" and sig.status == "WAITING" and "Manual setup" in signals.card(sig) and "TEST" not in signals.card(sig)
    tg = FakeTG()
    asyncio.run(bot.publish(db, tg, sig, "admin:test"))
    assert "Manual setup" in tg.sent[-1][2] and "💬" not in tg.sent[-1][2] and sig.published_at is not None  # no invented reasons
    signals.settle(sig, "TARGET_HIT", sig.target)
    db.commit()
    assert signals.stats(db)["closed"] == 0 and signals.stats(db)["total"] == 0  # tests never count as results


# ---------------------------------------------------------------- onboarding: group welcome + personal /start
def test_a_new_member_of_the_signal_group_gets_one_welcome_with_a_start_button() -> None:
    from app.telegram.ingestion import new_members_in_signal_group

    upd = {"message": {"chat": {"id": -1001, "username": "botpricechat", "type": "supergroup"},
                       "new_chat_members": [{"id": 5, "first_name": "Ana"}, {"id": 6, "first_name": "spam", "is_bot": True}]}}
    assert new_members_in_signal_group(upd, "botpricechat") == (-1001, ["Ana"])  # bots are never welcomed
    assert new_members_in_signal_group(upd, "someothergroup") is None
    text, btns = bot.welcome_group(["Ana"], "cryptopricechatbot")
    assert "Ana" in text and btns[0][0]["url"] == "https://t.me/cryptopricechatbot?start=welcome"


def test_private_start_explains_how_it_works_and_shows_this_members_own_setup(db: Session, md: FakeMD) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, 991, 991, "/start welcome"))
    assert "How it works" in tg.sent[-1][2] and "link this Telegram" in tg.sent[-1][2]  # not linked yet
    m = kiwi(db, 4, "992")
    asyncio.run(bot.handle_command(db, tg, 992, 992, "/start"))
    out = tg.sent[-1][2]
    assert "Mode: <b>PAPER</b>" in out and "API wallet: not yet" in out and "AUTO: off" in out and "tap ENTER" in out
    assert "private key" in out  # the safety line, every time
    from app.tradingos import accounts

    accounts.update(db, m, {"auto_enabled": True, "auto_hours": 4})
    asyncio.run(bot.handle_command(db, tg, 992, 992, "/start"))
    assert "AUTO trades new signals" in tg.sent[-1][2] and "AUTO: ON" in tg.sent[-1][2]



def test_website_signal_carries_its_real_trigger_close_times_and_tp2(db: Session, md: FakeMD) -> None:
    """Kiwi's chat on the website says "triggered" / "closed" only from these recorded times, and shows the engine's TP2."""
    from app.api.routes.tradingos import sig_out
    from app.tradingos import risk

    sig = signals.create(db, signals.analyze(md, "SOL"), "1h", "WAITING", "test")
    db.commit()
    out = sig_out(db, sig)
    assert out["activated_at"] is None and out["closed_at"] is None  # nothing happened yet: nothing to say
    assert out["tp2"] == risk.tp2_of((sig.entry_lo + sig.entry_hi) / 2, sig.target)


def breakout_4h(n: int = 120) -> list[Candle]:
    """TEST FIXTURE: 4h candles, a gently rising range (EMA50 below price), then the last CLOSED candle closes far above the
    previous 20 highs: a fresh breakout in the trend direction."""
    span = 4 * 3_600_000
    t0, out = int(time.time() * 1000) - (n + 1) * span, []
    for i in range(n - 1):
        mid = 95 + i * 0.05
        o, c = mid - 0.4 if i % 2 else mid + 0.4, mid + 0.4 if i % 2 else mid - 0.4
        out.append(Candle(t0 + i * span, o, mid + 1, mid - 1, c, 1000))
    last = out[-1].c
    out.append(Candle(t0 + (n - 1) * span, last, last + 4.2, last - 0.2, last + 4, 3000))
    return out


def test_breakout_4h_v1_fires_only_on_a_fresh_closed_breakout_with_the_trend(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    md = FakeMD({"BRK": breakout_4h(), "FLAT": flat()})
    monkeypatch.setattr(market, "_shared", md)
    a = signals.analyze_breakout(md, "BRK")
    assert a.decision == "LONG" and "4h breakout" in a.reasons[0] and a.indicators["strategy"] == "breakout_4h_v1"
    assert a.stop < a.entry[0] and a.target > a.entry[1] and a.rr >= 1.6  # stop 2 ATR below, target 2R above
    assert signals.analyze_breakout(md, "FLAT").decision == "NO TRADE"
    s = signals.settings(db)
    s.strategies, s.auto_publish, s.allowed_coins = {"trend_pullback_v1": False, "breakout_4h_v1": True}, True, "BRK,FLAT"
    db.commit()
    out = signals.scan(db, md)
    sig = db.scalar(select(TsSignal).where(TsSignal.number == out["created"][0]))
    assert sig.strategy == "breakout_4h_v1" and sig.timeframe == "4h" and sig.side == "LONG"
    assert "no fresh 4h breakout" in " ".join(out["no_trade"]["FLAT"])  # every NO TRADE says why


def test_expired_signals_record_when_they_closed(db: Session, md: FakeMD) -> None:
    """The website's Kiwi chat says "expired" / "cancelled" only from the recorded close time: it must be set."""
    from datetime import timedelta

    from app.db.base import utcnow

    sig = signals.create(db, signals.analyze(md, "SOL"), "1h", "WAITING", "test")
    sig.entry_lo, sig.entry_hi = 1.0, 1.1  # never reached
    sig.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    signals.track(db, md)
    assert sig.status == "EXPIRED" and sig.closed_at is not None


def test_members_never_see_finished_pipeline_check_signals(db: Session, md: FakeMD) -> None:
    from app.api.routes.tradingos import site_signals

    real = signals.create(db, signals.analyze(md, "SOL"), "1h", "WAITING", "test")
    check = signals.test_signal(db, md, "BTC", "admin")
    real.published_at = check.published_at = real.created_at
    db.commit()
    ids = [x["id"] for x in site_signals(db)["items"]]
    assert real.id in ids and check.id in ids  # an open check is shown (labelled TEST) so the owner can test with it
    check.status = "CANCELLED"
    db.commit()
    assert check.id not in [x["id"] for x in site_signals(db)["items"]]  # finished checks never clutter members' feed
