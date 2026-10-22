"""Brand Manager's extra story sources. TEST FIXTURES only: a fake Hyperliquid client and a mocked CoinGecko reply."""

import asyncio
from datetime import datetime, timezone

import httpx

from app.brand_manager import defaults, sources
from app.tradingos.market import Candle, Market

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def _m(coin: str, mark: float, prev: float, funding: float, vol: float = 2e8) -> Market:
    return Market(coin=coin, mark=mark, mid=mark, oracle=mark, prev_day=prev, funding=funding, open_interest=1000.0, day_volume=vol,
                  max_leverage=20, impact_bid=None, impact_ask=None, sz_decimals=2)


class FakeMD:
    def top(self, n: int = 20, min_volume: float = 0) -> list[Market]:
        ms = [_m("SOL", 160.0, 145.0, 0.0000125), _m("DOGE", 0.21, 0.2, 0.00025), _m("BTC", 67000.0, 66500.0, 0.0000125),
              _m("THIN", 5.0, 3.0, 0.0, vol=1e6)]
        return [m for m in ms if m.day_volume >= min_volume][:n]

    def candles(self, coin: str, interval: str, count: int) -> list[Candle]:
        return [Candle(1_760_000_000_000 + i * 3_600_000, 1, 1, 1, 150.0 + i, 1) for i in range(count)]


def test_hyperliquid_moves_and_funding_become_sourced_stories(db) -> None:  # type: ignore[no-untyped-def]
    topics = sources.hyperliquid(db, FakeMD(), NOW)
    by = {t.key: t for t in topics}
    move = next(t for t in topics if t.topic.startswith("SOL is up"))
    assert move.category == "crypto_derivatives" and move.template == "price_alert"
    assert move.brief["numbers"]["change_24h_pct"] == 10.34 and move.brief["stat"]["value"] == 10.34
    assert len(move.brief["chart"]["points"]) == 48 and move.brief["sources"][0]["name"] == "Hyperliquid"
    fund = next(t for t in topics if t.topic.startswith("DOGE funding"))
    assert "longs pay shorts" in fund.topic and any("Funding is a regular payment" in f["text"] for f in fund.brief["facts"])
    assert fund.brief["stat"]["decimals"] == 4 and fund.brief["stat"]["value"] == 0.025
    assert not any("THIN" in t.topic for t in topics)  # illiquid markets never make a story
    assert not any(t.topic.startswith("BTC is") for t in topics)  # a 0.75% day isn't news
    assert len(by) == len(topics)


def test_coingecko_trending() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/search/trending")
        return httpx.Response(200, json={"coins": [{"item": {"id": "pepe", "name": "Pepe", "symbol": "pepe", "market_cap_rank": 31,
                                                             "large": "https://img.test/pepe.png",
                                                             "data": {"price": 0.0000123, "price_change_percentage_24h": {"usd": 12.5}}}}]})

    async def run() -> list:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await sources.coingecko_trending(c, NOW)

    [t] = asyncio.run(run())
    assert t.topic == "Pepe is trending #1 on CoinGecko today" and t.brief["numbers"]["change_24h_pct"] == 12.5
    assert "most searched coins on CoinGecko" in t.brief["facts"][0]["text"] and t.brief["images"][0]["url"].startswith("https://")


def test_a_brand_that_chose_every_category_gets_the_new_one_but_a_narrow_choice_is_kept() -> None:
    old_all = ["crypto_news", "crypto_market", "pokemon_market", "pokemon_rare", "facts", "pricechat_highlights"]
    assert "crypto_derivatives" in defaults.merged({"categories": old_all})["categories"]
    assert defaults.merged({"categories": ["pokemon_rare"]})["categories"] == ["pokemon_rare"]
    assert "crypto_derivatives" in defaults.merged({})["categories"]
