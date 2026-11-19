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
                     r"(?:iran|israel|russia|ukraine|us|u\.s\.)\w* (?:attack|strike)s?|launch\w* (?:strikes|attacks|missiles|drones)|"
                     # held-out replay 2026-11-15: "Iran targets US military site in Gulf", "Bahrain intercepts Iranian aerial attacks"
                     r"(?:aerial|air|rocket|missile|drone|military) attacks?|intercept\w* .{0,30}(?:attacks?|missiles?|drones?|rockets?)|"
                     r"targets? .{0,30}(?:base|airbase|military|site|radar|embassy|ship|tanker|port|refinery))")),
    ("disaster", _rx(r"\b(earthquake|quake|tsunami|hurricane|typhoon|cyclone|flood\w*|wildfire|eruption|plane crash|crash(?:ed)? (?:kills|killing)|"
                     r"derail\w*|explosion|collapse[sd]? (?:kills|killing))\b")),
    ("hack", _rx(r"\b(hack\w*|exploit\w*|drain\w*|stolen|heist|breach\w*)\b")),
    ("aviation", _rx(r"\b(ground stop|grounded|groundings|flights? (?:cancel\w*|delay\w*|halted)|air traffic control|airspace (?:closed|closure))\b")),
    ("outage", _rx(r"\b(outages?|went down|is down|are down|down for|offline|disruption|system failure|glitch(?:es)?|restored)\b")),
    ("earnings", _rx(r"\b(earnings|quarterly (?:results|revenue|profit)|revenue|guidance|profit warning|results beat|beats? (?:estimates|expectations))\b")),
    ("rates", _rx(r"\b(rate (?:cut|hike|decision)|cuts? rates|raises? rates|hikes? rates|holds? rates|interest rates?|fomc)\b")),
    # one data release is one event per country: "Japan's economy unexpectedly contracts" and "...shrinks" (replay: posted twice)
    ("macro", _rx(r"\b(inflation|cpi|consumer prices|gdp|economy (?:unexpectedly )?(?:contracts|shrinks|grows|expands)|payrolls|jobs report|unemployment rate|"
                  r"jobless rate|retail sales)\b")),
    ("legal", _rx(r"\b(probe\w*|investigat\w*|lawsuit|sues|sued|charged|indict\w*|subpoena\w*|scandal|testif\w*|hearing|arrest\w*|convict\w*|sentenc\w*|"
                  r"spy|espionage)\b")),
    ("deal", _rx(r"\b(merger|merge|acquisition|acquire\w*|takeover|buyout|ipo|to buy|deal to)\b")),
    ("policy", _rx(r"\b(tariffs?|sanction\w*|export controls?|bill|law|executive order|budget|funding|stimulus|regulat\w*|ban\w*)\b")),
    ("market", _rx(r"\b(rall(?:y|ies)|slump\w*|plung\w*|tumbl\w*|surg\w*|soar\w*|sell-?off|falls?|drops?|rises?|jumps?|slides?|crash\w*|record high|all-time high|"
                   r"breaks? (?:below|above)|liquidat\w*|holds? near|loses?|lost)\b")),
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
    places: set[str] = field(default_factory=set)  # every country named (for one country's data release)
    indicator: str = ""  # which data release: inflation, growth, jobs, retail
    title_stems: set[str] = field(default_factory=set)
    title: str = ""

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
    # a company named in two ordinary words ("United Airlines") is one name: kept as "~UNITED AIRLINES", used only for incidents
    if not shout:
        for run in re.findall(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+)\b", raw):
            w = run.split()
            out |= {"~" + f"{w[i]} {w[i + 1]}".upper() for i in range(len(w) - 1)}
    if re.search(r"\bbank(?:s|ing)?\b(?! of)", raw, re.I):
        out.add("~BANKS")
    for m in SYSTEMIC.finditer(raw):  # the service that fails is the incident's identity: "major US banks" and "banks" are one
        name = m.group(0).lower()
        out.add("~" + ("BANKS" if "bank" in name and name != "bank of america" else name.upper()))
    return out - {s for s in STATES}  # a state is not an anchor: two Iran stories are not one story because both say Iran


MONEY = re.compile(r"\$\s?(\d+(?:[.,]\d+)?)\s*(trillion|tn|billion|bn|b|million|mn|m)\b", re.I)
PCT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b)")
DEATHS = re.compile(r"\b(?:kill(?:s|ed|ing)?|dead|deaths?|death toll (?:rises to|of|at)?)\s+(?:all |at least |more than |over |nearly |about |some )?(\d[\d,]*)|"
                    r"(\d[\d,]*)\s+(?:people\s+|civilians\s+|soldiers\s+|troops\s+)?(?:were\s+|are\s+)?(?:killed|dead|die[ds]?)\b", re.I)
NOT_HAPPENED = re.compile(r"\b(pitch\w*|propos\w*|asks?|asked|asking|seeks?|sought|seeking|plans?|planned|considers?|considering|could|may|might|weighs?|"
                          r"moves? to|moving to|pushes? to|threatens? to|vows? to|"
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
                 states={k for k, rx in STATES.items() if rx.search(head)}, deaths=deaths_of(text), money=money_of(head), pct=pct_of(head),
                 places=places_of(head), indicator=indicator_of(main_clause_soft(head)), title_stems={w[:5] for w in checks.tokens(head)}, title=head)


INDICATORS = {"inflation": _rx(r"\b(inflation|cpi|consumer prices)\b"), "growth": _rx(r"\b(gdp|economy)\b"),
              "jobs": _rx(r"\b(payrolls|jobs report|unemployment|jobless|wages?)\b"), "retail": _rx(r"\bretail sales\b")}


def indicator_of(head: str) -> str:
    return next((k for k, rx in INDICATORS.items() if rx.search(head or "")), "")


def places_of(head: str) -> set[str]:
    from app.newsdesk import editorial

    out = editorial.countries_in(head) | {k for k, rx in STATES.items() if rx.search(head)}
    if re.search(r"(?:^|[^A-Za-z])(UK|U[.]K[.]|Britain|British)(?:$|[^A-Za-z])", head or ""):
        out.add("UK")
    return out


