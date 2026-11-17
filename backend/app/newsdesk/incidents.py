"""Major infrastructure incidents: banking outages, payment disruptions, exchange failures, airline groundings, cloud and email outages,
cyberattacks, telecom and power failures (owner, 2026-11-17).

Production missed three in one week because no market keyword was in their headlines, so the News Desk dropped them before they were
stored and Trending never saw them:
- 2026-11-12 Microsoft 365 / Outlook / Teams outage ("A negative Outlook: Microsoft scrambles to fix email outage", NPR);
- 2026-11-13 the FAA's nationwide ground stop ("Nationwide FAA ground stop lifted but cancellations...", Fox Business);
- 2026-11-16 JPMorgan Chase's software-bug outage and the reports at other big banks ("JPMorgan Chase systems recovering following
  hourslong nationwide technical outage", Fox Business 18:51 UTC; "JPMorgan Chase says temporary software bug is affecting consumer
  banking", CNBC 22:14 UTC).

detect() reads ONE headline and says what kind of incident it tells, at whom, how widely, and whether it is the event itself or its
aftermath. It never connects two headlines on its own: same_incident() does, and only for the same kind of incident at the same named
company (or two sector-wide reports of the same kind). Chase and "major US banks", Microsoft and the FAA, United Airlines and the FAA stay
apart: separate outages are not assumed to be connected.

Deterministic, no network, no model. What it finds is evidence for ranking and Admin; every publishing gate still applies."""

import re
from dataclasses import dataclass, field

_rx = lambda p: re.compile(p, re.I)  # noqa: E731

