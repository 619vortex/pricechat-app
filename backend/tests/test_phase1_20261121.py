"""Phase 1 of the engine roadmap (owner, 2026-11-21): plausibility limits and the automatic safety stop, an independent second market,
one set of market-hours and freshness rules, Telegram corrections with formatting, and replay validation on real market history.

The replay data is real: tests/newsdesk_plausibility_calibration_20261121.json (Yahoo Finance: 2 years of daily closes and the largest
15-minute..24-hour moves of the last 60 days for every watched instrument), tests/price_updates_yahoo_20261121.json (posts 199 and 203)
and tests/newsdesk_silver_daily_20261120.json (the silver crash). No test reaches a real service.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.models.tradingos import TsLiquidation
from app.newsdesk import corrections, datapost, engine, events, livedata, markethours, moves, recap, safety, sources
from app.brand_manager import sources as bm_sources

CALIB = json.loads(Path(__file__).with_name("newsdesk_plausibility_calibration_20261121.json").read_text())
NOW = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)


# --- replay validation: no real move of the last two years trips a limit; real data errors do --------------------------------------

def test_every_real_move_in_the_calibration_data_is_well_inside_its_limit() -> None:
    for group, symbols in CALIB["max_moves"].items():
        for symbol, worst in symbols.items():
            for window, pct in worst.items():
                assert pct * 1.5 <= safety.limit(group, window), (symbol, window, pct, safety.limit(group, window))


def test_every_real_daily_close_of_two_years_passes_and_every_unit_error_is_caught() -> None:
    checked = 0
    for symbol, closes in CALIB["daily_closes"].items():
        group = markethours.group_of(symbol)
        for (_, a), (_, b) in zip(closes, closes[1:]):
            assert safety.plausible_move(group, "day", (b / a - 1) * 100) == "", (symbol, a, b)
            checked += 1
            # a price read in the wrong unit (cents, a contract count, a 100-share lot) is always held
            for factor in (100.0, 0.01):
                assert safety.plausible_move(group, "day", (b * factor / a - 1) * 100), (symbol, factor)
    assert checked > 9000


def test_the_real_market_events_of_this_week_still_go_through() -> None:
    from tests.test_price_updates_20261121 import CLOSE_1120, T199, T203, wide_at

    for t in (T203, T199, CLOSE_1120):  # Telegram 203, 199 and the 11-20 close: measured, not held
        assert wide_at(t).figures
    assert safety._pending == []
    assert safety.plausible_move("stock", "day", -14.0) == ""      # Nvidia's -14.0% on 2026-11-20 (X 145)
    assert safety.plausible_move("commodity", "day", -6.75) == ""  # silver, 2026-11-20
    assert safety.plausible_move("commodity", "24h", -6.4) == ""


# --- a broken figure is held, recorded, reported; enough of them stop market posts -------------------------------------------------

def broken_bitcoin(now: datetime) -> sources.Quote:
    """Five-minute bars flat at 60,000, the newest one read 100 times too low (a unit error in the feed): "-99% in 15 minutes"."""
    t = now.timestamp()
    bars = [(int(t - 49 * 3600 + i * 300), 60000.0, 1.0) for i in range(49 * 12)] + [(int(t), 600.0, 1.0)]
    return sources.Quote("BTC-USD", 600.0, now, None, None, bars)


def test_an_implausible_move_is_never_measured_and_is_held() -> None:
    item = next(w for w in sources.DEFAULT_WATCHLIST if w["symbol"] == "BTC-USD")
    assert moves.measure(broken_bitcoin(NOW), item, NOW) is None
    held = [p for p in safety._pending if p["kind"] == "implausible_move"]
    assert held and held[0]["subject"] == "BTC-USD" and "beyond" in held[0]["why"]


async def test_held_figures_are_recorded_and_three_in_an_hour_pause_market_posts(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[tuple[str, str]] = []

    async def notify(db_: Session, key: str, text: str, **kw: Any) -> bool:
        told.append((key, text))
        return True

    import app.alerts

    monkeypatch.setattr(app.alerts, "notify_owner", notify)
    desk = engine.settings_row(db)
    desk.state = {"market_pause": False}  # the owner had resumed market posts
    db.commit()
    for i in range(3):
        safety.note("implausible_move", f"SYM{i}", "a broken bar", change=-99.0)
        await safety.settle(db, NOW + timedelta(minutes=i * 10))
    db.refresh(desk)
    assert desk.state["market_pause"] is True and desk.state["market_pause_reason"].startswith("automatic: 3 implausible")
    assert len(desk.state["anomalies"]) == 3 and events.market_paused(db)
    assert any(k == "newsdesk:safety:stop" and "SAFETY STOP" in t for k, t in told)
    assert sum(1 for k, _ in told if k.startswith("newsdesk:safety:implausible_move")) == 3
    out = engine.settings_out(desk)
    assert out["market_pause"] is True and out["market_pause_reason"].startswith("automatic") and len(out["held_figures"]) == 3


def test_two_held_figures_alone_never_stop_anything(db: Session) -> None:
    state = {"anomalies": [{"at": (NOW - timedelta(minutes=5)).isoformat()}, {"at": (NOW - timedelta(minutes=50)).isoformat()},
                           {"at": (NOW - timedelta(hours=3)).isoformat()}]}
    assert len(safety.recent(state, NOW)) == 2  # the 3-hour-old one is outside the window
    state["breaker_reset_at"] = (NOW - timedelta(minutes=10)).isoformat()
    assert len(safety.recent(state, NOW)) == 1  # the owner resumed 10 minutes ago: only what came after counts


def test_the_owners_resume_resets_the_safety_stop(client: Any, db: Session) -> None:
    desk = engine.settings_row(db)
    desk.state = {"market_pause": True, "market_pause_reason": "automatic: 3 implausible figures", "anomalies": []}
    db.commit()
    r = client.put("/api/x/newsdesk/settings", json={"market_pause": False})
    assert r.status_code == 200
    db.expire_all()
    s = engine.settings_row(db).state
    assert s["market_pause"] is False and "market_pause_reason" not in s and s.get("breaker_reset_at")


# --- liquidations against the coin's own history ----------------------------------------------------------------------------------

def liq_history(db: Session, coin: str, hourly_usd: float, hours: int = 72) -> None:
    start = int((NOW - timedelta(hours=hours + 1)).timestamp() * 1000)
    for h in range(hours):
        db.add(TsLiquidation(venue="binance", coin=coin, t=start + h * 3_600_000, side="long", px=57000.0, qty=hourly_usd / 57000.0, usd=hourly_usd))
    db.commit()


def test_an_hour_fifty_times_the_coins_typical_hour_is_held(db: Session) -> None:
    liq_history(db, "BTC", 2_000_000.0)
    t = int((NOW - timedelta(minutes=30)).timestamp() * 1000)
    db.add(TsLiquidation(venue="binance", coin="BTC", t=t, side="long", px=57000.0, qty=2000.0, usd=120_000_000.0))  # a 20x unit error on $6M
    db.commit()
    assert bm_sources.liquidation_topics(db, NOW) == []
    held = [p for p in safety._pending if p["kind"] == "implausible_liquidations"]
    assert held and "60 times BTC's typical recorded hour" in held[0]["why"]


def test_a_real_flush_against_the_same_history_goes_through(db: Session) -> None:
    liq_history(db, "BTC", 2_000_000.0)
    t = int((NOW - timedelta(minutes=30)).timestamp() * 1000)
    db.add(TsLiquidation(venue="binance", coin="BTC", t=t, side="long", px=57000.0, qty=300.0, usd=17_500_000.0))  # post 204's real size
    db.commit()
    [topic] = bm_sources.liquidation_topics(db, NOW)
    assert topic.brief["numbers"]["liquidations_1h_usd"] == 17_500_000


def test_the_absolute_ceilings() -> None:
    assert safety.liquidation_problem("BTC", 10_100_000_000.0, []) .startswith("$10,100.0M in an hour is above the $500M ceiling")
    assert safety.liquidation_problem("PEPE", 200_000_000.0, []) != ""  # an altcoin's ceiling is lower
    assert safety.liquidation_problem("PEPE", 20_000_000.0, []) == ""


# --- one set of market-hours rules ----------------------------------------------------------------------------------------------

def test_a_session_is_named_as_the_session_it_was() -> None:
    fri_close = datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)
    assert markethours.day_word(fri_close, datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc), "stock") == "today"
    assert markethours.day_word(fri_close, datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc), "stock") == "yesterday"
    assert markethours.day_word(fri_close, datetime(2026, 11, 23, 13, 0, tzinfo=timezone.utc), "stock") == "on Friday"
    # 01:30 UTC Saturday is still Friday evening in New York: Friday's session is "today" there
    assert markethours.day_word(fri_close, datetime(2026, 11, 21, 1, 30, tzinfo=timezone.utc), "index") == "today"
    # crypto's day is the UTC day
    assert markethours.day_word(datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc), datetime(2026, 11, 21, 1, 0, tzinfo=timezone.utc), "crypto_major") == "yesterday"


def test_one_freshness_limit_on_every_path() -> None:
    assert moves.FRESH == 20 * 60 and livedata.LIVE_PRICE_MAX == markethours.FRESH and datapost.LIVE_MAX_AGE == markethours.FRESH


def nvidia_daily() -> list[tuple[datetime, float]]:
    rows = CALIB["daily_closes"]["NVDA"]
    return [(datetime.fromtimestamp(t, tz=timezone.utc), c) for t, c in rows]


def test_fridays_record_drop_read_on_monday_says_on_friday_never_today() -> None:
    series = nvidia_daily()
    at = series[-1][0].replace(hour=21, minute=0)  # the close of 2026-11-20 (Friday)
    last = datapost.Last("close", series[-1][1], series[-2][1], at, "EQUITY")
    same_evening = datapost.anomaly("NVDA", series, datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc), last)  # 18:00 in New York: today
    saturday = datapost.anomaly("NVDA", series, datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc), last)
    assert same_evening is not None and "today" in " ".join(same_evening.lines)
    post = saturday
    assert post is not None and "today" not in post.headline.lower() and ("yesterday" in post.headline or "on Friday" in post.headline)
    assert not any("today was" in x for x in post.lines)


def test_the_recap_measures_us_indices_against_the_previous_close() -> None:
    from tests.test_price_updates_20261121 import T203, quotes_at

    out = recap.rows({}, quotes_at(T203), T203)  # the market check's rows, as recap builds them (no crypto markets given)
    spx = next(r for r in out if r["name"] == "S&P 500")
    assert round(spx["pct"], 2) == round((6966.75 - 7175.83) / 7175.83 * 100, 2)  # -2.91% against Thursday's close, never a rolling 24 h


def test_a_52_week_extreme_is_stamped_with_its_price_time_not_the_desks_pass() -> None:
    t = NOW.timestamp()
    bars = [(int(t - 49 * 3600 + i * 300), 100.0, 1.0) for i in range(49 * 12 - 2)] + [(int(t - 600), 112.0, 1.0)]  # the newest bar: 10 min ago
    q = sources.Quote("TSLA", 112.0, NOW, 111.0, 80.0, bars)
    item = next(w for w in sources.DEFAULT_WATCHLIST if w["symbol"] == "TSLA")
    m = moves.extreme(q, item, NOW)
    assert m is not None and m.kind == "high" and m.as_of == NOW - timedelta(minutes=10)


# --- the independent second market ------------------------------------------------------------------------------------------------

def xyz(prices: dict[str, float]) -> Any:
    async def read(*_: Any, **__: Any) -> dict[str, tuple[float, float]]:
        return {k: (v, v) for k, v in prices.items()}
    return read


async def test_the_s_and_p_500_is_checked_against_trade_xyz(monkeypatch: pytest.MonkeyPatch) -> None:
    item = next(w for w in sources.DEFAULT_WATCHLIST if w["symbol"] == "^GSPC")
    monkeypatch.setattr(sources, "xyz_prices", xyz({"xyz:SP500": 6960.6}))
    ok, detail = await sources.second_opinion(item, 6962.99)
    assert ok and "trade.xyz SP500 6,960.60 vs Yahoo 6,962.99" in detail
    ok, detail = await sources.second_opinion(item, 7175.83)  # a stale or wrong Yahoo level, 3% off the live market
    assert not ok and "apart" in detail


async def test_no_second_market_means_one_source_said_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    other = {"symbol": "^N225", "name": "Nikkei", "group": "index"}  # no second market exists for it
    assert not sources.has_second(other)
    assert await sources.second_opinion(other, 38000.0) == (True, "one price source (Yahoo Finance)")
    # owner, 2026-11-23: a second market that can't be read is NOT verified (no automatic move without reliable verification), no longer "one
    # source, said so". The Dow and the Nasdaq Composite have no second source the desk may read: one source, so held (events.held_market)
    for sym in ("^DJI", "^IXIC"):
        assert not sources.has_second(next(w for w in sources.DEFAULT_WATCHLIST if w["symbol"] == sym))
    gold = next(w for w in sources.DEFAULT_WATCHLIST if w["symbol"] == "GC=F")
    ok, detail = await sources.second_opinion(gold, 3515.4)  # trade.xyz unreadable (conftest)
    assert not ok and detail.startswith("not verified: Hyperliquid trade.xyz couldn't be read")


async def test_a_combined_index_move_contradicted_by_the_second_market_is_not_offered(db: Session) -> None:
    from tests.test_price_updates_20261121 import T203, wide_at

    m = wide_at(T203)

    async def disagrees(item: dict[str, Any], price: float, market: Any = None) -> tuple[bool, str]:
        if item.get("symbol") != "^GSPC":  # any other member with a second market agrees
            return True, f"second market {price:,.2f} vs Yahoo {price:,.2f}: 0.00% apart"
        return False, f"Hyperliquid trade.xyz SP500 7,150.00 vs Yahoo {price:,.2f}: 2.60% apart"

    assert await events._offer_move(db, m, [], T203, {}, events.POLICY, disagrees, None, {}) == 0
    assert any(p["kind"] == "second_source" and p["subject"] == "^GSPC" for p in safety._pending)


async def test_at_sending_a_disagreement_holds_the_post(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_price_updates_20261121 import T203, wide_at

    async def disagrees(item: dict[str, Any], price: float, *a: Any, **kw: Any) -> tuple[bool, str]:
        if item.get("symbol") != "^GSPC":  # any other member with a second market agrees
            return True, f"second market {price:,.2f} vs Yahoo {price:,.2f}: 0.00% apart"
        return False, "Hyperliquid trade.xyz SP500 7,150.00 vs Yahoo 6,966.75: 2.63% apart"

    monkeypatch.setattr(livedata, "SECOND", disagrees)
    items = [w for w in sources.DEFAULT_WATCHLIST if w["group"] == "index"]
    why = await livedata.second_market(wide_at(T203), items)
    assert why.startswith("S&P 500 disagrees with an independent market")


# --- corrections keep Telegram's formatting --------------------------------------------------------------------------------------

def test_a_correction_written_with_telegram_formatting_is_sent_as_formatting() -> None:
    struck = "<s>JUST IN: BTCUSD_PERP longs liquidated: $10.1B on Binance in an hour.</s>\n\nCORRECTION: the $10.1B figure was wrong."
    assert corrections.tg_html(struck) and corrections.tg_length(struck) == len(struck) - len("<s></s>")
    assert corrections.tg_html('See <a href="https://t.me/Pricechatupdates/204">the post</a>, <b>corrected</b>.')
    assert not corrections.tg_html("Bitcoin < $60K and > $55K")       # a comparison, not markup: sent as written
    assert not corrections.tg_html("<s>unclosed")                     # broken markup: plain text, never a refused edit
    assert not corrections.tg_html("<script>x</script>")              # only Telegram's own tags


async def test_a_news_storys_price_tag_is_dropped_once_it_is_no_longer_the_market_now(db: Session) -> None:
    from app.models.newsdesk import NdEvent

    published = NOW - timedelta(hours=2)
    ev = NdEvent(key="k", kind="news", title="Bitcoin slides as ETF outflows mount", headline="BITCOIN SLIDES AS ETF OUTFLOWS MOUNT (BTC -5.4% IN 24H)",
                 summary="", topic="flows", label="", evidence="two outlets", category="crypto", sources=[],
                 facts={"price_tag": "BTC -5.4% IN 24H", "stat": "-5.4%", "published_at": published.isoformat()})
    state, detail = await livedata.refresh(db, ev, NOW)
    assert state == livedata.OK and "dropped" in detail
    assert ev.headline == "BITCOIN SLIDES AS ETF OUTFLOWS MOUNT" and ev.facts["price_tag"] == "" and ev.facts["tag_dropped"] == "BTC -5.4% IN 24H"
    fresh = NdEvent(key="k2", kind="news", title="Bitcoin slides", headline="BITCOIN SLIDES (BTC -5.4% IN 24H)", summary="", topic="flows", label="",
                    evidence="two outlets", category="crypto", sources=[], facts={"price_tag": "BTC -5.4% IN 24H", "published_at": (NOW - timedelta(minutes=20)).isoformat()})
    assert (await livedata.refresh(db, fresh, NOW))[1] == "" and fresh.headline.endswith("(BTC -5.4% IN 24H)")