def same_event(a: Event, b: Event) -> bool:
    """One real-world event, told twice: the same kind of event (a tanker attack and a missile strike are both the war) with the same
    specific actor or place, or the same conflict in the same region. Never just a shared keyword: two Nvidia stories (its CEO's scandal,
    its earnings) are two events; two FAA stories about one ground stop are one."""
    if a.kin() == b.kin() == "conflict":
        # one war is not one event: the same specific target/actor named, or the SAME belligerents exchanging fire within the window. Replay
        # 2026-11-15..18: "Ukraine, Russia race to build military satellite networks" must not block a drone strike on Russia's petrochemical
        # capital; "US strikes Iranian missile site" then "ADNOC ship attacked in Hormuz" (same Gulf exchange) stays one event
        if {x for x in a.anchors & b.anchors if not x.startswith(("$", "~"))}:
            return True
        if not (a.regions & b.regions):
            return False
        fire_a, fire_b = bool(HARD_CONFLICT.search(a.title or "")), bool(HARD_CONFLICT.search(b.title or ""))
        if not (fire_a and fire_b):
            return False
        # one wave of strikes: a belligerent in common ("Iran targets US military site in Gulf", then "Iran targets Bahrain airbase as strikes
        # resume"); only a substantial development (more deaths, a new country hit) makes a second post within the window
        return not a.states or not b.states or bool(a.states & b.states)
    if a.kin() == b.kin() == "macro":
        return bool(a.places & b.places) and a.indicator == b.indicator  # one country's release of one indicator (UK jobs != UK inflation)
    shared = a.anchors & b.anchors
    if a.kin() == b.kin() == "market":
        return bool({x for x in shared if not x.startswith("~")})  # the same asset moving within the market window (24 h)
    incident = a.kin() == b.kin() and a.kin() in ("hack", "disruption", "disaster")
    specific = {x for x in shared if not x.startswith("$") and (incident or not x.startswith("~"))}  # an asset is a topic, not an event
    if a.kin() == b.kin() and a.kin() != "news":
        # one big name in common is not one event: Nvidia's CEO scandal and a probe into who avoided Nvidia losses are two stories
        # (an incident is the exception: "Binance hack" and "loot from the Binance hack" are one hack)
        return bool(specific) and (len(specific) >= 2 or a.kin() in ("hack", "disruption", "disaster")
                                   or not all(MEGA.fullmatch(x.lower()) for x in specific))
    if len(specific) >= 2 and a.family != "market" and b.family != "market":
        return True
    # one shared specific name AND most of the words in common: "Estonia to close Russia border after 'sabotage' bid" and "NATO member
    # Estonia to close Russia border after attempted 'sabotage'" (replay: posted twice)
    return bool(specific and a.title_stems and b.title_stems and len(a.title_stems & b.title_stems) / len(a.title_stems | b.title_stems) >= 0.45)


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


HARD_CONFLICT = _rx(r"\b(strikes?|struck|attack\w*|missiles?|drones?|invad\w*|declares? war|shot down|targets?|targeted|intercept\w*)\b")
HARD_OUTCOME = _rx(r"\b(arrests?|arrested|charged|indicted|convicted|sentenced|files? for bankruptcy|bankrupt|ceasefire (?:agreed|reached|signed|takes effect)|"
                   r"truce (?:agreed|reached|signed)|declares? war|closes? the strait|strait (?:closed|shut)|blockade|"
                   r"(?:halt|suspend|paus|freez)\w* (?:all )?withdrawals|resigns|resigned|ousted|fired as|steps? down|stepped down|depegs?|delisted|"
                   r"nuclear test|funds? (?:recovered|returned|frozen|seized)|shuts? down|shut down|shutting down|halts? (?:production|exports|output|operations))\b")
PEACE_DONE = _rx(r"\b(ceasefire|truce|peace (?:deal|agreement|accord))\b.{0,30}\b(agreed|reached|signed|takes effect|in effect|announced)\b|"
                 r"\b(agree|agrees|agreed|sign|signs|signed|reach|reaches|reached)\b.{0,30}\b(ceasefire|truce|peace (?:deal|agreement|accord))\b")


# --- why should anyone care ------------------------------------------------------------------------------------------------------------

INDEX = _rx(r"\b(s&p|s&p 500|nasdaq|dow|stoxx|ftse|nikkei|hang seng|kospi|stock market|global stocks|world stocks)\b")
CRYPTO = _rx(r"\b(bitcoin|btc|ether(?:eum)?(?![.\w])|eth|crypto market|crypto)\b")
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
OVER = _rx(r"\b(lifted|resolved|resolves|restored|addresses|fixes|fixed|returning to normal|back to normal|ends?|ended|resumes?|resumed|recover\w*|"
           r"still|days after|after (?:the|friday|last|monday|tuesday|"
           r"wednesday|thursday|saturday|sunday)\w*)\b")
CORP_HARD = _rx(r"\b(arrests?|arrested|charged|indicted|files? for bankruptcy|bankrupt|resigns|resigned|ousted|fired|steps? down|stepped down|"
                r"(?:halt|suspend|paus|freez)\w* (?:all )?withdrawals|collapses?|insolven\w*|delist\w*|raided)\b")