# The incident kinds and who they hit. A name counts only as a whole word; "Chase" alone only next to banking context ("police chase").
ENTITIES: dict[str, dict[str, re.Pattern[str]]] = {
    "banking": {
        "JPMorgan Chase": _rx(r"\bjp ?morgan(?: chase)?\b|\bchase(?: bank)?(?=(?:'s|’s)? (?:customers?|users?|app|online|mobile|website|accounts?|banking|systems?|outage|"
                              r"down|issues?|problems?|glitch|services?|atms?|clients?|says|said|confirms?)\b)|\bchase bank\b|\bchase,"),
        "Bank of America": _rx(r"\bbank of america\b|\bbofa\b"),
        "Wells Fargo": _rx(r"\bwells fargo\b"),
        "U.S. Bank": _rx(r"\bu\.?s\.? bank(?:corp)?\b|\bu\.?s\.? bancorp\b"),
        "Citi": _rx(r"\bciti(?:group|bank)?\b"),
        "Capital One": _rx(r"\bcapital one\b"),
        "PNC": _rx(r"\bpnc\b"),
        "Truist": _rx(r"\btruist\b"),
        "TD Bank": _rx(r"\btd bank\b"),
        "Goldman Sachs": _rx(r"\bgoldman sachs\b"),
        "Morgan Stanley": _rx(r"\bmorgan stanley\b"),
        "HSBC": _rx(r"\bhsbc\b"),
        "Barclays": _rx(r"\bbarclays\b"),
        "Lloyds": _rx(r"\blloyds\b"),
        "NatWest": _rx(r"\bnatwest\b"),
        "Santander": _rx(r"\bsantander\b"),
        "Navy Federal": _rx(r"\bnavy federal\b"),
    },
    "payments": {
        "Zelle": _rx(r"\bzelle\b"), "Venmo": _rx(r"\bvenmo\b"), "PayPal": _rx(r"\bpaypal\b"), "Cash App": _rx(r"\bcash app\b"),
        "Visa": _rx(r"\bvisa(?= (?:card|cards|payments?|network|outage|down|systems?)\b)|\bvisa's\b"), "Mastercard": _rx(r"\bmastercard\b"),
        "American Express": _rx(r"\bamerican express\b|\bamex\b"), "Stripe": _rx(r"\bstripe\b"), "Square": _rx(r"\bsquare (?:outage|payments?|down|confirms)\b"),
        "Fedwire": _rx(r"\bfedwire\b"), "SWIFT": _rx(r"\bswift (?:network|payments?|system|messaging)\b"), "Plaid": _rx(r"\bplaid\b"),
        "Apple Pay": _rx(r"\bapple pay\b"), "Google Pay": _rx(r"\bgoogle pay\b"),
    },
    "exchange": {
        "NYSE": _rx(r"\bnyse\b|\bnew york stock exchange\b"), "Nasdaq": _rx(r"\bnasdaq(?= (?:outage|glitch|halts?|halted|systems?|exchange|trading (?:halt|glitch|outage)))"),
        "CME": _rx(r"\bcme(?: group| globex)?\b"), "Cboe": _rx(r"\bcboe\b"), "London Stock Exchange": _rx(r"\blondon stock exchange\b|\blse\b"),
        "Coinbase": _rx(r"\bcoinbase\b"), "Binance": _rx(r"\bbinance\b"), "Kraken": _rx(r"\bkraken\b"), "Robinhood": _rx(r"\brobinhood\b"),
        "Charles Schwab": _rx(r"\bschwab\b"), "Fidelity": _rx(r"\bfidelity\b"), "Interactive Brokers": _rx(r"\binteractive brokers\b"),
        "Bybit": _rx(r"\bbybit\b"), "OKX": _rx(r"\bokx\b"), "Hyperliquid": _rx(r"\bhyperliquid\b"),
        "Gemini": _rx(r"\bgemini(?: exchange)?(?= confirms| exchange| outage| users| customers| trading)"), "Crypto.com": _rx(r"\bcrypto\.com\b"),
        "Bitstamp": _rx(r"\bbitstamp\b"),
    },
    "aviation": {
        "FAA": _rx(r"\bfaa(?:'s|’s)?\b|\bfederal aviation administration\b"), "air traffic control": _rx(r"\bair traffic control\w*\b|\bair traffic controllers?\b"),
        "United Airlines": _rx(r"\bunited airlines\b"), "American Airlines": _rx(r"\bamerican airlines\b"), "Delta": _rx(r"\bdelta(?: air lines| airlines)?(?= (?:flights?|outage|grounds?|systems?|says|ground stop))|\bdelta air lines\b"),
        "Southwest": _rx(r"\bsouthwest(?: airlines)?(?= (?:flights?|outage|grounds?|systems?|says|ground stop|airlines))"), "JetBlue": _rx(r"\bjetblue\b"),
        "Alaska Airlines": _rx(r"\balaska airlines\b"), "Spirit": _rx(r"\bspirit airlines\b"), "Frontier": _rx(r"\bfrontier airlines\b"),
        "Ryanair": _rx(r"\bryanair\b"), "British Airways": _rx(r"\bbritish airways\b"), "Lufthansa": _rx(r"\blufthansa\b"), "Air France": _rx(r"\bair france\b"),
        "Emirates": _rx(r"\bemirates (?:airline|flights?)\b"), "Heathrow": _rx(r"\bheathrow\b"), "NATS": _rx(r"\bnats\b"), "Eurocontrol": _rx(r"\beurocontrol\b"),
    },
    "cloud": {
        "Microsoft": _rx(r"\bmicrosoft(?: 365| azure| teams| outlook)?\b|\boutlook\b|\bazure\b|\bms teams\b|\bmicrosoft teams\b"),
        "AWS": _rx(r"\baws\b|\bamazon web services\b"), "Google": _rx(r"\bgoogle cloud\b|\bgmail\b|\bgoogle (?:services|search|workspace)\b|\byoutube\b"),
        "Cloudflare": _rx(r"\bcloudflare\b"), "CrowdStrike": _rx(r"\bcrowdstrike\b"), "Oracle": _rx(r"\boracle cloud\b"), "Salesforce": _rx(r"\bsalesforce\b|\bslack\b"),
        "GitHub": _rx(r"\bgithub\b"), "Akamai": _rx(r"\bakamai\b"), "Fastly": _rx(r"\bfastly\b"), "Apple": _rx(r"\bicloud\b|\bapple (?:services|outage)\b"),
        "Meta": _rx(r"\b(?:facebook|instagram|whatsapp)\b"), "X": _rx(r"\bx \(formerly twitter\)|\btwitter\b"), "OpenAI": _rx(r"\bchatgpt\b|\bopenai\b"), "Zoom": _rx(r"\bzoom\b"),
        "DigitalOcean": _rx(r"\bdigitalocean\b"), "Twilio": _rx(r"\btwilio\b"),
    },
    "telecom": {
        "AT&T": _rx(r"\bat&t\b|\bat&amp;t\b"), "Verizon": _rx(r"\bverizon\b"), "T-Mobile": _rx(r"\bt-mobile\b"), "Comcast": _rx(r"\bcomcast\b|\bxfinity\b"),
        "Spectrum": _rx(r"\bspectrum (?:outage|internet|down)\b"), "Starlink": _rx(r"\bstarlink\b"), "Vodafone": _rx(r"\bvodafone\b"), "BT": _rx(r"\bbt (?:broadband|outage)\b"),
    },
    "power": {"power grid": _rx(r"\b(?:power|electric(?:ity)?) grid\b|\bnational grid\b")},
}
# sector words: what a headline that names no company is about
SECTOR = {
    "banking": _rx(r"\bbanks?\b|\bbanking\b|\bbank customers\b|\batms?\b|\bdirect deposits?\b"),
    "payments": _rx(r"\bpayments? (?:network|system|systems|outage|processing)\b|\bcard payments?\b|\bpayment processor\w*\b"),
    "exchange": _rx(r"\bstock exchange\b|\btrading (?:halt\w*|glitch|outage)\b|\bexchange (?:outage|glitch)\b"),
    "aviation": _rx(r"\bflights?\b|\bairlines?\b|\bairports?\b|\bair travel\b|\bair traffic\b|\bpilots?\b|\bdepartures\b"),
    "cloud": _rx(r"\bemail\b|\bcloud\b|\bservers?\b|\bdata cent(?:er|re)s?\b|\binternet\b|\bwebsites?\b|\bapps?\b"),
    "telecom": _rx(r"\bcellular\b|\bmobile network\b|\bcell service\b|\bphone service\b|\b911\b|\bbroadband\b"),
    "power": _rx(r"\bpower\b|\belectricity\b|\bblackouts?\b"),
}
# what went wrong (the event itself)
DISRUPTION = _rx(
    r"\b(outages?|blackouts?|went down|goes down|is down|are down|was down|were down|down for (?:thousands|millions|hours|users|customers|many)|"
    r"service(?:s)? (?:disruption|disrupted|interruption|issues?|problems?)|disruptions?|disrupt(?:s|ed)\b|"
    r"system(?:s|wide)? (?:failure|outage|issues?|problems?|glitch|crash|went dark)|systemwide|technical (?:issues?|problems?|glitch|outage|failure|difficulties)|"
    r"glitch(?:es)?|software bug|it (?:failure|outage|meltdown)|meltdown|"
    r"ground stops?|grounds? (?:all |its )?(?:flights|planes|jets|fleet)|grounded|flights? (?:halted|suspended|grounded|cancell?ed)|halt(?:s|ed)? (?:all )?(?:flights|departures|trading)|"
    r"trading halt(?:ed)?|halts? trading|cyber ?attacks?|ransomware|data breach|breached|hacked|hackers?|malware attack|ddos|"
    r"can(?:'|’)?t (?:access|log ?in|move|get to|use|pay)|cannot (?:access|log ?in)|unable to (?:access|log ?in|pay|make|withdraw|use)|locked out|"
    r"(?:login|log-in|log in|payment|transaction|deposit)s? (?:issues?|problems?|failures?|delays?)|"
    r"went dark|offline|crash(?:es|ed)?)\b")
