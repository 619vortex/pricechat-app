"""Group signals into topics and score them (0-100): growth velocity (30), engagement (25), freshness (15), audience relevance (20) and
discussion potential (10), times a learned weight from our own posts' metrics (bounded to 0.8-1.2, app/trending/learning).

Deterministic and explainable: every topic keeps its parts, so Admin shows why it scored what it did."""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.newsdesk import checks
from app.newsdesk.engine import classify as nd_classify

CATEGORIES = ("crypto", "stocks", "markets", "geopolitics", "technology", "world")
FROM_DESK = {"crypto": "crypto", "stocks": "stocks", "commodities": "markets", "economics": "markets", "politics": "geopolitics", "viral": "world"}
RELEVANCE = {"crypto": 1.0, "stocks": 0.9, "markets": 0.9, "geopolitics": 0.75, "technology": 0.7, "world": 0.5}
TECH = {"artificial intelligence": 2.0, "openai": 2.0, "chatgpt": 2.0, "anthropic": 2.0, "semiconductor": 2.0, "chipmaker": 2.0, "chips": 1.0,
        "microsoft": 1.5, "google": 1.0, "alphabet": 1.5, "meta": 1.0, "spacex": 1.5, "quantum": 1.5, "cyberattack": 2.0, "data breach": 2.0,
        "outage": 1.5, "smartphone": 1.0, "iphone": 1.5, "robot": 1.0, "startup": 1.0, "antitrust": 1.5, "tech": 1.0, "ai": 1.5}
WORLD = {"earthquake": 2.5, "hurricane": 2.5, "typhoon": 2.5, "wildfire": 2.0, "flood": 1.5, "pandemic": 2.0, "outbreak": 2.0, "explosion": 1.5,
         "evacuat": 1.5, "tsunami": 2.5, "volcano": 2.0, "climate": 1.0, "pope": 1.0, "royal": 0.5}


SPORT = {"league": 1.5, "premier league": 2.0, "super league": 2.0, "champions league": 2.0, "football": 1.5, "soccer": 1.5, "match": 0.5, "fixture": 1.5,
         "head-to-head": 2.0, "goal": 0.5, "striker": 2.0, "midfielder": 2.0, "nba": 2.0, "nfl": 2.0, "nhl": 2.0, "mlb": 2.0, "ufc": 2.0, "tennis": 2.0,
         "golf": 2.0, "cricket": 2.0, "grand prix": 2.0, "f1": 2.0, "bandy": 2.5, "world cup": 2.0, "playoff": 2.0, "quarterback": 2.0, "coach": 1.0,
         "transfer window": 2.0, "olympic": 1.5, "rugby": 2.0, "boxing": 2.0, "wrestling": 2.0, "derby": 1.0, "real madrid": 2.0, "stats": 0.5}
STEMS = {"evacuat"}  # matched as a word start; every other word must match whole ("ai" is not "aid", "meta" is not "metal")


def _strength(text: str, words: dict[str, float]) -> float:
    low = text.lower()
    return sum(w for k, w in words.items() if re.search(rf"\b{re.escape(k)}" + ("" if k in STEMS else r"\b"), low))


def classify(title: str, summary: str = "", hint: str | None = None) -> tuple[str | None, float]:
    """(category, keyword strength) or (None, 0): nothing our audience follows (sport, celebrities, weather in one town...)."""
    desk_cat, desk_strength = nd_classify(title, summary, hint if hint in FROM_DESK else None)
    best, strength = (FROM_DESK.get(desk_cat or ""), desk_strength) if desk_cat else (None, 0.0)
    if desk_cat == "viral":  # "record high" says how big, not what about: a named market wins ("Bitcoin hits record high" is crypto)
        from app.newsdesk.engine import KEYWORDS

        subject = {c: _strength(f"{title} {summary}", w) for c, w in KEYWORDS.items() if c != "viral"}
        top = max(subject, key=lambda c: subject[c])
        if subject[top] > 0:
            best, strength = FROM_DESK[top], min(strength, 4.0)
    for cat, words in (("technology", TECH), ("world", WORLD)):
        s = min(_strength(f"{title} {summary}", words), 4.0)
        if s > strength or (best == "world" and cat == "technology" and s >= strength and s > 0):
            best, strength = cat, s
    sport = _strength(f"{title} {summary}", SPORT) + (1.0 if re.search(r"\b\w+ vs\.? \w+", title, re.I) else 0.0)
    if sport >= 2.0 and sport >= strength:
        return None, 0.0  # a match, a league, a player: not this account's beat, whatever else the headlines mention
    return best, strength


