"""TCGplayer.

The TCGplayer developer API has been closed to new applicants since late 2024 (owned by eBay); access is limited
to existing key holders and approved partners. Price Chat has no TCGplayer credentials.
"""

from app.providers.pokemon.base import PokemonProvider, ProviderInfo


class TCGplayerProvider(PokemonProvider):
    info = ProviderInfo(
        key="tcgplayer", name="TCGplayer",
        serves=(),
        access="Closed to new developers. Only existing key holders / approved partners.",
        licence="—",
        credentials=())

    def status(self) -> tuple[bool, str]:
        return False, "Not available: TCGplayer isn't accepting new API applications."
