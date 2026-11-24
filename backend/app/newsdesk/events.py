"""The shared editorial desk: one decision per story or market move, released once, delivered by X and Telegram each on its own.

SCHEDULE (owner, 2026-11-09, Policy.schedule = "slots", the default): exactly six posts a day, at 00:00, 04:00, 08:00, 12:00, 16:00 and
20:00 Dubai time. Each slot carries the best verified story that clears regular_bar (Bitcoin, Ethereum, Hyperliquid, macro, commodities
and geopolitics rank first); if none does, a market check made only of measured prices (app/newsdesk/recap). Nothing is posted between
slots. A slot the server couldn't fill within slot_catch_up is missed, never posted late, and the owner is told. The "windows" plan
described below is kept for state.policy.schedule = "windows".

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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent, NdStory
from app.newsdesk import brief, chaos, checks, editorial, engagement, headline, livedata, moves, newsworthy, quality, safety, sources, urgency, virality
from app.newsdesk import importance

logger = logging.getLogger(__name__)


@dataclass
class Policy:
    regular_per_day: int = 4
    window_hours: int = 6
    regular_gap: timedelta = timedelta(hours=3)
    regular_bar: int = 56  # the lowest a regular post may score, reached late in its window
    # owner, 2026-11-10: important stories were losing their scores while they waited for a slot (Ukraine talks 67 -> 43, Saudi pipeline
    # 56 -> 31). Verified important news (verified_important) keeps its eligibility up to hold_hours, its best score fading hold_decay a
    # point an hour after the first slot interval, and is rechecked (significance, repeats, diversity, the checks) when its slot comes.
    # owner, 2026-11-15 (today's news first): 6 h, not 10. Past six hours a story is stale; only a real development (an update) goes.
    hold_hours: timedelta = timedelta(hours=6)
    hold_decay: float = 1.0
    # major breaking news, confirmed by two or more independent credible outlets, goes at once instead of waiting for a slot
    # owner, 2026-11-10: 70, and only clear financial, market or major geopolitical significance (editorial.breaking_worthy); routine
    # political controversy never breaks ("Pete Hegseth decries impeachment push" broke at 67 in the replay)
    breaking_bar: int = 70  # its score now (fresh), not a remembered best
    controversy_penalty: int = 12  # regular slots: routine political controversy without market/geopolitical consequence ranks this much lower
    breaking_max_age: timedelta = timedelta(minutes=90)  # from first publication: breaking means it just happened
    breaking_per_day: int = 2
    breaking_gap: timedelta = timedelta(minutes=90)  # since the last post of any kind
    breaking_before_slot: timedelta = timedelta(minutes=45)  # this close to a slot, breaking news goes now AS that slot's post (owner, 2026-11-15)
    aging_from: timedelta = timedelta(hours=3)  # slot ranking: a story older than this ranks lower, up to aging_penalty points at news_ttl
    aging_penalty: float = 10.0
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
    # the schedule (owner, 2026-11-09): "slots" = exactly one post at each of these Dubai hours, the best story that clears regular_bar or,
    # if none does, a verified market check (app/newsdesk/recap); no extraordinary posts in between. "windows" = the plan above.
    schedule: str = ""  # "" = DEFAULT_SCHEDULE
    slot_hours: tuple[int, ...] = (0, 4, 8, 12, 16, 20)  # Dubai (UTC+4, no daylight saving)
    slot_catch_up: timedelta = timedelta(minutes=90)  # a slot not filled by then (the server was down) is missed, never posted late
    recap: bool = False  # see RECAP_FALLBACK
    market_bar: int = 70  # a price move or liquidation flush on its own needs this to take a slot: no small "price alert" posts
    experiments: bool = True  # about one story in five tries its runner-up factual hook (app/newsdesk/viral)
    priority_bonus: int = 6  # ranking only, never the bar: Bitcoin, Ethereum, Hyperliquid, macro, commodities, geopolitics come first
    # owner, 2026-11-20: the desk's own canonical names too (editorial.canon: GOLD, SILVER, OIL, SPX, IXIC). A silver move is filed as
    # "SILVER", so before this a silver crash never counted as a priority subject
    priority_assets: frozenset[str] = field(default_factory=lambda: frozenset({"BTC", "ETH", "HYPE", "$BTC", "$ETH", "$HYPE", "BTC-USD", "ETH-USD", "GC=F", "CL=F", "SI=F", "^GSPC", "^IXIC",
                                                                             "GOLD", "SILVER", "OIL", "SPX", "IXIC"}))
    # the wire style (app/newsdesk/wire, network.post_style "wire"): exceptional market moves and material updates may break between slots
    # too, under a rolling cap (breaking_per_6h) as well as the daily one; slot ranking favours surprising stories and downranks filler
    wire: bool = False
    breaking_per_6h: int = 0  # 0 = no rolling cap (the older styles)
    breaking_market_age: timedelta = timedelta(minutes=30)  # a measured move breaks only while it is happening
    # owner, 2026-11-20 ("allow significant market moves to publish promptly without waiting hours"): a MAJOR or SIGNIFICANT measured move
    # on a priority asset (app/newsdesk/urgency) goes promptly, between slots, in every style, majors first. Its own lane: at most
    # market_breaking_per_day in 24 h (the backtest's busiest days had 4 majors at once: a few posts a market day are enough), never within
    # min_gap of another post, never a second post on the same asset inside asset_cooldown (a deeper move is an update)
    market_breaking_per_day: int = 3


# the wire style's breaking lane (owner, 2026-11-14: "publish meaningful breaking updates quickly ... a sane rate limit so breaking can't
# flood the channel"): at most 3 a day and 2 in any six hours, 90 minutes from any other post, never 45 minutes before a slot
WIRE_BREAKING_PER_DAY = 3
INCIDENT_SLOT_MAX_MIN = 180  # an infrastructure incident takes a regular slot only within 3 h of its first report
WIRE_BREAKING_PER_6H = 2


DEFAULT_SCHEDULE = "slots"
# Owner, 2026-11-09: "every telegram post will also post a tweet". X waits for Telegram's outcome on each event and posts exactly what
# Telegram delivered: X's own daily limit and category switches no longer drop one, and X gets extra time after Telegram's delivery.
X_FOLLOWS_TELEGRAM = True
X_AFTER_TELEGRAM = timedelta(hours=1)  # how long X may still post an event after Telegram delivered it
TG_WAITING = ("pending", "queued")  # "uncertain": Telegram may well have posted it, so X follows
# owner, 2026-11-10: a slot with no important story is SKIPPED, never filled with a market check (the four WTI "market check" posts of
# 2026-11-10 were this fallback). The code stays for tests and a deliberate future decision; nothing in Admin turns it back on.
RECAP_FALLBACK = False
POLICY = Policy()
DUBAI = timezone(timedelta(hours=4), "Dubai")
PRIORITY_TOPICS = {"macro", "hack", "liquidations", "etf", "failure", "geopolitics", "flows"}
PRIORITY_CATEGORIES = {"economics", "commodities", "politics"}
PRIORITY_WORDS = re.compile(r"\b(bitcoin|btc|ethereum|ether|eth|hyperliquid|hype|fed|federal reserve|rate (?:cut|hike)|inflation|cpi|jobs report|tariffs?|sanctions?|"
                            r"war|missile|ceasefire|opec|oil|gold|treasur(?:y|ies)|recession)\b", re.I)
STRONG = ("official", "two outlets", "price data", "exchange data")
# slots are four hours apart, so a story can wait up to four hours for one while freshness fades its score. For the bar (never the ranking),
# a news story is judged by its best score before freshness (facts["peak"]) until it has waited one full slot interval: a strong story at
# 22:10 is still strong at 00:00, and one that more outlets picked up since counts them. Older than that, it had its slot: freshness counts.
# Freshness always decides between candidates.


CREDIBLE = ("official", "two outlets")
# important enough to be held for a later slot or to break between slots: verified significance (geopolitics, regulation, mega-cap
# corporate, institutions, flows, macro data) or a chaos topic. A story only about a price moving, or a whale, is not.
IMPORTANT_TOPICS = {"hack", "scam", "failure", "controversy", "macro", "etf", "flows", "geopolitics", "regulation", "corporate", "structure"}


def outlets_of(ev: NdEvent) -> set[str]:
    """Distinct outlets reporting it (one outlet seen through two of its feeds counts once)."""
    return {str(s.get("name") or "").strip().lower() for s in ev.sources or []} - {""}


def verified_important(ev: NdEvent) -> bool:
    """An important news story independently confirmed by at least two different credible outlets (the desk's curated feeds)."""
    if ev.kind not in ("news", "update") or ev.evidence not in CREDIBLE or len(outlets_of(ev)) < 2:
        return False
    return bool((ev.facts or {}).get("importance")) or (ev.topic or "") in IMPORTANT_TOPICS


def held_score(ev: NdEvent, now: datetime, p: Policy) -> float:
    """What a candidate stands at for the bar. A story's best score counts for one slot interval; verified important news keeps it up
    to hold_hours, fading hold_decay a point per hour after that interval. Freshness always decides the ranking between candidates."""
    if ev.kind == "market":
        return float(ev.virality)
    interval = 24 * 60 / max(1, len(p.slot_hours))
    peak = float((ev.facts or {}).get("peak") or 0)
    age = _age(ev, now)
    if age <= interval:
        return max(float(ev.virality), peak)
    if verified_important(ev) and age <= p.hold_hours.total_seconds() / 60:
        return max(float(ev.virality), peak - p.hold_decay * (age - interval) / 60)
    return float(ev.virality)
RELEASED = "released"
FAMILIES = ({"hack", "scam", "failure"}, {"controversy"})  # Bybaz hacked, then Bybaz halts withdrawals: one story
ESCALATE = re.compile(r"\b(lazarus|north korea\w*|behind the|blamed?|attribut\w+|(?:reopen|resum)\w* withdrawals|outflows|refund\w*|arrest\w*|charged|indict\w*|bankrupt\w*|insolven\w*|(?:halt|paus|suspend|freez)\w* (?:all )?withdrawals|seiz\w+|frozen|confirms?|confirmed|"
                      r"recover\w*|returns? (?:the )?funds|delist\w*|sentenc\w*|guilty|shut(?:s|ting)? down|collaps\w*|liquidat\w+|depeg\w*|steps? down|stepped down|resign\w*|ousted|fired|launder\w*|"
                      r"fbi|doj|justice department|criminal)\b", re.I)


NUMBERS = ("market_bar", "regular_per_day", "regular_bar", "opening_bar", "extra_bar", "extra_per_day", "ceiling", "window_hours", "topic_max", "updates_per_story")
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
    over = (desk_state or {}).get("policy") or {}
    if over.get("schedule") in ("slots", "windows"):
        p.schedule = over["schedule"]
    hours = over.get("slot_hours")
    if isinstance(hours, list) and hours and all(isinstance(h, int) and 0 <= h <= 23 for h in hours):
        p.slot_hours = tuple(sorted(set(hours)))
    if isinstance(over.get("slot_catch_up_minutes"), int) and 10 <= over["slot_catch_up_minutes"] <= 230:
        p.slot_catch_up = timedelta(minutes=over["slot_catch_up_minutes"])
    p.recap = RECAP_FALLBACK and over.get("recap") is not False
    p.experiments = (desk_state or {}).get("experiments", True) is not False  # Viral Engine 2.0 hook experiments (Admin switch)
    from app.newsdesk import network

    if network.post_style(desk_state) == "wire":
        p.wire = True
        # the daily breaking cap stays the desk's own (2026-11-16: the wire style became the standard; publishing limits unchanged)
        p.breaking_per_day = min(p.breaking_per_day, WIRE_BREAKING_PER_DAY)
        p.breaking_per_6h = WIRE_BREAKING_PER_6H
    return p


def sched(p: Policy) -> str:
    return p.schedule or DEFAULT_SCHEDULE


# --- the schedule --------------------------------------------------------------------------------------------------


def slot_times(now: datetime, p: Policy, back_hours: int = 24) -> list[datetime]:
    """Every slot (UTC) from back_hours ago up to and including the next one, oldest first."""
    local = now.astimezone(DUBAI)
    out = []
    for d in (-2, -1, 0, 1):
        day = (local + timedelta(days=d)).replace(minute=0, second=0, microsecond=0)
        out += [day.replace(hour=h).astimezone(now.tzinfo or timezone.utc) for h in p.slot_hours]
    out = sorted(set(out))
    nxt = next(t for t in out if t > now)
    return [t for t in out if now - timedelta(hours=back_hours) <= t <= nxt]


def current_slot(now: datetime, p: Policy) -> datetime | None:
    """The slot this moment belongs to, while it may still be filled (its start up to slot_catch_up later)."""
    past = [t for t in slot_times(now, p, back_hours=6) if t <= now]
    if not past:
        return None
    t = past[-1]
    return t if now - t <= p.slot_catch_up else None


def next_slot(now: datetime, p: Policy) -> datetime:
    return slot_times(now, p, back_hours=0)[-1]


def slot_label(t: datetime) -> str:
    return t.astimezone(DUBAI).strftime("%H:%M")


def slot_filled(db: Session, t: datetime, p: Policy) -> NdEvent | None:
    """The event released for the slot starting at t (anything released from t until the next slot, or breaking news released just
    before it as that slot's post: facts.slot_at)."""
    later = [x for x in slot_times(t + timedelta(minutes=1), p, back_hours=0) if x > t]
    end = later[0] if later else t + timedelta(hours=4)
    ev = db.scalar(select(NdEvent).where(NdEvent.status == RELEASED, NdEvent.released_at >= t, NdEvent.released_at < end).order_by(NdEvent.released_at).limit(1))
    if ev is not None:
        return ev
    early = db.scalars(select(NdEvent).where(NdEvent.status == RELEASED, NdEvent.slot == "slot", NdEvent.released_at >= t - p.breaking_before_slot - timedelta(minutes=5),
                                             NdEvent.released_at < t))
    return next((e for e in early if str((e.facts or {}).get("slot_at") or "") == t.isoformat()), None)


def aging(ev: NdEvent, now: datetime, p: Policy) -> float:
    """Slot ranking only: points off a news story older than aging_from, growing to aging_penalty at news_ttl (today's news first)."""
    if ev.kind not in ("news", "data"):
        return 0.0
    age, start, end = _age(ev, now), p.aging_from.total_seconds() / 60, p.news_ttl.total_seconds() / 60
    if age <= start:
        return 0.0
    return p.aging_penalty * min(1.0, (age - start) / max(1.0, end - start))


def latency(ev: NdEvent, now: datetime) -> dict[str, int | None]:
    """Measured minutes: the story's first report -> now, and our first sight of it -> now."""
    seen = _utc(ev.first_seen, now)
    return {"event_to_release_min": int(_age(ev, now)), "detect_to_release_min": int((now - seen).total_seconds() // 60) if seen else None}


def priority(ev: NdEvent, p: Policy) -> bool:
    f = ev.facts or {}
    assets = {str(a).upper() for a in (f.get("assets") or [])} | {str(t).upper() for t in (f.get("tickers") or [])} | canon_assets(ev)
    return bool(assets & p.priority_assets or ev.topic in PRIORITY_TOPICS or ev.category in PRIORITY_CATEGORIES or PRIORITY_WORDS.search(ev.headline or ""))


def canon_assets(ev: NdEvent) -> set[str]:
    """The event's assets by the desk's canonical names (editorial.canon), so a move filed as "SILVER" and a data post filed as "SI=F" are
    the same asset (owner, 2026-11-20: silver went out as two events, #870 the 24-hour move and #880 the one-day drop, and the asset
    cooldown compared "SILVER" with "SI=F"). Liquidation flushes keep their own "LIQ:" key: a flush is its own story."""
    return {(str(a).upper() if str(a).upper().startswith("LIQ:") else editorial.canon(str(a))) for a in ((ev.facts or {}).get("assets") or []) if a}


def urgency_of(ev: NdEvent) -> str:
    return str(((ev.facts or {}).get("urgency") or {}).get("level") or "")


def _shares_asset(ev: NdEvent, others: list[NdEvent], now: datetime, within: timedelta) -> NdEvent | None:
    mine = canon_assets(ev)
    if not mine:
        return None
    return next((e for e in others if mine & canon_assets(e) and now - _utc(e.released_at, now) < within), None)


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
        return f"${m.group(1)}{'T' if m.group(2).lower() in ('trillion', 'tn') else 'B' if m.group(2).lower() in ('billion', 'bn', 'b') else 'M'}"
    if measured:
        return f"{'+' if measured[1] > 0 else '-'}{abs(measured[1]):.1f}%"
    pm = brief.PERCENT.search(title or "")
    return pm.group(0) if pm else ""


def _engagement_points(adj: dict[str, float], topic: str) -> float:
    return float(adj.get(topic, 0.0))


def discovery_points(evidence: str, lead: str, outlets: set[str]) -> float:
    """Owner, 2026-11-11: our own verified discoveries lead, generic single-publisher headlines follow. A story the desk confirmed across
    three or more independent outlets, or with a World Monitor catalog outlet among them, or from an official source earns points; the
    two-outlet floor and a single attributed outlet earn none."""
    every = {lead, *outlets}
    if evidence == "official":
        return 2.0
    if evidence != "two outlets" or len(every) < 2:
        return 0.0
    # two outlets is the evidence floor every verified story already meets: only wider confirmation earns more
    return (3.0 if len(every) >= 3 else 0.0) + (2.0 if any(sources.via(n) for n in every) else 0.0)


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
            group = chaos.GROUP.get(claim.symbol, "stock")
            # a US stock or index is never tagged with a rolling window (Telegram 203: a session is measured from the previous close), and a
            # tag is a real part of the move: half its alert line at least, never "(BTC -0.4% IN 1H)"
            measured = None if group in moves.EQUITY_GROUPS else moves.biggest(q, {"group": group}, published)
            if measured and measured[2] < 0.5:
                measured = None
            if measured and (claim.direction == 0 or (measured[1] > 0) == (claim.direction > 0)):
                tag = headline.price_tag(claim.asset, measured[0], measured[1])
                spark = moves.spark(q, published)
            else:
                measured = None
    age_min = max(0.0, (now - published).total_seconds() / 60) if published else 999
    if v.importance is not None:
        s = editorial.importance_score(v.importance, evidence=v.evidence, outlets=len(outlets), outlets_3h=len(near), age_minutes=age_min, title=story.title or "")
    else:
        s = virality.news(title=story.title or "", summary=story.summary or "", evidence=v.evidence, lead_topic=b.topic, outlets=len(outlets), outlets_3h=len(near),
                          age_minutes=age_min, alleged=b.alleged, tickers=b.tickers, measured_ratio=measured[2] if measured else 0.0, engagement=_engagement_points(adj, b.topic))
    bonus = discovery_points(v.evidence, story.source, outlets)
    if bonus:
        s = virality._finish({**{k: x for k, x in s.parts.items() if k != "freshness"}, "discovery": bonus}, age_min)
    head = headline.news(story.title or "", tag=tag)
    told = " ".join(f"{x.title} {x.summary or ''}" for x in [story, *v.others]) + f" {v.price_check} {tag}"
    assets = [claim.asset] if claim is not None and claim.at < lead else []  # the asset the headline leads with: "Bitcoin ... as Wall Street sells off" is Bitcoin's
    facts = {"tickers": b.tickers, "flag": b.flag, "alleged": b.alleged, "falling": b.falling, "risk": b.topic in ("hack", "scam", "failure"), "price_check": v.price_check,
             "price_tag": tag, "told": told[:4000], "assets": assets, "stat": _stat(story.title or "", tag, measured), "spark": spark,
             "published_at": published.isoformat() if published else None, "story_ids": [story.id, *[o.id for o in v.others]], "outlets": sorted({story.source, *outlets})}
    if v.importance is not None:
        facts["importance"] = {"kind": v.importance.kind, "why": v.importance.why}
    if measured and claim is not None and measured[2] >= 1 and published is not None:
        # the story's own move, past its alert line by our prices: the picture can be the real chart of it
        q = quotes[claim.symbol]
        cut = published.timestamp()
        pts = [(int(t), float(c)) for t, c, _ in q.bars if cut - 86400 <= t <= cut and c > 0]
        if len(pts) >= 10:
            step = max(1, len(pts) // 120)
            facts.update({"points": [list(x) for x in pts[::step]], "pct": round(measured[1], 2), "window": measured[0], "coin": claim.asset,
                          "name": {"OIL": "Brent crude" if claim.symbol == "BZ=F" else "WTI crude"}.get(claim.asset, claim.asset), "data_source": "Yahoo Finance"})
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
    shared_names = checks.distinctive_names(story.title) & checks.distinctive_names(ev.title)
    family = next((f for f in FAMILIES if ev.topic in f), {ev.topic})
    if shared_names and ev.topic and ev.topic in ("hack", "scam", "failure", "controversy") and family & set(virality.topics_in(story.title or "")):
        return True
    # owner, 2026-11-14 (post 166: a footballer's death merged into a hospital strike on "Israeli" and "Gaza"): same-event evidence, not
    # shared countries, adjectives or ordinary words (checks.same_event)
    return checks.same_event(ev.title or "", story.title or "")


# A development must be a reported fact, not a maybe (owner, 2026-11-10: "allowed story developments contain genuinely new sourced facts").
HEDGED = re.compile(r"\b(could|may|might|would|reportedly|rumou?r\w*|sources? say|people familiar|considering|weighs?|weighing|mulls?|eyes|"
                    r"set to|expected to|poised to|likely|speculat\w*|unconfirmed)\b|\?\s*$", re.I)
CONFIRM = {"confirm", "confirms", "confirmed"}


def development(story: NdStory, parent: NdEvent, told_before: list[str] | tuple[str, ...] = ()) -> str:
    """What's materially new in this story against everything already told about it (the event out and its updates), or "" if it's the
    same news again. Only a fact the outlet reports: a hedged headline ("could", "reportedly", "set to", a question) is no development,
    and a bare "confirms" counts only from an official source."""
    title = story.title or ""
    if HEDGED.search(title):
        return ""
    told = " | ".join([str((parent.facts or {}).get("told") or parent.title), str(parent.title or ""), *told_before])
    new_usd, old_usd = brief._amount_usd(title), max((brief._amount_usd(t) for t in told.split(" | ")), default=0)
    if new_usd and old_usd and new_usd >= old_usd * 1.25:
        return "a bigger figure"
    words = {w.lower() for w in ESCALATE.findall(title)} - {w.lower() for w in ESCALATE.findall(told)}
    if story.tier != 0:
        words -= CONFIRM
    if words:
        return f"new: {sorted(words)[0]}"
    if story.tier == 0 and parent.evidence != "official":
        return "an official source"
    return ""


# --- decide --------------------------------------------------------------------------------------------------------


async def decide(db: Session, now: datetime, *, quotes: dict[str, sources.Quote], watchlist: list[dict[str, Any]] | None = None, categories: dict[str, bool] | None = None,
                 market: Any = None, second_opinion: Any = None, desk_state: dict[str, Any] | None = None, liquidations: bool = True,
                 daily: dict[str, Any] | None = None) -> dict[str, Any]:
    """One pass of the desk. Safe to run from both lanes: event keys are unique and release is one row at a time."""
    safety.migrate_pause(db, now)  # once: the old unscoped automatic market pause moves to the scope its anomalies justify (2026-11-24)
    try:  # established wars in the desk's own record (importance.established_wars): strikes inside them are significant, not major
        importance.WARS = importance.established_wars(newsworthy.told(db, now), now)
    except Exception as e:  # noqa: BLE001 - without it only Russia-Ukraine counts as established
        logger.info("newsdesk events: established wars unreadable (%s)", type(e).__name__)
    p = policy(desk_state)
    adj = engagement.adjustments(db, now)
    recent = list(db.scalars(select(NdEvent).where(NdEvent.created_at >= now - TRACK)))
    out = {"stories": 0, "markets": 0, "updates": 0, "released": None}
    _expire(db, recent, now, p)
    # stories
    rows = list(db.scalars(select(NdStory).where(NdStory.created_at >= now - timedelta(hours=36))))
    ready, held = chaos.candidates(rows, quotes, now, max_age=p.news_ttl, categories=categories)
    for v in [*ready, *important(held, rows, now)]:
        out["stories"] += _offer_story(db, v, recent, quotes, now, adj, p)
    # market moves, judged against each asset's own daily history (app/newsdesk/urgency): the daily closes are read first (cached 20 min)
    enabled = categories or {}
    from app.newsdesk import datapost

    if daily is None:
        try:
            daily = await datapost.fetch_all()
        except Exception as e:  # noqa: BLE001 - without history a move is judged on its alert line alone (routine unless 1.5x / 2x)
            logger.info("newsdesk events: daily closes: %s", type(e).__name__)
            daily = {}
    livedata.remember(quotes, daily)  # a post made from these seconds later uses them as its re-read (app/newsdesk/livedata)
    for m in moves.detect(quotes, sources.DEFAULT_WATCHLIST if watchlist is None else watchlist, now):
        if not enabled.get(str(m.item.get("category") or "crypto"), True):
            continue
        out["markets"] += await _offer_move(db, m, recent, now, adj, p, second_opinion, market, daily, quotes)
    if liquidations and enabled.get("crypto", True):
        try:
            out["markets"] += _offer_liquidations(db, recent, now, adj, p)
        except Exception as e:  # noqa: BLE001 - the optional source never stops the rest
            logger.info("newsdesk events: liquidations: %s", type(e).__name__)
            db.rollback()
            recent = list(db.scalars(select(NdEvent).where(NdEvent.created_at >= now - timedelta(hours=36))))
    # Viral Engine 2.0: data posts and MARKETS GONE MAD, from daily closes we measure (app/newsdesk/datapost). Admin can switch them off.
    if (desk_state or {}).get("data_posts", True) is not False and enabled.get("stocks", True) is not False:
        try:
            db.flush()
            recent = list(db.scalars(select(NdEvent).where(NdEvent.created_at >= now - TRACK)))  # with this pass's moves (one story per move)
            out["data"] = len(datapost.offer(db, now, daily or {}, recent, p.regular_bar))
        except Exception as e:  # noqa: BLE001 - the optional source never stops the rest
            logger.info("newsdesk events: data posts: %s", type(e).__name__)
    db.commit()
    out["updates"] = sum(1 for e in recent if e.kind == "update" and e.status == "candidate")
    # held figures (implausible, contradicted by a second market) are recorded and reported, and enough of them pause market posts,
    # before anything is released in this pass (app/newsdesk/safety)
    out["held"] = await safety.settle(db, now)
    _prerender(db, now, p)
    released = release(db, now, p)
    db.commit()
    if released is not None:
        _render_for(db, released, now)
    if released is None and sched(p) == "slots":
        released = await fill_with_recap(db, now, p, quotes, market)
        if released is None:
            note_skip(db, now, p)
    out["released"] = released.id if released is not None else None
    db.commit()
    return out


def _gpu_on(db: Session) -> bool:
    from app.newsdesk import engine

    return render_queue_enabled(engine.settings_row(db).state or {})


def render_queue_enabled(state: dict[str, Any]) -> bool:
    from app.newsdesk import render_queue

    return render_queue.enabled(state)


def _prerender(db: Session, now: datetime, p: Policy) -> None:
    """The GPU PC paints the likely stories' backgrounds before a slot opens (app/newsdesk/render_queue). Never stops the desk."""
    try:
        if sched(p) != "slots" or not _gpu_on(db):
            return
        from app.newsdesk import render_queue

        waiting = sorted(db.scalars(select(NdEvent).where(NdEvent.status == "candidate")), key=lambda e: (-e.virality, e.id))
        waiting = [e for e in waiting if e.virality >= p.regular_bar - 4 and not _blocked(e)]
        if render_queue.prerender(db, now, next_slot(now, p), waiting):
            db.commit()
    except Exception as e:  # noqa: BLE001
        logger.info("newsdesk events: prerender: %s", type(e).__name__)
        db.rollback()


def _render_for(db: Session, ev: NdEvent, now: datetime) -> None:
    try:
        if not _gpu_on(db):
            return
        from app.newsdesk import render_queue

        if render_queue.on_release(db, ev, now) is not None:
            db.commit()
    except Exception as e:  # noqa: BLE001
        logger.info("newsdesk events: render on release: %s", type(e).__name__)
        db.rollback()


MAJOR_KIND = {"markets": "macro", "corporate": "corporate", "geopolitics": "geopolitics", "incident": "infrastructure", "disaster": "geopolitics", "hack": "corporate"}


def _major_significance(story: NdStory) -> "editorial.Significance | None":
    from app.terminal import intel

    title = story.title or ""
    if intel.RUMOR.search(title):
        return None
    kind, why = intel.impact_class(title, story.summary or "", [], {})
    return editorial.Significance(MAJOR_KIND[kind], 28.0, f"major: {why}") if kind else None


def important(held: list[chaos.Verdict], rows: list[NdStory], now: datetime) -> list[chaos.Verdict]:
    """Stories the chaos filter turned away only for not being market chaos, that still matter (app/newsdesk/editorial.significance):
    geopolitics, regulation milestones, mega-cap corporate shocks, big institutions on crypto rails, large ETF flows. Verified only by an
    official source or a second outlet; a single outlet's word is not enough here."""
    out = []
    for v in held:
        if v.status not in ("rejected", "held") or not editorial.admissible(v.why):
            continue
        sig = editorial.significance(v.story.title or "", v.story.summary or "")
        if sig is None:
            # owner, 2026-11-18 replay: "Nasdaq enters correction as Wall Street sells off" and "Nvidia beats estimates as revenue jumps 103%
            # to $115.8 billion" (two outlets each) never became events: neither chaos nor significance() reads them as news that matters
            sig = _major_significance(v.story)
        if sig is None:
            continue
        others = editorial.corroborating(v.story, rows)
        evidence = "official" if v.story.tier == 0 else "two outlets" if {o.source for o in others} - {v.story.source} else ""
        if not evidence:
            continue
        b = brief.assess(v.story, others, now)
        b.topic, b.falling = sig.kind, False
        b = brief.relabel(b, strong=True)
        out.append(chaos.Verdict(v.story, "post", "", evidence, b, others, "", importance=sig))
    return out


def _expire(db: Session, recent: list[NdEvent], now: datetime, p: Policy = POLICY) -> None:
    for e in recent:
        if e.status == "candidate" and e.expires_at is not None and _utc(e.expires_at, now) <= now:
            if verified_important(e) and _age(e, now) < p.hold_hours.total_seconds() / 60:
                e.expires_at = now + p.hold_hours - timedelta(minutes=_age(e, now))  # held: eligible until hold_hours after it broke
                continue
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
        horizon = TRACK if ev.status == RELEASED else timedelta(hours=24)  # a story out there is tracked for five days (Viral Engine 2.0)
        if ev.status in ("missed", "rejected"):
            continue  # an earlier telling that never went out (it expired waiting) mustn't swallow the story's later, fresher tellings
        if ev.kind in ("news", "update") and _same(ev, story, {str(x.get("url")) for x in ev.sources or []}) and _utc(ev.created_at, now) >= now - horizon:
            match = ev if ev.parent_id is None else (db.get(NdEvent, ev.parent_id) or ev)  # an update's story is its original's
            break
    if match is None and any(r.key == f"story:{story.link}" for r in recent):
        return 0
    score, facts, head = score_story(v, quotes, now, adj)
    if match is not None and match.status != RELEASED:
        lead_story = ((match.facts or {}).get("story_ids") or [None])[0]
        if match.status == "candidate" and (lead_story == story.id or score.total > match.virality):
            _fill(match, v, score, facts, head, now, quotes, adj, p.experiments)  # the leading story scored now (it fades as it ages), or a stronger telling of it
        elif match.status == "candidate":
            match.sources = _merge_sources(match.sources, _sources(v))
            match.facts = {**(match.facts or {}), "outlets": sorted(set((match.facts or {}).get("outlets") or []) | set(facts["outlets"]))}
        return 0
    if match is not None:
        if any(story.link == str(x.get("url")) for e in recent if e.id == match.id or e.parent_id == match.id for x in e.sources or []):
            return 0
        kids = list(db.scalars(select(NdEvent).where(NdEvent.parent_id == match.id, NdEvent.status.in_(("candidate", RELEASED)))))
        what = development(story, match, [str(k.title or "") for k in kids])
        if not what or len(kids) >= p.updates_per_story or any(story.link == str(x.get("url")) for k in kids for x in k.sources or []):
            return 0
        ev = NdEvent(key=f"update:{story.link}"[:200], kind="update", parent_id=match.id, first_seen=now, expires_at=now + p.news_ttl / 2, status="candidate")
        _fill(ev, v, score, facts, head, now, quotes, adj, p.experiments)
        first = _utc(match.released_at, now)
        ev.facts = {**ev.facts, "development": what, "assets": [], "kicker": f"UPDATE · first reported {first.day} {first:%b}" if first else "UPDATE",
                    "development_source": {"outlet": story.source, "url": story.link, "headline": story.title, "since": parent_told(match)}}
        ev.topic = match.topic or ev.topic
        _inherit_magnitude(ev, match)
        return 1 if _insert(db, recent, ev) else 0
    published = _utc(story.published_at, now) or now
    ev = NdEvent(key=f"story:{story.link}"[:200], kind="news", first_seen=now, expires_at=published + p.news_ttl, status="candidate")
    _fill(ev, v, score, facts, head, now, quotes, adj, p.experiments)
    if not _insert(db, recent, ev):
        return 0
    _measure(ev, "candidate", db)
    return 1


def _measure(ev: NdEvent, stage: str, db: Session | None = None) -> None:
    """One production log line per candidate (owner, 2026-11-19: validate the gate on NEW real events, rejected ones included): the
    audience score, evidence, decision and exact reason, and what the extraordinary path would do. Log only: never changes a decision."""
    import json

    try:
        out = newsworthy.shadow(ev.title or ev.headline or "", ev.summary or "", outlets=len(outlets_of(ev)), evidence=str(ev.evidence or ""))
        if db is not None:  # story memory's view (shadow: the repeat gate already decided), owner 2026-11-19
            from app.newsdesk import memory

            out["memory"] = memory.followup(db, ev.title or ev.headline or "", ev.summary or "")
        logger.info("editorial candidate: %s", json.dumps({"id": ev.id, "stage": stage, "kind": ev.kind, "virality": int(ev.virality or 0), **out},
                                                         ensure_ascii=False))
        if db is not None:  # kept in production (app/newsdesk/validation): Terminal › Settings › Validation
            from app.newsdesk import validation

            validation.record_candidate(db, ev, out)
            from app.config.settings import get_settings
            from app.newsdesk import chief

            chief.shortlist(db, ev, out, out.get("memory"), settings=get_settings())  # the AI editor's shortlist: stored, never sent while it is off
    except Exception as e:  # noqa: BLE001
        logger.info("editorial candidate: #%s not measured (%s)", ev.id, type(e).__name__)


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


ANGLE_MAX = 5.0  # the most a strong factual hook adds when slot stories are ranked; the score and every bar are judged without it


def _angle(ev: NdEvent, v: chaos.Verdict, quotes: dict[str, sources.Quote] | None, now: datetime, adj: dict[str, float] | None, experiments: bool = True) -> None:
    """Viral Engine 2.0: pitch the story five ways (app/newsdesk/viral), keep the plan on the event, and let a strong factual hook rank it
    a little higher among stories that already cleared the bar (facts.angle_bonus). Never raises: a story without a plan is simply posted
    the way it was before."""
    from app.newsdesk import viral

    try:
        learned = {k.split(":", 1)[1]: x for k, x in (adj or {}).items() if k.startswith("hook:")}
        since = _utc(v.story.published_at, now) or _utc(ev.first_seen, now)
        pl = viral.plan(ev, [v.story, *v.others], quotes, now, learned, since=since, experiments=experiments)
    except Exception as e:  # noqa: BLE001
        logger.info("newsdesk events: no viral plan for %s (%s)", ev.key, type(e).__name__)
        return
    bonus = round(max(0.0, min(ANGLE_MAX, (pl.chosen.score - 40) / 8)), 1) if pl.chosen is not None else 0.0
    ev.facts = {**(ev.facts or {}), "viral": pl.out(), "angle_bonus": bonus}


def _fill(ev: NdEvent, v: chaos.Verdict, score: virality.Score, facts: dict[str, Any], head: str, now: datetime,
          quotes: dict[str, sources.Quote] | None = None, adj: dict[str, float] | None = None, experiments: bool = True) -> None:
    b = v.brief
    ev.title, ev.headline, ev.summary = (v.story.title or "")[:300], head[:300], _summary(v)
    ev.topic, ev.evidence, ev.category = b.topic, v.evidence, v.story.category or "viral"
    # what the story is worth before freshness fades it, the best yet: the slot bar is judged on this (slots are 4 h apart; news_ttl caps age)
    fresh = float((score.parts or {}).get("freshness") or 1.0)
    peak = max(float((ev.facts or {}).get("peak") or 0), round(float(score.total) / max(fresh, 0.55)))
    kept = {k: (ev.facts or {})[k] for k in ("blocked",) if k in (ev.facts or {})}  # the owner's block survives a re-scoring
    ev.virality, ev.parts, ev.facts, ev.sources = float(score.total), score.parts, {**facts, "peak": peak, **kept}, _sources(v)
    if quotes is not None:
        _angle(ev, v, quotes, now, adj, experiments)


async def _offer_move(db: Session, m: moves.Move, recent: list[NdEvent], now: datetime, adj: dict[str, float], p: Policy, second_opinion: Any, market: Any,
                      daily: dict[str, Any] | None = None, quotes: dict[str, Any] | None = None) -> int:
    assets = sorted({moves.KEYS.get(sym, sym) for sym in ([m.symbol] if m.kind != "wide" else [])} | ({"BTC", "ETH", "SOL", "XRP"} if m.symbol == "CRYPTO" else set())
                    | ({"SPX", "IXIC", "DJI"} if m.symbol == "US-STOCKS" else set()))
    direction = "up" if m.up else "down"
    if m.kind == "wide" and not m.figures:
        return 0  # a market-wide post prints every member's level (Telegram 203, 2026-11-20, printed percentages only): no levels, no post
    if m.kind == "milestone" and any((e.facts or {}).get("kind") == "milestone" and (e.facts or {}).get("symbol") == m.symbol and (e.facts or {}).get("level") == m.level for e in recent):
        return 0  # one post per level per asset in three days, whichever way it crossed
    # the same asset: nothing new for six hours, and the same way for a day unless the move escalates ("oil -9.5% in 24h" again at -9.8% isn't news)
    # (the data posts' one-day anomaly is the same story: owner, 2026-11-20, never two crash posts for one move)
    mine = {editorial.canon(a) for a in assets}
    same = [e for e in recent if e.kind in ("market", "update", "news", "data") and canon_assets(e) & mine
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
    confirmed = False  # a second market compared and agreed (owner, 2026-11-23: no automatic move without it)
    if second_opinion is not None and m.kind == "wide" and any(sources.has_second(_item_of(f["symbol"])) for f in m.figures):
        # a market-wide move: every member with a second market is checked against it (the S&P 500 always is)
        details = []
        for fig in m.figures:
            it = _item_of(fig["symbol"])
            if not sources.has_second(it):
                continue
            ok, detail = await sources.call_second(second_opinion, it, float(fig["price"]), market, change=float(fig.get("change") or 0))
            if not ok:
                logger.info("newsdesk events: %s not verified (%s): %s", m.symbol, fig["name"], detail)
                if "apart" in detail:  # a real disagreement, not a second market that couldn't be read
                    safety.note("second_source", str(fig["symbol"]), detail, price=fig["price"])
                return 0
            details.append(f"{fig['name']}: {detail}")
        verified = "; ".join(details)
        confirmed = bool(details)
    elif second_opinion is not None and sources.has_second(m.item):
        ok, detail = await sources.call_second(second_opinion, m.item, m.price, market, change=m.change)
        if not ok:
            logger.info("newsdesk events: %s not verified: %s", m.symbol, detail)
            if "apart" in detail:  # a real disagreement between two markets, not a source that couldn't be read
                safety.note("second_source", m.symbol, detail, price=m.price)
            return 0
        verified = detail
        confirmed = True
    elif m.symbol == "CRYPTO":
        verified = "several assets, one price source (Yahoo Finance)"
    else:
        verified = "one price source (Yahoo Finance)" if not m.item.get("hl") else "one price source (Yahoo Finance): no second opinion available"
    two = confirmed  # a second market compared and agreed (Hyperliquid, trade.xyz)
    round_level = m.kind == "milestone" and moves.is_round(m.symbol, m.level, moves.step_of(m.item))
    score = virality.market(kind=m.kind, group=str(m.item.get("group") or "stock"), ratio=m.ratio, two_sources=two, symbol_key=m.key, round_level=round_level,
                            engagement=_engagement_points(adj, "move"))
    # the move against the asset's own history (owner, 2026-11-20): its urgency, ranking points for the context, and the verified line
    # "Daily closes show no one-day drop this large for Silver since May 2026" when it is that rare
    u = livedata.assess(m.symbol, m.change, m.ratio, now, daily) if m.kind in ("move", "wide") else None
    if u is not None and u.points:
        score.parts["context"] = u.points
        score.total = max(0, min(100, int(round(score.total + u.points))))
    extra = livedata.since_line(m, u) if u is not None else ""
    # a level or a 52-week extreme: what it means in the asset's own record (owner, 2026-11-24: intelligent price milestones)
    ms = moves.milestone_context(m, (daily or {}).get(m.symbol) or [], now) if m.kind in ("milestone", "high", "low") else {}
    if ms:
        extra = headline.milestone_line(m, ms)
    cross = moves.cross_asset(m, quotes or {}, now)  # the markets around it over the same window (figures only, never a cause)
    extra = " ".join(x for x in (extra, moves.cross_line(m, cross)) if x)
    head = headline.market(m)
    told = livedata.market_told(m, head, extra)
    tk = moves.CASHTAG.get(m.symbol)
    names = {"Bitcoin": "BTC", "Ethereum": "ETH", "Solana": "SOL", "XRP": "XRP"}
    tickers = [tk] if tk else [names[n] for n, _ in m.members if n in names][:3]
    facts = {"symbol": m.symbol, "assets": assets, "kind": m.kind, "window": m.window, "change_pct": round(m.change, 2), "ratio": round(m.ratio, 2), "price": m.price,
             "as_of": m.as_of.isoformat(), "level": m.level, "members": m.members, "direction": direction, "verified": verified, "two_sources": two,
             "tickers": tickers, "falling": not m.up,
             "risk": False, "told": told, "spark": m.spark, "stat": f"{'+' if m.change > 0 else '-'}{abs(m.change):.1f}%" if m.kind in ("move", "wide") else writer_money(m),
             "published_at": m.as_of.isoformat(), "flag": "🇺🇸" if m.symbol == "US-STOCKS" else "", **({"urgency": u.out()} if u is not None else {}),
             "round_level": round_level, **({"milestone": ms} if ms else {}), **({"cross": cross} if cross else {}),
             **livedata.move_facts(m, verified, extra)}
    url = f"https://finance.yahoo.com/quote/{m.symbol}" if m.symbol not in ("CRYPTO", "US-STOCKS") else "https://finance.yahoo.com/markets/"
    src = [{"name": "Yahoo Finance", "url": url, "at": m.as_of.isoformat()}]
    if live:
        ev = max(live, key=lambda e: e.virality)  # the move is still on: the waiting post always carries the latest price and time
        ev.title = ev.headline = head
        ev.virality, ev.parts, ev.facts, ev.sources = float(score.total), score.parts, {**facts, "development": (ev.facts or {}).get("development", "")}, src
        ev.summary = livedata.market_summary(m, verified, extra)
        ev.expires_at = now + p.market_ttl
        return 0
    key = f"move:{m.key}:{m.kind}:{direction}:{now:%Y%m%d%H}" if parent is None else f"move-update:{m.key}:{parent.id}:{m.window}:{now:%Y%m%d%H%M}"
    ev = NdEvent(key=key[:200], kind="update" if parent else "market", parent_id=parent.id if parent else None, title=head, headline=head, summary=livedata.market_summary(m, verified, extra),
                 topic="move", label="", evidence="price data", category=str(m.item.get("category") or "crypto"), virality=float(score.total), parts=score.parts, sources=src,
                 facts={**facts, "development": "the move has deepened" if parent and not m.up else "the move has extended" if parent else ""}, status="candidate",
                 first_seen=now, expires_at=now + p.market_ttl)
    return 1 if _insert(db, recent, ev) else 0


def _item_of(symbol: str) -> dict[str, Any]:
    return next((x for x in moves.watch_items(sources.DEFAULT_WATCHLIST) if x["symbol"] == symbol), {"symbol": symbol})


def writer_money(m: moves.Move) -> str:
    from app.newsdesk import writer

    return writer.plain(m.price) if m.item.get("group") == "index" else writer.usd(m.price)


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


def _outage_story(ev: NdEvent) -> bool:
    """An infrastructure outage (app/newsdesk/incidents), whichever path admitted it (importance, or the chaos filter's "failure" topic). An
    exchange's halt that is still in force stays news for longer (withdrawals frozen); a resolved one does not."""
    from app.newsdesk import incidents

    inc = incidents.detect(ev.title or "")
    return inc is not None and (inc.kind != "exchange" or inc.resolved)


def release(db: Session, now: datetime, p: Policy = POLICY) -> NdEvent | None:
    """At most one event per pass. The best candidate that fits the plan goes; the others say why they wait."""
    out = list(db.scalars(select(NdEvent).where(NdEvent.status == RELEASED, NdEvent.released_at >= now - timedelta(hours=24))))
    waiting = sorted(db.scalars(select(NdEvent).where(NdEvent.status == "candidate")), key=lambda e: (-e.virality, e.id))
    held = [e for e in waiting if held_market(db, e)]
    for e in held:  # a pause, or a move no second market confirmed: it waits (and expires); news goes on. Said on each one
        why = held_market(db, e)
        if why.startswith("market-data posts are paused"):
            why += " (" + str(_pause_reason(db) or "owner") + ")"
        importance.record(e, importance.judge_event(e, _age(e, now)), "safety", "held", now, why)
    if held:
        waiting = [e for e in waiting if e not in held]
    if not waiting:
        return None
    if sched(p) == "slots":
        return _release_slot(db, now, p, out, waiting)
    times = sorted(_utc(e.released_at, now) for e in out)
    last_any = times[-1] if times else None
    regular = [e for e in out if e.slot == "regular"]
    extra = [e for e in out if e.slot == "extra"]
    window = window_start(now, p.window_hours) if p.window_hours else None
    into = (now - window).total_seconds() / (p.window_hours * 3600) if window is not None else 1.0
    bar = round(p.opening_bar - (p.opening_bar - p.regular_bar) * min(1.0, into / 0.75))
    last_regular = max((_utc(e.released_at, now) for e in regular), default=None)
    covered = editorial.coverage(db, now)
    for ev in waiting:
        f = ev.facts or {}
        age = _age(ev, now)
        if _repeat(ev, covered, now) or _blocked(ev):
            continue
        f = ev.facts or {}
        if ev.kind == "update":
            parent = db.get(NdEvent, ev.parent_id) if ev.parent_id else None
            if parent is None or parent.status != RELEASED:
                ev.why = "its original never went out"
                continue
            if now - (_utc(parent.released_at, now) or now) < p.update_gap:
                ev.why = "too soon after the original"
                continue
        if ev.kind != "update" and _shares_asset(ev, out, now, p.asset_cooldown) is not None:
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
        nw_why, label = _newsworthy(db, ev, label, now)
        if nw_why:  # owner, 2026-11-18: better news, not more news
            ev.why = nw_why
            continue
        f = ev.facts or {}  # now with its newsworthiness verdict
        problem = preflight(ev, label, now)
        if problem:  # both platforms must be able to carry it, or neither does: they stay in step
            ev.status, ev.why = "rejected", f"failed the checks: {problem}"
            continue
        ev.status, ev.slot, ev.released_at, ev.label = RELEASED, slot, now, label
        editorial.stamp(ev, now)
        ev.why = ("extraordinary: " if slot == "extra" else "") + f"scores {ev.virality:g} ({virality.describe(ev.parts or {})})"
        ev.x_status = ev.tg_status = "pending"
        logger.info("newsdesk events: released #%s [%s %s %s]: %s", ev.id, slot, int(ev.virality), ev.evidence, ev.headline[:100])
        return ev
    return None


def memory(db: Session, ev: NdEvent, covered: list[editorial.Covered], now: datetime) -> str:
    """THE repeat check (owner, 2026-11-23: one decision pipeline): is this event already out, on any lane, with nothing materially new?
    One question, asked once per pass, with three detectors behind it, strictest first: the story signature against released events
    (editorial.repeat_verdict, which knows a material development), what the channel actually delivered this week (quality.already_posted,
    by story identity), and the event family across every lane (newsworthy.repeat_of: Telegram, X, Trending, the Terminal). Returns the
    reason ("" when it is new). A repeat is folded away for good (status superseded)."""
    if _repeat(ev, covered, now):
        return ev.why or "a repeat of a story already out"
    told = quality.already_posted(db, ev, now)
    if told:
        ev.status, ev.why = "superseded", told
        return told
    title = ev.title or ev.headline or ""
    rep = newsworthy.repeat_of(title, ev.summary or "", newsworthy.told(db, now, skip_event=ev.id, platform="telegram"), now)
    if rep:
        ev.status, ev.why = "superseded", f"a repeat: {rep}"
        return ev.why
    return _same_incident(db, ev, now)


def _root(db: Session, ev: NdEvent) -> NdEvent:
    seen = 0
    while ev.parent_id and seen < 10:
        parent = db.get(NdEvent, ev.parent_id)
        if parent is None:
            break
        ev, seen = parent, seen + 1
    return ev


def _same_incident(db: Session, ev: NdEvent, now: datetime, p: "Policy | None" = None) -> str:
    """One incident told in other words (quality.same_incident), against what was released in the last 72 hours. Nothing new: folded away
    (superseded). A material development (quality.incident_development): it goes as an UPDATE of the original (kind update, parent_id: the
    lanes reply to the original), at most updates_per_story per story. Owner, 2026-11-23: no duplicates, never at the cost of a real
    development."""
    if ev.kind not in ("news", "update"):
        return ""
    p = p or POLICY
    title = ev.title or ev.headline or ""
    out = list(db.scalars(select(NdEvent).where(NdEvent.status == RELEASED, NdEvent.id != ev.id, NdEvent.kind.in_(("news", "update")),
                                                NdEvent.released_at >= now - timedelta(hours=quality.INCIDENT_WINDOW_H))))
    same = [e for e in out if quality.same_incident(e.title or e.headline or "", title, (now - _utc(e.released_at, now)).total_seconds() / 3600)]
    if not same:
        return ""
    root = _root(db, min(same, key=lambda e: _utc(e.released_at, now)))
    if ev.kind == "update" and ev.parent_id == root.id:
        return ""  # already this story's update: the update rules (events.development) decided
    thread = [root] + list(db.scalars(select(NdEvent).where(NdEvent.parent_id == root.id, NdEvent.status == RELEASED)))
    dev = quality.incident_development(title, ev.summary or "", [e.title or e.headline or "" for e in thread])
    if not dev:
        ev.status, ev.why = "superseded", f"the same incident as #{root.id} (\"{(root.headline or root.title or '')[:80]}\"), nothing materially new"
        ev.facts = {**(ev.facts or {}), "merged_into": root.id}
        return ev.why
    updates = sum(1 for e in thread if e.id != root.id)
    if updates >= p.updates_per_story:
        ev.status, ev.why = "superseded", f"a development of #{root.id} ({dev}), but it already had {updates} updates"
        return ev.why
    ev.kind, ev.parent_id = "update", root.id
    ev.facts = {**(ev.facts or {}), "development": dev, "new_because": dev}
    return ""


def review(db: Session, waiting: list[NdEvent], now: datetime, p: Policy, out: list[NdEvent]) -> list[tuple[NdEvent, "importance.Decision"]]:
    """Every waiting candidate, through the one pipeline, in this order: the owner's block, the repeat check, the rules that keep a thread
    coherent (an update needs its original out; a second move on one asset is an update), then the editor's judgement (app/newsdesk/
    importance). Each refusal is recorded on the event with its reason (facts.decisions; ev.why). Returns the eligible ones with their
    verdicts, best first: tier, then score (+ the priority subjects' bonus and a strong angle, - the diversity penalty)."""
    covered = editorial.coverage(db, now)
    keep: list[tuple[NdEvent, importance.Decision]] = []
    for ev in waiting:
        age = _age(ev, now)
        if _blocked(ev):
            importance.record(ev, None, "owner", "held", now, ev.why)
            continue
        rep = memory(db, ev, covered, now)
        if rep:
            importance.record(ev, None, "repeat", "refused", now, rep)
            continue
        if ev.kind == "update":
            parent = db.get(NdEvent, ev.parent_id) if ev.parent_id else None
            if parent is None or parent.status != RELEASED:
                importance.record(ev, None, "thread", "waiting", now, "an update: its original never went out")
                continue
        elif ev.kind == "market":
            same = _shares_asset(ev, out, now, p.asset_cooldown)
            if same is not None:
                importance.record(ev, None, "thread", "waiting", now, f"this asset's move went out at {_utc(same.released_at, now):%H:%M} UTC (#{same.id}): "
                                  "a deeper move is an update, not a new post")
                continue
        if ev.kind in ("news", "update") and age > INCIDENT_SLOT_MAX_MIN and _outage_story(ev):
            importance.record(ev, None, "recency", "refused", now, f"an infrastructure incident first reported {int(age)} min ago: news while it happens")
            continue
        d = importance.judge_event(ev, age)
        if ev.kind == "update" and d.tier == importance.NOISE and (ev.facts or {}).get("development"):
            # a material development of a story we told (events.development measured it) is worth telling, at least as notable
            d = importance.Decision(importance.NOTABLE, importance.TIER_AT[-1][1], f"notable: an update ({(ev.facts or {})['development']})", d.facts, d.parts, d.verified)
        if d.tier == importance.NOISE:
            importance.record(ev, d, "judge", "refused", now)
            continue
        keep.append((ev, d))

    def order(x: tuple[NdEvent, importance.Decision]) -> tuple[int, float, int]:
        ev, d = x
        bonus = (p.priority_bonus if priority(ev, p) else 0) + float((ev.facts or {}).get("angle_bonus") or 0)
        return (-d.rank, -(d.score + bonus - editorial.diversity(ev, covered, now)[0]), ev.id)

    return sorted(keep, key=order)


# Owner, 2026-11-24: "Do not impose an arbitrary daily limit on genuinely major breaking news." There is no daily count for MAJOR stories or
# MAJOR market moves (it was 6 and 3 a day). What stays: the minimum gap between posts, the asset cooldown, the repeat check (one event, one
# post) and a BURST guard, which is a malfunction detector, not a quota: more than MAJOR_BURST distinct majors released inside BURST_WINDOW
# means a feed or a judgement is probably broken; the next one waits for a person and the owner is told.
MAJOR_BURST = 4
BURST_WINDOW = timedelta(minutes=60)
MAJOR_PER_DAY = None  # removed 2026-11-24 (kept as a name for old imports)


def major_burst(out: list[NdEvent], now: datetime) -> str:
    """Why a major is held as a probable malfunction ("" = it isn't)."""
    recent = [e for e in out if (e.facts or {}).get("major") or (e.facts or {}).get("market_major")]
    recent = [e for e in recent if now - _utc(e.released_at, now) < BURST_WINDOW]
    if len(recent) >= MAJOR_BURST:
        return (f"{len(recent)} major posts in the last {int(BURST_WINDOW.total_seconds() // 60)} minutes: an unusual burst, held for a person "
                "(a feed or the judgement may be broken)")
    return ""


def _release(db: Session, ev: NdEvent, d: "importance.Decision", now: datetime, slot_kind: str, why: str, extra: dict[str, Any] | None = None) -> NdEvent | None:
    """The factual checks, then the release. A post that fails the checks is rejected with the reason and the next candidate is tried."""
    age = _age(ev, now)
    f = ev.facts or {}
    if ev.kind == "update":
        label = brief.UPDATE
    elif ev.kind == "data":
        label = ""
    else:
        label = virality.label_for(int(max(ev.virality, d.score)), topic=ev.topic, strong=ev.evidence in STRONG, age_minutes=age, risk=bool(f.get("risk")))
        if ev.kind in ("news",) and not (d.tier == importance.MAJOR and age <= 90):
            label = ""  # BREAKING / JUST IN only for a major event within 90 minutes of the first report (owner, 2026-11-18)
    problem = preflight(ev, label, now)
    if problem:  # both platforms must be able to carry it, or neither does
        ev.status = "rejected"
        importance.record(ev, d, "checks", "rejected", now, f"failed the checks: {problem}")
        return None
    ev.status, ev.slot, ev.released_at, ev.label = RELEASED, slot_kind, now, label
    ev.facts = {**(ev.facts or {}), "latency": latency(ev, now), **(extra or {})}
    if d.tier == importance.MAJOR:
        ev.facts = {**ev.facts, "major": d.reason}
    editorial.stamp(ev, now)
    importance.record(ev, d, "release", "released", now, f"{why}: {d.reason}" + (f"; new because {f['new_because']}" if f.get("new_because") else ""))
    ev.x_status = ev.tg_status = "pending"
    logger.info("newsdesk events: released #%s (%s, %s, score %.0f) [%s] (event %s min, seen %s min ago): %s", ev.id, why, d.tier, d.score, ev.evidence,
                ev.facts["latency"]["event_to_release_min"], ev.facts["latency"]["detect_to_release_min"], (ev.headline or "")[:100])
    return ev


def _release_slot(db: Session, now: datetime, p: Policy, out: list[NdEvent], waiting: list[NdEvent]) -> NdEvent | None:
    """Schedule "slots", with importance first (owner, 2026-11-23):
    1. a MAJOR market move on a priority asset goes now (its own lane, _release_market_major);
    2. a MAJOR verified story goes now, outside the slot plan, the diversity rule and any daily count; only the minimum gap between
       posts and the burst guard (MAJOR_BURST in BURST_WINDOW) hold it. A SIGNIFICANT verified story scoring breaking_bar or more may break too,
       within the lane's caps (breaking_per_day, breaking_gap). Close to an unfilled slot either goes AS that slot's post;
    3. an open slot takes the best SIGNIFICANT story, else the best NOTABLE one; diversity only ranks, except that two posts in a row from
       one desk hold a NOTABLE story; a story failing the factual checks is rejected and the next one is tried;
    4. otherwise the slot waits, and every candidate says why (tier, score, reason)."""
    slot = current_slot(now, p)
    open_slot = slot is not None and slot_filled(db, slot, p) is None
    if not open_slot:  # between slots, as before: an open slot's post is chosen from every candidate, moves and stories ranked together
        moved = _release_market_major(db, now, p, out, waiting)
        if moved is not None:
            return moved
    ranked = review(db, waiting, now, p, out)
    upcoming = next_slot(now, p)
    early = not open_slot and upcoming - now < p.breaking_before_slot and slot_filled(db, upcoming, p) is None
    day = [e for e in out if now - _utc(e.released_at, now) < timedelta(hours=24)]
    last = max((_utc(e.released_at, now) for e in day), default=None)
    gap_ok = last is None or now - last >= p.min_gap
    majors_today = sum(1 for e in day if (e.facts or {}).get("major"))
    breaking_today = sum(1 for e in day if e.slot == "breaking" and not (e.facts or {}).get("major") and not (e.facts or {}).get("market_major"))
    covered = editorial.coverage(db, now)
    # 2. breaking
    six_h = sum(1 for e in day if e.slot == "breaking" and not (e.facts or {}).get("major") and not (e.facts or {}).get("market_major")
                and now - _utc(e.released_at, now) < timedelta(hours=6))

    def lane_held() -> str:  # the wire style's measured moves: only MAJOR moves take this lane (owner, 2026-11-23)
        # owner, 2026-11-24: no daily or six-hour count on genuinely major news (breaking_per_day / breaking_per_6h held major moves before);
        # the spacing after the last post and the burst guard remain
        if last is not None and now - last < p.min_gap:
            return f"under {int(p.min_gap.total_seconds() // 60)} minutes since the last post"
        return major_burst(out, now)

    for ev, d in ranked:
        # owner, 2026-11-23: immediate breaking ONLY for verified MAJOR events; significant and notable stories take the six slots.
        # The wire style's extra way for a measured move (exceptional, live, a priority asset) therefore needs a MAJOR move too
        way = _wire_breaking(db, ev, now, p, out, ev.virality) if p.wire and d.tier == importance.MAJOR and ev.kind not in ("news", "update") else ""
        if way:
            held = lane_held()
            if held:
                _note_breaking_miss(ev, held, now)
                importance.record(ev, d, "breaking", "waiting", now, f"{way}, held from breaking: {held}")
                continue
            rel = _release(db, ev, d, now, "breaking", f"breaking ({way}), posted now instead of waiting for the {slot_label(upcoming)} Dubai slot")
            if rel is not None:
                return rel
            continue
        if ev.kind not in ("news", "update") or not d.verified or _age(ev, now) > p.breaking_max_age.total_seconds() / 60:
            continue
        if d.tier != importance.MAJOR:
            _note_breaking_miss(ev, f"{d.tier}, not major: it takes the next slot (only a verified major event breaks)", now)
            continue
        held = "" if gap_ok else f"under {int(p.min_gap.total_seconds() // 60)} minutes since the last post"
        held = held or major_burst(out, now)  # never a daily count (owner, 2026-11-24)
        if held:
            _note_breaking_miss(ev, held, now)
            importance.record(ev, d, "breaking", "waiting", now, f"{d.tier}, held from breaking: {held}")
            continue
        if open_slot or early:
            target = slot if open_slot else upcoming
            rel = _release(db, ev, d, now, "slot", f"breaking ({d.tier}, confirmed by {len(outlets_of(ev))} outlets), posted now as the "
                           f"{slot_label(target)} Dubai slot's post instead of waiting for it",
                           {"slot_at": target.isoformat(), **({"early": True} if early else {})})
        else:
            rel = _release(db, ev, d, now, "breaking", f"breaking ({d.tier}, confirmed by {len(outlets_of(ev))} outlets), posted now instead of "
                           f"waiting for the {slot_label(upcoming)} Dubai slot")
        if rel is not None:
            return rel
    # 3. the slot
    if not open_slot:
        for ev, d in ranked:
            importance.record(ev, d, "slot", "waiting", now, f"waiting for the {slot_label(upcoming)} Dubai slot: {d.reason}")
        return None
    if not gap_ok:
        for ev, d in ranked:
            importance.record(ev, d, "slot", "waiting", now, f"the slot is open, but under {int(p.min_gap.total_seconds() // 60)} minutes since the last post: {d.reason}")
        return None
    for ev, d in ranked:
        if d.tier == importance.NOTABLE and not d.verified and ev.kind in ("news", "update"):
            # owner, 2026-11-23 (quality above quantity): a minor story from one unofficial outlet never takes a post
            importance.record(ev, d, "slot", "waiting", now, f"a notable story from one outlet: it fills a slot only once confirmed ({d.reason})")
            continue
        if d.tier == importance.NOTABLE:
            run = editorial.diversity(ev, covered, now)[1]
            if run:
                importance.record(ev, d, "diversity", "waiting", now, f"{run} (a notable story; significant ones are never held by it)")
                continue
            topical = sum(1 for e in out if e.topic == ev.topic and e.kind != "recap" and now - _utc(e.released_at, now) < p.topic_window)
            if topical >= p.topic_max:
                importance.record(ev, d, "diversity", "waiting", now, f"{topical} {ev.topic or 'other'} posts on this subject in 12 hours (a notable story)")
                continue
        rel = _release(db, ev, d, now, "slot", f"the {slot_label(slot)} Dubai slot", {"slot_at": slot.isoformat()})
        if rel is not None:
            return rel
    return None


def _release_breaking(db: Session, now: datetime, p: Policy, out: list[NdEvent], waiting: list[NdEvent]) -> NdEvent | None:
    """Kept for the callers that ask for the between-slots lane on its own: the same pipeline (_release_slot) decides."""
    return _release_slot(db, now, p, out, waiting)


STRONG_BYPASS_PER_DAY = 3  # owner, 2026-11-18: strong (not major) stories that may break through the lane's limits in 24 h


def _newsworthy(db: Session, ev: NdEvent, label: str, now: datetime) -> tuple[str, str]:
    """The last editorial question before any release, slot or breaking (owner, 2026-11-18): why should anyone care, and is it new on ANY
    lane (Telegram, X, Trending, released events)? Returns (why not, or "", the label it may carry). A slot is never filled for its own
    sake: when nothing passes, the slot stays empty. BREAKING / JUST IN only for a MAJOR consequence within 90 minutes of the first report."""
    title = ev.title or ev.headline or ""
    if ev.kind not in ("news", "update"):
        if ev.kind == "market":
            rep = newsworthy.repeat_of(title, ev.summary or "", newsworthy.told(db, now, skip_event=ev.id, platform="telegram"), now)
            if rep:
                return f"not published: {rep}", label
        return "", label
    # a release goes to both platforms: it is a repeat when the channel already carried the event (X then skips it on its own if X did)
    a = newsworthy.judge(db, title, ev.summary or "", now, age_min=_age(ev, now), skip_event=ev.id, platform="telegram", outlets=len(outlets_of(ev)))
    if not a.ok:
        _note_breaking_miss(ev, a.reason, now)
        logger.info("editorial gate: #%s refused (interest %s): %s | %s", ev.id, a.interest, a.reason[:200], title[:100])
        from app.newsdesk import validation

        validation.record_gate(db, ev, False, a.interest, a.reason)
        return f"not published: {a.reason}", label
    ev.facts = {**(ev.facts or {}), "newsworthy": {"why": a.why, "strength": a.strength, "stop_scroll": a.stop_scroll, "parts": a.parts}}
    logger.info("editorial gate: #%s qualifies (%s, interest %s): %s | %s", ev.id, a.strength, a.interest, (a.pitch or a.why)[:160], title[:100])
    from app.newsdesk import validation

    validation.record_gate(db, ev, True, a.interest, a.pitch or a.why)  # measured in production (owner, 2026-11-18)
    if ev.kind == "update" or not label:
        return "", label
    return "", (label if a.strength == "major" and _age(ev, now) <= 90 else "")


def verified_major(ev: NdEvent) -> bool:
    """Two or more independent credible outlets on a major event, whatever topic the desk filed it under (production replay: the Apple
    outage was filed as a stock "move", so verified_important said no)."""
    return ev.kind in ("news", "update") and ev.evidence in CREDIBLE and len(outlets_of(ev)) >= 2 and bool(major_breaking(ev))


def major_breaking(ev: NdEvent) -> str:
    """Why this verified news event is major enough to break through the lane's spacing and caps (app/terminal/intel.impact_class), or "".
    Never a hedged or speculative report; two or more independent outlets are already required (verified_important)."""
    from app.newsdesk import incidents
    from app.terminal import intel

    title = ev.title or ""
    if ev.kind not in ("news", "update") or intel.RUMOR.search(title):
        return ""
    names = outlets_of(ev)
    inc = incidents.detect(title, ev.summary or "")
    parts = {"incident": {"major": True, "verified": len(names) >= 2, "label": inc.label}} if inc is not None and inc.major else {}
    srcs = [{"source": "news", "outlet": n, "title": title, "reliable": True, "extra": {}} for n in names]
    kind, why = intel.impact_class(title, ev.summary or "", srcs, parts)
    return why if kind else ""


def _release_market_major(db: Session, now: datetime, p: Policy, out: list[NdEvent], waiting: list[NdEvent]) -> NdEvent | None:
    """Between slots: a MAJOR or SIGNIFICANT measured move on a priority asset goes now, majors first (owner, 2026-11-20: "significant
    market moves publish promptly without waiting hours"). Production: the silver crash (-6.4% in 24 h, then -6.75% on the day, its biggest
    one-day drop in 189 days) waited from before the 00:00 UTC slot to the 04:00 one and was then stale. Every repeat, diversity, newsworthiness and accuracy check still applies; its own
    caps (Policy.market_breaking_per_day, min_gap, the asset cooldown) keep a market-wide day from flooding the channel. Close to a slot it
    goes AS that slot's post, so the day's count is unchanged."""
    # owner, 2026-11-23: between slots only a MAJOR move (the editor's tier, which reads the urgency); a significant one takes the next slot
    majors = [e for e in waiting if e.kind == "market" and urgency_of(e) in (urgency.MAJOR, urgency.SIGNIFICANT) and priority(e, p)
              and importance.judge_event(e, _age(e, now)).tier == importance.MAJOR]
    if not majors:
        return None
    day = [e for e in out if now - _utc(e.released_at, now) < timedelta(hours=24)]
    last = max((_utc(e.released_at, now) for e in day), default=None)
    upcoming = next_slot(now, p)
    early = upcoming - now < p.breaking_before_slot and slot_filled(db, upcoming, p) is None
    closed = ""
    # 2026-11-24: no daily count for major moves (was market_breaking_per_day); one post per asset (the cooldown below), the gap and the burst guard
    if last is not None and now - last < p.min_gap:
        closed = f"under {int(p.min_gap.total_seconds() // 60)} minutes since the last post"
    else:
        closed = major_burst(out, now)
    covered = editorial.coverage(db, now)
    for ev in sorted(majors, key=lambda e: (urgency_of(e) != urgency.MAJOR, -e.virality, e.id)):
        u = (ev.facts or {}).get("urgency") or {}
        if closed:
            _note_breaking_miss(ev, f"{urgency_of(ev)} market move held: {closed}", now)
            continue
        same = _shares_asset(ev, out, now, p.asset_cooldown)
        if same is not None:
            ev.why = f"this asset went out at {_utc(same.released_at, now):%H:%M} UTC (#{same.id}): a deeper move is an update, not a new post"
            continue
        if _repeat(ev, covered, now) or _blocked(ev):
            continue
        # owner, 2026-11-23: diversity never holds a major or significant market move (it only ranks): no hard hold here
        label = virality.label_for(int(ev.virality), topic=ev.topic, strong=True, age_minutes=_age(ev, now), risk=False)
        nw_why, label = _newsworthy(db, ev, label, now)
        if nw_why:
            ev.why = nw_why
            continue
        f = ev.facts or {}
        problem = preflight(ev, label, now)
        if problem:
            ev.status, ev.why = "rejected", f"failed the checks: {problem}"
            continue
        ev.status, ev.slot, ev.released_at, ev.label = RELEASED, ("slot" if early else "breaking"), now, label
        ev.facts = {**f, "latency": latency(ev, now), "market_major": u.get("why") or "major move", **({"slot_at": upcoming.isoformat(), "early": True} if early else {})}
        editorial.stamp(ev, now)
        ev.why = (f"{urgency_of(ev)} market move ({u.get('why')}): scores {ev.virality:g} ({virality.describe(ev.parts or {})}); "
                  + (f"posted now as the {slot_label(upcoming)} Dubai slot's post" if early else f"posted now instead of waiting for the {slot_label(upcoming)} Dubai slot"))
        ev.x_status = ev.tg_status = "pending"
        logger.info("newsdesk events: released #%s as a %s MARKET MOVE%s [%s, %s] (event %s min, seen %s min ago): %s", ev.id, urgency_of(ev).upper(),
                    f" for the {slot_label(upcoming)} Dubai slot" if early else "", int(ev.virality), u.get("why"),
                    ev.facts["latency"]["event_to_release_min"], ev.facts["latency"]["detect_to_release_min"], ev.headline[:100])
        return ev
    return None


_MISSES_LOGGED: dict[tuple[int, str, str], None] = {}


def _note_breaking_miss(ev: NdEvent, reason: str, now: datetime) -> None:
    """Why a fresh, verified important story did not break between slots: kept on the event (facts.breaking_miss, Admin shows facts) and
    logged once per reason, so production shows exactly what held a story back (2026-11-16: the Iran drone strike on a US base in Bahrain
    waited 130 minutes for a slot and nothing said why)."""
    f = ev.facts or {}
    if f.get("breaking_miss") != reason:
        ev.facts = {**f, "breaking_miss": reason}
    key = (ev.id, ev.title or "", reason)  # the facts are rewritten when a story is re-scored, so the log keeps its own memory (per process)
    if key in _MISSES_LOGGED:
        return
    _MISSES_LOGGED[key] = None
    while len(_MISSES_LOGGED) > 2000:
        _MISSES_LOGGED.pop(next(iter(_MISSES_LOGGED)))
    logger.info("newsdesk events: #%s not breaking (%s) [%s %s, %d outlets, %d min old]: %s", ev.id, reason, int(ev.virality), ev.evidence,
                len(outlets_of(ev)), int(_age(ev, now)), (ev.headline or ev.title or "")[:100])


def _wire_breaking(db: Session, ev: NdEvent, now: datetime, p: Policy, out: list[NdEvent], bare: float) -> str:
    """The wire style's two extra ways to break between slots (what makes it, or ""). Both still meet every rule below in _release_breaking
    (repeats, diversity, the owner's block, the checks) and its caps.
    - an exceptional measured move or liquidation flush (editorial.exceptional) on a priority asset, scoring market_bar, while it happens,
      and not this asset's second move in asset_cooldown;
    - an update with a material development (editorial.material_development) on a story already out, strong evidence, regular_bar,
      update_gap after the original, fresh."""
    f = ev.facts or {}
    if ev.kind == "market":
        if ev.virality < p.market_bar or not priority(ev, p) or _age(ev, now) > p.breaking_market_age.total_seconds() / 60:
            return ""
        big, detail = editorial.exceptional(ev)
        if not big:
            return ""
        if _shares_asset(ev, out, now, p.asset_cooldown) is not None:
            return ""
        return f"exceptional market move: {detail}"
    if ev.kind == "update":
        parent = db.get(NdEvent, ev.parent_id) if ev.parent_id else None
        if parent is None or parent.status != RELEASED or now - (_utc(parent.released_at, now) or now) < p.update_gap:
            return ""
        if not editorial.material_development(str(f.get("development") or "")) or ev.evidence not in STRONG or bare < p.regular_bar:
            return ""
        if _age(ev, now) > p.breaking_max_age.total_seconds() / 60:
            return ""
        return f"material development: {f.get('development')}"
    return ""


TRACK = timedelta(days=5)  # how long a story we posted is followed for material developments (updates_per_story caps how many go out)


def parent_told(parent: NdEvent) -> str:
    return str((parent.facts or {}).get("told") or parent.title or "")[:200]


def _repeat(ev: NdEvent, covered: list[editorial.Covered], now: datetime) -> bool:
    """The same story as one already out, with nothing materially new: folded away for good (status superseded), with the reason."""
    v = editorial.repeat_verdict(ev, covered, now)
    if v.ok:
        if v.reason:
            f = ev.facts or {}
            src = f.get("development_source") or {}
            reason = v.reason
            if ev.kind == "update" and f.get("development"):
                reason += f" ({f['development']}" + (f", reported by {src.get('outlet')}: \"{str(src.get('headline') or '')[:120]}\"" if src else "") + ")"
            ev.facts = {**f, "new_because": reason}
        return False
    ev.status, ev.why = "superseded", v.reason
    ev.facts = {**(ev.facts or {}), "merged_into": v.same_as}
    return True


def _blocked(ev: NdEvent) -> bool:
    """The owner blocked this candidate from Admin's preview queue: it waits, never released, until unblocked."""
    b = (ev.facts or {}).get("blocked") or {}
    if b.get("on"):
        ev.why = "blocked by the owner" + (f": {b.get('reason')}" if b.get("reason") else "")
        return True
    return False


async def fill_with_recap(db: Session, now: datetime, p: Policy, quotes: dict[str, sources.Quote], market: Any = None) -> NdEvent | None:
    """Schedule "slots": an open slot no story filled gets the verified market check. The key (recap:<slot>) makes it once per slot,
    whichever lane runs first."""
    from app.newsdesk import recap

    slot = current_slot(now, p)
    if sched(p) != "slots" or not p.recap or slot is None or slot_filled(db, slot, p) is not None:
        return None
    if db.scalar(select(NdEvent.id).where(NdEvent.key == f"recap:{slot:%Y-%m-%dT%H:%M}")) is not None:
        return None
    try:
        ev = await (recap.build_visual if recap.VISUAL else recap.build)(db, now, slot, slot_label(slot), quotes, market)
    except Exception as e:  # noqa: BLE001 - the market check failing never stops the desk; the slot alert tells the owner
        logger.warning("newsdesk recap: failed for the %s slot (%s)", slot_label(slot), type(e).__name__)
        db.rollback()
        return None
    if ev is None:
        return None
    ev.status, ev.slot, ev.released_at = RELEASED, "slot", now
    editorial.stamp(ev, now)
    ev.why = f"the {slot_label(slot)} Dubai slot: no story cleared the bar of {p.regular_bar}, so the biggest real market move" if recap.VISUAL else f"the {slot_label(slot)} Dubai slot: no story cleared the bar of {p.regular_bar}, so a market brief from measured data"
    ev.x_status = ev.tg_status = "pending"
    if not _insert(db, [], ev):
        db.rollback()
        return None
    db.commit()
    logger.info("newsdesk events: released #%s for the %s Dubai slot: market check (%s)", ev.id, slot_label(slot), ", ".join((ev.facts or {}).get("recap_assets") or []))
    return ev


FAILED = ("missed", "failed", "skipped", "uncertain")
SKIP_REASON = "nothing new and worthwhile: no verified story cleared the bar, no exceptional market move the channel hasn't already shown"


def note_skip(db: Session, now: datetime, p: Policy) -> None:
    """An open slot the desk looked at and chose to leave empty (owner, 2026-11-10: skip rather than repeat or fill). Recorded in the desk's
    state, so the schedule shows it as skipped, not missed, and the owner isn't alerted as if the server were down."""
    from app.models.newsdesk import NdSettings

    slot = current_slot(now, p)
    row = db.get(NdSettings, 1)
    if slot is None or row is None or slot_filled(db, slot, p) is not None:
        return
    skips = dict((row.state or {}).get("skipped_slots") or {})
    if slot.isoformat() in skips:
        return
    skips[slot.isoformat()] = SKIP_REASON
    row.state = {**(row.state or {}), "skipped_slots": dict(sorted(skips.items())[-24:])}
    # what the desk passed over, in the logs (2026-11-16: two overnight slots went empty and production had no record of why)
    waiting = list(db.scalars(select(NdEvent).where(NdEvent.status == "candidate")))
    # 2026-11-24: ranked by the editor's tier first (production 11-23 listed three high-virality refusals and never said why approved,
    # lower-virality stories such as #1200 US sanctions on Iran were passed over), and an empty reason says which pause holds it
    order = {"major": 0, "significant": 1, "notable": 2}
    best = sorted(waiting, key=lambda e: (order.get(_tier(e), 3), -e.virality, e.id))[:6]

    def _why(e: NdEvent) -> str:
        return (e.why or held_market(db, e) or "no reason recorded")[:140]

    logger.info("newsdesk events: skipped the %s Dubai slot; best waiting: %s", slot_label(slot),
                " | ".join(f"#{e.id} {_tier(e) or '-'} {int(e.virality)} {e.evidence} \"{(e.headline or e.title or '')[:70]}\": {_why(e)}" for e in best) or "none")
    # and why, for the owner alert when slots keep going empty (2026-11-22: every slot from 11-21 12:00 UTC was skipped and nobody was told)
    details = dict((row.state or {}).get("skip_details") or {})
    important = [e for e in waiting if _tier(e) in IMPORTANT]
    details[slot.isoformat()] = {"important": [f"#{e.id} {_tier(e)}: \"{(e.headline or e.title or '')[:100]}\": {(e.why or 'no reason recorded')[:200]}"
                                               for e in sorted(important, key=lambda e: (_tier(e) != "major", e.id))[:5]],
                                 "reasons": refusal_reasons(waiting),
                                 "best": (f"#{best[0].id} scores {best[0].virality:g} ({best[0].evidence}) \"{(best[0].headline or best[0].title or '')[:100]}\": "
                                          f"{(best[0].why or 'no reason recorded')[:160]}") if best else ""}
    row.state = {**(row.state or {}), "skip_details": dict(sorted(details.items())[-24:])}
    db.commit()


def _reason_key(why: str) -> str:
    """Refusals that differ only in their numbers are one reason ("scores 41, under the bar of 56" and "scores 38, ...")."""
    return re.sub(r"\s+", " ", re.sub(r"\d[\d.,:%]*", "#", why or "")).strip()[:160]


def refusal_reasons(waiting: list[NdEvent], top: int = 5) -> list[list[Any]]:
    """The commonest reasons the waiting candidates were refused: [[one example, how many], ...], commonest first."""
    seen: dict[str, list[Any]] = {}
    for e in waiting:
        why = (e.why or "no reason recorded").strip()
        k = _reason_key(why)
        if k in seen:
            seen[k][1] += 1
        else:
            seen[k] = [why[:200], 1]
    return sorted(seen.values(), key=lambda r: -r[1])[:top]


def skipped_slots(db: Session) -> dict[str, str]:
    from app.models.newsdesk import NdSettings

    row = db.get(NdSettings, 1)
    return dict((row.state or {}).get("skipped_slots") or {}) if row is not None else {}
ALERT_HOURS = 6
_alerted: dict[str, datetime] = {}  # this process: each alert key handled once per ALERT_HOURS, even while the owner chat is unset (no log spam)


def slots_since(db: Session, now: datetime | None = None) -> datetime | None:
    """When the six-slot schedule started on this database (stored in the desk's state). Slots before it were never owed, so they are
    neither shown as missed nor alerted on. With `now`, the moment is recorded if it isn't yet."""
    from app.models.newsdesk import NdSettings

    row = db.get(NdSettings, 1)
    if row is None:
        return None
    at = (row.state or {}).get("slots_since")
    if at:
        return datetime.fromisoformat(at)
    if now is not None:
        row.state = {**(row.state or {}), "slots_since": now.isoformat()}
        db.commit()
        return now
    return None  # only slots this recent are alerted on, so each problem is told once (the owner-alert cooldown is 6 hours)


def _fresh(key: str, now: datetime) -> bool:
    last = _alerted.get(key)
    if last is not None and now - last < timedelta(hours=ALERT_HOURS):
        return False
    _alerted[key] = now
    return True


async def alert_slots(db: Session, now: datetime, p: Policy) -> int:
    """Owner alerts, never silent: a missed slot, and a platform that failed to deliver a slot's post. Returns how many were sent."""
    if sched(p) != "slots":
        return 0
    from app.alerts import notify_owner

    sent = 0
    since = slots_since(db, now)
    for sl in schedule(db, now, p)["slots"]:
        at = datetime.fromisoformat(sl["at"])
        if now - at > timedelta(hours=ALERT_HOURS) or (since is not None and at < since - p.slot_catch_up):
            continue
        if sl["state"] == "missed" and _fresh(f"newsdesk:slot:{sl['at']}", now):
            sent += await notify_owner(db, f"newsdesk:slot:{sl['at']}", f"News Desk: the {sl['dubai']} Dubai slot was missed. Nothing was released within "
                                       f"{int(p.slot_catch_up.total_seconds() // 60)} minutes: no story cleared the bar and no market check could be verified, "
                                       "or the server was down. Check Admin > X > News Desk.", now=now)
        for platform, status in (("Telegram", sl["telegram"]), ("X", sl["x"])):
            if sl["event"] and status in FAILED and _fresh(f"newsdesk:delivery:{sl['event']}:{platform}", now):
                ev = db.get(NdEvent, sl["event"])
                note = (ev.tg_note if platform == "Telegram" else ev.x_note) if ev is not None else ""
                sent += await notify_owner(db, f"newsdesk:delivery:{sl['event']}:{platform}", f"News Desk: the {sl['dubai']} Dubai post (event #{sl['event']}) "
                                           f"was not delivered to {platform}: {status}" + (f" ({note[:240]})" if note else "") + "."
                                           + (f"\n{(ev.headline or '')[:160]}" if ev is not None else "") + "\nCheck Admin > Terminal.", now=now)
    # owner, 2026-11-20: posts released between slots (breaking news, major market moves) are owed too: one that a platform didn't deliver
    # is told the same way
    for ev in db.scalars(select(NdEvent).where(NdEvent.status == RELEASED, NdEvent.slot.in_(("breaking", "extra")),
                                               NdEvent.released_at >= now - timedelta(hours=ALERT_HOURS))):
        for platform, status, note in (("Telegram", ev.tg_status, ev.tg_note), ("X", ev.x_status, ev.x_note)):
            key = f"newsdesk:delivery:{ev.id}:{platform}"
            if status in FAILED and _fresh(key, now):
                at = _utc(ev.released_at, now)
                sent += await notify_owner(db, key, f"News Desk: the {at.astimezone(DUBAI):%H:%M} Dubai {(urgency_of(ev) or 'major') + ' market move' if (ev.facts or {}).get('market_major') else 'breaking post'} "
                                           f"(event #{ev.id}) was not delivered to {platform}: {status}" + (f" ({(note or '')[:240]})" if note else "") + "."
                                           f"\n{(ev.headline or '')[:160]}\nCheck Admin > Terminal.", now=now)
    sent += await _alert_skip_run(db, now, p)
    sent += await _alert_important_lost(db, now)
    return sent


IMPORTANT = ("major", "significant")


def _tier(ev: NdEvent) -> str:
    return str(((ev.facts or {}).get("editor") or {}).get("tier") or "")


async def _alert_important_lost(db: Session, now: datetime) -> int:
    """Owner, 2026-11-23: "Alert me when significant stories are rejected". A major or significant story (the editor's tier) that ended
    without going out (missed: no slot before it went stale; rejected: it failed the factual checks) is told once, with its last reason.
    Repeats (superseded) are not losses and are not told. Never raises."""
    from app.alerts import notify_owner

    try:
        lost = [e for e in db.scalars(select(NdEvent).where(NdEvent.status.in_(("missed", "rejected")), NdEvent.updated_at >= now - timedelta(hours=6)))
                if _tier(e) in IMPORTANT and not (e.facts or {}).get("lost_alerted")]
        if not lost:
            return 0
        lines = [f"News Desk: {len(lost)} {'story' if len(lost) == 1 else 'stories'} rated major or significant did not go out:"]
        for e in lost[:6]:
            lines.append(f"- #{e.id} {_tier(e)}, {e.status}: \"{(e.headline or e.title or '')[:120]}\": {(e.why or 'no reason recorded')[:220]}")
        lines.append("Check Admin > Terminal (each story's decision history is on the event).")
        key = "newsdesk:lost:" + ",".join(str(e.id) for e in lost[:6])
        sent = await notify_owner(db, key, "\n".join(lines), now=now) if _fresh(key, now) else 0
        for e in lost:
            e.facts = {**(e.facts or {}), "lost_alerted": now.isoformat()}
        db.commit()
        return sent
    except Exception as e:  # noqa: BLE001 - an alert never breaks the lane that asked
        logger.warning("newsdesk events: the lost-story alert failed (%s)", type(e).__name__)
        db.rollback()
        return 0


SKIP_ALERT_AFTER = 2  # empty (skipped) slots in a row before the owner is told; told again after each further SKIP_ALERT_AFTER


async def _alert_skip_run(db: Session, now: datetime, p: Policy) -> int:
    """Owner, 2026-11-22: production skipped every slot from 11-21 12:00 UTC into 11-22 and nobody was alerted (a skip is by design, so it
    never counted as missed). Slots are followed in order once each is decided (filled, skipped, missed); the run of skipped slots in a row
    is kept in the desk's state (skip_run). At SKIP_ALERT_AFTER skips in a row: ONE owner alert with the top refusal reasons and the best
    waiting candidate; no repeat until SKIP_ALERT_AFTER more skips or a slot is filled. A filled slot after an alerted run sends one
    "recovered" message. A missed slot ends the run quietly (it has its own alert). Never raises."""
    from app.alerts import notify_owner
    from app.models.newsdesk import NdSettings

    try:
        row = db.get(NdSettings, 1)
        if row is None:
            return 0
        state = row.state or {}
        run = dict(state.get("skip_run") or {})
        seen = datetime.fromisoformat(run["seen"]) if run.get("seen") else None
        details = state.get("skip_details") or {}
        decided = [s for s in schedule(db, now, p)["slots"] if s["state"] in ("filled", "skipped", "missed", "before")
                   and (seen is None or datetime.fromisoformat(s["at"]) > seen)]
        if not decided:
            return 0
        sent = 0
        for s in decided:
            run["seen"] = s["at"]
            if s["state"] == "skipped":
                important = (details.get(s["at"]) or {}).get("important") or []
                key = f"newsdesk:skipped-important:{s['at']}"
                if important and _fresh(key, now):  # owner, 2026-11-23: never a silent empty slot while a significant story waits
                    sent += await notify_owner(db, key, f"News Desk: the {s['dubai']} Dubai slot went empty while major or significant stories were waiting:\n"
                                               + "\n".join(f"- {x}" for x in important) + "\nCheck Admin > Terminal.", now=now)
                if not run.get("first"):
                    run.update(first=s["at"], count=0, alerted=0)
                run["count"] = int(run.get("count") or 0) + 1
                run["last"] = s["at"]
                continue
            if s["state"] == "filled" and run.get("alerted"):
                key = f"newsdesk:skipped:{run['first']}:recovered"
                if _fresh(key, now):
                    sent += await notify_owner(db, key, f"News Desk recovered: the {s['dubai']} Dubai slot was filled (event #{s['event']}) after "
                                               f"{run.get('count')} empty slots in a row.\n{(s.get('headline') or '')[:160]}", now=now)
            run = {"seen": run["seen"]}  # filled, missed or before the schedule: the run of skips, if any, is over
        count, first = int(run.get("count") or 0), run.get("first")
        if first and count >= SKIP_ALERT_AFTER and count // SKIP_ALERT_AFTER > int(run.get("alerted") or 0):
            key = f"newsdesk:skipped:{first}:{count // SKIP_ALERT_AFTER}"
            run["alerted"] = count // SKIP_ALERT_AFTER
            if _fresh(key, now):
                sent += await notify_owner(db, key, _skip_run_text(run, details, now, p), now=now)
            row = db.get(NdSettings, 1)  # read again: notify_owner commits
        row.state = {**(row.state or {}), "skip_run": run}
        db.commit()
        return sent
    except Exception as e:  # noqa: BLE001 - the skip alert never breaks the slot alerts or the lane that asked
        logger.warning("newsdesk events: the skipped-slots alert failed (%s)", type(e).__name__)
        db.rollback()
        return 0


def _skip_run_text(run: dict[str, Any], details: dict[str, Any], now: datetime, p: Policy) -> str:
    first, last = datetime.fromisoformat(run["first"]), datetime.fromisoformat(run["last"])
    slots = [t for t in sorted(details) if first <= datetime.fromisoformat(t) <= last]
    merged: dict[str, list[Any]] = {}
    for t in slots:
        for example, n in (details[t].get("reasons") or []):
            k = _reason_key(example)
            if k in merged:
                merged[k][1] += n
            else:
                merged[k] = [example, n]
    top = sorted(merged.values(), key=lambda r: -r[1])[:3]
    best = next((details[t].get("best") for t in reversed(slots) if details[t].get("best")), "")
    lines = [f"News Desk: {run['count']} slots in a row went empty (skipped), from the {slot_label(first)} Dubai slot ({first:%m-%d %H:%M} UTC) "
             f"to the {slot_label(last)} Dubai slot ({last:%m-%d %H:%M} UTC). No story cleared the bar of {p.regular_bar}."]
    lines.append("Top reasons the waiting stories were refused:" if top else "Top reasons: none recorded (no candidates were waiting, or the skips came before this alert existed).")
    lines += [f"- {example[:200]} ({n}x)" for example, n in top]
    lines.append(f"Best waiting: {best}" if best else "Best waiting: none")
    lines.append("Check Admin > X > News Desk.")
    return "\n".join(lines)


def schedule(db: Session, now: datetime, p: Policy) -> dict[str, Any]:
    """What Admin and the monitors show: every slot of the last 24 hours with what filled it and each platform's delivery, the next slot,
    the last delivery per platform."""
    slots = []
    since = slots_since(db)
    skips = skipped_slots(db)
    for t in slot_times(now, p):
        if t > now:
            continue
        ev = slot_filled(db, t, p)
        state = "filled" if ev is not None else ("open" if now - t <= p.slot_catch_up else "missed")
        if ev is None and t.isoformat() in skips and state == "missed":
            state = "skipped"  # the desk looked and found nothing worth posting: by design, not a fault
        if ev is None and since is not None and t < since - p.slot_catch_up:
            state = "before"  # the schedule wasn't running yet
        slots.append({"at": t.isoformat(), "dubai": slot_label(t), "state": state, "event": ev.id if ev else None, "kind": ev.kind if ev else None,
                      "headline": ev.headline if ev else None, "x": ev.x_status if ev else None, "telegram": ev.tg_status if ev else None,
                      "telegram_message_id": ev.tg_message_id if ev else None})
    last_tg = db.scalar(select(NdEvent).where(NdEvent.tg_status == "delivered").order_by(NdEvent.released_at.desc()).limit(1))
    last_x = db.scalar(select(NdEvent).where(NdEvent.x_status.in_(("published", "dry_run"))).order_by(NdEvent.released_at.desc()).limit(1))
    nxt = next_slot(now, p)
    return {"schedule": sched(p), "slot_hours_dubai": list(p.slot_hours), "next_slot": nxt.isoformat(), "next_slot_dubai": slot_label(nxt), "slots": slots,
            "last_telegram": {"event": last_tg.id, "at": last_tg.released_at.isoformat() if last_tg.released_at else None, "message_id": last_tg.tg_message_id} if last_tg else None,
            "last_x": {"event": last_x.id, "at": last_x.released_at.isoformat() if last_x.released_at else None, "status": last_x.x_status} if last_x else None,
            "failures": [{"event": x["event"], "dubai": x["dubai"], "x": x["x"], "telegram": x["telegram"]} for x in slots
                         if x["event"] and (x["telegram"] in FAILED or x["x"] in FAILED)]}


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


def liquidation_post(ev: NdEvent) -> bool:
    return (ev.topic or "") == "liquidations" or any(str(a).startswith("LIQ:") for a in ((ev.facts or {}).get("assets") or []))


def held_market(db: Session, ev: NdEvent) -> str:
    """Why a market post is held by a pause ("" = it isn't): the owner's (or a price-data safety stop's) market_pause holds every market
    post; a liquidation-data stop (liq_pause) holds liquidation posts only (owner, 2026-11-23: "A liquidation-data problem must never
    silence independently verified market news")."""
    if not market_post(ev):
        return ""
    if market_paused(db):
        return "market-data posts are paused"
    if liquidation_post(ev):
        if liquidations_paused(db):
            return "liquidation posts are paused (liquidation data held as implausible)"
    else:  # 2026-11-24: a price-data safety stop holds only its own asset class (state.class_pause), never liquidation-paused or other classes
        from app.models.newsdesk import NdSettings

        s = db.get(NdSettings, 1)
        state = (s.state or {}) if s is not None else {}
        if state.get("class_pause"):
            f = ev.facts or {}
            symbols = [str(f.get("symbol") or "")] + [str(x.get("symbol") or "") for x in (f.get("figures") or []) if isinstance(x, dict)]
            for sym in [x for x in symbols if x and x != "CRYPTO"]:
                why = safety.class_paused(state, sym)
                if why:
                    return why
    if ev.kind in ("market", "update") and not liquidation_post(ev) and (ev.facts or {}).get("two_sources") is False:
        # owner, 2026-11-23: "No automatic major-move publishing without reliable verification"
        return f"not verified by a second market ({str((ev.facts or {}).get('verified') or 'one price source')[:120]}): a measured move goes out only when two markets agree"
    return ""


def liquidations_paused(db: Session) -> bool:
    from app.models.newsdesk import NdSettings

    s = db.get(NdSettings, 1)
    return bool((s.state or {}).get("liq_pause")) if s is not None else False


def market_post(ev: NdEvent) -> bool:
    """A post made of the desk's own measured market data: a price move, a level, a data post, a market check or a liquidation flush."""
    f = ev.facts or {}
    return ev.kind in ("market", "data", "recap") or bool(f.get("symbol")) or (ev.topic or "") == "liquidations"


# owner, 2026-11-21 (Phase 0 deployment): desk-measured market posts stay paused from the moment this code runs until the owner explicitly
# resumes them (PUT /newsdesk/settings {"market_pause": false}). There was no way to pause them before the deploy (the switch is new),
# so the safe default is "paused": no window in which a market post can go out unreviewed.
MARKET_PAUSE_DEFAULT = True


def _pause_reason(db: Session) -> str:
    from app.models.newsdesk import NdSettings

    s = db.get(NdSettings, 1)
    return str(((s.state or {}).get("market_pause_reason") if s is not None else "") or "")


def market_paused(db: Session) -> bool:
    """Owner's switch (News Desk settings, market_pause): no desk-measured market post is released or delivered while it is on. Unset:
    MARKET_PAUSE_DEFAULT."""
    from app.models.newsdesk import NdSettings

    s = db.get(NdSettings, 1)
    if s is None:
        return MARKET_PAUSE_DEFAULT
    value = (s.state or {}).get("market_pause")
    return MARKET_PAUSE_DEFAULT if value is None else bool(value)


def pending(db: Session, platform: str, now: datetime) -> list[NdEvent]:
    """Released events this platform still owes, oldest first. One it can no longer deliver in time is marked missed, for this platform only."""
    col = NdEvent.x_status if platform == "x" else NdEvent.tg_status
    rows = list(db.scalars(select(NdEvent).where(NdEvent.status == RELEASED, col.in_(("pending", "queued")), NdEvent.released_at >= now - timedelta(hours=12)).order_by(NdEvent.released_at, NdEvent.id)))
    out = []
    for ev in rows:
        why_held = held_market(db, ev)
        if why_held:
            mark(ev, platform, "skipped", why_held)
            logger.info("newsdesk events: %s won't deliver #%s: %s", platform, ev.id, why_held)
            continue
        limit = POLICY.deliver_market if ev.kind == "market" or (ev.facts or {}).get("symbol") else POLICY.deliver_news
        if platform == "x" and X_FOLLOWS_TELEGRAM and ev.tg_status in ("delivered", "uncertain"):
            limit += X_AFTER_TELEGRAM  # Telegram posted it near the end of its window: X still follows
        problem = editorial.delivery_gate(db, ev, now, platform)  # the story history and the owner's block, on every platform, every time
        if problem:
            mark(ev, platform, "skipped", problem)
            logger.info("newsdesk events: %s won't deliver #%s: %s", platform, ev.id, problem)
            continue
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


def overview(db: Session, now: datetime, desk_state: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = list(db.scalars(select(NdEvent).where(NdEvent.created_at >= now - timedelta(hours=48)).order_by(NdEvent.id.desc()).limit(60)))
    day = [e for e in rows if e.status == RELEASED and e.released_at and _utc(e.released_at, now) >= now - timedelta(hours=24)]
    return {"plan": {"regular_per_day": POLICY.regular_per_day, "regular_bar": POLICY.regular_bar, "extra_bar": POLICY.extra_bar, "extra_per_day": POLICY.extra_per_day, "ceiling": POLICY.ceiling},
            "released_24h": {"regular": sum(1 for e in day if e.slot == "regular"), "extra": sum(1 for e in day if e.slot == "extra"), "slot": sum(1 for e in day if e.slot == "slot")},
            "schedule": schedule(db, now, policy(desk_state)), "events": [event_out(e) for e in rows]}
