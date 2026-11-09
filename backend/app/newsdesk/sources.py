"""Discover and verify: RSS feeds, prices over time, and a second opinion on crypto prices.

Prices come from Yahoo Finance's public chart endpoint, which needs no key but isn't a licensed feed: swapping `fetch_quote` for a paid
provider changes nothing else. Crypto is checked against Hyperliquid before anything is believed.
"""

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

import httpx

from app.brand_manager.discovery import parse_feed

logger = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (compatible; PriceChatNewsDesk/1.0; +https://pricechat.live)"}
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CATEGORIES = ("crypto", "stocks", "commodities", "politics", "economics", "viral")
# tier: 0 official, 1 major outlet, 2 specialist. category None: decided from the headline's words.
DEFAULT_FEEDS: list[dict[str, Any]] = [
    {"name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "tier": 0, "category": "economics"},
    {"name": "ECB", "url": "https://www.ecb.europa.eu/rss/press.html", "tier": 0, "category": "economics"},
    {"name": "SEC", "url": "https://www.sec.gov/news/pressreleases.rss", "tier": 0, "category": None},
    {"name": "White House", "url": "https://www.whitehouse.gov/news/feed/", "tier": 0, "category": "politics"},
    {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "tier": 1, "category": None},
    {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/world/rss.xml", "tier": 1, "category": "politics"},
    {"name": "NPR", "url": "https://feeds.npr.org/1006/rss.xml", "tier": 1, "category": None},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml", "tier": 1, "category": "politics"},
    {"name": "CNBC", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664", "tier": 1, "category": None},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", "tier": 1, "category": None},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss", "tier": 2, "category": "crypto"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed", "tier": 2, "category": "crypto"},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml", "tier": 2, "category": "crypto"},
]
# group: which thresholds apply. hl: its Hyperliquid name, for the second opinion. step: a round-number milestone every this much.
DEFAULT_WATCHLIST: list[dict[str, Any]] = [
    {"symbol": "BTC-USD", "name": "Bitcoin", "group": "crypto_major", "category": "crypto", "emoji": "₿", "hl": "BTC", "step": 5000},
    {"symbol": "ETH-USD", "name": "Ethereum", "group": "crypto_major", "category": "crypto", "emoji": "🔷", "hl": "ETH", "step": 500},
    {"symbol": "SOL-USD", "name": "Solana", "group": "crypto_alt", "category": "crypto", "emoji": "🟣", "hl": "SOL"},
    {"symbol": "XRP-USD", "name": "XRP", "group": "crypto_alt", "category": "crypto", "emoji": "💧", "hl": "XRP"},
    {"symbol": "^GSPC", "name": "S&P 500", "group": "index", "category": "stocks", "emoji": "📊"},
    {"symbol": "^IXIC", "name": "Nasdaq", "group": "index", "category": "stocks", "emoji": "📊"},
    {"symbol": "^DJI", "name": "Dow Jones", "group": "index", "category": "stocks", "emoji": "📊"},
    {"symbol": "NVDA", "name": "NVIDIA", "group": "stock", "category": "stocks", "emoji": "📈"},
    {"symbol": "TSLA", "name": "Tesla", "group": "stock", "category": "stocks", "emoji": "📈"},
    {"symbol": "AAPL", "name": "Apple", "group": "stock", "category": "stocks", "emoji": "📈"},
    {"symbol": "GC=F", "name": "Gold", "group": "commodity", "category": "commodities", "emoji": "🥇", "step": 100},
    {"symbol": "SI=F", "name": "Silver", "group": "commodity", "category": "commodities", "emoji": "🥈", "step": 5},
    {"symbol": "CL=F", "name": "Oil (WTI)", "group": "commodity", "category": "commodities", "emoji": "🛢️", "step": 10},
    {"symbol": "HG=F", "name": "Copper", "group": "commodity", "category": "commodities", "emoji": "🟠"},
]
# Tiered additions (2026-11-09: the desk was starved by thin crypto coverage and a dead MarketWatch feed). Read alongside the feeds stored
# in Admin (which a new list here wouldn't reach), unless state.extra_feeds is false. A second crypto outlet is what lets most crypto
# stories meet the evidence rules (two outlets), so this widens what can be verified without lowering the bar.
EXTRA_FEEDS: list[dict[str, Any]] = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "tier": 2, "category": "crypto"},
    {"name": "The Defiant", "url": "https://thedefiant.io/api/feed", "tier": 2, "category": "crypto"},
    {"name": "CryptoSlate", "url": "https://cryptoslate.com/feed/", "tier": 2, "category": "crypto"},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/.rss/full/", "tier": 2, "category": "crypto"},
    {"name": "CNBC", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "tier": 1, "category": "economics"},
    {"name": "OilPrice", "url": "https://oilprice.com/rss/main", "tier": 2, "category": "commodities"},
    {"name": "CFTC", "url": "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", "tier": 0, "category": None},
]
STALE_AFTER = timedelta(days=7)  # a feed whose newest item is older than this is broken, whatever its HTTP status says