SCOPE = _rx(r"\b(nationwide|nation-wide|widespread|global(?:ly)?|worldwide|world-wide|across the (?:us|u\.s\.|country|globe|world)|massive|major|"
            r"thousands|millions|tens of thousands|hundreds of thousands|all (?:us |u\.s\. )?flights|every flight|several (?:large |major |big )?(?:us |u\.s\. )?banks|"
            r"multiple (?:banks|airlines|airports)|big banks|biggest banks|largest banks|hours-?long|hourslong|day-?long|days-?long|full ground stop)\b")
RESOLVED = _rx(r"\b(resolved|restored|recover(?:s|ed|ing)|back (?:online|up)|resum(?:es|ed|ing)|lifted|ends?|ended|fixed|returns? to normal|comes back)\b")
HEDGED = _rx(r"\b(reportedly|rumou?r\w*|unconfirmed|may|might|could|possible|possibly|potential|fears?|feared|risk of|if|would|threat\w*|warns? of|"
             r"claims? (?:to have|it)|alleged\w*)\b")
# the incident is background: a feature, an explainer, a retrospective or analysis about one (still evidence that it happened)
AFTERMATH = _rx(r"\b(what (?:we know|to know|caused|happened)|explain\w*|why (?:the|some|did|is|was)|how (?:the|to)|lessons?|warnings? before|years of warnings|"
                r"modernization|moderni[sz]e|aging|ageing|tiger team|head off another|prevent another|after (?:friday|thursday|monday|tuesday|wednesday|saturday|sunday)(?:'s|’s)? |"
                r"after last week|highlights?|shows? (?:dependence|how)|vulnerabilit\w+|fragile|questions? (?:remain|raised)|theor(?:y|ies)|"
                r"so (?:they|he|she|we) |bought a|buys? a|drove|drives? \d+|road trip|stranded passengers? share|key test|"
                r"who is|what is|inside |opinion|analysis|could everything|wonder|cost of|bill for|lawsuits? over|compensation|"
                # production 2026-11-17: "The Chaos and Unexpected Joys of a 19-Hour Global Email Outage" (a feature, days later)
                r"joys?|the chaos (?:and|of)|surviv\w+|learned|life without|\d+-hour (?:global )?(?:email |internet )?outage)\b")
