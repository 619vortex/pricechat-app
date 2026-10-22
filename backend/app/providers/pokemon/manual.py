"""Manually imported sales and population (JSON rows from the dashboard).

Always available: the owner supplies records they are entitled to use (own sales, licensed exports, auction results
they recorded). Every row is validated, normalised and deduplicated like any other source; imported sales are marked
unverified unless the importer says they were verified.
"""

from app.providers.pokemon.base import PokemonProvider, ProviderInfo


class ManualProvider(PokemonProvider):
    info = ProviderInfo(
        key="manual", name="Manual import",
        serves=("get_sales", "get_population"),
        access="Import in the dashboard (Pokémon › Data sources).",
        licence="Only data you have the right to use.",
        credentials=())