ROUTINE = _rx(r"\b(says?|said|warns?|urges?|calls? for|pitch\w*|propos\w*|vows?|slams?|criticiz\w*|praises?|discuss\w*|weighs?|considers?|eyes|"
              r"probes? whether|asks?|seeks?|hires?|meets?|to meet|talks?|signals?|hints?|could|may|might|as (?:\w+ )*risks? (?:rise|grow|mount)|"
              r"what we know|explainer|analysis|opinion|explained|q&a|reacts?|blames?|downplays?|suspects?|breaks silence|backs calls)\b")
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
    interest: int = 0  # the audience-interest score (audience()), 0-100
    pitch: str = ""  # the specific reason THIS audience would care, when it passes


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
    # a market move's own figure may sit in the lead sentence of the same article ("...as liquidations sweep through leveraged longs. More
    # than $1 billion in positions were forced shut in 12 hours"): only that sentence, never the background further down
    lead = re.split(r"(?<=[.!?])\s+", (summary or "").strip())[0] if summary else ""
    move = ev.family == "market" or bool(LIQ.search(head))
    pct = ev.pct or (pct_of(lead) if move else 0.0)
    # the move must be the SUBJECT's: "AERO falls 27%, lagging Bitcoin" is a small token's move, "Harvard keeps its Bitcoin ETF stake as fund
    # gains 42%" is nobody's market move, "Asia is building 73% of new nuclear capacity" is no move at all (replay 2026-11-15..18)
    subject = subject_of(head)
    if pct and not FORECAST.search(head) and not EVERGREEN.search(head):
        if INDEX.search(subject):
            if pct >= 4:
                take("major", f"a {pct:g}% index move")
            elif pct >= 2:
                take("strong", f"a {pct:g}% index move")
        elif CRYPTO.search(subject):
            if pct >= 10:
                take("major", f"a {pct:g}% crypto move")
            elif pct >= 5:
                take("strong", f"a {pct:g}% crypto move")
        elif OIL.search(head) and OIL.search(subject + " " + head[:60]):
            if pct >= 8:
                take("major", f"oil moves {pct:g}%")
            elif pct >= 5:
                take("strong", f"oil moves {pct:g}%")
        elif MEGA.search(subject) and move:
            if pct >= 10:
                take("major", f"a {pct:g}% move in a mega-cap")
            elif pct >= 6:
                take("strong", f"a {pct:g}% move in a mega-cap")
        elif ev.family == "earnings" and MEGA.search(head[:40]) and pct >= 50:  # "Nvidia revenue jumps 103% to $115.8 billion"
            take("strong", f"a mega-cap's results: {pct:g}% growth")
    money = money_of(head) or (money_of(lead) if LIQ.search(head) else 0.0)
    if FORECAST.search(head):
        money = 0.0
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
    main = main_clause(head)  # "Bahrain intercepts Iranian aerial attacks as Tehran says it attacked...": the side clause's "says" is not the story
    if ev.kin() == "conflict":
        acting = ev.states
        if WAR_MAJOR.search(head):
            take("major", WAR_MAJOR.search(head).group(0))  # type: ignore[union-attr]
        elif len(acting) >= 2 and HARD_CONFLICT.search(main) and not ROUTINE.search(main):
            take("strong", f"{' and '.join(sorted(acting))}: a state strikes another")
    if ev.kin() == "disruption" and SCALE_DISRUPTION.search(head) and not OVER.search(head) and not REACTION.search(head) and not ROUTINE.search(main):
        take("strong", f"a {SCALE_DISRUPTION.search(head).group(0)} disruption, happening now")  # type: ignore[union-attr]
    if CENTRAL_BANK.search(head) and RATE_ACT.search(head):
        take("strong", "a central-bank rate decision")
    if (m := CORP_HARD.search(head)) and not HEDGE.search(head) and (MEGA.search(head) or ev.family == "hack" or ANY_FIRM_HARD.search(m.group(0))):
        take("strong", f"a hard outcome: {m.group(0)}")
    if RELATIVE.search(head) and not ROUTINE.search(head) and ev.states and SUPPLY.search(head):  # "Russian diesel exports double"
        take("strong", f"a country's {SUPPLY.search(head).group(0)} {RELATIVE.search(head).group(0)}")  # type: ignore[union-attr]
    if ev.family == "disaster" and deaths == 0 and re.search(r"\bmagnitude ([7-9](?:\.\d)?)\b", text, re.I):
        take("strong", "a magnitude 7+ earthquake")
    if ev.kin() == "conflict" and not ROUTINE.search(head) and (m := INFRA_HIT.search(head)):
        take("major" if CHOKE_CLOSED.search(head) else "strong", f"energy or shipping hit: {m.group(0)}")
    if (m := SHIPS_HIT.search(head)) and (n := next((g for g in m.groups() if g), "0")) and (int(n) if n.isdigit() else WORDS.get(n.lower(), 0)) >= 2:
        take("strong", f"{m.group(0)}")
    if (m := SANCTION_ACT.search(head)) and not NOT_HAPPENED.search(head):
        take("strong", f"a sanction or tariff that takes effect: {m.group(0)}")
    if MACRO_WORD.search(main) and (m := MACRO_EXTREME.search(head)):  # the economy is the subject, not shoe prices ("...amid inflation")
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
    # a study, pilot, test or exploration is not an outcome (production 2026-11-18: "Citi and Circle to help World Bank study stablecoin payouts")
    if sig is not None and not HEDGE.search(head) and not NOT_YET.search(head) and not HOLDINGS.search(head) and not EVERGREEN.search(head) and (sig.kind in ("structure", "flows") or str(sig.why).startswith("market-wide swing")):
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
                    r"(?:\d+|one|two|three|five|ten)-year (?:high|low)|in a year|"
                    r"unexpected\w*|surpris\w*|contracts?|contracted|recession)\b")
INDEX_REGIME = _rx(r"\b(?:enters?|entered|in|into|tips? into)\s+(?:a )?(correction|bear market)\b|\bcircuit breakers?\b|\btrading (?:halted|halt)\b|"
                   r"\bworst (?:day|week|month) since\b")
NOT_YET = _rx(r"\b(stud(?:y|ies|ying)|pilots?|piloting|trials?|testing|to test|tests|explor\w*|consider\w*|plans? to|planning|aims? to|to help|"
              r"research\w*|evaluat\w*|proof of concept|memorandum|mou|partnership to)\b")
PRICE_EXTREME = _rx(r"\b(record high|all-time high|new record|(?:lowest|highest) (?:level |close )?since (?:(?:january|february|march|april|may|june|july|"
                    r"august|september|october|november|december) )?(?:19|20)\d\d|\d+-year (?:high|low))\b")
MAJOR_ASSET = _rx(r"\b(xrp|solana|sol|gold|silver|dollar|yen|euro|treasur(?:y|ies)|10-year|bond yields?)\b")
FORECAST = _rx(r"\b(set for|set to|options show|expected to|poised to|projected to|forecast to|on track to|to (?:grow|rise|fall|reach|hit|top|double))\b")
REACTION = _rx(r"\b(reacts?|reacted|downplays?|blames?|breaks silence|experts|break down|explain\w*|joys|chaos and|could everything|what it'?s like|"
               r"fact check|how to|here'?s|eye opener|bullish|bearish|track record|what (?:it|this) means|history (?:says|shows)|demands?)\b")
