"""Telegram bot (community feedback round): /chart options in any order, the study chart panels (and honest notes when data
is missing), /markets weekly, /signal answered in groups, /idea and /ideas, /help, unknown commands, Bot Chat routing,
the open-interest recorder and the signal performance report. No network: a fake Hyperliquid and a fake CoinGecko."""

import asyncio
import time
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsIdea, TsOiSnapshot, TsSignal
from app.tradingos import bot, chartcmd, ideas, indicators as ind, market, oi, performance, signals, weekly
from app.tradingos.market import Candle

from tests.test_trading_os import FakeMD, FakeTG, flat, uptrend_pullback


class StudyMD(FakeMD):
    """TEST FIXTURE: FakeMD plus funding history and book levels."""

    def funding_history(self, coin: str, hours: int = 168) -> list[tuple[int, float]]:
        now = int(time.time() * 1000)
        return [(now - h * 3_600_000, 0.00001 if h % 3 else -0.000005) for h in range(min(hours, 336), 0, -1)]

    def levels(self, coin: str, sig_figs: int = 5) -> dict[str, list[tuple[float, float]]]:
        px = self.series[self.market(coin).coin][-1].c
        return {"bids": [(px * (1 - i / 1000), 10.0 + i) for i in range(1, 20)], "asks": [(px * (1 + i / 1000), 8.0 + i) for i in range(1, 20)]}


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> StudyMD:
    f = StudyMD({"BTC": uptrend_pullback(400), "ETH": uptrend_pullback(400, 50.0), "SOL": uptrend_pullback(400, 20.0), "FLAT": flat(400)})
    monkeypatch.setattr(market, "_shared", f)
    weekly._cache.clear()
    return f


# ---------------------------------------------------------------- /chart parsing
def test_chart_words_in_any_order() -> None:
    r = chartcmd.parse(["15M", "BB", "BTC", "OI", "FUNDING"])
    assert (r.coin, r.tf, r.opts) == ("BTC", "15m", {"bb", "oi", "funding"})
    assert chartcmd.parse(["BTC", "15", "RSI", "WIDE"]).tf == "15m" and chartcmd.parse(["BTC", "15", "WIDE"]).wide  # "fc btc 15" style
    assert chartcmd.parse(["$ETH"]).tf == "1h" and chartcmd.parse(["ETHUSDT", "4H"]).coin == "ETH"
    assert chartcmd.parse(["BTC", "1M", "DEPTH", "OI"]).opts == {"depth", "oi"}
    assert chartcmd.parse(["W", "1D"]).coin == "W"  # W is a coin, not a "wide" shortcut


@pytest.mark.parametrize("words,needle", [([], "Usage"), (["BTC", "ETH"], "Two coins"), (["BTC", "1H", "4H"], "Two timeframes"),
                                          (["BTC", "1H", "ichimoku!"], "didn't understand: ichimoku!")])
def test_chart_usage_errors_explain(words: list[str], needle: str) -> None:
    with pytest.raises(chartcmd.ChartUsageError) as e:
        chartcmd.parse([w.upper() if w.isalnum() else w for w in words])
    assert needle.lower() in str(e.value).lower() and "Options:" in str(e.value)


def test_indicator_series_match_the_last_values() -> None:
    closes = [c.c for c in uptrend_pullback(120)]
    assert abs(ind.rsi_series(closes)[-1] - ind.rsi(closes)) < 1e-9  # type: ignore[operator]
    line, sig = ind.macd_series(closes)
    m = ind.macd(closes)
    assert m and abs(line[-1] - m[0]) < 1e-9 and abs(sig[-1] - m[1]) < 1e-9  # type: ignore[operator]
    assert ind.bollinger_series(closes)[-1] == ind.bollinger(closes) and ind.bollinger_series(closes)[5] is None


# ---------------------------------------------------------------- the study chart
def test_every_panel_draws_and_missing_oi_is_explained(db: Session, md: StudyMD) -> None:
    png, cap = chartcmd.build(db, md, chartcmd.parse(["BTC", "1H", "RSI", "BB", "MACD", "MA", "VOLUME", "FUNDING", "OI", "DEPTH", "WIDE"]))
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20_000
    assert "rsi · bb · macd · ma · volume · funding · depth" in cap and "No OI chart yet" in cap and "OI now $" in cap


def test_oi_panel_uses_price_chats_own_record(db: Session, md: StudyMD) -> None:
    now = utcnow()
    for h in range(0, 100, 4):  # hourly chart covers 120 h: snapshots inside the window
        db.add(TsOiSnapshot(at=now - timedelta(hours=h), coin="BTC", mark=100, oi_usd=1e9 + h * 1e6, funding=0.00001))
    db.commit()
    _, cap = chartcmd.build(db, md, chartcmd.parse(["BTC", "1H", "OI"]))
    assert "Layers: oi" in cap and "No OI chart" not in cap


