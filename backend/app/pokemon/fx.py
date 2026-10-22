"""USD conversion for sales in other currencies: the European Central Bank reference rate on the sale's date, via the
free Frankfurter API (no key). The rate and its source are stored on the sale. No rate available -> the row is rejected
(never guessed); the importer can also take an explicit usd_price or fx_rate column (source "admin")."""

from datetime import date
from typing import Any

import httpx

SUPPORTED = {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "HKD", "SGD", "CNY", "KRW", "NZD", "SEK", "NOK", "DKK"}


class FxError(Exception):
    pass


class FxRates:
    """Caches rates per (currency, day) for one import."""

    def __init__(self, base: str, transport: Any = None) -> None:
        self.base, self.transport = base.rstrip("/"), transport
        self._cache: dict[tuple[str, date], float] = {}

    def usd_per_unit(self, currency: str, day: date) -> tuple[float, str]:
        cur = currency.upper()
        if cur == "USD":
            return 1.0, "usd"
        if cur not in SUPPORTED:
            raise FxError(f"{cur} isn't a supported currency")
        key = (cur, day)
        if key not in self._cache:
            try:
                with httpx.Client(timeout=15.0, transport=self.transport, follow_redirects=True) as c:
                    r = c.get(f"{self.base}/{day.isoformat()}", params={"from": cur, "to": "USD"})
            except httpx.HTTPError as exc:
                raise FxError("the exchange-rate service didn't answer") from exc
            rate = (r.json().get("rates") or {}).get("USD") if r.status_code == 200 else None
            if not rate:
                raise FxError(f"no ECB rate for {cur} on {day.isoformat()}")
            self._cache[key] = float(rate)
        return self._cache[key], "ecb"


# --- display rate: USD -> JPY for showing prices in yen (never used to create or change a price) ----------------------

def latest_usd_jpy(settings: Any, transport: Any = None, max_age_hours: float = 6.0) -> dict[str, Any] | None:
    """The latest ECB reference rate (via Frankfurter), cached on disk; refreshed at most every `max_age_hours`.
    Returns {"rate", "rate_date", "fetched_at", "source", "stale"} or None when no rate has ever been fetched."""
    import json
    import time
    from datetime import datetime, timezone
    from pathlib import Path

    f = Path(settings.tcgdex_cache_dir).parent / "fx" / "usd_jpy.json"
    cached = None
    try:
        cached = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if cached and time.time() - f.stat().st_mtime < max_age_hours * 3600:
        return {**cached, "stale": False}
    try:
        with httpx.Client(timeout=10.0, transport=transport, follow_redirects=True) as c:
            r = c.get(f"{settings.fx_api_base.rstrip('/')}/latest", params={"from": "USD", "to": "JPY"})
        rate = (r.json().get("rates") or {}).get("JPY") if r.status_code == 200 else None
        if not rate:
            raise FxError("no USD/JPY rate")
        out = {"base": "USD", "quote": "JPY", "rate": float(rate), "rate_date": str(r.json().get("date")),
               "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "European Central Bank reference rate (via Frankfurter)"}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out), encoding="utf-8")
        return {**out, "stale": False}
    except (httpx.HTTPError, ValueError, FxError, OSError):
        return {**cached, "stale": True} if cached else None
