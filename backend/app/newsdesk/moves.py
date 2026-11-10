"""Live market moves, measured from our own 5-minute prices: crashes and pumps over 15 minutes to a day, the whole crypto market or the US
indices moving at once, fresh 52-week extremes and round-number levels. The same code reads a live quote or a replayed one.

A move counts only from bars at or before `now`, and only while the latest bar is fresh: a closed market or late data claims nothing.
Each window has its own alert line per asset group (the move must beat it); `ratio` is how many times over the line it went.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.newsdesk import sources

WINDOWS = {"15m": 900, "1h": 3600, "4h": 14400, "24h": 86400}
WINDOW_WORDS = {"15m": "in 15 minutes", "1h": "in an hour", "4h": "in 4 hours", "24h": "in 24 hours"}
LINES: dict[str, dict[str, float]] = {
    "crypto_major": {"15m": 2.0, "1h": 3.0, "4h": 5.0, "24h": 8.0},
    "crypto_alt": {"15m": 4.0, "1h": 6.0, "4h": 10.0, "24h": 15.0},
    "index": {"15m": 0.9, "1h": 1.2, "4h": 1.8, "24h": 2.5},
    "stock": {"15m": 3.0, "1h": 4.5, "4h": 6.0, "24h": 8.0},
    "commodity": {"15m": 1.5, "1h": 2.5, "4h": 3.5, "24h": 5.0},
}
FRESH = 1200  # the latest bar must be this recent
# assets watched for moves beyond the desk's own watchlist: the names whose moves the market talks about
EXTRA_MOVERS: list[dict[str, Any]] = [
    {"symbol": "COIN", "name": "Coinbase", "group": "stock", "category": "stocks", "emoji": "📈"},
    {"symbol": "MSTR", "name": "Strategy (MSTR)", "group": "stock", "category": "stocks", "emoji": "📈"},
    {"symbol": "META", "name": "Meta", "group": "stock", "category": "stocks", "emoji": "📈"},
    {"symbol": "AMZN", "name": "Amazon", "group": "stock", "category": "stocks", "emoji": "📈"},
    {"symbol": "MSFT", "name": "Microsoft", "group": "stock", "category": "stocks", "emoji": "📈"},
    {"symbol": "GOOGL", "name": "Alphabet", "group": "stock", "category": "stocks", "emoji": "📈"},
    # Brent: oil stories are usually told in Brent ("Brent crashes 7% on Iran peace hopes"); without its prices the claim can't be checked
    {"symbol": "BZ=F", "name": "Oil (Brent)", "group": "commodity", "category": "commodities", "emoji": "🛢️"},
]
KEYS = {"BTC-USD": "BTC", "ETH-USD": "ETH", "SOL-USD": "SOL", "XRP-USD": "XRP", "^GSPC": "SPX", "^IXIC": "IXIC", "^DJI": "DJI", "GC=F": "GOLD", "SI=F": "SILVER",
        "CL=F": "OIL", "HG=F": "COPPER", "BZ=F": "BRENT"}
CASHTAG = {"BTC-USD": "BTC", "ETH-USD": "ETH", "SOL-USD": "SOL", "XRP-USD": "XRP", "GC=F": "GOLD", "SI=F": "SILVER", "NVDA": "NVDA", "TSLA": "TSLA", "AAPL": "AAPL",
           "COIN": "COIN", "MSTR": "MSTR", "META": "META", "AMZN": "AMZN", "MSFT": "MSFT", "GOOGL": "GOOGL"}


@dataclass
class Move:
    kind: str  # move | wide | high | low | milestone
    item: dict[str, Any]
    price: float
    as_of: datetime
    window: str = ""
    change: float = 0.0
    ratio: float = 0.0
    level: float = 0.0
    members: list[tuple[str, float]] = field(default_factory=list)  # a wide move: (name, change)
    spark: list[float] = field(default_factory=list)  # the last day's prices, for the card

    @property
    def symbol(self) -> str:
        return str(self.item.get("symbol", ""))

    @property
    def key(self) -> str:
        return KEYS.get(self.symbol, self.symbol)

    @property
    def up(self) -> bool:
        return self.change > 0 if self.kind in ("move", "wide") else self.kind == "high" or (self.kind == "milestone" and self.change > 0)


def price_at(q: sources.Quote, ts: float, *, tolerance: int = 900) -> float | None:
    """The last bar at or before ts, within the tolerance."""
    best = None
    for t, close, _ in q.bars:
        if t > ts:
            break
        if close > 0:
            best = (t, close)
    return best[1] if best and ts - best[0] <= tolerance else None


def spark(q: sources.Quote, now: datetime, hours: int = 24, points: int = 48) -> list[float]:
    ts = now.timestamp()
    bars = [c for t, c, _ in q.bars if ts - hours * 3600 <= t <= ts and c > 0]
    if len(bars) < 4:
        return []
    step = max(1, len(bars) // points)
    out = bars[::step]
    if out[-1] != bars[-1]:
        out.append(bars[-1])
    return [round(x, 6) for x in out]


def measure(q: sources.Quote, item: dict[str, Any], now: datetime) -> Move | None:
    """The biggest move past its line, over any window, as of now."""
    ts = now.timestamp()
    last = [b for b in q.bars if b[0] <= ts and b[1] > 0]
    if not last or ts - last[-1][0] > FRESH:
        return None
    price = last[-1][1]
    lines = LINES.get(item.get("group", "stock"), LINES["stock"])
    best: Move | None = None
    for window, secs in WINDOWS.items():
        then = price_at(q, last[-1][0] - secs, tolerance=max(600, int(secs * 0.05)))
        if not then:
            continue
        change = (price - then) / then * 100
        ratio = abs(change) / lines[window]
        if ratio >= 1 and (best is None or ratio > best.ratio):
            best = Move("move", item, price, datetime.fromtimestamp(last[-1][0], tz=now.tzinfo), window, change, ratio)
    return best


def biggest(q: sources.Quote, item: dict[str, Any], now: datetime) -> tuple[str, float, float] | None:
    """(window, change, ratio) of the largest move against its line, past the line or not: how big a story's price claim is."""
    ts = now.timestamp()
    price = price_at(q, ts, tolerance=1800)
    if not price:
        return None
    lines = LINES.get(item.get("group", "stock"), LINES["stock"])
    out = None
    for window, secs in WINDOWS.items():
        then = price_at(q, ts - secs, tolerance=1800)
        if then:
            change = (price - then) / then * 100
            ratio = abs(change) / lines[window]
            if out is None or ratio > out[2]:
                out = (window, change, ratio)
    return out