def test_oi_recorder_keeps_the_top_markets_and_prunes(db: Session, md: StudyMD) -> None:
    db.add(TsOiSnapshot(at=utcnow() - timedelta(days=40), coin="BTC", mark=1, oi_usd=1, funding=0))
    db.commit()
    out = oi.record(db, md)
    assert out == {"recorded": 4, "pruned": 1}
    assert {r.coin for r in db.scalars(select(TsOiSnapshot))} == {"BTC", "ETH", "SOL", "FLAT"}
    assert oi.change(db, "BTC", 7) is None  # one snapshot can't make a weekly change
    for d in (7.1, 4):
        db.add(TsOiSnapshot(at=utcnow() - timedelta(days=d), coin="ETH", mark=1, oi_usd=100.0 if d > 7 else 150.0, funding=0))
    db.commit()
    ch = oi.change(db, "ETH", 7)
    assert ch and ch[0] == 100.0 and ch[2] > 0


# ---------------------------------------------------------------- the commands
def test_chart_command_replies_with_options_and_errors(db: Session, md: StudyMD) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/chart BTC 15m bb oi funding", group=True, reply_to=3))
    assert tg.sent[-1][0] == "photo" and "<b>$BTCUSD</b> | 15m | Hyperliquid Perpetual" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/chart BTC 15m sparkles", group=True, reply_to=4))
    assert "didn't understand: sparkles" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/chart BTC 5m"))  # the old form still works
    assert tg.sent[-1][0] == "photo"


def test_signal_is_answered_in_groups(db: Session, md: StudyMD) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal FLAT", group=True, reply_to=3))
    flat = tg.sent[-1]  # no setup passes the rules: still the best plan the chart offers, as a WATCH idea with its chart
    assert flat[0] == "photo" and "WATCH · LONG idea" in flat[2] and "Not a full signal" in flat[2] and "privately" not in flat[2]
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal SOL", group=True, reply_to=4))
    assert tg.sent[-1][0] == "photo" and "not a published signal" in tg.sent[-1][2]
    assert db.scalar(select(TsSignal)) is None  # analysis only: nothing is published or traded
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/positions", group=True, reply_to=5))
    assert "privately" in tg.sent[-1][2]  # account commands still never answer in groups


def test_help_is_organised_and_unknown_commands_are_handled(db: Session, md: StudyMD) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/help"))
    text = tg.sent[-1][2]
    for head in ("Market data", "Charts", "Technical analysis", "Community ideas", "Trading bot"):
        assert head in text
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/moon"))
    assert "I don't know /moon" in tg.sent[-1][2] and "/help" in tg.sent[-1][2]
    n = len(tg.sent)
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/ban", group=True, reply_to=7))  # another bot's command in a group: silence
    assert len(tg.sent) == n


def test_bot_chat_routing_moves_main_chat_charts(db: Session, md: StudyMD) -> None:
    s = signals.settings(db)
    s.main_chat, s.publish_chat = "@thepricechat", "@botpricechat"
    db.commit()
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -100, 9, "/chart BTC 1h", group=True, reply_to=3, chat_username="thepricechat"))
    assert tg.sent[-1][0] == "photo" and tg.sent[-1][1] == -100  # default "anywhere": answered where asked, as before
    s.chart_routing = "bot_chat"
    db.commit()
    asyncio.run(bot.handle_command(db, tg, -100, 9, "/chart BTC 1h", group=True, reply_to=4, chat_username="thepricechat"))
    assert tg.sent[-2][0] == "photo" and tg.sent[-2][1] == "@botpricechat"
    assert tg.sent[-1][1] == -100 and "Bot Chat" in tg.sent[-1][2] and "t.me/botpricechat" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, -200, 9, "/chart BTC 1h", group=True, reply_to=5, chat_username="botpricechat"))
    assert tg.sent[-1][0] == "photo" and tg.sent[-1][1] == -200  # in Bot Chat itself: answered there
    asyncio.run(bot.handle_command(db, tg, -100, 9, "/price BTC", group=True, reply_to=6, chat_username="thepricechat"))
    assert tg.sent[-1][1] == -100 and "BTC" in tg.sent[-1][2]  # text answers never move


