"""One tick of the pipeline, every few minutes, from the scheduler. Everything is in the database, so a restart loses nothing.

Publishing: a post is claimed by one conditional UPDATE before X is called, so two runs can't both send it. A timeout may have reached X, so it's
marked uncertain and never retried on its own. Rate limits and server errors retry with a wait. Refused credentials or no X credit pause the desk.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.x import XRuntime
from app.connectors.x.errors import XAuthError, XCreditsError, XError, XForbiddenError, XRateLimitError, XUnavailableError
from app.db.base import utcnow
from app.models.newsdesk import NdPost, NdSettings, NdStory
from app.newsdesk import checks, sources, writer

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
    "politics": {"tariff": 2.5, "sanction": 2.5, "trade deal": 2.0, "trade war": 2.0, "election": 1.5, "ceasefire": 2.0, "invasion": 2.5, "executive order": 2.0, "shutdown": 2.0, "summit": 1.0, "missile": 1.5},
    "viral": {"record high": 2.0, "all-time high": 2.0, "biggest ever": 2.0, "billionaire": 1.0, "record": 1.0, "collapse": 1.5},
}


# --- settings ------------------------------------------------------------------------------------------------------


def settings_row(db: Session) -> NdSettings:
    row = db.get(NdSettings, 1)
    if row is None:
        row = NdSettings(id=1, categories={c: True for c in sources.CATEGORIES}, feeds=sources.DEFAULT_FEEDS, watchlist=sources.DEFAULT_WATCHLIST, state={})
        db.add(row)
        db.flush()
    return row


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


async def collect(db: Session, s: NdSettings, client: httpx.AsyncClient, now: datetime) -> dict[str, Any]:
    fresh, problems = 0, {}
    seen = set(db.scalars(select(NdStory.link).where(NdStory.created_at >= now - timedelta(days=4))))
    for feed in s.feeds or []:
        items, problem = await sources.fetch_feed(client, feed)
        if problem:
            problems[feed["url"]] = problem
        for it in sources.recent(items, now):
            if it["link"] in seen:
                continue
            seen.add(it["link"])
            title = sources.clean_title(it["title"])
            if checks.NOT_NEWS.search(title) or checks.is_explainer(title):
                continue
            category, strength = classify(title, it.get("summary", ""), feed.get("category"))
            if category is None:
                continue
            db.add(NdStory(link=it["link"], title=title[:300], summary=(it.get("summary") or "")[:800], source=feed["name"], tier=int(feed.get("tier", 2)), category=category,
                           published_at=it["at"], fingerprint=checks.fingerprint(title), score=strength))
            fresh += 1
    db.query(NdStory).filter(NdStory.created_at < now - timedelta(days=7)).delete(synchronize_session=False)
    db.commit()
    return {"new_stories": fresh, "feed_problems": problems}


def corroboration(story: NdStory, recent_rows: list[NdStory]) -> list[NdStory]:
    """Other outlets carrying the same story."""
    return [o for o in recent_rows if o.id != story.id and o.source != story.source and checks.similarity(o.fingerprint, story.fingerprint) >= 0.34]


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
            source_text: str, names: list[str], expires: datetime | None, now: datetime) -> NdPost | None:
    """Deduplicate and check; only a post that passes everything enters the queue. The checks stay with it."""
    fp = checks.fingerprint(text)
    if duplicate(fp, posts) is not None:
        return None
    result = checks.check_post(text, source_text=source_text, source_names=names, breaking=priority == "breaking")
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
                candidate = {"key": key, "text": writer.move_post(item, window, change, q.price, as_of, big=big, volume=spike), "score": 5.0 + min(ratio, 4.0), "breaking": big,
                             "facts": {**facts, "window": window, "change_pct": round(change, 2), "volume_spike": spike}}
        if candidate is None and q.high_52w and q.price >= q.high_52w * 0.9995 and not on_cooldown(db, f"high:{item['symbol']}", COOLDOWN["extreme"], now):
            candidate = {"key": f"high:{item['symbol']}", "text": writer.extreme_post(item, True, q.price, as_of), "score": 6.0, "breaking": False, "facts": {**facts, "high_52w": q.high_52w}}
        if candidate is None and q.low_52w and q.price <= q.low_52w * 1.0005 and not on_cooldown(db, f"low:{item['symbol']}", COOLDOWN["extreme"], now):
            candidate = {"key": f"low:{item['symbol']}", "text": writer.extreme_post(item, False, q.price, as_of), "score": 6.0, "breaking": False, "facts": {**facts, "low_52w": q.low_52w}}
        step = item.get("step")
        if candidate is None and step and previous:
            below, above = int(previous // step), int(q.price // step)
            if below != above:
                up = above > below
                level = float(max(above, below) * step)
                key = f"mile:{item['symbol']}:{level:g}"
                if not on_cooldown(db, key, COOLDOWN["milestone"], now):
                    candidate = {"key": key, "text": writer.milestone_post(item, level, up, q.price, as_of), "score": 5.5, "breaking": False, "facts": {**facts, "level": level, "previous": previous}}
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
        text = writer.selloff_post(falling, latest.strftime("%d %b %H:%M"))
        made += enqueue(db, posts, kind="market", category="stocks", priority="breaking", text=text, key=f"selloff:{now:%Y%m%d}", score=8.0, facts={"indices": dict(falling), "as_of": latest.isoformat()},
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


async def plan_news(db: Session, s: NdSettings, settings: Settings, posts: list[NdPost], now: datetime, transport: Any = None) -> int:
    enabled = s.categories or {}
    threshold = SENSITIVITY.get(s.sensitivity, SENSITIVITY["medium"])["breaking"]
    rows = list(db.scalars(select(NdStory).where(NdStory.created_at >= now - timedelta(hours=36))))
    scored: list[tuple[float, NdStory, list[NdStory]]] = []
    for story in rows:
        if story.used or not enabled.get(story.category or "", False) or story.published_at is None:
            continue
        at = story.published_at if story.published_at.tzinfo else story.published_at.replace(tzinfo=now.tzinfo)
        others = corroboration(story, rows)
        scored.append((score_story(story.tier, story.score, len({o.source for o in others}), (now - at).total_seconds() / 3600), story, others))
    scored.sort(key=lambda r: -r[0])
    wanted = max(1, s.target_per_day // 3)
    made, writes = 0, 0
    queued = sum(1 for p in posts if p.status == "queued" and p.priority == "scheduled")
    for score, story, others in scored:
        at = story.published_at if story.published_at.tzinfo else story.published_at.replace(tzinfo=now.tzinfo)
        age = (now - at).total_seconds() / 3600
        verified = story.tier == 0 or len({o.source for o in others}) >= 1  # official, or a second outlet carries it
        breaking = score >= threshold and age <= 1.5 and verified
        if not breaking and (queued >= wanted or score < 3.5 or story.tier > 1 and score < 4.0):
            continue
        if writes >= 4:
            break
        if same_story(story.title, posts, now) is not None:
            story.used = True
            continue
        source_text = " ".join([story.title, story.summary or ""])
        text = None
        if not breaking:
            writes += 1
            text = await writer.rewrite(settings, story.title, story.summary or "", story.source, transport=transport)
            if text is not None and not checks.check_post(text, source_text=source_text, source_names=[story.source]).ok:
                logger.info("newsdesk: the model's text failed the checks; using the headline")
                text = None
        text = text or writer.headline_post(story.title, story.source, story.category or "viral", breaking)
        post = enqueue(db, posts, kind="news", category=story.category or "viral", priority="breaking" if breaking else "scheduled", text=text, key=f"story:{story.id}", score=score,
                       facts={"title": story.title, "corroborated_by": sorted({o.source for o in others}), "published_at": at.isoformat(), "official": story.tier == 0},
                       src=[{"name": x.source, "url": x.link, "at": x.published_at.isoformat() if x.published_at else None} for x in [story, *others][:5]],
                       source_text=source_text, names=[story.source], expires=now + timedelta(hours=12), now=now)
        story.used = True
        for o in others:
            o.used = True
        if post is not None:
            made += 1
            queued += 0 if breaking else 1
    db.commit()
    return made


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
    queued = list(db.scalars(select(NdPost).where(NdPost.status == "queued").order_by(NdPost.score.desc(), NdPost.id)))
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


async def publish(db: Session, s: NdSettings, post: NdPost, settings: Settings, runtime: XRuntime | None, agent_id: int, now: datetime) -> str:
    """Send one post, or record what a dry run would have sent. Returns its final status."""
    from app.social.guard import ActionRequest, SocialActionGuard
    from app.models.enums import Platform, SocialAction

    if not _claim(db, post):
        return "already claimed"
    db.refresh(post)
    if s.mode != "live":
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
        db.commit()
        return "blocked"
    from app.social.x_service import make_x_client

    client = make_x_client(db, settings, runtime)  # type: ignore[arg-type]
    try:
        permit = guard.issue_news_permit(req)
        data = await client.create_post(post.text, permit=permit)
    except XUnavailableError as e:
        if "in time" in str(e):
            post.status, post.error = "uncertain", "X didn't answer in time: it may have posted. Check X before resending."
        else:
            _retry(post, str(e), now)
        db.commit()
        return post.status
    except (XCreditsError, XAuthError, XForbiddenError) as e:
        post.status, post.error = "queued", str(e)[:300]
        s.paused_until, s.pause_reason = now + timedelta(hours=6), f"X refused the desk: {str(e)[:200]}"
        db.commit()
        logger.warning("newsdesk: paused: X refused it (%s)", type(e).__name__)
        return "paused"
    except XRateLimitError as e:
        _retry(post, str(e), now, minutes=30)
        db.commit()
        return post.status
    except XError as e:
        post.status, post.error = "failed", str(e)[:300]
        db.commit()
        return "failed"
    finally:
        await client.aclose()
    x_id = str(data.get("id") or "")
    if not x_id:
        post.status, post.error = "uncertain", "X answered without confirming a post id"
        db.commit()
        return post.status
    post.status, post.x_id, post.published_at, post.error = "published", x_id, now, None
    guard.record_attempt(req, None, None, "news", reason="news desk", external_id=x_id)
    db.commit()
    logger.info("newsdesk published %s", x_id)
    return "published"


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
        quotes = await sources.fetch_quotes(client, s.watchlist or [])
    posts = _recent_posts(db, now)
    made_markets = await detect_markets(db, s, quotes, posts, now, market)
    db.refresh(s)
    try:
        made_liq = detect_liquidations(db, s, posts, now)
    except Exception as e:  # noqa: BLE001 - the optional source never stops the rest
        logger.info("newsdesk: liquidations: %s", type(e).__name__)
        db.rollback()
        made_liq = 0
    made_news = await plan_news(db, s, settings, posts, now, writer_transport)
    made_snap = snapshot(db, s, quotes, posts, now)
    db.commit()
    result = None
    if why is None:
        post = choose(db, s, now)
        if post is not None:
            result = await publish(db, s, post, settings, runtime, agent_id, now)
    db.refresh(s)
    state = dict(s.state or {})
    out.update({"quotes": len(quotes), "of": len(s.watchlist or []), **collected, "queued_markets": made_markets + made_liq + made_snap, "queued_news": made_news, "published": result, "paused": why})
    state["last_run"] = {"at": now.isoformat(), **out}
    state["feed_problems"] = collected["feed_problems"]
    s.state = state
    db.commit()
    return out


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
            "max_breaking_per_day": s.max_breaking_per_day, "breaking_gap_minutes": s.breaking_gap_minutes, "sensitivity": s.sensitivity, "categories": s.categories, "feeds": s.feeds, "watchlist": s.watchlist}


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
            "x": {"configured": creds.configured, "can_write": creds.user_context, "status": state.status, "account": state.account_username, "blocked_until": state.blocked_until.isoformat() if state.blocked_until else None},
            "writer": "model" if credentials.resolve("anthropic_api_key", settings) and settings.newsdesk_model else "headlines",
            "today": {"published": _sent(db, s, day_ago), "breaking": _sent(db, s, day_ago, "breaking"), "target": s.target_per_day},
            "cost": {"per_post_usd": plain, "posted_30d": sent_30, "estimate_30d_usd": round(sent_30 * plain, 2), "budget": budget.limits(), "x_spend_today_usd": budget.today()["cost"], "x_spend_month_usd": budget.month()["cost"]},
            "queue": rows("queued", limit=30), "published": rows("published", "dry_run", limit=50), "problems": rows("failed", "uncertain", limit=20)}


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


