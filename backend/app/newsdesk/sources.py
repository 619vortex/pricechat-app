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

UA = {"User-Agent": "Mozilla/5.0 (compatible; PriceChatNewsDesk/1.0; +https://pricechat.live)",
      "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"}
# The same reader without the "Mozilla/5.0" prefix. Some CDNs refuse a client that calls itself Mozilla but doesn't behave like a browser
# (2026-11-24: The Guardian answered every read 403 for a day; the identical request minus "Mozilla/5.0" gets 200, checked from the same
# machine). Saying plainly what we are is the honest fix, not a workaround: a feed marked agent "plain" is always read this way, and any
# feed answering 403 or 406 is asked once more this way before it counts as failed.
PLAIN_UA = {**UA, "User-Agent": "PriceChatNewsDesk/1.0 (+https://pricechat.live)"}
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
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "tier": 1, "category": None},
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
    # global markets, macro and world news (owner, 2026-11-10: "repair or replace the broken feeds, improve global market coverage").
    # Each checked live the same day (fresh items, parseable). Headline feeds only: the desk never copies an article, it credits the outlet.
    {"name": "Bloomberg", "url": "https://feeds.bloomberg.com/markets/news.rss", "tier": 1, "category": None},
    {"name": "Bloomberg", "url": "https://feeds.bloomberg.com/economics/news.rss", "tier": 1, "category": "economics"},
    {"name": "Bloomberg", "url": "https://feeds.bloomberg.com/politics/news.rss", "tier": 1, "category": "politics"},
    {"name": "Financial Times", "url": "https://www.ft.com/markets?format=rss", "tier": 1, "category": None},
    {"name": "Financial Times", "url": "https://www.ft.com/world?format=rss", "tier": 1, "category": "politics"},
    {"name": "New York Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "tier": 1, "category": None},
    {"name": "New York Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml", "tier": 1, "category": "economics"},
    {"name": "New York Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "tier": 1, "category": "politics"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/business/rss", "tier": 1, "category": None, "agent": "plain"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/business/economics/rss", "tier": 1, "category": "economics", "agent": "plain"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/world/rss", "tier": 1, "category": "politics", "agent": "plain"},
    {"name": "CNBC", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069", "tier": 1, "category": None},
    {"name": "CNBC", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362", "tier": 1, "category": "politics"},
    {"name": "CNBC", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19836768", "tier": 1, "category": "commodities"},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "tier": 1, "category": None},
    {"name": "France 24", "url": "https://www.france24.com/en/business/rss", "tier": 1, "category": None},
    {"name": "Mining.com", "url": "https://www.mining.com/feed/", "tier": 2, "category": "commodities"},
    # official macro data and central banks (tier 0: their own announcements)
    {"name": "BLS", "url": "https://www.bls.gov/feed/bls_latest.rss", "tier": 0, "category": "economics"},
    {"name": "Bank of England", "url": "https://www.bankofengland.co.uk/rss/news", "tier": 0, "category": "economics"},
    {"name": "Bank of Japan", "url": "https://www.boj.or.jp/en/rss/whatsnew.xml", "tier": 0, "category": "economics"},
    # fast world desks (2026-11-24: more independent mainstream outlets, so a major world story reaches two-outlet verification sooner).
    # Each checked live that day: fresh, dated, parseable, allowed by the site's robots.txt; named as app/trending's RELIABLE_OUTLETS and
    # MAINSTREAM spell them. Measured: BBC top stories and ABC News a new item every ~10 min, The Independent and SCMP every ~15 min,
    # Sky News and The Washington Post every few hours (short lists, but early on big breaking stories).
    {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/rss.xml", "tier": 1, "category": None},
    {"name": "The Independent", "url": "https://www.independent.co.uk/news/world/rss", "tier": 1, "category": "politics"},
    {"name": "Sky News", "url": "https://feeds.skynews.com/feeds/rss/world.xml", "tier": 1, "category": "politics"},
    {"name": "ABC News", "url": "https://abcnews.go.com/abcnews/internationalheadlines", "tier": 1, "category": "politics"},
    {"name": "The Washington Post", "url": "https://feeds.washingtonpost.com/rss/world", "tier": 1, "category": "politics"},
    {"name": "South China Morning Post", "url": "https://www.scmp.com/rss/91/feed", "tier": 1, "category": None},
]
# World Monitor (owner, 2026-11-10: "integrate World Monitor as an additional global intelligence source"). World Monitor's code is
# AGPL-3.0 and its hosted API's terms forbid public redistribution below the $299.99/month Business plan, so neither is used. Its source
# catalog was used only to find publishers we lacked; each feed below is that publisher's own public RSS, fetched directly, checked live
# 2026-11-10 (fresh, dated, parseable). Google News copies, mirrors, proxies, bot-blocked sites and press-release wires were left out.
# "via" credits the catalog in Admin; posts credit the publisher, as for every other feed. state.worldmonitor_feeds = false turns them off.
WORLDMONITOR_VIA = "World Monitor catalog"
WORLDMONITOR_FEEDS: list[dict[str, Any]] = [{**f, "via": WORLDMONITOR_VIA} for f in [
    # world and geopolitics: more independent outlets, so more major stories reach two-outlet verification
    {"name": "DW", "url": "https://rss.dw.com/xml/rss-en-all", "tier": 1, "category": "politics"},
    # Euronews left out 2026-11-11: answers HTTP 406 to Railway's servers on every fetch (fine from a home connection)
    {"name": "Le Monde", "url": "https://www.lemonde.fr/en/rss/une.xml", "tier": 1, "category": "politics"},
    {"name": "CNA", "url": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "tier": 1, "category": None},
    {"name": "ABC Australia", "url": "https://www.abc.net.au/news/feed/2942460/rss.xml", "tier": 1, "category": "politics"},
    {"name": "NBC News", "url": "https://feeds.nbcnews.com/nbcnews/public/news", "tier": 1, "category": "politics"},
    {"name": "CBS News", "url": "https://www.cbsnews.com/latest/rss/main", "tier": 1, "category": "politics"},
    {"name": "Axios", "url": "https://api.axios.com/feed/", "tier": 1, "category": None},
    {"name": "Politico", "url": "https://rss.politico.com/politics-news.xml", "tier": 1, "category": "politics"},
    {"name": "The National", "url": "https://www.thenationalnews.com/arc/outboundfeeds/rss/?outputType=xml", "tier": 1, "category": None},
    {"name": "UN News", "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "tier": 0, "category": "politics"},
    # conflicts, defence and shipping
    {"name": "The War Zone", "url": "https://www.twz.com/feed", "tier": 2, "category": "politics"},
    {"name": "Breaking Defense", "url": "https://breakingdefense.com/feed/", "tier": 2, "category": "politics"},
    {"name": "Defense One", "url": "https://www.defenseone.com/rss/all/", "tier": 2, "category": "politics"},
    {"name": "gCaptain", "url": "https://gcaptain.com/feed/", "tier": 2, "category": "commodities"},
    {"name": "Meduza", "url": "https://meduza.io/rss/en/all", "tier": 2, "category": "politics"},
    {"name": "Moscow Times", "url": "https://www.themoscowtimes.com/rss/news", "tier": 2, "category": "politics"},
    # markets and crypto
    {"name": "Fox Business", "url": "https://moxie.foxbusiness.com/google-publisher/latest.xml", "tier": 1, "category": None},
    {"name": "Seeking Alpha", "url": "https://seekingalpha.com/market_currents.xml", "tier": 2, "category": "stocks"},
    {"name": "Unchained", "url": "https://unchainedcrypto.com/feed/", "tier": 2, "category": "crypto"},
]]
WORLDMONITOR_NAMES = {f["name"].lower() for f in WORLDMONITOR_FEEDS}

# feeds that died: dropped even when stored in Admin (MarketWatch's market pulse has had nothing new since 3 Jul 2025)
RETIRED_FEEDS = {"https://feeds.content.dowjones.io/public/rss/mw_marketpulse"}
STALE_AFTER = timedelta(days=7)  # a feed whose newest item is older than this is broken, whatever its HTTP status says


def feeds_for(stored: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """The stored feeds plus the tiered additions not already there (by address)."""
    stored = [f for f in stored if f.get("url") not in RETIRED_FEEDS]
    if state.get("extra_feeds") is False:
        return list(stored)
    have = {f.get("url") for f in stored}
    extra = [*EXTRA_FEEDS, *(WORLDMONITOR_FEEDS if state.get("worldmonitor_feeds") is not False else [])]
    return [*stored, *[f for f in extra if f["url"] not in have]]


def via(name: str) -> str | None:
    """Where the desk found this outlet's feed, when it wasn't our own pick (Admin attribution)."""
    return WORLDMONITOR_VIA if (name or "").strip().lower() in WORLDMONITOR_NAMES else None


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
    # the exchange session, from Yahoo's own metadata (stocks and indices): the official previous close a day's change is measured from,
    # the regular session the price belongs to, and when the previous session closed (its weekday names the reference: "Thursday's close")
    previous_close: float | None = None
    session_open: datetime | None = None
    session_close: datetime | None = None
    previous_session_close: datetime | None = None

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
    return Quote(symbol, float(price), datetime.fromtimestamp(int(at), tz=timezone.utc), meta.get("fiftyTwoWeekHigh"), meta.get("fiftyTwoWeekLow"), bars,
                 **_session(meta, float(price)))


def _utc_ts(v: Any) -> datetime | None:
    return datetime.fromtimestamp(int(v), tz=timezone.utc) if isinstance(v, (int, float)) and v > 0 else None


def _session(meta: dict[str, Any], price: float) -> dict[str, Any]:
    """Yahoo's previousClose (the official close of the session before the current one: 23,879.20 for the Nasdaq on 2026-11-20, where
    the last 5-minute bar of 11-19 said 23,882.10) and the regular session times. A previous close that disagrees wildly with the price is
    not believed (a day's move over 50% on a watchlist instrument is a data error, not a story)."""
    out: dict[str, Any] = {}
    prev = meta.get("previousClose")
    if isinstance(prev, (int, float)) and prev > 0 and abs(price - prev) / prev < 0.5:
        out["previous_close"] = float(prev)
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = _utc_ts(regular.get("start")), _utc_ts(regular.get("end"))
    if start and end and end > start:
        out["session_open"], out["session_close"] = start, end
        ends = []
        for day in meta.get("tradingPeriods") or []:
            for p in day if isinstance(day, list) else [day]:
                e = _utc_ts(p.get("end")) if isinstance(p, dict) else None
                if e is not None and e <= start:
                    ends.append(e)
        if ends:
            out["previous_session_close"] = max(ends)
    return out


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


# an independent second price for what Yahoo measures outside crypto (owner, 2026-11-21, Phase 1: "independent price verification"):
# Hyperliquid's trade.xyz markets, whose oracle price follows the index, the stock or the commodity. Checked 2026-11-21 against Yahoo:
# SP500 6,960.6 vs ^GSPC, GOLD 3,533.9 vs GC=F 3,515.4 (both on a Saturday, after Friday's close). There is no trade.xyz market for the Dow
# or the Nasdaq Composite (XYZ100 is the Nasdaq-100, another index): those keep one source and say so.
XYZ = {"^GSPC": "xyz:SP500", "NVDA": "xyz:NVDA", "TSLA": "xyz:TSLA", "AAPL": "xyz:AAPL", "COIN": "xyz:COIN", "MSTR": "xyz:MSTR", "META": "xyz:META",
       "AMZN": "xyz:AMZN", "MSFT": "xyz:MSFT", "GOOGL": "xyz:GOOGL", "GC=F": "xyz:GOLD", "SI=F": "xyz:SILVER", "CL=F": "xyz:CL", "BZ=F": "xyz:BRENTOIL",
       "HG=F": "xyz:COPPER"}
XYZ_TOLERANCE = {"index": 1.0, "stock": 1.5, "commodity": 1.5}  # percent: a perpetual's oracle and a futures front month or a quote a minute old
XYZ_TTL = 60.0
_xyz: tuple[float, dict[str, tuple[float, float]]] | None = None


async def xyz_prices(transport: Any = None) -> dict[str, tuple[float, float]]:
    """{trade.xyz market: (oracle price, mark price)}, read at most once a minute."""
    global _xyz
    import time

    if _xyz is not None and time.monotonic() - _xyz[0] <= XYZ_TTL:
        return _xyz[1]
    async with httpx.AsyncClient(transport=transport, timeout=8) as c:
        r = await c.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs", "dex": "xyz"})
        r.raise_for_status()
        meta, ctxs = r.json()
    # (oracle, mark, price 24 hours ago): the third lets a move be compared like for like (FUTURES_BASIS below)
    out = {str(a.get("name")): (float(x.get("oraclePx") or 0), float(x.get("markPx") or 0), float(x.get("prevDayPx") or 0))
           for a, x in zip(meta["universe"], ctxs)}
    _xyz = (time.monotonic(), out)
    return out


# The Nasdaq Composite and the Dow have no second source the desk may read (owner, 2026-11-23: verify them from the actual Railway environment,
# never DIA x 100 as the Dow): trade.xyz has neither index; Nasdaq.com answers a browser but stalls any automated reader that says who it is
# (checked 2026-11-23: our User-Agent times out, a browser's gets 200; we do not pretend to be a browser); Cboe's DJX feed is behind a
# Cloudflare challenge; WSJ's quote API needs a site token. So a Nasdaq-only or Dow-only move is never published automatically (one source:
# held, events.held_market). A market-wide US move is verified on its S&P 500 member (trade.xyz) and prints the Nasdaq and the Dow beside it
# as Yahoo's figures. A keyed market-data provider would add them (owner's decision).
# the basis between two markets widens with the move: a perpetual's oracle vs a futures front month. On a 6% day silver's trade.xyz oracle
# and COMEX were 1.63% apart (refused at a flat 1.5%, production 2026-11-23): the tolerance grows by this share of the move. A unit error or
# a wrong instrument is still many times outside it.
MOVE_ALLOWANCE = 0.25
# 2026-11-24: comparing LEVELS of a perpetual's oracle and a COMEX/NYMEX futures contract measures the futures basis, not the data: on 11-23
# copper's trade.xyz oracle and Yahoo's HG=F were 2.6-3.0% apart on every pass and every copper move was held as "two markets disagreeing".
# Equivalent instruments are compared on what they share: for a futures symbol (Yahoo "=F") the level only has to be the same instrument
# within FUTURES_BASIS (a unit error or a wrong market is far outside it), and a day-sized move (|change| >= MOVE_CHECK_MIN) must show on
# trade.xyz too: the same direction and at least MOVE_CONFIRM_SHARE of its size over trade.xyz's own last 24 hours.
FUTURES_BASIS = 5.0
MOVE_CHECK_MIN = 2.0
MOVE_CONFIRM_SHARE = 0.33


async def call_second(fn: Any, item: dict[str, Any], price: float, market: Any = None, change: float = 0.0) -> tuple[bool, str]:
    """second_opinion (or a stand-in with the older signature, without the move's size)."""
    import inspect

    try:
        params = inspect.signature(fn).parameters
        takes = "change" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):
        takes = False
    if market is None:
        return await (fn(item, price, change=change) if takes else fn(item, price))
    return await (fn(item, price, market, change=change) if takes else fn(item, price, market))


def has_second(item: dict[str, Any]) -> bool:
    return bool(item.get("hl")) or str(item.get("symbol") or "") in XYZ


async def second_opinion(item: dict[str, Any], price: float, market: Any = None, transport: Any = None, change: float | None = None) -> tuple[bool, str]:
    """Crypto must agree with Hyperliquid; indices, stocks and commodities with a trade.xyz market must agree with its oracle price. The
    tolerance grows with the move (`change`, %, MOVE_ALLOWANCE). A disagreement
    ("... apart") is a refusal and a safety anomaly. Owner, 2026-11-23: "No automatic major-move publishing without reliable verification": a
    second market that can't be read is a refusal too (not an anomaly: nothing disagreed). An instrument with no second market at all keeps one
    source and says so."""
    name = item.get("hl")
    symbol = str(item.get("symbol") or "")
    allowance = MOVE_ALLOWANCE * abs(float(change or 0))
    if not name and symbol in XYZ:
        try:
            got = (await xyz_prices(transport)).get(XYZ[symbol])
        except Exception as e:  # noqa: BLE001 - unreadable: not verified
            return False, f"not verified: Hyperliquid trade.xyz couldn't be read ({type(e).__name__})"
        if not got or got[0] <= 0:
            return False, "not verified: no trade.xyz price"
        other = got[0]
        diff = abs(other - price) / price * 100
        futures = symbol.endswith("=F")
        tol = (FUTURES_BASIS if futures else XYZ_TOLERANCE.get(str(item.get("group") or ""), 1.5)) + allowance
        said = f"Hyperliquid trade.xyz {XYZ[symbol][4:]} {other:,.2f} vs Yahoo {price:,.2f}: {diff:.2f}% apart (tolerance {tol:.2f}%)"
        if diff > tol:
            return False, said
        prev = float(got[2]) if len(got) > 2 and got[2] else 0.0
        if futures and change is not None and abs(float(change)) >= MOVE_CHECK_MIN:
            if prev <= 0:
                return False, f"not verified: trade.xyz {XYZ[symbol][4:]} has no 24-hour price to compare the {float(change):+.2f}% move with"
            theirs = (other / prev - 1) * 100
            if theirs * float(change) <= 0 or abs(theirs) < MOVE_CONFIRM_SHARE * abs(float(change)):
                return False, (f"Hyperliquid trade.xyz {XYZ[symbol][4:]} moved {theirs:+.2f}% in 24 hours vs Yahoo {float(change):+.2f}%: "
                               f"the moves are apart (same contract family, basis {diff:.2f}%)")
            return True, f"{said}; trade.xyz moved {theirs:+.2f}% in 24 hours vs Yahoo {float(change):+.2f}% (futures basis allowed)"
        return True, said
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
    plain = feed.get("agent") == "plain"
    try:
        r = await client.get(feed["url"], headers=PLAIN_UA if plain else UA, timeout=15, follow_redirects=True)
        if r.status_code in (403, 406) and not plain:  # see PLAIN_UA: asked once more, saying plainly what we are
            r = await client.get(feed["url"], headers=PLAIN_UA, timeout=15, follow_redirects=True)
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
