"""The shared editorial desk: one decision per story or market move, released once, delivered by X and Telegram each on its own.

Every few minutes (from whichever lane runs first) `decide`:
1. judges fresh stories with the evidence rules (app/newsdesk/chaos) and scores those that pass from 0 to 100 (app/newsdesk/virality);
2. measures live market moves from our own prices (app/newsdesk/moves), and the liquidation flushes the exchange feeds recorded;
3. folds other outlets and reworded headlines into the event they belong to, and turns real developments of a story already out into updates;
4. releases at most one event, by the plan below. A released event goes to both platforms; each records its own delivery.

The plan: 3-4 regular posts a day, one per six-hour window (00, 06, 12, 18 UTC), at least three hours apart. The bar
starts at 64 when a window opens and eases to 56 by its last 90 minutes: a strong story goes at once, a fair one only if nothing better came. A quiet
day posts fewer: nothing is ever posted to fill the plan. On top of it, an extraordinary event (strong evidence, fresh) may go out at
once (80+): at most 3 a day, 45 minutes from any other post, and never more than 7 posts in 24 hours in all. Any two posts are 20 minutes apart. One
asset's move goes out once in six hours (as news or as prices) unless it escalates; at most two posts on one topic in 12 hours unless
extraordinary. An update needs a real development (a bigger figure, an arrest, a halt, an official confirmation) and replies to the original.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent, NdStory
from app.newsdesk import brief, chaos, checks, engagement, headline, moves, sources, virality

logger = logging.getLogger(__name__)


@dataclass
class Policy:
    regular_per_day: int = 4
    window_hours: int = 6
    regular_gap: timedelta = timedelta(hours=3)
    regular_bar: int = 56  # the lowest a regular post may score, reached late in its window
    opening_bar: int = 64  # what a regular post must score at the start of a window: early on, something better may still come
    extra_bar: int = 80
    extra_per_day: int = 3
    extra_gap: timedelta = timedelta(minutes=45)
    extra_max_age: timedelta = timedelta(minutes=90)
    min_gap: timedelta = timedelta(minutes=20)
    ceiling: int = 7
    asset_cooldown: timedelta = timedelta(hours=6)
    topic_window: timedelta = timedelta(hours=12)
    topic_max: int = 2
    updates_per_story: int = 2
    update_gap: timedelta = timedelta(minutes=30)
    news_ttl: timedelta = timedelta(hours=6)  # a story older than this isn't released
    market_ttl: timedelta = timedelta(minutes=45)
    deliver_news: timedelta = timedelta(hours=2)  # a platform that can't deliver in this time misses it
    deliver_market: timedelta = timedelta(minutes=40)


POLICY = Policy()
STRONG = ("official", "two outlets", "price data", "exchange data")
RELEASED = "released"
FAMILIES = ({"hack", "scam", "failure"}, {"controversy"})  # Bybaz hacked, then Bybaz halts withdrawals: one story
ESCALATE = re.compile(r"\b(lazarus|north korea\w*|behind the|blamed?|attribut\w+|(?:reopen|resum)\w* withdrawals|outflows|refund\w*|arrest\w*|charged|indict\w*|bankrupt\w*|insolven\w*|(?:halt|paus|suspend|freez)\w* (?:all )?withdrawals|seiz\w+|frozen|confirms?|confirmed|"
                      r"recover\w*|returns? (?:the )?funds|delist\w*|sentenc\w*|guilty|shut(?:s|ting)? down|collaps\w*|liquidat\w+|depeg\w*|steps? down|stepped down|resign\w*|ousted|fired|launder\w*)\b", re.I)


NUMBERS = ("regular_per_day", "regular_bar", "opening_bar", "extra_bar", "extra_per_day", "ceiling", "window_hours", "topic_max", "updates_per_story")
MINUTES = ("regular_gap", "extra_gap", "min_gap", "asset_cooldown")


def policy(desk_state: dict[str, Any] | None = None) -> Policy:
    """The plan, with any override stored in the desk's state.policy: the numbers above as integers, and the gaps as "<name>_minutes".
    window_hours 0 turns the six-hour windows off."""
    p = Policy()
    for k, v in ((desk_state or {}).get("policy") or {}).items():
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            continue
        if k in NUMBERS and v <= 100:
            setattr(p, k, v)
        elif k.endswith("_minutes") and k[:-8] in MINUTES and v <= 2880:
            setattr(p, k[:-8], timedelta(minutes=v))
    if p.window_hours and 24 % p.window_hours:
        p.window_hours = 6
    return p


def _utc(at: datetime | None, now: datetime) -> datetime | None:
    return at if at is None or at.tzinfo else at.replace(tzinfo=now.tzinfo)


def window_start(now: datetime, hours: int) -> datetime:
    return now.replace(hour=now.hour - now.hour % hours, minute=0, second=0, microsecond=0)


# --- what each event is --------------------------------------------------------------------------------------------


def brief_of(ev: NdEvent, now: datetime) -> brief.Brief:
    """The shared format's view of an event, for either platform's text and the card."""
    f = ev.facts or {}
    first = _utc(datetime.fromisoformat(f["published_at"]), now) if f.get("published_at") else _utc(ev.first_seen, now)
    age = max(0, int((now - first).total_seconds() // 60)) if first else 0
    return brief.Brief(int(ev.virality), float(ev.virality), ev.label, ev.topic, list(f.get("tickers") or []), str(f.get("flag") or ""), True, bool(f.get("alleged")), age,
                       [virality.describe(ev.parts or {})], bool(f.get("falling")))


def _stat(title: str, tag: str, measured: tuple[str, float, float] | None) -> str:
    """The one figure the card shows large: a dollar amount from the story, or the measured move."""
    m = brief.AMOUNT.search(title or "")
    if m:  # "$1 billion" -> "$1B", "$46 million" -> "$46M": the source's own figure, written short
        return f"${m.group(1)}{'B' if m.group(2).lower() in ('billion', 'bn', 'b') else 'M'}"
    if measured:
        return f"{'+' if measured[1] > 0 else '-'}{abs(measured[1]):.1f}%"
    pm = brief.PERCENT.search(title or "")
    return pm.group(0) if pm else ""


def _engagement_points(adj: dict[str, float], topic: str) -> float:
    return float(adj.get(topic, 0.0))


def score_story(v: chaos.Verdict, quotes: dict[str, sources.Quote], now: datetime, adj: dict[str, float]) -> tuple[virality.Score, dict[str, Any], str]:
    """(score, facts, headline) for a story that passed the evidence rules."""
    story, b = v.story, v.brief
    published = _utc(story.published_at, now)
    outlets = {o.source for o in v.others if o.source != story.source}
    near = {o.source for o in v.others if o.source != story.source and o.published_at and published and abs((_utc(o.published_at, now) - published).total_seconds()) <= 3 * 3600}
    measured = None
    tag = ""
    spark: list[float] = []
    claim = chaos.claim_of(story.title or "")
    lead = len(" ".join((story.title or "").split()[:3]))
    if claim is not None and claim.at < lead and published is not None:
        q = quotes.get(claim.symbol)
        if q is not None and chaos.verify(claim, q, published)[0] == "confirmed":
            measured = moves.biggest(q, {"group": chaos.GROUP.get(claim.symbol, "stock")}, published)
            if measured and (claim.direction == 0 or (measured[1] > 0) == (claim.direction > 0)):
                tag = headline.price_tag(claim.asset, measured[0], measured[1])
                spark = moves.spark(q, published)
            else:
                measured = None
    s = virality.news(title=story.title or "", summary=story.summary or "", evidence=v.evidence, lead_topic=b.topic, outlets=len(outlets), outlets_3h=len(near),
                      age_minutes=max(0.0, (now - published).total_seconds() / 60) if published else 999, alleged=b.alleged, tickers=b.tickers,
                      measured_ratio=measured[2] if measured else 0.0, engagement=_engagement_points(adj, b.topic))
    head = headline.news(story.title or "", tag=tag)
    told = " ".join(f"{x.title} {x.summary or ''}" for x in [story, *v.others]) + f" {v.price_check} {tag}"
    assets = [claim.asset] if claim is not None and claim.at < lead else []  # the asset the headline leads with: "Bitcoin ... as Wall Street sells off" is Bitcoin's
    facts = {"tickers": b.tickers, "flag": b.flag, "alleged": b.alleged, "falling": b.falling, "risk": b.topic in ("hack", "scam", "failure"), "price_check": v.price_check,
             "price_tag": tag, "told": told[:4000], "assets": assets, "stat": _stat(story.title or "", tag, measured), "spark": spark,
             "published_at": published.isoformat() if published else None, "story_ids": [story.id, *[o.id for o in v.others]], "outlets": sorted({story.source, *outlets})}
    return s, facts, head


def _sources(v: chaos.Verdict) -> list[dict[str, Any]]:
    out, names = [], set()
    for x in [v.story, *sorted(v.others, key=lambda o: (o.tier, o.published_at or datetime.max))]:
        if x.source not in names and (x.link or "").startswith("https://") and len(out) < 3:
            names.add(x.source)
            out.append({"name": x.source, "url": x.link, "at": x.published_at.isoformat() if x.published_at else None})
    return out


def _summary(v: chaos.Verdict) -> str:
    from app.newsdesk.telegram import _from_feed

    return brief.summary_short(_from_feed(v.story, v.others)[1])


# --- clustering and updates ----------------------------------------------------------------------------------------


def _same(ev: NdEvent, story: NdStory, links: set[str]) -> bool:
    """One story: the same link, a close headline, or the same names (two of them, or one and the same kind of event: Binance and a hack)."""
    if ev.key == f"story:{story.link}" or story.link in links:
        return True
    mine, theirs = checks.names(story.title), checks.names(ev.title)
    if mine and theirs and not mine & theirs:
        return False  # Bybaz hacked and Qorvex hacked are two stories, however alike the wording
    shared_names = mine & theirs
    family = next((f for f in FAMILIES if ev.topic in f), {ev.topic})
    if shared_names and ev.topic and ev.topic in ("hack", "scam", "failure", "controversy") and family & set(virality.topics_in(story.title or "")):
        return True
    if checks.similarity(checks.fingerprint(ev.title), story.fingerprint) >= 0.34:
        return True
    shared = checks.names(story.title) & checks.names(ev.title)
    near = checks.similarity(checks.fingerprint(ev.title), story.fingerprint)
    return len(shared) >= 2 or (bool(shared) and near >= 0.15)


def development(story: NdStory, parent: NdEvent) -> str:
    """What's materially new in this story against the event already out, or "" if it's the same news again."""
    told = str((parent.facts or {}).get("told") or parent.title)
    new_usd, old_usd = brief._amount_usd(story.title or ""), brief._amount_usd(told)
    if new_usd and old_usd and new_usd >= old_usd * 1.25:
        return "a bigger figure"
    words = {w.lower() for w in ESCALATE.findall(story.title or "")} - {w.lower() for w in ESCALATE.findall(told)}
    if words:
        return f"new: {sorted(words)[0]}"
    if story.tier == 0 and parent.evidence != "official":
        return "an official source"
    return ""


# --- decide --------------------------------------------------------------------------------------------------------


async def decide(db: Session, now: datetime, *, quotes: dict[str, sources.Quote], watchlist: list[dict[str, Any]] | None = None, categories: dict[str, bool] | None = None,
                 market: Any = None, second_opinion: Any = None, desk_state: dict[str, Any] | None = None, liquidations: bool = True) -> dict[str, Any]:
    """One pass of the desk. Safe to run from both lanes: event keys are unique and release is one row at a time."""
    p = policy(desk_state)
    adj = engagement.adjustments(db, now)
    recent = list(db.scalars(select(NdEvent).where(NdEvent.created_at >= now - timedelta(hours=72))))
    out = {"stories": 0, "markets": 0, "updates": 0, "released": None}
    _expire(db, recent, now)
    # stories
    rows = list(db.scalars(select(NdStory).where(NdStory.created_at >= now - timedelta(hours=36))))
    ready, _ = chaos.candidates(rows, quotes, now, max_age=p.news_ttl, categories=categories)
    for v in ready:
        out["stories"] += _offer_story(db, v, recent, quotes, now, adj, p)
    # market moves
    enabled = categories or {}
    for m in moves.detect(quotes, sources.DEFAULT_WATCHLIST if watchlist is None else watchlist, now):
        if not enabled.get(str(m.item.get("category") or "crypto"), True):
            continue
        out["markets"] += await _offer_move(db, m, recent, now, adj, p, second_opinion, market)
    if liquidations and enabled.get("crypto", True):
        try:
            out["markets"] += _offer_liquidations(db, recent, now, adj, p)
        except Exception as e:  # noqa: BLE001 - the optional source never stops the rest
            logger.info("newsdesk events: liquidations: %s", type(e).__name__)
            db.rollback()
            recent = list(db.scalars(select(NdEvent).where(NdEvent.created_at >= now - timedelta(hours=36))))
    db.commit()
    out["updates"] = sum(1 for e in recent if e.kind == "update" and e.status == "candidate")
    released = release(db, now, p)
    out["released"] = released.id if released is not None else None
    db.commit()
    return out


def _expire(db: Session, recent: list[NdEvent], now: datetime) -> None:
    for e in recent:
        if e.status == "candidate" and e.expires_at is not None and _utc(e.expires_at, now) <= now:
            e.status = "missed"
            e.why = e.why or "no slot before it went stale"


def _insert(db: Session, recent: list[NdEvent], ev: NdEvent) -> bool:
    try:
        with db.begin_nested():
            db.add(ev)
            db.flush()
    except IntegrityError:
        return False
    recent.append(ev)
    return True


def _offer_story(db: Session, v: chaos.Verdict, recent: list[NdEvent], quotes: dict[str, sources.Quote], now: datetime, adj: dict[str, float], p: Policy) -> int:
    story = v.story
    links = {story.link, *[o.link for o in v.others]}
    match = None
    for ev in sorted(recent, key=lambda e: e.id or 0):
        horizon = timedelta(hours=72) if ev.status == RELEASED else timedelta(hours=24)  # a story out there can develop for three days
        if ev.kind in ("news", "update") and _same(ev, story, {str(x.get("url")) for x in ev.sources or []}) and _utc(ev.created_at, now) >= now - horizon:
            match = ev if ev.parent_id is None else (db.get(NdEvent, ev.parent_id) or ev)  # an update's story is its original's
            break
    if match is None and any(r.key == f"story:{story.link}" for r in recent):
        return 0
    score, facts, head = score_story(v, quotes, now, adj)
    if match is not None and match.status != RELEASED:
        lead_story = ((match.facts or {}).get("story_ids") or [None])[0]
        if match.status == "candidate" and (lead_story == story.id or score.total > match.virality):
            _fill(match, v, score, facts, head, now)  # the leading story scored now (it fades as it ages), or a stronger telling of it
        elif match.status == "candidate":
            match.sources = _merge_sources(match.sources, _sources(v))
            match.facts = {**(match.facts or {}), "outlets": sorted(set((match.facts or {}).get("outlets") or []) | set(facts["outlets"]))}
        return 0
    if match is not None:
        if any(story.link == str(x.get("url")) for e in recent if e.id == match.id or e.parent_id == match.id for x in e.sources or []):
            return 0
        what = development(story, match)
        kids = list(db.scalars(select(NdEvent).where(NdEvent.parent_id == match.id, NdEvent.status.in_(("candidate", RELEASED)))))
        if not what or len(kids) >= p.updates_per_story or any(story.link == str(x.get("url")) for k in kids for x in k.sources or []):
            return 0
        ev = NdEvent(key=f"update:{story.link}"[:200], kind="update", parent_id=match.id, first_seen=now, expires_at=now + p.news_ttl / 2, status="candidate")
        _fill(ev, v, score, facts, head, now)
        ev.facts = {**ev.facts, "development": what, "assets": []}
        ev.topic = match.topic or ev.topic
        _inherit_magnitude(ev, match)
        return 1 if _insert(db, recent, ev) else 0
    published = _utc(story.published_at, now) or now
    ev = NdEvent(key=f"story:{story.link}"[:200], kind="news", first_seen=now, expires_at=published + p.news_ttl, status="candidate")
    _fill(ev, v, score, facts, head, now)
    return 1 if _insert(db, recent, ev) else 0


def _inherit_magnitude(ev: NdEvent, parent: NdEvent) -> None:
    """An update is as big as its story: "Binance CFO steps down after record hack" carries the $6.3B of the hack it follows, though its own
    headline names no figure. Only the magnitude is inherited; severity, evidence and freshness stay the update's own."""
    parts = dict(ev.parts or {})
    own, theirs = float(parts.get("magnitude") or 0), float((parent.parts or {}).get("magnitude") or 0)
    if theirs <= own:
        return
    fresh = float(parts.get("freshness") or 1.0)
    parts["magnitude"] = theirs
    ev.parts = parts
    ev.virality = float(max(0, min(100, int(round(ev.virality + (theirs - own) * fresh)))))


