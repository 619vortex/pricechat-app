"""PSA (Professional Sports Authenticator), official PSA Public API.

Spec: https://api.psacard.com/publicapi/swagger.json. Auth: `Authorization: bearer <token>` (PSA_API_KEY). PSA must
APPROVE the account first: until then every call answers 403 "Access to this API is limited to approved customers."
Endpoints used (read-only):
  GET /cert/GetByCertNumber/{certNumber}       one slab: card identity, grade, label, and that grade's population
  GET /pop/GetPSASpecPopulation/{specID}       card-wide population per grade for one PSA spec (= one exact card)
Two kinds of numbers, never mixed:
  - certificate-specific: what PSA says about ONE slab (cert number, grade, label, its SpecID);
  - card-wide population: how many copies of that spec PSA has graded per grade (source "psa").
PSA has no price, sales or population-history endpoint. Results are cached (certs never change; population is cached
for hours) so the free tier's ~100 calls/day go far. Nothing from psacard.com pages is scraped.
"""

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any, ClassVar

import httpx

from app.providers.pokemon.base import PokemonProvider, PopulationRecord, ProviderInfo, Unavailable

CERT_TTL = 7 * 24 * 3600  # a cert's identity and grade don't change
POP_TTL = 12 * 3600
NOT_APPROVED = ("PSA recognised the key, but this PSA account isn't approved for the Public API yet "
                "(PSA: \"Access to this API is limited to approved customers\"). Ask PSA to approve API access "
                "(collectors-apis@collectors.com); nothing else is needed here.")

# PSASpecPopSummmaryModel field -> PSA grade. Qualified counts (Grade9Q = "9 with a qualifier such as OC") and "Auth"
# are a different product from the plain grade, so they are kept apart and never added to the grade's count.
_POP_FIELD = re.compile(r"^Grade(\d{1,2})(?:_(5))?$")


def pop_grade(field: str) -> str | None:
    m = _POP_FIELD.match(field)
    if not m:
        return None
    return m.group(1) + (".5" if m.group(2) else "")


