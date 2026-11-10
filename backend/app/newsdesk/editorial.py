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
ESCALATION = 2.0  # a repeat move inside 24 h must be at least twice the size already posted, into a higher tier AND a major one (MAJOR_TIER)
MAJOR_TIER = 2  # e.g. oil 7.5%+, Bitcoin 12%+, the S&P 4%+ in 24 h (owner, 2026-11-10: stop treating every change in oil's 24h % as a story)


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
    out = {"subjects": sorted(subjects), "type": typ, "direction": direction, "magnitude": round(abs(pct), 2), "class": asset_class(subjects),
           "cause": cause, "event": ev.id, "headline": (ev.headline or "")[:140]}
    out["desk"] = desk_of(ev, out)
    return out


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
        if e.tg_status in NOT_OUT and e.x_status in NOT_OUT:
            continue  # released but never went out anywhere (refused as a repeat, blocked, missed): it covered nothing
        at = e.released_at if e.released_at.tzinfo else e.released_at.replace(tzinfo=now.tzinfo)
        out.append(Covered(signature(e), at, e.id))
    return out


NOT_OUT = ("skipped", "missed", "failed")


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
        if a == "market" and b != "market" and age <= WINDOW["market"]:
            # a bare price move on something the channel covered as news today: the move is that story's echo, not news
            return Verdict(False, f"{shared} was already covered {hours} h ago (#{c.event_id}, {b}): a price move on it isn't a new story", c.event_id)
        if a == "market" and b == "market":
            # 24 h hard block (owner, 2026-11-10): a bigger or opposite number on the same asset is the same story. Only a verified cause (a
            # sourced news story explaining a move shown only as a number) or a major escalation gets through.
            if sig.get("cause") and not old.get("cause"):
                return Verdict(True, f"a verified cause for the {shared} move shown {hours} h ago")
            new, before = float(sig.get("magnitude") or 0), float(old.get("magnitude") or 0)
            cls = sig.get("class") or "stock"
            if before and new >= before * ESCALATION and tier(cls, new) > tier(cls, before) and tier(cls, new) >= MAJOR_TIER:
                return Verdict(True, f"the {shared} move escalated from {before:g}% to {new:g}%, a major move")
            return Verdict(False, f"same story as #{c.event_id} {hours} h ago ({shared} {old.get('direction') or ''} {before:g}%): "
                                  f"{new:g}% is not a material change", c.event_id)
        if a == b or {a, b} <= {"incident", "controversy"}:
            if is_update:
                return Verdict(True, "a development of a story already out")
            return Verdict(False, f"same story as #{c.event_id} {hours} h ago ({shared}, {b})", c.event_id)
    return Verdict(True)


# The rule as Admin states it (owner, 2026-11-10: "the same asset/event blocked 24 h unless a genuinely major new development").
RULE = ("One story is posted once, on Telegram and on X alike. The same asset or event is blocked for 24 hours (market moves), 72 hours "
        "(hacks, failures, scams) or 48 hours (other news) unless something materially new happens: a move at least twice the size "
        "already posted AND into a major severity tier, a verified cause for a move shown only as a number, or a "
        "material development (an arrest, charges, the FBI or DOJ, withdrawals halted or resumed, funds recovered, a resignation, an "
        "official confirmation, a bigger figure). A development must be a fact the outlet reports, new against everything already "
        "told about the story: never a maybe (\"could\", \"reportedly\", \"set to\", a question).")


def repeat_verdict(ev: Any, covered: list[Covered], now: datetime) -> Verdict:
    """judge() for an event, without changing it. An update counts as new only with a material development."""
    material = ev.kind == "update" and material_development(str((ev.facts or {}).get("development") or ""))
    return judge(signature(ev), covered, now, is_update=material)


def covered_before(db: Any, ev: Any, now: datetime, hours: int = 72) -> list[Covered]:
    """What was out before this event was released (an event is never its own repeat, nor one of a later post's)."""
    at = ev.released_at
    at = at if at is None or at.tzinfo else at.replace(tzinfo=now.tzinfo)
    return [c for c in coverage(db, now, hours) if c.event_id != ev.id and (at is None or c.at < at)]


def delivery_gate(db: Any, ev: Any, now: datetime) -> str:
    """Asked by each platform before it delivers a released event: "" to go ahead, or why not. The owner's block, and the story history:
    X follows Telegram, but never posts what the history says is a repeat, whatever Telegram did."""
    f = ev.facts or {}
    if (f.get("blocked") or {}).get("on"):
        return f"blocked by the owner{': ' + str(f['blocked'].get('reason')) if f['blocked'].get('reason') else ''}"
    v = repeat_verdict(ev, covered_before(db, ev, now), now)
    return "" if v.ok else f"repeat: {v.reason}"