@dataclass
class Sig:
    """What ranking needs of a signal (a TtSignal row or a plain dict in tests)."""

    id: int
    source: str
    title: str
    outlet: str
    reliable: bool
    engagement: float
    first_seen: datetime
    last_seen: datetime
    summary: str = ""
    url: str = ""
    published_at: datetime | None = None
    history: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    category: str | None = None

    @classmethod
    def of(cls, row: Any) -> "Sig":
        return cls(id=row.id, source=row.source, title=row.title, outlet=row.outlet, reliable=bool(row.reliable), engagement=float(row.engagement or 0),
                   first_seen=_aware(row.first_seen), last_seen=_aware(row.last_seen), summary=row.summary or "", url=row.url or "",
                   published_at=_aware(row.published_at) if row.published_at else None, history=list(row.history or []), extra=dict(row.extra or {}),
                   category=row.category)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class Cluster:
    signals: list[Sig]
    category: str
    strength: float

    @property
    def lead(self) -> Sig:
        """The headline the topic is named after: a reliable outlet's, else the most engaged signal's."""
        pool = [s for s in self.signals if s.reliable] or self.signals
        return max(pool, key=lambda s: (s.reliable, s.engagement, -s.first_seen.timestamp()))

    @property
    def reliable(self) -> list[Sig]:
        return [s for s in self.signals if s.reliable]


