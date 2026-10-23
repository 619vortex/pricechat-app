"""Evidence pack of the Investment Committee (app/committee/evidence). No network: every source is an httpx.MockTransport
serving trimmed copies of the real answers (shapes verified with one live GET each on 2026-10-23, recorded below)."""

import asyncio
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.committee.evidence import EvidenceError, build_evidence, provider_status
from app.committee.evidence import coingecko, common, news, technical
from app.tradingos import indicators as ind
from app.tradingos import market, venues
from app.tradingos.market import Candle, MarketData
from app.tradingos.structure import Venues

H = 3_600_000

# ---------------------------------------------------------------- real answer shapes (trimmed live samples, 2026-10-23)
CG_SEARCH_BTC = {"coins": [
    {"id": "bitcoin", "name": "Bitcoin", "api_symbol": "bitcoin", "symbol": "BTC", "market_cap_rank": 1,
     "thumb": "https://coin-images.coingecko.com/coins/images/1/thumb/bitcoin.png", "large": "https://coin-images.coingecko.com/coins/images/1/large/bitcoin.png"},
    {"id": "bitget-wrapped-btc", "name": "Bitget Wrapped BTC", "api_symbol": "bitget-wrapped-btc", "symbol": "BGBTC", "market_cap_rank": 289,
     "thumb": "", "large": ""},
    {"id": "bitcoin-pro", "name": "Bitcoin Pro", "api_symbol": "bitcoin-pro", "symbol": "BTCP", "market_cap_rank": 1493, "thumb": "", "large": ""}],
    "exchanges": [], "icos": [], "categories": [], "nfts": []}
CG_COIN_BTC = {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "web_slug": "bitcoin", "categories": ["Layer 1 (L1)", "Proof of Work (PoW)"],
               "genesis_date": "2009-01-03", "market_cap_rank": 1, "last_updated": "2026-10-23T02:54:57.000Z",
               "market_data": {"current_price": {"usd": 81800}, "ath": {"usd": 126080}, "ath_change_percentage": {"usd": -35.0974},
                               "market_cap": {"usd": 1644693224063}, "market_cap_rank": 1, "fully_diluted_valuation": {"usd": 1644698378547},
                               "total_volume": {"usd": 30000000000}, "total_supply": 20102109.0, "max_supply": 21000000.0,
                               "circulating_supply": 20102046.0, "last_updated": "2026-10-23T02:54:57.000Z"}}
CG_SEARCH_AMBIGUOUS = {"coins": [
    {"id": "abc-one", "name": "ABC One", "api_symbol": "abc-one", "symbol": "ABC", "market_cap_rank": 120},
    {"id": "abc-two", "name": "ABC Two", "api_symbol": "abc-two", "symbol": "ABC", "market_cap_rank": 310},
    {"id": "abc-old", "name": "ABC Old", "api_symbol": "abc-old", "symbol": "ABC", "market_cap_rank": None}], "exchanges": []}
# alternative.me /fng/?limit=N: {"name", "data": [{"value": "50", "value_classification": "Neutral", "timestamp": "1792713600",
#   "time_until_update": "75791"}, ...newest first], "metadata": {"error": null}}
# Hyperliquid fundingHistory: [{"coin": "BTC", "fundingRate": "0.0000125", "premium": "-0.0001237596", "time": 1792713600026}, ...]
# RSS items (CoinDesk uses CDATA titles, Cointelegraph CDATA links + HTML descriptions, Decrypt plain text):
RSS_COINDESK = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="http://search.yahoo.com/mrss/" version="2.0"><channel>
<title>CoinDesk: Bitcoin, Ethereum, Crypto News and Price Data</title><link>https://www.coindesk.com/</link>
<item><title><![CDATA[Bitcoin ETFs see record inflows as BTC rallies]]></title>
<link>https://www.coindesk.com/markets/2026/10/22/bitcoin-etfs-record-inflows</link>
<guid isPermaLink="false">03a2b707-4827-4df8-b111-ec51f7d7b7dd</guid><pubDate>{d1}</pubDate>
<description><![CDATA[Spot bitcoin funds took in more than $1 billion in three days.]]></description><dc:creator>Jesse Hamilton</dc:creator></item>
<item><title><![CDATA[Fed holds rates steady, Powell signals patience]]></title><link>https://www.coindesk.com/policy/fed-holds</link>
<pubDate>{d2}</pubDate><description><![CDATA[The FOMC kept its target range unchanged.]]></description></item>
<item><title><![CDATA[Old bitcoin story]]></title><link>https://www.coindesk.com/old</link><pubDate>{old}</pubDate><description/></item>
</channel></rss>"""
RSS_COINTELEGRAPH = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel><title>Cointelegraph.com News</title>
<item><title>Switzerland approves first spot Bitcoin ETF for retail investors</title><pubDate>{d1}</pubDate>
<guid isPermaLink="true">https://cointelegraph.com/news/switzerland-approves-first-spot-bitcoin-etf</guid>
<link><![CDATA[https://cointelegraph.com/news/switzerland-approves-first-spot-bitcoin-etf?utm_source=rss_feed]]></link>
<description><![CDATA[<p style="float:right"><img src="https://s3-images.ctmedia.io/x.jpg"></p><p>BIT Asset Management's Bitcoin Fund ETF is expected to begin trading on SIX in November.</p>]]></description>
<category>Latest News</category></item>
<item><title>From the future: BTC to the moon</title><link>https://cointelegraph.com/news/future</link><pubDate>{future}</pubDate>
<description>Must never be shown.</description></item>
</channel></rss>"""
RSS_DECRYPT = """<?xml version='1.0' encoding='utf-8' ?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>Decrypt</title><link>https://decrypt.co/feed</link>
<item><title>Is the Internet Safe Again After the PHP Nightmare?</title><link>https://decrypt.co/381662/internet-safe</link>
<pubDate>{d2}</pubDate><description>PHP shipped its fix. Here&#39;s what that means for your passwords.</description>
<dc:creator>Jose Antonio Lanz</dc:creator><guid isPermaLink="false">https://decrypt.co/?p=381662</guid></item>
</channel></rss>"""


