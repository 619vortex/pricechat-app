"""Editorial judgement across slots (owner, 2026-11-10): one story is covered once, unless something materially new happens; genuinely
important verified news outranks routine price moves; a slot with nothing worthwhile and new stays empty.

WHAT HAPPENED (the last 48 hours of the channel, reviewed 2026-11-10): "Oil (WTI) slides, down 4.4% in 24 hours" at 04:00 Dubai, then "Oil (WTI)
gets hammered, down 6.0% in 24 hours" at 08:00 with a near-identical chart; two written market briefs in a row both led by "Ethereum down
~6% in 24 hours"; two Bitcoin liquidation "alerts" of $8M and $17M. Meanwhile the verified stories of those hours (Nvidia's board and the
FBI investigating its CEO, Trump calling the Iran war "effectively over", NYSE choosing Avalanche to settle tokenized stocks, Coinbase's
federal trust charter, Brent falling 7% on Iran peace hopes) never became candidates: the evidence filter only admitted "market chaos",
and a slot without a story fell back to the day's biggest price change however ordinary (3% crypto, 1.5% macro) and however recently shown.

THE RULES HERE
1. A story's identity (`signature`): its subjects (the asset, company, country or named party it is about, canonicalised: WTI, Brent and
   "oil" are all OIL), its type (a market move, an incident such as a hack, a controversy, geopolitics, regulation ...), its direction and
   magnitude for a market move, and whether it carries a cause from a verified source. Stamped on the event when it is released (facts["story"]),
   so the record survives restarts and needs no new table; events released before this existed get theirs computed from their fields.
2. The same story (`judge`): a released event shares a subject and a type family within the type's window (market moves 24 h, incidents
   72 h, other news 48 h). It goes out again only when something materially new happened:
   - a market move that ESCALATES: at least 1.5 times the size already reported AND into a higher severity tier (TIERS: the desk's own
     24-hour alert line, then roughly 1.5x steps). Trailing 24-hour figures drift a few points from slot to slot as the window rolls
     (-4.4% then -6.0% is one selloff restated); a step into the next tier at half as big again is where wire services write a new
     headline ("extends losses to 9%");
   - a move the other way (a reversal is a different story), or a verified report giving the CAUSE of a move we only showed as a number;
   - for news, a development the desk already established (events.development: a bigger figure, an arrest, an official confirmation),
     which arrives as an "update" event.
3. Importance: stories that matter but aren't "market chaos" (geopolitics, regulation milestones, mega-cap corporate shocks, the big
   institutions moving onto crypto rails, ETF flows of $500M+) are admitted when verified by an official source or a second outlet, and
   scored by `importance_score`. A price move on its own takes a slot only when it is exceptional (`exceptional`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.newsdesk import brief, checks

# --- subjects ------------------------------------------------------------------------------------------------------

# canonical subject <- how headlines and our symbols name it
ALIASES: dict[str, re.Pattern[str]] = {
    "OIL": re.compile(r"\b(oil|crude|wti|brent|opec\+?)\b", re.I),
    "BTC": re.compile(r"\b(bitcoin|btc)\b", re.I),
    "ETH": re.compile(r"\b(ether|ethereum|eth)\b", re.I),
    "SOL": re.compile(r"\b(solana)\b", re.I),
    "XRP": re.compile(r"\bxrp\b", re.I),
    "HYPE": re.compile(r"\b(hyperliquid|hype)\b", re.I),
    "GOLD": re.compile(r"\bgold\b", re.I),
    "SILVER": re.compile(r"\bsilver\b", re.I),
    "SPX": re.compile(r"s&p(?:\s?500)?|\bdow jones\b|\bthe dow\b|\bwall street\b", re.I),
    "IXIC": re.compile(r"\bnasdaq\b(?![- ]listed)", re.I),
}
SYMBOLS = {"CL=F": "OIL", "BZ=F": "OIL", "BRENT": "OIL", "OIL": "OIL", "OIL (WTI)": "OIL", "OIL (BRENT)": "OIL", "WTI": "OIL", "BTC-USD": "BTC", "$BTC": "BTC",
           "BITCOIN": "BTC", "ETH-USD": "ETH", "$ETH": "ETH", "ETHEREUM": "ETH", "SOL-USD": "SOL", "$SOL": "SOL", "SOLANA": "SOL", "XRP-USD": "XRP", "$HYPE": "HYPE",
           "GC=F": "GOLD", "SI=F": "SILVER", "^GSPC": "SPX", "S&P 500": "SPX", "DJI": "SPX", "^DJI": "SPX", "DOW JONES": "SPX", "^IXIC": "IXIC", "NASDAQ": "IXIC",
           "US-STOCKS": "SPX", "CRYPTO": "CRYPTO", "HG=F": "COPPER"}
COUNTRIES: dict[str, re.Pattern[str]] = {
    "IRAN": re.compile(r"\biran\w*\b", re.I), "ISRAEL": re.compile(r"\b(israel\w*|gaza|hamas|hezbollah)\b", re.I),
    "RUSSIA": re.compile(r"\b(russia\w*|kremlin|putin)\b", re.I), "UKRAINE": re.compile(r"\b(ukrain\w*|kyiv|zelensk\w*)\b", re.I),
    "CHINA": re.compile(r"\b(china|chinese|beijing|xi jinping)\b", re.I), "SAUDI": re.compile(r"\b(saudi\w*|riyadh|aramco)\b", re.I),
    "NKOREA": re.compile(r"\b(north korea\w*|pyongyang|lazarus)\b", re.I), "TAIWAN": re.compile(r"\btaiwan\w*\b", re.I),
    "VENEZUELA": re.compile(r"\bvenezuel\w*\b", re.I), "YEMEN": re.compile(r"\b(yemen\w*|houthis?)\b", re.I), "QATAR": re.compile(r"\b(qatar\w*|doha)\b", re.I),
    "INDIA": re.compile(r"\b(india|indian|new delhi)\b", re.I), "JAPAN": re.compile(r"\b(japan\w*|tokyo|bank of japan)\b", re.I),
    "EU": re.compile(r"\b(european union|european commission|brussels|eurozone)\b", re.I), "UK": re.compile(r"\b(britain|british|bank of england)\b", re.I),
}
GENERIC = {"crypto", "markets", "market", "report", "reports", "says", "after", "amid", "over", "into", "with", "from", "this", "that", "week", "today", "price", "prices",
           "shares", "stock", "stocks", "billion", "million", "news", "live", "first", "elliptic", "data", "analysts", "investors", "traders"}


def canon(symbol_or_name: str) -> str:
    s = str(symbol_or_name or "").strip().upper()
    if s.startswith("LIQ:"):
        s = s[4:]
    return SYMBOLS.get(s, s.lstrip("$"))


def assets_in(text: str) -> set[str]:
    return {k for k, rx in ALIASES.items() if rx.search(text or "")}


def countries_in(text: str) -> set[str]:
    return {k for k, rx in COUNTRIES.items() if rx.search(text or "")}


def names_in(title: str) -> set[str]:
    """Specific names (Binance, Nvidia, Avalanche), lower case; the generic words a headline capitalises don't count."""
    return {n for n in checks.names(title) if n not in GENERIC and not n.isdigit()}


