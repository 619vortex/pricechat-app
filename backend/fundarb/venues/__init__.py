"""The four venue adapters behind one interface (fundarb.venues.base.PerpVenue)."""

from fundarb.venues.backpack import BackpackVenue
from fundarb.venues.base import PerpVenue, TradingDisabled, VenueError, VenueUnavailable
from fundarb.venues.hyperliquid import HyperliquidVenue
from fundarb.venues.lighter import LighterVenue
from fundarb.venues.orderly import OrderlyVenue

VENUES: dict[str, type[PerpVenue]] = {"hyperliquid": HyperliquidVenue, "lighter": LighterVenue, "orderly": OrderlyVenue, "backpack": BackpackVenue}

__all__ = ["VENUES", "PerpVenue", "HyperliquidVenue", "LighterVenue", "OrderlyVenue", "BackpackVenue", "VenueError", "VenueUnavailable",
           "TradingDisabled"]
