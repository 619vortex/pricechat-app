"""Registry of Pokémon market-data providers (see base.py for the contract)."""

from typing import Any

from app.config.settings import Settings
from app.providers.pokemon.base import PokemonProvider, Unavailable
from app.providers.pokemon.auctions import FanaticsProvider, GoldinProvider, HeritageProvider
from app.providers.pokemon.beckett import BeckettProvider
from app.providers.pokemon.ebay import EbayProvider
from app.providers.pokemon.gemrate import GemRateProvider
from app.providers.pokemon.manual import ManualProvider
from app.providers.pokemon.pokemontcg import PokemonTCGProvider
from app.providers.pokemon.pricecharting import PriceChartingProvider
from app.providers.pokemon.psa import PSAProvider
from app.providers.pokemon.tcgplayer import TCGplayerProvider
from app.providers.pokemon.tcgdex import TCGdexProvider
from app.providers.pokemon.scrydex import ScrydexProvider

PROVIDERS: dict[str, type[PokemonProvider]] = {p.info.key: p for p in (
    PSAProvider, GemRateProvider, EbayProvider, PriceChartingProvider, HeritageProvider, GoldinProvider, FanaticsProvider, TCGplayerProvider,
    BeckettProvider, PokemonTCGProvider, TCGdexProvider, ScrydexProvider, ManualProvider)}


def provider(key: str, settings: Settings, transport: Any = None) -> PokemonProvider:
    if key not in PROVIDERS:
        raise Unavailable(f"Unknown provider: {key}", "unknown")
    return PROVIDERS[key](settings, transport)


__all__ = ["PROVIDERS", "PokemonProvider", "Unavailable", "provider"]