# --- types ---------------------------------------------------------------------------------------------------------

FAMILY = {"hack": "incident", "scam": "incident", "failure": "incident", "move": "market", "liquidations": "market", "whale": "market", "recap": "market",
          "controversy": "controversy", "macro": "macro", "etf": "flows", "flows": "flows", "geopolitics": "geopolitics", "regulation": "regulation",
          "corporate": "corporate", "structure": "structure"}
WINDOW = {"market": timedelta(hours=24), "incident": timedelta(hours=72)}
NEWS_WINDOW = timedelta(hours=48)
CAUSE = re.compile(r"\b(as|amid|after|on|following|over|despite|because of|due to)\b\s+\S+", re.I)

# severity tiers per asset class, in % over 24 hours: the first is the desk's own 24-hour alert line (app/newsdesk/moves.LINES), then ~1.5x steps
TIERS: dict[str, tuple[float, ...]] = {
    "crypto_major": (8.0, 12.0, 18.0, 25.0, 35.0),
    "crypto_alt": (15.0, 22.0, 33.0, 50.0),
    "index": (2.5, 4.0, 6.0, 9.0),
    "stock": (8.0, 12.0, 18.0, 27.0),
    "commodity": (5.0, 7.5, 11.0, 16.0, 24.0),
}
CLASS = {"BTC": "crypto_major", "ETH": "crypto_major", "CRYPTO": "crypto_major", "SOL": "crypto_alt", "XRP": "crypto_alt", "HYPE": "crypto_alt", "SPX": "index",
         "IXIC": "index", "OIL": "commodity", "GOLD": "commodity", "SILVER": "commodity", "COPPER": "commodity"}
