"""Pre-deployment review of PR #17 (2026-11-24): the branded card prints a measured move's own window beside the 24-hour chart, and a
writer's copy may never turn a related market's figure into a cause or an effect."""
from datetime import datetime, timezone

from app.newsdesk import card, moves, newscard
from tests.test_nd_newscard_20261124 import _ev

NOW = datetime(2026, 11, 24, 18, 30, tzinfo=timezone.utc)
CROSS = {"cross": [{"name": "Nasdaq", "symbol": "^IXIC", "change": -1.5}, {"name": "Gold", "symbol": "GC=F", "change": 0.6}]}


def test_the_card_prints_the_move_s_own_window() -> None:
    f = {"stat": "-3.2%", "spark": [61200, 60900, 60500, 60100], "as_of": NOW.isoformat(), "symbol": "BTC-USD", "kind": "move"}
    with_window = newscard.render(_ev(kind="market", headline="BITCOIN FALLS 3.2% IN AN HOUR", facts={**f, "window": "1h"}), NOW)
    without = newscard.render(_ev(kind="market", headline="BITCOIN FALLS 3.2% IN AN HOUR", facts=f), NOW)
    assert with_window and without and with_window != without
    assert card.STAT_WINDOW["1h"] == "IN 1 HOUR" and card.STAT_WINDOW["day"] == "SINCE THE LAST CLOSE"


def test_a_level_s_price_is_not_given_a_window() -> None:
    f = {"stat": "$59,800", "as_of": NOW.isoformat(), "symbol": "BTC-USD", "kind": "level", "window": "1h"}
    assert newscard.render(_ev(kind="market", headline="BITCOIN BELOW $60,000", facts=f), NOW) == \
        newscard.render(_ev(kind="market", headline="BITCOIN BELOW $60,000", facts={**f, "window": ""}), NOW)


def test_the_desk_s_own_context_line_passes() -> None:
    assert moves.cross_problems(CROSS, {"headline": "Bitcoin falls 3.2% in an hour", "context": "In the same hour: Nasdaq −1.5%, Gold +0.6%."}) == []


def test_a_related_market_is_never_a_cause() -> None:
    for context in ("Bitcoin fell as the Nasdaq dropped 1.5%.", "Losses on the Nasdaq dragged crypto lower.",
                    "Traders sold after Nasdaq fell 1.5%.", "Gold's rise drove the move."):
        assert moves.cross_problems(CROSS, {"context": context}), context
    assert moves.cross_problems(CROSS, {"stakes": "The Nasdaq sell-off sent bitcoin lower."})


def test_a_related_market_is_never_in_the_headline() -> None:
    assert moves.cross_problems(CROSS, {"headline": "Bitcoin and Nasdaq slide together"})


def test_no_related_markets_no_check() -> None:
    assert moves.cross_problems({}, {"headline": "Bitcoin falls as Nasdaq drops"}) == []
