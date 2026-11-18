"""Editorial judgment shared by every publishing lane: News Desk slots and breaking, Telegram, X, Trending and the Terminal's fast lane
(owner, 2026-11-18: "I do not want more news. I want better news").

Four separate questions, never folded into one score:
  verified       is it true? (the callers' evidence rules: outlets, officials, rumour checks; unchanged here)
  why it matters a concrete, source-stated consequence: deaths, money, a market move of real size, a state striking another state, a
                 nationwide or global disruption while it lasts, a hard corporate outcome, a central-bank decision. Several outlets
                 repeating a story does NOT make it matter, and dramatic words without one of these facts count for nothing.
  new            the underlying real-world event (who, where, what kind of event) has not gone out on any lane in the last days, or this is
                 a substantial new development of it (more deaths, a new country struck, a move or sum twice the size, a hard outcome).
  stop-scroll    would a reader who follows markets and geopolitics stop for it: consequence, novelty, urgency, relevance.

Production 2026-11-18 (the posts that made the owner write this): Telegram 190 "Nvidia stock up 3% as earnings beat" (a 3% move: nothing
happened), 191 "FAA signed off on repairs before ground stop" + "Duffy pitches $50B for FAA" (the fourth post about one ground stop, and
two developments in one post), 192 "ADNOC ship attacked in Hormuz as maritime security risks rise" (another round of the Gulf conflict
with no new country, no deaths, no market figure), X 765 "Democrats probe whether Lutnick helped Cantor avoid Nvidia losses" (a political
inquiry into a maybe). Every one of them clears this module's questions only with something new and concrete; none of them did."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.newsdesk import checks

# --- the event: what happened, to whom, where ------------------------------------------------------------------------------------------


def _rx(p: str) -> re.Pattern[str]:
    return re.compile(p, re.I)


# event families, most specific first: a headline belongs to the first that matches ("Duffy pitches $50B for FAA after ground stop" is the
# ground stop's story, not a budget story)
FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    ("conflict", _rx(r"\b(air ?strikes?|strikes? (?:on|at|against)|struck|missiles?|drones?|bomb\w*|shell(?:ed|ing)|troops|invasion|invad\w*|"
                     r"ceasefire|truce|(?<!pre-)(?<!post-)\bwar\b|(?:ship|tanker|vessel|carrier|base|embassy)s? (?:\w+ )?(?:attack\w*|hit|seized|struck|targeted)|"
                     r"attack\w* (?:on|against) (?:a |an |the )?(?:\w+ )?(?:ship|tanker|vessel|base|embassy|city|port)|"
                     r"(?:iran|israel|russia|ukraine|us|u\.s\.)\w* (?:attack|strike)s?|launch\w* (?:strikes|attacks|missiles|drones))")),
    ("disaster", _rx(r"\b(earthquake|quake|tsunami|hurricane|typhoon|cyclone|flood\w*|wildfire|eruption|plane crash|crash(?:ed)? (?:kills|killing)|"
                     r"derail\w*|explosion|collapse[sd]? (?:kills|killing))\b")),
    ("hack", _rx(r"\b(hack\w*|exploit\w*|drain\w*|stolen|heist|breach\w*)\b")),
    ("aviation", _rx(r"\b(ground stop|grounded|groundings|flights? (?:cancel\w*|delay\w*|halted)|air traffic control|airspace (?:closed|closure))\b")),
    ("outage", _rx(r"\b(outages?|went down|is down|are down|down for|offline|service disruption|system failure|restored)\b")),
    ("earnings", _rx(r"\b(earnings|quarterly (?:results|revenue|profit)|revenue|guidance|profit warning|results beat|beats? (?:estimates|expectations))\b")),
    ("rates", _rx(r"\b(rate (?:cut|hike|decision)|cuts? rates|raises? rates|hikes? rates|holds? rates|interest rates?|fomc)\b")),
    ("legal", _rx(r"\b(probe\w*|investigat\w*|lawsuit|sues|sued|charged|indict\w*|subpoena\w*|scandal|testif\w*|hearing|arrest\w*|convict\w*|sentenc\w*|"
                  r"spy|espionage)\b")),
    ("deal", _rx(r"\b(merger|merge|acquisition|acquire\w*|takeover|buyout|ipo|to buy|deal to)\b")),
    ("policy", _rx(r"\b(tariffs?|sanction\w*|export controls?|bill|law|executive order|budget|funding|stimulus|regulat\w*|ban\w*)\b")),
    ("market", _rx(r"\b(rall(?:y|ies)|slump\w*|plung\w*|tumbl\w*|surg\w*|soar\w*|sell-?off|falls?|drops?|rises?|jumps?|slides?|crash\w*|record high|all-time high|"
                   r"breaks? (?:below|above)|liquidat\w*|holds? near)\b")),
]
# families that are one running story when they share actors and place: an attack on a tanker is part of the war that caused it
KIN = {"conflict": "conflict", "aviation": "disruption", "outage": "disruption"}

REGIONS: dict[str, re.Pattern[str]] = {
    "GULF": _rx(r"\b(iran\w*|tehran|irgc|bahrain\w*|kuwait\w*|qatar\w*|doha|uae|emirat\w*|abu dhabi|dubai|saudi\w*|riyadh|oman\w*|hormuz|"
                r"persian gulf|gulf|qeshm|adnoc|aramco|iraq\w*|baghdad)\b"),
    "LEVANT": _rx(r"\b(israel\w*|gaza|hamas|hezbollah|lebanon|lebanese|beirut|west bank|syria\w*|damascus|idf|rafah|khan younis)\b"),
    "UKRAINE_WAR": _rx(r"\b(ukrain\w*|kyiv|kharkiv|odesa|russia\w*|kremlin|moscow|crimea|donbas|zelensk\w*|putin)\b"),
    "RED_SEA": _rx(r"\b(red sea|houthis?|yemen\w*|bab el-mandeb|aden)\b"),
    "TAIWAN_STRAIT": _rx(r"\b(taiwan\w*|taipei|south china sea|pla navy)\b"),
    "KOREA": _rx(r"\b(north korea\w*|pyongyang|south korea\w*|seoul)\b"),
}
STATES = {  # countries that act or are struck, for "a new country is involved" (escalation)
    "US": _rx(r"\b(u\.s\.|us|usa|united states|american|pentagon|washington|centcom|white house)\b"), "IRAN": _rx(r"\b(iran\w*|tehran|irgc)\b"),
    "ISRAEL": _rx(r"\b(israel\w*|idf)\b"), "RUSSIA": _rx(r"\b(russia\w*|kremlin|moscow)\b"), "UKRAINE": _rx(r"\b(ukrain\w*|kyiv)\b"),
    "BAHRAIN": _rx(r"\bbahrain\w*\b"), "KUWAIT": _rx(r"\bkuwait\w*\b"), "QATAR": _rx(r"\bqatar\w*\b"), "UAE": _rx(r"\b(uae|emirat\w*|abu dhabi)\b"),
    "SAUDI": _rx(r"\b(saudi\w*|riyadh)\b"), "IRAQ": _rx(r"\b(iraq\w*|baghdad)\b"), "SYRIA": _rx(r"\bsyria\w*\b"), "LEBANON": _rx(r"\b(lebanon|lebanese|hezbollah)\b"),
    "YEMEN": _rx(r"\b(yemen\w*|houthis?)\b"), "CHINA": _rx(r"\b(china|chinese|beijing)\b"), "TAIWAN": _rx(r"\btaiwan\w*\b"), "NKOREA": _rx(r"\b(north korea\w*|pyongyang)\b"),
    "OMAN": _rx(r"\boman\w*\b"), "JORDAN": _rx(r"\bjordan\w*\b"), "EGYPT": _rx(r"\begypt\w*\b"), "TURKEY": _rx(r"\b(turkey|turkish|ankara)\b"),
    "PAKISTAN": _rx(r"\bpakistan\w*\b"), "INDIA": _rx(r"\b(india|indian)\b"), "POLAND": _rx(r"\b(poland|polish)\b"), "NATO": _rx(r"\bnato\b"),
}
ACRONYM = re.compile(r"\b([A-Z][A-Z0-9&]{1,6})\b")
NOT_ANCHOR = {"US", "UK", "EU", "UN", "AI", "CEO", "CFO", "GOP", "THE", "AND", "FOR", "NEW", "IPO", "ETF", "ETFS", "Q1", "Q2", "Q3", "Q4", "USD", "GDP", "CPI",
              "BREAKING", "JUST", "IN", "UPDATE", "SEC", "DOJ", "FBI", "UTC", "AM", "PM", "TV", "S&P", "OK", "LIVE"}
WEAK_NAMES = {"report", "reports", "update", "latest", "breaking", "middle", "east", "senate", "house", "congress", "democrats", "republicans", "officials",
              "government", "president", "minister", "says", "after", "before", "amid", "over", "stock", "shares", "market", "markets", "price", "news",
              "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "nationwide", "global", "maritime", "security", "military"}


@dataclass
class Event:
    family: str
    anchors: set[str]  # specific actors and things: FAA, ADNOC, NVIDIA, HORMUZ (never "Senate", never a weekday)
    regions: set[str]
    states: set[str]
    deaths: int = 0
    money: float = 0.0  # largest dollar figure stated as having happened (a proposal or a maybe doesn't count), in $
    pct: float = 0.0  # largest percentage move stated

    def kin(self) -> str:
        return KIN.get(self.family, self.family)


def family_of(text: str) -> str:
    for name, rx in FAMILIES:
        if rx.search(text or ""):
            return name
    return "news"


def _shouting(text: str) -> bool:
    letters = [c for c in text or "" if c.isalpha()]
    return len(letters) > 12 and sum(c.isupper() for c in letters) / len(letters) > 0.7


def _ordinary(word: str) -> bool:
    try:
        from app.newsdesk import wire

        return bool(wire.ordinary_word(word.lower()))
    except Exception:  # noqa: BLE001 - without the word list, every capitalised word counts
        return False


SMALL = {"to", "of", "in", "on", "at", "by", "as", "is", "are", "for", "the", "and", "from", "over", "hot", "new", "ceo", "its", "it", "an", "a", "be"}


def anchors_of(text: str) -> set[str]:
    """The specific actors and things a headline is about. A shouted headline (ALL CAPS) is read in title case and only its short words
    can be acronyms; Title Case words that are only ordinary English ("Leaves", "Questions") never count; assets do (Ethereum = $ETH)."""
    from app.newsdesk import editorial

    raw = text or ""
    shout = _shouting(raw)
    if shout:
        out = {w for w in re.findall(r"\b[A-Z]{2,4}\b", raw) if w not in NOT_ANCHOR and w.lower() not in SMALL and w.lower() not in WEAK_NAMES
               and not _ordinary(w)}
    else:
        out = {a for a in ACRONYM.findall(raw) if a not in NOT_ANCHOR and not a.isdigit()}
    words = checks.names(raw.title() if shout else raw)
    # an asset's name is its ticker ("Bitcoin" is $BTC, counted once), never a second anchor
    out |= {n.upper() for n in words if n.lower() not in WEAK_NAMES and not n.isdigit() and len(n) >= 4 and not _ordinary(n) and not editorial.assets_in(n)}
    out |= {f"${a}" for a in editorial.assets_in(raw)}
    if re.search(r"\bhormuz\b", text or "", re.I):
        out.add("HORMUZ")
    # the event's own noun when it is specific: a ground stop, a ceasefire
    for phrase in ("ground stop", "ceasefire", "shutdown", "default", "bank run", "depeg"):
        if phrase in (text or "").lower():
            out.add(phrase.upper().replace(" ", "_"))
    return out - {s for s in STATES}  # a state is not an anchor: two Iran stories are not one story because both say Iran


MONEY = re.compile(r"\$\s?(\d+(?:[.,]\d+)?)\s*(trillion|tn|billion|bn|b|million|mn|m)\b", re.I)
PCT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b)")
DEATHS = re.compile(r"\b(?:kill(?:s|ed|ing)?|dead|deaths?|death toll (?:rises to|of|at)?)\s+(?:all |at least |more than |over |nearly |about |some )?(\d[\d,]*)|"
                    r"(\d[\d,]*)\s+(?:people\s+|civilians\s+|soldiers\s+|troops\s+)?(?:were\s+|are\s+)?(?:killed|dead|die[ds]?)\b", re.I)
NOT_HAPPENED = re.compile(r"\b(pitch\w*|propos\w*|asks?|asked|asking|seeks?|sought|seeking|plans?|planned|considers?|considering|could|may|might|weighs?|"
                          r"wants?|calls? for|urges?|request\w*|bid for|talks? (?:on|about|over)|whether)\b", re.I)


def _scale(n: str, unit: str) -> float:
    v = float(n.replace(",", ""))
    u = unit.lower()
    return v * (1e12 if u in ("trillion", "tn") else 1e9 if u in ("billion", "bn", "b") else 1e6)


def money_of(text: str) -> float:
    """The largest dollar figure stated as fact. "Duffy pitches $50 billion for the FAA" is a request, not money that moved."""
    best = 0.0
    for m in MONEY.finditer(text or ""):
        before = (text or "")[max(0, m.start() - 60):m.start()]
        if NOT_HAPPENED.search(before):
            continue
        best = max(best, _scale(m.group(1), m.group(2)))
    return best


def pct_of(text: str) -> float:
    return max((float(m.group(1)) for m in PCT.finditer(text or "")), default=0.0)


WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
         "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "a dozen": 12, "dozens": 24, "scores": 40, "hundreds": 200}


def deaths_of(text: str) -> int:
    t = text or ""
    for w, n in sorted(WORDS.items(), key=lambda x: -len(x[0])):
        t = re.sub(rf"\b{w}\b", str(n), t, flags=re.I)
    return max((int(g.replace(",", "")) for m in DEATHS.finditer(t) for g in m.groups() if g), default=0)


def event_of(title: str, summary: str = "") -> Event:
    """The underlying event of a story, from its headline (the summary only adds figures: a summary's background must not change the
    event's identity)."""
    head = title or ""
    text = f"{head} {summary or ''}"
    return Event(family=family_of(head), anchors=anchors_of(head), regions={k for k, rx in REGIONS.items() if rx.search(head)},
                 states={k for k, rx in STATES.items() if rx.search(head)}, deaths=deaths_of(text), money=money_of(head), pct=pct_of(head))


def same_event(a: Event, b: Event) -> bool:
    """One real-world event, told twice: the same kind of event (a tanker attack and a missile strike are both the war) with the same
    specific actor or place, or the same conflict in the same region. Never just a shared keyword: two Nvidia stories (its CEO's scandal,
    its earnings) are two events; two FAA stories about one ground stop are one."""
    if a.kin() == b.kin() == "conflict":
        return bool(a.regions & b.regions) or bool(a.anchors & b.anchors)
    shared = a.anchors & b.anchors
    if a.kin() == b.kin() == "market":
        return bool(shared)  # the same asset moving within the market window (24 h)
    specific = {x for x in shared if not x.startswith("$")}  # an asset is a topic, not an event
    if a.kin() == b.kin() and a.kin() != "news":
        # one big name in common is not one event: Nvidia's CEO scandal and a probe into who avoided Nvidia losses are two stories
        # (an incident is the exception: "Binance hack" and "loot from the Binance hack" are one hack)
        return bool(specific) and (len(specific) >= 2 or a.kin() in ("hack", "disruption", "disaster")
                                   or not all(MEGA.fullmatch(x.lower()) for x in specific))
    return len(specific) >= 2 and a.family != "market" and b.family != "market"


def development(new: Event, before: list[Event], text: str) -> str:
    """What is substantially new against everything already told about this event ("" = nothing): more deaths, a country newly drawn in,
    a move or a sum at least twice the size, or a hard outcome (arrest, charges, bankruptcy, a ceasefire agreed, a strait closed)."""
    deaths = max((e.deaths for e in before), default=0)
    if new.deaths >= 10 and new.deaths >= max(2 * deaths, deaths + 10):
        return f"the death toll is now {new.deaths} (was {deaths or 'not given'})"
    seen_states = set().union(*(e.states for e in before)) if before else set()
    fresh_states = new.states - seen_states - {"US"}
    if new.kin() == "conflict" and fresh_states and HARD_CONFLICT.search(text or ""):
        return f"{', '.join(sorted(fresh_states))} drawn into the conflict"
    pct = max((e.pct for e in before), default=0.0)
    if new.pct and pct and new.pct >= 2 * pct:
        return f"the move is now {new.pct:g}% (was {pct:g}%)"
    money = max((e.money for e in before), default=0.0)
    if new.money and money and new.money >= 1.5 * money and new.money - money >= 20e6:  # "losses rise to $500M" after $300M
        return f"the sum is now ${new.money / 1e6:,.0f}M (was ${money / 1e6:,.0f}M)"
    m = HARD_OUTCOME.search(text or "")
    return f"a hard outcome: {m.group(0)}" if m else ""


HARD_CONFLICT = _rx(r"\b(strikes?|struck|attack\w*|missiles?|drones?|invad\w*|declares? war|shot down)\b")
HARD_OUTCOME = _rx(r"\b(arrests?|arrested|charged|indicted|convicted|sentenced|files? for bankruptcy|bankrupt|ceasefire (?:agreed|reached|signed|takes effect)|"
                   r"truce (?:agreed|reached|signed)|declares? war|closes? the strait|strait (?:closed|shut)|blockade|"
                   r"(?:halt|suspend|paus|freez)\w* (?:all )?withdrawals|resigns|resigned|ousted|fired as|steps? down|stepped down|depegs?|delisted|"
                   r"nuclear test|funds? (?:recovered|returned|frozen|seized)|shuts? down|shut down|shutting down|halts? (?:production|exports|output|operations))\b")
PEACE_DONE = _rx(r"\b(ceasefire|truce|peace (?:deal|agreement|accord))\b.{0,30}\b(agreed|reached|signed|takes effect|in effect|announced)\b|"
                 r"\b(agree|agrees|agreed|sign|signs|signed|reach|reaches|reached)\b.{0,30}\b(ceasefire|truce|peace (?:deal|agreement|accord))\b")


# --- why should anyone care ------------------------------------------------------------------------------------------------------------

INDEX = _rx(r"\b(s&p|s&p 500|nasdaq|dow|stoxx|ftse|nikkei|hang seng|kospi|stock market|global stocks|world stocks)\b")
CRYPTO = _rx(r"\b(bitcoin|btc|ether(?:eum)?|eth|crypto market|crypto)\b")
OIL = _rx(r"\b(brent|wti|oil prices?|crude prices?|crude futures|oil futures|gas prices|natural gas prices?|"
          r"(?:oil|crude) (?:falls?|rises?|jumps?|slides?|surges?|plunges?|tumbles?|soars?|drops?|climbs?|spikes?|slumps?|slips?|gains?))\b")
RELATIVE = _rx(r"\b(doubles?|doubled|triples?|tripled|halves|halved|quadruple\w*)\b")
AFTERMATH = _rx(r"\b(?:from|after|over|since) (?:the |its |a |an )?(?:\$[\d.,]+\s*(?:b|bn|billion|m|mn|million)\b\s*)?(?:\w+\s+){0,2}"
                r"(?:hack|exploit|heist|theft)\b")
LIQ = _rx(r"\bliquidat\w*\b")
MEGA = _rx(r"\b(nvidia|apple|microsoft|alphabet|google|amazon|meta|tesla|berkshire|jpmorgan|openai|anthropic|tsmc|samsung|broadcom|binance|coinbase|tether)\b")
CENTRAL_BANK = _rx(r"\b(fed|federal reserve|ecb|bank of england|boe|bank of japan|boj|pboc|snb|rba)\b")
RATE_ACT = _rx(r"\b(cuts?|hikes?|raises?|lifts?|lowers?|holds?|keeps?|leaves?) (?:its |key |interest |benchmark |policy )*rates?\b|\brate (?:cut|hike)\b")
SCALE_DISRUPTION = _rx(r"\b(nationwide|global|worldwide|widespread|across (?:the )?(?:us|u\.s\.|europe|country)|millions of|all flights|every flight)\b")
OVER = _rx(r"\b(lifted|resolved|restored|returning to normal|back to normal|ends?|ended|resumes?|resumed|recover\w*|after (?:the|friday|last|monday|tuesday|"
           r"wednesday|thursday|saturday|sunday)\w*)\b")
CORP_HARD = _rx(r"\b(arrests?|arrested|charged|indicted|files? for bankruptcy|bankrupt|resigns|resigned|ousted|fired|steps? down|stepped down|"
                r"(?:halt|suspend|paus|freez)\w* (?:all )?withdrawals|collapses?|insolven\w*|delist\w*|raided)\b")
ROUTINE = _rx(r"\b(says?|said|warns?|urges?|calls? for|pitch\w*|propos\w*|vows?|slams?|criticiz\w*|praises?|discuss\w*|weighs?|considers?|eyes|"
              r"probes? whether|asks?|seeks?|hires?|meets?|to meet|talks?|signals?|hints?|could|may|might|as (?:\w+ )*risks? (?:rise|grow|mount)|"
              r"what we know|explainer|analysis|opinion|explained|q&a)\b")
HEDGE = _rx(r"\b(reportedly|allegedly|rumou?r\w*|unconfirmed|sources say|according to sources|whether|could|may|might|report:|: report\b|- report\b)\b")
WAR_MAJOR = _rx(r"\b(declares? war|invasion|invad\w*|nuclear|closes? the strait|strait (?:closed|shut)|blockade|assassinat\w*|kills? (?:the )?(?:president|"
                r"supreme leader|prime minister|commander|chief))\b")


@dataclass
class Assessment:
    ok: bool
    strength: str  # major | strong | none
    why: str  # why it matters, as a reader would be told (empty when it doesn't)
    reason: str  # why not, when not ok
    stop_scroll: int
    parts: dict[str, int] = field(default_factory=dict)
    event: Event | None = None


def _consequence(title: str, summary: str, ev: Event) -> tuple[str, str]:
    """(strength, why) from the facts the story states. The headline carries the event; the summary may supply its figures."""
    head = title or ""
    text = f"{head} {summary or ''}"
    best: tuple[str, str] = ("none", "")

    def take(strength: str, why: str) -> None:
        nonlocal best
        if strength == "major" or best[0] == "none":
            best = (strength, why) if best[0] != "major" else best

    deaths = deaths_of(head)
    # a single strike in a war already running kills people most days: it takes a toll that changes the story
    strong_at, major_at = (25, 75) if ev.kin() == "conflict" else (10, 50)
    if deaths >= major_at:
        take("major", f"{deaths} killed")
    elif deaths >= strong_at:
        take("strong", f"{deaths} killed")
    pct = ev.pct
    if pct:
        if INDEX.search(head):
            if pct >= 4:
                take("major", f"a {pct:g}% index move")
            elif pct >= 2:
                take("strong", f"a {pct:g}% index move")
        elif CRYPTO.search(head):
            if pct >= 10:
                take("major", f"a {pct:g}% crypto move")
            elif pct >= 5:
                take("strong", f"a {pct:g}% crypto move")
        elif OIL.search(head):
            if pct >= 8:
                take("major", f"oil moves {pct:g}%")
            elif pct >= 5:
                take("strong", f"oil moves {pct:g}%")
        elif MEGA.search(head):
            if pct >= 10:
                take("major", f"a {pct:g}% move in a mega-cap")
            elif pct >= 6:
                take("strong", f"a {pct:g}% move in a mega-cap")
        elif pct >= 20:
            take("strong", f"a {pct:g}% move")
    money = money_of(head)
    if money and not AFTERMATH.search(head):  # "...loot from $6.3B Binance hack": the old theft's figure, not a new one
        if ev.family == "hack":
            if money >= 250e6:
                take("major", f"${money / 1e6:,.0f}M stolen")
            elif money >= 25e6:
                take("strong", f"${money / 1e6:,.0f}M stolen")
        elif LIQ.search(head):
            if money >= 1e9:
                take("major", f"${money / 1e9:g}B liquidated")
            elif money >= 500e6:
                take("strong", f"${money / 1e6:,.0f}M liquidated")
        elif money >= 10e9:
            take("strong", f"${money / 1e9:g}B at stake")
    if ev.kin() == "conflict":
        acting = ev.states
        if WAR_MAJOR.search(head):
            take("major", WAR_MAJOR.search(head).group(0))  # type: ignore[union-attr]
        elif len(acting) >= 2 and HARD_CONFLICT.search(head) and not ROUTINE.search(head):
            take("strong", f"{' and '.join(sorted(acting))}: a state strikes another")
    if ev.kin() == "disruption" and SCALE_DISRUPTION.search(head) and not OVER.search(head):
        take("strong", f"a {SCALE_DISRUPTION.search(head).group(0)} disruption, happening now")  # type: ignore[union-attr]
    if CENTRAL_BANK.search(head) and RATE_ACT.search(head):
        take("strong", "a central-bank rate decision")
    if (m := CORP_HARD.search(head)) and not HEDGE.search(head) and (MEGA.search(head) or ev.family == "hack" or ANY_FIRM_HARD.search(m.group(0))):
        take("strong", f"a hard outcome: {m.group(0)}")
    if RELATIVE.search(head) and not ROUTINE.search(head):
        take("strong", f"a figure {RELATIVE.search(head).group(0)}")  # type: ignore[union-attr]
    if ev.family == "disaster" and deaths == 0 and re.search(r"\bmagnitude ([7-9](?:\.\d)?)\b", text, re.I):
        take("strong", "a magnitude 7+ earthquake")
    if ev.kin() == "conflict" and not ROUTINE.search(head) and (m := INFRA_HIT.search(head)):
        take("major" if CHOKE_CLOSED.search(head) else "strong", f"energy or shipping hit: {m.group(0)}")
    if (m := SHIPS_HIT.search(head)) and (n := next((g for g in m.groups() if g), "0")) and (int(n) if n.isdigit() else WORDS.get(n.lower(), 0)) >= 2:
        take("strong", f"{m.group(0)}")
    if (m := SANCTION_ACT.search(head)) and not NOT_HAPPENED.search(head):
        take("strong", f"a sanction or tariff that takes effect: {m.group(0)}")
    if MACRO_WORD.search(head) and (m := MACRO_EXTREME.search(head)):
        take("strong", f"economic data at an extreme: {m.group(0)}")
    if (m := INDEX_REGIME.search(head)):
        take("strong", f"a market regime: {m.group(0)}")
    if (m := PRICE_EXTREME.search(head)) and (INDEX.search(head) or CRYPTO.search(head) or OIL.search(head) or MAJOR_ASSET.search(head)) and not HEDGE.search(head):
        take("strong", f"a price extreme: {m.group(0)}")
    if (m := REGULATOR_ACT.search(head)) and not HEDGE.search(head):
        take("strong", f"a regulator acts: {m.group(0)}")
    if ev.kin() == "conflict" and (m := PEACE_DONE.search(head)) and not HEDGE.search(head):
        take("major", f"a war's turning point: {m.group(0)}")
    # this account's beat (owner, 2026-11-10/16): Wall Street institutions moving onto crypto rails, ETF flows in the billions, a market-wide
    # swing in the billions. Each is a stated fact with consequences for the readers' money, not a mood
    from app.newsdesk import editorial

    sig = editorial.significance(head)
    if sig is not None and not HEDGE.search(head) and (sig.kind in ("structure", "flows") or str(sig.why).startswith("market-wide swing")):
        take("strong", str(sig.why))
    return best


INFRA_HIT = _rx(r"\b(?:hits?|struck|strikes?|attack\w*|targets?|ablaze|on fire|damag\w*)\b.{0,40}\b(oil (?:terminal|refinery|field|facility|tanker|port)|refinery|"
                r"gas (?:field|terminal|facility|pipeline)|pipeline|lng (?:terminal|plant|tanker)|oil tanker|export terminal|bridge)\b|"
                r"\b(oil (?:terminal|refinery|field|facility)|refinery|pipeline|lng (?:terminal|plant))\b.{0,30}\b(hit|struck|attacked|ablaze|on fire|damaged)\b")
CHOKE_CLOSED = _rx(r"\b(closes?|closed|shut|blockade\w*|halts? (?:all )?shipping|mines?)\b.{0,40}\b(strait|hormuz|suez|bab el-mandeb|bosphorus)\b|"
                   r"\b(strait|hormuz|suez|bab el-mandeb)\b.{0,40}\b(closed|shut|blockade\w*|mined)\b")
SHIPS_HIT = _rx(r"\b(\d+|two|three|four|five|six|seven|eight|nine|ten|dozens)\s+(?:ships|tankers|vessels)\b.{0,30}\b(?:hit|struck|attacked|seized|sunk|damaged)\b|"
                r"\b(?:hit|strike|struck|attack|seize|sink)s?\s+(\d+|two|three|four|five|six|seven|eight|nine|ten)\s+(?:ships|tankers|vessels)\b")
SANCTION_ACT = _rx(r"\b(?:imposes?|imposed|announces?|adopts?|slaps?|hits?|unveils?|expands?)\b.{0,30}\b(?:sweeping |new |fresh |broad )?"
                   r"(?:sanctions|tariffs?|export ban|embargo|price cap)\b")
MACRO_WORD = _rx(r"\b(inflation|cpi|consumer prices|unemployment|jobless|payrolls|jobs report|gdp|economy|recession|retail sales)\b")
MACRO_EXTREME = _rx(r"\b(highest|lowest|fastest|slowest|biggest|record|since (?:19|20)\d\d|in (?:\d+|two|three|four|five|ten) (?:years|decades)|"
                    r"unexpected\w*|surpris\w*|contracts?|contracted|recession)\b")
INDEX_REGIME = _rx(r"\b(?:enters?|entered|in|into|tips? into)\s+(?:a )?(correction|bear market)\b|\bcircuit breakers?\b|\btrading (?:halted|halt)\b|"
                   r"\bworst (?:day|week|month) since\b")
PRICE_EXTREME = _rx(r"\b(record high|all-time high|new record|(?:lowest|highest) (?:level |close )?since (?:(?:january|february|march|april|may|june|july|"
                    r"august|september|october|november|december) )?(?:19|20)\d\d|\d+-year (?:high|low))\b")
MAJOR_ASSET = _rx(r"\b(xrp|solana|sol|gold|silver|dollar|yen|euro|treasur(?:y|ies)|10-year|bond yields?)\b")
REGULATOR_ACT = _rx(r"\b(sec|cftc|doj|justice department|fca|esma|ofac|treasury|fed|occ)\b\s+(?:sues|charges|fines|bans|shuts|freezes|sanctions|"
                    r"approves|rejects|orders)\b")


def assess(title: str, summary: str = "", *, relevant: bool = True, age_min: float | None = None, repeat: str = "") -> Assessment:
    """Why should anyone care? A concrete, source-stated consequence (strength strong or major), not a repeat, and a stop-scroll score of
    60 or more. The parts are kept apart so Admin can show each one."""
    ev = event_of(title, summary)
    head = title or ""
    strength, why = _consequence(title, summary, ev)
    hedged = bool(HEDGE.search(head))
    routine = bool(ROUTINE.search(head))
    if hedged and strength != "none":
        strength, why = "none", ""
    consequence = {"major": 70, "strong": 45, "none": 0}[strength]
    novelty = 0 if repeat else 15
    urgency = 0 if age_min is None else 10 if age_min <= 60 else 5 if age_min <= 180 else 0
    relevance = 10 if relevant else 0
    controversy = 5 if strength != "none" and ev.family in ("legal", "conflict") else 0
    parts = {"consequence": consequence, "novelty": novelty, "urgency": urgency, "relevance": relevance, "controversy": controversy}
    score = sum(parts.values())
    if repeat:
        return Assessment(False, strength, why, repeat, score, parts, ev)
    if strength == "none":
        if hedged:
            reason = "a hedged or speculative report (\"whether\", \"could\", \"report:\"): nothing has happened yet"
        elif routine:
            reason = "a statement, request or reaction with no concrete consequence"
        else:
            reason = "no concrete consequence the sources state (no deaths, money, market move of size, escalation or hard outcome)"
        return Assessment(False, strength, "", f"why should anyone care? {reason}", score, parts, ev)
    if score < STOP_SCROLL:
        return Assessment(False, strength, why, f"stop-scroll {score} < {STOP_SCROLL}: {why}, but not enough to stop a reader", score, parts, ev)
    return Assessment(True, strength, why, "", score, parts, ev)


STOP_SCROLL = 60


# --- what already went out, on every lane -------------------------------------------------------------------------------------------


@dataclass
class Told:
    lane: str  # telegram | x-newsdesk | x-trending | released
    ref: str
    title: str
    at: datetime
    event: Event


REPEAT_WINDOW = {"conflict": timedelta(hours=48), "disruption": timedelta(hours=96), "market": timedelta(hours=24)}
DEFAULT_WINDOW = timedelta(days=7)


def _aware(d: datetime | None, now: datetime) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=now.tzinfo)