ESCALATION = 1.5  # a repeat move must be at least this many times the size already reported (and reach a higher tier)


def asset_class(subjects: set[str] | list[str]) -> str:
    for s in subjects:
        if s in CLASS:
            return CLASS[s]
    return "stock"


def tier(cls: str, pct: float) -> int:
    return sum(1 for t in TIERS.get(cls, TIERS["stock"]) if abs(pct) >= t)


def exceptional_pct(cls: str, pct: float) -> bool:
    """A 24-hour move worth a post on its own: at or past the desk's 24-hour alert line for its class."""
    return tier(cls, pct) >= 1


# --- the signature -------------------------------------------------------------------------------------------------


def _pct_of(ev: Any) -> float:
    f = ev.facts or {}
    for k in ("pct", "change_pct"):
        if isinstance(f.get(k), (int, float)):
            return float(f[k])
    tag = str(f.get("price_tag") or "")
    m = re.search(r"([+-]\d+(?:\.\d+)?)%", tag)
    if m:
        return float(m.group(1))
    m = brief.PERCENT.search(f"{ev.headline or ''}")
    if m:
        return float(m.group(1)) * (-1 if f.get("falling") else 1)
    return 0.0


def signature(ev: Any) -> dict[str, Any]:
    """What story an event is: stored as facts["story"] when released. Works for events made before this existed."""
    f = ev.facts or {}
    if isinstance(f.get("story"), dict) and f["story"].get("subjects"):
        return f["story"]
    topic = ev.topic or ""
    text = f"{ev.title or ''} {ev.headline or ''}"
    kind = ev.kind or "news"
    if kind in ("recap", "market") or (kind != "update" and topic in ("move", "liquidations")):
        typ = "market"
    else:
        typ = FAMILY.get(topic, "news")
    subjects: set[str] = set()
    if typ == "market":
        own = [*(f.get("assets") or []), *(f.get("recap_assets") or [])]
        if f.get("name") and kind == "recap":
            own.append(str(f["name"]))
        subjects |= {canon(a) for a in own if a}
        if kind in ("news", "update") or not subjects:
            subjects |= assets_in(ev.title or ev.headline or "")
        if not subjects:
            subjects |= names_in(ev.title or "")
    else:
        subjects |= names_in(ev.title or ev.headline or "") | countries_in(text) | {canon(a) for a in (f.get("assets") or [])}
    pct = _pct_of(ev) if typ == "market" else 0.0
    falling = f.get("falling")
    direction = ("down" if pct < 0 else "up") if pct else ("down" if falling else "up" if falling is False else "")
    if f.get("direction") in ("up", "down"):
        direction = f["direction"]
    cause = bool(kind in ("news", "update") and typ == "market" and CAUSE.search(ev.title or ""))
    return {"subjects": sorted(subjects), "type": typ, "direction": direction, "magnitude": round(abs(pct), 2), "class": asset_class(subjects),
            "cause": cause, "event": ev.id, "headline": (ev.headline or "")[:140]}


def stamp(ev: Any, now: datetime) -> None:
    sig = signature(ev)
    ev.facts = {**(ev.facts or {}), "story": {**sig, "at": now.isoformat()}}


@dataclass
class Covered:
    sig: dict[str, Any]
    at: datetime
    event_id: int | None


def coverage(db: Any, now: datetime, hours: int = 72) -> list[Covered]:
    """What the channel carried lately, newest first (released events: both platforms deliver what's released)."""
    from sqlalchemy import select

    from app.models.newsdesk import NdEvent

    rows = db.scalars(select(NdEvent).where(NdEvent.status == "released", NdEvent.released_at >= now - timedelta(hours=hours)).order_by(NdEvent.released_at.desc()))
    out = []
    for e in rows:
        at = e.released_at if e.released_at.tzinfo else e.released_at.replace(tzinfo=now.tzinfo)
        out.append(Covered(signature(e), at, e.id))
    return out


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    same_as: int | None = None