def _merge_sources(a: list[Any], b: list[Any]) -> list[Any]:
    out, names = [], set()
    for x in [*(a or []), *(b or [])]:
        if x.get("name") not in names and len(out) < 3:
            names.add(x.get("name"))
            out.append(x)
    return out


def _fill(ev: NdEvent, v: chaos.Verdict, score: virality.Score, facts: dict[str, Any], head: str, now: datetime) -> None:
    b = v.brief
    ev.title, ev.headline, ev.summary = (v.story.title or "")[:300], head[:300], _summary(v)
    ev.topic, ev.evidence, ev.category = b.topic, v.evidence, v.story.category or "viral"
    ev.virality, ev.parts, ev.facts, ev.sources = float(score.total), score.parts, facts, _sources(v)


async def _offer_move(db: Session, m: moves.Move, recent: list[NdEvent], now: datetime, adj: dict[str, float], p: Policy, second_opinion: Any, market: Any) -> int:
    assets = sorted({moves.KEYS.get(sym, sym) for sym in ([m.symbol] if m.kind != "wide" else [])} | ({"BTC", "ETH", "SOL", "XRP"} if m.symbol == "CRYPTO" else set())
                    | ({"SPX", "IXIC", "DJI"} if m.symbol == "US-STOCKS" else set()))
    direction = "up" if m.up else "down"
    if m.kind == "milestone" and any((e.facts or {}).get("kind") == "milestone" and (e.facts or {}).get("symbol") == m.symbol and (e.facts or {}).get("level") == m.level for e in recent):
        return 0  # one post per level per asset in three days, whichever way it crossed
    # the same asset: nothing new for six hours, and the same way for a day unless the move escalates ("oil -9.5% in 24h" again at -9.8% isn't news)
    same = [e for e in recent if e.kind in ("market", "update", "news") and set((e.facts or {}).get("assets") or []) & set(assets)
            and _utc(e.created_at, now) >= now - (timedelta(hours=24) if (e.facts or {}).get("direction", direction) == direction else p.asset_cooldown)]
    live = [e for e in same if e.status == "candidate" and e.kind == "market"]
    out_already = [e for e in same if e.status == RELEASED]
    parent = None
    if out_already:
        last = max(out_already, key=lambda e: _utc(e.released_at, now) or now)
        prev = float((last.facts or {}).get("ratio") or 0)
        same_way = (last.facts or {}).get("direction") == direction
        if not (m.kind in ("move", "wide") and same_way and prev and m.ratio >= prev + 1 and last.kind == "market"):
            return 0  # one move per asset per six hours, unless it has escalated
        if db.scalar(select(func.count()).select_from(NdEvent).where(NdEvent.parent_id == last.id, NdEvent.status.in_(("candidate", RELEASED)))) >= p.updates_per_story:
            return 0
        parent = last
    if second_opinion is not None and m.item.get("hl"):
        ok, detail = await second_opinion(m.item, m.price, market)
        if not ok:
            logger.info("newsdesk events: %s not verified: %s", m.symbol, detail)
            return 0
        verified = detail
    elif m.symbol == "CRYPTO":
        verified = "several assets, one price source (Yahoo Finance)"
    else:
        verified = "one price source (Yahoo Finance)" if not m.item.get("hl") else "one price source (Yahoo Finance): no second opinion available"
    two = "Hyperliquid" in verified
    round_level = m.kind == "milestone" and m.level > 0 and (m.level / (m.item.get("step") or m.level)) % 10 == 0
    score = virality.market(kind=m.kind, group=str(m.item.get("group") or "stock"), ratio=m.ratio, two_sources=two, symbol_key=m.key, round_level=round_level,
                            engagement=_engagement_points(adj, "move"))
    head = headline.market(m)
    stamp = m.as_of.strftime("%d %b %H:%M")
    told = f"{head} {stamp} {m.price} {m.change} {m.level} " + " ".join(f"{n} {c:.1f}" for n, c in m.members)
    tk = moves.CASHTAG.get(m.symbol)
    names = {"Bitcoin": "BTC", "Ethereum": "ETH", "Solana": "SOL", "XRP": "XRP"}
    tickers = [tk] if tk else [names[n] for n, _ in m.members if n in names][:3]
    facts = {"symbol": m.symbol, "assets": assets, "kind": m.kind, "window": m.window, "change_pct": round(m.change, 2), "ratio": round(m.ratio, 2), "price": m.price,
             "as_of": m.as_of.isoformat(), "level": m.level, "members": m.members, "direction": direction, "verified": verified, "tickers": tickers, "falling": not m.up,
             "risk": False, "told": told, "spark": m.spark, "stat": f"{'+' if m.change > 0 else '-'}{abs(m.change):.1f}%" if m.kind in ("move", "wide") else writer_money(m),
             "published_at": m.as_of.isoformat(), "flag": "🇺🇸" if m.symbol == "US-STOCKS" else ""}
    url = f"https://finance.yahoo.com/quote/{m.symbol}" if m.symbol not in ("CRYPTO", "US-STOCKS") else "https://finance.yahoo.com/markets/"
    src = [{"name": "Yahoo Finance", "url": url, "at": m.as_of.isoformat()}]
    if live:
        ev = max(live, key=lambda e: e.virality)  # the move is still on: the waiting post always carries the latest price and time
        ev.title = ev.headline = head
        ev.virality, ev.parts, ev.facts, ev.sources = float(score.total), score.parts, {**facts, "development": (ev.facts or {}).get("development", "")}, src
        ev.summary = _market_summary(facts, m)
        ev.expires_at = now + p.market_ttl
        return 0
    key = f"move:{m.key}:{m.kind}:{direction}:{now:%Y%m%d%H}" if parent is None else f"move-update:{m.key}:{parent.id}:{m.window}:{now:%Y%m%d%H%M}"
    ev = NdEvent(key=key[:200], kind="update" if parent else "market", parent_id=parent.id if parent else None, title=head, headline=head, summary=_market_summary(facts, m),
                 topic="move", label="", evidence="price data", category=str(m.item.get("category") or "crypto"), virality=float(score.total), parts=score.parts, sources=src,
                 facts={**facts, "development": "the move has deepened" if parent and not m.up else "the move has extended" if parent else ""}, status="candidate",
                 first_seen=now, expires_at=now + p.market_ttl)
    return 1 if _insert(db, recent, ev) else 0


