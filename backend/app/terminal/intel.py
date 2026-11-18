"""What the Terminal knows about one story (a Trending topic): two scores that are never mixed, the rumour watch, the fast lane and hooks.

- viral potential (0-100): shock, urgency, financial impact, novelty, controversy, curiosity, scale, relevance and MEASURED social momentum
  (Google Trends traffic, outlets publishing in the last hour, items per hour, Hacker News points, GDELT article counts, Reddit comments).
  No engagement figure is ever invented: a component with no measurement scores 0 and says so.
- evidence (0-100): official sources, independent reliable outlets, mainstream outlets, the affected company's own word, minus hedged or
  speculative wording. It decides what may be published; viral potential only decides what is worth looking at first.
- rumour: no reliable outlet at all, or a lead headline that is itself hedged ("reportedly", "unconfirmed", "sources say"). A rumour is never
  published automatically (owner, 2026-11-18): it goes to the Terminal's Rumor watch for a person to decide.
- fast lane: a verified (evidence >= FAST_EVIDENCE, not a rumour), fresh (<= FAST_MAX_AGE_MIN since the first report), high-impact event
  (a hard geopolitical event, a verified major outage, a major corporate shock, a market-wide swing, a big move in a major asset, a large
  hack, an official disaster alert) may publish without waiting for momentum scoring (Trending's 75 line) or for a News Desk slot. Every
  gate, cap, stop and the one-story rule still apply (app/trending/engine.auto_ok, coordinate).
- hooks: three short X headlines (breaking, curiosity, bold), built from the lead outlet's own headline only, each run through Trending's
  gate. A hook that fails is shown with the reason and can't be published as is.
"""

import math
import re
from typing import Any

from app.newsdesk import brief, editorial
from app.trending import rank, sensitive, writer

OFFICIAL_SOURCES = {"faa", "status", "usgs", "gdacs"}
# an outlet's X account is that outlet, not a second one (@Reuters and the Reuters feed are one report)
X_ALIASES = {"reuters": "reuters", "reutersbiz": "reuters", "ap": "ap", "afp": "afp", "bbcbreaking": "bbc", "business": "bloomberg", "markets": "bloomberg",
             "wsj": "wall street journal", "ft": "financial times", "cnbc": "cnbc", "coindesk": "coindesk", "theblock__": "the block"}
DISPUTE = re.compile(r"\b(denies|denied|deny|false|fake|debunk\w*|fact[- ]check\w*|no evidence|misleading|not true|hoax|refutes?|refuted|"
                     r"disputes?|disputed|contradict\w*|retract\w*|corrects?|correction)\b", re.I)
CLAIM_KINDS = {
    "cover-up": re.compile(r"\b(cover[- ]?up|covered up|hid(?:ing|den)?|secret\w*|classified|leak\w*|whistleblower)\b", re.I),
    "military incident": re.compile(r"\b(strike|missile|drone|shot down|jets?|warship|troops|explosion|attack\w*|intercept\w*)\b", re.I),
    "financial scandal": re.compile(r"\b(fraud|ponzi|insolven\w*|bankrupt\w*|embezzl\w*|insider|rug ?pull|exit scam|frozen withdrawals?|halts? withdrawals?)\b", re.I),
    "government controversy": re.compile(r"\b(resign\w*|impeach\w*|indict\w*|probe|investigat\w*|scandal|corruption|bribe\w*)\b", re.I),
    "catastrophe": re.compile(r"\b(earthquake|tsunami|crash\w*|killed|dead|deaths|collapse\w*|explosion|wildfire|flood\w*|outage\w*)\b", re.I),
}
SOCIAL_SOURCES = {"google", "reddit", "x_trends", "x_accounts", "hn", "gdelt"}
FAST_EVIDENCE = 55.0
FAST_MAX_AGE_MIN = 120
MAINSTREAM_KEYS = {sensitive.outlet_key(m) for m in rank.MAINSTREAM}

# the headline itself is hedged: a report of a report, a claim, a leak
RUMOR = re.compile(r"\b(reportedly|unconfirmed|unverified|rumou?rs?|rumou?red|alleged(?:ly)?|purported(?:ly)?|claims? (?:that|to)|"
                   r"sources? (?:say|said|tell|told)|people familiar|according to (?:a |one )?(?:source|report|post|leak)s?|leak(?:s|ed)?|"
                   r"speculat\w*|circulating|viral (?:video|post|claim)s?|unclear whether|not (?:yet )?confirmed|could not be (?:independently )?verified)\b", re.I)

