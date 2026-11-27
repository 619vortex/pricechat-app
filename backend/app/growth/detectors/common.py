"""Shared helpers for the detectors: asset names, which stored series to use for an asset, and plain statistics."""

from __future__ import annotations

from datetime import date, timedelta

from app.growth import sources
from app.growth.memory import Memory, Series

NAMES = {"BTC": "Bitcoin", "ETH": "Ether", "SOL": "Solana", "XRP": "XRP", "DOGE": "Dogecoin", "ADA": "Cardano", "AVAX": "Avalanche",
         "LINK": "Chainlink", "LTC": "Litecoin", "BCH": "Bitcoin Cash", "DOT": "Polkadot", "UNI": "Uniswap", "AAVE": "Aave",
         "XLM": "Stellar", "ATOM": "Cosmos", "NEAR": "NEAR", "HBAR": "Hedera", "SUI": "Sui", "HYPE": "HYPE", "BNB": "BNB"}
MAJORS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
STABLE_OR_WRAPPED = {"USDC", "USDT", "DAI", "FDUSD", "USDE", "PYUSD", "TUSD", "WBTC", "WETH", "STETH"}


def name(coin: str) -> str:
    return NAMES.get(coin.upper(), coin)


def price(mem: Memory, coin: str) -> Series | None:
    """The longest stored daily close for a coin FROM A CLEARED SOURCE (app.growth.sources). Today that is Hyperliquid only (history from
    each perp's listing); a stored Coinbase series is never used while Coinbase isn't cleared."""
    cands = [mem.get(f"px:{coin}:close"), mem.get(f"px:{coin.upper()}:close"), mem.get(f"cb:{coin.upper()}-USD:close")]
    ok = [s for s in cands if s is not None and sources.commercial_ok(s.source)]
    return min(ok, key=lambda s: s.first) if ok else None


def data_label(s: Series) -> str:
    """How a post names the stored window's data ("Hyperliquid data"): every record / rarity claim says which history it is measured in."""
    lab = sources.labels([s.source])[0] if s.source else "our"
    if s.source == "derived_hl":
        lab = "Hyperliquid"
    return f"{lab} data"


def hl_coins(mem: Memory) -> list[str]:
    """Crypto perps only: the XYZ markets (gold, indices) are never part of a crypto universe (breadth, rankings)."""
    return [k[3:-6] for k in mem.keys("px:") if k.endswith(":close") and k[3:-6].upper() not in STABLE_OR_WRAPPED and not k[3:].startswith("XYZ-")]


# Assets a broad financial audience knows (owner, 2026-11-27: "avoid obscure low-volume tokens"). Rankings and comparisons name only these.
WELL_KNOWN = ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "TRX", "AVAX", "LINK", "DOT", "LTC", "BCH", "XLM", "SUI", "HYPE", "NEAR",
              "UNI", "AAVE", "ATOM", "HBAR", "XMR", "FIL", "ARB", "OP", "APT", "TAO", "ONDO", "kPEPE", "kSHIB", "TON", "ETC")
DISPLAY = {"kPEPE": "PEPE", "kSHIB": "SHIB"}


def well_known(mem: Memory, as_of: date, min_days: int = 0) -> list[str]:
    """WELL_KNOWN coins with a close on as_of (and min_days of history), most traded first."""
    stored = set(hl_coins(mem))
    out = []
    for c in WELL_KNOWN:
        s = mem.get(f"px:{c}:close")
        if c not in stored or not s or s.index_on_or_before(as_of) is None or s.dates[s.index_on_or_before(as_of)] != as_of:  # type: ignore[index]
            continue
        if min_days and (as_of - s.first).days < min_days:
            continue
        out.append((avg_volume(mem, c, as_of), c))
    return [c for _, c in sorted(out, reverse=True)]


def avg_volume(mem: Memory, coin: str, as_of: date, days: int = 30) -> float:
    s = mem.get(f"px:{coin}:vol")
    if not s:
        return 0.0
    lo = as_of - timedelta(days=days)
    vals = [v for d, v in zip(s.dates, s.values) if lo < d <= as_of]
    return sum(vals) / len(vals) if vals else 0.0


def most_traded(mem: Memory, as_of: date, n: int = 50, min_days: int = 0) -> list[str]:
    """The n perps with the highest 30-day average USD volume that have a close on as_of (and at least min_days of history)."""
    out = []
    for c in hl_coins(mem):
        s = mem.get(f"px:{c}:close")
        if not s or s.last < as_of or s.index_on_or_before(as_of) is None or s.dates[s.index_on_or_before(as_of)] != as_of:  # type: ignore[index]
            continue
        if min_days and (as_of - s.first).days < min_days:
            continue
        out.append((avg_volume(mem, c, as_of), c))
    return [c for _, c in sorted(out, reverse=True)[:n]]


def ret(s: Series, as_of: date, days: int) -> tuple[float, float, float] | None:
    """(return %, start value, end value) over exactly `days` calendar days ending on as_of; None when either day isn't stored."""
    i = s.index_on_or_before(as_of)
    j = s.index_on_or_before(as_of - timedelta(days=days))
    if i is None or j is None or s.dates[i] != as_of or s.dates[j] != as_of - timedelta(days=days) or s.values[j] <= 0:
        return None
    return (s.values[i] / s.values[j] - 1) * 100, s.values[j], s.values[i]


def percentile_rank(values: list[float], x: float) -> float:
    """Share (0..1) of values <= x."""
    if not values:
        return 0.0
    return sum(1 for v in values if v <= x) / len(values)


def whole_years(first: date, as_of: date) -> int:
    return int(((as_of - first).days) // 365.25)


def nice_bin(values: list[float], target: int = 32) -> float:
    """A round histogram bin width (1, 2, 2.5, 5 x 10^k) giving about `target` bins over the values' range."""
    import math

    span = (max(values) - min(values)) or 1.0
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)


def us_market_closed(d: date) -> bool:
    """True on a weekend or a New York Stock Exchange holiday. Hyperliquid's index and metal perpetuals trade around the clock, so on those
    days their price is not a price the index (or the metals' main session) printed: a cross-asset comparison ends on the last open day."""
    if d.weekday() >= 5:
        return True
    return d in _nyse_holidays(d.year)


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    first = date(year, month, 1)
    if nth > 0:
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))
    nxt = date(year + (month == 12), month % 12 + 1, 1)  # the last one: from the end of the month
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def _easter(year: int) -> date:  # anonymous Gregorian algorithm
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    return date(year, month, (h + l - 7 * m + 33 * month + 19) % 32)


def _nyse_holidays(year: int) -> set[date]:
    days = {_observed(date(year, 1, 1)), _nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _easter(year) - timedelta(days=2),
            _nth_weekday(year, 5, 0, -1), _observed(date(year, 7, 4)), _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 11, 3, 4),
            _observed(date(year, 12, 25))}
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))
    return days
