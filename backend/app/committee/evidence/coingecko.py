"""CoinGecko snapshot for the fundamental section (public API; an optional demo key from COINGECKO_API_KEY is sent as
x-cg-demo-api-key and never logged).

Matching is deliberately strict, because a wrong coin is worse than no coin: /search?query=SYM, keep only results whose
symbol equals the ticker exactly and that have a market-cap rank; the best-ranked one is used. If a second exact-symbol
result is also ranked inside the top 500, the ticker is ambiguous and no numbers are used (the pack says so). CoinGecko is
a current snapshot: in backtest mode it is not called."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.committee.evidence.common import FetchError, cache_get, cache_put, get_json, mark

API = "https://api.coingecko.com/api/v3"
AMBIGUOUS_RANK = 500
SEARCH_TTL = 3600.0
COIN_TTL = 600.0


def _headers() -> dict[str, str]:
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    return {"x-cg-demo-api-key": key} if key else {}


def pick(symbol: str, coins: list[dict[str, Any]]) -> dict[str, Any]:
    """{"id", "name", "matched_by", "ambiguous", "candidates"} from /search's coins (id None when there's no confident match)."""
    sym = symbol.upper()
    exact = [c for c in coins if str(c.get("symbol", "")).upper() == sym and isinstance(c.get("market_cap_rank"), int)]
    exact.sort(key=lambda c: c["market_cap_rank"])
    cands = [{"id": c.get("id"), "name": c.get("name"), "rank": c["market_cap_rank"]} for c in exact[:5]]
    if not exact:
        return {"id": None, "name": None, "matched_by": None, "ambiguous": False, "candidates": cands}
    if len(exact) >= 2 and exact[1]["market_cap_rank"] <= AMBIGUOUS_RANK:
        return {"id": None, "name": None, "matched_by": None, "ambiguous": True, "candidates": cands}
    return {"id": exact[0].get("id"), "name": exact[0].get("name"), "matched_by": "exact symbol, best market-cap rank",
            "ambiguous": False, "candidates": cands}


async def lookup(http: httpx.AsyncClient, symbol: str) -> dict[str, Any]:
    """The coingecko fact: match info plus, for a confident match, the market numbers. Raises FetchError when CoinGecko
    can't be read."""
    sym = symbol.upper()
    try:
        search = cache_get(f"cg:search:{sym}", SEARCH_TTL)
        if search is None:
            search = cache_put(f"cg:search:{sym}", await get_json(http, f"{API}/search", what="CoinGecko", params={"query": sym},
                                                                  headers=_headers()))
        m = pick(sym, (search or {}).get("coins") or [])
        if not m["id"]:
            mark("coingecko", True, "search answered")
            return m
        coin = cache_get(f"cg:coin:{m['id']}", COIN_TTL)
        if coin is None:
            coin = cache_put(f"cg:coin:{m['id']}", await get_json(
                http, f"{API}/coins/{m['id']}", what="CoinGecko", headers=_headers(),
                params={"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"}))
    except FetchError as e:
        mark("coingecko", False, str(e))
        raise
    mark("coingecko", True, "coin answered")
    md = coin.get("market_data") or {}

    def usd(k: str) -> float | None:
        v = md.get(k)
        v = v.get("usd") if isinstance(v, dict) else v
        return float(v) if isinstance(v, (int, float)) else None

    m.update({"market_cap": usd("market_cap"), "fdv": usd("fully_diluted_valuation"), "circulating": usd("circulating_supply"),
              "total": usd("total_supply"), "max_supply": usd("max_supply"), "rank": coin.get("market_cap_rank"), "ath": usd("ath"),
              "ath_change_pct": usd("ath_change_percentage"), "categories": list(coin.get("categories") or [])[:8],
              "genesis_date": coin.get("genesis_date"), "last_updated": coin.get("last_updated") or md.get("last_updated"),
              "name": coin.get("name") or m["name"]})
    return m