SHOCK = re.compile(r"\b(attack\w*|strike[sd]?|explosion\w*|blast|missiles?|killed|dead|deaths?|crash\w*|plunge\w*|collapse\w*|halt\w*|"
                   r"hack\w*|exploit\w*|drained|stolen|outage\w*|down|grounded|ground stop|earthquake|tsunami|hurricane|default\w*|bankrupt\w*|"
                   r"seized|arrested|assassinat\w*|coup|invasion|emergency|evacuat\w*|wipe[sd]? out|liquidat\w*|depeg\w*)\b", re.I)
URGENT = re.compile(r"\b(breaking|just in|now|live|ongoing|developing|halted|emergency|immediately|tonight|this morning)\b", re.I)
NOVEL = re.compile(r"\b(first[- ]ever|first time|record\w*|never|unprecedented|historic|all-time|biggest|largest|since \d{4})\b", re.I)
CONTROVERSY = re.compile(r"\b(scandal\w*|fraud\w*|probe\w*|investigat\w*|accus\w*|lawsuit|sues|sued|indict\w*|charged|banned?|resign\w*|"
                         r"ousted|fired|subpoena\w*|cover-?up|whistleblower|sanction\w*|backlash)\b", re.I)
CURIOUS = re.compile(r"\b(mystery|mysterious|secret\w*|unknown|unexplained|suddenly|quietly|why|how|leak\w*|revealed|reveals|hidden|surpris\w*)\b", re.I)
SCALE = re.compile(r"\b(nationwide|global\w*|worldwide|millions?|billions?|trillions?|thousands|across (?:the )?(?:us|country|europe|asia)|"
                   r"several (?:banks|countries|airports)|all flights|entire)\b", re.I)
CASUALTIES = re.compile(r"\b(\d[\d,]*)\s+(?:people\s+|passengers\s+|soldiers\s+|civilians\s+)?(?:are\s+|were\s+)?(?:killed|dead|deaths|died)\b"
                        r"|\b(?:killing|kills|killed)\s+(?:all\s+|at least\s+)?(\d[\d,]*)\b|\bdeath toll (?:rises to |hits |reaches )?(\d[\d,]*)\b", re.I)
# audit 2026-11-18 (real misses, 2026-11-15..18): tankers hit by mines in Hormuz, Nasdaq's slide into a correction, Nvidia's results
SHIPPING = re.compile(r"\b(?:tankers?|ships?|vessels?|cargo ships?|container ships?)\b.{0,60}\b(?:hit|struck|attacked|seized|caught fire|ablaze|sunk|sink|explo\w+)\b"
                      r"|\bmines?\b.{0,30}\b(?:hit|struck|damag\w+)\b.{0,30}\b(?:tankers?|ships?|vessels?)\b", re.I)
CHOKEPOINT = re.compile(r"\b(?:hormuz|red sea|bab al-?mandab|suez|strait|gulf of (?:oman|aden)|black sea|taiwan strait)\b", re.I)
INDEX_SHOCK = re.compile(r"\b(?:s&p(?: 500)?|nasdaq|dow(?: jones)?|nikkei|stoxx|ftse|kospi|hang seng)\b.{0,50}\b(?:correction|bear market|worst (?:day|week|session)|"
                         r"crash\w*|circuit breakers?|trading halt\w*|plunges? \d|tumbles? \d)\b"
                         r"|\b(?:correction|bear market|worst (?:day|week|session))\b.{0,40}\b(?:s&p(?: 500)?|nasdaq|dow)\b", re.I)
EARNINGS = re.compile(r"\b(?:earnings|results|revenue|quarterly|guidance|forecast|profit)\b", re.I)
EARNINGS_MOVE = re.compile(r"\b(?:beats?|misses?|tops?|record|jumps?|soars?|surges?|plunges?|slumps?|sinks?|raises?|cuts?|slashes?)\b", re.I)
# a hard geopolitical event is major when it reaches strategic ground: US or allied forces, the Gulf and its oil, a capital, a bridge,
# a base, a nuclear site, a chokepoint, a great power. "Russian drone attack on Kharkiv kills three" is war news, not a breaking override.
STRATEGIC = re.compile(r"\b(?:u\.?s\.?|us|american|nato|pentagon|iran\w*|irgc|israel\w*|china|chinese|taiwan|north korea\w*|nuclear|saudi\w*|gulf|"
                       r"hormuz|bahrain\w*|kuwait\w*|qatar\w*|uae|emirates|oil|refiner\w*|terminal|pipeline|bridge|air ?base|base|carrier|navy|warships?|"
                       r"kremlin|moscow|kyiv|tehran|beijing|crimea\w*|red sea|strait|nato)\b", re.I)