def judge(sig: dict[str, Any], covered: list[Covered], now: datetime, *, is_update: bool = False) -> Verdict:
    """Is this new, or a story the channel already carried with nothing materially new?"""
    mine = set(sig.get("subjects") or [])
    if not mine:
        return Verdict(True)
    for c in covered:
        old = c.sig
        if sig.get("event") is not None and old.get("event") == sig.get("event"):
            continue
        theirs = set(old.get("subjects") or [])
        if not mine & theirs:
            continue
        a, b = sig.get("type"), old.get("type")
        window = WINDOW.get(a, NEWS_WINDOW) if a == b else NEWS_WINDOW
        age = now - c.at
        if age > window:
            continue
        shared = ", ".join(sorted(mine & theirs))
        hours = max(1, int(age.total_seconds() // 3600))
        if a == "market" and b == "market":
            if sig.get("direction") and old.get("direction") and sig["direction"] != old["direction"]:
                continue  # a reversal is a different story
            if sig.get("cause") and not old.get("cause"):
                return Verdict(True, f"a verified cause for the {shared} move shown {hours} h ago")
            new, before = float(sig.get("magnitude") or 0), float(old.get("magnitude") or 0)
            cls = sig.get("class") or "stock"
            if before and new >= before * ESCALATION and tier(cls, new) > tier(cls, before):
                return Verdict(True, f"the {shared} move escalated from {before:g}% to {new:g}%")
            return Verdict(False, f"same story as #{c.event_id} {hours} h ago ({shared} {old.get('direction') or ''} {before:g}%): "
                                  f"{new:g}% is not a material change", c.event_id)
        if a == b or {a, b} <= {"incident", "controversy"}:
            if is_update:
                return Verdict(True, "a development of a story already out")
            return Verdict(False, f"same story as #{c.event_id} {hours} h ago ({shared}, {b})", c.event_id)
    return Verdict(True)


# --- importance ----------------------------------------------------------------------------------------------------

_rx = lambda p: re.compile(p, re.I)  # noqa: E731
GEO_EVENT = _rx(r"\b(wars?|missiles?|air ?strikes?|strikes? on|attack(?:s|ed)?|invasion|invade\w*|ceasefire|truce|peace (?:talks|deal|plan|hopes)|nuclear|troops|"
                r"drones?|shot down|downs? missiles|blockade|embargo|coup|annex\w*)\b")
SANCTIONS = _rx(r"\b(sanction\w*|export controls?|tariffs?)\b")
MEGA = _rx(r"\b(nvidia|apple|microsoft|google|alphabet|amazon|meta|tesla|openai|anthropic|jpmorgan|goldman sachs|blackrock|binance|coinbase|tether|circle|"
           r"strategy|microstrategy|robinhood|berkshire|saudi aramco|aramco|samsung|tsmc|intel|boeing|visa|mastercard|paypal)\b")
CORP_SHOCK = _rx(r"\b(investigat\w*|probe\w*|fbi|doj|justice department|subpoena\w*|scandal|spy|espionage|resign\w*|steps? down|ousted|fired|indict\w*|charged|"
                 r"antitrust|bankrupt\w*|ipo|acquir\w+|merger|takeover|break ?up|layoffs?)\b")
CORP_BIG = _rx(r"\b(investigat\w*|probe\w*|fbi|doj|justice department|scandal|spy|espionage|resign\w*|steps? down|ousted|fired|indict\w*|charged|bankrupt\w*)\b")
REG_BODY = _rx(r"\b(sec|cftc|occ|fdic|federal reserve|ecb|treasury|congress|senate|house|supreme court|european commission|esma|fca|white house)\b")
REG_EVENT = _rx(r"\b(approv\w*|charter|bans?|banned|rules?|ruling|framework|sues|sued|lawsuit|crackdown|deadline|bill|law|executive order|licen[cs]e\w*|"
                r"exemption|enforcement|subpoena\w*)\b")
FIN_CONTEXT = _rx(r"\b(crypto\w*|bitcoin|ether\w*|stablecoins?|tokeni[sz]\w*|banks?|banking|exchanges?|etfs?|securities|markets?|trading|prediction markets?)\b")
INSTITUTION = _rx(r"\b(nyse|nasdaq|cme|blackrock|fidelity|jpmorgan|goldman sachs|morgan stanley|citi(?:group)?|bank of america|state street|visa|mastercard|paypal|"
                  r"dtcc|ice|vanguard|charles schwab|hsbc|ubs|deutsche bank)\b")
RAILS = _rx(r"\b(tokeni[sz]\w*|bitcoin|crypto\w*|blockchain|stablecoins?|on-?chain|settle\w*)\b")
FLOWS = _rx(r"\betfs?\b.*\b(outflows?|inflows?|shed|pulled|withdrew|record)\b|\b(outflows?|inflows?)\b.*\betfs?\b")


@dataclass
class Significance:
    kind: str  # geopolitics | corporate | regulation | structure | flows
    points: float
    why: str


LIVE = re.compile(r"\blive\s*:|\blive (?:updates|blog)\b|\bas it happened\b", re.I)


def significance(title: str, summary: str = "") -> Significance | None:
    """Why a story that isn't market chaos still matters to our readers, or None. Read from the outlet's headline only. A live blog's
    rolling headline ("Iran war live: ...") is not a story."""
    t = title or ""
    if LIVE.search(t):
        return None
    countries = countries_in(t)
    if GEO_EVENT.search(t) and countries:
        return Significance("geopolitics", 30.0, f"geopolitics: {GEO_EVENT.search(t).group(0).lower()} ({', '.join(sorted(countries))})")
    if SANCTIONS.search(t) and countries:
        return Significance("geopolitics", 24.0, f"sanctions ({', '.join(sorted(countries))})")
    if MEGA.search(t) and CORP_SHOCK.search(t):
        big = CORP_BIG.search(t) is not None
        return Significance("corporate", 28.0 if big else 20.0, f"{MEGA.search(t).group(0)}: {CORP_SHOCK.search(t).group(0).lower()}")
    usd = brief._amount_usd(t)
    if FLOWS.search(t) and usd >= 5e8:
        return Significance("flows", 28.0 if usd >= 1e9 else 24.0, f"ETF flows of ${usd / 1e9:.1f}B")
    if INSTITUTION.search(t) and RAILS.search(t):
        return Significance("structure", 24.0, f"{INSTITUTION.search(t).group(0)} on crypto rails")
    if REG_BODY.search(t) and REG_EVENT.search(t) and FIN_CONTEXT.search(t):
        return Significance("regulation", 24.0, f"{REG_BODY.search(t).group(0)}: {REG_EVENT.search(t).group(0).lower()}")
    return None


OVERRIDABLE = ("not market chaos", "not about a market", "not enough market impact", "routine (",
               # held for want of a second outlet: importance finds the second outlet by names, not wording (it must still exist)
               "a scam, fraud or allegation needs", "one outlet, and it doesn't say")


def admissible(why: str) -> bool:
    """Which chaos-filter rejections importance may overturn. Never rumours, opinion, promotion, reassurance, or contradicted prices."""
    return any(why.startswith(x) for x in OVERRIDABLE)


def corroborating(story: Any, rows: list[Any], hours: float = 12) -> list[Any]:
    """Other outlets carrying the same story: two shared names, or one shared name and close wording, within `hours`. Looser than the
    fingerprint match the chaos filter uses, because outlets word the same story differently ("NYSE picks Avalanche ..." / "NYSE to
    settle tokenized stocks on Avalanche")."""
    mine = _who(story.title)
    at = _naive_utc(story.published_at)
    out = []
    for o in rows:
        if o.id == story.id or o.source == story.source or o.published_at is None or at is None:
            continue
        if abs((_naive_utc(o.published_at) - at).total_seconds()) > hours * 3600:
            continue
        shared = mine & _who(o.title)
        near = checks.similarity(checks.fingerprint(story.title), checks.fingerprint(o.title))
        if len(shared) >= 2 or (shared and near >= 0.15):
            out.append(o)
    return out


def _who(title: str) -> set[str]:
    """Names and countries, each counted once ("Saudi" the word and SAUDI the country are one)."""
    countries = countries_in(title)
    names = {n for n in names_in(title) if not any(COUNTRIES[c].search(n) for c in countries)}
    return names | countries


def _naive_utc(at: datetime | None) -> datetime | None:
    if at is None or at.tzinfo is None:
        return at
    return at.astimezone(timezone.utc).replace(tzinfo=None)


def importance_score(sig: Significance, *, evidence: str, outlets: int, outlets_3h: int, age_minutes: float, title: str) -> Any:
    """0-100, built like virality's score so the two rank together: significance (instead of chaos severity), reach, evidence, spread.
    Spread counts every outlet carrying it (2.5 each, up to 10): a world story is told over a day, not within three hours."""
    from app.newsdesk import virality

    reach = 12 if sig.kind == "geopolitics" else 12 if MEGA.search(title) or INSTITUTION.search(title) else 10 if assets_in(title) & {"BTC", "ETH", "OIL"} else 6
    ev = virality.EVIDENCE.get(evidence, 0) + (min(outlets - 1, 2) * 1.5 if evidence == "two outlets" and outlets > 1 else 0)
    parts = {"significance": sig.points, "reach": float(min(15, reach)), "evidence": float(min(15, ev)), "spread": float(min(10, 2.5 * max(outlets, outlets_3h)))}
    return virality._finish(parts, age_minutes)


# --- market moves on their own ---------------------------------------------------------------------------------------

LIQ_EXCEPTIONAL_USD = 100_000_000  # an hour's liquidations worth a post on their own: $8M and $17M flushes are routine


def exceptional(ev: Any) -> tuple[bool, str]:
    """A market event (a measured move, a level, a liquidation flush) takes a slot only when exceptional."""
    f = ev.facts or {}
    if ev.topic == "liquidations":
        usd = float(f.get("usd") or 0)
        return (usd >= LIQ_EXCEPTIONAL_USD, f"${usd / 1e6:.0f}M of liquidations in an hour (needs ${LIQ_EXCEPTIONAL_USD / 1e6:.0f}M)")
    kind = f.get("kind")
    ratio = float(f.get("ratio") or 0)
    if kind in ("move", "wide"):
        need = 1.0 if f.get("window") == "24h" else 1.5  # a short-window spike must clear its line by half again: 1-hour wiggles aren't news
        return (ratio >= need, f"{f.get('change_pct')}% in {f.get('window')} is {ratio:.2f}x its alert line (needs {need:g}x)")
    if kind == "milestone":
        round_level = float((ev.parts or {}).get("magnitude") or 0) >= 20
        return (round_level, "a round-number level" if round_level else "a level that isn't a round number")
    if kind in ("high", "low"):
        major = set(f.get("assets") or []) & {"BTC", "ETH", "SPX", "IXIC", "GOLD", "OIL"}
        return (bool(major), "a 52-week extreme of a major market" if major else "a 52-week extreme of a minor asset")
    return (False, "not a measured move")


def rank_bonus(ev: Any) -> float:
    """Ranking only (never the bar): verified important news first, then chaos news, then exceptional moves."""
    f = ev.facts or {}
    if f.get("importance"):
        return 4.0
    if ev.kind == "market":
        return -6.0
    return 0.0


# what makes a follow-up worth a post of its own: something happened. Attribution ("North Korea", "Lazarus"), laundering, outflows and
# reactions are context: "hackers launder Binance loot via memecoins" after "North Korean hackers wash Binance loot through memecoins" is the
# same story told twice (2026-11-09, 16:00 and 20:00 UTC in the replay).
STRONG_DEVELOPMENT = re.compile(r"\b(arrest\w*|charged|indict\w*|sentenc\w*|guilty|bankrupt\w*|insolven\w*|(?:halt|paus|suspend|freez)\w* (?:all )?withdrawals|"
                                r"(?:reopen|resum)\w* withdrawals|recover\w*|returns? (?:the )?funds|seiz\w+|frozen|delist\w*|shut(?:s|ting)? down|collaps\w*|"
                                r"steps? down|stepped down|resign\w*|ousted|fired|depeg\w*|confirms?|confirmed|fbi|doj|justice department|criminal)\b", re.I)


def material_development(development: str) -> bool:
    """An update's development (events.development) is material: a bigger figure, an official source, or a strong event word."""
    d = development or ""
    return d in ("a bigger figure", "an official source") or bool(STRONG_DEVELOPMENT.search(d))


def escalation_note(before: float, after: float) -> str:
    return f"{before:g}% -> {after:g}% (x{after / before:.2f})" if before else f"{after:g}%"


__all__ = ["Covered", "Significance", "Verdict", "admissible", "canon", "corroborating", "coverage", "exceptional", "exceptional_pct", "importance_score",
           "judge", "rank_bonus", "signature", "significance", "stamp", "tier"]