# reassurance and denials are not the incident ("U.S. Bank systems are not directly impacted")
REASSURE = _rx(r"\b(not (?:directly )?(?:impacted|affected)|unaffected|no (?:outage|disruption|impact)|working normally|operating normally|denies (?:outage|any)|"
               # "Coinbase says 99% of customer crypto is offline and out of hackers' reach": cold storage, not an outage
               r"out of (?:\w+['’]?\s){0,2}reach|cold storage|safe from|\d+% of (?:customer |user )?(?:crypto|funds|assets|coins))")
# what is not an infrastructure incident at all, whatever the words: phishing campaigns, scam warnings, product bugs, stock picks
NOT_INCIDENT = _rx(r"\b(phishing campaign|scam (?:alert|warning)|how to protect|tips|stocks? to (?:buy|watch)|price target|upgrade|downgrade|earnings|"
                   r"says these stocks|stake in|police chase|car chase)\b")

FINANCIAL = {"banking", "payments", "exchange"}
# whose failure is national by itself: the country's airspace, the biggest banks, card networks and exchanges, the clouds everyone runs on
SYSTEMIC = {"FAA", "air traffic control", "JPMorgan Chase", "Bank of America", "Wells Fargo", "Citi", "Visa", "Mastercard", "Fedwire", "SWIFT", "Zelle",
            "NYSE", "Nasdaq", "CME", "Microsoft", "AWS", "Google", "Cloudflare", "CrowdStrike", "power grid"}
LABEL = {"banking": "banking outage", "payments": "payment disruption", "exchange": "exchange or trading failure", "aviation": "aviation disruption",
         "cloud": "cloud, email or app outage", "telecom": "telecom outage", "power": "power failure", "cyber": "cyberattack"}
# the flights themselves stopped: aviation, whoever else the headline names ("Tech issue grounds all US flights day after global Microsoft outage")
AVIATION_EVENT = _rx(r"\b(ground stops?|grounds? (?:all |its )?(?:us |u\.s\. )?(?:flights|planes|jets|fleet)|flights? (?:halted|suspended|grounded)|halt(?:s|ed)? (?:all )?(?:flights|departures))\b")
CYBER = _rx(r"\b(cyber ?attacks?|ransomware|data breach|breached|hacked|hackers?|malware attack|ddos)\b")


@dataclass
class Incident:
    kind: str  # banking | payments | exchange | aviation | cloud | telecom | power | cyber
    entities: frozenset[str] = field(default_factory=frozenset)
    scope: bool = False  # nationwide / widespread / thousands / several banks...
    aftermath: bool = False  # a feature, explainer or retrospective about it, not the event
    hedged: bool = False
    resolved: bool = False
    disruption: str = ""

    @property
    def major(self) -> bool:
        """The event itself, told plainly, at a named major company or service, or sector-wide at national scale."""
        return not self.aftermath and not self.hedged and (bool(self.entities) or self.scope)

    @property
    def financial(self) -> bool:
        return self.kind in FINANCIAL

    @property
    def systemic(self) -> bool:
        """National in scale whatever the headline says: a systemic provider, or a widespread failure."""
        return self.scope or bool(self.entities & SYSTEMIC)

    @property
    def label(self) -> str:
        return LABEL.get(self.kind, "infrastructure incident")

    def why(self) -> str:
        who = ", ".join(sorted(self.entities)) or ("sector-wide" if self.scope else "unnamed")
        bits = [self.label, who]
        if self.scope and self.entities:
            bits.append("widespread")
        if self.resolved:
            bits.append("resolved or recovering")
        if self.aftermath:
            bits.append("aftermath, not the event")
        if self.hedged:
            bits.append("hedged")
        return " · ".join(bits)

    def out(self) -> dict[str, object]:
        return {"kind": self.kind, "label": self.label, "entities": sorted(self.entities), "scope": self.scope, "major": self.major,
                "aftermath": self.aftermath, "hedged": self.hedged, "resolved": self.resolved, "why": self.why()}


