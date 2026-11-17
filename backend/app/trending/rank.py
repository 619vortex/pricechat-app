"""Group signals into topics and score them (0-100), TODAY'S news first (owner, 2026-11-15): momentum (30: measured growth, posting
velocity, search interest, engagement growth), freshness (25: full for 0-3 h, fading to 6 h, then stale), market impact (15), surprise
(10), audience relevance (10) and engagement (10), times a learned weight from our own posts' metrics (bounded to 0.8-1.2,
app/trending/learning). Momentum comes only from measured growth (growth()); a topic with no measured growth is capped at NO_GROWTH_CAP,
below the automatic line. A story first reported more than STALE_AFTER ago is stale (capped at STALE_CAP, below the suggestion line)
unless it has a genuinely new development: new independent outlets or a rise in searches in the last DEVELOPMENT_WINDOW.

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
GEOPOLITICS = {"putin": 2.0, "xi jinping": 2.0, "xi": 1.5, "kremlin": 2.0, "nato": 2.0, "summit": 1.5, "g7": 2.0, "g20": 2.0, "sanctions": 2.0,
               "military": 1.5, "missile": 2.0, "missiles": 2.0, "airstrike": 2.0, "airstrikes": 2.0, "troops": 1.5, "ceasefire": 2.0, "invasion": 2.0,
               "nuclear": 1.5, "iran": 1.5, "israel": 1.5, "ukraine": 1.5, "russia": 1.5, "taiwan": 1.5, "north korea": 2.0, "gaza": 1.5, "pentagon": 1.5,
               "trade talks": 2.0, "trade deal": 2.0, "tariff": 1.5, "tariffs": 1.5, "white house": 1.0, "president": 0.5, "prime minister": 0.5}
WORLD = {"earthquake": 2.5, "hurricane": 2.5, "typhoon": 2.5, "wildfire": 2.0, "flood": 1.5, "pandemic": 2.0, "outbreak": 2.0, "explosion": 1.5,
         "evacuat": 1.5, "tsunami": 2.5, "volcano": 2.0, "climate": 1.0, "pope": 1.0, "royal": 0.5}


SPORT = {"league": 1.5, "premier league": 2.0, "super league": 2.0, "champions league": 2.0, "football": 1.5, "soccer": 1.5, "match": 0.5, "fixture": 1.5,
         "head-to-head": 2.0, "goal": 0.5, "striker": 2.0, "midfielder": 2.0, "nba": 2.0, "nfl": 2.0, "nhl": 2.0, "mlb": 2.0, "ufc": 2.0, "tennis": 2.0,
         "golf": 2.0, "cricket": 2.0, "grand prix": 2.0, "f1": 2.0, "bandy": 2.5, "world cup": 2.0, "playoff": 2.0, "quarterback": 2.0, "coach": 1.0,
         "transfer window": 2.0, "olympic": 1.5, "rugby": 2.0, "boxing": 2.0, "wrestling": 2.0, "derby": 1.0, "real madrid": 2.0, "stats": 0.5,
         "all blacks": 2.5, "six nations": 2.5, "autumn nations": 2.5, "springboks": 2.5, "wallabies": 2.5, "game day": 2.0, "gameday": 2.0,
         "kick-off": 2.0, "kickoff": 2.0, "test match": 2.0, "squad": 1.0, "touchdown": 2.0, "super bowl": 2.5, "ryder cup": 2.5, "wimbledon": 2.5,
         "march madness": 2.5, "fantasy football": 2.5, "line-up": 1.0, "lineup": 1.0, "manager sacked": 2.0, "hat-trick": 2.0,
         # entertainment and celebrity: not this account's beat either
         "elton john": 2.0, "celebrity": 2.0, "chat show": 2.0, "talk show": 2.0, "radio show": 1.5, "red carpet": 2.5, "box office": 2.0,
         "album": 1.5, "concert": 1.5, "tour dates": 2.0, "oscars": 2.5, "grammys": 2.5, "emmys": 2.5, "strictly come dancing": 2.5,
         "eurovision": 2.5, "reality show": 2.0, "royal family": 1.5, "king charles": 1.5, "jokes": 1.0, "sitcom": 2.0, "tv series": 1.5,
         # 2026-11-15 production: tennis, reality TV, critics' reviews and a gold-bar burglary led topics
         "wta": 2.5, "atp": 2.5, "match point": 2.5, "semi-finals": 1.5, "semi-final": 1.5, "grand slam": 2.0, "reality tv": 2.5, "love island": 2.5,
         "tv show": 2.0, "netflix series": 2.0, "drama": 1.0, "thriller": 1.5, "princess": 1.5, "prince": 1.0, "stole": 2.0, "heist": 2.0,
         "burglar": 2.0, "burglary": 2.0, "robbery": 2.0, "murder": 2.0}
STEMS = {"evacuat"}  # matched as a word start; every other word must match whole ("ai" is not "aid", "meta" is not "metal")


def _strength(text: str, words: dict[str, float]) -> float:
    low = text.lower()
    return sum(w for k, w in words.items() if re.search(rf"\b{re.escape(k)}" + ("" if k in STEMS else r"\b"), low))


def classify(title: str, summary: str = "", hint: str | None = None) -> tuple[str | None, float]:
    """(category, keyword strength) or (None, 0): nothing our audience follows (sport, celebrities, weather in one town...)."""
    desk_cat, desk_strength = nd_classify(title, summary, hint if hint in FROM_DESK else None)
    if desk_cat and _strict_strength(f"{title} {summary}", desk_cat) <= 0:
        desk_cat, desk_strength = None, 0.0  # only a short keyword's prefix matched ("dow" in "down", "eth" in "ethics")
    best, strength = (FROM_DESK.get(desk_cat or ""), desk_strength) if desk_cat else (None, 0.0)
    if desk_cat == "viral":  # "record high" says how big, not what about: a named market wins ("Bitcoin hits record high" is crypto)
        from app.newsdesk.engine import KEYWORDS

        subject = {c: _strength(f"{title} {summary}", w) for c, w in KEYWORDS.items() if c != "viral"}
        top = max(subject, key=lambda c: subject[c])
        if subject[top] > 0:
            best, strength = FROM_DESK[top], min(strength, 4.0)
    for cat, words in (("geopolitics", GEOPOLITICS), ("technology", TECH), ("world", WORLD)):
        s = min(_strength(f"{title} {summary}", words), 4.0)
        if s > strength or (best == "world" and cat in ("technology", "geopolitics") and s >= strength and s > 0):
            best, strength = cat, s
    if _vetoed(title, summary, strength):
        return None, 0.0  # a match, a league, a player, a celebrity: not this account's beat, whatever else the headlines mention
    inc = incident_of(title)
    if inc is not None and (best is None or best == "world" or inc.major):
        # owner, 2026-11-17: "Major U.S. banks report widespread service outages" had no category ("outages" is not "outage") and was
        # dropped; a major incident is filed where its consequences land: money (markets) or the systems everyone uses (technology)
        cat = "markets" if inc.financial else "technology"
        return cat, max(strength if best == cat else 0.0, INCIDENT_STRENGTH if inc.major else 1.5)
    return best, strength


INCIDENT_STRENGTH = 3.0


def incident_of(text: str) -> Any:
    """The infrastructure incident a headline (or a topic's joined headlines) tells, or None (app/newsdesk/incidents)."""
    from app.newsdesk import incidents

    return incidents.detect(text)


def _strict_strength(text: str, desk_cat: str) -> float:
    """The News Desk category's keyword weight with keywords of four letters or fewer matched as whole words."""
    from app.newsdesk.engine import KEYWORDS

    low = text.lower()
    return sum(w for k, w in KEYWORDS.get(desk_cat, {}).items() if re.search(rf"\b{re.escape(k)}" + (r"\b" if len(k) <= 4 else ""), low))


def _vetoed(title: str, summary: str, strength: float) -> bool:
    fixture = re.search(r"\b\w+ vs\.? \w+", title, re.I) or re.search(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)? v [A-Z][a-z]+", title)  # "Wales v New Zealand"
    sport = _strength(f"{title} {summary}", SPORT) + (1.5 if fixture else 0.0)
    if re.fullmatch(r"\s*[\w.&'-]+(?: [\w.&'-]+)? (?:vs\.?|v) [\w.&'-]+(?: [\w.&'-]+)?\s*", title, re.I) and not _title_on_beat(title):
        return True  # production 2026-11-15: "sa vs ban" (a cricket match) took "geopolitics" from its posts' summaries; a bare fixture is a match
    return sport >= 2.0 and sport >= strength


def _title_on_beat(title: str) -> bool:
    """The title by itself names something on the beat ("Biden vs Putin", "Nvidia vs AMD"), not only its linked posts' summaries."""
    from app.newsdesk.engine import KEYWORDS

    if any(_strength(title, w) > 0 for w in (GEOPOLITICS, TECH, WORLD)):
        return True
    return any(_strict_strength(title, c) > 0 for c in KEYWORDS)


# --- exceptional off-beat stories (owner, 2026-11-15: "major stories aren't excluded solely because they involve crime, entertainment or
# another normally low-priority category") -------------------------------------------------------------------------------------------
# A story the beat filter would drop stays a topic when it is exceptional: financial impact (a large sum, or market words with a real sum)
# or global significance (many independent outlets, several of them mainstream). Match results never qualify on coverage alone. Being a
# topic is all the exception gives: genuine momentum is still measured (no growth: capped at 60), and the 75 line, the sensitive-subject
# rules and every gate still apply.
EXC_MONEY = 500e6
EXC_MONEY_WITH_MARKET = 50e6
EXC_OUTLETS, EXC_MAINSTREAM = 6, 3
MARKET_WORDS = {"shares": 1.5, "stock": 1.5, "stocks": 1.0, "market": 1.0, "markets": 1.0, "investors": 1.0, "valuation": 1.5, "ipo": 2.0, "takeover": 1.5,
                "acquisition": 1.5, "buyout": 1.5, "bankruptcy": 2.0, "sanctions": 1.5, "insurers": 1.5, "insured": 1.0, "losses": 1.0, "economy": 1.0,
                "revenue": 1.0, "sales": 0.5, "deal": 0.5, "fine": 0.5, "fined": 1.5, "settlement": 1.0, "lawsuit": 0.5}
RESULTS = re.compile(r"\b(beats?|beaten|defeats?|wins?|won|draws?|match point|semi-?finals?|finals?|knock(?:s|ed)? out|goals?|scores?|title)\b", re.I)
_MONEY = re.compile(r"(?:[$€£]\s?(\d+(?:\.\d+)?)\s?(bn|billion|b|m|mn|million|trillion|tn)\b)|(?:(\d+(?:\.\d+)?)\s?(billion|million|trillion)\s(?:dollars|euros|pounds))", re.I)
_SCALE = {"m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9, "tn": 1e12, "trillion": 1e12}


def money(text: str) -> float:
    """The largest sum written in the text ($1.2 billion, €500m, £3bn), or 0."""
    best = 0.0
    for m in _MONEY.finditer(text or ""):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        best = max(best, float(num) * _SCALE[unit.lower()])
    return best


def exceptional(sigs: list[Sig]) -> str | None:
    """Why an off-beat story (crime, entertainment, sport, royals...) is still a topic, or None."""
    reliable = [s for s in sigs if s.reliable]
    if not reliable:
        return None
    text = " ".join(s.title for s in reliable[:8])
    sum_ = money(text)
    market, hits = _words(text, MARKET_WORDS)
    if sum_ >= EXC_MONEY:
        return f"financial impact: {short_money(sum_)}"
    if sum_ >= EXC_MONEY_WITH_MARKET and market >= 1.5:
        return f"financial impact: {short_money(sum_)} ({', '.join(hits[:2])})"
    outlets = _outlets(reliable)
    main = {k for k in outlets if k in {_okey(m) for m in MAINSTREAM}}
    if len(outlets) >= EXC_OUTLETS and len(main) >= EXC_MAINSTREAM and not RESULTS.search(text):
        return f"global significance: {len(outlets)} independent outlets, {len(main)} mainstream"
    return None


def short_money(n: float) -> str:
    return f"${n / 1e9:.1f}B" if n >= 1e9 else f"${n / 1e6:.0f}M"


def _okey(name: str) -> str:
    from app.trending.sensitive import outlet_key

    return outlet_key(name)


def off_topic(title: str, summary: str = "") -> bool:
    """Sport or entertainment beyond what any market words in it weigh (classify's veto). Merely unclassified news is not off-topic."""
    if classify(title, summary)[0] is not None:
        return False
    return _vetoed(title, summary, 0.0)


QUOTE_LED = re.compile(r"^\s*['‘\"“«]")


def quote_led(title: str) -> bool:
    return bool(QUOTE_LED.match(title or ""))


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
    exception: str | None = None  # an off-beat story kept for its financial impact or global significance (exceptional())

    @property
    def lead(self) -> Sig:
        """The headline the topic is named after: a reliable outlet's, else the most engaged signal's. A headline that opens with a
        quotation ("'It is not unusual to go 48 hours with little sleep': what it's like...") leads only when no plain one exists: the
        tweet leads with what happened, not with someone's words (2026-11-16 production, trend post 152)."""
        pool = [s for s in self.signals if s.reliable] or self.signals
        return max(pool, key=lambda s: (s.reliable, not quote_led(s.title), s.engagement, -s.first_seen.timestamp()))

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
    # the same incident at the same company, or two sector-wide reports of it ("Customers report service outages at major US banks" /
    # "Major U.S. banks report widespread service outages"); never a different company's outage (app/newsdesk/incidents.same_incident)
    return related(seed.title, s.title) or _same_incident(seed.title, s.title)


def _same_incident(a: str, b: str) -> bool:
    from app.newsdesk import incidents

    return incidents.same_incident(a, b)


def _split_google_units(units: dict[str, list[Sig]]) -> None:
    """A Google search term links whatever articles mention it: for "cop31" a Guardian diary ("what it's like to cover a Cop climate summit")
    and a Reuters report ("UN climate chief urges speed as COP31 confronts fossil fuel divide"), which trend post 152 then told as one story
    (2026-11-16). The term keeps only the articles that tell the same event as its lead article (a topical one first); every other article
    stands on its own and must find its own event by the strict rule (related)."""
    from app.trending.sources import topical

    for key in [k for k in units if k.startswith("google:")]:
        unit = units[key]
        arts = [s for s in unit if s.extra.get("kind") == "article"]
        if len(arts) < 2:
            continue
        anchor = min(arts, key=lambda s: (not topical(s.title), not s.reliable, -s.engagement, s.id))
        for s in arts:
            if s is not anchor and not related(anchor.title, s.title):
                unit.remove(s)
                units[f"id:{s.id}"] = [s]


def cluster(signals: list[Sig]) -> list[Cluster]:
    """Seed-based grouping: reliable and engaged signals first; each later unit joins the first topic whose seed it matches (_joins), else
    starts its own. An inverted index on the seeds' words keeps it near-linear."""
    order = sorted(signals, key=lambda s: (not s.reliable, -s.engagement, s.id))
    units: dict[str, list[Sig]] = {}
    for s in order:
        # one search term is one unit whatever the country (US and GB link the same article: one signal, one group)
        key = re.sub(r"^google:[A-Za-z]{2}:", "google:", str(s.extra.get("group") or "")) or f"id:{s.id}"
        units.setdefault(key, []).append(s)
    _split_google_units(units)
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
        # 2026-11-14 production: a rugby fixture and a royal chat-show story led "stocks" topics because the cluster's category came from
        # all its members' headlines together. A reliable news item that is off-topic on its own (sport, celebrities: classify() -> None)
        # never sits in a topic; a topic whose reliable items are mostly off-topic is dropped. Google/Reddit/X items keep their place
        # (a trend query has no category of its own; its articles decide).
        from app.trending.sources import topical

        news = [x for x in g if x.reliable]
        news = [x for x in news if topical(x.title)] if news else news  # features, reviews and round-ups never count, exceptional or not
        on = [x for x in news if not off_topic(x.title, x.summary)]
        exc = None
        if news and len(on) * 2 < len(news):
            exc = exceptional(news)
            if exc is None:
                continue
            on = news  # an exceptional story keeps all its reporting
        g = [x for x in g if not x.reliable or x in on]
        if not g or (not news and any(x.reliable for x in g)):
            continue
        text = " ".join(x.title for x in g[:6])
        hint = next((x.category for x in g if x.category), None)
        cat, strength = classify(text, " ".join(x.summary for x in g[:3])[:600], hint)
        if exc is not None:
            if cat is None:  # its own words are off-beat: file it by what makes it exceptional
                cat, strength = ("markets" if exc.startswith("financial") else "world"), 2.0
            out.append(Cluster(g, cat, strength, exception=exc))
        elif cat is not None:
            out.append(Cluster(g, cat, strength))
    return out


# --- growth (owner, 2026-11-14: genuine evidence of GROWING interest, not just many articles) -------------------------------------------
# Measured, never assumed: items and outlets added to the topic across our own successive scans (first_seen is when a scan first saw a
# signal), a Google Trends traffic tier and its change between scans (each signal keeps its engagement per scan in history), Reddit / X
# engagement growth, and a burst of fresh publications. Coverage by many outlets on its own scores low; without measured growth (scans,
# Google, engagement) a topic is capped at NO_GROWTH_CAP, below the automatic line (75).

NO_GROWTH_CAP = 60.0
GROWTH_LOOKBACK = timedelta(hours=3)
MIN_OBSERVED = timedelta(minutes=15)  # a topic first seen in this scan has no scan history yet (scans run every 5 minutes)
BURST_OUTLETS = 3  # independent reliable outlets first publishing it within the last hour: measured posting velocity
# general news outlets: a story they pick up has crossed over from its niche. Coverage-based momentum (scans, bursts) counts in full only
# with one of them; three crypto trade sites on a routine crypto story is their beat, not a trend (2026-11-15 calibration)
MAINSTREAM = {"reuters", "bloomberg", "associated press", "ap", "ap news", "cnbc", "financial times", "ft", "wall street journal", "wsj", "bbc",
              "bbc news", "new york times", "the new york times", "the guardian", "al jazeera", "axios", "politico", "cnn", "nbc news", "cbs news",
              "abc news", "dw", "sky news", "nikkei asia", "the washington post", "washington post", "marketwatch", "yahoo finance", "fortune",
              "forbes", "business insider", "the economist", "barron's", "npr", "france 24", "le monde", "abc australia", "the hill",
              "usa today", "newsweek", "the independent", "fox business", "fox news", "los angeles times", "the telegraph", "time"}
# a major incident (app/newsdesk/incidents) is breaking news when TWO independent credible outlets report it within the hour: outages are
# reported first by a few outlets and widely only hours later (2026-11-16: Fox Business 18:51, CNBC 22:14, BBC 00:18 on the bank outages)
INCIDENT_BURST_OUTLETS = 2
INCIDENT_SEARCH_BONUS = 0.25
NICHE_FACTOR = 0.6
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
    inc = cluster_incident(c)
    burst_need = INCIDENT_BURST_OUTLETS if inc is not None and inc.major else BURST_OUTLETS
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
        if inc is not None and inc.major and g_to >= 5_000 and _names_affected(str(t.extra.get("query") or ""), inc):
            # people searching for the very service that failed ("chase", "united airlines", "outlook down") measure how many it hit
            # (2026-11-16: Google's "chase" trend at 10K+ during JPMorgan Chase's outage was attached to a local police-chase story)
            v_google = min(1.0, v_google + INCIDENT_SEARCH_BONUS)
            out["notes"].append("searches for the affected service")
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
    # posting velocity: independent outlets whose FIRST item on it is from the last hour, and none of them older than two hours. Early
    # detection: it counts from the first scan, before any scan history exists; measured (publication times), never assumed.
    firsts: dict[str, datetime] = {}
    from app.trending.sensitive import outlet_key

    for s in sig:
        if s.reliable and s.outlet:
            k = outlet_key(s.outlet)
            firsts[k] = min(firsts.get(k, _seen(s)), _seen(s))
    hour = [k for k, t in firsts.items() if t >= now - timedelta(hours=1)]
    two = [k for k, t in firsts.items() if t >= now - timedelta(hours=2)]
    out["outlets_1h"], out["outlets_2h"] = len(hour), len(two)
    v_post = 0.0
    if len(hour) >= burst_need and len(hour) * 2 >= len(firsts):
        v_post = min(1.0, len(hour) / burst_need)
        out["evidence"].append("burst")
        out["notes"].append(f"{len(hour)} outlets broke it in the last hour")
    elif len(two) >= burst_need and len(two) * 3 >= len(firsts) * 2:  # two thirds new: acceleration, not steady coverage
        v_post = min(1.0, len(two) / (4 if burst_need == BURST_OUTLETS else 3))
        out["evidence"].append("burst")
        out["notes"].append(f"{len(two)} outlets broke it in the last 2 h")
    mainstream = any(outlet_key(s.outlet) in {outlet_key(m) for m in MAINSTREAM} for s in sig if s.reliable and s.outlet)
    out["mainstream"] = mainstream
    if not mainstream:
        v_scan, v_burst, v_post = NICHE_FACTOR * v_scan, NICHE_FACTOR * v_burst, NICHE_FACTOR * v_post
        if v_scan or v_post:
            out["notes"].append("niche outlets only")
    vals = sorted([v_scan, v_google, v_engage, 0.6 * v_burst, v_post], reverse=True)
    out["velocity"] = min(1.0, vals[0] + 0.3 * vals[1])
    out["measured"] = bool(out["evidence"])
    return out


# --- score ----------------------------------------------------------------------------------------------------------------

NORM = {"google": 6.0, "reddit": 4.5, "x_trends": 6.0, "x_accounts": 4.0}  # log10 of the engagement that counts as "very high"


FRESH_FULL = timedelta(hours=3)  # 0-3 h: today's news, full freshness
STALE_AFTER = timedelta(hours=6)  # older than this from the first report: stale unless a genuinely new development
DEVELOPMENT_WINDOW = timedelta(hours=2)
STALE_CAP = 35.0  # below the suggestion line: a stale story is never tweeted, however much it still trends
# market impact: what moves prices or positions (weights summed, capped); surprise: what nobody expected (same)
IMPACT = {"federal reserve": 2.0, "fed": 1.5, "fomc": 2.0, "rate cut": 2.0, "rate hike": 2.0, "interest rate": 1.5, "inflation": 1.5, "cpi": 2.0,
          "jobs report": 2.0, "payrolls": 2.0, "gdp": 1.5, "tariff": 1.5, "tariffs": 1.5, "sanction": 1.5, "sanctions": 1.5, "etf": 1.5, "sec": 1.5,
          "hack": 2.0, "hacked": 2.0, "exploit": 2.0, "liquidation": 2.0, "liquidations": 2.0, "liquidated": 2.0, "bankruptcy": 2.0, "bankrupt": 2.0,
          "default": 1.5, "halts": 1.5, "halted": 1.5, "delist": 1.5, "earnings": 1.0, "guidance": 1.0, "merger": 1.0, "acquisition": 1.0, "ipo": 1.0,
          "crash": 1.5, "crashes": 1.5, "plunge": 1.5, "plunges": 1.5, "tumbles": 1.0, "soars": 1.5, "surges": 1.0, "sell-off": 1.5, "selloff": 1.5,
          "record high": 1.5, "all-time high": 2.0, "lowest since": 1.5, "highest since": 1.0, "oil": 1.0, "opec": 1.5, "gold": 1.0, "treasury": 1.0,
          "yields": 1.0, "recession": 1.5, "stablecoin": 1.0, "bitcoin": 1.0, "ethereum": 1.0, "outflows": 1.0, "inflows": 1.0, "missile": 1.0,
          "ceasefire": 1.0, "strike": 0.5, "shutdown": 1.0, "rate hikes": 2.0, "rate cuts": 2.0, "rate rises": 2.0, "rate rise": 2.0,
          "military": 1.0, "attack": 1.0, "attacks": 1.0, "airstrike": 1.0, "airstrikes": 1.0, "invasion": 2.0, "nuclear": 1.0, "summit": 1.0,
          "trade talks": 1.0, "trade deal": 1.0, "targets": 0.5}
SURPRISE = {"unexpected": 2.0, "unexpectedly": 2.0, "surprise": 2.0, "surprises": 2.0, "shock": 2.0, "shocks": 2.0, "first time": 1.5,
            "first-ever": 2.0, "unprecedented": 2.0, "emergency": 2.0, "abrupt": 1.5, "abruptly": 1.5, "historic": 1.0, "record": 1.0,
            "biggest": 1.0, "worst": 1.0, "halts": 1.5, "suspends": 1.5, "resigns": 2.0, "ousted": 2.0, "collapse": 2.0, "collapses": 2.0,
            "crash": 1.5, "crashes": 1.5, "plunges": 1.5, "soars": 1.5, "seized": 1.5, "seizes": 1.5, "arrested": 1.5, "lowest since": 1.5}
CATEGORY_IMPACT = {"crypto": 1.0, "markets": 1.0, "stocks": 0.9, "geopolitics": 0.7, "technology": 0.5, "world": 0.3}
_BIG_MOVE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s?%")


def _words(text: str, table: dict[str, float]) -> tuple[float, list[str]]:
    low = text.lower()
    hits = [k for k in table if re.search(rf"\b{re.escape(k)}\b", low)]
    return sum(table[k] for k in hits), hits


def freshness_of(event_time: datetime, last_update: datetime, now: datetime, developing: bool) -> tuple[float, str | None]:
    """(0-1, stale reason or None). Full for the first three hours from the first report, fading to half at six; past six hours only a
    genuinely new development (in the last two hours) keeps it alive, at a low level, and only while that development is fresh."""
    age = now - event_time
    if age <= FRESH_FULL:
        return 1.0, None
    if age <= STALE_AFTER:
        return 1.0 - 0.5 * (age - FRESH_FULL) / (STALE_AFTER - FRESH_FULL), None
    if developing and now - last_update <= DEVELOPMENT_WINDOW:
        return 0.35, None
    return 0.05, f"first reported {age.total_seconds() / 3600:.0f} h ago, no new development in the last {int(DEVELOPMENT_WINDOW.total_seconds() // 3600)} h"


def score(c: Cluster, now: datetime, weights: dict[str, float] | None = None) -> tuple[float, dict[str, Any]]:
    sig = c.signals
    weights = weights or {}
    g = growth(c, now)
    momentum = 30 * g["velocity"]

    types = {s.source for s in sig}
    outlets = {s.outlet for s in sig if s.reliable}
    norms = [min(1.0, math.log10(max(s.engagement, 1)) / NORM[s.source]) for s in sig if s.source in NORM]
    norms.append(min(0.5, len(_outlets(sig)) / 6))  # coverage alone: half at most
    comments = sum(float(s.extra.get("comments") or 0) for s in sig)
    engagement = 10 * min(1.0, 0.6 * max(norms) + 0.2 * min(1.0, len(types) / 3) + 0.2 * min(1.0, comments / 500))

    # the timeline: when it happened (its first report), when we first saw it, its newest item
    pool = [s for s in sig if s.reliable] or sig
    event_time = min(_seen(s) for s in pool)
    first_seen = min(s.first_seen for s in sig)
    last_update = max(_seen(s) for s in sig)
    age = max(0.0, (now - event_time).total_seconds() / 3600)
    new_outlets = len(_outlets([s for s in sig if _seen(s) >= now - DEVELOPMENT_WINDOW]) - _outlets([s for s in sig if _seen(s) < now - DEVELOPMENT_WINDOW]))
    google_rise = bool(g.get("google_up")) and int(g.get("google_minutes") or 999) <= DEVELOPMENT_WINDOW.total_seconds() / 60
    developing = new_outlets >= 2 or google_rise
    fresh, stale = freshness_of(event_time, last_update, now, developing)
    freshness = 25 * fresh

    text = " ".join(s.title for s in pool[:6])
    imp_w, imp_hits = _words(text, IMPACT)
    big = max((float(m.group(1)) for m in _BIG_MOVE.finditer(text)), default=0.0)
    impact = 15 * min(1.0, 0.4 * CATEGORY_IMPACT.get(c.category, 0.3) + 0.6 * min(1.0, imp_w / 3 + (0.3 if big >= 5 else 0.0)))
    sur_w, sur_hits = _words(text, SURPRISE)
    surprise = 10 * min(1.0, sur_w / 3 + (0.4 if big >= 10 else 0.0))

    relevance = 10 * RELEVANCE.get(c.category, 0.4) * (0.6 + 0.4 * min(1.0, c.strength / 3))
    inc = cluster_incident(c)
    if inc is not None and inc.major:
        # a bank, a payment network, an exchange, the airspace or the country's email failing moves markets and reaches everyone: market
        # impact and surprise as a hack or an exchange halt would earn (production 2026-11-16: United's outage scored impact 3, surprise 0)
        impact = max(impact, 15 * (0.9 if inc.financial or inc.systemic else 0.75))
        surprise = max(surprise, 10 * (0.6 if not inc.resolved else 0.4))
        imp_hits = list(dict.fromkeys([inc.label] + imp_hits))

    mult = weights.get(f"cat:{c.category}", 1.0)
    src_w = [weights.get(f"src:{t}", 1.0) for t in types]
    mult *= sum(src_w) / len(src_w) if src_w else 1.0
    mult = max(0.8, min(1.2, mult))
    total = min(100.0, (momentum + freshness + impact + surprise + relevance + engagement) * mult)
    g["capped"] = not g["measured"] and total > NO_GROWTH_CAP
    if not g["measured"]:
        total = min(total, NO_GROWTH_CAP)  # no measured growth: never at the automatic line, however many outlets have it
    if stale:
        total = min(total, STALE_CAP)  # yesterday's story is not today's news, however much it still trends
    span_h = max(0.25, min(2.0, (now - event_time).total_seconds() / 3600))
    items_2h = sum(1 for s in sig if _seen(s) >= now - timedelta(hours=2))
    official = [s for s in sig if s.reliable and s.extra.get("official")]
    first_report = min([_seen(s) for s in sig] + [_seen(s) for s in official])
    verified = len(_outlets(sig)) >= 2 or bool(official)
    timeline = {"event_time": event_time.isoformat(), "first_seen": first_seen.isoformat(), "last_update": last_update.isoformat(),
                "first_report": first_report.isoformat(), "verified": verified,
                "source_count": len(_outlets(sig)) + len(types - {"news"}), "outlets": len(_outlets(sig)), "outlets_1h": g.get("outlets_1h", 0),
                "items_per_hour": round(items_2h / span_h, 1), "developing": developing, "new_outlets_2h": new_outlets}
    parts = {"velocity": round(momentum, 1), "freshness": round(freshness, 1), "impact": round(impact, 1), "surprise": round(surprise, 1),
             "relevance": round(relevance, 1), "engagement": round(engagement, 1), "learned": round(mult, 3), "recent_2h": g["recent_2h"],
             "prior_6h": g["prior_6h"], "sources": sorted(types), "outlets": sorted(outlets), "age_hours": round(age, 1), "stale": stale,
             "impact_words": imp_hits[:4], "surprise_words": sur_hits[:4], "timeline": timeline, "exception": c.exception,
             "incident": incident_out(c, inc),
             "growth": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in g.items()}}
    parts["why"] = why(parts, round(total, 1))
    return round(total, 1), parts


def cluster_incident(c: Cluster) -> Any:
    """The incident a topic tells, read from its lead (a reliable headline first): the strongest plain telling among its reliable items."""
    from app.newsdesk import incidents

    pool = c.reliable or c.signals
    found = [i for i in (incidents.detect(s.title) for s in [c.lead, *pool[:8]]) if i is not None]
    if not found:
        return None
    return max(found, key=lambda i: (i.major, bool(i.entities), i.scope, not i.resolved))


def _names_affected(query: str, inc: Any) -> bool:
    """A search term that names the failed service or the failure itself ("chase", "chase down", "united airlines", "outlook outage")."""
    q = checks.tokens(query) | set(re.findall(r"[a-z]+", query.lower()))
    if not q:
        return False
    names = set()
    for e in inc.entities:
        names |= set(re.findall(r"[a-z]+", e.lower()))
    return bool(q & (names - {"bank", "of", "the", "air", "lines"})) or bool(q & {"outage", "outages", "down", "grounded", "ground", "stop"})


def incident_out(c: Cluster, inc: Any) -> dict[str, Any] | None:
    """What Admin shows about an incident topic: what it is, and whether the evidence allows it to go out automatically."""
    if inc is None:
        return None
    from app.newsdesk import incidents

    reliable = [(s.outlet, s.title) for s in c.signals if s.reliable]
    official = any(s.extra.get("official") for s in c.signals if s.reliable)
    ok, because = incidents.verified(reliable, official=official)
    return {**inc.out(), "verified": ok, "evidence": because}


def why(parts: dict[str, Any], total: float) -> str:
    """Why this story ranks where it does, in one line (Admin and the scan log)."""
    if parts.get("stale"):
        return f"Stale: {parts['stale']}"
    t = parts.get("timeline") or {}
    bits = []
    age = float(parts.get("age_hours") or 0)
    bits.append(f"{'fresh' if age <= 3 else 'aging'} ({age:.1f} h since the first report)" + (", developing" if t.get("developing") and age > 3 else ""))
    g = parts.get("growth") or {}
    if g.get("notes"):
        bits.append("momentum: " + "; ".join(g["notes"][:2]))
    elif not g.get("measured"):
        bits.append("no measured growth (approval only)")
    if parts.get("exception"):
        bits.append(f"off-beat but exceptional ({parts['exception']})")
    if parts.get("incident"):
        inc = parts["incident"]
        bits.append(f"incident: {inc.get('why')}" + ("" if inc.get("verified") else f" (unverified: {inc.get('evidence')})"))
    if parts.get("impact_words"):
        bits.append("market impact: " + ", ".join(parts["impact_words"][:3]))
    if parts.get("surprise_words"):
        bits.append("surprise: " + ", ".join(parts["surprise_words"][:2]))
    bits.append(f"{t.get('outlets', 0)} outlet{'' if t.get('outlets') == 1 else 's'}")
    return f"{total:g}: " + "; ".join(bits)