PREVIEW = re.compile(r"\b(?:set to (?:report|announce|meet|vote|release)|ahead of|expected to|is due to|due to report|will report|preview|what to expect|awaits?|braces? for)\b", re.I)
HACK = re.compile(r"\b(hack\w*|exploit\w*|drained|stolen|heist|breach\w*)\b", re.I)
FIGURE = re.compile(r"(?:[$€£]\s?\d[\d,.]*\s?(?:bn|billion|b|m|mn|million|trillion|tn|k)?\b)|(?:\b\d[\d,.]*\s?%)|(?:\b\d[\d,.]*\s(?:billion|million|trillion)\b)", re.I)
AUDIENCE = {"crypto": "crypto", "stocks": "stocks", "markets": "markets", "geopolitics": "markets", "technology": "tech", "world": "markets"}


def casualties(text: str) -> int:
    """The largest death or injury figure the words state, else 0."""
    return max((int(g.replace(",", "")) for m in CASUALTIES.finditer(text or "") for g in m.groups() if g), default=0)


def _sources(srcs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [x for x in (srcs or []) if isinstance(x, dict)]


def _extra(x: dict[str, Any]) -> dict[str, Any]:
    return x.get("extra") or {}


def official(x: dict[str, Any]) -> bool:
    return bool(x.get("reliable")) and (x.get("source") in OFFICIAL_SOURCES or bool(_extra(x).get("official")))


def outlet_of(x: dict[str, Any]) -> str:
    """The reporting organisation behind a source item: an outlet's X account counts as that outlet."""
    o = str(x.get("outlet") or "")
    if x.get("source") == "x_accounts":
        h = o.lstrip("@").lower()
        return X_ALIASES.get(h, "@" + h)
    return sensitive.outlet_key(o)


X_NAMES = {"reuters": "Reuters", "ap": "AP", "afp": "AFP", "bloomberg": "Bloomberg", "bbc": "BBC", "wall street journal": "WSJ",
           "financial times": "FT", "cnbc": "CNBC", "coindesk": "CoinDesk", "the block": "The Block"}


def x_credit(x: dict[str, Any]) -> str:
    """How a post is credited in our text: the outlet's name (Reuters), else the account name without the @ (CENTCOM on X)."""
    o = outlet_of(x)
    if o in X_NAMES:
        return X_NAMES[o]
    handle = str(_extra(x).get("handle") or str(x.get("outlet") or "").lstrip("@"))
    return f"{handle} on X" if o.startswith("@") else o


def x_tier(x: dict[str, Any]) -> str:
    return str(_extra(x).get("tier") or "C") if x.get("source") == "x_accounts" else ""


def lead_of(srcs: list[dict[str, Any]], title: str) -> dict[str, Any]:
    rel = [x for x in srcs if x.get("reliable")]
    return (rel or srcs or [{"title": title, "outlet": "", "source": ""}])[0]


# --- evidence ------------------------------------------------------------------------------------------------------------------------


def evidence(title: str, srcs: list[dict[str, Any]] | None, parts: dict[str, Any] | None) -> dict[str, Any]:
    """{"score", "level", "rumor", "reasons", "outlets", "official"}: how well what we have backs the story, independent of its reach."""
    srcs, parts = _sources(srcs), parts or {}
    reliable = [x for x in srcs if x.get("reliable")]
    offs = [x for x in reliable if official(x)]
    outlets = sorted({outlet_of(x) for x in reliable if not official(x)} - {""})
    main = [o for o in outlets if o in MAINSTREAM_KEYS]
    lead = lead_of(srcs, title)
    lead_title = str(lead.get("title") or title or "")
    reasons: list[str] = []
    score = 0.0
    if offs:
        score += 50
        reasons.append(f"official source: {offs[0].get('outlet')}")
    n = len(outlets)
    score += (0, 30, 55, 68)[n] if n < 4 else 78
    if n:
        reasons.append(f"{n} independent reliable outlet{'s' if n != 1 else ''}" + (f" ({', '.join(outlets[:4])})" if n else ""))
    if main:
        score += 10
        reasons.append("mainstream: " + ", ".join(main[:3]))
    # X: tier-B accounts (named investigators, newsrooms) add partial weight; they never verify a story on their own (app/terminal/xintel)
    xb = sorted({str(x.get("outlet") or "") for x in srcs if x_tier(x) == "B"})
    if xb:
        score += min(20.0, 10.0 * len(xb))
        reasons.append(f"{len(xb)} tier-B X account{'s' if len(xb) != 1 else ''} ({', '.join(xb[:3])}): partial weight")
    inc = parts.get("incident") or {}
    if inc.get("major") and inc.get("verified"):
        score = max(score, 60.0)
        reasons.append(f"incident verified: {inc.get('evidence') or 'yes'}")
    hedged = RUMOR.search(lead_title)
    spec = editorial.SPECULATIVE.search(lead_title)
    if hedged:
        score -= 25
        reasons.append(f"hedged headline ({hedged.group(0).lower()})")
    elif spec:
        score -= 10
        reasons.append(f"speculative wording ({spec.group(0).lower()})")
    rumor = not reliable or bool(hedged)
    if not reliable:
        social = [x for x in srcs if x.get("source") in SOCIAL_SOURCES or x.get("source") == "x_accounts"]
        score = min(30.0 if xb else 20.0, 5.0 * len({x.get("source") for x in social}) + 2.0 * min(5, len(social)) + min(20.0, 10.0 * len(xb)))
        reasons.insert(0, "no reliable outlet yet: social, X or search signals only")
    score = max(0.0, min(100.0, score))
    level = ("confirmed" if score >= 75 and (n >= 2 or offs) else "corroborated" if score >= 55 else "single source" if score >= 30 else "unverified")
    return {"score": round(score, 1), "level": level, "rumor": rumor, "reasons": reasons, "outlets": n, "official": bool(offs),
            "mainstream": bool(main)}


# --- viral potential -----------------------------------------------------------------------------------------------------------------

WEIGHTS = {"shock": 14, "urgency": 12, "financial": 14, "novelty": 8, "controversy": 8, "curiosity": 6, "scale": 12, "relevance": 10, "social": 16}


def _hits(rx: re.Pattern[str], text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).lower() for m in rx.finditer(text)))