def extreme(q: sources.Quote, item: dict[str, Any], now: datetime) -> Move | None:
    """A fresh 52-week high or low: at or past the year's extreme now, and short of it an hour ago."""
    ts = now.timestamp()
    price, before = price_at(q, ts, tolerance=FRESH), price_at(q, ts - 3600, tolerance=1800)
    if not price or not before:
        return None
    at = datetime.fromtimestamp(ts, tz=now.tzinfo)
    if q.high_52w and price >= q.high_52w * 0.9995 and before < q.high_52w * 0.995:
        return Move("high", item, price, at, level=float(q.high_52w))
    if q.low_52w and price <= q.low_52w * 1.0005 and before > q.low_52w * 1.005:
        return Move("low", item, price, at, level=float(q.low_52w))
    return None


def milestone(q: sources.Quote, item: dict[str, Any], now: datetime) -> Move | None:
    """A round level crossed in the last half hour and held by 0.5% (a price hovering on $50 isn't crossing it again and again)."""
    step = item.get("step")
    ts = now.timestamp()
    price, before = price_at(q, ts, tolerance=FRESH), price_at(q, ts - 1800, tolerance=900)
    if not step or not price or not before:
        return None
    below, above = int(before // step), int(price // step)
    if below == above:
        return None
    up = above > below
    level = float(max(above, below) * step)
    if abs(price - level) / level < 0.005:
        return None
    return Move("milestone", item, price, datetime.fromtimestamp(ts, tz=now.tzinfo), change=1.0 if up else -1.0, level=level)


def detect(quotes: dict[str, sources.Quote], watchlist: list[dict[str, Any]], now: datetime) -> list[Move]:
    """Every move worth considering right now, strongest first. When several crypto assets or two US indices move together, the market move
    replaces its members."""
    items = list(watchlist) + [x for x in EXTRA_MOVERS if x["symbol"] not in {w["symbol"] for w in watchlist}]
    found: list[Move] = []
    for item in items:
        q = quotes.get(item["symbol"])
        if q is None or not q.bars:
            continue
        m = measure(q, item, now) or extreme(q, item, now) or milestone(q, item, now)
        if m is not None:
            m.spark = spark(q, now)
            found.append(m)
    for groups, name, symbol in ((("crypto_major", "crypto_alt"), "Crypto market", "CRYPTO"), (("index",), "US stocks", "US-STOCKS")):
        members = [m for m in found if m.kind == "move" and m.item.get("group") in groups]
        for direction in (1, -1):
            same = [m for m in members if (m.change > 0) == (direction > 0)]
            if len(same) >= (3 if symbol == "CRYPTO" else 2) and any(m.item.get("group") in ("crypto_major", "index") for m in same):
                lead = max(same, key=lambda m: (m.item.get("group") in ("crypto_major", "index"), m.ratio))
                together = []  # every member over the lead's window, so the figures side by side mean the same thing
                for m in sorted(same, key=lambda m: -m.ratio):
                    then = price_at(quotes[m.symbol], lead.as_of.timestamp() - WINDOWS[lead.window], tolerance=max(600, int(WINDOWS[lead.window] * 0.05)))
                    change = (m.price - then) / then * 100 if then else 0.0
                    line = LINES.get(m.item.get("group", "stock"), LINES["stock"])[lead.window]
                    if then and (change > 0) == (direction > 0) and abs(change) >= line * 0.5:  # a real part of the move, not "+0.0%"
                        together.append((m.item["name"], change))
                if len(together) < 2:
                    continue
                wide = Move("wide", {"symbol": symbol, "name": name, "group": lead.item.get("group"), "category": lead.item.get("category")}, lead.price, lead.as_of,
                            lead.window, lead.change, max(m.ratio for m in same), members=together[:4], spark=lead.spark)
                found = [m for m in found if m not in same] + [wide]
    return sorted(found, key=lambda m: -m.ratio)


def watch_items(watchlist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(watchlist) + [x for x in EXTRA_MOVERS if x["symbol"] not in {w["symbol"] for w in watchlist}]
