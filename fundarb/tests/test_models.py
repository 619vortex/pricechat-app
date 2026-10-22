from datetime import datetime, timedelta, timezone

import pytest

from fundarb.models import FundingRate, OrderBook, VenueQuote
from fundarb.symbols import base_of, canonical, hyperliquid_coin
from fundarb.venues.base import VenueUnavailable, interval_from_timestamps

T0 = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def book(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OrderBook:
    return OrderBook.build("v", "BTC-PERP", bids, asks, T0)


def test_hourly_and_eight_hour_rates_normalize_to_the_same_apr() -> None:
    b = book([(100, 10)], [(101, 10)])
    hourly = VenueQuote.build("hl", "BTC-PERP", FundingRate(0.0000125, 1, "api", "predicted", T0), 100, 100, b, (1.5, 4.5), "api")
    eight = VenueQuote.build("ord", "BTC-PERP", FundingRate(0.0001, 8, "api", "predicted", T0), 100, 100, b, None, "unavailable: x")
    assert hourly.funding_rate_hourly == pytest.approx(0.0000125) and eight.funding_rate_hourly == pytest.approx(0.0000125)
    assert hourly.funding_apr == pytest.approx(0.0000125 * 8760) == pytest.approx(eight.funding_apr)  # 10.95%
    assert eight.maker_bps is None and eight.taker_bps is None and eight.fee_source == "unavailable: x"  # never a silent 0
    assert hourly.funding_interval_hours == 1 and eight.funding_interval_hours == 8


def test_quote_refuses_impossible_inputs() -> None:
    with pytest.raises(ValueError, match="interval"):
        VenueQuote.build("v", "BTC-PERP", FundingRate(0.001, 0, "api", "predicted", T0), 1, 1, book([(1, 1)], [(2, 1)]), None, "")
    with pytest.raises(ValueError, match="empty order book"):
        VenueQuote.build("v", "BTC-PERP", FundingRate(0.001, 1, "api", "predicted", T0), 1, 1, book([], [(2, 1)]), None, "")


def test_book_is_sorted_best_first_and_equal_prices_merge() -> None:
    b = book([(98, 1), (99.5, 2), (99.5, 3), (0, 9)], [(102, 1), (100.5, 1)])  # worst bid first, a zero-price row
    assert [(l.price, l.size) for l in b.bids] == [(99.5, 5), (98, 1)] and b.ask == 100.5 and b.mid == 100.0


def test_depth_within_one_percent_and_whether_the_book_reached_the_edge() -> None:
    b = book([(99.9, 1), (99.5, 1), (98.0, 50)], [(100.1, 2), (100.6, 1), (102, 50)])  # mid 100: band 99..101
    bid_usd, ask_usd, complete = b.depth_within(1.0)
    assert bid_usd == pytest.approx(99.9 + 99.5) and ask_usd == pytest.approx(200.2 + 100.6) and complete
    short = book([(99.9, 1)], [(100.1, 1)])
    assert short.depth_within(1.0)[2] is False  # the fetched levels end inside the band
    q = VenueQuote.build("v", "BTC-PERP", FundingRate(0.0001, 8, "api", "predicted", T0), 100, 100, b, (0, 1), "api")
    assert q.depth_usd_1pct == pytest.approx(199.4)  # the thinner side


def test_interval_is_the_median_spacing_of_the_venues_own_settlements() -> None:
    base = T0.timestamp()
    assert interval_from_timestamps([base + h * 3600 for h in range(12)]) == 1.0
    assert interval_from_timestamps([base, base + 8 * 3600, base + 16 * 3600, base + 16 * 3600, base + 24 * 3600]) == 8.0  # duplicate ignored
    assert interval_from_timestamps([base, base + 3600, base + 7200, base + 6 * 3600 + 7200]) == 1.0  # one gap doesn't change it
    with pytest.raises(VenueUnavailable):
        interval_from_timestamps([base, base + 3600])


def test_canonical_symbols() -> None:
    assert canonical("BTC") == "BTC-PERP" and canonical("kPEPE") == "1000PEPE-PERP" and canonical("1000PEPE") == "1000PEPE-PERP"
    assert canonical("KAS") == "KAS-PERP"  # an uppercase K is a ticker, not a thousand prefix
    assert hyperliquid_coin("1000PEPE-PERP") == "kPEPE" and hyperliquid_coin("BTC-PERP") == "BTC"
    with pytest.raises(ValueError):
        base_of("BTCUSDT")
    assert (T0 + timedelta(0)).tzinfo is timezone.utc
