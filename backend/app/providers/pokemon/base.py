"""Pokémon market-data providers: one adapter per source, one normalised interface.

Every method either returns real data from the provider, or raises `Unavailable` with a plain reason (no credentials,
no licence, the provider has no such endpoint). An adapter never invents, estimates or scrapes. Which methods a source
can serve, and on what terms, is declared in `ProviderInfo` and shown on the Data Sources page.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config.settings import Settings


class Unavailable(Exception):
    """The provider can't serve this (not connected, not licensed, or no such data). Never "no results"."""

    def __init__(self, reason: str, code: str = "unavailable") -> None:
        super().__init__(reason)
        self.reason, self.code = reason, code


@dataclass(slots=True)
class CardRecord:
    provider: str
    provider_id: str
    name: str
    set_name: str | None = None
    set_code: str | None = None
    number: str | None = None
    year: int | None = None
    rarity: str | None = None
    image_url: str | None = None
    language: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PriceRecord:
    provider: str
    grading_company: str
    grade: str
    price_cents: int
    currency: str
    as_of: datetime


@dataclass(slots=True)
class SaleRecord:
    provider: str
    grading_company: str
    grade: str
    price_cents: int
    currency: str
    sale_date: datetime
    source_listing_id: str | None = None
    url: str | None = None
    sale_type: str | None = None
    cert_number: str | None = None
    auction_house: str | None = None


@dataclass(slots=True)
class PopulationRecord:
    provider: str
    grading_company: str
    grade: str
    count: int
    as_of: datetime


@dataclass(slots=True)
class ListingRecord:
    """An active listing (asking price) or, with `sold_at`, an item the provider reports as sold."""

    provider: str
    listing_id: str
    title: str
    price_cents: int
    currency: str
    url: str | None = None
    condition: str | None = None
    seller: str | None = None
    listing_type: str | None = None
    image_url: str | None = None
    sold_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    key: str
    name: str
    serves: tuple[str, ...]  # which of the six methods this source can legitimately serve once connected
    access: str  # how access is obtained (shown to the owner)
    licence: str  # what we may do with the data
    credentials: tuple[str, ...] = ()  # secret names (KNOWN_SECRETS) it needs; values are never shown
    rate_limit: str = ""  # as documented by the provider


class PokemonProvider:
    info: ProviderInfo

    def __init__(self, settings: Settings, transport: Any = None) -> None:
        self.settings, self.transport = settings, transport

    # --- status ------------------------------------------------------------------------------------------------
    def missing_credentials(self) -> list[str]:
        from app.security.credentials import resolve

        return [c for c in self.info.credentials if not resolve(c, self.settings)]

    def status(self) -> tuple[bool, str]:
        """(connected, why not). Connected = the credentials exist; it says nothing about data having been imported."""
        from app.security.credentials import env_name

        missing = self.missing_credentials()
        if missing:
            return False, f"Not connected: add {', '.join(env_name(m) for m in missing)}."
        return True, ""

    def notes(self) -> tuple[str, ...]:
        """Extra facts for the Data Sources page (e.g. what this account's access does NOT include)."""
        return ()

    def _need(self, method: str) -> None:
        connected, why = self.status()
        if method not in self.info.serves:
            raise Unavailable(f"{self.info.name} doesn't provide this ({method}).", "not_offered")
        if not connected:
            raise Unavailable(why, "not_connected")

    # --- the normalised interface (default: unavailable) ---------------------------------------------------------
    async def search_cards(self, query: str) -> list[CardRecord]:
        self._need("search_cards")
        raise Unavailable(f"{self.info.name}: card search isn't implemented yet.", "not_implemented")

    async def get_card(self, provider_id: str) -> CardRecord:
        self._need("get_card")
        raise Unavailable(f"{self.info.name}: card lookup isn't implemented yet.", "not_implemented")

    async def get_current_prices(self, provider_id: str) -> list[PriceRecord]:
        self._need("get_current_prices")
        raise Unavailable(f"{self.info.name}: prices aren't implemented yet.", "not_implemented")

    async def get_sales(self, provider_id: str, since: datetime | None = None) -> list[SaleRecord]:
        self._need("get_sales")
        raise Unavailable(f"{self.info.name}: sales aren't implemented yet.", "not_implemented")

    async def get_population(self, provider_id: str) -> list[PopulationRecord]:
        self._need("get_population")
        raise Unavailable(f"{self.info.name}: population isn't implemented yet.", "not_implemented")

    async def get_listing_data(self, query: str) -> list[ListingRecord]:
        self._need("get_listing_data")
        raise Unavailable(f"{self.info.name}: listings aren't implemented yet.", "not_implemented")
