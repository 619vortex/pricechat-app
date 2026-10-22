"""The Pokémon Market on the PUBLIC website (pricechat.live/pokemon): which read-only endpoints visitors may call, and
what is removed from those responses before they leave the server.

Everything else under /api/pokemon stays admin-only (syncs, API keys, data sources, review queue, eBay live lookups,
watchlist, PSA certs, provenance, data quality).

Removed for visitors (the owner, signed in, still sees everything):
  - PriceCharting values: its API licence covers internal use only. A row whose price comes from PriceCharting shows no
    price / market cap publicly; reference prices and PriceCharting comparisons are dropped.
  - active eBay listings, PSA cert numbers, sale provenance details;
  - Market Pulse spending: the X budget, cost and configuration (the feed itself stays).
"""

import re
from typing import Any

_PUBLIC_GET = [re.compile(p) for p in (
    r"^/api/pokemon/(market|top100|movers|trending|sets|grades|years|fx|compare|pulse|sales/recent|cards)$",
    r"^/api/pokemon/cards/\d+(/(chart|history|meta|quality))?$",
)]
_DROP = {"reference", "reference_prices", "pricecharting", "active_listings", "psa_certs", "provenance"}
_PRICE_KEYS = ("market_price", "market_cap", "price", "shown_price")
_PULSE_STATUS_KEEP = {"enabled", "last_refresh_at", "connected", "posts_stored"}


def is_public_get(method: str, path: str) -> bool:
    return method.upper() in ("GET", "HEAD") and any(p.match(path.rstrip("/")) for p in _PUBLIC_GET)


def sanitize(obj: Any, path: str = "") -> Any:
    """A copy of a public market response without what visitors may not see (see the module notes)."""
    if isinstance(obj, list):
        return [sanitize(x, path) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if k in _DROP:
            continue
        if k == "status" and path.endswith("/pulse") and isinstance(v, dict):
            out[k] = {kk: vv for kk, vv in v.items() if kk in _PULSE_STATUS_KEEP}
            continue
        out[k] = sanitize(v, path)
    if out.get("price_source") == "pricecharting":
        for k in _PRICE_KEYS:
            if k in out:
                out[k] = None
        out["price_source"] = None
        out["sparkline"] = None
    return out