EVERGREEN = _rx(r"\bvs\.?\b|\bversus\b|over the past (?:year|\d+ (?:days|weeks|months))|year-to-date|\bytd\b|this year so far")
HOLDINGS = _rx(r"\b(holdings?|stakes?|13f|filing shows|keeps?|kept)\b")
SUPPLY = _rx(r"\b(exports?|imports?|production|output|reserves|shipments|supply)\b")
MOVE_VERB = _rx(r"\b(falls?|fell|drops?|dropped|slides?|slid|plunges?|plunged|tumbles?|sinks?|rises?|rose|jumps?|jumped|surges?|surged|soars?|gains?|"
                r"climbs?|loses?|lost|slumps?|crash\w*|breaks?|hits?|sheds?)\b")


def subject_of(head: str) -> str:
    """The headline's subject: at most its first four words, before a move verb that comes early ("Nasdaq falls 2%" -> "Nasdaq"; "Harvard,
    Dartmouth keep Bitcoin ETF stakes steady as fund gains 42%": no early move, so no subject)."""
    main = main_clause_soft(head)
    m = MOVE_VERB.search(main)
    if not m or len(main[:m.start()].split()) > 4:
        return ""
    return main[:m.start()]


def main_clause_soft(head: str) -> str:
    """The main clause without cutting at commas ("China, Canada agree...", "Bitcoin, Ether lose...")."""
    return re.split(r"\s+as\s+|\s+after\s+|\s+amid\s+|\s+while\s+|;", head or "", maxsplit=1, flags=re.I)[0]


def main_clause(head: str) -> str:
    return re.split(r",|;|\s+as\s+|\s+after\s+|\s+amid\s+|\s+while\s+", head or "", maxsplit=1, flags=re.I)[0]
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
    from app.newsdesk import quality

    weak = structural_weak(head) or (REACTION.search(head).group(0) if REACTION.search(head) else None)
    if (hedged or weak) and strength != "none":  # a round-up, feature, reaction or live page never qualifies, whatever figure it quotes
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
    return audience_gate(Assessment(True, strength, why, "", score, parts, ev), title, summary)


STOP_SCROLL = 60


# --- what already went out, on every lane -------------------------------------------------------------------------------------------


@dataclass
class Told:
    lane: str  # telegram | x-newsdesk | x-trending | released
    ref: str
    title: str
    at: datetime
    event: Event


REPEAT_WINDOW = {"conflict": timedelta(hours=48), "disruption": timedelta(hours=96), "market": timedelta(hours=24), "macro": timedelta(hours=72)}
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


def judge(db: Any, title: str, summary: str = "", now: datetime | None = None, *, relevant: bool = True, age_min: float | None = None,
          outlets: int = 0, **skip: Any) -> Assessment:
    """assess() with the cross-lane history: the one call every lane makes before it publishes. A story with no stated figure can still
    qualify through the separate exceptional-story path (exceptional())."""
    from app.db.base import utcnow

    now = now or utcnow()
    history = told(db, now, **skip)
    rep = repeat_of(title, summary, history, now)
    a = assess(title, summary, relevant=relevant, age_min=age_min, repeat=rep)
    if not a.ok and not rep and a.strength == "none" and (dev := development_of(title, summary, history, now)):
        # a substantial development of a story already out IS the reason to care: losses doubled, the founder arrested, withdrawals halted
        parts = {**a.parts, "consequence": 45}
        return audience_gate(Assessment(True, "strong", dev, "", sum(parts.values()), parts, a.event), title, summary)
    return qualify(title, summary, outlets=outlets, base=a, repeat=rep)


def qualify(title: str, summary: str = "", *, outlets: int = 0, base: Assessment | None = None, repeat: str = "") -> Assessment:
    """The consequence path, then (only if that says no, and never for a repeat) the exceptional-story path."""
    a = base or assess(title, summary, repeat=repeat)
    if a.ok or repeat or a.strength != "none":
        return a
    e = exceptional(title, summary, outlets=outlets)
    if not e.ok:
        x = extraordinary(title, summary, outlets=outlets) if EXTRAORDINARY_LIVE else None
        if x is not None and x.ok:
            parts = {**a.parts, "consequence": 45}
            return audience_gate(Assessment(True, "extraordinary", x.why, "", sum(parts.values()), parts, a.event), title, summary)
        return a
    parts = {**a.parts, "consequence": 45}
    return audience_gate(Assessment(True, "exceptional", e.why, "", sum(parts.values()), parts, a.event), title, summary)


ANY_FIRM_HARD = _rx(r"\b(arrests?|arrested|charged|indicted|bankrupt|files? for bankruptcy|insolven\w*|(?:halt|suspend|paus|freez)\w* (?:all )?withdrawals)\b")


# --- the exceptional-story path (owner, 2026-11-18, second round) --------------------------------------------------------------------
# Replay of 270 real candidates (11-15..18): the consequence path missed stories with no figure that a sharp desk carries - Estonia closing
# its Russia border after sabotage, the Supreme Court ending Custodia Bank's Fed-account fight, a judge acquitting the MIT brothers in the
# $25M MEV case, California suing OpenAI, the Apple and US bank outages, the EU sanctioning Russia's richest woman, an ongoing cyberattack on
# Equinor. What they share is not a keyword: a NAMED institution with real power COMPLETED a decisive act (a court ruled, a state closed a
# border, a regulator sued), or a system people depend on is failing NOW, or critical infrastructure is under attack NOW - verified by two
# or more outlets, stated as fact, new, and on this account's beat.


@dataclass
class Exceptional:
    ok: bool
    why: str
    reason: str


AUTHORITY = _rx(r"\b(supreme court|high court|appeals court|federal court|court|judge|jury|regulators?|sec|cftc|doj|justice department|fbi|ftc|fca|esma|"
                r"european commission|eu|congress|senate|parliament|government|cabinet|ministry|treasury|white house|pentagon|central bank|fed|ecb|nato|"
                r"un security council|attorneys? general|california|texas|new york|florida|"
                r"estonia|latvia|lithuania|finland|poland|germany|france|britain|uk|japan|china|canada|mexico|india|brazil|australia|south korea|"
                r"israel|iran|russia|ukraine|turkey|saudi arabia|uae|qatar|egypt|norway|sweden|denmark|netherlands|italy|spain|switzerland|singapore|"
                r"taiwan|vietnam|argentina|us|u\.s\.|united states|beijing|moscow|washington|tokyo|brussels|ottawa|london)\b")
