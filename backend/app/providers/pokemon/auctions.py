"""Auction houses (Heritage, Goldin, Fanatics Collect / PWCC): REAL realised prices, but no public API.

How their results reach Price Chat: an export or record of results the owner may use (their own account's won lots /
consignment statements, a results file the house provides) imported with Data sources › Import sales (CSV) and the
house's preset. Nothing is fetched from their sites: Heritage's archive needs a login and sits behind anti-bot protection,
and none of them licenses its results for automated collection. Realised prices are SALES (verified by the admin's import).
"""

from app.providers.pokemon.base import PokemonProvider, ProviderInfo


class _AuctionExport(PokemonProvider):
    def status(self) -> tuple[bool, str]:
        return False, "No API: import a results export with Import sales (CSV) and this house's preset."


class HeritageProvider(_AuctionExport):
    info = ProviderInfo(
        key="heritage", name="Heritage Auctions", serves=("get_sales",),
        access="No public API. Auction Archives need a free account to show realised prices; export / record your own results.",
        licence="Your own records, or data Heritage permits you to use. No automated collection (the site uses anti-bot protection).")


class GoldinProvider(_AuctionExport):
    info = ProviderInfo(
        key="goldin", name="Goldin", serves=("get_sales",),
        access="No public API. Import results from your own account (won lots / consignments) or a file Goldin provides.",
        licence="Your own records, or data Goldin permits you to use. No scraping.")


class FanaticsProvider(_AuctionExport):
    info = ProviderInfo(
        key="fanatics", name="Fanatics Collect (PWCC)", serves=("get_sales",),
        access="No public API. Import results from your own account or a file Fanatics Collect provides.",
        licence="Your own records, or data Fanatics Collect permits you to use. No scraping.")
