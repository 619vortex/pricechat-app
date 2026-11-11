"""The money angle (Viral Engine 2.0, owner 2026-11-11): which assets a story exposes, and how they actually moved since it broke.

Every figure here is measured on our own 5-minute prices (the same quotes app/newsdesk/moves reads), from the last bar at or before the
story's first report to the latest bar. Nothing is predicted: an asset the story names that didn't move past its noise line isn't
mentioned, and a story with no fresh prices has no money angle. Winners and losers are simply the measured gainers and fallers among the
exposed assets, never a forecast of who will gain.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from app.newsdesk import moves, sources

# (words in the story, the Yahoo symbols they expose). The broad ones (rates, war) expose the assets markets price those through.
EXPOSURE: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"\b(oil|crude|opec\+?|brent|wti|tankers?|hormuz|refiner\w*|aramco|pipeline)\b", re.I), ("BZ=F", "CL=F")),
    (re.compile(r"\b(gold|bullion|safe[- ]haven)\b", re.I), ("GC=F",)),
    (re.compile(r"\bsilver\b", re.I), ("SI=F",)),
    (re.compile(r"\b(bitcoin|btc|crypto\w*|stablecoins?|exchange hack)\b", re.I), ("BTC-USD",)),
    (re.compile(r"\b(ether|ethereum|eth)\b", re.I), ("ETH-USD",)),
    (re.compile(r"\b(solana|sol)\b", re.I), ("SOL-USD",)),
    (re.compile(r"\b(xrp|ripple)\b", re.I), ("XRP-USD",)),
    (re.compile(r"\b(nvidia|nvda|chips?|semiconductors?|chipmakers?)\b", re.I), ("NVDA",)),
    (re.compile(r"\b(tesla|tsla|musk)\b", re.I), ("TSLA",)),
    (re.compile(r"\b(apple|aapl|iphone)\b", re.I), ("AAPL",)),
    (re.compile(r"\bcoinbase\b", re.I), ("COIN",)),
    (re.compile(r"\b(microstrategy|saylor|mstr)\b", re.I), ("MSTR",)),
    (re.compile(r"\b(meta|facebook|instagram)\b", re.I), ("META",)),
    (re.compile(r"\bamazon\b", re.I), ("AMZN",)),
    (re.compile(r"\bmicrosoft\b", re.I), ("MSFT",)),
    (re.compile(r"\b(alphabet|google)\b", re.I), ("GOOGL",)),
    (re.compile(r"\b(fed|federal reserve|rate (?:cut|hike)s?|inflation|cpi|jobs report|payrolls|treasur(?:y|ies)|yields?|tariffs?)\b", re.I),
     ("^GSPC", "^IXIC", "GC=F", "BTC-USD")),
    (re.compile(r"\b(stocks?|wall street|s&p|nasdaq|equities|dow)\b", re.I), ("^GSPC", "^IXIC")),
    (re.compile(r"\b(war|missiles?|air ?strikes?|invasion|sanctions?|ceasefire|blockade|drone attack)\b", re.I), ("BZ=F", "GC=F")),
]
NAMES = {w["symbol"]: w["name"] for w in [*sources.DEFAULT_WATCHLIST, *moves.EXTRA_MOVERS]} | {"BZ=F": "Brent crude", "CL=F": "WTI crude", "^GSPC": "S&P 500",
                                                                                              "^IXIC": "Nasdaq", "GC=F": "Gold", "SI=F": "Silver"}
TAGS = {"BTC-USD": "$BTC", "ETH-USD": "$ETH", "SOL-USD": "$SOL", "XRP-USD": "$XRP", "NVDA": "$NVDA", "TSLA": "$TSLA", "AAPL": "$AAPL", "COIN": "$COIN",
        "MSTR": "$MSTR", "META": "$META", "AMZN": "$AMZN", "MSFT": "$MSFT", "GOOGL": "$GOOGL"}
GROUP = {w["symbol"]: w.get("group", "stock") for w in [*sources.DEFAULT_WATCHLIST, *moves.EXTRA_MOVERS]} | {"BZ=F": "commodity", "CL=F": "commodity",
                                                                                                           "GC=F": "commodity", "SI=F": "commodity"}
NOISE_SHARE = 0.75  # a reaction must be at least 3/4 of the asset's one-hour alert line (BTC 2.25%, the S&P 0.9%, oil and gold 1.9%): smaller moves
# happen on any afternoon, and pairing them with a story would suggest a cause nobody showed
MAX_ASSETS = 4


@dataclass
class Reaction:
    symbol: str
    name: str
    pct: float
    since: str  # ISO time of the bar we measured from
    tag: str = ""

    @property
    def text(self) -> str:
        return f"{self.name} {self.pct:+.1f}%"

    def out(self) -> dict[str, Any]:
        return asdict(self)


def exposed(text: str) -> list[str]:
    out: list[str] = []
    for rx, syms in EXPOSURE:
        if rx.search(text or ""):
            out += [s for s in syms if s not in out]
    return out


def noise(symbol: str) -> float:
    return moves.LINES.get(GROUP.get(symbol, "stock"), moves.LINES["stock"])["1h"] * NOISE_SHARE


def reaction(text: str, quotes: dict[str, sources.Quote], since: datetime | None, now: datetime) -> list[Reaction]:
    """The exposed assets that moved past their noise line between the story's first report and now, biggest first. `text` should be the
    headline: an asset only a summary mentions in passing ("stablecoins") isn't what the story is about."""
    if since is None:
        return []
    out = []
    for sym in exposed(text):
        q = quotes.get(sym)
        if q is None or not q.bars or (now - q.as_of).total_seconds() > moves.FRESH:
            continue
        then = moves.price_at(q, since.timestamp(), tolerance=1800)
        last = [c for t, c, _ in q.bars if t <= now.timestamp() and c > 0]
        if not then or not last:
            continue
        pct = (last[-1] - then) / then * 100
        if abs(pct) >= noise(sym):
            out.append(Reaction(sym, NAMES.get(sym, sym), round(pct, 1), since.isoformat(), TAGS.get(sym, "")))
    out.sort(key=lambda r: -abs(r.pct) / max(noise(r.symbol), 0.01))
    return out[:MAX_ASSETS]


def lines(rs: list[Reaction], lead: str = "Since the first report") -> dict[str, str]:
    """The sentences the money and winners-and-losers angles may use. Every figure is one we measured."""
    if not rs:
        return {}
    up = [r for r in rs if r.pct > 0]
    down = [r for r in rs if r.pct < 0]
    out = {"reaction": f"{lead}: " + ", ".join(r.text for r in rs[:3]) + "."}
    if up and down:
        out["winners_losers"] = f"Winners: {', '.join(r.text for r in up[:2])}. Losers: {', '.join(r.text for r in down[:2])}."
    return out


__all__ = ["Reaction", "exposed", "lines", "reaction"]