def social(srcs: list[dict[str, Any]], parts: dict[str, Any]) -> tuple[float, list[str]]:
    """0-1 and what was measured. Only our sources' own figures: never an estimate."""
    tl = parts.get("timeline") or {}
    g = parts.get("growth") or {}
    seen: list[str] = []
    traffic = max((sources_traffic(x) for x in srcs if x.get("source") == "google"), default=0.0)
    gt = rank.tier(traffic) if traffic else 0.0
    if traffic:
        seen.append(f"Google Trends {rank.short(traffic)}+ searches" + (" and rising" if g.get("google_up") else ""))
    o1h = int(tl.get("outlets_1h") or g.get("outlets_1h") or 0)
    if o1h:
        seen.append(f"{o1h} outlet{'s' if o1h != 1 else ''} published in the last hour")
    iph = float(tl.get("items_per_hour") or 0)
    if iph >= 1:
        seen.append(f"{iph:g} reports per hour")
    hn = max((float(_extra(x).get("points") or x.get("engagement") or 0) for x in srcs if x.get("source") == "hn"), default=0.0)
    if hn:
        seen.append(f"Hacker News {int(hn)} points")
    gd = sum(1 for x in srcs if x.get("source") == "gdelt")
    if gd:
        seen.append(f"{gd} early web report{'s' if gd != 1 else ''} (GDELT)")
    rc = sum(float(_extra(x).get("comments") or 0) for x in srcs if x.get("source") == "reddit")
    if rc:
        seen.append(f"{int(rc)} Reddit comments")
    xs = [x for x in srcs if x.get("source") == "x_accounts"]
    xe = sum(float(x.get("engagement") or 0) for x in xs)
    xa = len({str(x.get("outlet") or "").lower() for x in xs})
    if xs:
        seen.append(f"X: {xa} watched account{'s' if xa != 1 else ''}, {int(xe)} likes/reposts/replies/quotes (X's public counts)")
    v = (0.35 * gt + 0.25 * min(1.0, o1h / 4) + 0.15 * min(1.0, iph / 6) + 0.1 * min(1.0, math.log10(max(hn, 1)) / 3)
         + 0.1 * min(1.0, gd / 5) + 0.05 * min(1.0, rc / 500) + 0.2 * min(1.0, math.log10(max(xe, 1)) / 4) + 0.1 * min(1.0, xa / 4))
    if g.get("google_up") or g.get("burst"):
        v = min(1.0, v + 0.1)
    return min(1.0, v), seen