def detect(title: str, summary: str = "") -> Incident | None:
    """The incident one headline tells, or None. Read from the headline (the summary only fills in WHO when the headline is generic)."""
    t = re.sub(r"\s+", " ", (title or "").replace("&amp;", "&")).strip()
    if not t or NOT_INCIDENT.search(t) or REASSURE.search(t):
        return None
    hit = DISRUPTION.search(t)
    if hit is None:
        return None
    found: dict[str, set[str]] = {}
    for kind, names in ENTITIES.items():
        for name, rx in names.items():
            if rx.search(t):
                found.setdefault(kind, set()).add(name)
    sectors = [k for k, rx in SECTOR.items() if rx.search(t)]
    kind = _kind(found, sectors, t)
    if kind is None:
        return None
    entities = found.get(kind, set()) if kind != "cyber" else set().union(*found.values()) if found else set()
    if kind == "cyber" and not entities:
        return None  # "a cyberattack" at nobody we can name is not a major incident (and not one this account can verify)
    if not entities and kind in ("cloud", "telecom") and not SCOPE.search(t):
        return None  # "app outage" with no name and no scale
    return Incident(kind=kind, entities=frozenset(entities), scope=bool(SCOPE.search(t)), aftermath=bool(AFTERMATH.search(t)) or "?" in t,
                    hedged=bool(HEDGED.search(t)), resolved=bool(RESOLVED.search(t)), disruption=hit.group(0).lower())


def _kind(found: dict[str, set[str]], sectors: list[str], t: str) -> str | None:
    if CYBER.search(t) and found:
        return "cyber"
    if AVIATION_EVENT.search(t):
        return "aviation"
    # a named company decides; its own sector breaks ties ("United Airlines ... flights" is aviation; "Chase ... banks" is banking)
    for k in ("banking", "payments", "exchange", "aviation", "telecom", "power", "cloud"):
        if k in found and (k in sectors or len(found) == 1 or k != "cloud"):
            return k
    if "cloud" in found:
        return "cloud"
    for k in ("banking", "aviation", "payments", "exchange", "power", "telecom", "cloud"):
        if k in sectors:
            return k
    return None


def same_incident(a: str, b: str) -> bool:
    """Two headlines tell the same incident: the same kind at the same named company or service, or two sector-wide reports of the same
    kind that name nobody. A company-specific report and a sector-wide one stay apart (Chase is not 'major US banks' unless the sector
    report names Chase), and different companies never merge (Microsoft vs the FAA, United Airlines vs the FAA)."""
    ia, ib = detect(a), detect(b)
    if ia is None or ib is None or ia.kind != ib.kind:
        return False
    if ia.entities or ib.entities:
        return bool(ia.entities & ib.entities)
    return ia.scope and ib.scope


def desk_category(inc: Incident) -> str:
    """The News Desk category an incident story is stored under (it decides which Admin toggle covers it)."""
    return "stocks" if inc.financial else "viral"


def verified(titles_by_outlet: list[tuple[str, str]], *, official: bool = False) -> tuple[bool, str]:
    """Enough evidence to publish an incident on its own: an official source, two independent outlets telling the same incident, or one
    outlet carrying the affected company's own statement ("United Airlines ... airline says", "JPMorgan Chase says ..."). Hedged reports
    ("reportedly") never count."""
    from app.trending.sensitive import outlet_key

    if official:
        return True, "an official source"
    plain = [(o, t) for o, t in titles_by_outlet if (i := detect(t)) is not None and not i.hedged]
    if not plain:
        return False, "no plain report of the incident (only hedged or background coverage)"
    outlets = {outlet_key(o) for o, _ in plain if o}
    if len(outlets) >= 2:
        return True, f"{len(outlets)} independent outlets"
    statement = _rx(r"\b(says|said|confirms?|confirmed|statement|spokesperson|acknowledg\w+)\b")
    for _, t in plain:
        i = detect(t)
        if i is not None and i.entities and statement.search(t):
            return True, "the affected company's own statement"
    return False, "one outlet and no statement from the affected company: needs a second outlet"
