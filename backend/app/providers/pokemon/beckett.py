"""Beckett (BGS).

No public developer API or licensed data feed for BGS population or prices was found. The BGS pop report on
beckett.com is a website, not an API, and isn't scraped. A licensed feed would need an agreement with Beckett.
"""

from app.providers.pokemon.base import PokemonProvider, ProviderInfo


class BeckettProvider(PokemonProvider):
    info = ProviderInfo(
        key="beckett", name="Beckett (BGS)",
        serves=(),
        access="No public API. A data agreement with Beckett would be needed.",
        licence="—",
        credentials=())

    def status(self) -> tuple[bool, str]:
        return False, "Not available: Beckett has no public API or data feed for this."