def writer_money(m: moves.Move) -> str:
    from app.newsdesk import writer

    return writer.plain(m.price) if m.item.get("group") == "index" else writer.usd(m.price)


def _market_summary(facts: dict[str, Any], m: moves.Move) -> str:
    stamp = m.as_of.strftime("%H:%M")
    if "Hyperliquid" in str(facts.get("verified") or ""):
        return f"Price as of {stamp} UTC from Yahoo Finance, matched against Hyperliquid."
    return f"Price from Yahoo Finance as of {stamp} UTC."


def _offer_liquidations(db: Session, recent: list[NdEvent], now: datetime, adj: dict[str, float], p: Policy) -> int:
    """The flushes Price Chat's exchange feeds recorded: only a big one (three times the source's own threshold in an hour)."""
    from app.brand_manager.sources import LIQ_HOUR_USD, liquidation_topics

    made = 0
    for t in liquidation_topics(db, now):
        data = t.brief or {}
        total = float((data.get("numbers") or {}).get("liquidations_1h_usd") or 0)
        head = str(data.get("headline") or "")
        coin = str((data.get("coin") or {}).get("symbol") or "")
        if total < LIQ_HOUR_USD * 3 or not head or not coin:
            continue
        if any(f"LIQ:{coin}" in ((e.facts or {}).get("assets") or []) and _utc(e.created_at, now) >= now - timedelta(hours=3) for e in recent):
            continue
        score = virality.liquidations(usd=total, coin=coin, engagement=_engagement_points(adj, "liquidations"))
        text = brief.clean_headline(head)
        ev = NdEvent(key=f"liq:{coin}:{now:%Y%m%d%H}", kind="market", title=head[:300], headline=text[:300], summary="Data: public exchange liquidation feeds.", topic="liquidations",
                     evidence="exchange data", category="crypto", virality=float(score.total), parts=score.parts,
                     sources=[{"name": "public exchange feeds", "url": "https://pricechat.live", "at": now.isoformat()}],
                     facts={"assets": [f"LIQ:{coin}"], "usd": total, "tickers": [coin], "told": f"{head} {total}", "stat": brief.AMOUNT.search(head).group(0) if brief.AMOUNT.search(head) else "",
                            "published_at": now.isoformat(), "falling": True, "risk": False, "flag": ""},
                     status="candidate", first_seen=now, expires_at=now + p.market_ttl)
        made += 1 if _insert(db, recent, ev) else 0
    return made