PLATFORM_LANES = {"telegram": ("telegram", "released"), "x": ("x-newsdesk", "x-trending", "released")}


def told(db: Any, now: datetime, hours: int = 168, *, skip_event: int | None = None, skip_tg: int | None = None, skip_x: int | None = None,
         skip_topic: int | None = None, platform: str | None = None) -> list[Told]:
    """Every story that went out (or is on its way out) in the last `hours`, on Telegram, X (News Desk and Trending / Terminal) and
    released News Desk events, newest first. Pinned posts are channel posts, so they are here too."""
    from sqlalchemy import select

    from app.models.newsdesk import NdEvent, NdPost, NdTelegramPost
    from app.models.trending import TtPost

    since = now - timedelta(hours=hours)
    out: list[Told] = []
    for p in db.scalars(select(NdTelegramPost).where(NdTelegramPost.created_at >= since, NdTelegramPost.status.in_(("delivered", "publishing", "uncertain")))):
        if p.id == skip_tg or (skip_event is not None and p.story_link == f"event:{skip_event}"):  # the event's own send is not a prior post
            continue
        t = p.title or p.headline or ""
        out.append(Told("telegram", f"telegram post {p.message_id or p.id}", t, _aware(p.delivered_at or p.created_at, now) or now, event_of(t, p.text or "")))
    for p in db.scalars(select(NdPost).where(NdPost.created_at >= since, NdPost.status.in_(("published", "uncertain", "publishing", "dry_run")))):
        if p.id == skip_x or (skip_event is not None and (p.facts or {}).get("event_id") == skip_event):
            continue
        f = p.facts or {}
        t = str(f.get("title") or f.get("headline") or (p.text or "").split("\n")[0])
        out.append(Told("x-newsdesk", f"X post {p.id}", t, _aware(p.published_at or p.created_at, now) or now, event_of(t, p.text or "")))
    for p in db.scalars(select(TtPost).where(TtPost.created_at >= since, TtPost.status.in_(("published", "uncertain", "publishing", "dry_run")))):
        if p.topic_id is not None and p.topic_id == skip_topic:
            continue
        lead = str(((p.sources or [{}])[0] or {}).get("title") or (p.text or "").split("\n")[0])
        out.append(Told("x-trending", f"trend post {p.id}", lead, _aware(p.published_at or p.created_at, now) or now, event_of(lead, p.text or "")))
    for e in db.scalars(select(NdEvent).where(NdEvent.status == "released", NdEvent.released_at >= since)):
        if e.id == skip_event or (e.tg_status in ("skipped", "missed", "failed") and e.x_status in ("skipped", "missed", "failed")):
            continue
        out.append(Told("released", f"event {e.id}", e.title or e.headline, _aware(e.released_at, now) or now, event_of(e.title or e.headline, e.summary or "")))
    if platform in PLATFORM_LANES:  # a repeat is a second post about one event on the SAME platform; X and Telegram carrying it once each is one story
        out = [h for h in out if h.lane in PLATFORM_LANES[platform]]
    out.sort(key=lambda x: x.at, reverse=True)
    return out


