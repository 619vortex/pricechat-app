"""Market data words (owner, 2026-11-19: "distinguish intraday price, daily change, official close and settlement. Never call an intraday
quote a closing price. Validate percentages against the correct timestamp and reference price").

The real case: Telegram 194 (2026-11-18 20:00 UTC) said gold "closed at 3,724.8" while the COMEX session was still trading; the
session's final value was 3,741.9. The payloads below are Yahoo Finance chart metadata as it came back on 2026-11-19."""
from datetime import datetime, timedelta, timezone
from typing import Any

from app.newsdesk import datapost

T = lambda s: datetime.fromtimestamp(s, tz=timezone.utc)  # noqa: E731


def payload(instrument: str, market_time: int, start: int, end: int, price: float) -> dict[str, Any]:
    return {"chart": {"result": [{"meta": {"instrumentType": instrument, "regularMarketTime": market_time, "regularMarketPrice": price,
                                           "currentTradingPeriod": {"regular": {"start": start, "end": end}}}}]}}


def series(prev: float, last: float, last_at: datetime, n: int = 130) -> list[tuple[datetime, float]]:
    out, p = [], prev
    for i in range(n - 2, 0, -1):
        p = prev * (1 + 0.01 * ((i % 5) - 2) / 5)
        out.append((last_at - timedelta(days=i + 1), p))
    out.append((last_at - timedelta(days=1), prev))
    out.append((last_at, last))
    return out


def test_gold_on_18_nov_at_20_utc_was_a_live_price_not_a_close() -> None:
    # COMEX session 18 Nov (Yahoo: regular start 23:00 UTC the day before, end 21:59 UTC); the price read at 20:00 UTC
    at = datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)
    s = series(3864.2, 3724.8, at.replace(hour=5))
    last = datapost.last_of(payload("FUTURE", int(at.timestamp()), int(at.replace(hour=0).timestamp()) - 3600, int(at.replace(hour=21, minute=59).timestamp()), 3724.8), s)
    assert last is not None and last.kind == "intraday"
    words = last.words("gold", "GC=F")
    assert words == "Gold at $3,725 at 20:00 UTC, −3.6% from the previous close ($3,864), with the session still trading."
    assert "closed" not in words and "settle" not in words
    p = datapost.anomaly("GC=F", s, at + timedelta(minutes=5), last)
    if p is not None:  # the move clears the bar on this synthetic history: then its words say "so far today"
        assert "so far today" in p.headline and "closed at" not in p.told and p.last["kind"] == "intraday"


def test_a_session_that_has_ended_is_a_close_for_stocks_and_a_session_end_for_futures() -> None:
    # ^GSPC on 19 Nov 05:25 UTC: last trade 18 Nov 21:57 UTC, the next session starts 19 Nov 14:30 UTC
    at = T(1795039045)
    s = series(7619.38, 7323.63, at)
    index = datapost.last_of(payload("INDEX", 1795039045, 1795098600, 1795122000, 7323.63), s)
    assert index is not None and index.kind == "close" and index.words("the S&P 500", "^GSPC").startswith("The S&P 500 closed at 7,324, −3.9% from the previous close")
    fut = datapost.last_of(payload("FUTURE", 1795039045, 1795098600, 1795122000, 7323.63), s)
    assert fut is not None and fut.kind == "session"
    assert fut.words("gold", "GC=F").startswith("Gold ended the session at") and "settle" not in fut.words("gold", "GC=F")  # never a settlement we didn't read


def test_crypto_never_closes_so_its_day_is_counted_from_00_utc() -> None:
    at = T(1795065868)  # BTC-USD 19 Nov 05:24 UTC
    s = series(65305.06, 63403.63, at)
    last = datapost.last_of(payload("CRYPTOCURRENCY", 1795065868, 1795046400, 1795132740, 63403.63), s)
    assert last is not None and last.kind == "utc_day"
    assert last.words("Bitcoin", "BTC-USD") == "Bitcoin at $63,404 at 05:24 UTC, −2.9% since the 00:00 UTC daily open ($65,305)."


def test_a_quote_that_disagrees_with_its_bar_or_missing_metadata_makes_no_claim() -> None:
    at = T(1795065301)
    s = series(3853.0, 3729.9, at)
    assert datapost.last_of(payload("FUTURE", 1795065301, 1795064400, 1795150740, 3790.0), s) is None  # 1.6% apart
    assert datapost.last_of({"chart": {"result": [{}]}}, s) is None


def test_a_live_figure_is_not_posted_once_it_is_old() -> None:
    at = datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)
    post = {"last": {"kind": "intraday", "price": 3724.8, "prev": 3864.2, "at": at.isoformat()}}
    assert datapost.fresh_problem(post, at + timedelta(minutes=15)) is None
    # one freshness limit on every path since 2026-11-21 (app/newsdesk/markethours.FRESH, 20 min; data posts allowed 90 before)
    assert datapost.fresh_problem(post, at + timedelta(minutes=30)) == "its live price is from 20:00 UTC, 30 min ago"
    assert datapost.fresh_problem(post, at + timedelta(hours=3)) == "its live price is from 20:00 UTC, 180 min ago"
    assert datapost.fresh_problem({"last": {**post["last"], "kind": "close"}}, at + timedelta(days=1)) is None  # a close stays a close


def test_the_daily_change_is_measured_against_the_previous_bar() -> None:
    last = datapost.Last("close", 110.0, 100.0, T(1795039045))
    assert round(last.change, 6) == 10.0 and "+10.0% from the previous close ($100.00)" in last.words("Apple", "AAPL")
