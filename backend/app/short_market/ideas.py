"""Story ideas for Short Market: found in sources Price Chat already reads, each with the brief (facts, numbers, sources)
the story must stick to. Nothing here is generated: every fact has a source.

Sources: crypto and finance news feeds (headlines + summaries, linked, never copied in full), CoinGecko (movers, the
whole market, what people search for), the Crypto Fear & Greed Index, Wikipedia's "on this day" (money history),
the hand-checked fact bank, Price Chat's Pokémon market, and posts already collected from X. No API keys, no sign-in.
A source that is down is skipped; it never stops the others.
"""

import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand_manager import discovery
from app.brand_manager.defaults import FACTS
from app.brand_manager.discovery import COINGECKO, UA, _clean, _key, _money, _words
from app.db.base import utcnow
from app.models.short_market import SmIdea, SmShort
from app.short_market.defaults import BANNED, CATEGORIES, FINANCE_FEEDS, IDEA_KEEP_DAYS, IDEA_TTL_MINUTES

logger = logging.getLogger(__name__)
FEAR_GREED = "https://api.alternative.me/fng/"
ON_THIS_DAY = "https://en.wikipedia.org/api/rest_v1/feed/onthisday/events"
TRENDING_MAX_RANK = 300
MONEY = re.compile(r"\b(banks?|banking|stock market|stock exchange|wall street|dollars?|currency|currencies|gold standard|gold rush|financial|"
                   r"econom(?:y|ic)|treasury|inflation|market crash|recession|depression|bankrupt\w*|federal reserve|euro|pound sterling|"
                   r"bitcoin|cryptocurrency|coins?|mint|tax(?:es)?|debt|billion|million|shares|investors?)\b", re.I)


@dataclass
class Idea:
    category: str
    topic: str
    key: str
    score: float
    brief: dict[str, Any] = field(default_factory=dict)


def _big(v: float) -> str:
    """$3.41 trillion / $182.5 billion: the way a person says it."""
    for size, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if abs(v) >= size:
            return f"${v / size:,.2f} {word}"
    return _money(v, cents=False)


# --- news ----------------------------------------------------------------------------------------------------------------


async def news(client: httpx.AsyncClient, now: datetime) -> list[Idea]:
    out = [Idea("news", t.topic, t.key, t.score, t.brief) for t in await discovery.crypto_news(client, now)]
    for name, url in FINANCE_FEEDS:
        try:
            r = await client.get(url, headers=UA)
            items = discovery.parse_feed(r.text, name) if r.status_code == 200 else []
        except httpx.HTTPError:
            items = []
        for it in items[:12]:
            if it["at"] is None or now - it["at"] > timedelta(hours=24) or not it["summary"]:
                continue  # a bare headline is not enough to tell a story from
            hours = max(0.0, (now - it["at"]).total_seconds() / 3600)
            facts = [{"text": it["title"], "source": name}]
            if it["summary"].lower() != it["title"].lower():
                facts.append({"text": it["summary"], "source": name})
            out.append(Idea("news", it["title"], _key("news", it["link"]), max(0.2, 1 - hours / 24), {
                "headline": it["title"], "facts": facts, "numbers": {}, "images": [],
                "sources": [{"name": name, "url": it["link"], "published": it["at"].isoformat()}],
                "note": "Report only what the headline and summary say; attribute it to the source."}))
    return out


# --- markets -------------------------------------------------------------------------------------------------------------


