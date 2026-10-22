"""eBay (official APIs only).

- Browse API (app keys, OAuth client credentials, scope api_scope): ACTIVE listings. These are asking prices; they are
  stored as listings and never used as sales.
- Marketplace Insights API (scope buy.marketplace.insights): items SOLD in the last 90 days. Limited Release: only works if
  eBay approved this application. Price Chat asks for that scope; if eBay refuses, sold data is reported as UNAVAILABLE.
- Sold-listing web pages are not scraped (they need a login since Aug 2026, and scraping breaks eBay's terms).
"""

import base64
import time
from datetime import datetime, timezone
from typing import Any, ClassVar

import httpx

from app.providers.pokemon.base import ListingRecord, PokemonProvider, ProviderInfo, Unavailable

BROWSE_SCOPE = "https://api.ebay.com/oauth/api_scope"
INSIGHTS_SCOPE = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"
CATEGORY = "183454"  # Pokémon TCG > Pokémon Individual Cards
EXCLUDE = ("proxy", "custom", "celebrations", "reprint", "lot", "orica", "metal", "jumbo")
SOLD_UNAVAILABLE = "SOLD DATA UNAVAILABLE FROM CURRENT EBAY API ACCESS (Marketplace Insights is not granted to these keys)"


class EbayProvider(PokemonProvider):
    info = ProviderInfo(
        key="ebay", name="eBay",
        serves=("get_listing_data", "get_sales"),
        access="eBay developer account and app keys (Browse API: active listings). Sold data needs Marketplace Insights "
               "approval from eBay (limited release, not open to new applicants).",
        licence="eBay API License Agreement. Active listings are asking prices, never treated as sales.",
        credentials=("ebay_client_id", "ebay_client_secret"),
        rate_limit="Browse API: 5,000 calls/day (default application limit)")

    # per client id: {scope: (token, expires)} and whether Marketplace Insights was granted (None = not checked yet)
    _tokens: ClassVar[dict[tuple[str, str], tuple[str, float]]] = {}
    sold_access: ClassVar[dict[str, bool]] = {}
    requests: int = 0

    def _creds(self) -> tuple[str, str]:
        from app.security.credentials import resolve

        connected, why = self.status()
        if not connected:
            raise Unavailable(why, "not_connected")
        return resolve("ebay_client_id", self.settings) or "", resolve("ebay_client_secret", self.settings) or ""

    def notes(self) -> tuple[str, ...]:
        if not self.status()[0]:
            return ("Sold data: needs eBay app keys AND Marketplace Insights approval from eBay.",)
        cid = self._creds()[0]
        state = self.sold_access.get(cid)
        if state is None:
            return ("Sold data access not checked yet: it is checked on the next sync.",)
        return ("Sold data: Marketplace Insights access granted.",) if state else (SOLD_UNAVAILABLE,)

    async def _token(self, client: httpx.AsyncClient, scope: str) -> str:
        cid, secret = self._creds()
        cached = self._tokens.get((cid, scope))
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        self.requests += 1
        r = await client.post(f"{self.settings.ebay_api_base.rstrip('/')}/identity/v1/oauth2/token",
                              headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode(),
                                       "Content-Type": "application/x-www-form-urlencoded"},
                              data={"grant_type": "client_credentials", "scope": scope})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or not body.get("access_token"):
            err = str(body.get("error") or r.status_code)
            if scope == INSIGHTS_SCOPE:
                self.sold_access[cid] = False
                raise Unavailable(SOLD_UNAVAILABLE, "no_access")
            hint = (" The App ID and Cert ID don't form a valid Production keyset: check the Cert ID (it starts with PRD-) and that the "
                    "keyset is enabled in the eBay developer portal (Production keysets stay disabled until the marketplace "
                    "account deletion notification is set up or an exemption is granted).") if err == "invalid_client" else ""
            raise Unavailable(f"eBay refused the app keys ({err}).{hint}", "auth")
        if scope == INSIGHTS_SCOPE:
            self.sold_access[cid] = True
        self._tokens[(cid, scope)] = (body["access_token"], time.time() + int(body.get("expires_in") or 7200))
        return body["access_token"]

    async def _get(self, path: str, params: dict[str, Any], scope: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
                token = await self._token(client, scope)
                self.requests += 1
                r = await client.get(f"{self.settings.ebay_api_base.rstrip('/')}{path}", params=params,
                                     headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": self.settings.ebay_marketplace})
        except httpx.HTTPError as exc:
            raise Unavailable("eBay didn't answer.", "unreachable") from exc
        if r.status_code == 429:
            raise Unavailable("eBay's daily call limit is reached.", "rate_limited")
        if r.status_code in (401, 403):
            if scope == INSIGHTS_SCOPE:
                self.sold_access[self._creds()[0]] = False
                raise Unavailable(SOLD_UNAVAILABLE, "no_access")
            raise Unavailable("eBay refused the request (keys / permissions).", "auth")
        if r.status_code == 404:
            raise Unavailable("eBay: no such item (ended or removed).", "not_found")
        if r.status_code != 200:
            raise Unavailable(f"eBay answered HTTP {r.status_code}.", "error")
        return r.json()

    @staticmethod
    def _money(m: dict[str, Any] | None) -> tuple[int, str] | None:
        if not m or m.get("value") in (None, ""):
            return None
        return round(float(m["value"]) * 100), str(m.get("currency") or "USD")

    @staticmethod
    def _when(v: str | None) -> datetime | None:
        if not v:
            return None
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    async def get_listing_data(self, query: str) -> list[ListingRecord]:
        """ACTIVE listings for a query (asking prices). Never sales."""
        body = await self._get("/buy/browse/v1/item_summary/search", {"q": query, "category_ids": CATEGORY, "limit": 50}, BROWSE_SCOPE)
        out = []
        for it in body.get("itemSummaries") or []:
            money = self._money(it.get("price") or it.get("currentBidPrice"))
            if not money or not it.get("itemId"):
                continue
            out.append(ListingRecord(provider="ebay", listing_id=str(it["itemId"]), title=str(it.get("title") or ""), price_cents=money[0],
                                     currency=money[1], url=it.get("itemWebUrl"), condition=it.get("condition"),
                                     seller=(it.get("seller") or {}).get("username"),
                                     listing_type=",".join(o.lower() for o in it.get("buyingOptions") or []) or None,
                                     image_url=(it.get("image") or {}).get("imageUrl")))
        return out

    async def get_sales(self, provider_id: str, since: datetime | None = None) -> list[ListingRecord]:  # type: ignore[override]
        """SOLD items (last 90 days) via Marketplace Insights. `provider_id` is the search query. Raises Unavailable
        (no_access) when eBay hasn't granted this application the Marketplace Insights scope."""
        body = await self._get("/buy/marketplace_insights/v1_beta/item_sales/search",
                               {"q": provider_id, "category_ids": CATEGORY, "limit": 50}, INSIGHTS_SCOPE)
        out = []
        for it in body.get("itemSales") or []:
            money = self._money(it.get("lastSoldPrice"))
            sold = self._when(it.get("lastSoldDate"))
            if not money or not sold or not it.get("itemId"):
                continue
            if since and sold < since:
                continue
            out.append(ListingRecord(provider="ebay", listing_id=str(it["itemId"]), title=str(it.get("title") or ""), price_cents=money[0],
                                     currency=money[1], url=it.get("itemWebUrl"), condition=it.get("condition"),
                                     listing_type=",".join(o.lower() for o in it.get("buyingOptions") or []) or None,
                                     image_url=(it.get("image") or {}).get("imageUrl"), sold_at=sold))
        return out


def query_for(card: Any, grade_label: str | None) -> str:
    """Exact eBay query from the catalog identity: year, set, name, number, edition, language, grader + grade, exclusions."""
    parts = [str(card.year), "Pokemon", card.set_name if "(" not in card.set_name else card.set_name.split("(")[0].strip(),
             card.name.replace("★", "").strip(), card.card_number or ""]
    if card.edition in ("1st Edition", "Shadowless"):
        parts.append(card.edition)
    if card.language != "English" and card.language.lower() not in " ".join(parts).lower():
        parts.append(card.language)
    if card.variant and card.variant.lower() not in " ".join(parts).lower():
        parts.append(card.variant)
    if grade_label and not grade_label.startswith("RAW"):
        parts.append(grade_label)
    exclude = list(EXCLUDE) + (["1st", "shadowless"] if card.edition == "Unlimited" else [])
    q = " ".join(p for p in parts if p)
    return q + " " + " ".join(f"-{w}" for w in exclude)
