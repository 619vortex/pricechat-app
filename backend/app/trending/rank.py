"""Group signals into topics and score them (0-100): growth velocity (30), engagement (25), freshness (15), audience relevance (20) and
discussion potential (10), times a learned weight from our own posts' metrics (bounded to 0.8-1.2, app/trending/learning). Velocity comes
only from measured growth (growth()); a topic with no measured growth is capped at NO_GROWTH_CAP, below the automatic line.

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




# --- grouping (owner, 2026-11-14: one Nvidia cluster pulled 26 loosely related items) -----------------------------------------------
# A signal joins a topic only when it tells the SAME EVENT as the topic's seed (its first, most reliable item; never "any member", so a
# cluster can't drift from story to story), within WINDOW of it. A Google trend and the articles Google links to it are one unit; a
# one-word search trend ("nvidia") never pulls in headlines on its own, only through those linked articles.

WINDOW = timedelta(hours=12)
# words that say nothing about which event it is: two stories never become one on these
GENERIC = set("""shares share stock stocks price prices market markets company companies firm firms report reports reported says said year years
week weeks day days today news update updates inc corp group chief ceo official officials investors investor trading traders plan plans deal
deals business global world people time first last next amid could would biggest record latest""".split())


def _content(title: str, names: set[str]) -> set[str]:
    """The words that say what happened (an action, an object, a figure): not the subject's own name, places, nationalities or filler."""
    out = set()
    for w in checks.tokens(title):
        base = re.sub(r"'s$", "", w)
        if base in names or base in checks.COMMON_NAMES or base in checks.PLACES or base in checks.DEMONYMS or base in GENERIC:
            continue
        out.add(base)
    return out