async def _json(client: httpx.AsyncClient, url: str, **params: Any) -> Any:
    try:
        r = await client.get(url, headers=UA, params=params or None)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def mood(client: httpx.AsyncClient, now: datetime) -> list[Idea]:
    """The whole crypto market in one story: its size, its 24-hour move, Bitcoin's share and the Fear & Greed Index."""
    g = ((await _json(client, f"{COINGECKO}/global")) or {}).get("data") or {}
    fg = ((await _json(client, FEAR_GREED, limit=2)) or {}).get("data") or []
    cap, change = (g.get("total_market_cap") or {}).get("usd"), g.get("market_cap_change_percentage_24h_usd")
    if not cap or change is None or not fg:
        return []
    try:
        today, label = int(fg[0]["value"]), str(fg[0]["value_classification"])
        before = (int(fg[1]["value"]), str(fg[1]["value_classification"])) if len(fg) > 1 else None
    except (KeyError, TypeError, ValueError):
        return []
    btc = (g.get("market_cap_percentage") or {}).get("btc")
    facts = [{"text": f"The whole crypto market is worth {_big(cap)} ({now:%b %d} UTC).", "source": "CoinGecko"},
             {"text": f"In the last 24 hours its value changed by {change:+.2f}%.", "source": "CoinGecko"},
             {"text": f"The Crypto Fear & Greed Index reads {today} out of 100 today: {label}.", "source": "alternative.me"}]
    numbers: dict[str, Any] = {"total_market_cap_usd": round(cap), "change_24h_pct": round(change, 2), "fear_greed": today, "scale": 100}
    if before:
        facts.append({"text": f"Yesterday the index read {before[0]}: {before[1]}.", "source": "alternative.me"})
        numbers["fear_greed_yesterday"] = before[0]
    if btc:
        facts.append({"text": f"Bitcoin alone makes up {btc:.1f}% of the market.", "source": "CoinGecko"})
        numbers["btc_dominance_pct"] = round(btc, 1)
    facts.append({"text": "The index runs from 0, extreme fear, to 100, extreme greed.", "source": "alternative.me"})
    swing = abs(today - before[0]) if before else 0
    return [Idea("markets", f"Crypto market mood today: {label} ({today}/100)", _key("mood", now.date().isoformat()),
                 1.0 + min(abs(change), 8) / 10 + min(swing, 20) / 40, {
                     "headline": f"The crypto market's mood today is {label}: {today} out of 100", "facts": facts, "numbers": numbers, "images": [],
                     "sources": [{"name": "CoinGecko", "url": "https://www.coingecko.com/en/global-charts"},
                                 {"name": "Crypto Fear & Greed Index (alternative.me)", "url": "https://alternative.me/crypto/fear-and-greed-index/"}]})]


async def trending(client: httpx.AsyncClient, now: datetime) -> list[Idea]:
    """What people search for on CoinGecko right now."""
    coins = ((await _json(client, f"{COINGECKO}/search/trending")) or {}).get("coins") or []
    out = []
    for place, c in enumerate(coins[:3], start=1):
        it = c.get("item") or {}
        name, sym, cid = it.get("name"), str(it.get("symbol") or "").upper(), it.get("id")
        ch = (((it.get("data") or {}).get("price_change_percentage_24h")) or {}).get("usd")
        if not name or not cid or ch is None or not it.get("market_cap_rank") or it["market_cap_rank"] > TRENDING_MAX_RANK:
            continue  # day-old coins nobody has heard of trend too: they are not a story
        nth = {1: "the most-searched", 2: "the second most-searched", 3: "the third most-searched"}[place]
        facts = [{"text": f"{name} ({sym}) is {nth} coin on CoinGecko right now ({now:%b %d} UTC).", "source": "CoinGecko trending searches"},
                 {"text": f"Its price changed {ch:+.1f}% in the last 24 hours.", "source": "CoinGecko"}]
        numbers: dict[str, Any] = {"change_24h_pct": round(ch, 1), "market_cap_rank": it["market_cap_rank"]}
        facts.append({"text": f"By market value it ranks number {it['market_cap_rank']} among all coins.", "source": "CoinGecko"})
        facts.append({"text": "Trending means people are searching for it, not that its price will move.", "source": "CoinGecko"})
        out.append(Idea("markets", f"Everyone is searching for {name} right now", _key("trend", cid, now.date().isoformat()),
                        1.05 - place * 0.1 + min(abs(ch), 30) / 60, {
                            "headline": f"{name} is {nth} coin on CoinGecko", "facts": facts, "numbers": numbers, "images": [],
                            "coin": {"id": cid, "name": name, "symbol": sym},
                            "sources": [{"name": "CoinGecko", "url": f"https://www.coingecko.com/en/coins/{cid}"}]}))
    return out


# --- history, facts ------------------------------------------------------------------------------------------------------