DECISIVE = _rx(r"\b(rules (?:that|against|in favou?r|for)|ruled|ends?|ended|strikes? down|upholds?|overturns?|acquits?|acquitted|convicts?|convicted|sentences?|sentenced|sues|sued|"
               r"charges|charged|indicts?|indicted|fines?|fined|bans?|banned|blocks|blocked|orders|ordered|approves?|approved|rejects|rejected|"
               r"closes?|closed|to close|shuts?|expels?|expelled|sanctions|sanctioned|seizes?|seized|freezes?|froze|nationali[sz]es|imposes?|imposed|"
               r"lifts|lifted|agree(?:s|d)? to (?:\w+ ){0,2}(?:cut|reduce|lift|end|remove|scrap)|passes|passed|signs|signed|recogni[sz]es|declares?|"
               r"arrests|arrested|raids?|raided|recalls?|expels)\b")
SYSTEMIC = _rx(r"\b(apple|icloud|microsoft|azure|google|aws|amazon web services|cloudflare|meta|whatsapp|instagram|visa|mastercard|paypal|chase|jpmorgan|"
               r"bank of america|wells fargo|citi(?:bank|group)?|hsbc|barclays|major (?:us |u\.s\. )?banks|banks|united airlines|american airlines|delta|"
               r"southwest|airlines|faa|air traffic control|nyse|nasdaq|cme|binance|coinbase|schwab|fidelity|robinhood|starlink|verizon|at&t|t-mobile|"
               r"national grid|power grid)\b")
OUTAGE = _rx(r"\b(outages?|is down|are down|went down|goes down|disruption|disrupted|glitch(?:es)?|system failure|offline|crash(?:ed|es)?)\b")
CYBER = _rx(r"\b(cyber ?attack\w*|hack(?:ed|ers|s)?|breach(?:ed)?|ransomware)\b")
CRITICAL = _rx(r"\b(oil|gas|energy|grid|pipeline|port|airport|hospitals?|central bank|ministry|government|military|nuclear|water|"
               r"\d{2,}\s+(?:major\s+)?(?:websites|companies|banks|organi[sz]ations|hospitals|firms))\b")
MODAL = _rx(r"\b(can|could|would|will|may|might|should|must|help|helps|helping|wants? to|hopes? to|ready to)\b")
OFF_BEAT = _rx(r"\b(nba|nfl|nhl|mlb|uefa|fifa|premier league|james bond|oscars?|grammys?|album|box office|celebrity|royal family)\b")


def exceptional(title: str, summary: str = "", *, outlets: int = 0, **_: Any) -> Exceptional:
    """Exceptional without a figure. All of: two or more outlets (or the caller has already verified it), stated as fact (no hedge, no
    forecast, no study or plan), something that HAPPENED (not talk, a reaction or a feature), on the beat; and one of:
      an institution's decisive act - a named court, regulator, legislature or state is the subject and the verb is completed ("Supreme
        Court ends...", "Judge acquits...", "California sues...", "Estonia to close Russia border" - a decision announced);
      a systemic outage now - a service people depend on (Apple, the big banks, an airline, the FAA, an exchange) failing, not "resolved";
      critical infrastructure under cyberattack now - energy, grid, ports, hospitals, government, or dozens of organisations at once."""
    head = title or ""
    main = main_clause_soft(head)
    from app.newsdesk import quality

    if outlets == 1:
        return Exceptional(False, "", "one outlet: an exceptional story needs two or more independent outlets")
    if HEDGE.search(head) or FORECAST.search(head) or NOT_YET.search(head) or NOT_HAPPENED.search(main):
        return Exceptional(False, "", "speculative, a plan or a forecast: nothing has happened yet")
    if REACTION.search(head) or structural_weak(head) or OFF_BEAT.search(head):
        return Exceptional(False, "", "a reaction, feature or off-beat story")
    words = main.split()
    lead = " ".join(words[:6])
    # the institution must be the SUBJECT and its decisive verb must follow at once ("Supreme Court ends", "China, Canada agree to cut");
    # "UK energy giant quits North Sea over tax raids" is not the UK raiding anything
    if (actor := AUTHORITY.search(lead)) and (act := DECISIVE.search(main[actor.end():])) and len(main[actor.end():actor.end() + act.start()].split()) <= 3 \
            and not ROUTINE.search(main[:act.start() + actor.end()]) and not MODAL.search(main[actor.end():actor.end() + act.start()]):
        return Exceptional(True, f"exceptional: {actor.group(0)} {act.group(0)} (a decisive act by an institution)", "")
    if SYSTEMIC.search(main) and OUTAGE.search(main) and not OVER.search(head):
        return Exceptional(True, f"exceptional: {SYSTEMIC.search(main).group(0)} outage, happening now", "")  # type: ignore[union-attr]
    if CYBER.search(main) and CRITICAL.search(head) and not OVER.search(head):
        return Exceptional(True, f"exceptional: cyberattack on {CRITICAL.search(head).group(0)}, ongoing", "")  # type: ignore[union-attr]
    return Exceptional(False, "", "no figure, and not an institution's decisive act, a systemic outage or a critical cyberattack")


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


def structural_weak(head: str) -> str | None:
    """A round-up or a feature, whatever figure it quotes. Only quality's structural checks: its word-list filler rule stays in the News
    Desk's own release rules (here it read "fake job interviews" in a $90M hack as an interview feature)."""
    from app.newsdesk import quality

    t = (head or "").strip()
    if quality.is_roundup(t):
        return "a round-up of several stories, not one story"
    return quality.feature(t)


# --- would OUR audience care? (owner, 2026-11-19) ---------------------------------------------------------------------------------------
# "Price Chat's Telegram channel still looks like a boring automated RSS feed ... Stop publishing ordinary news simply because it's
# verified or geopolitically significant. Before publishing, ask: why would our crypto, markets and world-news audience actually care?"
# Audit of the last 30 real posts (167-198): Telegram 197 "U.S., China can help end Ukraine war, Trump tells Xi before trilateral with
# Putin" (a remark, through the exceptional path) and 198 "Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south" (an
# interception with nothing hit, through "a state strikes another") were verified and significant, and nobody's reason to open the channel.
#
# A consequence (assess/exceptional) is necessary; it is not enough. The story must also carry an ANGLE this audience cares about, stated in
# the story itself: a crash or pump, money on the scale of billions, a market regime, a major attack (a state strikes a state and it lands),
# casualties, a scandal (arrests, charges, investigations, frozen or seized money), a genuine surprise (first, record, unexpected), or a
# direct hit on crypto, markets or energy. Each angle adds to the audience-interest score and names the specific reason to publish. Routine
# military updates (missiles intercepted, nothing hit) and diplomacy talk (remarks, meetings, "can help", "ahead of the summit") are refused
# outright unless an outcome is stated (a deal signed, sanctions imposed, a strike that landed). Verification is never an angle.

