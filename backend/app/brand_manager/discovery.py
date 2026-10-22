"""Topic discovery + research: finds what to make videos about, from authorized, reliable sources, and writes the brief
(facts, numbers, images, sources) the script must stick to. Nothing here is generated: every fact has a source.

Sources: crypto news RSS feeds (CoinDesk, Cointelegraph, Decrypt: headlines + summaries, linked, never copied in full),
CoinGecko market data and its trending list, Hyperliquid (big moves, funding, open interest: app/brand_manager/sources.py),
Price Chat's own Pokémon market (verified sales from every marketplace and auction house, validated 30-day moves up and
down with their price history, Top 100 entries), a hand-checked fact bank, Price Chat's own YouTube videos and the
community's weekly totals. Not used: TikTok trend scraping (no such authorized API) and Google Trends (no
official API).

Ranking: audience relevance (category weight) × freshness × size of the story × evidence × originality × how the
category performed before (analytics) × riding a live trend word (trends.py, scaled by how trend videos performed). Topics seen in the last `duplicate_days` are skipped.
"""

import asyncio
import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand_manager import sources
from app.brand_manager.defaults import CATEGORIES, COINS, CRYPTO_FEEDS, FACTS
from app.db.base import utcnow
from app.models.brand_manager import BmVideo

logger = logging.getLogger(__name__)
COINGECKO = "https://api.coingecko.com/api/v3"
UA = {"User-Agent": "PriceChat-BrandManager/1.0 (+https://pricechat.live)"}


@dataclass
class Topic:
    category: str
    topic: str
    key: str
    score: float
    brief: dict[str, Any] = field(default_factory=dict)

    @property
    def template(self) -> str:
        return CATEGORIES[self.category]["template"]


def _key(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p).lower().strip() for p in parts).encode()).hexdigest()[:32]


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def _money(cents_or_usd: float, cents: bool = True) -> str:
    v = cents_or_usd / 100 if cents else cents_or_usd
    return f"${v:,.2f}" if v < 100 else f"${v:,.0f}"


def _clean(text: str, n: int = 400) -> str:
    t = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n]


# --- crypto news (RSS) -------------------------------------------------------------------------------------------------


def parse_feed(xml_text: str, source: str) -> list[dict[str, Any]]:
    if len(xml_text) > 2_000_000 or "<!ENTITY" in xml_text or "<!DOCTYPE" in xml_text[:2000]:
        return []  # no entity tricks
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title") or "", 300)
        link = (item.findtext("link") or "").strip()
        when = None
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "")
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
        if title and link.startswith("http"):
            out.append({"title": title, "link": link[:500], "summary": _clean(item.findtext("description") or "", 500), "at": when, "source": source})
    return out


async def crypto_news(client: httpx.AsyncClient, now: datetime) -> list[Topic]:
    topics: list[Topic] = []
    for name, url in CRYPTO_FEEDS:
        try:
            r = await client.get(url, headers=UA)
            items = parse_feed(r.text, name) if r.status_code == 200 else []
        except httpx.HTTPError:
            items = []
        for it in items[:15]:
            if it["at"] is None or now - it["at"] > timedelta(hours=18):
                continue
            hours = max(0.0, (now - it["at"]).total_seconds() / 3600)
            relevance = 1.3 if re.search(r"bitcoin|btc|ethereum|eth\b|solana|etf|sec\b|binance|xrp", it["title"], re.I) else 1.0
            facts = [{"text": it["title"], "source": it["source"]}]
            if it["summary"] and it["summary"].lower() != it["title"].lower():
                facts.append({"text": it["summary"], "source": it["source"]})
            topics.append(Topic("crypto_news", it["title"], _key("news", it["link"]), relevance * max(0.2, 1 - hours / 18), {
                "headline": it["title"], "facts": facts, "numbers": {}, "images": [],
                "sources": [{"name": it["source"], "url": it["link"], "published": it["at"].isoformat()}],
                "note": "Report only what the headline and summary say; attribute it to the source."}))
    return topics


# --- crypto market (CoinGecko) -------------------------------------------------------------------------------------------


