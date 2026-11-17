"""Official status pages: the providers' own word that a service is down (owner, 2026-11-17: "prioritize official status feeds and reliable
free sources for banking, exchanges, cloud services and major infrastructure incidents").

Public, free, no key, documented machine endpoints only:
- Atlassian Statuspage API (/api/v2/summary.json: its open incidents): Cloudflare, GitHub, Coinbase, Kraken, Gemini, Crypto.com, Bitstamp,
  Robinhood, Square, OpenAI, DigitalOcean, Twilio. Only incidents the provider rates "major" or "critical" become items.
- Google Cloud (status.cloud.google.com/incidents.json): open incidents rated "high" or a SERVICE_OUTAGE.
- AWS Health Dashboard RSS: "Service disruption" items from the last 12 hours not marked resolved.
- Azure status RSS: Azure posts only broad-impact incidents there; items from the last 12 hours.

Each item is official (extra.official): it verifies that the provider itself says so, nothing more. Its headline is written from the
provider's own words ("Cloudflare confirms major outage: <incident name>"), so app/newsdesk/incidents reads it like a news headline and
joins it to news of the same company's outage (incidents.same_incident), never to another company's."""

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

STATUSPAGES: dict[str, str] = {
    "Cloudflare": "https://www.cloudflarestatus.com", "GitHub": "https://www.githubstatus.com", "Coinbase": "https://status.coinbase.com",
    "Kraken": "https://status.kraken.com", "Gemini": "https://status.gemini.com", "Crypto.com": "https://status.crypto.com",
    "Bitstamp": "https://status.bitstamp.net", "Robinhood": "https://status.robinhood.com", "Square": "https://www.issquareup.com",
    "OpenAI": "https://status.openai.com", "DigitalOcean": "https://status.digitalocean.com", "Twilio": "https://status.twilio.com",
}
GCP = "https://status.cloud.google.com/incidents.json"
AWS = "https://status.aws.amazon.com/rss/all.rss"
AZURE = "https://azure.status.microsoft/en-us/status/feed/"
SEVERE = {"major", "critical"}
RECENT = timedelta(hours=12)
UA = "server:pricechat-trending-topics:1.0"
Item = dict[str, Any]


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _clean(t: str, n: int = 140) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")).strip()[:n]


def _item(provider: str, key: str, title: str, url: str, at: datetime | None, summary: str = "", **extra: Any) -> Item:
    return {"source": "status", "external_id": f"status:{provider.lower()}:{key}"[:300], "title": title[:300], "summary": summary[:800], "url": url,
            "outlet": f"{provider} status", "reliable": True, "engagement": 1.0, "published_at": at,
            "extra": {"official": True, "provider": provider, **extra}}


def parse_statuspage(provider: str, base: str, body: dict[str, Any]) -> list[Item]:
    out = []
    for inc in body.get("incidents") or []:
        impact = str(inc.get("impact") or "").lower()
        if impact not in SEVERE or inc.get("status") in ("resolved", "postmortem"):
            continue
        name = _clean(str(inc.get("name") or ""))
        if not name:
            continue
        title = f"{provider} confirms {impact} outage: {name}"
        url = str(inc.get("shortlink") or f"{base}/incidents/{inc.get('id')}")
        out.append(_item(provider, str(inc.get("id")), title, url, _when(inc.get("started_at") or inc.get("created_at")),
                         summary=_clean(str(((inc.get("incident_updates") or [{}])[0]).get("body") or ""), 600), impact=impact, status=inc.get("status")))
    return out


def parse_gcp(body: list[dict[str, Any]], now: datetime) -> list[Item]:
    out = []
    for inc in body or []:
        if inc.get("end"):
            continue
        if str(inc.get("severity") or "") != "high" and inc.get("status_impact") != "SERVICE_OUTAGE":
            continue
        begin = _when(inc.get("begin"))
        if begin is not None and now - begin > timedelta(days=2):
            continue
        desc = _clean(str(inc.get("external_desc") or ""))
        out.append(_item("Google Cloud", str(inc.get("id")), f"Google Cloud confirms outage: {desc}", f"https://status.cloud.google.com/{inc.get('uri') or ''}", begin,
                         impact=str(inc.get("severity") or "")))
    return out


def parse_rss(provider: str, xml_text: str, now: datetime, *, must: re.Pattern[str] | None = None) -> list[Item]:
    if len(xml_text) > 3_000_000 or "<!ENTITY" in xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        title = _clean(it.findtext("title") or "")
        at = _when(it.findtext("pubDate"))
        if not title or at is None or now - at > RECENT or re.search(r"\b(resolved|operating normally|informational)\b", title, re.I):
            continue
        if must is not None and not must.search(title):
            continue
        guid = it.findtext("guid") or title
        what = re.sub(r"^service disruption:\s*", "", title, flags=re.I)
        region = re.search(r"#([a-z0-9-]+?)_\d+$", guid)
        label = f"{provider} confirms service disruption: {what}" + (f" ({region.group(1)})" if region else "")
        out.append(_item(provider, guid[-120:], label, it.findtext("link") or "", at, summary=_clean(it.findtext("description") or "", 600)))
    return out


async def read(client: httpx.AsyncClient, now: datetime) -> tuple[list[Item], str | None, dict[str, Any]]:
    """Every status page, in parallel. A page that fails is reported, never stops the others."""
    problems: list[str] = []
    headers = {"User-Agent": UA, "Accept": "application/json, application/rss+xml, application/xml;q=0.9"}

    async def get(url: str) -> httpx.Response | None:
        try:
            r = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            problems.append(f"{url.split('/')[2]}: {type(e).__name__}")
            return None
        if r.status_code != 200:
            problems.append(f"{url.split('/')[2]}: HTTP {r.status_code}")
            return None
        return r

    names = list(STATUSPAGES)
    urls = [f"{STATUSPAGES[n]}/api/v2/summary.json" for n in names] + [GCP, AWS, AZURE]
    got = await asyncio.gather(*(get(u) for u in urls))
    items: list[Item] = []
    for name, r in zip(names, got[: len(names)]):
        if r is not None:
            try:
                items += parse_statuspage(name, STATUSPAGES[name], r.json())
            except (ValueError, json.JSONDecodeError):
                problems.append(f"{name}: not JSON")
    gcp, aws, azure = got[len(names):]
    if gcp is not None:
        try:
            items += parse_gcp(gcp.json(), now)
        except (ValueError, json.JSONDecodeError):
            problems.append("Google Cloud: not JSON")
    if aws is not None:
        items += parse_rss("AWS", aws.text, now, must=re.compile(r"^service (?:disruption|impact)", re.I))
    if azure is not None:
        items += parse_rss("Microsoft Azure", azure.text, now)
    ok = len(urls) - len(problems)
    return items, (None if ok else "; ".join(problems[:4])), {"requests": len(urls), "idle": not items, "partial": "; ".join(problems[:4]) if problems and ok else ""}