# --- release -------------------------------------------------------------------------------------------------------


def release(db: Session, now: datetime, p: Policy = POLICY) -> NdEvent | None:
    """At most one event per pass. The best candidate that fits the plan goes; the others say why they wait."""
    out = list(db.scalars(select(NdEvent).where(NdEvent.status == RELEASED, NdEvent.released_at >= now - timedelta(hours=24))))
    waiting = sorted(db.scalars(select(NdEvent).where(NdEvent.status == "candidate")), key=lambda e: (-e.virality, e.id))
    if not waiting:
        return None
    times = sorted(_utc(e.released_at, now) for e in out)
    last_any = times[-1] if times else None
    regular = [e for e in out if e.slot == "regular"]
    extra = [e for e in out if e.slot == "extra"]
    window = window_start(now, p.window_hours) if p.window_hours else None
    into = (now - window).total_seconds() / (p.window_hours * 3600) if window is not None else 1.0
    bar = round(p.opening_bar - (p.opening_bar - p.regular_bar) * min(1.0, into / 0.75))
    last_regular = max((_utc(e.released_at, now) for e in regular), default=None)
    for ev in waiting:
        f = ev.facts or {}
        age = _age(ev, now)
        if ev.kind == "update":
            parent = db.get(NdEvent, ev.parent_id) if ev.parent_id else None
            if parent is None or parent.status != RELEASED:
                ev.why = "its original never went out"
                continue
            if now - (_utc(parent.released_at, now) or now) < p.update_gap:
                ev.why = "too soon after the original"
                continue
        assets = set(f.get("assets") or [])
        if ev.kind != "update" and assets and any(assets & set((e.facts or {}).get("assets") or []) and now - _utc(e.released_at, now) < p.asset_cooldown for e in out):
            ev.why = "this asset's move went out within six hours"
            continue
        topical = sum(1 for e in out if e.topic == ev.topic and now - _utc(e.released_at, now) < p.topic_window)
        strong = ev.evidence in STRONG
        reasons = []
        slot = ""
        if len(out) >= p.ceiling:
            ev.why = f"{len(out)} posts in 24 hours, the ceiling"
            continue
        if last_any is not None and now - last_any < p.min_gap:
            ev.why = "under 20 minutes since the last post"
            continue
        # a regular slot
        if ev.virality < bar:
            reasons.append(f"scores {ev.virality:g}, under the bar of {bar} at this point in the window")
        elif len(regular) >= p.regular_per_day:
            reasons.append(f"{len(regular)} regular posts in 24 hours already")
        elif window is not None and any(_utc(e.released_at, now) >= window for e in regular):
            reasons.append("this six-hour window has had its post")
        elif last_regular is not None and now - last_regular < p.regular_gap:
            reasons.append("under three hours since the last regular post")
        elif topical >= p.topic_max:
            reasons.append(f"{topical} {ev.topic or 'other'} posts in 12 hours")
        else:
            slot = "regular"
        # or an extraordinary one, outside the plan
        if not slot and ev.virality >= p.extra_bar and strong and age <= p.extra_max_age.total_seconds() / 60:
            if len(extra) >= p.extra_per_day:
                reasons.append(f"{len(extra)} extraordinary posts in 24 hours already")
            elif last_any is not None and now - last_any < p.extra_gap:
                reasons.append("under 45 minutes since the last post")
            else:
                slot = "extra"
        if not slot:
            ev.why = "; ".join(reasons) or "waiting"
            continue
        risk = bool(f.get("risk"))
        label = brief.UPDATE if ev.kind == "update" else virality.label_for(int(ev.virality), topic=ev.topic, strong=strong, age_minutes=age, risk=risk)
        problem = preflight(ev, label, now)
        if problem:  # both platforms must be able to carry it, or neither does: they stay in step
            ev.status, ev.why = "rejected", f"failed the checks: {problem}"
            continue
        ev.status, ev.slot, ev.released_at, ev.label = RELEASED, slot, now, label
        ev.why = ("extraordinary: " if slot == "extra" else "") + f"scores {ev.virality:g} ({virality.describe(ev.parts or {})})"
        ev.x_status = ev.tg_status = "pending"
        logger.info("newsdesk events: released #%s [%s %s %s]: %s", ev.id, slot, int(ev.virality), ev.evidence, ev.headline[:100])
        return ev
    return None