# ---------------------------------------------------------------- a fake world
class World:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.now_ms = int(time.time() * 1000)
        self.lag_ms = 0  # candles stop this long before now (stale data)
        self.leak_after: int | None = None  # ms: anything later is poisoned (x10 prices, 5% funding, F&G 99)
        self.hl = {"BTC": 80_000.0, "ETH": 3_000.0}
        self.okx = {"BTC-USDT": 80_010.0, "BTC-USDT-SWAP": 80_005.0, "KASX-USDT": 0.25}
        self.down: set[str] = set()  # hosts answering 503
        self.cg_search: dict[str, Any] = CG_SEARCH_BTC
        self.gecko_pools: list[dict[str, Any]] = []

    # deterministic prices: two slow waves around the base
    def px(self, base: float, t: int) -> float:
        h = t / H
        return base * (1 + 0.03 * math.sin(h / 9) + 0.015 * math.sin(h / 41))

    def candle(self, base: float, t: int, span: int) -> Candle:
        o, c = self.px(base, t), self.px(base, t + span)
        k = 10 if self.leak_after is not None and t + span > self.leak_after else 1
        return Candle(t, o * k, max(o, c) * 1.002 * k, min(o, c) * 0.998 * k, c * k, 100 + (t // span) % 7 * 10)

    def series(self, base: float, span: int, start: int, end: int | None) -> list[Candle]:
        stop = self.now_ms - self.lag_ms  # the fake ignores endTime beyond "now": callers must cut it themselves
        first = (start // span + 1) * span
        return [self.candle(base, t, span) for t in range(first, (stop // span) * span + 1, span)]

    def handler(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        host = req.url.host
        body = json.loads(req.content) if req.method == "POST" and req.content else None
        self.requests.append((req.method, url, body))
        if host in self.down:
            return httpx.Response(503, text="down")
        if host == "api.hyperliquid.xyz":
            return httpx.Response(200, json=self.hyperliquid(body))
        if host == "www.okx.com":
            inst = req.url.params.get("instId")
            if req.url.path.endswith("/ticker"):
                if inst not in self.okx:
                    return httpx.Response(200, json={"code": "51001", "msg": "Instrument ID or Spread ID doesn't exist.", "data": []})
                p = self.okx[inst]
                return httpx.Response(200, json={"code": "0", "msg": "", "data": [{"instId": inst, "last": str(p), "open24h": str(p * 0.98),
                                                                                    "volCcy24h": "5000000", "ts": str(self.now_ms)}]})
            if req.url.path.endswith("/candles"):
                bar = req.url.params.get("bar")
                span = {"1H": H, "4H": 4 * H, "1Dutc": 24 * H, "15m": H // 4}[bar]
                cs = self.series(self.okx[inst], span, self.now_ms - span * 300, None)
                rows = [[str(c.t), str(c.o), str(c.h), str(c.l), str(c.c), str(c.v), "0", "0", "1"] for c in reversed(cs)]
                return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})
        if host in ("api.binance.com", "fapi.binance.com"):
            return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
        if host == "api.bybit.com":
            return httpx.Response(200, json={"retCode": 0, "retMsg": "OK", "result": {"list": []}})
        if host == "api.geckoterminal.com":
            return httpx.Response(200, json={"data": self.gecko_pools})
        if host == "api.dexscreener.com":
            return httpx.Response(200, json={"pairs": []})
        if host == "api.coingecko.com":
            if req.url.path.endswith("/search"):
                return httpx.Response(200, json=self.cg_search)
            if req.url.path.endswith("/coins/bitcoin"):
                return httpx.Response(200, json=CG_COIN_BTC)
            if req.url.path.endswith("/ping"):
                return httpx.Response(200, json={"gecko_says": "(V3) To the Moon!"})
            return httpx.Response(404, json={"error": "coin not found"})
        if host == "api.alternative.me":
            day = 86_400
            today = self.now_ms // 1000 // day * day
            rows = []
            for k in range(40):
                ts = today - k * day
                v = 99 if self.leak_after is not None and ts * 1000 > self.leak_after else 30 + k
                rows.append({"value": str(v), "value_classification": "Extreme Greed" if v == 99 else "Fear", "timestamp": str(ts)})
            return httpx.Response(200, json={"name": "Fear and Greed Index", "data": rows, "metadata": {"error": None}})
        if host in ("www.coindesk.com", "cointelegraph.com", "decrypt.co"):
            now = datetime.fromtimestamp(self.now_ms / 1000, tz=timezone.utc)
            fill = {"d1": format_datetime(now - timedelta(hours=3)), "d2": format_datetime(now - timedelta(hours=20)),
                    "old": format_datetime(now - timedelta(hours=60)), "future": format_datetime(now + timedelta(hours=2))}
            tpl = {"www.coindesk.com": RSS_COINDESK, "cointelegraph.com": RSS_COINTELEGRAPH, "decrypt.co": RSS_DECRYPT}[host]
            return httpx.Response(200, content=re.sub(r"\{(\w+)\}", lambda m: fill[m.group(1)], tpl).encode(),
                                  headers={"content-type": "application/xml"})
        return httpx.Response(404, text=f"no fake for {url}")

    def hyperliquid(self, body: dict[str, Any]) -> Any:
        kind = body["type"]
        if kind == "metaAndAssetCtxs":
            uni = [{"name": c, "szDecimals": 5, "maxLeverage": 40} for c in self.hl]
            ctxs = []
            for c, base in self.hl.items():
                m = self.px(base, self.now_ms)
                ctxs.append({"markPx": str(m), "midPx": str(m), "oraclePx": str(m), "prevDayPx": str(m * 0.99), "funding": "0.0000125",
                             "openInterest": "30000", "dayNtlVlm": "3000000000", "impactPxs": [str(m * 0.9999), str(m * 1.0001)]})
            return [{"universe": uni}, ctxs]
        if kind == "meta":
            return {"universe": [{"name": c, "szDecimals": 5, "maxLeverage": 40, "onlyIsolated": False} for c in self.hl]}
        if kind == "candleSnapshot":
            r = body["req"]
            span = market.INTERVALS[r["interval"]] * 1000
            return [{"t": c.t, "T": c.t + span - 1, "s": r["coin"], "i": r["interval"], "o": str(c.o), "c": str(c.c), "h": str(c.h),
                     "l": str(c.l), "v": str(c.v), "n": 50} for c in self.series(self.hl[r["coin"]], span, r["startTime"], r.get("endTime"))]
        if kind == "l2Book":
            m = self.px(self.hl[body["coin"]], self.now_ms)
            return {"levels": [[{"px": str(m * (1 - i / 10_000)), "sz": "50", "n": 3} for i in range(1, 20)],
                               [{"px": str(m * (1 + i / 10_000)), "sz": "50", "n": 3} for i in range(1, 20)]]}
        if kind == "fundingHistory":
            first = (body["startTime"] // H + 1) * H
            return [{"coin": body["coin"], "fundingRate": "0.05" if self.leak_after is not None and t > self.leak_after else "0.0000125",
                     "premium": "-0.0001", "time": t + 26} for t in range(first, self.now_ms, H)]
        raise AssertionError(kind)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> World:
    w = World()
    t = httpx.MockTransport(w.handler)
    monkeypatch.setattr(market, "_shared", MarketData(transport=t))
    monkeypatch.setattr(venues, "_shared", Venues(transport=t))
    venues._blocked.clear()
    from app.tradingos import history

    monkeypatch.setattr(history, "DIR", tmp_path)  # no stored research history: Hyperliquid (the fake) answers
    monkeypatch.delenv("COMMITTEE_NEWS_FEEDS", raising=False)
    common.reset()
    w.transport = t  # type: ignore[attr-defined]
    return w


def http_of(w: World) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=w.transport)  # type: ignore[attr-defined]


async def pack(w: World, db: Any, symbol: str, **kw: Any) -> dict[str, Any]:
    async with http_of(w) as http:
        return await build_evidence(db, symbol, http=http, **kw)


def numbers(x: Any) -> list[float]:
    if isinstance(x, bool) or x is None:
        return []
    if isinstance(x, (int, float)):
        return [float(x)]
    if isinstance(x, dict):
        return [n for v in x.values() for n in numbers(v)]
    if isinstance(x, list):
        return [n for v in x for n in numbers(v)]
    return []


# ---------------------------------------------------------------- packs
async def test_btc_hyperliquid_perp_pack(world: World, db: Any) -> None:
    p = await pack(world, db, "BTC")
    a = p["asset"]
    assert (a["symbol"], a["venue"], a["market_type"], a["market_id"], a["label"], a["hl_tradable"]) == (
        "BTC", "hyperliquid", "perp", "BTC", "Hyperliquid perpetual", True)
    assert p["mode"] == "live" and p["timeframe"] == "1h" and p["degraded"] == []
    assert p["price"]["kind"] == "mark" and p["price"]["source"] == "hyperliquid" and p["price"]["stale"] is False
    assert abs(p["price"]["value"] - world.px(80_000, world.now_ms)) / p["price"]["value"] < 1e-4

    t = p["sections"]["technical"]
    assert t["status"] == "ok", t["limitations"]
    assert set(t["facts"]["frames"]) == {"1h", "4h", "1d"}
    f1 = t["facts"]["frames"]["1h"]
    for k in ("close", "ema20", "ema50", "ema200", "rsi14", "macd", "macd_signal", "macd_hist", "bb_upper", "bb_mid", "bb_lower", "bb_width_pct",
              "atr14", "atr_pct", "vol_ratio20", "trend", "structure", "breakout", "realized_vol_pct", "candles", "last_closed"):
        assert k in f1
    assert f1["candles"] == technical.CANDLES and f1["ema200"] is not None
    last_closed = datetime.fromisoformat(f1["last_closed"].replace("Z", "+00:00"))
    assert last_closed <= datetime.now(timezone.utc)  # never the forming candle
    assert all(s < p["price"]["value"] for s in t["facts"]["support"]) and all(r > p["price"]["value"] for r in t["facts"]["resistance"])
    sig = t["facts"]["signal"]
    assert sig["rule"] == "trend_pullback_v1" and sig["scope"] == "full" and sig["verdict"] in ("LONG", "SHORT", "WAIT")
    assert t["lean"] in ("bullish", "bearish", "neutral") and -2 <= t["score"] <= 2

    fu = p["sections"]["fundamental"]
    assert fu["facts"]["max_leverage"] == 40 and fu["facts"]["only_isolated"] is False and fu["facts"]["sz_decimals"] == 5
    assert fu["facts"]["coingecko"]["id"] == "bitcoin" and fu["facts"]["coingecko"]["ambiguous"] is False
    assert fu["facts"]["coingecko"]["max_supply"] == 21_000_000 and fu["facts"]["token_unlocks"] == "unavailable"
    assert "OKX Spot" in fu["facts"]["listed_on"] and "Hyperliquid Perpetual" in fu["facts"]["listed_on"]
    assert any("token unlocks" in x for x in fu["limitations"]) and fu["lean"] == "neutral" and fu["score"] == 0

    n = p["sections"]["news"]
    assert n["status"] == "ok"
    titles = {i["title"]: i for i in n["items"]}
    assert titles["Bitcoin ETFs see record inflows as BTC rallies"]["match"] == "symbol"
    assert titles["Switzerland approves first spot Bitcoin ETF for retail investors"]["match"] == "name"
    assert titles["Fed holds rates steady, Powell signals patience"]["match"] == "macro"
    assert "Old bitcoin story" not in titles and "From the future: BTC to the moon" not in titles  # > 48 h, after as_of
    assert all(0 <= i["age_h"] <= 48 and len(i["summary"]) <= 300 and "<" not in i["summary"] for i in n["items"])
    assert n["lean"] == "bullish" and n["score"] == 1  # two bullish coin headlines, no bearish one

    s = p["sections"]["sentiment"]
    assert s["facts"]["funding_1h_pct"] == 0.00125 and s["facts"]["funding_24h_avg_pct"] == 0.00125
    assert s["facts"]["fear_greed"]["value"] == 30 and s["facts"]["oi_usd"] > 0
    assert any("taker flow" in x for x in s["limitations"])

    ids = [r["id"] for r in p["refs"]]
    assert len(ids) == len(set(ids))
    for sec in p["sections"].values():  # every reference a section cites exists
        assert set(sec["refs"]) <= set(ids)
        for r in sec["reasons"]:
            assert set(r["refs"]) <= set(ids)
    assert p["price"]["ref"] in ids


async def test_token_not_on_hyperliquid_comes_from_okx_spot(world: World, db: Any) -> None:
    p = await pack(world, db, "KASX")
    a = p["asset"]
    assert (a["venue"], a["market_type"], a["market_id"], a["label"], a["hl_tradable"]) == ("okx", "spot", "KASX-USDT", "OKX spot KASX-USDT", False)
    assert p["price"]["kind"] == "last" and p["price"]["value"] == 0.25
    t = p["sections"]["technical"]
    assert t["facts"]["signal"]["scope"] == "chart rules" and t["status"] in ("ok", "partial")
    assert any(r["source"].startswith("OKX KASX-USDT 1h candles") for r in p["refs"])
    s = p["sections"]["sentiment"]
    assert "funding_1h_pct" not in s["facts"] and any("no Hyperliquid perpetual" in x for x in s["limitations"])
    assert p["sections"]["fundamental"]["facts"].get("max_leverage") is None


async def test_news_feeds_down_is_unavailable_not_a_crash(world: World, db: Any) -> None:
    world.down = {"www.coindesk.com", "cointelegraph.com", "decrypt.co"}
    p = await pack(world, db, "BTC")
    n = p["sections"]["news"]
    assert n["status"] == "unavailable" and n["items"] == [] and n["lean"] == "unknown" and n["score"] is None
    assert any("every news feed failed" in x for x in n["limitations"]) and "news: every feed failed" in p["degraded"]
    common.reset()
    world.down = {"decrypt.co"}
    p = await pack(world, db, "BTC")
    assert p["sections"]["news"]["status"] == "partial" and any("Decrypt answered 503" in x for x in p["sections"]["news"]["limitations"])


async def test_every_source_down_still_reports_honestly(world: World, db: Any) -> None:
    world.down = {"www.coindesk.com", "cointelegraph.com", "decrypt.co", "api.coingecko.com", "api.alternative.me"}
    p = await pack(world, db, "BTC")
    assert p["sections"]["fundamental"]["facts"].get("coingecko") is None
    assert any("CoinGecko unavailable" in x for x in p["sections"]["fundamental"]["limitations"])
    s = p["sections"]["sentiment"]
    assert "fear_greed" not in s["facts"] and any("Fear & Greed unavailable" in x for x in s["limitations"])
    assert any(d.startswith("coingecko:") for d in p["degraded"])


async def test_stale_candles_mark_the_technical_section_stale(world: World, db: Any) -> None:
    world.lag_ms = 10 * H
    p = await pack(world, db, "BTC")
    t = p["sections"]["technical"]
    assert t["status"] == "stale" and any("stale market data" in x for x in t["limitations"])


async def test_stale_price(world: World, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    md = market.data()
    fresh = md.markets()[1]
    monkeypatch.setattr(md, "markets", lambda: (time.time() - market.STALE_AFTER - 60, fresh))
    p = await pack(world, db, "BTC")
    assert p["price"]["stale"] is True and p["sections"]["technical"]["status"] == "stale"
    assert any("price is older" in x for x in p["sections"]["technical"]["limitations"])
    world.lag_ms = 10 * H  # stale price AND stale candles: nothing current to reason on
    common.reset()
    with pytest.raises(EvidenceError) as e:
        await pack(world, db, "ETH")
    assert e.value.code == "stale"


async def test_ambiguous_coingecko_match_uses_no_numbers(world: World, db: Any) -> None:
    world.cg_search = CG_SEARCH_AMBIGUOUS
    world.hl["ABC"] = 2.0
    p = await pack(world, db, "ABC")
    fu = p["sections"]["fundamental"]
    cg = fu["facts"]["coingecko"]
    assert cg["ambiguous"] is True and cg["id"] is None and "market_cap" not in cg and "fdv" not in cg
    assert [c["id"] for c in cg["candidates"]] == ["abc-one", "abc-two"]
    assert any("several coins use the ticker ABC" in x for x in fu["limitations"])
    assert not any(r["source"].startswith("CoinGecko abc") for r in p["refs"])
    assert not any("/coins/abc" in u for _, u, _ in world.requests)  # no numbers were even fetched


def test_coingecko_pick_rules() -> None:
    m = coingecko.pick("ABC", CG_SEARCH_AMBIGUOUS["coins"])
    assert m["ambiguous"] is True and m["id"] is None and [c["id"] for c in m["candidates"]] == ["abc-one", "abc-two"]
    m = coingecko.pick("BTC", CG_SEARCH_BTC["coins"])
    assert (m["id"], m["ambiguous"], m["matched_by"]) == ("bitcoin", False, "exact symbol, best market-cap rank")
    far = [{"id": "a", "symbol": "XYZ", "market_cap_rank": 80}, {"id": "b", "symbol": "XYZ", "market_cap_rank": 900}]
    assert coingecko.pick("XYZ", far)["id"] == "a"  # the second one is outside the top 500: not ambiguous
    assert coingecko.pick("XYZ", [{"id": "c", "symbol": "XYZ", "market_cap_rank": None}])["id"] is None  # unranked: no confident match
    assert coingecko.pick("XYZ", [{"id": "d", "symbol": "XYZW", "market_cap_rank": 3}])["id"] is None  # exact symbol only


async def test_backtest_pack_never_leaks_later_data(world: World, db: Any) -> None:
    as_of = datetime.fromtimestamp((world.now_ms - 5 * 24 * H) / 1000, tz=timezone.utc).replace(minute=30, second=0, microsecond=0)
    world.leak_after = int(as_of.timestamp() * 1000)  # the fake serves later candles / funding / F&G anyway, poisoned
    p = await pack(world, db, "BTC", as_of=as_of, mode="backtest")
    cut = as_of.timestamp()

    def ts(s: str) -> float:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

    assert p["mode"] == "backtest" and p["as_of"] == as_of.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert p["price"]["kind"] == "close" and ts(p["price"]["ts"]) <= cut
    assert p["price"]["value"] < 80_000 * 1.2  # a later (x10) candle never reaches the pack
    t = p["sections"]["technical"]
    for tf, f in t["facts"]["frames"].items():
        assert ts(f["last_closed"]) <= cut, tf
    assert max(numbers(t["facts"]["frames"]) + numbers(t["facts"]["support"]) + numbers(t["facts"]["resistance"])
               + [n for n in numbers(t["facts"]["scenarios"])]) < 80_000 * 1.2
    assert t["facts"]["signal"]["scope"] == "chart rules"
    s = p["sections"]["sentiment"]
    assert s["facts"]["funding_1h_pct"] == 0.00125 and s["facts"]["funding_7d_avg_pct"] == 0.00125  # no 5% rows after as_of
    assert ts(s["facts"]["fear_greed"]["ts"]) <= cut and s["facts"]["fear_greed"]["value"] != 99
    for r in p["refs"]:
        if r["ts"]:
            assert ts(r["ts"]) <= cut, r
    for name in ("news",):
        assert p["sections"][name]["status"] == "unavailable"
        assert any(common.NO_HISTORY in x for x in p["sections"][name]["limitations"])
    assert p["sections"]["fundamental"]["status"] == "unavailable"
    assert any(common.NO_HISTORY in x for x in p["sections"]["fundamental"]["limitations"])
    assert any("community mentions: " + common.NO_HISTORY == x for x in s["limitations"])
    hosts = {httpx.URL(u).host for _, u, _ in world.requests}
    assert not hosts & {"api.coingecko.com", "www.coindesk.com", "cointelegraph.com", "decrypt.co"}
    hl = [b for _, u, b in world.requests if b and b.get("type") == "candleSnapshot"]
    assert hl and all(b["req"]["endTime"] == int(cut * 1000) for b in hl)


async def test_unknown_and_ambiguous_symbols(world: World, db: Any) -> None:
    with pytest.raises(EvidenceError) as e:
        await pack(world, db, "!!!")
    assert e.value.code == "unknown_symbol"
    with pytest.raises(EvidenceError) as e:
        await pack(world, db, "ZZZQ")
    assert e.value.code == "unknown_symbol"

    def pool(n: int, token: str) -> dict[str, Any]:
        return {"id": f"eth_0xpool{n}", "type": "pool",
                "attributes": {"name": "PEPE / WETH", "address": f"0xpool{n}", "base_token_price_usd": "0.00001", "reserve_in_usd": str(500_000 * n),
                               "volume_usd": {"h24": "100000"}, "price_change_percentage": {"h24": "1.2"}},
                "relationships": {"base_token": {"data": {"id": f"eth_{token}"}}, "dex": {"data": {"id": "uniswap_v3"}}}}
    world.gecko_pools = [pool(1, "0x" + "a" * 40), pool(2, "0x" + "b" * 40)]
    with pytest.raises(EvidenceError) as e:
        await pack(world, db, "PEPE dex")
    assert e.value.code == "ambiguous" and {c["address"] for c in e.value.choices} == {"0x" + "a" * 40, "0x" + "b" * 40}


async def test_community_mentions_from_ingest_tables(world: World, db: Any) -> None:
    from app.db.base import utcnow
    from app.models.enums import Platform
    from app.models.social import SocialPost

    now = utcnow()
    rows = [("$BTC looks strong", 2), ("bitcoin to the moon", 5), ("BTC!", 20), ("BTCUSDT is not a mention of the coin word", 3)]
    rows += [(f"btc day {k}", 24 * k + 3) for k in (1, 2, 3, 4, 5, 6, 6)]  # lowercase btc: not the ticker, not the name
    rows += [(f"BTC day {k}", 24 * k + 3) for k in (1, 2, 3, 4, 5, 6, 6)]
    for i, (text, hours) in enumerate(rows):
        db.add(SocialPost(platform=Platform.X, external_id=f"m{i}", text=text, posted_at=now - timedelta(hours=hours)))
    db.commit()
    p = await pack(world, db, "BTC")
    c = p["sections"]["sentiment"]["facts"]["community"]
    assert c["mentions_24h"] == 3 and c["mentions_7d_avg"] == 1.0
    assert any("community mentions are noisy" in x for x in p["sections"]["sentiment"]["limitations"])


async def test_provider_status_reports_every_source(world: World) -> None:
    async with http_of(world) as http:
        st = await provider_status(http)
    assert set(st) == {"hyperliquid", "venues", "coingecko", "news", "fear_greed", "community"}
    assert all(v["ok"] for v in st.values()) and all(v["age_s"] is not None for v in st.values())
    n = len(world.requests)
    async with http_of(world) as http:
        await provider_status(http)
    assert len(world.requests) == n  # probes at most every 5 minutes
    common.reset()
    world.down = {"api.coingecko.com"}
    async with http_of(world) as http:
        st = await provider_status(http)
    assert st["coingecko"]["ok"] is False and st["coingecko"]["age_s"] is None and "503" in st["coingecko"]["detail"]


# ---------------------------------------------------------------- scenarios
def flat(n: int, px: float, span: int = H, t0: int = 0) -> list[Candle]:
    return [Candle(t0 + i * span, px, px * 1.001, px * 0.999, px, 10) for i in range(n)]


def lv(*pairs: tuple[float, str]) -> list[tuple[float, str, int]]:
    return [(v, lbl, i) for i, (v, lbl) in enumerate(pairs)]


def test_scenarios_follow_the_documented_rules() -> None:
    cs = flat(40, 100.0)
    atr = 2.0
    lows = lv((95.0, "1h pivot low"), (90.0, "4h pivot low"), (99.8, "1h pivot low"))
    highs = lv((101.0, "1h pivot high"), (101.8, "1h pivot high"), (104.0, "1h pivot high"), (110.0, "1d pivot high"), (120.0, "4h pivot high"))
    long = technical.scenario("long", 100.5, cs, "1h", atr, lows, highs)
    assert long["trigger"] == 101.0 and "nearest resistance above the price 100.50: 1h pivot high 101.00" in long["trigger_rule"]
    # 99.8 is only 1.2 below the trigger (< 0.75 ATR + buffer would leave < 1 ATR): skipped for 95
    assert long["invalidation"] == 95.0 - 0.25 * atr and "(1h pivot low 95) minus 0.25 x ATR14(1h) 2 = 94.5" in long["invalidation_rule"]
    # R = 6.5: 101.8 and 104 are closer than 1R, so T1 is 110; T2 must be >= T1 + 1 ATR
    assert long["targets"] == [110.0, 120.0]
    assert long["target_rule"].startswith("T1 = 1d pivot high 110.00 (first resistance at least max(1R, 1 x ATR) = 6.5 beyond the trigger")
    assert "T2 = 4h pivot high 120.00 (next resistance at least 1 x ATR beyond T1)" in long["target_rule"]
    short = technical.scenario("short", 100.5, cs, "1h", atr, lows, highs)
    assert short["trigger"] == 99.8 and short["invalidation"] == 101.8 + 0.25 * atr  # 101 is only 1.2 above: skipped
    assert short["targets"] == [95.0, 90.0]


def test_scenario_falls_back_to_breakout_level_and_atr_targets() -> None:
    cs = flat(40, 100.0)
    hi20, lo20 = max(c.h for c in cs[-20:]), min(c.l for c in cs[-20:])
    long = technical.scenario("long", 100.5, cs, "1h", 2.0, lv((97.0, "1h pivot low")), [])
    assert long["trigger"] == round(hi20, 4) and "highest high of the last 20 closed 1h candles" in long["trigger_rule"]
    assert long["invalidation"] == 96.5
    assert long["targets"] == [round(hi20 + 4, 4), round(hi20 + 6, 4)]
    assert "T1 = trigger + 2 x ATR14(1h) 2 = " in long["target_rule"] and "T2 = trigger + 3 x ATR14(1h) 2 = " in long["target_rule"]
    # a far invalidation (R > 2 ATR): T1 = 1R, T2 = T1 + 1 ATR
    far = technical.scenario("long", 100.5, cs, "1h", 2.0, lv((92.0, "1d pivot low")), [])
    r = hi20 - 91.5
    assert far["targets"] == [round(hi20 + r, 4), round(hi20 + r + 2, 4)]
    assert "T1 = trigger + 1R (R = |trigger - invalidation| = " in far["target_rule"] and "T2 = T1 + 1 x ATR14(1h) 2" in far["target_rule"]
    # nothing behind the trigger: invalidation = trigger -/+ 1.5 ATR
    short = technical.scenario("short", 100.5, cs, "1h", 2.0, [], [])
    assert short["trigger"] == round(lo20, 4) and short["invalidation"] == round(lo20 + 3, 4)
    assert "no resistance at least 0.75 x ATR above the trigger: trigger + 1.5 x ATR14(1h) 2" in short["invalidation_rule"]
    assert short["targets"] == [round(lo20 - 4, 4), round(lo20 - 6, 4)]
    assert technical.scenario("long", 100.5, cs[:10], "1h", 2.0, [], []) is None  # too few candles
    assert technical.scenario("long", 100.5, cs, "1h", None, [], []) is None  # no ATR


def test_levels_within_a_quarter_atr_are_merged() -> None:
    cs = flat(40, 99.8)  # ranges reach 100.3 with the 0.5 tolerance, not 100.6
    pool = lv((100.0, "1h pivot high"), (100.3, "4h pivot high"), (100.6, "1d pivot high"), (105.0, "1d pivot high"))
    out = technical.cluster(pool, cs, 2.0, "1h")
    assert [v for v, _, _ in out] == [100.3, 105.0]  # {100, 100.3}: tie on touches, the more recent wins; then 100.6 is within 0.5 of 100.3
    assert out[0][1] == "4h pivot high (kept from 3 levels within 0.25 x ATR14(1h) 2: most touches (40), then most recent)"
    assert technical.cluster(pool, cs, None, "1h") == pool


def assert_scenario_spacing(p: dict[str, Any]) -> None:
    t = p["sections"]["technical"]["facts"]
    atr = t["frames"][p["timeframe"]]["atr14"]
    for side, sc in t["scenarios"].items():
        assert sc is not None, side
        trig, inv, tg = sc["trigger"], sc["invalidation"], sc["targets"]
        r = abs(trig - inv)
        assert r >= atr * 0.999, (side, sc)
        assert abs(tg[0] - trig) >= max(r, atr) * 0.999, (side, sc)
        assert len(tg) == 2 and abs(tg[1] - tg[0]) >= atr * 0.999, (side, sc)
        assert (tg[0] > trig) == (side == "long") and (inv < trig) == (side == "long")
    for levels in (t["support"], t["resistance"]):
        assert all(abs(a - b) > 0.25 * atr for a, b in zip(levels, levels[1:])), levels


async def test_btc_fixture_scenarios_are_not_too_tight(world: World, db: Any) -> None:
    # the first version gave T1 82,905.5 for a trigger at 82,867.7 (0.05%) on this fixture: pivots 0.04% apart were all kept
    p = await pack(world, db, "BTC")
    assert_scenario_spacing(p)
    for h in (6, 30, 77):  # and at other moments of the fixture's price waves
        world.now_ms -= h * H
        common.reset()
        market._shared._cache.clear()  # type: ignore[union-attr]
        assert_scenario_spacing(await pack(world, db, "BTC"))


def test_technical_build_with_too_few_candles_has_null_scenarios() -> None:
    now = datetime.now(timezone.utc)
    t0 = int(now.timestamp() * 1000) // H * H - 11 * H
    out = technical.build({"1h": flat(10, 50.0, H, t0), "4h": [], "1d": []}, "1h", 50.0, now)
    assert out["facts"]["scenarios"] == {"long": None, "short": None}
    assert out["status"] == "partial" and any("scenarios can't be derived" in x for x in out["limitations"])
    assert technical.build({"1h": []}, "1h", None, now)["status"] == "unavailable"


def test_technical_lean_rules() -> None:
    up = {"trend": "up", "macd_hist": 1.0, "rsi14": 55}
    assert technical.lean({"1h": up, "4h": {"trend": "up"}, "1d": {"trend": "up"}}, "1h")[:2] == ("bullish", 2)
    assert technical.lean({"1h": {**up, "rsi14": 75}, "4h": {"trend": "range"}, "1d": {"trend": "down"}}, "1h")[:2] == ("neutral", 0)
    assert technical.lean({"1h": {"trend": "down", "macd_hist": -1.0, "rsi14": 25}, "4h": {"trend": "down"}}, "1h")[:2] == ("bearish", -1)
    assert technical.lean({"1h": None}, "1h")[:2] == ("unknown", None)


# ---------------------------------------------------------------- indicators added for the pack
def test_new_indicators() -> None:
    highs = [1, 3, 2, 5, 4, 6, 5, 7, 6]
    lows = [0, 2, 1, 4, 3, 5, 4, 6, 5]
    pl, ph = ind.pivots(highs, lows, k=1)
    assert ph == [3, 5, 6, 7] and pl == [1, 3, 4]
    assert ind.swing_structure(highs, lows, k=1) == "HH/HL"
    assert ind.swing_structure([h * -1 for h in highs], [lo * -1 for lo in lows], k=1) in ("LH/LL", "mixed")
    assert ind.nearest_levels([10, 10.01, 11, 9, 12], 9.5, below=False, n=2) == [10, 11]  # 10.01 clusters with 10
    assert ind.volume_ratio([1] * 20 + [3]) == 3.0 and ind.volume_ratio([1] * 5) is None
    assert ind.realized_vol_pct([100.0] * 21) == 0.0 and ind.realized_vol_pct([100.0] * 5) is None
    assert ind.realized_vol_pct([100 * (1.01 if i % 2 else 1) for i in range(21)]) == pytest.approx(math.log(1.01) * 100, rel=1e-6)


def test_assist_levels_still_work() -> None:
    from app.tradingos import assist

    cs = [Candle(i, 1, h, lo, 1, 1) for i, (h, lo) in enumerate(zip([1, 3, 2, 5, 4], [0, 2, 1, 4, 3]))]
    assert assist._pivots(cs, 1) == ([1], [3, 5])
    assert assist._nearest([1, 2, 3], 2.5, True) == [2, 1]


# ---------------------------------------------------------------- RSS safety
def test_rss_parser_refuses_entities_and_big_bodies() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]><rss><channel><item><title>&lol2;</title></item></channel></rss>'
    with pytest.raises(common.FetchError):
        news.parse(bomb, "Evil")
    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>ETH upgrade ships</title>
    <link href="https://ex.com/a"/><updated>2026-10-22T10:00:00Z</updated><summary>&lt;b&gt;Big&lt;/b&gt; news</summary></entry></feed>"""
    (it,) = news.parse(atom, "Ex")
    assert it["url"] == "https://ex.com/a" and it["summary"] == "Big news" and it["published"].tzinfo is not None



async def test_feed_over_2mb_is_refused() -> None:
    common.reset()
    big = b"<rss>" + b" " * (common.MAX_BYTES + 10) + b"</rss>"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=big))) as http:
        with pytest.raises(common.FetchError, match="more than 2 MB"):
            await news.fetch_feed(http, "https://big.example/feed")


def test_news_feeds_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMITTEE_NEWS_FEEDS", "https://a.example/rss, https://www.coindesk.com/arc/outboundfeeds/rss/")
    assert news.feeds() == ["https://a.example/rss", "https://www.coindesk.com/arc/outboundfeeds/rss/"]
    assert news.source_name(news.feeds()[1]) == "CoinDesk" and news.source_name(news.feeds()[0]) == "a.example"


def test_evidence_never_imports_execution() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "committee" / "evidence"
    for f in root.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert not re.search(r"tradingos\.(execution|exchange|accounts)|tradingos import .*\b(execution|exchange|accounts)\b", src), f.name


async def test_hyperliquid_down_never_falls_back_to_spot(world: World, db: Any) -> None:
    world.down = {"api.hyperliquid.xyz"}
    with pytest.raises(EvidenceError) as e:
        await pack(world, db, "BTC")
    assert e.value.code == "no_data" and "Hyperliquid" in str(e.value)
    p = await pack(world, db, "BTC okx")  # an exchange named explicitly still works, labelled as what it is
    assert p["asset"]["label"] == "OKX spot BTC-USDT" and p["asset"]["hl_tradable"] is False
    assert any(d.startswith("hyperliquid:") for d in p["degraded"])