def sources_traffic(x: dict[str, Any]) -> float:
    """A Google trend's search traffic: its approx_traffic label ("200K+"), else the engagement we stored for it."""
    raw = _extra(x).get("approx_traffic")
    return _traffic(str(raw)) if raw else float(x.get("engagement") or 0)


def _traffic(raw: str) -> float:
    from app.trending.sources import traffic

    return traffic(raw)


def viral(title: str, category: str, srcs: list[dict[str, Any]] | None, parts: dict[str, Any] | None) -> dict[str, Any]:
    """{"score", "parts": {component: 0-10}, "drivers": [...], "measured": [...]}: how far it could travel, not whether it is true."""
    srcs, parts = _sources(srcs), parts or {}
    text = " ".join(str(x.get("title") or "") for x in srcs[:6]) or title
    comp: dict[str, float] = {}
    drivers: list[str] = []
    shock = _hits(SHOCK, text)
    big = max((float(m.group(1)) for m in rank._BIG_MOVE.finditer(text)), default=0.0)
    comp["shock"] = min(10.0, 3.5 * len(shock) + (4 if big >= 10 else 2 if big >= 5 else 0))
    age_h = float(parts.get("age_hours") or 0)
    comp["urgency"] = min(10.0, (10 if age_h <= 1 else 7 if age_h <= 3 else 4 if age_h <= 6 else 1) * (0.5 if parts.get("stale") else 1.0)
                          + (2 if URGENT.search(text) else 0))
    usd = brief._amount_usd(text)
    comp["financial"] = min(10.0, float(parts.get("impact") or 0) / 15 * 8 + (2 if usd >= 1e9 else 1 if usd >= 1e8 else 0))
    comp["novelty"] = min(10.0, 4.0 * len(_hits(NOVEL, text)) + (3 if parts.get("surprise_words") else 0))
    comp["controversy"] = min(10.0, 4.0 * len(_hits(CONTROVERSY, text)))
    comp["curiosity"] = min(10.0, 4.0 * len(_hits(CURIOUS, text)) + (2 if "?" in title else 0))
    cas = casualties(text)
    outlets = int((parts.get("timeline") or {}).get("outlets") or 0)
    comp["scale"] = min(10.0, 3.0 * len(_hits(SCALE, text)) + (5 if cas >= 100 else 3 if cas >= 10 else 0) + min(4.0, outlets * 0.8)
                        + (3 if (parts.get("incident") or {}).get("major") else 0))
    comp["relevance"] = min(10.0, float(parts.get("relevance") or 0))
    soc, measured = social(srcs, parts)
    comp["social"] = round(10 * soc, 1)
    total = sum(WEIGHTS[k] * comp[k] / 10 for k in WEIGHTS)
    for k in sorted(comp, key=lambda k: -WEIGHTS[k] * comp[k]):
        if comp[k] >= 5:
            drivers.append(k)
    if shock:
        drivers = drivers or ["shock"]
    return {"score": round(min(100.0, total), 1), "parts": {k: round(v, 1) for k, v in comp.items()}, "drivers": drivers[:4],
            "measured": measured, "words": (shock + _hits(NOVEL, text))[:5]}


def claim_status(title: str, srcs: list[dict[str, Any]] | None, ev: dict[str, Any]) -> dict[str, Any]:
    """Rumour radar: confirmed (corroborated or better, not hedged), disputed (any source denies, debunks or corrects it), or
    unsupported; and what kind of claim it is. Shown with the original claims, never published as fact unless confirmed."""
    srcs = _sources(srcs)
    texts = [str(x.get("title") or "") for x in srcs] or [title]
    disputes = [{"outlet": x.get("outlet"), "title": x.get("title"), "url": x.get("url")} for x in srcs if DISPUTE.search(str(x.get("title") or ""))]
    kinds = [k for k, rx in CLAIM_KINDS.items() if any(rx.search(t) for t in texts)]
    if disputes:
        status = "disputed"
    elif not ev.get("rumor") and ev.get("level") in ("confirmed", "corroborated"):
        status = "confirmed"
    elif not ev.get("rumor"):
        status = "reported"
    else:
        status = "unsupported"
    first = min((x for x in srcs if x.get("at")), key=lambda x: str(x.get("at")), default=srcs[0] if srcs else {"title": title})
    return {"status": status, "kinds": kinds, "disputes": disputes[:4], "original": {"outlet": first.get("outlet"), "title": first.get("title"), "url": first.get("url"), "at": first.get("at")}}