def preflight(ev: NdEvent, label: str, now: datetime) -> str:
    """Write the event for X and for Telegram and run the desk's checks on both, before it's released. A summary that fails is dropped
    (the headline can stand alone); a headline that fails stops the event. Returns the problem, or ""."""
    from app.newsdesk.telegram import MAX_LEN

    keep = ev.label
    ev.label = label
    try:
        b = brief_of(ev, now)
        f = ev.facts or {}
        names = [x["name"] for x in ev.sources or []][:3]
        told = f"{f.get('told') or ev.title} {ev.headline} {ev.summary} {' '.join(b.tickers)}"
        x = brief.x_text(ev.headline, (ev.sources or [])[:2], b, ev.key)
        rx = checks.check_post(x, source_text=told, source_names=names[:2], breaking=label == brief.BREAKING)
        if not rx.ok:
            return f"X: {rx.reason}"
        srcs = [{"name": s["name"], "url": s["url"]} for s in ev.sources or []][:3]
        for summary in ([ev.summary, ""] if ev.summary else [""]):
            _, plain, _ = brief.telegram_html(ev.headline, summary, srcs, b, ev.key, [])
            rt = brief.check(plain, source_text=told, names=names, brief=b, max_len=MAX_LEN)
            if rt.ok:
                ev.summary = summary
                return ""
        return f"Telegram: {rt.reason}"
    finally:
        ev.label = keep


