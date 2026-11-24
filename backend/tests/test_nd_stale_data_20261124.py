"""Strict stale market data rejection (owner, 2026-11-24): a day's change is published only when the daily bars are the price's own
session and the one right before it. The cases are production's: silver 880 (11-20 01:40 UTC, measured from 11-18's close) and bitcoin
1046 (11-21 04:03 UTC, measured from 11-20's open)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.newsdesk import datapost, livedata, markethours

UTC = timezone.utc


def bars(days: list[str], hour: int, closes: list[float]) -> list[tuple[datetime, float]]:
    return [(datetime.fromisoformat(d).replace(hour=hour, tzinfo=UTC), c) for d, c in zip(days, closes)]


def history(last_days: list[str], last_closes: list[float], hour: int, n: int = 130) -> list[tuple[datetime, float]]:
    first = datetime.fromisoformat(last_days[0]).replace(hour=hour, tzinfo=UTC)
    old = [(first - timedelta(days=i), last_closes[0] * (1 + 0.004 * ((i % 5) - 2))) for i in range(n, 0, -1)]
    return old + bars(last_days, hour, last_closes)


def test_futures_trade_in_the_next_day_s_session_after_18_new_york() -> None:
    # 01:40 UTC on 20 Nov is 20:40 in New York on the 19th, but CME is trading the 20 Nov session
    assert markethours.trading_day(datetime(2026, 11, 20, 1, 40, tzinfo=UTC), "commodity", "SI=F") == date(2026, 11, 20)
    assert markethours.trading_day(datetime(2026, 11, 19, 20, 0, tzinfo=UTC), "commodity", "SI=F") == date(2026, 11, 19)
    # Sunday evening's open belongs to Monday; a stock's price is its New York date; crypto's is the UTC date
    assert markethours.trading_day(datetime(2026, 11, 22, 23, 30, tzinfo=UTC), "commodity", "GC=F") == date(2026, 11, 23)
    assert markethours.trading_day(datetime(2026, 11, 20, 20, 0, tzinfo=UTC), "stock", "NVDA") == date(2026, 11, 20)
    assert markethours.trading_day(datetime(2026, 11, 21, 4, 3, tzinfo=UTC), "crypto_major", "BTC-USD") == date(2026, 11, 21)


def test_the_previous_session_skips_weekends_and_exchange_holidays() -> None:
    assert markethours.previous_trading_day(date(2026, 11, 23), "commodity", "GC=F") == date(2026, 11, 20)  # Monday -> Friday
    assert markethours.previous_trading_day(date(2026, 11, 27), "stock", "NVDA") == date(2026, 11, 25)  # Thanksgiving 26 Nov
    assert markethours.previous_trading_day(date(2026, 11, 23), "crypto_major", "BTC-USD") == date(2026, 11, 22)  # crypto has weekends


def test_silver_880_its_previous_close_was_two_days_old() -> None:
    # Yahoo's newest bar was still dated 19 Nov (its close overwritten by the live 41.99), the one before it 18 Nov's 45.00
    s = history(["2026-11-18", "2026-11-19"], [45.00, 41.985], hour=5)
    last = datapost.Last("intraday", 41.985, 45.00, datetime(2026, 11, 20, 1, 40, tzinfo=UTC), "FUTURE")
    why = datapost.reference_problem("SI=F", s, last)
    assert why and "belongs to the 20 Nov session" in why and "a day old" in why
    assert datapost.anomaly("SI=F", s, datetime(2026, 11, 20, 1, 45, tzinfo=UTC), last) is None  # never the -6.7% post


def test_silver_with_the_right_bars_is_measured() -> None:
    s = history(["2026-11-19", "2026-11-20"], [44.32, 41.55], hour=5)
    last = datapost.Last("intraday", 41.55, 44.32, datetime(2026, 11, 20, 14, 0, tzinfo=UTC), "FUTURE")
    assert datapost.reference_problem("SI=F", s, last) is None


def test_bitcoin_1046_its_daily_open_was_a_day_old() -> None:
    s = history(["2026-11-19", "2026-11-20"], [61902.0, 56270.0], hour=0)
    last = datapost.Last("utc_day", 56270.0, 61902.0, datetime(2026, 11, 21, 4, 3, tzinfo=UTC), "CRYPTOCURRENCY")
    why = datapost.reference_problem("BTC-USD", s, last)
    assert why and "20 Nov" in why and "21 Nov" in why
    ok = history(["2026-11-20", "2026-11-21"], [59240.0, 56270.0], hour=0)
    assert datapost.reference_problem("BTC-USD", ok, last) is None


def test_a_missing_session_between_the_bars_is_refused() -> None:
    # Friday's bar missing: Monday's price against Thursday's close is a two-day change
    s = history(["2026-11-19", "2026-11-23"], [100.0, 90.0], hour=14)
    last = datapost.Last("intraday", 90.0, 100.0, datetime(2026, 11, 23, 16, 0, tzinfo=UTC), "EQUITY")
    why = datapost.reference_problem("NVDA", s, last)
    assert why and "not the previous session (20 Nov)" in why


def test_a_stock_after_the_weekend_is_measured_against_friday() -> None:
    s = history(["2026-11-20", "2026-11-23"], [100.0, 90.0], hour=14)
    last = datapost.Last("intraday", 90.0, 100.0, datetime(2026, 11, 23, 16, 0, tzinfo=UTC), "EQUITY")
    assert datapost.reference_problem("NVDA", s, last) is None


@pytest.mark.asyncio
async def test_the_pre_send_reread_holds_rather_than_fading_on_stale_bars(monkeypatch) -> None:
    s = history(["2026-11-18", "2026-11-19"], [45.00, 41.985], hour=5)
    last = datapost.Last("intraday", 41.985, 45.00, datetime(2026, 11, 20, 1, 40, tzinfo=UTC), "FUTURE")

    async def fake_daily(symbol, transport):
        return s, last

    monkeypatch.setattr(livedata, "_fetch_daily", fake_daily)
    livedata.forget()

    class Ev:
        kind, facts, headline, title, summary = "data", {"data": {"symbols": ["SI=F"], "kind": "anomaly", "last": last.out()}}, "", "", ""

    status, why = await livedata._refresh_data(Ev(), datetime(2026, 11, 20, 1, 42, tzinfo=UTC), None)
    assert status == livedata.HOLD and "stale daily bars" in why