async def crypto_market(client: httpx.AsyncClient, now: datetime) -> list[Topic]:
    try:
        r = await client.get(f"{COINGECKO}/coins/markets", headers=UA, params={
            "vs_currency": "usd", "ids": ",".join(c[0] for c in COINS), "price_change_percentage": "24h,7d"})
        rows = r.json() if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    topics = []
    day = now.date().isoformat()
    for row in rows if isinstance(rows, list) else []:
        cid, name, sym = row.get("id"), row.get("name"), str(row.get("symbol", "")).upper()
        price, ch24, ch7 = row.get("current_price"), row.get("price_change_percentage_24h_in_currency"), row.get("price_change_percentage_7d_in_currency")
        if price is None or ch24 is None:
            continue
        big = abs(ch24) >= (2.0 if cid in ("bitcoin", "ethereum") else 4.0)
        if not big and cid != "bitcoin":
            continue
        direction = "up" if ch24 >= 0 else "down"
        topic = f"{name} is {direction} {abs(ch24):.1f}% in 24 hours" if big else f"Bitcoin market update: {_money(price, cents=False)}"
        facts = [{"text": f"{name} ({sym}) price: {_money(price, cents=False)} (CoinGecko, {now:%b %d %H:%M} UTC).", "source": "CoinGecko"},
                 {"text": f"24-hour change: {ch24:+.2f}%.", "source": "CoinGecko"}]
        if ch7 is not None:
            facts.append({"text": f"7-day change: {ch7:+.2f}%.", "source": "CoinGecko"})
        if row.get("market_cap_rank"):
            facts.append({"text": f"Market cap rank: #{row['market_cap_rank']}.", "source": "CoinGecko"})
        if row.get("total_volume"):
            facts.append({"text": f"24-hour trading volume: {_money(row['total_volume'], cents=False)}.", "source": "CoinGecko"})
        topics.append(Topic("crypto_market", topic, _key("market", cid, day, "big" if big else "daily"), (1.0 + min(abs(ch24), 15) / 10) * (1.2 if big else 0.7), {
            "headline": topic, "facts": facts, "coin": {"id": cid, "name": name, "symbol": sym},
            "numbers": {"price_usd": price, "change_24h_pct": round(ch24, 2), **({"change_7d_pct": round(ch7, 2)} if ch7 is not None else {}),
                        **({"market_cap_rank": row["market_cap_rank"]} if row.get("market_cap_rank") else {})},
            "images": [], "sources": [{"name": "CoinGecko", "url": f"https://www.coingecko.com/en/coins/{cid}"}]}))
    return topics


