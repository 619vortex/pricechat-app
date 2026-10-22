"""TCGdex (https://tcgdex.dev): free, open card CATALOG over REST, English and Japanese, no key.

Used for identity, metadata and official card images only. TCGdex card payloads also embed Cardmarket / TCGplayer
`pricing` (marketplace aggregates such as trend / avg / marketPrice). Those are NOT sold transactions: they are removed
from every response before it is cached or used, so they can never reach sales, prices or charts.

Endpoints used (GET, base https://api.tcgdex.net/v2/{lang}):
  /cards?set.id=eq:{set}&variants.holo=true    a set's holo cards (brief: id, localId, name, image)
  /cards?set.id=eq:{set}&dexId=eq:{n}          a set's cards of one Pokédex number (Japanese matching)
  /cards/{id}                                   one card: rarity, variants (1st Edition...), illustrator, dexId, set...
  /sets/{id}                                    one set: name, release date, card counts
Responses are cached on disk (data/cache/tcgdex) for days; missing fields stay None.
"""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.providers.pokemon.base import CardRecord, PokemonProvider, ProviderInfo, Unavailable

LANGS = ("en", "ja")


def strip_pricing(data: Any) -> Any:
    """Remove every `pricing` field (marketplace aggregates, not sales), at any depth."""
    if isinstance(data, dict):
        return {k: strip_pricing(v) for k, v in data.items() if k != "pricing"}
    if isinstance(data, list):
        return [strip_pricing(v) for v in data]
    return data


def image_url(base: str | None, quality: str = "high", ext: str = "webp") -> str | None:
    """TCGdex gives an image BASE url; the file is <base>/<quality>.<ext>."""
    return f"{base}/{quality}.{ext}" if base else None


class TCGdexProvider(PokemonProvider):
    info = ProviderInfo(
        key="tcgdex", name="TCGdex",
        serves=("search_cards", "get_card"),
        access="Free open REST API (api.tcgdex.net), no key. English and Japanese.",
        licence="Card catalog data and images. Its embedded Cardmarket/TCGplayer pricing is never imported (not sold prices).",
        credentials=())

    retries = 2
    backoff = 1.0

    def __init__(self, settings: Any, transport: Any = None) -> None:
        super().__init__(settings, transport)
        self.requests = 0
        self.cache_hits = 0

    def notes(self) -> tuple[str, ...]:
        return ("Catalog and images only. Japanese vintage sets have metadata but often no images.",
                "Pricing fields in TCGdex responses are marketplace aggregates, not sales: they are discarded.")

    def _cache_file(self, url: str) -> Path:
        return Path(self.settings.tcgdex_cache_dir) / f"{hashlib.sha1(url.encode()).hexdigest()}.json"

    async def get(self, lang: str, path: str) -> Any:
        """GET {base}/{lang}{path}, served from the disk cache when fresh. 404 -> Unavailable("not_found")."""
        if lang not in LANGS:
            raise Unavailable(f"Language {lang!r} isn't supported (en, ja).", "invalid")
        url = f"{self.settings.tcgdex_api_base.rstrip('/')}/{lang}{path}"
        f = self._cache_file(url)
        ttl = self.settings.tcgdex_cache_hours * 3600
        try:
            if f.exists() and time.time() - f.stat().st_mtime < ttl:
                self.cache_hits += 1
                return json.loads(f.read_text(encoding="utf-8"))["data"]
        except (OSError, ValueError, KeyError):
            pass  # a broken cache file is just refetched
        r: httpx.Response | None = None
        for attempt in range(self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
                    self.requests += 1
                    r = await client.get(url)
            except httpx.HTTPError:
                r = None
            if (r is not None and r.status_code < 500 and r.status_code != 429) or attempt == self.retries:
                break
            await asyncio.sleep(self.backoff * 2**attempt)
        if r is None:
            raise Unavailable("TCGdex didn't answer. Try again later.", "unreachable")
        if r.status_code == 404:
            raise Unavailable(f"TCGdex has no {path}.", "not_found")
        if r.status_code == 429:
            raise Unavailable("TCGdex is limiting requests; try again later.", "rate_limited")
        if r.status_code != 200:
            raise Unavailable(f"TCGdex answered HTTP {r.status_code}.", "unreachable")
        try:
            data = strip_pricing(r.json())
        except ValueError:
            raise Unavailable("TCGdex answered with something that isn't JSON.", "unavailable") from None
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({"url": url, "data": data}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
        except OSError:
            pass  # caching is best-effort
        return data

    async def set_holos(self, set_id: str, lang: str = "en") -> list[dict[str, Any]]:
        return await self.get(lang, f"/cards?set.id=eq:{set_id}&variants.holo=true")

    async def set_by_dex(self, set_id: str, dex: int, lang: str = "ja") -> list[dict[str, Any]]:
        return await self.get(lang, f"/cards?set.id=eq:{set_id}&dexId=eq:{dex}")

    async def card(self, card_id: str, lang: str = "en") -> dict[str, Any]:
        return await self.get(lang, f"/cards/{card_id}")

    async def set_info(self, set_id: str, lang: str = "en") -> dict[str, Any]:
        data = await self.get(lang, f"/sets/{set_id}")
        data.pop("cards", None)
        return data

    async def get_card(self, provider_id: str) -> CardRecord:
        """`provider_id` is a TCGdex card id, optionally "ja:<id>" for Japanese."""
        lang, _, cid = provider_id.rpartition(":")
        c = await self.card(cid, lang or "en")
        s = c.get("set") or {}
        total = (s.get("cardCount") or {}).get("official")
        return CardRecord("tcgdex", c["id"], c.get("name") or "", s.get("name"), s.get("id"),
                          f"{c.get('localId')}/{total}" if c.get("localId") and total else c.get("localId"), None, c.get("rarity"),
                          image_url(c.get("image")), "Japanese" if lang == "ja" else "English", {"variants": c.get("variants")})

    async def search_cards(self, query: str) -> list[CardRecord]:
        hits = await self.get("en", f"/cards?name={quote(query)}")
        return [CardRecord("tcgdex", h["id"], h.get("name") or "", image_url=image_url(h.get("image")), language="English") for h in hits[:50]]