INTEREST_BAR = 60
INTEREST_BASE = {"major": 70, "strong": 50, "exceptional": 50, "extraordinary": 50}
INTERCEPTED = _rx(r"\b(intercept\w*|shoots? down|shot down|downs|downed|repels?|repelled|thwart\w*|foils?|foiled|repulse\w*)\b")
LANDED = _rx(r"\b(kill\w*|dead|deaths?|wounded|injur\w*|casualt\w*|damag\w*|ablaze|on fire|fire at|destroy\w*|hits?|hit by|struck|"
             r"blasts?|explosions? (?:at|in|hit)|impact(?:ed|s)? (?:on|at)|sets? off|shut(?:s)? down)\b")
DIPLOMACY = _rx(r"\b(tells?|told|says?|said|talks?|meets?|meeting|summit|trilateral|bilateral|ahead of|discuss\w*|urges?|calls? (?:on|for)|"
                r"can help|could help|to help|pushes? for|seeks?|warns?|vows?|signals?|envoy|visits?|visiting|hosts?|phone call|spoke)\b")
BEAT = _rx(r"\b(crypto\w*|bitcoin|btc|ether(?:eum)?|eth|stablecoins?|tokens?|memecoins?|defi|exchanges?|binance|coinbase|tether|circle|"
           r"stocks?|shares|equities|nasdaq|s&p|dow|wall street|markets?|traders?|investors?|oil|crude|brent|wti|gas|gold|silver|dollar|"
           r"yields?|bonds?|treasur(?:y|ies)|fed|ecb|central bank|rates?|inflation|tariffs?|sanctions|liquidat\w*|etfs?|hedge funds?|banks?|"
           r"hormuz|shipping|tankers?|refiner(?:y|ies)|pipeline|ipo|earnings|revenue|nvidia|apple|microsoft|tesla|amazon|google|alphabet|meta|"
           r"openai|tsmc|samsung)\b")
SCANDAL = _rx(r"\b(arrest\w*|charged|charges|indict\w*|convict\w*|fraud\w*|scandal|affair|spy|spies|espionage|probe[sd]?|investigat\w*|"
              r"subpoena\w*|raid(?:ed|s)?|launder\w*|brib\w*|embezzl\w*|ponzi|seiz\w*|freez\w*|frozen|court order|sued|sues|lawsuit|"
              r"heist|rug ?pull\w*|drain(?:s|ed)?|scam\w*|collaps\w*|insolven\w*|bankrupt\w*|hack(?:ed|ers?)?|exploit\w*|stolen|theft|insider trading|manipulat\w*|acquit\w*|verdict|trial|sentenc\w*)\b")
AGREED = _rx(r"\b(?:agree(?:s|d)?|sign(?:s|ed)?|seal(?:s|ed)?|strikes?|struck|reach(?:es|ed)?)\b (?:a |an |the )?(?:new |final )?"
              r"(?:to (?:further )?(?!(?:talk|meet|discuss|hold|resume|continue|consider|explore|study)\b)\w+|deal|agreement|accord|treaty|pact)\b")
HOSTILE = _rx(r"\b(clos(?:e|es|ed|ing)|shuts?|expel\w*|seiz\w*|ban(?:s|ned)?|sanction\w*|nationali[sz]\w*|blockade\w*|declares?)\b")
SURPRISE = _rx(r"\b(unexpected\w*|surpris\w*|shock\w*|first(?:-ever)?|record|biggest|largest|worst|historic|unprecedented|"
               r"since (?:19|20)\d\d|in (?:\d+|two|three|four|five|ten) (?:years|decades)|\d+-year (?:high|low)|all-time)\b")
MARKET_ANGLE = _rx(r"\b(crash\w*|plunge\w*|tumbl\w*|soar\w*|surg\w*|rout|sell-?off|liquidat\w*|correction|bear market|"
                   r"breaks? (?:below|above)|record high|all-time high)\b")


LEADERS = _rx(r"\b(trump|biden|xi|putin|zelensk\w*|netanyahu|modi|erdogan|macron|starmer|merz|khamenei|kim jong un|mbs|bin salman|"
              r"president|prime minister|foreign minister|chancellor|secretary of state|rubio|vance|lavrov)\b")


STATE_NAMES = {"US": "the US", "UAE": "the UAE", "NKOREA": "North Korea", "SAUDI": "Saudi Arabia", "YEMEN": "the Houthis", "NATO": "NATO"}


GIANT = _rx(r"\b(nyse|nasdaq|cme|dtcc|swift|lseg|deutsche b(?:ö|oe|o)rse|ice|blackrock|vanguard|fidelity|jpmorgan|jp morgan|goldman(?: sachs)?|"
            r"morgan stanley|citi(?:group|bank)?|bank of america|wells fargo|hsbc|barclays|ubs|deutsche bank|bny(?: mellon)?|state street|"
            r"federal reserve|fed|ecb|bank of england|central bank|treasury)\b")


def _big_move(head: str, pct: float) -> str:
    """Which asset made a move big enough for this audience, by asset class (owner, 2026-11-18: a 3% stock move is not news)."""
    if not pct:
        return ""
    for rx, at in ((INDEX, 2), (CRYPTO, 5), (OIL, 5), (MEGA, 6), (MAJOR_ASSET, 3)):
        if (m := rx.search(head)) and pct >= at:
            return m.group(0)
    return ""