# ---------------------------------------------------------------- /markets weekly
def gecko(cap: float = 2.7e12, vol: float = 9e10) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"total_market_cap": {"usd": cap}, "total_volume": {"usd": vol},
                                                  "market_cap_change_percentage_24h_usd": -1.25, "market_cap_percentage": {"btc": 60.2},
                                                  "updated_at": int(time.time())}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_weekly_summary_uses_real_numbers_and_says_what_is_missing(db: Session, md: StudyMD) -> None:
    text = weekly.build(md, db, http=gecko())
    assert "Weekly market summary" in text and "<b>BTC</b>" in text and "<b>ETH</b>" in text and "7d" in text
    assert "Total market cap $2.70T (-1.2% 24h)" in text and "24h volume $90.0B" in text and "BTC dominance 60.2%" in text
    assert "Funding" in text and "this week vs last" in text
    assert "Weekly change not available yet" in text  # no OI week recorded: said, not invented
    assert "Headlines aren't included" in text
    weekly._cache.clear()
    assert "sanity check" in weekly.build(md, db, http=gecko(vol=4.7e13))  # CoinGecko's bad volume figure is left out


def test_markets_weekly_command_keeps_plain_markets(db: Session, md: StudyMD, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weekly, "coingecko_global", lambda http=None: None)
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/markets weekly"))
    assert "Weekly market summary" in tg.sent[-1][2] and "CoinGecko didn't answer" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, 1, 9, "/markets"))
    assert "market overview" in tg.sent[-1][2]


# ---------------------------------------------------------------- /idea
def test_idea_parse_and_rules() -> None:
    d = ideas.parse(["BTC", "long", "tp", "72k", "inv", "64,000", "Breakout", "retest", "holding"])
    assert (d.coin, d.side, d.target, d.invalidation, d.note) == ("BTC", "LONG", 72000.0, 64000.0, "Breakout retest holding")
    assert ideas.parse(["short", "eth"]).side == "SHORT"
    for bad, msg in ((["BTC"], "Share a trading idea"), (["BTC", "moon"], "long or short"), (["BTC", "long", "see", "https://x.y"], "links")):
        with pytest.raises(ideas.IdeaError, match=msg):
            ideas.parse(bad)


def test_ideas_are_stored_apart_from_signals_and_limited(db: Session, md: StudyMD) -> None:
    tg = FakeTG()
    px = md.series["BTC"][-1].c
    asyncio.run(bot.handle_command(db, tg, -5, 42, f"/idea BTC long tp {px * 1.1:.2f} inv {px * 0.95:.2f} trend intact", group=True,
                                   reply_to=1, author="Satoshi"))
    assert "💡 <b>BTC LONG</b> idea by Satoshi" in tg.sent[-1][2] and "not a Price Chat signal" in tg.sent[-1][2]
    asyncio.run(bot.handle_command(db, tg, -5, 42, f"/idea BTC long tp {px * 0.5:.2f}", group=True, reply_to=2, author="Satoshi"))
    assert "target should be above" in tg.sent[-1][2]
    for i in range(2):
        asyncio.run(bot.handle_command(db, tg, -5, 42, "/idea ETH short", group=True, reply_to=3 + i, author="Satoshi"))
    asyncio.run(bot.handle_command(db, tg, -5, 42, "/idea SOL long", group=True, reply_to=9, author="Satoshi"))
    assert "3 ideas in 24 hours" in tg.sent[-1][2]
    assert len(db.scalars(select(TsIdea)).all()) == 3 and db.scalar(select(TsSignal)) is None  # never a signal
    asyncio.run(bot.handle_command(db, tg, -5, 43, "/ideas", group=True, reply_to=10))
    assert "Community ideas" in tg.sent[-1][2] and "Since shared" in tg.sent[-1][2] and tg.sent[-1][2].count("💡") == 3
    asyncio.run(bot.handle_command(db, tg, -5, 43, "/ideas btc", group=True, reply_to=11))
    assert tg.sent[-1][2].count("💡") == 1


# ---------------------------------------------------------------- signal performance report
def mk(db: Session, n: int, status: str, pnl: float | None, strategy: str = "intraday_15m_v1", tf: str = "15m", **kw: Any) -> TsSignal:
    now = utcnow()
    s = TsSignal(number=n, coin="SOL", side="LONG", timeframe=tf, strategy=strategy, entry_lo=99.5, entry_hi=100.5, stop=99.0, target=102.0,
                 lev_lo=1, lev_hi=3, rr=2, status=status, expires_at=now + timedelta(hours=1), published_at=now - timedelta(hours=3),
                 activated_at=kw.get("activated_at", now - timedelta(hours=2)), closed_at=now - timedelta(hours=1) if pnl is not None else None,
                 exit_price=kw.get("exit_price", 101.0 if pnl is not None else None), model_pnl_pct=pnl, last_price=kw.get("last_price"),
                 last_checked_at=kw.get("last_checked_at", now))
    db.add(s)
    return s


