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
    return [k[3:-6] for k in mem.keys("px:") if k.endswith(":close") and k[3:-6].upper() not in STABLE_OR_WRAPPED]


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