def _age(ev: NdEvent, now: datetime) -> float:
    f = ev.facts or {}
    at = _utc(datetime.fromisoformat(f["published_at"]), now) if f.get("published_at") else _utc(ev.first_seen, now)
    return max(0.0, (now - at).total_seconds() / 60) if at else 0.0


# --- what each platform delivers -----------------------------------------------------------------------------------


def pending(db: Session, platform: str, now: datetime) -> list[NdEvent]:
    """Released events this platform still owes, oldest first. One it can no longer deliver in time is marked missed, for this platform only."""
    col = NdEvent.x_status if platform == "x" else NdEvent.tg_status
    rows = list(db.scalars(select(NdEvent).where(NdEvent.status == RELEASED, col.in_(("pending", "queued")), NdEvent.released_at >= now - timedelta(hours=12)).order_by(NdEvent.released_at, NdEvent.id)))
    out = []
    for ev in rows:
        limit = POLICY.deliver_market if ev.kind == "market" or (ev.facts or {}).get("symbol") else POLICY.deliver_news
        if now - _utc(ev.released_at, now) > limit and getattr(ev, f"{'x' if platform == 'x' else 'tg'}_status") == "pending":
            mark(ev, platform, "missed", "not delivered in time")
            continue
        out.append(ev)
    db.commit()
    return out