def record_skip(db: Any, now: datetime, entry: dict[str, Any]) -> None:
    """A candidate the desk turned away as a repeat without making an event of it (the no-story market check): kept in the desk's state
    (last 100) for the story history, so both platforms' record shows it."""
    from app.models.newsdesk import NdSettings

    row = db.get(NdSettings, 1)
    if row is None:
        return
    skips = list((row.state or {}).get("story_skips") or [])
    key = (entry.get("slot"), entry.get("subject"))
    if any((x.get("slot"), x.get("subject")) == key for x in skips):
        return
    row.state = {**(row.state or {}), "story_skips": [*skips, {**entry, "at": now.isoformat()}][-100:]}


def related(sig: dict[str, Any], old: dict[str, Any], age: timedelta) -> bool:
    """The same story for the history view (whether or not it was allowed out again)."""
    if not set(sig.get("subjects") or []) & set(old.get("subjects") or []):
        return False
    a, b = sig.get("type"), old.get("type")
    if not (a == b or {a, b} <= {"incident", "controversy"}):
        return False
    return age <= (WINDOW.get(a, NEWS_WINDOW) if a == b else NEWS_WINDOW)


def history(db: Any, now: datetime, hours: int = 72) -> list[dict[str, Any]]:
    """Every story of the last `hours`: what it is about, first and last coverage, the posts made (with each platform's delivery), and the
    later candidates merged into it or skipped, with why. Newest story first."""
    from sqlalchemy import select

    from app.models.newsdesk import NdEvent, NdSettings

    def aware(at: Any) -> datetime:
        return at if at.tzinfo else at.replace(tzinfo=now.tzinfo)

    rows = list(db.scalars(select(NdEvent).where(NdEvent.status.in_(("released", "superseded")), NdEvent.created_at >= now - timedelta(hours=hours + 24))))
    released = sorted((e for e in rows if e.status == "released" and e.released_at and aware(e.released_at) >= now - timedelta(hours=hours)),
                      key=lambda e: aware(e.released_at))
    groups: list[dict[str, Any]] = []
    by_event: dict[int, dict[str, Any]] = {}
    for e in released:
        sig, at = signature(e), aware(e.released_at)
        g = next((g for g in reversed(groups) if related(sig, g["sig"], at - g["last_at"])), None)
        if g is None:
            g = {"sig": sig, "first_at": at, "last_at": at, "posts": [], "merged": []}
            groups.append(g)
        g["last_at"] = at
        g["posts"].append({"event": e.id, "at": at.isoformat(), "kind": e.kind, "headline": e.headline, "telegram": e.tg_status, "x": e.x_status,
                           "why": (e.facts or {}).get("new_because") or ("first post" if len(g["posts"]) == 0 else "")})
        by_event[e.id] = g
    for e in rows:
        if e.status != "superseded":
            continue
        g = by_event.get(int((e.facts or {}).get("merged_into") or 0))
        if g is None:
            sig = signature(e)
            g = next((g for g in groups if related(sig, g["sig"], abs(aware(e.created_at) - g["last_at"]))), None)
        if g is not None:
            g["merged"].append({"event": e.id, "at": aware(e.updated_at or e.created_at).isoformat(), "headline": e.headline, "why": e.why})
    row = db.get(NdSettings, 1)
    for x in ((row.state or {}).get("story_skips") or []) if row is not None else []:
        g = by_event.get(int(x.get("same_as") or 0))
        if g is not None:
            g["merged"].append({"event": None, "at": x.get("at"), "headline": x.get("headline", ""), "why": x.get("reason", "")})
    for e in released:  # each platform's own refusals (the delivery gate) are part of the record too
        for platform, status, note in (("Telegram", e.tg_status, e.tg_note), ("X", e.x_status, e.x_note)):
            if status == "skipped" and note and (note.startswith("repeat") or note.startswith("blocked")):
                by_event[e.id]["merged"].append({"event": e.id, "at": None, "headline": e.headline, "why": f"{platform}: {note}"})
    out = []
    for g in reversed(groups):
        sig = g["sig"]
        out.append({"subjects": sig.get("subjects"), "type": sig.get("type"), "direction": sig.get("direction") or "", "topic": g["posts"][0]["headline"][:90],
                    "first_coverage": g["first_at"].isoformat(), "last_coverage": g["last_at"].isoformat(), "posts_made": len(g["posts"]),
                    "posts": g["posts"], "merged_or_skipped": sorted(g["merged"], key=lambda m: m["at"] or "")})
    return out


