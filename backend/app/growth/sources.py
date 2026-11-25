"""The Growth Engine's data-source registry: who each source is, where its terms are, whether its data may be used commercially in
published posts (`commercial_ok`), and the attribution line a post must carry.

Licence status (research reported 2026-11-25): cleared = Hyperliquid's public info API, alternative.me's Fear & Greed (attribution
"Fear & Greed Index: alternative.me"), EIA (public domain) and FRED's non-copyrighted series. NOT cleared: Coinbase, OKX, Binance, Bybit,
Kraken, DefiLlama, Coin Metrics, CoinGecko's free plan, Yahoo. A source that isn't cleared:
- never runs in production (`may_collect`), except a `store_only` internal rollup (liquidations) that is never surfaced, and
- a Finding that relies on it is never queued (`publishable`).
Yahoo is never used by this engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Source:
    name: str  # registry key, also mm_series.source
    label: str  # how a post names it
    terms_url: str
    commercial_ok: bool
    attribution: str
    notes: str = ""
    store_only: bool = False  # may be stored internally (never published) so its history isn't lost to pruning


SOURCES: dict[str, Source] = {s.name: s for s in (
    Source("hyperliquid", "Hyperliquid", "https://hyperliquid.gitbook.io/hyperliquid-docs/legal", True, "Data: Hyperliquid",
           "public info API (candleSnapshot, fundingHistory, metaAndAssetCtxs); no key. Daily history starts at each perp's listing "
           "(BTC/ETH: 2023), so every record or rarity claim names that window"),
    Source("alternative_me", "alternative.me", "https://alternative.me/crypto/fear-and-greed-index/", True, "Fear & Greed Index: alternative.me",
           "free with attribution, shown next to the number on graphics and in the sources line"),
    Source("derived_hl", "Price Chat (from Hyperliquid)", "https://hyperliquid.gitbook.io/hyperliquid-docs/legal", True, "Data: Hyperliquid",
           "Price Chat's own daily rollups of its Hyperliquid snapshots (ts_oi_snapshots) and breadth computed from Hyperliquid closes"),
    Source("eia", "U.S. EIA", "https://www.eia.gov/about/copyrights_reuse.php", True, "Data: U.S. EIA", "public domain (oil); no collector yet"),
    Source("fred", "FRED", "https://fred.stlouisfed.org/legal/", True, "Data: FRED, St. Louis Fed",
           "ONLY non-copyrighted series (never SP500 / Dow / NASDAQ index series); no collector yet"),
    # NOT cleared (licence research 2026-11-25): never collected in production and never surfaced in a draft
    Source("coinbase", "Coinbase Exchange", "https://www.coinbase.com/legal/market_data", False, "Data: Coinbase Exchange",
           "market-data terms forbid displaying data or derived charts/stats externally without written consent. Collector kept, disabled"),
    Source("liquidations", "OKX / Bybit / Binance liquidation feeds", "", False, "Liquidations: OKX, Bybit, Binance",
           "Price Chat's rollup of ts_liquidations (exchange WebSocket feeds). Rolled up internally only (store_only) so the history "
           "outlives the 35-day pruning; excluded from every detector", store_only=True),
    Source("okx", "OKX", "https://www.okx.com/help/api-agreement", False, "", "API agreement: personal, non-commercial only"),
    Source("bybit", "Bybit", "https://www.bybit.com/en/terms-service/", False, "", "not permitted"),
    Source("binance", "Binance", "https://www.binance.com/en/terms", False, "", "incl. data.binance.vision (CC BY-NC-SA); also blocked from Railway"),
    Source("kraken", "Kraken", "https://www.kraken.com/legal", False, "", "not permitted"),
    Source("defillama", "DefiLlama", "https://defillama.com/terms", False, "", "not permitted"),
    Source("coinmetrics", "Coin Metrics", "https://coinmetrics.io/terms", False, "", "community data: non-commercial"),
    Source("coingecko", "CoinGecko", "https://www.coingecko.com/en/api_terms", False, "Data: CoinGecko",
           "free Demo plan: not permitted; the paid Basic plan (~$35/mo) would be (owner decision)"),
    Source("yahoo", "Yahoo Finance", "https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html", False, "",
           "NEVER used by the Growth Engine (unlicensed)"),
)}


def get(name: str) -> Source | None:
    return SOURCES.get(name)


def commercial_ok(name: str) -> bool:
    s = SOURCES.get(name)
    return bool(s and s.commercial_ok)


def may_collect(name: str, settings: Any = None) -> bool:
    """A collector may run: always outside production (research, the local backfill); in production only for a cleared source."""
    if name == "yahoo":
        return False
    if commercial_ok(name) or (SOURCES.get(name) and SOURCES[name].store_only):
        return True
    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    return not bool(getattr(settings, "is_production", False))


def publishable(names: list[str] | tuple[str, ...] | set[str]) -> bool:
    return bool(names) and all(commercial_ok(n) for n in names)


def attribution(names: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        s = SOURCES.get(n)
        if s and s.attribution and s.attribution not in out:
            out.append(s.attribution)
    return out


def labels(names: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        s = SOURCES.get(n)
        lab = s.label if s else n
        if lab not in out:
            out.append(lab)
    return out


def registry() -> list[dict[str, Any]]:
    return [{"name": s.name, "label": s.label, "terms_url": s.terms_url, "commercial_ok": s.commercial_ok, "attribution": s.attribution,
             "notes": s.notes} for s in SOURCES.values()]