async def history(client: httpx.AsyncClient, now: datetime) -> list[Idea]:
    """Money history that happened on today's date (Wikipedia's "on this day")."""
    data = await _json(client, f"{ON_THIS_DAY}/{now.month:02d}/{now.day:02d}")
    out = []
    for ev in (data or {}).get("events") or []:
        text, year = _clean(str(ev.get("text") or ""), 400), ev.get("year")
        if not text or not isinstance(year, int) or year < 1500 or not MONEY.search(text):
            continue
        page = next((p for p in ev.get("pages") or [] if p.get("extract")), None)
        facts = [{"text": f"On {now:%B} {now.day}, {year}: {text}", "source": "Wikipedia, on this day"}]
        sources = [{"name": "Wikipedia: on this day", "url": f"https://en.wikipedia.org/wiki/{now:%B}_{now.day}"}]
        if page:
            first = re.split(r"(?<=[.!?])\s+", _clean(str(page["extract"]), 600))
            facts.append({"text": " ".join(first[:2])[:420], "source": f"Wikipedia: {page.get('normalizedtitle') or page.get('title')}"})
            url = (((page.get("content_urls") or {}).get("desktop")) or {}).get("page")
            if url:
                sources.append({"name": f"Wikipedia: {page.get('normalizedtitle') or page.get('title')}", "url": url})
        out.append(Idea("history", f"On this day in {year}: {text[:200]}", _key("otd", now.month, now.day, year, text[:60]),
                        0.85 + (0.15 if page else 0) + min(len(MONEY.findall(text)), 3) * 0.05, {
                            "headline": f"{now:%B} {now.day}, {year}: {text[:240]}", "facts": facts, "numbers": {"year": year, "day": now.day},
                            "images": [], "sources": sources, "note": "Tell it as a story from that day. Add no detail the facts don't give."}))
    out.sort(key=lambda i: -i.score)
    return out[:5]


def fact_stories(now: datetime) -> list[Idea]:
    """Three hand-checked facts linked into one story; two different sets a day."""
    out = []
    for n in range(2):
        rng = random.Random(now.date().toordinal() * 7 + n)
        pick = sorted(rng.sample(range(len(FACTS)), 3))
        chosen = [FACTS[i] for i in pick]
        out.append(Idea("facts", f"3 money facts: {chosen[0]['fact'][:110]}", _key("facts3", *pick), 0.75 - n * 0.05, {
            "headline": "Three facts most people don't know", "facts": [{"text": f["fact"], "source": f["source"]} for f in chosen],
            "numbers": {"facts": 3}, "images": [], "sources": [{"name": f["source"], "url": f["url"]} for f in chosen]}))
    return out


# --- X (posts already collected by the X connector; nothing is fetched here) --------------------------------------------


def x_buzz(db: Session, now: datetime) -> list[Idea]:
    from app.models.enums import Platform
    from app.models.social import SocialPost

    rows = db.scalars(select(SocialPost).where(SocialPost.platform == Platform.X, SocialPost.is_own.is_(False),
                                               SocialPost.posted_at >= now - timedelta(hours=48)).order_by(SocialPost.posted_at.desc()).limit(300))
    scored = []
    for p in rows:
        m = p.metrics or {}
        likes, reposts = int(m.get("likes") or 0), int(m.get("reposts") or 0)
        text = _clean(re.sub(r"https?://\S+", "", p.text or ""), 400)
        if likes < 100 or len(text) < 60 or not p.author_handle or (p.content_type or "post") == "reply":
            continue
        scored.append((likes + 3 * reposts, likes, reposts, text, p))
    out = []
    for engagement, likes, reposts, text, p in sorted(scored, key=lambda s: -s[0])[:4]:
        handle = p.author_handle.lstrip("@")
        facts = [{"text": f"@{handle} posted on X: “{text}”", "source": f"@{handle} on X"},
                 {"text": f"The post has {likes:,} likes" + (f" and {reposts:,} reposts." if reposts else "."), "source": "X"}]
        out.append(Idea("buzz", f"@{handle} on X: {text[:150]}", _key("xpost", p.external_id or p.id), 0.7 + min(engagement, 20_000) / 40_000, {
            "headline": f"A post by @{handle} is getting attention on X", "facts": facts,
            "numbers": {"likes": likes, **({"reposts": reposts} if reposts else {})}, "images": [],
            "sources": [{"name": f"@{handle} on X", "url": p.url or f"https://x.com/{handle}"}],
            "note": f"This is what one account said, not a checked fact: say 'according to a post by @{handle}'. Paraphrase it."}))
    return out


# --- gathering, ranking, storing -------------------------------------------------------------------------------------------


