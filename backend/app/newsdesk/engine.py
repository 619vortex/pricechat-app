"""One tick of the pipeline, every few minutes, from the scheduler. Everything is in the database, so a restart loses nothing.

Publishing: a post is claimed by one conditional UPDATE before X is called, so two runs can't both send it. A timeout may have reached X, so it's
marked uncertain and never retried on its own. Rate limits and server errors retry with a wait. Refused credentials or no X credit pause the desk.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.x import XRuntime
from app.connectors.x.errors import XAuthError, XCreditsError, XError, XForbiddenError, XRateLimitError, XUnavailableError
from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdPost, NdSettings, NdStory
from app.newsdesk import brief, chaos, checks, engagement, events, incidents, livedata, moves, sources, writer

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
LIVE = ("published", "publishing", "uncertain")
COUNTED = LIVE + ("dry_run",)  # what counts as sent, in the matching mode
DEDUPE_HOURS = 72
SIMILAR = 0.5
SENSITIVITY = {"low": {"move": 1.4, "breaking": 7.0}, "medium": {"move": 1.0, "breaking": 6.0}, "high": {"move": 0.75, "breaking": 5.0}}
COOLDOWN = {"move": timedelta(hours=6), "symbol": timedelta(hours=2), "extreme": timedelta(hours=24), "milestone": timedelta(days=5), "selloff": timedelta(hours=12), "liq": timedelta(hours=3), "snapshot": timedelta(hours=8)}
KEYWORDS: dict[str, dict[str, float]] = {
    "economics": {"interest rate": 2.5, "rate cut": 2.5, "rate hike": 2.5, "rates": 1.5, "fomc": 2.5, "inflation": 2.0, "cpi": 2.0, "jobs report": 2.0, "unemployment": 1.5, "payrolls": 2.0, "gdp": 1.5, "recession": 2.0, "central bank": 1.5, "powell": 1.5, "lagarde": 1.0, "monetary policy": 2.0, "treasury": 2.0, "bond": 1.5, "yields": 1.5},
    "crypto": {"bitcoin": 1.5, "ethereum": 1.5, "crypto": 1.0, "stablecoin": 1.0, "etf": 1.0, "hack": 2.0, "exploit": 2.0, "liquidat": 1.5, "binance": 1.0, "coinbase": 1.0, "solana": 1.0, "bankrupt": 2.0},
    "stocks": {"earnings": 1.5, "nvidia": 1.5, "tesla": 1.5, "apple": 1.0, "s&p 500": 1.5, "nasdaq": 1.5, "dow": 1.0, "wall street": 1.0, "stocks": 1.0, "shares": 1.0, "ipo": 1.0, "merger": 1.0, "takeover": 1.0, "layoffs": 1.0},
    "commodities": {"gold": 1.5, "silver": 1.5, "oil": 1.5, "brent": 1.5, "opec": 2.0, "copper": 1.5, "natural gas": 1.0, "crude": 1.5},
    "politics": {"tariff": 2.5, "sanction": 2.5, "trade deal": 2.0, "trade war": 2.0, "election": 1.5, "ceasefire": 2.0, "invasion": 2.5, "executive order": 2.0, "shutdown": 2.0, "summit": 1.0, "missile": 1.5,
                  "iran": 1.5, "israel": 1.5, "russia": 1.5, "ukrain": 1.5, "houthi": 2.0, "airstrike": 2.0, "air strike": 2.0, "nuclear": 1.5, "peace talks": 2.0,
                  "peace deal": 2.0, "attack on": 1.0, "impeach": 1.5, "troops": 1.5},
    "viral": {"record high": 2.0, "all-time high": 2.0, "biggest ever": 2.0, "billionaire": 1.0, "record": 1.0, "collapse": 1.5},
}


# --- settings ------------------------------------------------------------------------------------------------------


X_FORMAT = "visual"  # owner, 2026-11-09: X posts carry the same picture as Telegram, the headline and the source. "classic" = text only


def settings_row(db: Session) -> NdSettings:
    row = db.get(NdSettings, 1)
    if row is None:
        row = NdSettings(id=1, categories={c: True for c in sources.CATEGORIES}, feeds=sources.DEFAULT_FEEDS, watchlist=sources.DEFAULT_WATCHLIST, state={})
        db.add(row)
        db.flush()
    return row


STOPPED = "stopped"


def emergency_stop(db: Session) -> str:
    """Why nothing automated may be published right now ("" = it may): the News Desk's emergency stop (Admin, Terminal /stop) or the
    network's emergency pause. Checked by every sending path on both platforms, at the moment of sending (owner, 2026-11-21: the
    Terminal stop held X back but Telegram kept posting, and even took over the desk's decisions because "the desk isn't running")."""
    from app.newsdesk import network

    s = db.get(NdSettings, 1)
    if s is not None and s.kill_switch:
        return "Emergency stop is on"
    if s is not None and (s.pause_reason or "") == network.EMERGENCY:
        return network.EMERGENCY
    return ""


def blocked_reason(s: NdSettings, now: datetime) -> str | None:
    if s.kill_switch:
        return "Emergency stop is on"
    if not s.automation:
        return "Automation is off"
    until = s.paused_until
    if until is not None and (until if until.tzinfo else until.replace(tzinfo=now.tzinfo)) > now:
        return s.pause_reason or f"Paused until {until:%d %b %H:%M} UTC"
    return None


# --- discover, score ----------------------------------------------------------------------------------------------


def classify(title: str, summary: str, default: str | None) -> tuple[str | None, float]:
    """(category, keyword strength). No market-relevant word: not for this account, whichever feed it came from. A tie goes to the feed's own category."""
    text = f"{title} {summary}".lower()
    best, strength = None, 0.0
    for cat, words in KEYWORDS.items():
        hit = sum(w for k, w in words.items() if re.search(rf"\b{re.escape(k)}", text))
        if hit > strength or (hit == strength and hit > 0 and cat == default):
            best, strength = cat, hit
    return best or None, min(strength, 4.0) if best else 0.0


def score_story(tier: int, strength: float, others: int, age_hours: float) -> float:
    return round({0: 3.0, 1: 2.0}.get(tier, 1.0) + strength + min(others, 2) - min(age_hours / 12, 2.0), 2)


# A feed failing read after read rests (2026-11-24: The Guardian's three feeds failed every tick for a day, one log line each tick): after
# FEED_REST_AFTER failures in a row it is read again only after FEED_REST_FIRST, doubling up to FEED_REST_MAX, so a recovery is still seen
# within the hour. Admin keeps showing the problem; the owner hears once, from the watchdog (app/observability.dead_feeds).
FEED_REST_AFTER = 3
FEED_REST_FIRST = timedelta(minutes=10)
FEED_REST_MAX = timedelta(hours=1)


def feed_resting(prev: dict[str, Any], now: datetime) -> bool:
    """Skip this feed this tick? Only after FEED_REST_AFTER failures in a row, and only until its rest is over."""
    fails, checked = int(prev.get("fails") or 0), prev.get("checked")
    if fails < FEED_REST_AFTER or not checked:
        return False
    try:
        last = datetime.fromisoformat(checked)
    except (TypeError, ValueError):
        return False
    last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    return now - last < min(FEED_REST_MAX, FEED_REST_FIRST * 2 ** (fails - FEED_REST_AFTER))


