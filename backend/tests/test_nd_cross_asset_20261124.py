"""Cross-asset market context (owner, 2026-11-24): a market post says what the markets around it did over the same window, measured the
same way, from fresh quotes only, and never states a cause."""
from datetime import datetime, timedelta, timezone

from app.newsdesk import headline, livedata, moves, sources

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
ITEMS = {x["symbol"]: x for x in moves.watch_items(sources.DEFAULT_WATCHLIST)}


def quote(symbol: str, start: float, end: float, *, minutes: int = 120, last_at: datetime = NOW, prev_close: float | None = None) -> sources.Quote:
    """5-minute bars from `start` (minutes ago) to `end` (at last_at), a straight line."""
    n = minutes // 5
    bars = [(int((last_at - timedelta(minutes=5 * (n - i))).timestamp()), start + (end - start) * i / n, 0.0) for i in range(n + 1)]
    return sources.Quote(symbol, end, last_at, None, None, bars, previous_close=prev_close)


def btc_drop() -> moves.Move:
    return moves.Move("move", ITEMS["BTC-USD"], 57000.0, NOW, "1h", -5.0, 1.7, ref=60000.0, ref_at=NOW - timedelta(hours=1))


def test_a_bitcoin_drop_says_what_the_nasdaq_and_gold_did_in_the_same_hour() -> None:
    # each market's bars span exactly the hour (a stock's bar is read at its END: the Nasdaq's starts 5 minutes earlier)
    quotes = {"^IXIC": quote("^IXIC", 20000, 19700, minutes=65), "GC=F": quote("GC=F", 4000, 4024, minutes=60),
              "ETH-USD": quote("ETH-USD", 3000, 2790, minutes=60)}
    cross = moves.cross_asset(btc_drop(), quotes, NOW)
    got = {c["symbol"]: c["change"] for c in cross}
    assert set(got) == {"^IXIC", "GC=F", "ETH-USD"}
    assert round(got["^IXIC"], 1) == -1.5 and round(got["GC=F"], 1) == 0.6 and round(got["ETH-USD"], 1) == -7.0
    line = moves.cross_line(btc_drop(), cross)
    assert line == "In the same hour: Nasdaq −1.5%, Gold +0.6%, Ethereum −7.0%."
    assert not any(w in line.lower() for w in ("because", "as ", "after", "due to", "risk-off", "flight"))  # figures, never a cause


def test_a_market_that_is_shut_or_stale_is_left_out() -> None:
    # the Nasdaq's last bar is from the previous evening: it is not printed as if it moved this hour
    quotes = {"^IXIC": quote("^IXIC", 20000, 19700, last_at=NOW - timedelta(hours=17)), "GC=F": quote("GC=F", 4000, 4024)}
    cross = moves.cross_asset(btc_drop(), quotes, NOW)
    assert [c["symbol"] for c in cross] == ["GC=F"]


def test_a_level_or_an_extreme_has_no_window_so_no_context_line() -> None:
    m = moves.Move("milestone", ITEMS["BTC-USD"], 59400.0, NOW, change=-1.0, level=60000.0)
    assert moves.cross_asset(m, {"GC=F": quote("GC=F", 4000, 4024)}, NOW) == []


def test_a_day_move_compares_stocks_with_their_own_previous_close() -> None:
    m = moves.Move("move", ITEMS["^GSPC"], 6800.0, NOW, "day", -3.0, 1.2, ref=7010.0, ref_at=NOW - timedelta(hours=23))
    quotes = {"BTC-USD": quote("BTC-USD", 60000, 58200, minutes=24 * 60), "CL=F": quote("CL=F", 70.0, 72.1, minutes=24 * 60)}
    line = moves.cross_line(m, moves.cross_asset(m, quotes, NOW))
    assert line.startswith("Since the same close: Bitcoin −") and "Oil (WTI) +" in line


def test_the_context_line_is_part_of_what_the_post_prints_and_is_checked_against() -> None:
    m = btc_drop()
    line = moves.cross_line(m, [{"name": "Gold", "symbol": "GC=F", "change": 0.6}])
    told = livedata.market_told(m, headline.market(m), line)
    facts = livedata.move_facts(m, "", line)
    assert "Gold +0.6%" in told and "Gold +0.6%" in facts["data_note"]