def feeds_for(stored: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """The stored feeds plus the tiered additions not already there (by address)."""
    if state.get("extra_feeds") is False:
        return list(stored)
    have = {f.get("url") for f in stored}
    return [*stored, *[f for f in EXTRA_FEEDS if f["url"] not in have]]


# percent moves at sensitivity "medium"
THRESHOLDS: dict[str, dict[str, float]] = {
    "crypto_major": {"5m": 1.5, "1h": 3.0, "24h": 8.0},
    "crypto_alt": {"5m": 3.0, "1h": 6.0, "24h": 15.0},
    "index": {"5m": 0.8, "1h": 1.2, "24h": 2.5},
    "stock": {"5m": 2.5, "1h": 4.0, "24h": 8.0},
    "commodity": {"5m": 1.2, "1h": 2.5, "24h": 5.0},
}
WINDOWS = {"5m": 300, "1h": 3600, "24h": 86400}
FRESH_SECONDS = 1200  # older than this, the market is closed or the data is late: no move can be claimed
CROSS_CHECK_PCT = 1.0  # two sources may differ by this much


@dataclass
class Quote:
    symbol: str
    price: float
    as_of: datetime
    high_52w: float | None
    low_52w: float | None
    # (unix seconds, close, volume), oldest first
    bars: list[tuple[int, float, float]] = field(default_factory=list)

    def change(self, window: str, now: datetime) -> float | None:
        """Percent change over the window, or None if there's no bar close enough to compare with (a closed market, a holiday)."""
        if (now - self.as_of).total_seconds() > FRESH_SECONDS or not self.bars:
            return None
        seconds = WINDOWS[window]
        want = self.bars[-1][0] - seconds
        best = min(self.bars, key=lambda b: abs(b[0] - want))
        if abs(best[0] - want) > max(600, seconds * 0.05) or best[1] <= 0:
            return None
        return (self.price - best[1]) / best[1] * 100

    def volume_spike(self) -> float:
        """The latest bar's volume against the median bar's."""
        vols = [b[2] for b in self.bars if b[2] > 0]
        if len(vols) < 20 or self.bars[-1][2] <= 0:
            return 0.0
        return self.bars[-1][2] / (median(vols) or 1)


def parse_chart(symbol: str, payload: dict[str, Any]) -> Quote | None:
    """None unless the answer is complete and makes sense: a wrong number must never reach a post."""
    try:
        result = payload["chart"]["result"][0]
        meta, stamps = result["meta"], result["timestamp"]
        q = result["indicators"]["quote"][0]
        closes, vols = q["close"], q.get("volume") or []
    except (KeyError, IndexError, TypeError):
        return None
    bars = [(int(t), float(c), float(vols[i] or 0) if i < len(vols) else 0.0) for i, (t, c) in enumerate(zip(stamps, closes)) if c]
    price, at = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if not bars or not isinstance(price, (int, float)) or price <= 0 or not at:
        return None
    if abs(price - bars[-1][1]) / price * 100 > CROSS_CHECK_PCT:
        return None  # the headline price and the chart disagree
    return Quote(symbol, float(price), datetime.fromtimestamp(int(at), tz=timezone.utc), meta.get("fiftyTwoWeekHigh"), meta.get("fiftyTwoWeekLow"), bars)


async def fetch_quote(client: httpx.AsyncClient, symbol: str) -> Quote | None:
    try:
        r = await client.get(CHART.format(symbol=symbol), params={"range": "5d", "interval": "5m"}, headers=UA, timeout=15)
    except httpx.HTTPError as e:
        logger.info("newsdesk: quote %s: %s", symbol, type(e).__name__)
        return None
    if r.status_code != 200:
        logger.info("newsdesk: quote %s: HTTP %s", symbol, r.status_code)
        return None
    try:
        return parse_chart(symbol, r.json())
    except ValueError:
        return None


async def fetch_quotes(client: httpx.AsyncClient, watchlist: list[dict[str, Any]]) -> dict[str, Quote]:
    out: dict[str, Quote] = {}
    for w in watchlist:  # one at a time: this is a courtesy endpoint
        q = await fetch_quote(client, w["symbol"])
        if q is not None:
            out[w["symbol"]] = q
        await asyncio.sleep(0.25)
    return out


async def second_opinion(item: dict[str, Any], price: float, market: Any = None) -> tuple[bool, str]:
    """Crypto must agree with Hyperliquid. Others have one source, and say so in the checks."""
    name = item.get("hl")
    if not name:
        return True, "one price source (Yahoo Finance)"
    try:
        if market is None:
            from app.tradingos.market import MarketData

            market = MarketData()
        other = (await asyncio.to_thread(market.market, name)).mark
    except Exception as e:  # noqa: BLE001 - any failure means not verified
        return False, f"Hyperliquid couldn't confirm the price ({type(e).__name__})"
    diff = abs(other - price) / price * 100
    return diff <= CROSS_CHECK_PCT, f"Hyperliquid {other:,.2f} vs Yahoo {price:,.2f}: {diff:.2f}% apart"


def clean_title(title: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", " ", title or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"\s+[-–|]\s+(BBC News|BBC|CNBC|NPR|MarketWatch|Al Jazeera)$", "", t)


async def fetch_feed(client: httpx.AsyncClient, feed: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """(items, problem). A feed being down never stops the others."""
    try:
        r = await client.get(feed["url"], headers=UA, timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        return [], type(e).__name__
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}"
    items = parse_feed(r.text, feed["name"])
    return items, None if items else "no items"


def recent(items: list[dict[str, Any]], now: datetime, hours: int = 36) -> list[dict[str, Any]]:
    """Dated, not in the future, and new enough: an undated item can't be verified as news."""
    cutoff = now - timedelta(hours=hours)
    return [i for i in items if i.get("at") and cutoff <= i["at"] <= now + timedelta(minutes=10)]