def ahead_of_mainstream(srcs: list[dict[str, Any]] | None, parts: dict[str, Any] | None, ev: dict[str, Any]) -> bool:
    """Spreading before the major outlets have it: measured social or early-web signals, growing, no mainstream outlet or official source."""
    srcs, parts = _sources(srcs), parts or {}
    social_items = [x for x in srcs if x.get("source") in SOCIAL_SOURCES]
    g = parts.get("growth") or {}
    return bool(social_items) and not ev["mainstream"] and not ev["official"] and bool(g.get("measured") or g.get("google_up") or len(social_items) >= 2)


# --- the fast lane -------------------------------------------------------------------------------------------------------------------


def impact_class(title: str, summary: str, srcs: list[dict[str, Any]] | None, parts: dict[str, Any] | None) -> tuple[str | None, str]:
    """(kind, why) when the story is a high-impact event in itself, else (None, why not)."""
    srcs, parts = _sources(srcs), parts or {}
    t = title or ""
    inc = parts.get("incident") or {}
    if inc.get("major") and inc.get("verified") and not inc.get("resolved"):
        return "incident", f"verified major {inc.get('label') or 'incident'}"
    for x in srcs:
        e = _extra(x)
        if x.get("source") == "usgs" and float(e.get("magnitude") or 0) >= 6.5:
            return "disaster", f"USGS: magnitude {float(e['magnitude']):.1f} earthquake"
        if x.get("source") == "gdacs" and str(e.get("alert") or "").lower() == "red":
            return "disaster", f"GDACS red alert: {e.get('event') or 'disaster'}"
    if PREVIEW.search(t):  # "Nvidia set to report ... since Jensen Huang resigned": a preview, its background is old news
        return None, f"a preview or schedule, not the event ({PREVIEW.search(t).group(0).lower()})"
    cas = casualties(t)
    if cas >= 10:
        return "disaster", f"{cas}+ casualties reported"
    sig = editorial.significance(t, summary)
    if sig is not None:
        if sig.kind == "geopolitics" and editorial.hard_event(t) and STRATEGIC.search(t):
            return "geopolitics", f"hard geopolitical event ({sig.why})"
        if sig.kind == "corporate" and editorial.CORP_BIG.search(t):
            return "corporate", f"major corporate shock ({sig.why})"
        if sig.kind == "macro" and sig.why.startswith("market-wide swing"):
            return "markets", sig.why
        if sig.kind == "infrastructure" and inc.get("verified"):
            return "incident", sig.why
    if SHIPPING.search(t) and CHOKEPOINT.search(t):
        return "geopolitics", f"shipping attacked at a chokepoint ({CHOKEPOINT.search(t).group(0)})"
    if INDEX_SHOCK.search(t) and not editorial.SPECULATIVE.search(t):
        return "markets", f"index shock ({INDEX_SHOCK.search(t).group(0).lower()})"
    if editorial.MEGA.search(t) and EARNINGS.search(t) and EARNINGS_MOVE.search(t) and FIGURE.search(t) and not editorial.SPECULATIVE.search(t):
        return "corporate", f"{editorial.MEGA.search(t).group(0)} results"
    usd = brief._amount_usd(t)
    if HACK.search(t) and usd >= 5e7 and not editorial.SPECULATIVE.search(t):
        return "hack", f"{HACK.search(t).group(0).lower()} of ${usd / 1e6:,.0f}M"
    subjects = editorial.assets_in(t)
    big = max((float(m.group(1)) for m in rank._BIG_MOVE.finditer(t)), default=0.0)
    if subjects and big and editorial.tier(editorial.asset_class(subjects), big) >= editorial.MAJOR_TIER:
        return "markets", f"major move: {', '.join(sorted(subjects))} {big:g}%"
    return None, "not a high-impact event in itself (no hard event, verified outage, shock, swing, big hack or disaster alert)"


