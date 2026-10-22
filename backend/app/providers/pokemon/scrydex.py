"""Scrydex (https://scrydex.com/docs): paid Pokémon card data API. Headers X-Api-Key + X-Team-ID, base https://api.scrydex.com.

What Price Chat takes from it, and how it is labelled:
  - graded MARKET VALUATIONS per card variant + exact grade (low / mid / high / market, plus Scrydex's own 1-180 day
    trends): provider estimates, shown as "Scrydex market price", NEVER stored as sales;
  - PSA population per variant and grade (pop_reports): card-wide, grade-specific counts;
  - the provider's daily price history (price_history), when the plan includes it.
Every request costs 1 credit: responses are cached on disk, each sync has a hard request cap, and /account/v1/usage is
checked before a sync so a run never silently goes into overage.
"""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.providers.pokemon.base import PokemonProvider, ProviderInfo, Unavailable


class BudgetExceeded(Unavailable):
    def __init__(self, cap: int) -> None:
        super().__init__(f"Stopped at the request cap for this sync ({cap} requests = {cap} credits).", "budget")


class ScrydexProvider(PokemonProvider):
    info = ProviderInfo(
        key="scrydex", name="Scrydex",
        serves=("get_card", "get_current_prices", "get_population"),
        access="Paid plan at scrydex.com; API key + Team ID from the Account Hub.",
        licence="Provider market estimates (not sales), shown with Scrydex attribution. Check your plan's terms before public display.",
        credentials=("scrydex_api_key", "scrydex_team_id"),
        rate_limit="1 credit per request; 100 requests/second")

    retries = 2
    backoff = 1.0

    def __init__(self, settings: Any, transport: Any = None, max_requests: int | None = None) -> None:
        super().__init__(settings, transport)
        self.requests = 0
        self.cache_hits = 0
        self.max_requests = max_requests if max_requests is not None else settings.scrydex_max_requests_per_sync

    def notes(self) -> tuple[str, ...]:
        return ("Market estimates per exact variant and grade, shown as Scrydex market prices: never as verified sales.",
                "PSA population per variant (English cards). Market cap only where that grade's population is known.")

    def _cache_file(self, key: str) -> Path:
        return Path(self.settings.scrydex_cache_dir) / f"{hashlib.sha1(key.encode()).hexdigest()}.json"

    async def get(self, path: str, params: dict[str, Any] | None = None, cache: bool = True) -> Any:
        connected, why = self.status()
        if not connected:
            raise Unavailable(why, "not_connected")
        from app.security.credentials import resolve

        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = f"{self.settings.scrydex_api_base}{path}?{json.dumps(params, sort_keys=True)}"
        f = self._cache_file(key)
        if cache:
            try:
                if f.exists() and time.time() - f.stat().st_mtime < self.settings.scrydex_cache_hours * 3600:
                    self.cache_hits += 1
                    return json.loads(f.read_text(encoding="utf-8"))["data"]
            except (OSError, ValueError, KeyError):
                pass
        if self.requests >= self.max_requests:
            raise BudgetExceeded(self.max_requests)
        headers = {"X-Api-Key": resolve("scrydex_api_key", self.settings) or "", "X-Team-ID": resolve("scrydex_team_id", self.settings) or "",
                   "Accept": "application/json"}
        r: httpx.Response | None = None
        for attempt in range(self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
                    self.requests += 1
                    r = await client.get(f"{self.settings.scrydex_api_base.rstrip('/')}{path}", params=params, headers=headers)
            except httpx.HTTPError:
                r = None
            if (r is not None and r.status_code < 500 and r.status_code != 429) or attempt == self.retries or self.requests >= self.max_requests:
                break
            await asyncio.sleep(self.backoff * 2**attempt)
        if r is None:
            raise Unavailable("Scrydex didn't answer. Try again later.", "unreachable")
        detail = ""
        try:
            body = r.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):  # Scrydex: {"error": {"message": "Invalid credentials", "code": "INVALID_CREDENTIALS"}}
                detail = " ".join(str(x) for x in (err.get("code"), err.get("message")) if x)[:200]
            else:
                detail = str(body.get("message") or err or body.get("detail") or "")[:200] if isinstance(body, dict) else ""
        except ValueError:
            body = None
        if r.status_code == 401:
            raise Unavailable(f"Scrydex refused the credentials (HTTP 401{': ' + detail if detail else ''}). Check SCRYDEX_API_KEY and SCRYDEX_TEAM_ID.", "auth")
        if r.status_code == 403:
            raise Unavailable(f"Scrydex: this plan can't access that (HTTP 403{': ' + detail if detail else ''}).", "no_access")
        if r.status_code == 404:
            raise Unavailable(f"Scrydex has no {path}.", "not_found")
        if r.status_code == 429:
            raise Unavailable("Scrydex rate limit reached (HTTP 429). Try again shortly.", "rate_limited")
        if r.status_code != 200 or body is None:
            raise Unavailable(f"Scrydex answered HTTP {r.status_code}{': ' + detail if detail else ''}.", "unavailable")
        if cache:
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps({"url": key, "data": body}), encoding="utf-8")
                tmp.replace(f)
            except OSError:
                pass
        return body

    async def usage(self) -> dict[str, Any]:
        """Account usage (credits). Never cached: it is what keeps a sync out of overage."""
        body = await self.get("/account/v1/usage", cache=False)
        return body.get("data", body) if isinstance(body, dict) else {}

    async def card(self, card_id: str) -> dict[str, Any]:
        body = await self.get(f"/pokemon/v1/cards/{card_id}", {"include": "prices,pop_reports"})
        data = body.get("data", body) if isinstance(body, dict) else None
        if not isinstance(data, dict) or not data.get("id"):
            raise Unavailable(f"Scrydex returned no card for {card_id}.", "not_found")
        return data

    async def price_history(self, card_id: str, variant: str, company: str, grade: str, days: int = 365, max_pages: int = 10) -> list[dict[str, Any]]:
        """Daily points, all pages (1 credit per page, at most `max_pages`). Dates come as "YYYY/MM/DD"."""
        out: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            body = await self.get(f"/pokemon/v1/cards/{card_id}/price_history",
                                  {"days": days, "variant": variant, "company": company, "grade": grade, "page_size": 100, "page": page})
            rows = (body.get("data") if isinstance(body, dict) else None) or []
            out += rows
            total = body.get("total_count") if isinstance(body, dict) else None
            if not rows or not isinstance(total, int) or len(out) >= total:
                break
        return out