async def collect(db: Session, s: NdSettings, client: httpx.AsyncClient, now: datetime) -> dict[str, Any]:
    """New stories from every feed. Each is stored on its own: a link already in the table (a feed recycling a URL, such as a
    daily round-up, or a story stored a week ago) is skipped, never allowed to roll back everyone else's (2026-11-08 outage)."""
    fresh, problems = 0, {}
    incident_stored: list[str] = []
    db.query(NdStory).filter(NdStory.created_at < now - timedelta(days=7)).delete(synchronize_session=False)
    db.commit()  # the purge on its own, before any insert
    seen = set(db.scalars(select(NdStory.link)))  # every stored link (the table keeps a week), not just the last few days
    health: dict[str, Any] = {}
    before_health = ((s.state or {}).get("feed_health") or {}).get("feeds") or {}  # the last tick's, for failure streaks
    feeds = sources.feeds_for(s.feeds or [], s.state or {})
    gate = asyncio.Semaphore(8)  # fetched in parallel (35+ feeds; one slow site never holds up the tick), stored in list order below

    async def fetch(feed: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None] | None:
        if feed_resting(before_health.get(feed["url"]) or {}, now):
            return None
        async with gate:
            return await sources.fetch_feed(client, feed)

    fetched = await asyncio.gather(*(fetch(f) for f in feeds))
    started, resting, recovered = [], [], []  # logged once each, not every tick
    for feed, got in zip(feeds, fetched):
        prev = before_health.get(feed["url"]) or {}
        if got is None:  # resting (feed_resting): its last answer stands, unread
            health[feed["url"]] = {**prev, "name": feed["name"], "tier": feed.get("tier", 2), "via": feed.get("via"), "new": 0, "resting": True}
            problems[feed["url"]] = prev.get("problem") or "failing"
            continue
        items, problem = got
        newest = max((it["at"] for it in items if it.get("at")), default=None)
        if not problem and newest is not None and now - (newest if newest.tzinfo else newest.replace(tzinfo=now.tzinfo)) > sources.STALE_AFTER:
            problem = f"stale: newest item {newest:%d %b %Y}"
        elif not problem and not items:
            problem = "no items"
        if problem:
            problems[feed["url"]] = problem
        before = fresh
        fails = int(prev.get("fails") or 0) + 1 if problem else 0
        health[feed["url"]] = {"name": feed["name"], "tier": feed.get("tier", 2), "items": len(items), "newest": newest.isoformat() if newest else None, "problem": problem,
                               "via": feed.get("via"), "fails": fails, "checked": now.isoformat(),
                               "last_ok": now.isoformat() if not problem else prev.get("last_ok"),
                               "failing_since": (prev.get("failing_since") or now.isoformat()) if problem else None}
        label = f"{feed['name']}: {problem}"
        if problem and fails == 1:
            started.append(label)
        elif problem and fails == FEED_REST_AFTER:
            resting.append(label)
        elif not problem and int(prev.get("fails") or 0) >= FEED_REST_AFTER:
            recovered.append(feed["name"])
        for it in sources.recent(items, now):
            if it["link"] in seen:
                continue
            seen.add(it["link"])
            title = sources.clean_title(it["title"])
            if checks.NOT_NEWS.search(title) or checks.is_explainer(title):
                continue
            category, strength = classify(title, it.get("summary", ""), feed.get("category"))
            inc = incidents.detect(title)
            if category is None and inc is not None:
                # owner, 2026-11-17: the Microsoft 365 outage, the FAA's nationwide ground stop and JPMorgan Chase's outage were dropped
                # right here (no market keyword), so neither lane ever saw them. A major infrastructure incident is stored on its own merit.
                category, strength = incidents.desk_category(inc), (2.5 if inc.major else 1.0)
            if category is None:
                # owner, 2026-11-18 replay: "Mines hit five ships in the Strait of Hormuz, shipping officials say" was dropped here (no
                # market keyword), so the event had one outlet and never broke. A high-impact event (app/terminal/intel) is stored too.
                from app.terminal import intel

                kind, _ = intel.impact_class(title, it.get("summary", ""), [], {})
                if kind is not None:
                    category, strength = ("stocks" if kind in ("markets", "corporate") else "crypto" if kind == "hack" else "politics"), 2.5
            if category is None:
                continue
            if inc is not None:
                incident_stored.append(f"{feed['name']}: [{inc.why()}] {title[:120]}")
            try:
                with db.begin_nested():  # one story's conflict (e.g. the other lane stored it a moment ago) skips that story only
                    db.add(NdStory(link=it["link"], title=title[:300], summary=(it.get("summary") or "")[:800], source=feed["name"],
                                   tier=int(feed.get("tier", 2)), category=category, published_at=it["at"], fingerprint=checks.fingerprint(title),
                                   score=strength))
                fresh += 1
            except IntegrityError:
                logger.info("newsdesk: %s already stored, skipped", it["link"][:120])
        health[feed["url"]]["new"] = fresh - before
    db.commit()
    for line in incident_stored[:10]:  # production evidence: when an incident story first reached the desk (Admin › Trending shows the rest)
        logger.info("newsdesk: incident story stored: %s", line)
    if started:
        logger.info("newsdesk: feed problems: %s", "; ".join(started))
    if resting:
        logger.info("newsdesk: feeds failing %d reads in a row, now read at most every %d-%d min until they answer: %s", FEED_REST_AFTER,
                    FEED_REST_FIRST.seconds // 60, FEED_REST_MAX.seconds // 60, "; ".join(resting))
    if recovered:
        logger.info("newsdesk: feeds answering again: %s", ", ".join(recovered))
    return {"new_stories": fresh, "feed_problems": problems, "feed_health": health}


CORROBORATION_WINDOW = timedelta(hours=48)  # two tellings further apart than this are not one event


def corroboration(story: NdStory, recent_rows: list[NdStory]) -> list[NdStory]:
    """Other outlets carrying the same story: most of the words in common, and nothing that says it is another event (a different subject,
    place or figure: checks.same_event), reported within two days of each other."""
    def near_in_time(o: NdStory) -> bool:
        a, b = story.published_at, o.published_at
        if a is None or b is None:
            return True
        a, b = (a if a.tzinfo else a.replace(tzinfo=timezone.utc)), (b if b.tzinfo else b.replace(tzinfo=timezone.utc))
        return abs(a - b) <= CORROBORATION_WINDOW

    def worded_alike(o: NdStory) -> bool:  # most words in common, or one asset through one price level the same way (checks.same_event)
        return checks.similarity(o.fingerprint, story.fingerprint) >= 0.34 or bool(checks.price_levels(o.title) & checks.price_levels(story.title))

    return [o for o in recent_rows if o.id != story.id and o.source != story.source and worded_alike(o)
            and checks.same_event(o.title or "", story.title or "") and near_in_time(o)]


# --- dedupe ------------------------------------------------------------------------------------------------------


def _recent_posts(db: Session, now: datetime) -> list[NdPost]:
    return list(db.scalars(select(NdPost).where(NdPost.created_at >= now - timedelta(hours=DEDUPE_HOURS), NdPost.status.notin_(("rejected", "expired", "failed")))))


def same_story(title: str, posts: list[NdPost], now: datetime) -> NdPost | None:
    """A different headline about something already covered in the last day: one story, one post."""
    mine = checks.names(title)
    cutoff = now - timedelta(days=1)
    for p in posts:
        at = p.created_at if p.created_at is None or p.created_at.tzinfo else p.created_at.replace(tzinfo=now.tzinfo)
        if p.kind == "news" and (at is None or at >= cutoff) and mine & checks.names(str((p.facts or {}).get("title") or "")):
            return p
    return None


def duplicate(fingerprint: str, posts: list[NdPost]) -> NdPost | None:
    return next((p for p in posts if checks.similarity(p.fingerprint, fingerprint) >= SIMILAR), None)


def on_cooldown(db: Session, key: str, wait: timedelta, now: datetime) -> bool:
    last = db.scalar(select(func.max(NdPost.created_at)).where(NdPost.key == key, NdPost.status.notin_(("rejected", "expired"))))
    return last is not None and (last if last.tzinfo else last.replace(tzinfo=now.tzinfo)) > now - wait


def key_prefix_cooling(db: Session, prefix: str, wait: timedelta, now: datetime) -> bool:
    last = db.scalar(select(func.max(NdPost.created_at)).where(NdPost.key.like(f"{prefix}%"), NdPost.status.notin_(("rejected", "expired"))))
    return last is not None and (last if last.tzinfo else last.replace(tzinfo=now.tzinfo)) > now - wait


def enqueue(db: Session, posts: list[NdPost], *, kind: str, category: str, priority: str, text: str, key: str, score: float, facts: dict[str, Any], src: list[dict[str, Any]],
            source_text: str, names: list[str], expires: datetime | None, now: datetime, dedupe: bool = True, links: list[str] | None = None) -> NdPost | None:
    """Deduplicate and check; only a post that passes everything enters the queue. The checks stay with it. dedupe=False: the shared desk
    already decided this is a new event (a later, bigger move of the same asset reads much like the first)."""
    fp = checks.fingerprint(text)
    if dedupe and duplicate(fp, posts) is not None:
        return None
    result = checks.check_post(text, source_text=source_text, source_names=names, breaking=priority == "breaking", links=links or ())
    if not result.ok:
        logger.info("newsdesk: refused: %s: %s", result.reason, text[:90].replace("\n", " "))
        return None
    post = NdPost(kind=kind, category=category, priority=priority, status="queued", text=text, key=key[:160], fingerprint=fp, score=score, facts=facts, sources=src, checks=result.checks, expires_at=expires, metrics={})
    db.add(post)
    db.flush()
    posts.append(post)
    return post


# --- market ------------------------------------------------------------------------------------------------------


def _as_of(q: sources.Quote) -> str:
    return q.as_of.strftime("%d %b %H:%M")


async def detect_markets(db: Session, s: NdSettings, quotes: dict[str, sources.Quote], posts: list[NdPost], now: datetime, market: Any = None) -> int:
    enabled = s.categories or {}
    factor = SENSITIVITY.get(s.sensitivity, SENSITIVITY["medium"])["move"]
    state = dict(s.state or {})
    last_prices: dict[str, float] = dict(state.get("last_prices") or {})
    made = 0
    for item in s.watchlist or []:
        q = quotes.get(item["symbol"])
        if q is None:
            continue
        previous = last_prices.get(item["symbol"])
        last_prices[item["symbol"]] = q.price
        if not enabled.get(item["category"], True) or (now - q.as_of).total_seconds() > sources.FRESH_SECONDS:
            continue
        symbol_key = f"move:{item['symbol']}:"
        limits = sources.THRESHOLDS.get(item.get("group", ""), sources.THRESHOLDS["stock"])
        best: tuple[float, str, float] | None = None
        for window in sources.WINDOWS:
            change = q.change(window, now)
            if change is None:
                continue
            ratio = abs(change) / (limits[window] * factor)
            if ratio >= 1 and (best is None or ratio > best[0]):
                best = (ratio, window, change)
        src = [{"name": "Yahoo Finance", "url": sources.CHART.format(symbol=item["symbol"]), "at": q.as_of.isoformat()}]
        names = ["Yahoo Finance"]
        as_of = _as_of(q)
        facts: dict[str, Any] = {"symbol": item["symbol"], "price": q.price, "as_of": q.as_of.isoformat()}
        candidate: dict[str, Any] | None = None
        if best is not None and not key_prefix_cooling(db, symbol_key, COOLDOWN["symbol"], now):
            ratio, window, change = best
            direction = "up" if change > 0 else "down"
            key = f"{symbol_key}{window}:{direction}"
            if not on_cooldown(db, key, COOLDOWN["move"], now):
                big = ratio >= 1.5
                spike = q.volume_spike() >= 5
                b = brief.market_brief(item, change, ratio)  # the label and the words are shared with Telegram, through the facts
                candidate = {"key": key, "text": writer.move_post(item, window, change, q.price, as_of, big=big, volume=spike, ratio=ratio, label=b.label), "score": 5.0 + min(ratio, 4.0), "breaking": big,
                             "facts": {**facts, "window": window, "change_pct": round(change, 2), "volume_spike": spike, "ratio": round(ratio, 2),
                                       "headline": writer.move_headline(item, window, change, q.price, ratio), "label": b.label, "shock": b.shock}}
        if candidate is None and q.high_52w and q.price >= q.high_52w * 0.9995 and not on_cooldown(db, f"high:{item['symbol']}", COOLDOWN["extreme"], now):
            candidate = {"key": f"high:{item['symbol']}", "text": writer.extreme_post(item, True, q.price, as_of), "score": 6.0, "breaking": False,
                         "facts": {**facts, "high_52w": q.high_52w, "headline": writer.extreme_headline(item, True, q.price), "label": "", "shock": 50}}
        if candidate is None and q.low_52w and q.price <= q.low_52w * 1.0005 and not on_cooldown(db, f"low:{item['symbol']}", COOLDOWN["extreme"], now):
            candidate = {"key": f"low:{item['symbol']}", "text": writer.extreme_post(item, False, q.price, as_of), "score": 6.0, "breaking": False,
                         "facts": {**facts, "low_52w": q.low_52w, "headline": writer.extreme_headline(item, False, q.price), "label": "", "shock": 50}}
        step = item.get("step")
        if candidate is None and step and previous:
            below, above = int(previous // step), int(q.price // step)
            if below != above:
                up = above > below
                level = float(max(above, below) * step)
                key = f"mile:{item['symbol']}:{level:g}"
                if not on_cooldown(db, key, COOLDOWN["milestone"], now):
                    candidate = {"key": key, "text": writer.milestone_post(item, level, up, q.price, as_of), "score": 5.5, "breaking": False,
                                 "facts": {**facts, "level": level, "previous": previous, "headline": writer.milestone_headline(item, level, up, q.price), "label": "", "shock": 40}}
        if candidate is None:
            continue
        ok, detail = await sources.second_opinion(item, q.price, market)
        if not ok:
            logger.info("newsdesk: %s not verified: %s", item["symbol"], detail)
            continue
        candidate["facts"]["verified"] = detail
        text = candidate["text"]
        made += enqueue(db, posts, kind="market", category=item["category"], priority="breaking" if candidate["breaking"] else "scheduled", text=text, key=candidate["key"], score=candidate["score"],
                        facts=candidate["facts"], src=src, source_text=text, names=names, expires=now + timedelta(minutes=45), now=now) is not None
    indices = [(i["name"], quotes[i["symbol"]].change("24h", now)) for i in s.watchlist or [] if i.get("group") == "index" and i["symbol"] in quotes]
    falling = [(n, c) for n, c in indices if c is not None and c <= -sources.THRESHOLDS["index"]["24h"] * factor]
    if enabled.get("stocks", True) and len(falling) >= 2 and not key_prefix_cooling(db, "selloff:", COOLDOWN["selloff"], now):
        latest = max((quotes[i["symbol"]].as_of for i in s.watchlist or [] if i.get("group") == "index" and i["symbol"] in quotes))
        text = writer.selloff_post(falling, latest.strftime("%d %b %H:%M"), label=brief.JUST_IN)
        made += enqueue(db, posts, kind="market", category="stocks", priority="breaking", text=text, key=f"selloff:{now:%Y%m%d}", score=8.0,
                        facts={"indices": dict(falling), "as_of": latest.isoformat(), "headline": writer.selloff_headline(falling), "label": brief.JUST_IN, "shock": 70},
                        src=[{"name": "Yahoo Finance", "url": "https://finance.yahoo.com", "at": latest.isoformat()}], source_text=text, names=["Yahoo Finance"], expires=now + timedelta(minutes=45), now=now) is not None
    state["last_prices"] = last_prices
    s.state = state
    db.commit()
    return made


def detect_liquidations(db: Session, s: NdSettings, posts: list[NdPost], now: datetime) -> int:
    """From the exchange liquidation feeds Price Chat already records. A big flush only: the source's threshold, three times over."""
    if not (s.categories or {}).get("crypto", True):
        return 0
    from app.brand_manager.sources import LIQ_HOUR_USD, liquidation_topics

    made = 0
    for t in liquidation_topics(db, now):
        data = t.brief or {}
        total = (data.get("numbers") or {}).get("liquidations_1h_usd", 0)
        headline = data.get("headline") or ""
        coin = (data.get("coin") or {}).get("symbol", "")
        if total < LIQ_HOUR_USD * 3 or not headline or not coin or on_cooldown(db, f"liq:{coin}", COOLDOWN["liq"], now):
            continue
        text = f"💥 LIQUIDATIONS\n\n{headline}.\n\nData: public exchange feeds"
        made += enqueue(db, posts, kind="market", category="crypto", priority="scheduled", text=text, key=f"liq:{coin}", score=6.0, facts={"usd": total}, src=[{"name": "public exchange feeds", "url": "https://pricechat.live", "at": now.isoformat()}],
                        source_text=text, names=["public exchange feeds"], expires=now + timedelta(hours=1), now=now) is not None
    db.commit()
    return made


def snapshot(db: Session, s: NdSettings, quotes: dict[str, sources.Quote], posts: list[NdPost], now: datetime) -> int:
    """A plain market table when there's fresh data: real numbers, never filler news."""
    if on_cooldown(db, "snapshot", COOLDOWN["snapshot"], now):
        return 0
    rows, latest = [], None
    for item in s.watchlist or []:
        q = quotes.get(item["symbol"])
        if q is None or item.get("group") == "stock" or item["symbol"] in ("^DJI", "HG=F", "XRP-USD", "SOL-USD"):
            continue
        change = q.change("24h", now)
        if change is not None:
            rows.append((item["name"], change))
            latest = max(latest, q.as_of) if latest else q.as_of
    if len(rows) < 3 or latest is None:
        return 0
    text = writer.snapshot_post(rows, latest.strftime("%d %b %H:%M"))
    return enqueue(db, posts, kind="market", category="viral", priority="scheduled", text=text, key="snapshot", score=3.5, facts={"rows": dict(rows), "as_of": latest.isoformat()},
                   src=[{"name": "Yahoo Finance", "url": "https://finance.yahoo.com", "at": latest.isoformat()}], source_text=text, names=["Yahoo Finance"], expires=now + timedelta(minutes=45), now=now) is not None


# --- news ------------------------------------------------------------------------------------------------------


async def plan_news(db: Session, s: NdSettings, settings: Settings, posts: list[NdPost], now: datetime, transport: Any = None, quotes: dict[str, sources.Quote] | None = None) -> int:
    """The same stories as the Telegram lane, from the shared engine (app/newsdesk/chaos), written for X. X keeps its own queue, limits and dedupe."""
    rows = list(db.scalars(select(NdStory).where(NdStory.created_at >= now - timedelta(hours=36))))
    ready, _ = chaos.candidates([r for r in rows], quotes or {}, now, max_age=timedelta(hours=12), categories=s.categories or {})
    wanted = max(1, s.target_per_day // 3)
    made = 0
    queued = sum(1 for p in posts if p.status == "queued" and p.priority == "scheduled")
    since = now - timedelta(hours=6)
    moved = [str((p.facts or {}).get("title") or (p.facts or {}).get("headline") or "") for p in posts
             if (p.created_at if p.created_at is None or p.created_at.tzinfo else p.created_at.replace(tzinfo=now.tzinfo)) is None or (p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=now.tzinfo)) >= since]
    for v in ready:
        story, b = v.story, v.brief
        if story.used:
            continue
        breaking = b.label == brief.BREAKING
        if not breaking and queued >= wanted:
            continue
        if same_story(story.title, posts, now) is not None or chaos.moved_lately(story.title, moved):
            story.used = True  # one story, one post; and one move per asset in six hours, as Telegram does
            continue
        srcs = [{"name": x.source, "url": x.link, "at": x.published_at.isoformat() if x.published_at else None} for x in [story, *v.others] if x.link.startswith("https://")]
        named = list(dict.fromkeys(x["name"] for x in srcs))[:3]
        shown = [next(x for x in srcs if x["name"] == n) for n in named]
        text = brief.x_text(brief.clean_headline(story.title), shown, b, story.link)
        source_text = " ".join(f"{x.title} {x.summary or ''}" for x in [story, *v.others]) + " " + " ".join(b.tickers)
        at = story.published_at if story.published_at.tzinfo else story.published_at.replace(tzinfo=now.tzinfo)
        post = enqueue(db, posts, kind="news", category=story.category or "viral", priority="breaking" if breaking else "scheduled", text=text, key=f"story:{story.id}", score=round(b.rank / 10, 2),
                       facts={"title": story.title, "corroborated_by": sorted({o.source for o in v.others}), "published_at": at.isoformat(), "official": story.tier == 0,
                              "evidence": v.evidence, "price_check": v.price_check, "label": b.label, "shock": b.shock},
                       src=srcs[:5], source_text=source_text, names=named, expires=now + timedelta(hours=12), now=now)
        story.used = True
        for o in v.others:
            o.used = True
        if post is not None:
            made += 1
            queued += 0 if breaking else 1
            moved.append(story.title)
    db.commit()
    return made


# --- the shared desk's events, delivered to X -----------------------------------------------------------------------


X_STATUS = {"dry_run": "dry_run", "published": "published", "uncertain": "uncertain", "failed": "failed", "queued": "queued", "publishing": "queued", "expired": "missed", "rejected": "skipped"}


def _sync(ev: NdEvent, post: NdPost) -> None:
    events.mark(ev, "x", X_STATUS.get(post.status, post.status), post.error or "", post_id=post.id)


async def deliver_x(db: Session, s: NdSettings, settings: Settings, runtime: XRuntime | None, agent_id: int, now: datetime, transport: Any = None) -> str | None:
    """The next released event X still owes: written for X, checked, queued and sent (or recorded, in dry run). X's own failures stay X's:
    Telegram's delivery of the same event is untouched. Returns the publish result, or None if there was nothing to send."""
    if emergency_stop(db):
        return None
    posts = _recent_posts(db, now)
    for ev in events.pending(db, "x", now):
        if ev.x_status == "queued" and ev.x_post_id:
            post = db.get(NdPost, ev.x_post_id)
            if post is None or post.status != "queued":
                if post is not None:
                    _sync(ev, post)
                continue
            nb = post.not_before if post.not_before is None or post.not_before.tzinfo else post.not_before.replace(tzinfo=now.tzinfo)
            if nb is not None and nb > now:
                continue
            result = await publish(db, s, post, settings, runtime, agent_id, now)
            db.refresh(post)
            if result != STALE:
                _sync(ev, post)
            db.commit()
            return result
        following = events.X_FOLLOWS_TELEGRAM
        if following and ev.tg_status in events.TG_WAITING:
            continue  # wait for Telegram's outcome on this event (it also picks the picture X reuses)
        own = False
        if following and ev.tg_status == "skipped":
            # owner, 2026-11-18: each platform on its own. Telegram refusing ITS caption (a picture caption, a model's sentence) no longer
            # silences X: X posts its own checked headline-and-source text (never Telegram's refused package), once per event, and only
            # if Trending hasn't posted the story. Production: #649, #658, #662, #667 were lost on X because Telegram skipped them.
            own = True
            following = False
            logger.info("newsdesk: event %s: Telegram skipped it (%s); X writes its own checked text", ev.id, (ev.why or "")[:80])
        elif following and ev.tg_status not in ("delivered", "uncertain"):
            events.mark(ev, "x", "skipped", f"Telegram didn't post it ({ev.tg_status})")
            db.commit()
            continue
        if not following and not (s.categories or {}).get(ev.category, True):
            events.mark(ev, "x", "skipped", f"the {ev.category} category is off on X")
            continue
        cap = 10**6 if following else s.target_per_day + s.max_breaking_per_day  # following: Telegram's limits already decided it
        if ev.slot == "slot" and not following:  # the six scheduled slots are the plan: X's older 3-4 a day setting doesn't drop them
            cap = max(cap, len(events.policy(s.state or {}).slot_hours) + 1)  # +1: yesterday's same slot can still be inside a rolling 24 h
        sent = int(db.scalar(select(func.count()).select_from(NdEvent).where(NdEvent.x_status.in_(("dry_run", "published", "uncertain")), NdEvent.released_at >= now - timedelta(days=1))) or 0)
        if sent >= cap:
            events.mark(ev, "x", "skipped", f"X's own daily limit ({cap})")
            continue
        covered = _trend_covered(db, ev, now)
        if covered:  # Trending Topics already tweeted this story: one story, one post on X (app/trending/coordinate)
            events.mark(ev, "x", "skipped", covered)
            db.commit()
            continue
        # owner, 2026-11-20: X re-reads the live price too, right before it writes (production: X's own-text path posted a 2h15m-old silver
        # figure that Telegram had refused). New figures drop Telegram's caption, so X then writes its own headline from the fresh ones
        state, detail = await livedata.refresh(db, ev, now, transport=transport)
        if state == livedata.FADED:
            events.mark(ev, "x", "skipped", f"the move no longer stands: {detail}")
            logger.info("newsdesk: event %s not posted on X: the move no longer stands: %s", ev.id, detail)
            db.commit()
            continue
        if state == livedata.HOLD:
            ev.x_note = f"waiting for a fresh live price: {detail}"[:300]
            logger.warning("newsdesk: event %s held on X: waiting for a fresh live price: %s", ev.id, detail)
            db.commit()
            continue
        if own is False and network_stored_missing(ev):
            own = True  # the figures changed after Telegram's caption was written: X writes its own text from the fresh ones
        db.commit()
        b = events.brief_of(ev, now)
        f = ev.facts or {}
        named = list(dict.fromkeys(x["name"] for x in ev.sources or []))[:2]
        shown = [next(x for x in ev.sources if x["name"] == n) for n in named]
        text, links, source_text, named = x_words(ev, b, shown, named, package=not own)
        urgent = ev.label == brief.BREAKING or ev.slot in ("extra", "breaking")
        market_kind = ev.kind in ("market", "recap") or bool(f.get("symbol"))
        post = enqueue(db, posts, kind="market" if market_kind else "news", category=ev.category or "viral", priority="breaking" if urgent else "scheduled", text=text, key=f"event:{ev.id}",
                       score=round(ev.virality / 10, 2), facts={"event_id": ev.id, "title": ev.title, "headline": ev.headline, "topic": ev.topic, "label": ev.label, "virality": ev.virality,
                                                                 "evidence": ev.evidence, "slot": ev.slot, "price_check": f.get("price_check", ""), "symbol": f.get("symbol"),
                                                                 "verified": f.get("verified"), "parent_event": ev.parent_id, "viral": engagement.features(ev),
                                                                 "corroborated_by": [n for n in (f.get("outlets") or []) if n != (ev.sources or [{}])[0].get("name")]},
                       src=ev.sources or [], source_text=source_text, names=named, expires=now + timedelta(hours=2), now=now, dedupe=False, links=links)
        if post is None:
            if following:
                logger.warning("newsdesk: X couldn't follow Telegram on event %s: the X text failed the checks", ev.id)
            events.mark(ev, "x", "skipped", "the X text failed the checks")
            db.commit()
            continue
        events.mark(ev, "x", "queued", post_id=post.id)  # tied to its post first: if a send dies midway, the next tick never writes a second one
        db.commit()
        result = await publish(db, s, post, settings, runtime, agent_id, now)
        db.refresh(post)
        if result != STALE:
            _sync(ev, post)
        db.commit()
        return result
    return None


STALE = "stale"


def network_stored_missing(ev: NdEvent) -> bool:
    """Telegram's caption (the package X reuses) is gone: livedata dropped it because the figures changed after it was written."""
    from app.newsdesk import network

    return livedata.needs_live(ev) and not network.stored(ev)


def _trend_covered(db: Session, ev: NdEvent, now: datetime) -> str:
    """Why X skips this event: a Trending Topics post on the same story in the last 24 h. Never stops the desk if Trending misbehaves."""
    try:
        from app.trending import coordinate

        post = coordinate.trend_covered(db, [ev.title or "", ev.headline or ""], now)
    except Exception:  # noqa: BLE001
        logger.exception("newsdesk: the Trending Topics check failed; X goes on without it")
        return ""
    return f"Trending Topics already posted this story (trend post {post.id})" if post is not None else ""


def x_words(ev: NdEvent, b: Any, shown: list[dict[str, Any]], named: list[str], package: bool = True) -> tuple[str, list[str], str, list[str]]:
    """(text, allowed links, what the sources said, source names) for X. Price Chat News (2026-11-11): the caption Telegram's package
    wrote (headline, context, a question now and then, the article link), so both platforms say the same thing; without one, the older
    headline-and-source format."""
    from app.newsdesk import network

    f = ev.facts or {}
    pkg = network.stored(ev) if package else None
    if pkg and pkg.get("x_text"):
        links = [str(x.get("url")) for x in pkg.get("links") or [] if x.get("url")]
        names = [str(x.get("name")) for x in pkg.get("links") or [] if x.get("kind") != "pricechat" and x.get("name")] or named
        return checks.one_cashtag(str(pkg["x_text"])), links, network.source_text_of(ev), names  # X: one cashtag (403 otherwise)
    if X_FORMAT == "visual":  # the owner's format: the same picture as Telegram, the headline, a small source credit
        from app.newsdesk import visual

        text = visual.x_text(visual.post_headline(ev), visual.source_name(ev))
        note = network.data_note(ev)
        if note:  # a measured market post keeps its reference and "Market data:" line on X too (Telegram 203's missing levels, X side)
            head, src = visual.post_headline(ev), visual.source_name(ev)
            for n in dict.fromkeys([note, network._sentences(note)[-1]]):
                candidate = f"{head}\n\n{n}\n\nSource: {src}"
                if network.x_length(candidate) <= network.X_LIMIT:
                    text = candidate
                    break
        told = f"{f.get('told') or ev.title} {ev.headline} {note} {' '.join(b.tickers)}"
        return checks.one_cashtag(text), [], told, named
    else:
        text = (f.get("x_text") or "") if ev.kind == "recap" else brief.x_text(ev.headline, shown, b, ev.key)  # the market check is written for X when made
    return checks.one_cashtag(text), [], f"{f.get('told') or ev.title} {ev.headline} {' '.join(b.tickers)}", named


# --- publish ------------------------------------------------------------------------------------------------------


def _sent(db: Session, s: NdSettings, since: datetime, priority: str | None = None) -> int:
    stmt = select(func.count()).select_from(NdPost).where(NdPost.published_at >= since, NdPost.status.in_(LIVE if s.mode == "live" else COUNTED))
    if priority:
        stmt = stmt.where(NdPost.priority == priority)
    return int(db.scalar(stmt) or 0)


def _last_sent(db: Session, s: NdSettings, priority: str) -> datetime | None:
    last = db.scalar(select(func.max(NdPost.published_at)).where(NdPost.priority == priority, NdPost.status.in_(LIVE if s.mode == "live" else COUNTED)))
    return last


def choose(db: Session, s: NdSettings, now: datetime) -> NdPost | None:
    """Breaking first, within its limits. Otherwise the next slot, spaced through the day, from whichever enabled category has had least lately."""
    queued = list(db.scalars(select(NdPost).where(NdPost.status == "queued", NdPost.key.notlike("event:%")).order_by(NdPost.score.desc(), NdPost.id)))  # events go through deliver_x
    ready = [p for p in queued if p.not_before is None or (p.not_before if p.not_before.tzinfo else p.not_before.replace(tzinfo=now.tzinfo)) <= now]
    day_ago = now - timedelta(days=1)
    breaking = [p for p in ready if p.priority == "breaking"]
    if breaking and _sent(db, s, day_ago, "breaking") < s.max_breaking_per_day:
        last = _last_sent(db, s, "breaking")
        if last is None or (last if last.tzinfo else last.replace(tzinfo=now.tzinfo)) <= now - timedelta(minutes=s.breaking_gap_minutes):
            return breaking[0]
    scheduled = [p for p in ready if p.priority == "scheduled"]
    if not scheduled or _sent(db, s, day_ago, "scheduled") >= s.target_per_day:
        return None
    last = _last_sent(db, s, "scheduled")
    gap = timedelta(seconds=86400 / max(1, s.target_per_day) * 0.9)
    if last is not None and (last if last.tzinfo else last.replace(tzinfo=now.tzinfo)) > now - gap:
        return None
    counts = dict(db.execute(select(NdPost.category, func.count()).where(NdPost.published_at >= day_ago, NdPost.status.in_(COUNTED)).group_by(NdPost.category)).all())
    return min(scheduled, key=lambda p: (counts.get(p.category, 0), -p.score, p.id))


def expire(db: Session, now: datetime) -> int:
    n = db.execute(update(NdPost).where(NdPost.status == "queued", NdPost.expires_at.is_not(None), NdPost.expires_at <= now).values(status="expired")).rowcount
    # a run that died after claiming a post: it may have reached X, so a person looks; it's never resent
    n2 = db.execute(update(NdPost).where(NdPost.status == "publishing", NdPost.updated_at <= now - timedelta(minutes=15)).values(status="uncertain", error="The publisher stopped mid-send: check X before resending")).rowcount
    db.commit()
    return int(n or 0) + int(n2 or 0)


def _claim(db: Session, post: NdPost) -> bool:
    done = db.execute(update(NdPost).where(NdPost.id == post.id, NdPost.status == "queued").values(status="publishing", attempts=NdPost.attempts + 1, updated_at=utcnow()))
    db.commit()
    return done.rowcount == 1


HTTP_OF = {"x_auth": "401", "x_forbidden": "403", "x_not_found": "404", "x_bad_request": "400", "x_rate_limited": "429", "x_usage_capped": "429", "x_no_credit": "402",
           "x_unavailable": "5xx/network", "x_budget": "local budget", "x_not_configured": "not connected"}
STAGE_TEXT = {"permit": "the safety guard", "image": "making the picture", "upload": "the picture upload", "post": "the post"}


def _x_error(stage: str, e: Exception) -> str:
    """What Admin shows: where it failed, X's status and error code, and X's own words."""
    code = str(getattr(e, "code", "") or "")
    http = HTTP_OF.get(code)
    where = STAGE_TEXT.get(stage, stage)
    return (f"X {http} {code} at {where}: {e}" if http else f"{type(e).__name__} at {where}: {e}")[:300]


def _note_x(s: NdSettings, post: NdPost, now: datetime, *, ok: bool, outcome: str, message: str = "", stage: str = "") -> None:
    """The health line in Admin: the last post X confirmed and the last thing that went wrong, kept in the desk's state."""
    state = dict(s.state or {})
    entry = {"at": now.isoformat(), "post_id": post.id, "event_id": (post.facts or {}).get("event_id"), "outcome": outcome}
    if ok:
        state["x_last_ok"] = {**entry, "x_id": post.x_id}
    else:
        state["x_last_error"] = {**entry, "stage": stage, "message": message[:300]}
    s.state = state


def _editorial_hold(db: Session, post: NdPost, now: datetime | None = None) -> str:
    """Why an event's post must not go out after all, or "": the story still clears the editorial bar and every outlet behind it reports this
    same event (app/newsdesk/quality). Posts that aren't an event's (manual, test) are the owner's own."""
    from app.newsdesk import quality

    event_id = (post.facts or {}).get("event_id")
    ev = db.get(NdEvent, event_id) if event_id else None
    if ev is None:
        return ""
    rows = [db.get(NdStory, i) for i in ((ev.facts or {}).get("story_ids") or [])[:6]]
    # the caption was written from the rows that tell this event (telegram.stories_of leaves the others out): check those
    _, kept = quality.split_off_story(ev, [r for r in rows if r is not None])
    problems = quality.event_problems(ev, kept)
    if now is not None and (told := quality.already_posted(db, ev, now)):
        problems.append(told)
    return "; ".join(problems)


def _stale_market(db: Session, post: NdPost, now: datetime) -> str:
    """The one freshness rule (app/newsdesk/livedata.problem), at the moment X sends an event's market post."""
    event_id = (post.facts or {}).get("event_id")
    ev = db.get(NdEvent, event_id) if event_id else None
    return livedata.problem(ev, now) if ev is not None else ""


async def publish(db: Session, s: NdSettings, post: NdPost, settings: Settings, runtime: XRuntime | None, agent_id: int, now: datetime, *, force_live: bool = False) -> str:
    """Send one post, or record what a dry run would have sent. Returns its final status. One post's failure never raises: it is recorded on the
    post (and in the desk's x_last_error) so the next tick goes on. force_live: the owner's one test post, sent even while the desk is in dry run."""
    from app.social.guard import ActionRequest, SocialActionGuard
    from app.models.enums import Platform, SocialAction

    if emergency_stop(db):  # nothing goes out under the emergency stop: not a queued post, not the owner's test post
        logger.warning("newsdesk: post %s not sent: %s", post.id, emergency_stop(db))
        return STOPPED
    if not _claim(db, post):
        return "already claimed"
    db.refresh(post)
    if checks.cashtag_count(post.text) > checks.X_MAX_CASHTAGS:  # X answers 403 to an API post with more than one cashtag
        post.text = checks.one_cashtag(post.text)
        db.commit()
    stale = _stale_market(db, post, now) if not force_live else ""
    if stale:  # owner, 2026-11-20: the post waited (a retry, a pause) and its figures are no longer current: it is written again from fresh ones
        post.status, post.error = "expired", f"stale market data: {stale}"[:500]
        ev = db.get(NdEvent, (post.facts or {}).get("event_id"))
        if ev is not None:
            ev.x_status, ev.x_post_id, ev.x_note = "pending", None, f"re-reading the live price: {stale}"[:300]
        db.commit()
        logger.info("newsdesk: post %s not sent with stale figures (%s): X writes it again from a fresh price", post.id, stale)
        return STALE
    held = _editorial_hold(db, post, now)
    if held and not force_live:  # the final editorial check (owner, 2026-11-16), in dry run too: what would never go out is never recorded as sent
        post.status, post.error = "rejected", f"Final editorial check: {held}"
        db.commit()
        logger.info("newsdesk: post %s held by the final editorial check: %s", post.id, held)
        return "rejected"
    if s.mode != "live" and not force_live:
        post.status, post.published_at = "dry_run", now
        db.commit()
        logger.info("newsdesk DRY RUN [%s/%s score %.1f]: %s", post.priority, post.category, post.score, post.text.replace("\n", " / "))
        return "dry_run"
    guard = SocialActionGuard(db, agent_id, settings)
    req = ActionRequest(Platform.X, SocialAction.POST, text=post.text)
    problem = guard.news_problem(req)
    if problem:
        post.status, post.error = "queued", problem
        s.paused_until, s.pause_reason = now + timedelta(minutes=30), f"X guard: {problem}"
        _note_x(s, post, now, ok=False, outcome="blocked", message=problem, stage="permit")
        db.commit()
        return "blocked"
    from app.social.x_service import make_x_client

    client = make_x_client(db, settings, runtime)  # type: ignore[arg-type]
    stage = "permit"
    media = None
    picture: bytes | None = None
    try:
        event_id = (post.facts or {}).get("event_id")
        if X_FORMAT == "visual" and event_id:
            from app.newsdesk import visual

            ev = db.get(NdEvent, event_id)
            if ev is not None:
                stage = "image"
                image, meta = await visual.for_event(ev, db=db, platform="x")
                ev.facts = {**(ev.facts or {}), "visual": meta}
                if isinstance((post.facts or {}).get("viral"), dict):  # what the post actually carried, for what engagement learns
                    post.facts = {**post.facts, "viral": {**post.facts["viral"], "format": str(meta.get("kind") or "")}}
                if not image and not (post.facts or {}).get("text_ok") and "https://" not in post.text:
                    # owner, 2026-11-23: never a text-only post on its own. A post that carries its article's link shows X's own card of
                    # that article (the owner's X-links switch decides that); without a link and without a licensed picture it waits for
                    # the owner in the Terminal
                    why = "no licensed picture passed the Visual Editor"
                    post.status, post.error = "held", f"held for review: {why}"
                    from app.newsdesk import events as _events

                    _events.mark(ev, "x", "held", why)
                    _note_x(s, post, now, ok=False, outcome="held", message=post.error, stage="image")
                    db.commit()
                    logger.info("newsdesk: X post %s held for review: %s (event %s)", post.id, why, ev.id)
                    return "held"
                if image:  # the wire style's text-only post (b"") goes without a picture, as on Telegram
                    stage = "permit"
                    upload_permit = guard.issue_news_permit(req)  # permits are single use (guard.require_permit): the upload and the post each need one
                    stage = "upload"
                    media = [await client.upload_image(image, permit=upload_permit)]
                    picture = image
        stage = "permit"
        permit = guard.issue_news_permit(req)
        stage = "post"
        data = await client.create_post(post.text, permit=permit, media_ids=media)
    except XUnavailableError as e:
        if "in time" in str(e) and stage == "post":
            post.status, post.error = "uncertain", "X didn't answer in time: it may have posted. Check X before resending."
        else:
            _retry(post, _x_error(stage, e), now)
        _note_x(s, post, now, ok=False, outcome=post.status, message=_x_error(stage, e), stage=stage)
        db.commit()
        return post.status
    except (XCreditsError, XAuthError, XForbiddenError) as e:
        post.status, post.error = "queued", _x_error(stage, e)
        s.paused_until, s.pause_reason = now + timedelta(hours=6), f"X refused the desk: {_x_error(stage, e)[:200]}"
        _note_x(s, post, now, ok=False, outcome="paused", message=_x_error(stage, e), stage=stage)
        db.commit()
        logger.warning("newsdesk: paused: X refused it at %s (%s): %s", stage, type(e).__name__, str(e)[:200])
        return "paused"
    except XRateLimitError as e:
        _retry(post, _x_error(stage, e), now, minutes=30)
        _note_x(s, post, now, ok=False, outcome=post.status, message=_x_error(stage, e), stage=stage)
        db.commit()
        return post.status
    except XError as e:
        post.status, post.error = "failed", _x_error(stage, e)
        _note_x(s, post, now, ok=False, outcome="failed", message=post.error, stage=stage)
        db.commit()
        logger.warning("newsdesk: X post %s failed: %s", post.id, post.error)
        return "failed"
    except Exception as e:  # noqa: BLE001 - a bug or an odd answer must not leave the post "publishing" and fail every tick (2026-11-09/10)
        sent = stage == "post" and not isinstance(e, PermissionError)  # require_permit refuses before any request is made
        post.status = "uncertain" if sent else "failed"
        post.error = (_x_error(stage, e) + (" It may have reached X: check before resending." if sent else " Nothing was posted."))[:300]
        _note_x(s, post, now, ok=False, outcome=post.status, message=post.error, stage=stage)
        db.commit()
        logger.exception("newsdesk: X post %s stopped at %s", post.id, stage)
        return post.status
    finally:
        await client.aclose()
    x_id = str(data.get("id") or "")
    if not x_id:
        post.status, post.error = "uncertain", "X answered without confirming a post id"
        _note_x(s, post, now, ok=False, outcome="uncertain", message=post.error, stage="post")
        db.commit()
        return post.status
    post.status, post.x_id, post.published_at, post.error = "published", x_id, now, None
    guard.record_attempt(req, None, None, "news", reason="news desk", external_id=x_id)
    _note_x(s, post, now, ok=True, outcome="published")
    db.commit()
    ev_id = (post.facts or {}).get("event_id")
    ev_row = db.get(events.NdEvent, ev_id) if ev_id else None
    lat = events.latency(ev_row, now) if ev_row is not None else {}
    logger.info("newsdesk published %s (post %s, event %s, picture %s; event -> X %s min, first seen -> X %s min)", x_id, post.id, ev_id, "yes" if media else "no",
                lat.get("event_to_release_min"), lat.get("detect_to_release_min"))
    if picture and ev_row is not None:  # audited at once (app/newsdesk/validation); observes only
        from app.newsdesk import validation

        validation.record_image(db, "x", x_id, ev_row, picture, (ev_row.facts or {}).get("visual") or {}, f"https://x.com/i/status/{x_id}")
        db.commit()
        await validation.second_look(db, "x", x_id, picture, ev_row.headline or ev_row.title or "", (ev_row.facts or {}).get("visual") or {},
                                     settings=settings)  # optional; vision is off: nothing runs
    from app.newsdesk import memory  # the story's memory across every lane

    memory.remember(db, "x-newsdesk", x_id, (ev_row.title or ev_row.headline) if ev_row is not None else (post.text or "").split("\n")[0],
                    (ev_row.summary or "") if ev_row is not None else "", now)
    db.commit()
    return "published"


def _learn_casing(db: Session, now: datetime) -> None:
    """What the feeds write as ordinary words, for setting Title Case headlines in sentence case without losing a name (wire.headline_case)."""
    from app.newsdesk import wire

    try:
        rows = db.execute(select(NdStory.title, NdStory.summary).where(NdStory.created_at >= now - timedelta(days=3))).all()
        wire.learn_vocabulary(t for r in rows for t in (r[0], r[1]))
    except Exception as e:  # noqa: BLE001 - casing falls back to keeping unknown capitals
        logger.info("newsdesk: casing vocabulary not refreshed (%s)", type(e).__name__)


def _retry(post: NdPost, reason: str, now: datetime, minutes: int = 5) -> None:
    if post.attempts >= MAX_ATTEMPTS:
        post.status, post.error = "failed", f"Gave up after {post.attempts} tries: {reason[:200]}"
        return
    post.status, post.error, post.not_before = "queued", reason[:300], now + timedelta(minutes=minutes * 2 ** post.attempts)


# --- tick ------------------------------------------------------------------------------------------------------


async def tick(db: Session, settings: Settings, runtime: XRuntime | None, agent_id: int, now: datetime | None = None, *, transport: httpx.AsyncBaseTransport | None = None, market: Any = None, writer_transport: Any = None) -> dict[str, Any]:
    now = now or utcnow()
    s = settings_row(db)
    db.commit()
    expired = expire(db, now)
    why = blocked_reason(s, now)
    state = dict(s.state or {})
    out: dict[str, Any] = {"mode": s.mode, "expired": expired}
    if s.kill_switch or not s.automation:
        out["skipped"] = why
        state["last_run"] = {"at": now.isoformat(), **out}
        s.state = state
        db.commit()
        return out
    async with httpx.AsyncClient(transport=transport) as client:
        collected = await collect(db, s, client, now)
        quotes = await sources.fetch_quotes(client, moves.watch_items(s.watchlist or []))
    _learn_casing(db, now)
    posts = _recent_posts(db, now)
    # the shared desk decides for X and Telegram alike; the daily market table is X-only and off unless Admin turns it on (state.snapshot)
    decided = await events.decide(db, now, quotes=quotes, watchlist=s.watchlist or [], categories=s.categories or {}, market=market, second_opinion=sources.second_opinion,
                                  desk_state=s.state or {})
    made_snap = snapshot(db, s, quotes, posts, now) if (s.state or {}).get("snapshot") else 0
    db.commit()
    await events.alert_slots(db, now, events.policy(s.state or {}))
    from app.newsdesk import validation

    await validation.tick(db, now, settings=settings)  # flags to the owner at once; the report at 30 candidates
    from app.newsdesk import chief

    await chief.tick(db, now, settings=settings)  # the AI editor's shadow review: does nothing unless the owner switched it on
    result = None
    if why is None:
        result = await deliver_x(db, s, settings, runtime, agent_id, now, transport)
        if result is None:
            post = choose(db, s, now)  # a person's own posts, and anything queued before the shared desk
            if post is not None:
                result = await publish(db, s, post, settings, runtime, agent_id, now)
    db.refresh(s)
    state = dict(s.state or {})
    out.update({"quotes": len(quotes), "of": len(s.watchlist or []), **collected, "events": decided, "queued_markets": made_snap, "queued_news": decided.get("stories", 0), "published": result, "paused": why})
    state["last_run"] = {"at": now.isoformat(), **out}
    state["feed_problems"] = collected["feed_problems"]
    state["feed_health"] = {"at": now.isoformat(), "feeds": collected.get("feed_health") or {}}
    s.state = state
    db.commit()
    return out


async def follow_telegram(db: Session, settings: Settings, runtime: XRuntime | None, agent_id: int, now: datetime | None = None) -> str | None:
    """Owner, 2026-11-11: Telegram and X post at the same time. Called by the Telegram job the moment it delivers: X posts the same event
    (same words, links and picture) at once instead of on its own 5-minute tick. Every X rule still applies: the emergency stop, automation,
    pauses, X's checks; dry run records instead of posting."""
    now = now or utcnow()
    s = settings_row(db)
    if s.kill_switch or not s.automation or blocked_reason(s, now) is not None:
        return None
    return await deliver_x(db, s, settings, runtime, agent_id, now)


async def collect_metrics(db: Session, settings: Settings, runtime: XRuntime, now: datetime | None = None) -> dict[str, Any]:
    """Impressions, likes and reposts for the last three days' posts, once a day. X charges per post read, so at most 100."""
    from app.social.x_service import make_x_client

    now = now or utcnow()
    posts = list(db.scalars(select(NdPost).where(NdPost.status == "published", NdPost.x_id.is_not(None), NdPost.published_at >= now - timedelta(days=3)).limit(100)))
    if not posts:
        return {"updated": 0}
    client = make_x_client(db, settings, runtime)
    try:
        data = await client.own_post_metrics([p.x_id for p in posts])
    finally:
        await client.aclose()
    by_id = {str(d.get("id")): d for d in (data.get("data") or [])}
    n = 0
    for p in posts:
        d = by_id.get(p.x_id or "")
        if d:
            p.metrics = {**(d.get("public_metrics") or {}), **(d.get("non_public_metrics") or {}), **(d.get("organic_metrics") or {}), "read_at": now.isoformat()}
            n += 1
    db.commit()
    return {"updated": n}


# --- what Admin shows and changes -----------------------------------------------------------------------------


def post_out(p: NdPost) -> dict[str, Any]:
    return {"id": p.id, "kind": p.kind, "category": p.category, "priority": p.priority, "status": p.status, "text": p.text, "edited": bool(p.original_text), "score": p.score, "facts": p.facts, "sources": p.sources, "checks": p.checks,
            "error": p.error, "x_id": p.x_id, "url": f"https://x.com/i/status/{p.x_id}" if p.x_id else None, "metrics": p.metrics, "attempts": p.attempts,
            "created_at": p.created_at.isoformat() if p.created_at else None, "published_at": p.published_at.isoformat() if p.published_at else None, "expires_at": p.expires_at.isoformat() if p.expires_at else None}


def settings_out(s: NdSettings) -> dict[str, Any]:
    return {"automation": s.automation, "mode": s.mode, "kill_switch": s.kill_switch, "paused_until": s.paused_until.isoformat() if s.paused_until else None, "pause_reason": s.pause_reason, "target_per_day": s.target_per_day,
            "max_breaking_per_day": s.max_breaking_per_day, "breaking_gap_minutes": s.breaking_gap_minutes, "sensitivity": s.sensitivity, "categories": s.categories, "feeds": s.feeds, "watchlist": s.watchlist,
            "market_pause": events.MARKET_PAUSE_DEFAULT if (s.state or {}).get("market_pause") is None else bool((s.state or {}).get("market_pause")),
            "market_pause_reason": (s.state or {}).get("market_pause_reason") or ("not resumed by the owner since the 2026-11-21 deploy"
                                                                                  if (s.state or {}).get("market_pause") is None and events.MARKET_PAUSE_DEFAULT else None),
            "liq_pause": bool((s.state or {}).get("liq_pause")), "liq_pause_reason": (s.state or {}).get("liq_pause_reason"),
            "held_figures": list((s.state or {}).get("anomalies") or [])[-10:]}


def overview(db: Session, settings: Settings) -> dict[str, Any]:
    from app.connectors.state import get_connector_state
    from app.models.enums import Platform
    from app.security import credentials
    from app.social.budget import PlatformBudget

    now = utcnow()
    s = settings_row(db)
    db.commit()
    def rows(*statuses: str, limit: int = 50) -> list[dict[str, Any]]:
        return [post_out(p) for p in db.scalars(select(NdPost).where(NdPost.status.in_(statuses)).order_by(NdPost.created_at.desc()).limit(limit))]
    creds = credentials.x_credentials(settings)
    state = get_connector_state(db, Platform.X)
    budget = PlatformBudget(db, settings)
    day_ago = now - timedelta(days=1)
    plain = float(settings.x_write_cost_estimate_usd)
    sent_30 = int(db.scalar(select(func.count()).select_from(NdPost).where(NdPost.status == "published", NdPost.published_at >= now - timedelta(days=30))) or 0)
    return {"settings": settings_out(s), "blocked": blocked_reason(s, now), "last_run": (s.state or {}).get("last_run"), "feed_problems": (s.state or {}).get("feed_problems") or {},
            "feed_health": (s.state or {}).get("feed_health") or {},
            "x": {"configured": creds.configured, "can_write": creds.user_context, "status": state.status, "account": state.account_username, "blocked_until": state.blocked_until.isoformat() if state.blocked_until else None},
            "writer": "model" if credentials.resolve("anthropic_api_key", settings) and settings.newsdesk_model else "headlines",
            "today": {"published": _sent(db, s, day_ago), "breaking": _sent(db, s, day_ago, "breaking"), "target": s.target_per_day},
            "cost": {"per_post_usd": plain, "posted_30d": sent_30, "estimate_30d_usd": round(sent_30 * plain, 2), "budget": budget.limits(), "x_spend_today_usd": budget.today()["cost"], "x_spend_month_usd": budget.month()["cost"]},
            "queue": rows("queued", limit=30), "published": rows("published", "dry_run", limit=50), "problems": rows("failed", "uncertain", limit=20), "desk": events.overview(db, now, s.state or {}),
            "x_lane": x_lane(db, s, now)}


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def x_lane(db: Session, s: NdSettings, now: datetime) -> dict[str, Any]:
    """X publishing at a glance: live or dry run, the last post X confirmed, the last error in X's words, and each recent event's X outcome."""
    last = db.scalars(select(NdPost).where(NdPost.status == "published", NdPost.x_id.is_not(None)).order_by(NdPost.published_at.desc()).limit(1)).first()
    rows = list(db.scalars(select(NdEvent).where(NdEvent.status == events.RELEASED, NdEvent.released_at >= now - timedelta(hours=48)).order_by(NdEvent.released_at.desc()).limit(12)))
    by_post = {p.id: p for p in db.scalars(select(NdPost).where(NdPost.id.in_([e.x_post_id for e in rows if e.x_post_id])))} if rows else {}
    out = []
    for e in rows:
        p = by_post.get(e.x_post_id or -1)
        out.append({"id": e.id, "headline": e.headline, "released_at": _iso(e.released_at), "telegram": e.tg_status, "telegram_message_id": e.tg_message_id,
                    "x": e.x_status, "note": e.x_note or (p.error if p else "") or "", "post_id": e.x_post_id, "x_id": p.x_id if p else None,
                    "url": f"https://x.com/i/status/{p.x_id}" if p and p.x_id else None, "post_status": p.status if p else None})
    err = (s.state or {}).get("x_last_error")
    return {"mode": s.mode, "live": s.mode == "live", "paused": s.pause_reason if s.paused_until and _utc_naive(s.paused_until, now) > now else None,
            "last_ok": {"at": _iso(last.published_at), "x_id": last.x_id, "url": f"https://x.com/i/status/{last.x_id}", "post_id": last.id} if last else None,
            "last_error": err, "events": out}


def _utc_naive(d: datetime, now: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=now.tzinfo)


# --- the owner's one test post (Admin › News Desk › X publishing) ---------------------------------------------------


def _test_event(db: Session, event_id: int | None) -> NdEvent | None:
    if event_id is not None:
        return db.get(NdEvent, event_id)
    return db.scalars(select(NdEvent).where(NdEvent.status == events.RELEASED, NdEvent.tg_status == "delivered").order_by(NdEvent.released_at.desc()).limit(1)).first()


def _test_text(ev: NdEvent, now: datetime) -> tuple[str, checks.Result]:
    """Exactly what the desk would write for this event on X, through the same checks."""
    b = events.brief_of(ev, now)
    named = list(dict.fromkeys(x["name"] for x in ev.sources or []))[:2]
    shown = [next(x for x in ev.sources if x["name"] == n) for n in named]
    text, links, source_text, names = x_words(ev, b, shown, named)
    return text, checks.check_post(text, source_text=source_text, source_names=names, breaking=False, links=links)


async def test_post_preview(db: Session, event_id: int | None, now: datetime) -> dict[str, Any] | None:
    """The text and picture a test post would carry. Sends nothing."""
    import base64

    from app.newsdesk import visual

    ev = _test_event(db, event_id)
    if ev is None:
        return None
    text, result = _test_text(ev, now)
    image, meta = await visual.for_event(ev, db=db, platform="x")
    mime = "image/jpeg" if image[:3] == b"\xff\xd8\xff" else "image/png"
    return {"event_id": ev.id, "headline": ev.headline, "text": text, "ok": result.ok, "reason": result.reason if not result.ok else "", "x_status": ev.x_status,
            "image": f"data:{mime};base64,{base64.b64encode(image).decode()}" if image else "", "visual": meta}


async def send_test_post(db: Session, s: NdSettings, settings: Settings, runtime: XRuntime | None, agent_id: int, event_id: int, now: datetime) -> NdPost:
    """One real post to X for this event, with its picture, even while the desk is in dry run. Only on the owner's click in Admin."""
    ev = _test_event(db, event_id)
    if ev is None:
        raise ValueError("No such event.")
    if ev.x_status in ("published", "uncertain"):
        raise ValueError("This event is already on X (or may be): pick another.")
    text, result = _test_text(ev, now)
    if not result.ok:
        raise ValueError(f"The X text failed the checks: {result.reason}")
    post = NdPost(kind="news", category=ev.category or "viral", priority="scheduled", status="queued", text=text, key=f"test:{ev.id}:{int(now.timestamp())}", fingerprint=checks.fingerprint(text),
                  score=0.0, facts={"event_id": ev.id, "test": True, "headline": ev.headline}, sources=ev.sources or [], checks=result.checks, metrics={})
    db.add(post)
    db.commit()
    await publish(db, s, post, settings, runtime, agent_id, now, force_live=True)
    db.refresh(post)
    if post.status in ("published", "uncertain"):
        _sync(ev, post)  # the desk never posts this event to X a second time
        db.commit()
    return post


def edit_post(db: Session, post: NdPost, text: str) -> checks.Result:
    """A person's words still can't include a link, a promise or a false BREAKING. They may add figures they checked themselves."""
    names = [x.get("name") for x in post.sources or [] if x.get("name")][:1]
    result = checks.check_post(text, source_text="", source_names=names, breaking=post.priority == "breaking", as_edited=True)
    if result.ok:
        post.original_text = post.original_text or post.text
        post.text, post.fingerprint, post.checks = text.strip(), checks.fingerprint(text), result.checks
    return result


def new_manual(db: Session, text: str) -> tuple[NdPost | None, checks.Result]:
    result = checks.check_post(text, source_text="", source_names=[], as_edited=True)
    if not result.ok:
        return None, result
    post = NdPost(kind="news", category="viral", priority="scheduled", status="queued", text=text.strip(), key=f"manual:{utcnow().timestamp()}", fingerprint=checks.fingerprint(text), score=99.0, facts={"manual": True}, sources=[], checks=result.checks, metrics={})
    db.add(post)
    db.flush()
    return post, result