class PSAProvider(PokemonProvider):
    info = ProviderInfo(
        key="psa", name="PSA", serves=("cert", "get_population"),
        access="PSA account, then an API token from psacard.com/publicapi. PSA must approve the account for API access.",
        licence="Cert verification and population by spec, under the PSA API End User Agreement. No prices or sales.",
        credentials=("psa_api_token",),
        rate_limit="Free tier: 100 calls/day")

    _cache: ClassVar[dict[str, tuple[float, Any]]] = {}
    retries = 2
    backoff = 1.0  # seconds; doubled per retry

    def __init__(self, settings: Any, transport: Any = None) -> None:
        super().__init__(settings, transport)
        self.requests = 0

    def notes(self) -> tuple[str, ...]:
        return ("Population: card-wide counts per grade come from PSA's spec population endpoint for cards linked to a PSA "
                "SpecID (link a cert on the card page). A cert's own figures describe that one slab only.",
                "PSA provides no prices, sales or population history.")

    async def _get(self, path: str, ttl: float) -> dict[str, Any]:
        """One authenticated GET with caching, retries on outages, and plain reasons for every failure."""
        connected, why = self.status()
        if not connected:
            raise Unavailable(why, "not_connected")
        from app.security.credentials import resolve

        token = resolve("psa_api_token", self.settings)
        cache_key = f"{self.settings.psa_api_base}|{path}"
        hit = self._cache.get(cache_key)
        if hit and hit[0] > time.time():
            return hit[1]
        url = f"{self.settings.psa_api_base.rstrip('/')}{path}"
        r: httpx.Response | None = None
        for attempt in range(self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=20.0, transport=self.transport) as client:
                    self.requests += 1
                    r = await client.get(url, headers={"Authorization": f"bearer {token}"})
            except httpx.HTTPError:
                r = None
            if (r is not None and r.status_code < 500) or attempt == self.retries:
                break
            await asyncio.sleep(self.backoff * 2**attempt)  # outage / PSA error: retry, then give up
        if r is None:
            raise Unavailable("PSA didn't answer. Try again later.", "unreachable")
        text = r.text[:300]
        if r.status_code == 403 and "approved" in text.lower():
            raise Unavailable(NOT_APPROVED, "not_approved")
        if r.status_code in (401, 403):
            raise Unavailable("PSA refused the API key (expired or invalid). Create a new token at psacard.com/publicapi.", "auth")
        if r.status_code == 429:
            raise Unavailable("PSA's daily API limit is reached (100 calls/day on the free tier). Try again tomorrow.", "rate_limited")
        if r.status_code == 204:
            raise Unavailable("PSA: the request was empty.", "invalid")
        if r.status_code >= 500:
            raise Unavailable("PSA answered with an error (it also does this for an invalid key). Try again later.", "unreachable")
        if r.status_code != 200:
            raise Unavailable(f"PSA answered HTTP {r.status_code}.", "unavailable")
        try:
            body = r.json()
        except ValueError:
            raise Unavailable("PSA answered with something that isn't JSON.", "unavailable") from None
        if not isinstance(body, dict):
            raise Unavailable("PSA answered in an unexpected format.", "unavailable")
        if body.get("IsValidRequest") is False:
            raise Unavailable(str(body.get("ServerMessage") or "PSA: invalid request."), "invalid")
        self._cache[cache_key] = (time.time() + ttl, body)
        return body

    async def get_cert(self, cert_number: str) -> dict[str, Any]:
        """Verify one PSA cert: CERTIFICATE-SPECIFIC facts (this slab), with PSA's names normalised. Values as PSA gives them."""
        cert = "".join(ch for ch in str(cert_number) if ch.isdigit())
        if not cert:
            raise Unavailable("A PSA cert number is digits only.", "invalid")
        body = await self._get(f"/cert/GetByCertNumber/{cert}", CERT_TTL)
        c = body.get("PSACert") or {}
        if not c:
            raise Unavailable(str(body.get("ServerMessage") or "PSA has no record of that cert."), "not_found")
        pick = {"cert_number": "CertNumber", "spec_id": "SpecID", "spec_number": "SpecNumber", "year": "Year", "brand": "Brand",
                "category": "Category", "card_number": "CardNumber", "subject": "Subject", "variety": "Variety",
                "grade": "CardGrade", "grade_description": "GradeDescription", "label_type": "LabelType",
                "total_population": "TotalPopulation", "population_higher": "PopulationHigher",
                "total_population_with_qualifier": "TotalPopulationWithQualifier", "item_status": "ItemStatus"}
        out = {k: c.get(v) for k, v in pick.items() if c.get(v) not in (None, "")}
        out["scope"] = "certificate"  # these describe ONE slab; total_population is PSA's count at this slab's grade
        return out

    async def get_spec_population(self, spec_id: str | int) -> dict[str, Any]:
        """CARD-WIDE population for one PSA spec: plain grades (records), qualified counts and totals kept apart."""
        sid = "".join(ch for ch in str(spec_id) if ch.isdigit())
        if not sid:
            raise Unavailable("A PSA SpecID is digits only.", "invalid")
        body = await self._get(f"/pop/GetPSASpecPopulation/{sid}", POP_TTL)
        pop = body.get("PSAPop") or {}
        if not pop:
            raise Unavailable(str(body.get("ServerMessage") or "PSA has no population for that spec."), "not_found")
        now = datetime.now(timezone.utc)
        records = [PopulationRecord("psa", "PSA", g, int(v), now)
                   for k, v in pop.items() if (g := pop_grade(k)) is not None and isinstance(v, int)]
        qualified = {k: v for k, v in pop.items() if k.endswith("Q") and isinstance(v, int) and v}
        return {"spec_id": sid, "description": body.get("Description"), "records": records, "qualified": qualified,
                "total": pop.get("Total"), "authentic": pop.get("Auth"), "scope": "card", "as_of": now.isoformat()}

    async def get_population(self, provider_id: str) -> list[PopulationRecord]:
        self._need("get_population")
        return (await self.get_spec_population(provider_id))["records"]
