"""PriceCharting (official API, PRICECHARTING_API_KEY).

Paid subscription; the API is licensed for INTERNAL use (public display needs a commercial licence and written
permission). Its prices are REFERENCE values only: stored with PriceCharting's own grade labels, shown to the admin
with attribution, and never used as sales or as Price Chat's market price.

Trading-card price fields (prices in pennies): loose-price = Ungraded, cib-price = Grade 7, new-price = Grade 8,
graded-price = Grade 9, box-only-price = Grade 9.5, manual-only-price = PSA 10, bgs-10-price = BGS 10, condition-17-price = CGC 10,
condition-18-price = SGC 10. "Grade 9" etc. are not tied to a grading company, so they keep their own label; PSA 10, BGS 10 and CGC 10
map to exact grades (SGC is kept by label: Price Chat does not track SGC grades).
"""

import asyncio
import time
from typing import Any, ClassVar

import httpx

from app.pokemon.grades import Grade
from app.providers.pokemon.base import CardRecord, PokemonProvider, ProviderInfo, Unavailable

FIELDS: tuple[tuple[str, str, Grade | None], ...] = (
    ("loose-price", "Ungraded", None), ("cib-price", "Grade 7", None), ("new-price", "Grade 8", None),
    ("graded-price", "Grade 9", None), ("box-only-price", "Grade 9.5", None), ("manual-only-price", "PSA 10", Grade("PSA", "10")),
    # grader-specific 10s (PriceCharting trading-card fields). condition-9..16, 19, 20 are undocumented: never mapped.
    ("bgs-10-price", "BGS 10", Grade("BGS", "10")), ("condition-17-price", "CGC 10", Grade("CGC", "10")), ("condition-18-price", "SGC 10", None),
)


class PriceChartingProvider(PokemonProvider):
    info = ProviderInfo(
        key="pricecharting", name="PriceCharting",
        serves=("search_cards", "get_current_prices"),
        access="Paid subscription with API access (API key: PRICECHARTING_API_KEY).",
        licence="Internal use only by default. Public display needs a commercial licence and written permission.",
        credentials=("pricecharting_api_key",),
        rate_limit="Subscription terms (see PriceCharting API documentation)")

    min_interval: ClassVar[float] = 1.1  # seconds between calls: PriceCharting rate-limits rapid polling
    _last_call: ClassVar[float] = 0.0

    def notes(self) -> tuple[str, ...]:
        return ("Reference prices only: never sales, never Price Chat's market price. Internal display only without a commercial licence.",)

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        from app.security.credentials import resolve

        connected, why = self.status()
        if not connected:
            raise Unavailable(why, "not_connected")
        r = None
        for attempt in range(3):
            wait = PriceChartingProvider._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            PriceChartingProvider._last_call = time.monotonic()
            self.requests = getattr(self, "requests", 0) + 1
            try:
                async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
                    r = await client.get(f"{self.settings.pricecharting_api_base.rstrip('/')}{path}",
                                         params={**params, "t": resolve("pricecharting_api_key", self.settings)})
            except httpx.HTTPError as exc:
                raise Unavailable("PriceCharting didn't answer.", "unreachable") from exc
            if r.status_code != 429 or attempt == 2:
                break
            await asyncio.sleep(self.min_interval * 5 * (attempt + 1))  # rate limited: back off, then retry
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code in (401, 403) or body.get("status") == "error" and "token" in str(body.get("error-message", "")).lower():
            raise Unavailable("PriceCharting refused the API key.", "auth")
        if r.status_code == 429:
            raise Unavailable("PriceCharting rate limit reached.", "rate_limited")
        if r.status_code != 200:
            raise Unavailable(f"PriceCharting answered HTTP {r.status_code}.", "error")
        return body

    @staticmethod
    def _record(p: dict[str, Any]) -> CardRecord:
        return CardRecord(provider="pricecharting", provider_id=str(p.get("id")), name=str(p.get("product-name") or ""),
                          set_name=p.get("console-name"), extra={"release_date": p.get("release-date")})

    async def search_cards(self, query: str) -> list[CardRecord]:
        return [self._record(p) for p in (await self._get("/api/products", {"q": query})).get("products") or []]

    async def search_raw(self, query: str) -> list[dict[str, Any]]:
        return (await self._get("/api/products", {"q": query})).get("products") or []

    async def product(self, product_id: str) -> dict[str, Any]:
        return await self._get("/api/product", {"id": product_id})

    async def reference_prices(self, product_id: str, p: dict[str, Any] | None = None) -> list[tuple[str, Grade | None, int]]:
        """(PriceCharting label, exact grade or None, cents) for one product."""
        p = p if p is not None else await self._get("/api/product", {"id": product_id})
        out = []
        for field, label, grade in FIELDS:
            v = p.get(field)
            if isinstance(v, (int, float)) and v > 0:
                out.append((label, grade, int(v)))
        return out