def fast_lane(title: str, summary: str, srcs: list[dict[str, Any]] | None, parts: dict[str, Any] | None, ev: dict[str, Any]) -> tuple[bool, str]:
    """(True, why) when a verified, fresh, high-impact event may publish without waiting for momentum or a slot."""
    parts = parts or {}
    if ev["rumor"]:
        return False, "rumour or hedged report: human review only"
    if ev["score"] < FAST_EVIDENCE:
        return False, f"not verified enough for the fast lane (evidence {ev['score']:.0f} < {FAST_EVIDENCE:.0f})"
    if parts.get("stale"):
        return False, "stale"
    age_min = float(parts.get("age_hours") or 0) * 60
    if age_min > FAST_MAX_AGE_MIN:
        return False, f"first reported {age_min:.0f} min ago (fast lane only within {FAST_MAX_AGE_MIN} min)"
    kind, why = impact_class(title, summary, srcs, parts)
    if kind is None:
        return False, why
    return True, f"{why}; evidence {ev['level']} ({ev['score']:.0f}); {age_min:.0f} min since first report"


# --- hooks ---------------------------------------------------------------------------------------------------------------------------


def rule_hooks(title: str, outlet: str, category: str, ev: dict[str, Any], vscore: float, sens: dict[str, Any] | None) -> list[dict[str, str]]:
    """Three alternatives from the lead headline's own words. No new facts, no casualty figures beyond the headline's, no certainty a
    rumour doesn't have."""
    h = writer.headline(title).rstrip(" .")
    if not h:
        return []
    sens = sens or sensitive.classify(h)
    attributed = bool(writer.ATTRIBUTED.search(h))
    credit = f", {outlet} reports" if outlet and not attributed and ev["level"] in ("single source", "unverified") else ""
    fig = next((m.group(0).strip() for m in FIGURE.finditer(h)), "")
    if ev["rumor"]:
        src = f" ({outlet})" if outlet else ""
        return [{"kind": "breaking", "text": f"UNCONFIRMED: {h}{src}. Not verified yet."},
                {"kind": "viral", "text": f"Unverified, but watch this: {h}{src}."},
                {"kind": "curiosity", "text": f"Reports circulating: {h}. Price Chat is checking."}]
    breaking = f"BREAKING: {h}{credit}" if ev["level"] in ("confirmed", "corroborated") else f"JUST IN: {h}{credit}"
    # the curiosity line names who or where it is about first; the words stay the headline's own
    known = editorial.names_in(h)
    names = [w for w in re.findall(r"\b[A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]+)*", h) if w.lower() in known]
    subject = max(names, key=len) if names else ""
    curiosity = f"All eyes on {subject}: {h}{credit}." if subject else f"Developing: {h}{credit}."
    if sens:  # deaths, attacks on people, allegations: plain and attributed, never provocative
        bold = f"{h}{credit or (', ' + outlet + ' reports' if outlet and not attributed else '')}."
    elif fig:
        bold = f"{fig}. {h}{credit}."
    else:
        bold = f"{'Huge' if vscore >= 70 else 'Big'}: {h}{credit}."  # intensity only: no claim of market impact the source didn't make
    return [{"kind": k, "text": with_credit(v, outlet)} for k, v in (("breaking", breaking), ("viral", bold), ("curiosity", curiosity))]


def with_credit(text: str, outlet: str) -> str:
    """The house credit line ("Via Reuters", as Trending's writer ends a post) unless the words already name the outlet."""
    text = text.strip()
    return text if not outlet or outlet.lower() in text.lower() else f"{text}\n\nVia {outlet}"


def check_hook(text: str, *, source_text: str, outlet: str, reliable_count: int, breaking_ok: bool) -> dict[str, Any]:
    from app.trending import gates

    r = gates.gate(text, source_text=source_text, source_names=[outlet] if outlet else [], reliable_count=reliable_count, breaking_ok=breaking_ok)
    return {"ok": r["ok"], "reason": r["reason"]}


def breaking_allowed(ev: dict[str, Any]) -> bool:
    """BREAKING / JUST IN only on a verified story (corroborated or better, never a rumour)."""
    return not ev["rumor"] and ev["level"] in ("confirmed", "corroborated")