def _title_case(title: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][\w'’-]*", title or "") if len(w) > 3]
    return len(words) >= 4 and sum(w[0].isupper() for w in words) / len(words) >= 0.7


def related(a: str, b: str) -> bool:
    """Two headlines are one event, strictly: checks.same_event (nothing says otherwise: subjects, places, levels, figures), and evidence
    beyond a shared big name: the same subject AND the same action or amount, or (no distinctive subject) most of the words."""
    if not a or not b or not checks.same_event(a, b):
        return False
    if a.strip().lower() == b.strip().lower():
        return True
    if checks.price_levels(a) & checks.price_levels(b):
        return True
    na, nb = checks.distinctive_names(a), checks.distinctive_names(b)
    shared = na & nb
    act = _content(a, shared) & _content(b, shared)
    fa, fb = {k for k, *_ in checks._figures(a)}, {k for k, *_ in checks._figures(b)}
    money = {k for k in fa & fb if k[:1] in "$€£" or k.endswith(("%", "b", "m"))}
    if shared:
        # In a Title Case Headline ordinary words look like names ("Inflation"): one shared "name" then needs two shared action words.
        need = 2 if len(shared) == 1 and (_title_case(a) or _title_case(b)) else 1
        return len(act) >= need or bool(money)
    return checks.similarity(checks.fingerprint(a), checks.fingerprint(b)) >= 0.5 and len(act) >= 2


def _query_tokens(s: Sig) -> set[str]:
    if s.extra.get("kind") == "article":
        return set()  # an article Google linked to a trend is a headline like any other
    q = str(s.extra.get("query") or s.extra.get("trend") or "")
    if not q and s.source != "x_trends":
        return set()
    return checks.tokens(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", (q or s.title).lstrip("#")))


def _joins(s: Sig, seed: Sig) -> bool:
    """s tells the seed's event, close enough in time."""
    if abs((_seen(s) - _seen(seed)).total_seconds()) > WINDOW.total_seconds():
        return False
    qs, qseed = _query_tokens(s), _query_tokens(seed)
    if qs or qseed:  # a search trend: only a multi-word query fully inside the other's headline ("nvidia earnings")
        q, other = (qs, seed) if qs else (qseed, s)
        if _query_tokens(other):
            return q == _query_tokens(other) and len(q) >= 2
        return len(q) >= 2 and q <= checks.tokens(other.title)
    return related(seed.title, s.title)


def cluster(signals: list[Sig]) -> list[Cluster]:
    """Seed-based grouping: reliable and engaged signals first; each later unit joins the first topic whose seed it matches (_joins), else
    starts its own. An inverted index on the seeds' words keeps it near-linear."""
    order = sorted(signals, key=lambda s: (not s.reliable, -s.engagement, s.id))
    units: dict[str, list[Sig]] = {}
    for s in order:
        # one search term is one unit whatever the country (US and GB link the same article: one signal, one group)
        key = re.sub(r"^google:[A-Za-z]{2}:", "google:", str(s.extra.get("group") or "")) or f"id:{s.id}"
        units.setdefault(key, []).append(s)
    groups: list[list[Sig]] = []
    seeds: list[Sig] = []
    index: dict[str, set[int]] = {}
    for unit in units.values():
        words: set[str] = set()
        for s in unit:
            words |= checks.tokens(s.title) | _query_tokens(s)
        cands: set[int] = set()
        for w in words:
            cands |= index.get(w, set())
        home = next((gi for gi in sorted(cands) if any(_joins(s, seeds[gi]) for s in unit)), None)
        if home is None:
            groups.append(list(unit))
            seeds.append(unit[0])
            gi = len(groups) - 1
            for w in checks.tokens(unit[0].title) | _query_tokens(unit[0]):
                index.setdefault(w, set()).add(gi)
            if not unit[0].reliable:  # a trend-only seed: let its linked articles' words find it too
                for s in unit[1:]:
                    for w in checks.tokens(s.title):
                        index.setdefault(w, set()).add(gi)
        else:
            groups[home].extend(unit)
    out = []
    for g in groups:
        text = " ".join(x.title for x in g[:6])
        hint = next((x.category for x in g if x.category), None)
        cat, strength = classify(text, " ".join(x.summary for x in g[:3])[:600], hint)
        if cat is not None:
            out.append(Cluster(g, cat, strength))
    return out


# --- growth (owner, 2026-11-14: genuine evidence of GROWING interest, not just many articles) -------------------------------------------
# Measured, never assumed: items and outlets added to the topic across our own successive scans (first_seen is when a scan first saw a
# signal), a Google Trends traffic tier and its change between scans (each signal keeps its engagement per scan in history), Reddit / X
# engagement growth, and a burst of fresh publications. Coverage by many outlets on its own scores low; without measured growth (scans,
# Google, engagement) a topic is capped at NO_GROWTH_CAP, below the automatic line (75).

NO_GROWTH_CAP = 60.0
GROWTH_LOOKBACK = timedelta(hours=3)
MIN_OBSERVED = timedelta(minutes=25)  # a topic first seen in this scan has no scan history yet
TIERS = ((1_000_000, 1.0), (500_000, 0.9), (200_000, 0.75), (100_000, 0.65), (50_000, 0.55), (20_000, 0.45), (5_000, 0.35), (1_000, 0.25))


def tier(traffic: float) -> float:
    return next((v for t, v in TIERS if traffic >= t), 0.0)


def short(n: float) -> str:
    """200000 -> 200K+, the way Google writes its traffic tiers."""
    n = int(n)
    if n >= 1_000_000:
        return f"{n // 1_000_000}M+"
    if n >= 1_000:
        return f"{n // 1_000}K+"
    return f"{n}+"


def _points(s: Sig) -> list[tuple[datetime, float]]:
    pts = []
    for p in s.history or []:
        try:
            pts.append((_aware(datetime.fromisoformat(str(p[0]))), float(p[1])))
        except (ValueError, TypeError, IndexError):
            continue
    return sorted(pts)


def _growth(s: Sig, now: datetime, hours: float = 3.0) -> float:
    """Relative engagement growth per hour over the last few hours, from the signal's own history."""
    pts = [p for p in _points(s) if p[0] >= now - timedelta(hours=hours)]
    if len(pts) < 2:
        return 0.0
    (t0, v0), (t1, v1) = pts[0], pts[-1]
    span = max((t1 - t0).total_seconds() / 3600, 0.25)
    return max(0.0, (v1 - v0) / max(v0, 10.0) / span)


def _seen(s: Sig) -> datetime:
    """When a signal appeared: its own publication time when it has one (so the first scan after a restart doesn't make everything look
    brand new), never later than when we first saw it."""
    return min(s.published_at, s.first_seen) if s.published_at else s.first_seen


def _outlets(sigs: list[Sig]) -> set[str]:
    from app.trending.sensitive import outlet_key

    return {outlet_key(s.outlet) for s in sigs if s.reliable and s.outlet}


def growth(c: Cluster, now: datetime) -> dict[str, Any]:
    """What we measured about this topic's growth, and the velocity it earns (0-1). evidence: the measured kinds (scans, google,
    engagement); a burst of publications adds velocity but is not evidence on its own."""
    sig = c.signals
    out: dict[str, Any] = {"evidence": [], "notes": []}
    first_obs = min(s.first_seen for s in sig)
    observed = now - first_obs
    out["observed_min"] = int(observed.total_seconds() // 60)
    v_scan = 0.0
    if observed >= MIN_OBSERVED:
        ref = max(first_obs, now - GROWTH_LOOKBACK) + timedelta(minutes=1)
        before = [s for s in sig if s.first_seen <= ref]
        added = len(sig) - len(before)
        o_before, o_now = len(_outlets(before)), len(_outlets(sig))
        out.update({"items_before": len(before), "items_now": len(sig), "outlets_before": o_before, "outlets_now": o_now,
                    "since_min": int((now - ref).total_seconds() // 60)})
        new_outlets = o_now - o_before
        if new_outlets >= 2 or (added >= 3 and added >= 0.5 * max(1, len(before))):
            v_scan = min(1.0, 0.6 * min(1.0, new_outlets / 3) + 0.4 * min(1.0, added / max(3, len(before))))
            out["evidence"].append("scans")
            out["notes"].append(f"+{new_outlets} outlets, +{added} items in {out['since_min']} min")
    trends = [s for s in sig if s.source == "google" and s.extra.get("query") and s.extra.get("kind") != "article"]
    v_google = 0.0
    if trends:
        t = max(trends, key=lambda s: s.engagement)
        pts = [p for p in _points(t) if p[0] >= now - GROWTH_LOOKBACK] or [(now, t.engagement)]
        g_from, g_to = pts[0][1], t.engagement
        out.update({"google_from": g_from, "google_to": g_to, "google_query": str(t.extra.get("query") or ""), "google_geo": str(t.extra.get("geo") or "")})
        if pts[0][0] < now - timedelta(minutes=1):
            out["google_minutes"] = int((now - pts[0][0]).total_seconds() // 60)
        up = tier(g_to) > tier(g_from)
        v_google = min(1.0, tier(g_to) + (0.25 if up else 0.0))
        if g_to >= 5_000:  # on Google's trending-now list (rising searches, Google's own measure) with real traffic
            out["evidence"].append("google")
            out["notes"].append(f"Google {short(g_from)} -> {short(g_to)}" if up else f"Google {short(g_to)}")
        out["google_up"] = up
    v_engage = min(1.0, max((_growth(s, now) for s in sig if s.source in ("reddit", "x_trends", "x_accounts")), default=0.0))
    if v_engage >= 0.25:
        out["evidence"].append("engagement")
        out["notes"].append(f"engagement +{int(v_engage * 100)}%/h")
    recent = _outlets([s for s in sig if _seen(s) >= now - timedelta(hours=2)])
    prior = _outlets([s for s in sig if now - timedelta(hours=8) <= _seen(s) < now - timedelta(hours=2)])
    ratio = (len(recent) / 2 + 0.25) / (len(prior) / 6 + 0.25)
    v_burst = min(1.0, len(recent) / 4) * min(1.0, max(0.0, math.log2(ratio)) / 3)
    out.update({"recent_2h": len(recent), "prior_6h": len(prior)})
    if v_burst >= 0.3:
        out["notes"].append(f"{len(recent)} outlets in 2 h, {len(prior)} in the 6 h before")
    vals = sorted([v_scan, v_google, v_engage, 0.6 * v_burst], reverse=True)
    out["velocity"] = min(1.0, vals[0] + 0.3 * vals[1])
    out["measured"] = bool(out["evidence"])
    return out


# --- score ----------------------------------------------------------------------------------------------------------------

NORM = {"google": 6.0, "reddit": 4.5, "x_trends": 6.0, "x_accounts": 4.0}  # log10 of the engagement that counts as "very high"


def score(c: Cluster, now: datetime, weights: dict[str, float] | None = None) -> tuple[float, dict[str, Any]]:
    sig = c.signals
    weights = weights or {}
    g = growth(c, now)
    velocity = 30 * g["velocity"]

    types = {s.source for s in sig}
    outlets = {s.outlet for s in sig if s.reliable}
    norms = [min(1.0, math.log10(max(s.engagement, 1)) / NORM[s.source]) for s in sig if s.source in NORM]
    norms.append(min(0.3, len(_outlets(sig)) / 10))  # coverage alone: low
    engagement = 25 * min(1.0, 0.75 * max(norms) + 0.25 * min(1.0, len(types) / 3))

    first = min(_seen(s) for s in sig)
    newest = max((s.published_at or s.first_seen) for s in sig)
    age = max(0.0, (now - first).total_seconds() / 3600)
    freshness = 15 * math.exp(-age / 8) * (0.5 if now - newest > timedelta(hours=6) else 1.0)

    relevance = 20 * RELEVANCE.get(c.category, 0.4) * (0.6 + 0.4 * min(1.0, c.strength / 3))

    comments = sum(float(s.extra.get("comments") or 0) for s in sig)
    discussion = 10 * min(1.0, 0.4 * min(1.0, (len(types) - 1) / 2) + 0.4 * min(1.0, comments / 500) + 0.2 * min(1.0, len(_outlets(sig)) / 4))

    mult = weights.get(f"cat:{c.category}", 1.0)
    src_w = [weights.get(f"src:{t}", 1.0) for t in types]
    mult *= sum(src_w) / len(src_w) if src_w else 1.0
    mult = max(0.8, min(1.2, mult))
    total = min(100.0, (velocity + engagement + freshness + relevance + discussion) * mult)
    g["capped"] = not g["measured"] and total > NO_GROWTH_CAP
    if not g["measured"]:
        total = min(total, NO_GROWTH_CAP)  # no measured growth: never at the automatic line, however many outlets have it
    parts = {"velocity": round(velocity, 1), "engagement": round(engagement, 1), "freshness": round(freshness, 1), "relevance": round(relevance, 1),
             "discussion": round(discussion, 1), "learned": round(mult, 3), "recent_2h": g["recent_2h"], "prior_6h": g["prior_6h"], "sources": sorted(types),
             "outlets": sorted(outlets), "age_hours": round(age, 1), "growth": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in g.items()}}
    return round(total, 1), parts