# --- exact variant matching ------------------------------------------------------------------------------------------


_PLAIN_WORDS = ("noraritysymbol", "firstedition", "unlimited", "shadowless", "holofoil", "normal", "holo")


def variant_tokens(name: str) -> dict[str, bool]:
    n = name.lower()
    rest = n
    for w in _PLAIN_WORDS:
        rest = rest.replace(w, "")
    return {"first": "firstedition" in n, "unlimited": "unlimited" in n, "shadowless": "shadowless" in n, "norarity": "noraritysymbol" in n,
            "holo": "holo" in n and "reverse" not in n, "reverse": "reverse" in n,
            # anything else in the name is a special print (fourthPrint, jumbo, metal, stamped, error...): never a regular edition
            "special": bool(rest)}


def match_variant(variants: list[dict[str, Any]], edition: str, set_code: str | None, holo_type: str | None,
                  variant: str = "") -> tuple[dict[str, Any] | None, str]:
    """The ONE Scrydex variant that is this catalog card (edition + holo), or (None, why). Never a best guess.
    Base Set 1st Edition cards are all shadowless (Scrydex: firstEditionShadowless...); "Shadowless" in the catalog is the
    unlimited shadowless print; "Unlimited" excludes shadowless prints. A "No Rarity" catalog card (Japanese Base Set first
    print) is only Scrydex's noRaritySymbol... variant, and any other card never is."""
    want_norarity = variant.strip().lower() == "no rarity"
    want_holo = (holo_type or "").lower().startswith("holo") or (holo_type or "").lower() in ("gold star",)
    cands = []
    for v in variants:
        t = variant_tokens(str(v.get("name", "")))
        if t["reverse"] or t["special"] or t["norarity"] != want_norarity:
            continue
        if edition == "1st Edition" and not t["first"]:
            continue
        if edition == "Shadowless" and (t["first"] or not t["shadowless"]):
            continue
        if edition == "Unlimited" and (t["first"] or t["shadowless"]):
            continue
        if want_holo and not t["holo"]:
            continue
        cands.append(v)
    names = [str(v.get("name")) for v in cands]
    if not cands and edition in ("Unlimited", "—") and not want_norarity:
        # a card printed in ONE regular version only (e.g. POP Series gold stars: Scrydex calls it "normal"): that version is the card
        regular = [v for v in variants if not any(variant_tokens(str(v.get("name", "")))[k] for k in ("special", "reverse", "norarity"))]
        if len(regular) == 1 and not any(variant_tokens(str(v.get("name", "")))["first"] for v in variants):
            return regular[0], f"variant {regular[0].get('name')} (the card's only regular printing)"
    if len(cands) == 1:
        return cands[0], f"variant {names[0]}"
    if not cands:
        return None, f"no {edition} {'No Rarity ' if want_norarity else ''}{'holo ' if want_holo else ''}variant (Scrydex has: {', '.join(str(v.get('name')) for v in variants) or 'none'})"
    return None, f"ambiguous: {', '.join(names)}"
