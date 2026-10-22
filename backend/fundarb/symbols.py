"""Canonical symbols ("BTC-PERP") <-> each venue's native market names.

Thousand-unit contracts are canonicalized as "1000X-PERP": Hyperliquid writes them "kPEPE", Lighter "1000PEPE". Their
prices are per 1,000 tokens on both, so they compare directly."""

import re


def canonical(base: str) -> str:
    raw = base.strip()
    if re.fullmatch(r"k[A-Z0-9]{2,}", raw):  # Hyperliquid's kPEPE (a lowercase k, then the ticker)
        return f"1000{raw[1:]}-PERP"
    return f"{raw.upper()}-PERP"


def base_of(symbol: str) -> str:
    s = symbol.strip().upper()
    if not s.endswith("-PERP"):
        raise ValueError(f"not a canonical perp symbol: {symbol!r} (expected e.g. 'BTC-PERP')")
    return s[: -len("-PERP")]


def hyperliquid_coin(symbol: str) -> str:
    b = base_of(symbol)
    return "k" + b[4:] if b.startswith("1000") and len(b) > 4 else b