def test_performance_report_counts_costs_and_flags_problems(db: Session) -> None:
    mk(db, 1, "TARGET_HIT", 1.9)
    mk(db, 2, "STOP_HIT", -1.1)
    mk(db, 3, "STOP_HIT", -1.1)
    mk(db, 4, "EXPIRED", None, activated_at=None)
    mk(db, 5, "ACTIVE", None, last_price=101.0, last_checked_at=utcnow() - timedelta(minutes=30))
    mk(db, 6, "TARGET_HIT", 3.0, strategy="test")  # test signals never count
    db.commit()
    r = performance.report(db)
    o = r["overall"]
    assert o["signals"] == 5 and o["closed"] == 3 and o["win_rate"] == 33.3 and o["avg_pct"] == round(-0.3 / 3, 3)
    assert o["avg_r"] == pytest.approx((1.9 - 1.1 - 1.1) / 3, abs=1e-3) and o["profit_factor"] == round(1.9 / 2.2, 2)
    assert o["avg_pct_after_slippage"] < o["avg_pct"] and o["slippage_pct_per_trade"] == 0.1  # SOL: 0.05% a side
    assert o["expiry_rate"] == 20.0 and o["open"] == 1 and o["unrealized_pct"] == pytest.approx(1.0 - 0.09, abs=1e-6)
    assert o["data_quality"]["stale_open_signals"] == [5]
    f = " ".join(r["findings"])
    assert "Too few to judge" in f and "#005" in f and "loses" in f
    assert any("Keep collecting" in x for x in r["recommendations"]) and "out of sample" in r["backtest_reference"]
    assert "intraday_15m_v1 15m" in performance.text(r)


def test_fc_by_name(db: Session, md: StudyMD) -> None:
    assert bot.fc_command("FC gold") == "/fc gold" and bot.fc_command(" fc $Bitcoin ") == "/fc Bitcoin"
    assert bot.fc_command("fc") is None and bot.fc_command("fc gold now please") is None and bot.fc_command("fcgold") is None
    assert bot.coin_name("gold") == "PAXG" and bot.coin_name("bitcoin") == "BTC" and bot.coin_name("doge") == "DOGE"
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -100, 9, "/fc bitcoin", group=True, reply_to=8))
    assert tg.sent[-1][0] == "photo" and tg.sent[-1][1] == -100 and "BTC" in tg.sent[-1][2] and "4h" in tg.sent[-1][2].lower()
    asyncio.run(bot.handle_command(db, tg, -100, 9, "/price bitcoin", group=True, reply_to=9))
    assert tg.sent[-1][2].startswith("<b>$BTC</b>  ")  # "bitcoin" -> BTC: just the price and the 24h move


def test_signal_without_a_full_setup_still_suggests_the_best_one() -> None:
    from types import SimpleNamespace

    def a(**ind: float) -> signals.Analysis:
        return signals.Analysis(coin="X", decision="NO TRADE", reasons=["RSI 74 out of range for a long"], indicators=ind)

    up = signals.watch_setup(a(price=110, ema20=105, ema50=100, atr=2, support=95, resistance=112))
    assert up and up.side == "LONG" and up.entry == (104.6, 105.4) and up.stop == 102 and up.target == 111  # pullback to EMA20, 1.5 ATR, 2R
    down = signals.watch_setup(a(price=90, ema20=95, ema50=100, atr=2, support=89, resistance=104))
    assert down and down.side == "SHORT" and down.stop > max(down.entry) > min(down.entry) > down.target  # pressing support: the breakdown wins
    rng = signals.watch_setup(a(price=101, ema20=100, ema50=100.5, atr=1, support=99, resistance=106))
    assert rng and rng.side == "LONG" and rng.stop < 99  # no trend: the nearer range edge, stop past the swing low
    mom = signals.watch_setup(a(price=100, macd_hist=-0.5))  # no trend, no levels: momentum decides, ATR falls back to 1.5%
    assert mom and mom.side == "SHORT" and mom.stop == 102.25
    assert signals.watch_setup(a(price=100)) and signals.watch_setup(a()) is None  # only no price at all means WAIT
    listing = SimpleNamespace(tag="$XUSD", label="Hyperliquid Perpetual", change_24h=1.0)
    text = bot.signal_text(listing, "1h", a(price=110, ema20=105, ema50=100, atr=2, support=95, resistance=112), [])
    assert "WATCH · LONG idea" in text and "Wait for" in text and "Not a full signal: RSI 74" in text


def test_signal_without_a_coin_answers_with_the_most_promising_market(db: Session, md: StudyMD) -> None:
    tg = FakeTG()
    asyncio.run(bot.handle_command(db, tg, -5, 9, "/signal", group=True, reply_to=3))
    assert tg.sent[-1][0] == "photo" and "Most promising now" in tg.sent[-1][2] and "Entry" in tg.sent[-1][2]
    assert db.scalar(select(TsSignal)) is None  # analysis only: nothing is published or traded