def audience(title: str, summary: str, a: Assessment) -> tuple[int, str, str]:
    """(score 0-100, the specific reason to publish, why not). The base is the consequence's strength; every angle the story itself states
    adds to it and is named; routine military updates and diplomacy talk are refused outright. Nothing is invented: every angle is a match
    in the headline (or, for figures, its own lead sentence)."""
    head = title or ""
    ev = a.event or event_of(title, summary)
    main = main_clause(head)
    score = INTEREST_BASE.get(a.strength, 0)
    # refusals first: the forms that read as news and are not
    if ev.kin() == "conflict" and (m := INTERCEPTED.search(head)) and not LANDED.search(head) and not WAR_MAJOR.search(head) and ev.deaths == 0 \
            and not (SURPRISE.search(head) and SURPRISE.search(head).group(0).lower().startswith("first")):  # type: ignore[union-attr]
        return score - 30, "", f"a routine military update: {m.group(0)}, with nothing hit and no casualties stated"
    outcome = PEACE_DONE.search(head) or SANCTION_ACT.search(head) or RATE_ACT.search(head) or HARD_OUTCOME.search(head) or ev.deaths or ev.pct \
        or ev.money >= 1e9 or (ev.kin() == "conflict" and HARD_CONFLICT.search(main) and not INTERCEPTED.search(main)) or AGREED.search(head)
    if (m := DIPLOMACY.search(head)) and not outcome and (ev.states or LEADERS.search(head) or ev.kin() == "conflict"):
        return score - 30, "", f"diplomacy talk ({m.group(0)}): remarks, meetings or offers, with no signed outcome or consequence"
    angles: list[tuple[int, str]] = []
    if (asset := _big_move(head, ev.pct)) and not EVERGREEN.search(head) and not FORECAST.search(head):
        angles.append((15, f"a {ev.pct:g}% move in {asset}"))
    elif m := MARKET_ANGLE.search(head):
        angles.append((15, f"a market shock ({m.group(0)})"))
    if ev.money >= 1e9 and AFTERMATH.search(head):  # "after $6.3B heist": the scale of the case, not new money
        angles.append((10, f"the ${ev.money / 1e9:g}B {AFTERMATH.search(head).group(0).split()[-1]} case"))  # type: ignore[union-attr]
    elif ev.money >= 10e9:
        angles.append((20, f"${ev.money / 1e9:g}B at stake"))
    elif ev.money >= 1e9:
        angles.append((15, f"${ev.money / 1e9:g}B at stake"))
    elif ev.money >= 100e6:
        angles.append((10, f"${ev.money / 1e6:,.0f}M at stake"))
    elif (mm := money_of(head)) >= 1e9:  # an aftermath's figure ("after $6.3B heist"): the scale of the story, not new money
        angles.append((10, f"the ${mm / 1e9:g}B case"))
    if ev.deaths >= 10:
        angles.append((15, f"{ev.deaths} killed"))
    if ev.kin() == "conflict" and len(ev.states) >= 2 and HARD_CONFLICT.search(main) and not INTERCEPTED.search(main):
        angles.append((15, f"a major attack: {', '.join(STATE_NAMES.get(x, x.title()) for x in sorted(ev.states))}"))
    if WAR_MAJOR.search(head) or CHOKE_CLOSED.search(head) or INFRA_HIT.search(head):
        angles.append((15, f"escalation: {(WAR_MAJOR.search(head) or CHOKE_CLOSED.search(head) or INFRA_HIT.search(head)).group(0)}"))  # type: ignore[union-attr]
    if m := SCANDAL.search(head):
        angles.append((10, f"a scandal or crime: {m.group(0)}"))
    if "disruption, happening now" in (a.why or "") and re.search(OUTAGE.pattern + r"|\bground stop\b|\bgrounded\b|\bflights? (?:cancel|halt)\w*",
                                                                     main_clause_soft(head), re.I):  # the outage itself, not its paperwork  # Apple Pay, iCloud and the App Store down at once: readers feel it now
        angles.append((15, a.why))
    if ev.family == "disaster" and a.strength in ("major", "strong"):  # a magnitude 7+ quake, a deadly crash
        angles.append((15, f"a major disaster: {a.why}"))
    if (m := GIANT.search(" ".join(main_clause_soft(head).split()[:5]))) and str(a.why).endswith("on crypto rails"):
        angles.append((10, f"a market giant moves onto crypto rails: {m.group(0)}"))  # the NYSE settling on a blockchain, not a payments add-on
    if (m := CORP_HARD.search(head) or HARD_OUTCOME.search(head)) and not HEDGE.search(head):  # a collapse, an arrest, a resignation, halted withdrawals
        angles.append((10, f"a hard outcome: {m.group(0)}"))
    if m := (SURPRISE.search(head) or RELATIVE.search(head)):
        angles.append((10, f"a surprise: {m.group(0)}"))
    if PEACE_DONE.search(head) or SANCTION_ACT.search(head) or RATE_ACT.search(head):
        angles.append((10, "a decision that moves markets"))
    # the exceptional path's three kinds (exceptional()): a system people depend on failing now and critical infrastructure under attack
    # always matter to this audience; an institution's decisive act only when it lands on their markets, is a crime or case, or is hostile
    why = a.why or ""
    if why.startswith("extraordinary: "):  # a discovery, a revelation, a first: the reason itself
        angles.append((15, why.removeprefix("extraordinary: ")))
    if why.endswith("outage, happening now"):
        angles.append((15, why.removeprefix("exceptional: ")))
    elif why.startswith("exceptional: cyberattack"):
        angles.append((15, why.removeprefix("exceptional: ")))
    elif why.endswith("(a decisive act by an institution)") and (BEAT.search(head) or SCANDAL.search(head) or HOSTILE.search(head)):
        angles.append((10, f"a decisive act: {why.removeprefix('exceptional: ').removesuffix(' (a decisive act by an institution)')}"))
    if not angles:  # being on the beat is not a reason on its own: a routine stablecoin partnership is still routine
        return score, "", (f"{a.why or 'on the beat'}, but routine: no crash or pump, no billions, no major attack, casualties, scandal, surprise "
                           f"or market-moving decision for a crypto, markets or world-news reader")
    angles.sort(key=lambda x: -x[0])
    if m := BEAT.search(head):
        angles.append((10, f"on our readers' markets: {m.group(0)}"))
    score = min(100, score + sum(p for p, _ in angles))
    pitch = "; ".join(w for _, w in [*angles[:2], *angles[-1:]] if w) if len(angles) > 2 else "; ".join(w for _, w in angles)
    if score < INTEREST_BAR:
        return score, pitch, (f"audience interest {score} < {INTEREST_BAR}: {a.why}, but nothing that makes a crypto, markets or world-news reader care "
                              f"(no crash or pump, no billions, no major attack, casualties, scandal or surprise)")
    return score, pitch, ""