def _query_tokens(s: Sig) -> set[str]:
    if s.extra.get("kind") == "article":
        return set()  # an article Google linked to a trend is a headline like any other
    q = str(s.extra.get("query") or s.extra.get("trend") or "")
    if not q and s.source != "x_trends":
        return set()
    return checks.tokens(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", (q or s.title).lstrip("#")))


def _matches(a: Sig, b: Sig, ta: set[str], tb: set[str]) -> bool:
    if a.extra.get("group") and a.extra.get("group") == b.extra.get("group"):
        return True
    qa, qb = _query_tokens(a), _query_tokens(b)
    for q, other in ((qa, tb), (qb, ta)):
        if q and sum(len(w) for w in q) >= 5 and q <= other:
            return True  # a search trend ("nvidia earnings") and a headline that contains it
    if qa or qb:
        return False
    return checks.same_event(a.title, b.title)


def cluster(signals: list[Sig]) -> list[Cluster]:
    """Greedy single-link grouping, reliable and engaged signals first. An inverted token index keeps it near-linear."""
    order = sorted(signals, key=lambda s: (not s.reliable, -s.engagement, s.id))
    groups: list[list[Sig]] = []
    toks: dict[int, set[str]] = {}
    index: dict[str, set[int]] = {}
    by_group: dict[str, int] = {}
    for s in order:
        t = checks.tokens(f"{s.title}")
        toks[s.id] = t | _query_tokens(s)
        home = by_group.get(str(s.extra.get("group") or "")) if s.extra.get("group") else None
        if home is None:
            seen: set[int] = set()
            for w in toks[s.id]:
                seen |= index.get(w, set())
            for gi in sorted(seen):
                if any(_matches(s, o, toks[s.id], toks[o.id]) for o in groups[gi]):
                    home = gi
                    break
        if home is None:
            groups.append([s])
            home = len(groups) - 1
        else:
            groups[home].append(s)
        if s.extra.get("group"):
            by_group.setdefault(str(s.extra["group"]), home)
        for w in toks[s.id]:
            index.setdefault(w, set()).add(home)
    out = []
    for g in groups:
        text = " ".join(x.title for x in g[:6])
        hint = next((x.category for x in g if x.category), None)
        cat, strength = classify(text, " ".join(x.summary for x in g[:3])[:600], hint)
        if cat is not None:
            out.append(Cluster(g, cat, strength))
    return out


# --- score ----------------------------------------------------------------------------------------------------------------

NORM = {"google": 6.0, "reddit": 4.5, "x_trends": 6.0, "x_accounts": 4.0}  # log10 of the engagement that counts as "very high"


def _growth(s: Sig, now: datetime, hours: float = 3.0) -> float:
    """Relative engagement growth per hour over the last few hours, from the signal's own history."""
    pts = []
    for p in s.history or []:
        try:
            pts.append((_aware(datetime.fromisoformat(str(p[0]))), float(p[1])))
        except (ValueError, TypeError, IndexError):
            continue
    pts = [p for p in pts if p[0] >= now - timedelta(hours=hours)]
    if len(pts) < 2:
        return 0.0
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    span = max((t1 - t0).total_seconds() / 3600, 0.25)
    return max(0.0, (v1 - v0) / max(v0, 10.0) / span)


def _seen(s: Sig) -> datetime:
    """When a signal appeared: its own publication time when it has one (so the first scan after a restart doesn't make everything look
    brand new), never later than when we first saw it."""
    return min(s.published_at, s.first_seen) if s.published_at else s.first_seen


def score(c: Cluster, now: datetime, weights: dict[str, float] | None = None) -> tuple[float, dict[str, Any]]:
    sig = c.signals
    weights = weights or {}
    recent = [s for s in sig if _seen(s) >= now - timedelta(hours=2)]
    prior = [s for s in sig if now - timedelta(hours=8) <= _seen(s) < now - timedelta(hours=2)]
    ratio = (len(recent) / 2 + 0.25) / (len(prior) / 6 + 0.25)
    v_mentions = 0.5 * min(1.0, len(recent) / 4) + 0.5 * min(1.0, max(0.0, math.log2(ratio)) / 3)
    v_engage = min(1.0, max((_growth(s, now) for s in sig), default=0.0))
    velocity = 30 * (0.6 * v_mentions + 0.4 * v_engage)

    types = {s.source for s in sig}
    outlets = {s.outlet for s in sig if s.reliable}
    norms = [min(1.0, math.log10(max(s.engagement, 1)) / NORM[s.source]) for s in sig if s.source in NORM]
    norms.append(min(1.0, len(outlets) / 5))
    engagement = 25 * min(1.0, 0.75 * max(norms) + 0.25 * min(1.0, len(types) / 3))

    first = min(_seen(s) for s in sig)
    newest = max((s.published_at or s.first_seen) for s in sig)
    age = max(0.0, (now - first).total_seconds() / 3600)
    freshness = 15 * math.exp(-age / 8) * (0.5 if now - newest > timedelta(hours=6) else 1.0)

    relevance = 20 * RELEVANCE.get(c.category, 0.4) * (0.6 + 0.4 * min(1.0, c.strength / 3))

    comments = sum(float(s.extra.get("comments") or 0) for s in sig)
    discussion = 10 * min(1.0, 0.4 * min(1.0, (len(types) - 1) / 2) + 0.3 * min(1.0, comments / 500) + 0.3 * min(1.0, len(outlets) / 4))

    mult = weights.get(f"cat:{c.category}", 1.0)
    src_w = [weights.get(f"src:{t}", 1.0) for t in types]
    mult *= sum(src_w) / len(src_w) if src_w else 1.0
    mult = max(0.8, min(1.2, mult))
    total = min(100.0, (velocity + engagement + freshness + relevance + discussion) * mult)
    parts = {"velocity": round(velocity, 1), "engagement": round(engagement, 1), "freshness": round(freshness, 1), "relevance": round(relevance, 1),
             "discussion": round(discussion, 1), "learned": round(mult, 3), "recent_2h": len(recent), "prior_6h": len(prior), "sources": sorted(types),
             "outlets": sorted(outlets), "age_hours": round(age, 1)}
    return round(total, 1), parts