async def research_chart(client: httpx.AsyncClient, brief: dict[str, Any]) -> None:
    """A 7-day price chart for a market brief (CoinGecko), drawn by the worker. Failure leaves the brief chartless."""
    coin = (brief.get("coin") or {}).get("id")
    if not coin:
        return
    try:
        r = await client.get(f"{COINGECKO}/coins/{coin}/market_chart", headers=UA, params={"vs_currency": "usd", "days": "7"})
        pts = (r.json() or {}).get("prices") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        pts = None
    if pts:
        step = max(1, len(pts) // 84)
        brief["chart"] = {"label": f"{brief['coin']['symbol']} · 7 days (USD)", "points": [[int(p[0]), float(p[1])] for p in pts[::step]],
                          "source": "CoinGecko"}


# --- Pokémon (Price Chat's own market data) -----------------------------------------------------------------------------

MAX_MOVE_PCT = 150.0  # a vintage card "up 36,915%" is a data glitch, not news
MIN_PRICE_CENTS = 5_000  # $50: smaller moves on cheap cards are noise
MIN_SALES_30D = 2  # the move must rest on at least two verified sales


def plausible_move(r: dict[str, Any]) -> bool:
    ch, price = r.get("change_30d"), r.get("market_price")
    return (ch is not None and price is not None and 5.0 <= abs(ch) <= MAX_MOVE_PCT and price >= MIN_PRICE_CENTS
            and int(r.get("sales_30d") or 0) >= MIN_SALES_30D)


def _card_chart(r: dict[str, Any], c: dict[str, Any]) -> dict[str, Any] | None:
    """The card's own 30-day price line (verified sales / provider valuations), only when real observations fall in it."""
    sp = r.get("sparkline") or {}
    pts = [[int(t), round(v / 100, 2)] for t, v in sp.get("points") or [] if v is not None]
    if sp.get("carried_only") or len(pts) < 3:
        return None
    return {"points": pts, "label": f"{c['name']} {r.get('label') or 'PSA 10'} · 30 days · Price Chat Pokémon Market"}


def pokemon(db: Session, now: datetime) -> list[Topic]:
    from app.models.pokemon import PokemonCard, PokemonSale
    from app.pokemon import market
    from app.pokemon.service import card_out

    topics: list[Topic] = []
    week = now.strftime("%G-W%V")
    for direction, limit in (("gainers", 6), ("losers", 3)):
        try:
            rows = market.movers(db, market.DEFAULT_GRADE, direction, "30d", 40, now)["rows"]
        except Exception:  # noqa: BLE001 - the market may have no data yet
            logger.exception("pokemon movers")
            rows = []
        picked = 0
        for r in rows:
            c, ch, price = r["card"], r.get("change_30d"), r.get("market_price")
            if ch is None or price is None or not plausible_move(r):
                continue  # a thin or glitchy price series: never a story
            if picked >= limit:
                break
            picked += 1
            up = ch > 0
            name = f"{c['name']} ({c['set_name']}, {c['year']})"
            facts = [{"text": f"{name} {r.get('label') or 'PSA 10'} market price: {_money(price)}.", "source": "Price Chat Pokémon Market"},
                     {"text": f"30-day change: {ch:+.1f}%, from verified sales.", "source": "Price Chat Pokémon Market"}]
            if r.get("population"):
                facts.append({"text": f"{r.get('label') or 'PSA 10'} population: {r['population']:,}.", "source": "PSA population report"})
            if r.get("last_sale"):
                facts.append({"text": f"Last verified sale: {_money(r['last_sale'])}.", "source": "Price Chat Pokémon Market"})
            if str(r.get("label") or "PSA 10").startswith("PSA 10"):
                facts.append({"text": "PSA 10 is PSA's highest grade, called Gem Mint.", "source": "PSA grading standards"})
            word = "up" if up else "down"
            topics.append(Topic("pokemon_market", f"{c['name']} {r.get('label') or 'PSA 10'} is {word} {abs(ch):.0f}% in 30 days",
                                _key("pkmove", c["id"], week), (0.9 if up else 0.85) + min(abs(ch), 80) / 80, {
                                    "headline": f"{c['name']} {word} {abs(ch):.1f}% in 30 days", "facts": facts, "card": c,
                                    "numbers": {"market_price_usd": round(price / 100, 2), "change_30d_pct": round(ch, 1),
                                                **({"population": r["population"]} if r.get("population") else {}),
                                                **({"last_sale_usd": round(r["last_sale"] / 100, 2)} if r.get("last_sale") else {})},
                                    "stat": {"value": abs(round(ch, 1)), "prefix": "+" if up else "-", "suffix": "%", "decimals": 1, "kicker": "30-DAY MOVE"},
                                    "chart": _card_chart(r, c),
                                    "images": [{"url": c["image_url"], "credit": "Card image"}] if c.get("image_url") else [],
                                    "sources": [{"name": "Price Chat Pokémon Market", "url": f"https://pricechat.live/pokemon/cards/{c['id']}"}]}))
    since = now - timedelta(days=7)
    sales = db.execute(select(PokemonSale, PokemonCard).join(PokemonCard, PokemonCard.id == PokemonSale.card_id)
                       .where(PokemonSale.verified.is_(True), PokemonSale.excluded.is_(False), PokemonSale.sale_date >= since,
                              PokemonSale.sale_price_usd_cents >= 100_000).order_by(PokemonSale.sale_price_usd_cents.desc()).limit(5)).all()
    for s, card in sales:
        c = card_out(card, light=True)
        grade = f"{s.grading_company} {s.grade}" if s.grading_company != "RAW" else f"raw {s.grade}"
        where = s.auction_house or s.source
        facts = [{"text": f"{c['name']} ({c['set_name']}, {c['year']}) {grade} sold for {_money(s.sale_price_usd_cents)} on {s.sale_date:%B %d, %Y}.",
                  "source": where},
                 {"text": f"Sale type: {s.sale_type or 'sale'} via {where}.", "source": where}]
        if s.grading_company == "PSA" and str(s.grade) == "10":
            facts.append({"text": "PSA 10 is PSA's highest grade, called Gem Mint.", "source": "PSA grading standards"})
        topics.append(Topic("pokemon_rare", f"A {c['name']} {grade} just sold for {_money(s.sale_price_usd_cents)}", _key("pksale", s.id),
                            0.8 + min(s.sale_price_usd_cents / 1_000_000, 2.0) / 2, {
                                "headline": f"{c['name']} {grade} sold for {_money(s.sale_price_usd_cents)}", "facts": facts, "card": c,
                                "numbers": {"sale_usd": round(s.sale_price_usd_cents / 100, 2), "year": c["year"]},
                                "images": [{"url": c["image_url"], "credit": "Card image"}] if c.get("image_url") else [],
                                "sources": [{"name": where, "url": s.url or f"https://pricechat.live/pokemon/cards/{c['id']}"}]}))
    return topics


# --- facts + Price Chat's own content -------------------------------------------------------------------------------------


def facts_topic(used_keys: set[str], now: datetime) -> list[Topic]:
    import random

    rng = random.Random(now.date().toordinal())
    pool = list(range(len(FACTS)))
    rng.shuffle(pool)
    pick = sorted(pool[:5])
    key = _key("facts", *pick)
    if key in used_keys:
        return []
    chosen = [FACTS[i] for i in pick]
    return [Topic("facts", "5 crypto & collectibles facts most people don't know", key, 0.8, {
        "headline": "5 facts most people don't know", "facts": [{"text": f["fact"], "source": f["source"]} for f in chosen],
        "numbers": {}, "images": [], "sources": [{"name": f["source"], "url": f["url"]} for f in chosen]})]


def pricechat_content(db: Session, now: datetime) -> list[Topic]:
    from app.models.content_items import ContentItem

    rows = db.scalars(select(ContentItem).where(ContentItem.available.is_(True), ContentItem.published_at >= now - timedelta(days=30),
                                                ContentItem.url != "").order_by(ContentItem.published_at.desc()).limit(10))
    out = []
    for it in rows:
        if not it.title:
            continue
        facts = [{"text": f"Price Chat video: “{it.title}”.", "source": "Price Chat on YouTube"}]
        if it.description:
            facts.append({"text": _clean(it.description, 400), "source": "Price Chat on YouTube"})
        views = (it.metrics or {}).get("views")
        out.append(Topic("pricechat_highlights", f"From our channel: {it.title[:120]}", _key("yt", it.external_id), 0.7 + (0.3 if views and views > 1000 else 0), {
            "headline": it.title[:200], "facts": facts, "numbers": {**({"views": views} if views else {})},
            "images": [{"url": it.thumbnail_url, "credit": "Price Chat (own video)"}] if it.thumbnail_url else [],
            "sources": [{"name": "Price Chat on YouTube", "url": it.url}]}))
    return out


# --- ranking ---------------------------------------------------------------------------------------------------------------


def recent_videos(db: Session, brand_id: int, days: int, now: datetime) -> list[BmVideo]:
    return list(db.scalars(select(BmVideo).where(BmVideo.brand_id == brand_id, BmVideo.created_at >= now - timedelta(days=days))))


def rank(topics: list[Topic], strategy: dict[str, Any], recent: list[BmVideo], perf: dict[str, float] | None = None) -> list[Topic]:
    allowed = set(strategy.get("categories") or [])
    weights = strategy.get("category_weights") or {}
    seen_keys = {v.topic_key for v in recent}
    seen_words = [_words(v.topic) for v in recent]
    banned = [b.lower() for b in strategy.get("banned_topics") or []]
    out = []
    for t in topics:
        if t.category not in allowed or t.key in seen_keys:
            continue
        if any(b in t.topic.lower() for b in banned):
            continue
        w = _words(t.topic)
        if any(w and len(w & s) / len(w | s) > 0.6 for s in seen_words):
            continue  # too close to something we already made
        evidence = min(1.0, 0.6 + 0.2 * len(t.brief.get("sources") or []))
        t.score = round(t.score * float(weights.get(t.category, 1.0)) * evidence * (perf or {}).get(t.category, 1.0), 4)
        out.append(t)
    out.sort(key=lambda t: -t.score)
    return out


async def discover(db: Session, brand_id: int, strategy: dict[str, Any], perf: dict[str, float] | None = None, transport: Any = None,
                   now: datetime | None = None, trend_mult: float | None = None) -> list[Topic]:
    from app.brand_manager import trends

    now = now or utcnow()
    cats = set(strategy.get("categories") or [])
    topics: list[Topic] = []
    async with httpx.AsyncClient(timeout=20.0, transport=transport, follow_redirects=True) as client:
        if "crypto_news" in cats:
            topics += await crypto_news(client, now)
        if "crypto_market" in cats:
            topics += await crypto_market(client, now)
            topics += await sources.coingecko_trending(client, now)
        if "crypto_derivatives" in cats:
            from app.tradingos import market as hl

            topics += await asyncio.to_thread(sources.hyperliquid, db, hl.data(), now)
        if cats & {"pokemon_market", "pokemon_rare"}:
            topics += pokemon(db, now)
            topics += sources.top100_entries(db, now)
        recent = recent_videos(db, brand_id, int(strategy.get("duplicate_days", 14)), now)
        if "facts" in cats:
            topics += facts_topic({v.topic_key for v in recent_videos(db, brand_id, 60, now)}, now)
        if "pricechat_highlights" in cats:
            topics += pricechat_content(db, now)
            topics += sources.community_week(db, now)
        live = trends.current(db, now)  # the latest trend scan (job brand.trends); empty until it has run
        if "finance_trends" in cats:
            topics += trends.topics(live, now)
        trends.annotate(topics, live, trend_mult)
        ranked = rank(topics, strategy, recent, perf)
        for t in ranked[:6]:
            if t.category == "crypto_market":
                await research_chart(client, t.brief)
            if t.category in ("crypto_news", "crypto_market", "crypto_derivatives"):  # the chart the story's words call for
                from app.brand_manager import marketviz
                from app.tradingos import market as hl

                await asyncio.to_thread(marketviz.attach, t.brief, hl.data(), db, t.brief.get("market_visual_hint"))
    return ranked