def audience_gate(a: Assessment, title: str, summary: str = "") -> Assessment:
    """Every path that says yes (consequence, development, exceptional) ends here. A refusal is strength "none": nothing downstream (the
    fast lane, the breaking bypass, the last check before a send) treats it as a consequence."""
    score, pitch, why_not = audience(title, summary, a)
    if why_not:
        return Assessment(False, "none", a.why, f"why would our audience care? {why_not}", a.stop_scroll, a.parts, a.event, score, pitch)
    extra = a.why and not a.why.startswith(("exceptional:", "extraordinary:")) and a.why not in pitch and "a state strikes another" not in a.why
    return Assessment(True, a.strength, a.why, "", a.stop_scroll, a.parts, a.event, score, f"{pitch} ({a.why})" if extra else pitch)


# --- the extraordinary story (owner, 2026-11-19, validation round) ------------------------------------------------------------------------
# "Add a carefully verified extraordinary-news category for discoveries, bizarre events, major revelations and unexpected developments. Don't
# let ordinary speculation or conspiracy claims pass as facts." Stories with no figure that a reader would still forward: a discovery stated
# as found, documents that reveal, something done by mistake on a vast scale, a first in history. Verified like the exceptional path (two or
# more outlets), stated as fact (no hedge, forecast, plan, study-suggests, claim or theory), something that HAPPENED, on any beat but sport
# and celebrity.
# EXTRAORDINARY_LIVE is False until the next real candidates show it catches what the gate misses without letting junk through: until then
# it is judged and logged for every candidate ("editorial candidate:" lines) but publishes nothing.
EXTRAORDINARY_LIVE = False

FINDERS = r"(?:scientists?|researchers?|astronomers?|archaeologists?|physicists?|biologists?|geologists?|palaeontologists?|paleontologists?|" \
          r"doctors|surgeons|engineers|divers|miners|explorers|nasa|esa|cern|telescope|probe|rover|james webb|hubble|study|team)"
DISCOVERY = _rx(r"\b(discover(?:s|ed|y|ies)?|find(?:s)?|found|unearth(?:s|ed)?|detect(?:s|ed)|identif(?:y|ies|ied)|confirm(?:s|ed)|spot(?:s|ted)|"
                r"recover(?:s|ed)|uncover(?:s|ed)|sequenc(?:es|ed)|decod(?:es|ed)|photograph(?:s|ed))\b")
REVELATION = _rx(r"\b(leaked|leaks?|unsealed|declassified|(?:documents|records|emails|files|messages|filings|recordings|data) (?:show|reveal)s?|"
                 r"reveal(?:s|ed)|expos(?:es|ed)|admits?|admitted|confess(?:es|ed)|secretly)\b")
UNEXPECTED = _rx(r"\b(accidentally|mistakenly|by mistake|erroneously|fat[- ]finger\w*|in error|bizarre|unprecedented|for the first time(?: ever| in (?:history|\d+ years))?|"
                 r"first[- ]ever|world'?s first|never before|abruptly|unexpectedly|out of nowhere|stuns?|stunned|shock(?:s|ed)? (?:the|markets|investors|traders))\b")
CONSPIRACY = _rx(r"\b(conspirac\w*|hoax\w*|theor(?:y|ies|ists?)|claims? without evidence|unverified|unconfirmed|alleged(?:ly)?|rumou?r\w*|"
                 r"psychic|prophec\w*|prediction|predicts?|flat earth|cover-?up claims?|sighting|sightings|mystery (?:object|drone)s?|"
                 r"may|might|could|possibly|suggests?|hints?|appears? to|seems? to|believed to|thought to|likely|claims?|claimed|says? it (?:found|has))\b")


def extraordinary(title: str, summary: str = "", *, outlets: int = 0) -> Exceptional:
    """Extraordinary without a figure: a discovery, a revelation or an unexpected development, stated as fact by two or more outlets."""
    head = title or ""
    main = main_clause_soft(head)
    if outlets < 2:
        return Exceptional(False, "", "an extraordinary story needs two or more independent outlets")
    if CONSPIRACY.search(head) or HEDGE.search(head) or FORECAST.search(head) or NOT_YET.search(head) or NOT_HAPPENED.search(main):
        return Exceptional(False, "", "speculation, a claim or a theory: nothing established yet")
    if REACTION.search(head) or structural_weak(head) or OFF_BEAT.search(head) or EVERGREEN.search(head):
        return Exceptional(False, "", "a reaction, feature or off-beat story")
    if (m := DISCOVERY.search(main)) and re.search(rf"\b{FINDERS}\b", main, re.I):
        return Exceptional(True, f"extraordinary: a discovery ({m.group(0)})", "")
    if m := REVELATION.search(main):
        return Exceptional(True, f"extraordinary: a revelation ({m.group(0)})", "")
    if m := UNEXPECTED.search(head):
        return Exceptional(True, f"extraordinary: unexpected ({m.group(0)})", "")
    return Exceptional(False, "", "not a discovery, a revelation or an unexpected development")


def shadow(title: str, summary: str = "", *, outlets: int = 0, evidence: str = "") -> dict[str, Any]:
    """Everything the gate would say about a candidate, for the production log (measurement, owner 2026-11-19): the live decision, the
    audience score and reason, and what the extraordinary path would add while it is not live."""
    a = qualify(title, summary, outlets=outlets)
    x = extraordinary(title, summary, outlets=outlets)
    xa = audience_gate(Assessment(True, "extraordinary", x.why, "", 0, {}, event_of(title, summary)), title, summary) if x.ok and not a.ok else None
    return {"decision": "PUBLISH" if a.ok else "REJECT", "interest": a.interest, "strength": a.strength, "evidence": evidence, "outlets": outlets,
            "why": a.pitch if a.ok else a.reason, "extraordinary": (x.why if x.ok else x.reason),
            "extraordinary_would_publish": bool(xa is not None and xa.ok), "extraordinary_interest": xa.interest if xa is not None else 0, "live": EXTRAORDINARY_LIVE, "title": title[:200]}