def _sources_mod() -> Any:
    from app.newsdesk import sources

    return sources


STATUS_WORD = {"released": "selected", "superseded": "merged (repeat)", "rejected": "rejected", "missed": "expired", "candidate": "waiting"}


def decisions(db: Any, now: datetime, hours: int = 24, limit: int = 150) -> list[dict[str, Any]]:
    """Admin's record of why each candidate was selected or rejected (newest first)."""
    from sqlalchemy import select

    from app.models.newsdesk import NdEvent

    rows = db.scalars(select(NdEvent).where(NdEvent.created_at >= now - timedelta(hours=hours)).order_by(NdEvent.id.desc()).limit(limit))
    out = []
    for e in rows:
        f = e.facts or {}
        out.append({"event": e.id, "at": (e.released_at or e.updated_at or e.created_at).isoformat() if (e.released_at or e.updated_at or e.created_at) else None,
                    "decision": STATUS_WORD.get(e.status, e.status), "kind": e.kind, "desk": desk_of(e), "headline": e.headline or e.title or "",
                    "score": float(e.virality or 0), "evidence": e.evidence or "", "sources": len(e.sources or []),
                    "outlets": sorted({str(x.get("name") or "") for x in e.sources or []} - {""}),
                    "via_worldmonitor": sorted({str(x.get("name") or "") for x in e.sources or []
                                                if _sources_mod().via(str(x.get("name") or ""))}),
                    "development": f.get("development_source") or None,
                    "why": e.why or "", "new_because": f.get("new_because") or "", "telegram": e.tg_status, "x": e.x_status})
    return out


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


# official economic data that moves every market, when the headline says it moved: "China's imports grow at the fastest pace in five years"
MACRO_DATA = _rx(r"\b(inflation|cpi|ppi|consumer prices|wholesale (?:prices|inflation)|producer prices|jobs report|payrolls|unemployment|jobless|gdp|"
                 r"imports|exports|trade (?:surplus|deficit)|retail sales|industrial output|factory (?:activity|output)|pmi)\b")
MACRO_MOVE = _rx(r"\b(highest|lowest|fastest|slowest|biggest|record|surg\w+|jump\w*|plung\w+|soar\w*|slump\w*|tops?|crosses|unexpected\w*|"
                 r"hotter|cooler|beats?|miss(?:es|ed)?|contract\w*|shr[ia]nk\w*)\b")
BIG_ECONOMY = _rx(r"\b(u\.?s\.?|us|american|euro ?zone|german\w*|britain|uk|japan\w*)\b")


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
    if MACRO_DATA.search(t) and (countries_in(t) or BIG_ECONOMY.search(t)) and MACRO_MOVE.search(t):
        return Significance("macro", 24.0, f"macro data: {MACRO_DATA.search(t).group(0).lower()}")
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


# --- feed diversity (owner, 2026-11-10: one asset or market must not dominate) --------------------------------------------------------
DESK = {"crypto_major": "crypto", "crypto_alt": "crypto", "index": "stocks", "commodity": "commodities"}
TOPIC_DESK = {"macro": "macro", "geopolitics": "geopolitics", "regulation": "crypto", "hack": "crypto", "scam": "crypto", "failure": "crypto",
              "etf": "crypto", "flows": "crypto", "liquidations": "crypto", "whale": "crypto"}
CATEGORY_DESK = {"economics": "macro", "politics": "geopolitics", "commodities": "commodities", "stocks": "stocks", "crypto": "crypto"}
DIVERSITY_PENALTY = 5.0  # ranking points per post from the same desk in the last 24 h
RUN_LIMIT = 2  # never more than this many posts in a row from one desk


def desk_of(ev: Any, sig: dict[str, Any] | None = None) -> str:
    """Which part of the world a post is about: crypto, stocks, commodities, macro, geopolitics or other."""
    sig = sig or signature(ev)
    for s in sig.get("subjects") or []:
        if s in CLASS:
            return DESK[CLASS[s]]
    topic, cat = getattr(ev, "topic", None) or "", getattr(ev, "category", None) or ""
    return TOPIC_DESK.get(topic) or CATEGORY_DESK.get(cat) or "other"


