"""2026-11-24: liquidation figures judged against the same contract's traded value, and futures compared like for like with trade.xyz.

Production 2026-11-23: NVDA liquidations of $1.2-3.5M an hour on Binance + OKX were held as "61-170 times NVDA's typical recorded hour
($0.02M)" while OKX's NVDA perpetual traded $624M in the 14:00 UTC hour (read from OKX's public candles on 2026-11-24); copper moves were
held on every pass because trade.xyz COPPER (a perpetual) and Yahoo HG=F (a COMEX futures month) sat 2.6-3.0% apart: the futures basis.
"""
from __future__ import annotations

import pytest

from app.brand_manager import sources as bm_sources
from app.newsdesk import safety, sources

THIN = [20_000.0] * 48  # NVDA's recorded liquidation hours: a quiet perpetual, median $0.02M


def test_a_thin_coins_real_flush_is_believed_when_it_is_a_small_share_of_the_same_contracts_trading() -> None:
    nvda_hour = {"range_ratio": 9.0, "volume_ratio": 70.0, "volume_usd": 624_250_000.0, "venue": "OKX"}
    assert safety.liquidation_problem("NVDA", 3_500_000.0, THIN, nvda_hour) == ""
    calm = {"range_ratio": 1.0, "volume_ratio": 1.0, "volume_usd": 20_000_000.0, "venue": "OKX"}
    assert safety.liquidation_problem("NVDA", 3_500_000.0, THIN, calm) == ""  # 17.5% of the hour's trading: believed without a squeeze


def test_more_liquidated_than_could_have_traded_is_a_unit_error() -> None:
    hour = {"range_ratio": 1.0, "volume_ratio": 1.0, "volume_usd": 10_000_000.0, "venue": "OKX"}
    why = safety.liquidation_problem("DOGE", 40_000_000.0, [], hour)
    assert "4.0 times the $10.0M of DOGE traded on OKX" in why
    # Telegram 204: $10.1B against an ordinary BTC hour on OKX (~$250M): far above the ceiling and the trading
    assert safety.liquidation_problem("BTC", 10_100_000_000.0, [], {"volume_usd": 250_000_000.0, "venue": "OKX"}).startswith("$10,100.0M")


def test_between_the_plausible_share_and_the_unit_error_line_only_a_squeeze_is_believed() -> None:
    history = [100_000.0] * 48
    calm = {"range_ratio": 1.0, "volume_ratio": 1.0, "volume_usd": 12_000_000.0, "venue": "OKX"}
    assert "times SOL's typical recorded hour" in safety.liquidation_problem("SOL", 10_000_000.0, history, calm)
    squeeze = {**calm, "range_ratio": 4.0}
    assert safety.liquidation_problem("SOL", 10_000_000.0, history, squeeze) == ""


REAL_VENUE_HOUR_MARKET = bm_sources.venue_hour_market  # captured at import, before the autouse conftest stub replaces it


def test_okx_hour_is_read_from_candles() -> None:
    hour = 3_600_000
    since = 1_000 * hour
    bm_sources._VENUE_MARKET.clear()

    def fetch(inst: str, after_ms: int) -> list[list[str]]:
        assert inst == "NVDA-USDT-SWAP"
        if after_ms < since:  # one page holds the whole window
            return []
        # newest first: 170 quiet hours of $10M, then the two hours of the flush at $300M each with a 10% range
        rows = []
        for i in range(172):
            t = since + hour - i * hour
            loud = i < 2
            rows.append([str(t), "100", "110" if loud else "101", "100" if loud else "99.5", "105", "0", "0", "300000000" if loud else "10000000", "1"])
        return rows

    got = REAL_VENUE_HOUR_MARKET("NVDA", since, fetch=fetch)
    assert got is not None and got["venue"] == "OKX" and got["volume_usd"] == 600_000_000.0
    assert got["volume_ratio"] == pytest.approx(30.0) and got["range_ratio"] > 5


async def test_copper_futures_and_the_perpetual_agree_on_the_move_despite_the_basis(monkeypatch: pytest.MonkeyPatch) -> None:
    async def xyz(*_a, **_k):  # type: ignore[no-untyped-def]
        return {"xyz:COPPER": (5.74, 5.74, 5.60)}  # 11-23: oracle 5.74 vs Yahoo HG=F 5.59 (2.7% apart), +2.5% on trade.xyz in 24 h

    monkeypatch.setattr(sources, "xyz_prices", xyz)
    item = {"symbol": "HG=F", "group": "commodity"}
    ok, why = await sources.second_opinion(item, 5.59, change=2.8)
    assert ok and "futures basis allowed" in why
    ok, why = await sources.second_opinion(item, 5.59, change=-2.8)  # Yahoo says down, trade.xyz says up: the moves disagree
    assert not ok and "apart" in why


async def test_a_wrong_instrument_or_unit_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def xyz(*_a, **_k):  # type: ignore[no-untyped-def]
        return {"xyz:COPPER": (12_650.0, 12_650.0, 12_600.0), "xyz:SP500": (7_237.0, 7_237.0, 6_910.0)}  # copper per tonne, not per pound

    monkeypatch.setattr(sources, "xyz_prices", xyz)
    ok, why = await sources.second_opinion({"symbol": "HG=F", "group": "commodity"}, 5.59, change=2.8)
    assert not ok and "apart" in why
    ok, why = await sources.second_opinion({"symbol": "^GSPC", "group": "index"}, 7_237.17, change=4.73)  # an index keeps its tight level check
    assert ok and "basis" not in why


def test_asset_classes() -> None:
    assert [safety.price_class(s) for s in ("^GSPC", "NVDA", "GC=F", "BTC-USD", "HG=F")] == ["index", "stock", "commodity", "crypto", "commodity"]