def mark(ev: NdEvent, platform: str, status: str, note: str = "", *, post_id: int | None = None, message_id: int | None = None) -> None:
    if platform == "x":
        ev.x_status, ev.x_note = status, note[:300]
        if post_id is not None:
            ev.x_post_id = post_id
    else:
        ev.tg_status, ev.tg_note = status, note[:300]
        if post_id is not None:
            ev.tg_post_id = post_id
        if message_id is not None:
            ev.tg_message_id = message_id


def event_out(ev: NdEvent) -> dict[str, Any]:
    return {"id": ev.id, "kind": ev.kind, "parent_id": ev.parent_id, "headline": ev.headline, "label": ev.label, "topic": ev.topic, "evidence": ev.evidence, "virality": ev.virality,
            "why": ev.why, "status": ev.status, "slot": ev.slot, "sources": ev.sources, "released_at": ev.released_at.isoformat() if ev.released_at else None,
            "x": {"status": ev.x_status, "note": ev.x_note, "post_id": ev.x_post_id}, "telegram": {"status": ev.tg_status, "note": ev.tg_note, "message_id": ev.tg_message_id}}


def overview(db: Session, now: datetime) -> dict[str, Any]:
    rows = list(db.scalars(select(NdEvent).where(NdEvent.created_at >= now - timedelta(hours=48)).order_by(NdEvent.id.desc()).limit(60)))
    day = [e for e in rows if e.status == RELEASED and e.released_at and _utc(e.released_at, now) >= now - timedelta(hours=24)]
    return {"plan": {"regular_per_day": POLICY.regular_per_day, "regular_bar": POLICY.regular_bar, "extra_bar": POLICY.extra_bar, "extra_per_day": POLICY.extra_per_day, "ceiling": POLICY.ceiling},
            "released_24h": {"regular": sum(1 for e in day if e.slot == "regular"), "extra": sum(1 for e in day if e.slot == "extra")}, "events": [event_out(e) for e in rows]}