def diversity(ev: Any, covered: list[Covered], now: datetime) -> tuple[float, str]:
    """(ranking penalty, hard-block reason or ""). covered is newest first. Material updates of a story already out are exempt."""
    if getattr(ev, "kind", "") == "update":
        return 0.0, ""
    desk = desk_of(ev)
    if desk == "other":
        return 0.0, ""
    def desk_c(c: Covered) -> str:
        return c.sig.get("desk") or next((DESK[CLASS[s]] for s in c.sig.get("subjects") or [] if s in CLASS), "") or "other"
    day = [c for c in covered if now - c.at <= timedelta(hours=24)]
    same = sum(1 for c in day if desk_c(c) == desk)
    run = 0
    for c in covered:
        if desk_c(c) != desk:
            break
        run += 1
    if run >= RUN_LIMIT:
        return same * DIVERSITY_PENALTY, f"the last {run} posts were all {desk}: feed diversity holds a third in a row"
    return same * DIVERSITY_PENALTY, ""


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


# Breaking between slots (owner, 2026-11-10): clear financial, market or major geopolitical significance only. A hard geopolitical event
# (strikes, invasion, a ceasefire, a blockade, sanctions), never talk about one; and never routine political controversy.
GEO_HARD = _rx(r"\b(missiles?|air ?strikes?|strikes? on|struck|attack(?:s|ed)? on|invasion|invade\w*|ceasefire|truce|peace deal (?:signed|agreed|reached)|"
               r"declares? war|war (?:breaks out|begins)|nuclear (?:test|strike|attack)|troops (?:enter|cross|deploy\w*)|shot down|blockade|embargo|"
               r"coup|annex\w*|closes? the strait|strait of hormuz)\b")
POLITICAL_ROUTINE = _rx(r"\b(impeach\w*|decr(?:y|ies|ied)|defends?|defended|slams?|blasts?|criticis\w*|criticiz\w*|rebuk\w*|feud|spat|row over|mocks?|"
                        r"lashes out|hits back|fires back|campaign\w*|poll\w*|rally|hearing|testif\w*|approval rating|opinion|analysis|"
                        r"explainer|what to know)\b")
# talk about a geopolitical event, not the event: "Trump says Iran war to end 'fairly soon'", "Ukraine talks face an uphill battle"
TALK = _rx(r"\b(says?|said|calls? for|urges?|vows?|hopes?|sees|faces?|questions remain|uphill|warns?|threatens?|signals?|weighs?)\b")
FINANCIAL_KINDS = {"corporate", "flows", "structure", "macro", "regulation"}
FINANCIAL_TOPICS = {"hack", "failure", "scam", "etf", "flows", "macro"}


def breaking_worthy(title: str, topic: str = "", importance: dict[str, Any] | None = None) -> tuple[bool, str]:
    """May this story break between slots? (True, what makes it) or (False, why not)."""
    t = title or ""
    if POLITICAL_ROUTINE.search(t) or topic == "controversy":
        return False, "routine political controversy or commentary"
    kind = str((importance or {}).get("kind") or "")
    if kind == "geopolitics" or topic == "geopolitics":
        if (GEO_HARD.search(t) or (SANCTIONS.search(t) and countries_in(t))) and not TALK.search(t):
            return True, "major geopolitical event"
        return False, "geopolitics, but no hard event (talk, positions, reactions)"
    if kind == "corporate":
        return (True, "major corporate shock") if CORP_BIG.search(t) else (False, "corporate news, not a shock")
    if kind in FINANCIAL_KINDS or topic in FINANCIAL_TOPICS:
        return True, "financial or market significance"
    if topic in ("hack", "failure", "scam", "structure", "regulation", "corporate"):
        return True, "financial or market significance"
    return False, "no clear financial, market or major geopolitical significance"


def material_development(development: str) -> bool:
    """An update's development (events.development) is material: a bigger figure, an official source, or a strong event word."""
    d = development or ""
    return d in ("a bigger figure", "an official source") or bool(STRONG_DEVELOPMENT.search(d))


def escalation_note(before: float, after: float) -> str:
    return f"{before:g}% -> {after:g}% (x{after / before:.2f})" if before else f"{after:g}%"


__all__ = ["Covered", "Significance", "Verdict", "admissible", "breaking_worthy", "canon", "corroborating", "coverage", "exceptional", "exceptional_pct", "importance_score",
           "desk_of", "diversity", "judge", "rank_bonus", "signature", "significance", "stamp", "tier"]