async def gather(db: Session, transport: Any = None, now: datetime | None = None) -> list[Idea]:
    now = now or utcnow()
    found: list[Idea] = []
    async with httpx.AsyncClient(timeout=20.0, transport=transport, follow_redirects=True) as client:
        for source in (news, mood, trending, history):
            try:
                found += await source(client, now)
            except Exception:  # noqa: BLE001 - one source being down must not hide the others
                logger.exception("short market source %s", source.__name__)
        try:
            found += [Idea("markets", t.topic, t.key, t.score, t.brief) for t in await discovery.crypto_market(client, now)]
        except Exception:  # noqa: BLE001
            logger.exception("short market source crypto_market")
    for name, fn in (("pokemon", lambda: [Idea("collectibles", t.topic, t.key, t.score, t.brief) for t in discovery.pokemon(db, now)]),
                     ("facts", lambda: fact_stories(now)), ("x", lambda: x_buzz(db, now))):
        try:
            found += fn()
        except Exception:  # noqa: BLE001
            logger.exception("short market source %s", name)
    out: dict[str, Idea] = {}
    for i in found:
        low = i.topic.lower()
        if any(b in low for b in BANNED) or len(i.brief.get("facts") or []) < 2:
            continue  # one line is a headline, not a story
        i.score = round(i.score * float(CATEGORIES[i.category]["weight"]), 4)
        if i.key not in out or out[i.key].score < i.score:
            out[i.key] = i
    return sorted(out.values(), key=lambda i: -i.score)


def store(db: Session, found: list[Idea], now: datetime | None = None) -> int:
    now = now or utcnow()
    have = {r.key: r for r in db.scalars(select(SmIdea))}
    for i in found:
        row = have.get(i.key)
        if row is None:
            db.add(SmIdea(key=i.key, category=i.category, topic=i.topic[:300], score=i.score, brief=i.brief, found_at=now))
        else:
            row.category, row.topic, row.score, row.brief, row.found_at = i.category, i.topic[:300], i.score, i.brief, now
    for row in have.values():  # stories no source shows any more
        if now - row.found_at > timedelta(days=IDEA_KEEP_DAYS):
            db.delete(row)
    db.commit()
    return len(found)


def stale(db: Session, now: datetime | None = None) -> bool:
    newest = db.scalar(select(SmIdea.found_at).order_by(SmIdea.found_at.desc()).limit(1))
    return newest is None or (now or utcnow()) - newest > timedelta(minutes=IDEA_TTL_MINUTES)


async def refresh(db: Session, transport: Any = None, now: datetime | None = None) -> int:
    return store(db, await gather(db, transport, now), now)


def listing(db: Session, category: str | None = None, limit: int = 60, now: datetime | None = None) -> list[dict[str, Any]]:
    """Fresh ideas, best first; stories already made into a short sink to the bottom, and so do near-copies of them."""
    now = now or utcnow()
    made = list(db.scalars(select(SmShort).where(SmShort.status != "failed")))
    made_keys = {s.idea_key: s.id for s in made}
    made_words = [_words(s.topic) for s in made]
    q = select(SmIdea).where(SmIdea.found_at >= now - timedelta(hours=26))
    if category:
        q = q.where(SmIdea.category == category)
    rows = []
    for r in db.scalars(q):
        w = _words(r.topic)
        similar = any(w and len(w & s) / len(w | s) > 0.6 for s in made_words)
        rows.append((r.key in made_keys or similar, -r.score, r))
    rows.sort(key=lambda t: (t[0], t[1]))
    return [idea_out(r, made_keys.get(r.key), used) for used, _, r in rows[:limit]]


def idea_out(r: SmIdea, short_id: int | None = None, used: bool = False) -> dict[str, Any]:
    b = r.brief or {}
    return {"id": r.id, "key": r.key, "category": r.category, "category_label": CATEGORIES.get(r.category, {}).get("label", r.category),
            "topic": r.topic, "headline": b.get("headline") or r.topic, "score": r.score, "found_at": r.found_at.isoformat(),
            "facts": [{"text": str(f.get("text")), "source": str(f.get("source") or "")} for f in b.get("facts") or [] if isinstance(f, dict)][:6],
            "sources": b.get("sources") or [], "used": used, "short_id": short_id}