def repeat_of(title: str, summary: str, history: list[Told], now: datetime) -> str:
    """"" when this is a new event or a substantial development of one already told; else why it is a repeat."""
    ev = event_of(title, summary)
    same = [h for h in history if same_event(ev, h.event) and now - h.at <= REPEAT_WINDOW.get(ev.kin(), DEFAULT_WINDOW)]
    if not same:
        return ""
    if (dev := development(ev, [h.event for h in same], f"{title} {summary or ''}")):
        return ""
    first = min(same, key=lambda h: h.at)
    hours = max(1, int((now - first.at).total_seconds() // 3600))
    return (f"same event as {first.ref} ({hours} h ago: \"{first.title[:70]}\") and {len(same)} post(s) since; no substantial new development "
            f"(more deaths, a new country drawn in, a move or sum twice the size, a hard outcome)")


def development_of(title: str, summary: str, history: list[Told], now: datetime) -> str:
    ev = event_of(title, summary)
    same = [h for h in history if same_event(ev, h.event) and now - h.at <= REPEAT_WINDOW.get(ev.kin(), DEFAULT_WINDOW)]
    return development(ev, [h.event for h in same], f"{title} {summary or ''}") if same else ""


def judge(db: Any, title: str, summary: str = "", now: datetime | None = None, *, relevant: bool = True, age_min: float | None = None, **skip: Any) -> Assessment:
    """assess() with the cross-lane history: the one call every lane makes before it publishes."""
    from app.db.base import utcnow

    now = now or utcnow()
    history = told(db, now, **skip)
    rep = repeat_of(title, summary, history, now)
    a = assess(title, summary, relevant=relevant, age_min=age_min, repeat=rep)
    if not a.ok and not rep and a.strength == "none" and (dev := development_of(title, summary, history, now)):
        # a substantial development of a story already out IS the reason to care: losses doubled, the founder arrested, withdrawals halted
        parts = {**a.parts, "consequence": 45}
        return Assessment(True, "strong", dev, "", sum(parts.values()), parts, a.event)
    return a


ANY_FIRM_HARD = _rx(r"\b(arrests?|arrested|charged|indicted|bankrupt|files? for bankruptcy|insolven\w*|collapses?|(?:halt|suspend|paus|freez)\w* (?:all )?withdrawals)\b")


# --- one post, one event -------------------------------------------------------------------------------------------------------------


def stray_sentences(headline: str, lead_text: str, other_titles: list[str], sentences: list[str]) -> list[str]:
    """The sentences that tell a different story than the headline: another article's headline (FAA repairs + "Duffy pitches $50B for the
    FAA"), or a sentence the lead article doesn't contain. One post is one event."""
    bad = []
    lead_marks = [checks.fingerprint(s) for s in re.split(r"(?<=[.!?])\s+", lead_text or "") if s.strip()]
    for s in sentences:
        mark = checks.fingerprint(s)
        if not mark:
            continue
        if any(o and checks.similarity(mark, checks.fingerprint(o)) >= 0.5 for o in other_titles):
            bad.append(s)
            continue
        if lead_marks and not any(checks.similarity(mark, m) >= 0.5 for m in lead_marks):
            bad.append(s)
    return bad


def matters_sentence(sentence: str) -> bool:
    """A context line earns its place only by saying why the story matters: a figure, or a stated consequence."""
    return bool(checks.numbers(sentence or "")) or bool(re.search(r"\b(first|record|largest|biggest|halt\w*|suspend\w*|closed|killed|wiped|"
                                                                 r"lost|losses|evacuat\w*|grounded|banned|sanction\w*)\b", sentence or "", re.I))


__all__ = ["Assessment", "Event", "Told", "assess", "development_of", "event_of", "judge", "matters_sentence", "repeat_of", "same_event",
           "stray_sentences", "told"]
