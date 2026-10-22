"""GemRate (https://docs.gemrate.com): graded-card POPULATION across PSA, Beckett, SGC and CGC, by an official API.

Access: an API key from the GemRate team (x-api-key header). Basic plans include card search and current population;
population HISTORY (daily since 2022-01-01, sampled weekly / two-weekly over three years), the change feed, cert images
and bulk catalogs need a higher plan (403 otherwise). Quotas per plan (X-RateLimit-* headers) plus a per-second limit.

What Price Chat takes: population counts per exact grade. Grade keys are the grader's own (psa_10, beckett_9_5,
cgc_10 ...). Tiers Price Chat doesn't track separately are SKIPPED, never merged into another grade: PSA qualifiers and
Authentic, Beckett 10 Black Label, CGC Pristine / Perfect 10, every SGC grade. Never prices.
"""

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.providers.pokemon.base import CardRecord, PokemonProvider, PopulationRecord, ProviderInfo, Unavailable

_GRADERS = {"psa": "PSA", "beckett": "BGS", "cgc": "CGC"}  # SGC isn't a grading company Price Chat tracks yet
_KEY = re.compile(r"^(psa|beckett|cgc|sgc)_(\d{1,2})(?:_(5))?(?:_(pristine|perfect|black))?$")


def grade_from_key(key: str) -> tuple[str, str] | None:
    """'psa_10' -> ('PSA', '10'); 'beckett_9_5' -> ('BGS', '9.5'); 'beckett_10_pristine' -> ('BGS', '10').
    None for anything Price Chat must not merge: qualifiers, auth, Black Label, CGC Pristine / Perfect, SGC."""
    m = _KEY.match(key or "")
    if not m:
        return None
    grader, whole, half, tier = m.groups()
    company = _GRADERS.get(grader)
    if company is None:
        return None
    n = float(whole) + (0.5 if half else 0)
    if not 1 <= n <= 10:
        return None
    if tier and not (company == "BGS" and tier == "pristine" and n == 10):
        return None  # BGS Black Label / CGC Pristine & Perfect are separate assets
    if company == "BGS" and n == 10 and not tier:
        return None  # Beckett reports its 10s with a tier
    return company, str(int(n)) if n == int(n) else str(n)


def _day(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class GemRateProvider(PokemonProvider):
    info = ProviderInfo(
        key="gemrate", name="GemRate", serves=("search_cards", "get_population", "population_history"),
        access="API key from the GemRate team (docs.gemrate.com). Population history needs a plan that includes it.",
        licence="Under your GemRate API agreement: population counts as reported by PSA, Beckett and CGC. Check the agreement "
                "before showing them publicly.",
        credentials=("gemrate_api_key",),
        rate_limit="Per plan: call quotas per bucket + a per-second limit")

    requests = 0

    def notes(self) -> tuple[str, ...]:
        return ("Cards are linked to GemRate only when its description matches the catalog card with certainty (or an admin "
                "sets the GemRate id on the card). Unlinked cards keep population “—”.",
                "Not imported (never merged into another grade): PSA qualifiers, BGS Black Label 10, CGC Pristine / Perfect 10, SGC.")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        connected, why = self.status()
        if not connected:
            raise Unavailable(why, "not_connected")
        from app.security.credentials import resolve

        key = resolve("gemrate_api_key", self.settings)
        self.requests += 1
        try:
            async with httpx.AsyncClient(timeout=25.0, transport=self.transport) as client:
                r = await client.get(f"{self.settings.gemrate_api_base.rstrip('/')}{path}", params=params or {},
                                     headers={"x-api-key": str(key), "accept": "application/json"})
        except httpx.HTTPError as exc:
            raise Unavailable("GemRate didn't answer. Try again later.", "unreachable") from exc
        body: dict[str, Any] = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        code = ((body.get("error") or {}).get("code")) or ""
        if r.status_code == 401:
            raise Unavailable("GemRate refused the API key (missing, disabled or invalid).", "auth")
        if r.status_code == 403:
            raise Unavailable("GemRate: this API key's plan doesn't include that data.", "no_access")
        if r.status_code == 429:
            raise Unavailable("GemRate quota used up for this period." if code == "quota_exceeded" else "GemRate rate limit: slow down.",
                              "rate_limited")
        if r.status_code == 404:
            raise Unavailable("GemRate has no such card.", "not_found")
        if r.status_code != 200 or "data" not in body:
            raise Unavailable(f"GemRate error {r.status_code}{f' ({code})' if code else ''}.", "unavailable")
        return body

    async def search_cards(self, query: str) -> list[CardRecord]:
        """Structured search: '<year> <set> <subject> <parallel> <number>'. One CardRecord per (grader, match)."""
        self._need("search_cards")
        data = (await self._get("/cards/search/structured", {"query": query}))["data"]
        out = []
        for grader, hits in (data.get("results") or {}).items():
            for h in hits or []:
                out.append(CardRecord(provider="gemrate", provider_id=str(h["gemrate_id"]), name=str(h.get("description") or ""),
                                      extra={"grader": grader, "population": h.get("population"), "score": h.get("search_score")}))
        return out

    async def get_population(self, provider_id: str) -> list[PopulationRecord]:
        """Current population per exact grade (all graders GemRate links to this card)."""
        self._need("get_population")
        data = (await self._get(f"/cards/{provider_id}/population"))["data"]
        pd = ((data.get("population") or {}).get("population_data")) or {}
        default = _day(pd.get("data_last_updated")) or datetime.now(timezone.utc)
        out = []
        for grader, block in (pd.get("by_grader") or {}).items():
            as_of = _day((block or {}).get("last_population_change")) or default
            for key, count in ((block or {}).get("grades") or {}).items():
                g = grade_from_key(key)
                if g is not None and isinstance(count, int) and count >= 0:
                    out.append(PopulationRecord("gemrate", g[0], g[1], count, as_of))
        return out

    async def population_history(self, provider_id: str, interval: str = "two_week") -> list[PopulationRecord]:
        """Population per grade over time (needs a plan with history: 403 -> Unavailable 'no_access'). Pre-history entries
        (null counts) are skipped, never zero-filled."""
        self._need("population_history")
        data = (await self._get(f"/cards/{provider_id}/population/history", {"interval": interval}))["data"]
        pd = ((data.get("population") or {}).get("population_data")) or {}
        out = []
        for grader, block in (pd.get("by_grader") or {}).items():
            for point in (block or {}).get("history") or []:
                when = _day(point.get("date"))
                if when is None or point.get("grades") is None:
                    continue
                for key, count in point["grades"].items():
                    g = grade_from_key(key)
                    if g is not None and isinstance(count, int) and count >= 0:
                        out.append(PopulationRecord("gemrate", g[0], g[1], count, when))
        return out
