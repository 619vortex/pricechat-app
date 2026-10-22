"""The Pokémon TCG API (pokemontcg.io): free card CATALOG and official card images, English sets from Base Set on.

Used here for catalog identity and images only. Its price fields come from TCGplayer / Cardmarket under their terms,
so Price Chat does not import or show them. No key needed (a free key raises the rate limit).
"""

from typing import Any

import httpx

from app.providers.pokemon.base import CardRecord, PokemonProvider, ProviderInfo, Unavailable


class PokemonTCGProvider(PokemonProvider):
    info = ProviderInfo(
        key="pokemontcg", name="Pokémon TCG API",
        serves=("search_cards", "get_card"),
        access="Free public API. Optional free key for a higher rate limit.",
        licence="Card data and images for catalog use. Its embedded marketplace prices are not used.",
        credentials=())

    def _headers(self) -> dict[str, str]:
        from app.security.credentials import resolve

        key = resolve("pokemontcg_api_key", self.settings)
        return {"X-Api-Key": key} if key else {}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
                r = await client.get(f"{self.settings.pokemontcg_api_base.rstrip('/')}{path}", params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise Unavailable("The Pokémon TCG API didn't answer.", "unreachable") from exc
        if r.status_code == 404:
            raise Unavailable("The Pokémon TCG API has no such card.", "not_found")
        if r.status_code == 429:
            raise Unavailable("The Pokémon TCG API rate limit is reached; try later (a free key raises it).", "rate_limited")
        if r.status_code != 200:
            raise Unavailable(f"The Pokémon TCG API answered HTTP {r.status_code}.", "error")
        return r.json()

    @staticmethod
    def _card(d: dict[str, Any]) -> CardRecord:
        s = d.get("set") or {}
        printed = s.get("printedTotal")
        release = str(s.get("releaseDate") or "")
        number = d.get("number")
        return CardRecord(provider="pokemontcg", provider_id=str(d.get("id")), name=str(d.get("name") or ""), set_name=s.get("name"),
                          set_code=s.get("id"), number=f"{number}/{printed}" if printed and number else number,
                          year=int(release[:4]) if release[:4].isdigit() else None, rarity=d.get("rarity"),
                          image_url=(d.get("images") or {}).get("large") or (d.get("images") or {}).get("small"),
                          language="English", extra={"release_date": release or None})

    async def search_cards(self, query: str) -> list[CardRecord]:
        """`query` uses the API's own syntax, e.g. 'set.id:base1 number:4' or 'name:charizard'."""
        body = await self._get("/cards", {"q": query, "pageSize": 50})
        return [self._card(d) for d in body.get("data") or []]

    async def get_card(self, provider_id: str) -> CardRecord:
        return self._card((await self._get(f"/cards/{provider_id}")).get("data") or {})
