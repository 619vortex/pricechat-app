"""The editorial scorecard (owner, 2026-11-13: "a higher-engagement media engine"): every candidate story is scored on the five things that
make financial news travel, and a story that isn't worth a slot is rejected, not polished.

  surprise      0-20  a record, a first, an unexpected turn, a big move against its normal size, a contradiction the reporting states
  impact        0-20  the money involved (dollars, measured moves), macro and policy reach, mega-cap names, the assets our audience holds
  controversy   0-20  GENUINE conflict only: a named party accuses, sues, probes, fires, defies, bans; confirmed by an official source or two
                      outlets for the full points. An allegation from one outlet earns half. Deaths and harm are never "controversy".
  relevance     0-20  how close it is to our audience: Bitcoin, Ether and major crypto, exchanges, the Fed, oil and gold, mega-caps, hard
                      geopolitics with a market tie; routine politics with no market tie earns little
  discussion    0-20  will people argue about it: a government decision, a billionaire's move, winners and losers, a contradiction, a scandal.
                      Stories about deaths and victims are not debate material and stay low here

On top of the five (0-100): the beat (topic mix: scandals, billionaires, exchanges, government decisions, unusual stories and major crypto
rank up; minor corporate announcements are rejected outright) and what our audience engaged with lately per beat (engagement.beat_points,
normalized engagement on Telegram and X, -6..+6, nothing until enough posts are measured).

The total never replaces the evidence rules (app/newsdesk/chaos) or the virality bar: it is a SECOND, editorial bar. A news story under
`minimum` (Policy.editorial_min, Admin can change it) is not posted, and a slot with nothing above it is skipped (events.note_skip).

Factual safety (`safety`): an accusation about an identifiable person (fraud, an affair, misconduct, charges) needs an official source or two
independent outlets; one outlet is not enough to put a name next to an accusation, however well it would travel.

Everything here is deterministic and explained: each point comes with its reason, shown in Admin and in the examples document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.newsdesk import brief, checks

DIMENSIONS = ("surprise", "impact", "controversy", "relevance", "discussion")
MINIMUM = 45  # default editorial bar for a news story (Policy.editorial_min overrides; Admin: PUT /x/newsdesk/editorial/quality)
RANK_MAX = 8.0  # the most the scorecard moves a story in the slot ranking, either way
_rx = lambda p: re.compile(p, re.I)  # noqa: E731

RECORD = _rx(r"\b(record|all-time|biggest|largest|worst|best since|first(?:-ever| time| ever)|since (?:19|20)\d\d|in (?:a|over a|more than a) (?:decade|century)|"
             r"highest|lowest|unprecedented|never before|doubles?|doubled|triples?|tripled|halves|halved)\b")
UNEXPECTED = _rx(r"\b(unexpected\w*|surprise\w*|shock\w*|abrupt\w*|sudden\w*|stuns?|stunned|u-turn|reversal|reverses?|backtrack\w*|defies|defied|"
                 r"hotter than|cooler than|than expected|than forecast|missed|beats? (?:estimates|expectations))\b")
CONTRAST = _rx(r"\b(despite|even as|even though|but|yet|whereas|instead of|contrary to|weeks after|days after|months after)\b")
CONFLICT = _rx(r"\b(accus\w+|denie[sd]|deny|rejects?|rejected|defies|defied|clash\w*|feud\w*|row over|condemn\w*|sues|sued|suing|lawsuit|"
               r"probe\w*|investigat\w+|subpoena\w*|charged|charges|indict\w*|arrest\w*|fined|fines?|penalt\w+|bans?|banned|blocks?|blocked|"
               r"fires|fired|ousted|resign\w*|steps? down|stepped down|scandal\w*|fraud\w*|affair|misconduct|launder\w*|sanction\w*|crackdown|"
               r"seiz\w+|freez\w+|frozen|halts?|halted|suspend\w*|delist\w*|jail\w*|sentenc\w*|convict\w*)\b")
SCANDAL = _rx(r"\b(scandal\w*|fraud\w*|affair|misconduct|launder\w*|embezzl\w*|brib\w*|corrupt\w*|insider trading|ponzi|scam\w*|cover-?up|"
              r"probe\w*|investigat\w+|indict\w*|charged|arrest\w*|ousted|resign\w*|steps? down|stepped down|fired)\b")
SOMBRE = _rx(r"\b(killed|kills?|dead|deaths?|died|dies|casualt\w+|injured|wounded|victims?|hostages?|massacre|genocide|famine|earthquake|"
             r"hospital|civilians?|children)\b")
# money and reach
MACRO = _rx(r"\b(fed|federal reserve|fomc|powell|ecb|bank of japan|boj|bank of england|rate (?:cut|hike)s?|interest rates?|inflation|cpi|ppi|"
            r"jobs report|payrolls|unemployment|gdp|recession|treasur(?:y|ies)|yields?|bond market|debt ceiling|shutdown|tariffs?|trade war|"
            r"sanctions?|opec\+?|oil|crude|brent|wti|gold|silver|dollar|yuan|yen)\b")
CRYPTO_MAJOR = _rx(r"\b(bitcoin|btc|ether(?:eum)?|eth|solana|xrp|stablecoins?|usdt|usdc|tether|hyperliquid|hype|crypto(?:currenc(?:y|ies))?|etfs?|"
                   r"microstrategy|strategy's|saylor|defi)\b")
EXCHANGE = _rx(r"\b(binance|coinbase|kraken|okx|bybit|bitget|kucoin|htx|huobi|bitfinex|bitmex|gemini|upbit|bithumb|mexc|robinhood|hyperliquid|"
               r"crypto\.com|ftx|nyse|nasdaq|cme|cboe|lse|london stock exchange|exchange)\b")
CRYPTO_EXCHANGE = _rx(r"\b(binance|coinbase|kraken|okx|bybit|bitget|kucoin|htx|huobi|bitfinex|bitmex|gemini|upbit|bithumb|mexc|hyperliquid|crypto\.com|ftx|mercado bitcoin)\b")
BILLIONAIRE = _rx(r"\b(musk|elon|bezos|zuckerberg|buffett|berkshire|saylor|changpeng zhao|\bcz\b|justin sun|dimon|jensen huang|huang|altman|"
                  r"thiel|ackman|dalio|griffin|arnault|ambani|adani|masayoshi son|jack ma|gates|ellison|bloomberg|soros|druckenmiller|"
                  r"cathie wood|winklevoss|novogratz|billionaires?|tycoon|mogul)\b")
GOVERNMENT = _rx(r"\b(sec|cftc|occ|fdic|doj|justice department|treasury|white house|congress|senate|parliament|ministry|minister|government|"
                 r"regulators?|central bank|federal reserve|fed|ecb|supreme court|court|president|prime minister|trump|eu|european commission|"
                 r"executive order|sanctions?|tariffs?|bans?|banned|approves?|approved|ruling|rules|law|bill|lifts?|imposes?)\b")
GOV_DECISION = _rx(r"\b(approves?|approved|bans?|banned|blocks?|blocked|lifts?|lifted|imposes?|imposed|orders?|ordered|rules?|ruled|ruling|"
                   r"sanctions?|tariffs?|cuts?|hikes?|raises?|signs?|signed|passes|passed|vetoe?s?|rejects?|rejected|fines?|fined|sues|sued|"
                   r"charges|charged|executive order|decree|law|bill|cap|caps|capped)\b")
UNUSUAL = _rx(r"\b(bizarre|unusual|odd|strange|weird|from prison|memecoins?|meme|accidentally|mistake|fat[- ]finger|by error|lost (?:his|her|their) keys|"
              r"landfill|forgot|wrong address|typo|first-ever|for the first time|world's first)\b")
MEGA = _rx(r"\b(nvidia|apple|microsoft|google|alphabet|amazon|meta|tesla|openai|anthropic|jpmorgan|goldman sachs|blackrock|berkshire|saudi aramco|"
           r"aramco|samsung|tsmc|intel|boeing|visa|mastercard|paypal|deutsche bank|hsbc|ubs|morgan stanley|citigroup|bank of america|vanguard|fidelity)\b")
GEO_HARD = _rx(r"\b(missiles?|air ?strikes?|strikes? on|attack(?:s|ed)? on|invasion|invade\w*|ceasefire|truce|blockade|embargo|coup|war|troops|"
               r"tanker|strait of hormuz|nuclear)\b")
MARKET_TIE = _rx(r"\b(oil|crude|brent|wti|gas|lng|refiner\w*|tanker|pipelines?|exports?|imports?|output|production|shipping|sanctions?|tariffs?|"
                 r"markets?|stocks?|shares|bonds?|yields?|dollar|gold|bitcoin|crypto\w*)\b")
# a corporate announcement nobody outside the company argues about: a launch, a partnership, an appointment, a listing, an integration
MINOR_CORPORATE = _rx(r"\b(launch(?:es|ed)?|unveil\w*|introduc\w+|rolls? out|rolled out|partners? with|partnership|teams? up|collaborat\w+|integrat\w+|"
                      r"appoints?|appointed|names? (?:new )?(?:ceo|cfo|cto|chief|head|president)|hires?|hired|joins|expands? (?:to|into)|expansion|"
                      r"opens? (?:an? )?(?:office|hub|branch)|rebrand\w*|announces?|announced|now (?:available|supports?|live)|adds? support|lists?|"
                      r"listing|goes live|beta|pilot|upgrade|mainnet|testnet|whitepaper|roadmap|milestone|onboard\w*|wins? award|raises? \$?\d+(?:\.\d+)?\s?(?:m|million)\b|"
                      r"seed round|series [abc])\b")
# talk, not news: an opinion, an outlook, a round-up, a "faces an uphill battle"
SOFT = _rx(r"\b(faces? an? (?:uphill|tough)|could|may|might|outlook|what to (?:know|watch)|here'?s|explainer|analysis|opinion|preview|"
           r"week ahead|explained|in charts|live updates?|latest:|readiness|prepares?|calls? for|urges?|hopes?|vows?)\b")
ROUNDUP = re.compile(r"(?:,\s*[^,]+){1,}\s*(?:&|\band\b)\s*[^,]+$")  # "Hot Inflation, AI Selloff & Anthropic IPO"
STRONG = ("official", "two outlets", "price data", "exchange data")

BEATS = ("scandal", "billionaire", "exchange", "government", "crypto_major", "market_move", "unusual", "geopolitics", "macro",
         "minor_corporate", "soft_geopolitics", "other")
BEAT_NAMES = {"scandal": "Scandal", "billionaire": "Billionaires", "exchange": "Exchanges", "government": "Government decision",
              "crypto_major": "Major crypto", "market_move": "Major market move", "unusual": "Unusual money story", "geopolitics": "Geopolitics (market tie)",
              "macro": "Macro", "minor_corporate": "Minor corporate announcement", "soft_geopolitics": "Geopolitics (no market tie)", "other": "Other"}
BEAT_POINTS = {"scandal": 5.0, "billionaire": 5.0, "exchange": 4.0, "government": 4.0, "crypto_major": 3.0, "market_move": 2.0, "unusual": 4.0,
               "geopolitics": 2.0, "macro": 2.0, "minor_corporate": -15.0, "soft_geopolitics": -6.0, "other": -4.0}


@dataclass
class Scorecard:
    dims: dict[str, float]
    reasons: dict[str, list[str]]
    beat: str
    beats: list[str]
    beat_points: float
    audience: float = 0.0
    safety: list[str] = field(default_factory=list)
    minimum: int = MINIMUM

    @property
    def base(self) -> float:
        return round(sum(self.dims.values()), 1)

    @property
    def total(self) -> int:
        return int(max(0, min(100, round(self.base + self.beat_points + self.audience))))

    @property
    def reject(self) -> str:
        """Why this story must not be posted on editorial grounds, or ""."""
        if self.safety:
            return self.safety[0]
        if self.beat == "minor_corporate":
            return "a minor corporate announcement: not news our audience argues about"
        if self.total < self.minimum:
            weak = sorted(DIMENSIONS, key=lambda d: self.dims[d])[:2]
            return f"editorial score {self.total} under the minimum of {self.minimum} (weakest: {weak[0]} {self.dims[weak[0]]:g}, {weak[1]} {self.dims[weak[1]]:g})"
        return ""

    def rank_bonus(self) -> float:
        """Slot ranking only: a strong scorecard moves a story up, a weak one down (never past the bars)."""
        return round(max(-RANK_MAX, min(RANK_MAX, (self.total - 55) / 4)), 1)

    def out(self) -> dict[str, Any]:
        return {"total": self.total, "base": self.base, "dims": {k: round(v, 1) for k, v in self.dims.items()}, "reasons": self.reasons,
                "beat": self.beat, "beat_name": BEAT_NAMES.get(self.beat, self.beat), "beats": self.beats, "beat_points": self.beat_points,
                "audience": self.audience, "safety": self.safety, "minimum": self.minimum, "reject": self.reject, "rank_bonus": self.rank_bonus()}


def _usd(text: str) -> float:
    try:
        return float(brief._amount_usd(text))
    except Exception:  # noqa: BLE001
        return 0.0


def _pct(text: str) -> float:
    return max((float(p) for p in brief.PERCENT.findall(text or "")), default=0.0)


def _hit(rx: re.Pattern[str], text: str) -> str:
    m = rx.search(text or "")
    return m.group(0).lower() if m else ""


def people_in(title: str) -> list[str]:
    """Identifiable people a headline names (a known public figure, or a given name and a surname, or a title and a name)."""
    from app.newsdesk import clean, photos

    found = list(photos.people_named(title or ""))
    for w in re.findall(r"[A-Za-z]+", title or ""):
        if w.lower() in clean.KNOWN_PEOPLE:
            found.append(clean.KNOWN_PEOPLE[w.lower()])
    if re.search(r"\b(c\.?e\.?o\.?|cfo|founder|chairman|chairwoman|chief executive|executive|minister|president|governor|senator)\b", title or "", re.I) \
            and re.search(r"\b(affair|fraud|misconduct|accus\w+|alleg\w+|harass\w*|brib\w*|launder\w*|charged|arrest\w*|indict\w*)\b", title or "", re.I):
        found.append("a named role holder")
    return list(dict.fromkeys(found))


ACCUSATION = _rx(r"\b(fraud\w*|affair|misconduct|accus\w+|alleg\w+|harass\w*|abuse\w*|brib\w*|corrupt\w*|launder\w*|embezzl\w*|charged|charges|"
                 r"arrest\w*|indict\w*|insider trading|lied|lying|cover-?up|stole|theft|scam\w*|ponzi|manipulat\w+|spy|espionage)\b")


def accused(title: str, person: str, acc: re.Match[str]) -> bool:
    """Is the accusation about this person? The person comes before the accusation word ("Huang's affair", "CEO charged"), or a verb of
    accusation points at them ("SEC charges Do Kwon"). "Launderers mint Elon Musk memecoins" accuses no one called Musk."""
    if person == "a named role holder":
        return True
    last = person.split()[-1]
    at = title.lower().find(last.lower())
    if at < 0:
        return False
    if at < acc.start():
        return True
    return bool(re.search(rf"\b(accus\w*|charg\w*|sues|sued|indict\w*|arrest\w*|probe\w*|investigat\w*)\s+(?:\w+\s+){{0,3}}{re.escape(last)}", title, re.I))


def safety(title: str, evidence: str, outlets: int, text: str = "") -> list[str]:
    """Factual safety: an accusation about an identifiable person needs an official source or two independent outlets."""
    t = title or ""
    acc = ACCUSATION.search(t)
    if not acc:
        return []
    who = [p for p in people_in(t) if accused(t, p, acc)]
    if not who:
        return []
    if evidence == "official" or (evidence == "two outlets" and outlets >= 2):
        return []
    return [f"an accusation ({acc.group(0).lower()}) about an identifiable person ({who[0]}) with only {evidence or 'one outlet'}: needs an official "
            f"source or two independent outlets"]


def beats_of(title: str, text: str, kind: str = "news", topic: str = "") -> list[str]:
    t, all_ = title or "", f"{title or ''} {text or ''}"
    out: list[str] = []
    if kind in ("market", "recap", "data") or topic in ("move", "liquidations"):
        out.append("market_move")
    if SCANDAL.search(t) or topic in ("scam", "controversy"):
        out.append("scandal")
    if BILLIONAIRE.search(t):
        out.append("billionaire")
    if EXCHANGE.search(t) or topic in ("hack", "failure"):
        out.append("exchange")
    if GOVERNMENT.search(t) and GOV_DECISION.search(t):
        out.append("government")
    if CRYPTO_MAJOR.search(t):
        out.append("crypto_major")
    if UNUSUAL.search(t):
        out.append("unusual")
    if GEO_HARD.search(t) or topic == "geopolitics":
        out.append("geopolitics" if MARKET_TIE.search(all_) and not SOFT.search(t) else "soft_geopolitics")
    if MACRO.search(t) or topic == "macro":
        out.append("macro")
    strong = {"scandal", "billionaire", "government", "market_move", "unusual", "geopolitics", "macro"}
    big_money = _usd(t) >= 1e8
    if MINOR_CORPORATE.search(t) and not (set(out) & strong) and not big_money and topic not in ("hack", "failure", "scam") \
            and not UNEXPECTED.search(t) and not RECORD.search(t) and not (MEGA.search(t) and CRYPTO_MAJOR.search(t)):
        out.insert(0, "minor_corporate")
    return out or ["other"]


def primary_beat(beats: list[str]) -> str:
    if "minor_corporate" in beats:
        return "minor_corporate"
    best = max(beats, key=lambda b: (BEAT_POINTS.get(b, 0), -BEATS.index(b)))
    if best == "soft_geopolitics" and len(beats) > 1:
        best = max((b for b in beats if b != "soft_geopolitics"), key=lambda b: BEAT_POINTS.get(b, 0))
    return best


def assess(*, title: str, summary: str = "", text: str = "", kind: str = "news", topic: str = "", evidence: str = "", outlets: int = 1,
           parts: dict[str, Any] | None = None, alleged: bool = False, measured_pct: float = 0.0, learned: dict[str, float] | None = None,
           minimum: int = MINIMUM) -> Scorecard:
    """The scorecard from a story's own words and the numbers the desk already measured (virality parts)."""
    t = title or ""
    body = f"{t} {summary or ''}"
    every = f"{body} {text or ''}"
    parts = parts or {}
    dims: dict[str, float] = {}
    why: dict[str, list[str]] = {d: [] for d in DIMENSIONS}
    strong = evidence in STRONG
    # surprise
    s = 0.0
    if RECORD.search(t):
        s += 9
        why["surprise"].append(f"record/first: '{_hit(RECORD, t)}'")
    elif RECORD.search(summary or ""):
        s += 4
        why["surprise"].append(f"record in the reporting: '{_hit(RECORD, summary)}'")
    if UNEXPECTED.search(body):
        s += 6
        why["surprise"].append(f"unexpected: '{_hit(UNEXPECTED, body)}'")
    if CONTRAST.search(t):
        s += 4
        why["surprise"].append(f"contradiction: '{_hit(CONTRAST, t)}'")
    pct = max(_pct(t), abs(measured_pct))
    usd = _usd(body)
    if pct >= 20:
        s += 7
    elif pct >= 8:
        s += 5
    elif pct >= 4:
        s += 3
    if pct >= 4:
        why["surprise"].append(f"a {pct:g}% move")
    mag = float(parts.get("magnitude") or 0)
    if mag >= 20:
        s += 3
        why["surprise"].append(f"magnitude {mag:g}/40")
    if UNUSUAL.search(t):
        s += 5
        why["surprise"].append(f"unusual: '{_hit(UNUSUAL, t)}'")
    if topic in ("hack", "failure", "scam") and (usd >= 1e7 or EXCHANGE.search(t)):
        s += 8
        why["surprise"].append(f"a {topic} at a known platform or for real money")
    household = MEGA.search(t) or BILLIONAIRE.search(t)
    hard_geo = bool(GEO_HARD.search(t) and MARKET_TIE.search(every) and not SOFT.search(t))
    if hard_geo:
        s += 8
        why["surprise"].append(f"a hard geopolitical event with a market tie: '{_hit(GEO_HARD, t)}'")
    if SCANDAL.search(t) and household:
        s += 6
        why["surprise"].append(f"a scandal at a household name: '{household.group(0).lower()}'")
    dims["surprise"] = min(20.0, s)
    # impact
    i = 0.0
    if usd >= 1e9:
        i += 12
    elif usd >= 1e8:
        i += 9
    elif usd >= 1e7:
        i += 5
    elif usd >= 1e6:
        i += 2
    if usd >= 1e6:
        why["impact"].append(f"${usd / 1e6:,.0f}M involved")
    if pct >= 3:
        i += min(8, pct / 2)
        why["impact"].append(f"{pct:g}% move")
    if MACRO.search(t):
        i += 5
        why["impact"].append(f"macro/policy: '{_hit(MACRO, t)}'")
    if MEGA.search(t):
        i += 4
        why["impact"].append(f"mega-cap: '{_hit(MEGA, t)}'")
    if CRYPTO_MAJOR.search(t) or EXCHANGE.search(t):
        i += 3
        why["impact"].append("major crypto or an exchange")
    if topic in ("hack", "failure", "scam") and usd >= 1e8:
        i += 4 if usd >= 1e9 else 3
        why["impact"].append("losses at scale")
    if GEO_HARD.search(t) and MARKET_TIE.search(every):
        i += 4
        why["impact"].append("hard geopolitical event with a market tie")
    if SOFT.search(t):
        i -= 4
        why["impact"].append(f"talk, not an event: '{_hit(SOFT, t)}'")
    dims["impact"] = max(0.0, min(20.0, i))
    # controversy: genuine conflict between named parties, confirmed
    c = 0.0
    conflict = CONFLICT.search(t)
    sombre = bool(SOMBRE.search(t))
    if conflict and not sombre:
        c = 10.0 if strong else 5.0
        why["controversy"].append(f"conflict: '{conflict.group(0).lower()}' ({'confirmed' if strong else 'one outlet: half points'})")
        if SCANDAL.search(t):
            c += 5
            why["controversy"].append(f"scandal: '{_hit(SCANDAL, t)}'")
        if outlets >= 3:
            c += 3
            why["controversy"].append(f"{outlets} outlets carry it")
        if alleged and not strong:
            c = min(c, 6.0)
            why["controversy"].append("an allegation from one outlet: capped")
    elif CONTRAST.search(t) and not sombre:
        c = 6.0
        why["controversy"].append("a stated contradiction")
    if sombre:
        why["controversy"].append("harm to people is not controversy")
    dims["controversy"] = min(20.0, c)
    # relevance to our audience
    r = 0.0
    if CRYPTO_MAJOR.search(t):
        r += 10
        why["relevance"].append(f"major crypto: '{_hit(CRYPTO_MAJOR, t)}'")
    if EXCHANGE.search(t):
        r += 10 if CRYPTO_EXCHANGE.search(t) else 6
        why["relevance"].append(f"exchange: '{_hit(EXCHANGE, t)}'")
    if MACRO.search(t):
        r += 7
        why["relevance"].append(f"macro: '{_hit(MACRO, t)}'")
    if MEGA.search(t):
        r += 6
        why["relevance"].append(f"mega-cap: '{_hit(MEGA, t)}'")
    if BILLIONAIRE.search(t):
        r += 5
        why["relevance"].append(f"billionaire: '{_hit(BILLIONAIRE, t)}'")
    if GEO_HARD.search(t):
        r += 5 if MARKET_TIE.search(every) else 1
        why["relevance"].append("geopolitics " + ("with a market tie" if MARKET_TIE.search(every) else "without a market tie"))
    if topic in ("hack", "failure", "scam"):
        r += 8
        why["relevance"].append(f"a crypto {topic}: our audience's own money and platforms")
    reach = float(parts.get("reach") or 0)
    if reach:
        r += reach / 5
    if kind in ("market", "recap", "data"):
        r += 6
        why["relevance"].append("our own measured market data")
    if not why["relevance"]:
        why["relevance"].append("no link to markets, crypto or money our audience follows")
    dims["relevance"] = min(20.0, r)
    # discussion potential
    d = 0.0
    if GOVERNMENT.search(t) and GOV_DECISION.search(t):
        d += 7
        why["discussion"].append(f"a government decision: '{_hit(GOV_DECISION, t)}'")
    if BILLIONAIRE.search(t):
        d += 6
        why["discussion"].append("a billionaire")
    if conflict and not sombre:
        d += 6
        why["discussion"].append("people take sides")
    if CONTRAST.search(t) or UNEXPECTED.search(t):
        d += 4
        why["discussion"].append("a contradiction or surprise to argue over")
    if CRYPTO_MAJOR.search(t) and (pct >= 3 or usd >= 1e8):
        d += 4
        why["discussion"].append("big crypto money")
    if UNUSUAL.search(t):
        d += 4
        why["discussion"].append("an odd story people share")
    if topic in ("hack", "failure", "scam") and usd >= 1e7:
        d += 6
        why["discussion"].append("who is to blame, and is the platform safe")
    if SCANDAL.search(t) and household:
        d += 4
        why["discussion"].append("a household name under fire")
    if hard_geo:
        d += 6
        why["discussion"].append("what it means for oil, shipping and markets")
    if MACRO.search(t) and (pct >= 5 or CRYPTO_MAJOR.search(t)):
        d += 5
        why["discussion"].append("winners and losers across markets")
    if sombre:
        d = min(d, 4.0)
        why["discussion"].append("a story about harm to people: not debate material")
    dims["discussion"] = min(20.0, d)
    beats = beats_of(t, every, kind, topic)
    beat = primary_beat(beats)
    beat_points = BEAT_POINTS.get(beat, 0.0)
    if ROUNDUP.search(t) and not re.search(r"\b(is|are|was|has|have|says?|hits?|falls?|rises?|jumps?|drops?)\b", t, re.I):
        beat_points -= 10
        why["discussion"].append("a round-up title, not one story")
    audience = 0.0
    if learned and learned.get(beat):
        audience = round(max(-6.0, min(6.0, float(learned[beat]))), 1)
    return Scorecard(dims, why, beat, beats, beat_points, audience, safety(t, evidence, outlets, every), minimum)


def for_event(ev: Any, rows: list[Any] | None = None, learned: dict[str, float] | None = None, minimum: int = MINIMUM) -> Scorecard:
    f = ev.facts or {}
    outlets = len({str(s.get("name") or "").lower() for s in (getattr(ev, "sources", None) or [])} - {""}) or len(f.get("outlets") or []) or 1
    measured = float(f.get("pct") or 0)
    text = " ".join([str(f.get("told") or "")[:1500], *[f"{getattr(r, 'title', '')} {getattr(r, 'summary', '') or ''}" for r in rows or []]])
    return assess(title=getattr(ev, "title", "") or getattr(ev, "headline", "") or "", summary=getattr(ev, "summary", "") or "", text=text,
                  kind=getattr(ev, "kind", "") or "news", topic=getattr(ev, "topic", "") or "", evidence=getattr(ev, "evidence", "") or "",
                  outlets=outlets, parts=getattr(ev, "parts", None) or {}, alleged=bool(f.get("alleged")), measured_pct=measured, learned=learned,
                  minimum=minimum)


def stored(ev: Any) -> dict[str, Any]:
    return dict((ev.facts or {}).get("editorial") or {})


# --- story-level dedup on top of editorial.judge ----------------------------------------------------------------------------------

NEAR = 0.45  # headline token overlap that makes two stories the same event even when the names differ ("Saudi output" / "Riyadh reports")


def same_event(a: str, b: str) -> float:
    """Token overlap of two headlines after numbers are normalised: 0-1."""
    def toks(s: str) -> set[str]:
        return {w for w in checks.tokens(re.sub(r"(\d)[.,](\d)", r"\1\2", s or "")) if not w.isdigit() or len(w) > 2}
    x, y = toks(a), toks(b)
    return round(len(x & y) / len(x | y), 2) if x and y else 0.0


def near_duplicate(title: str, covered: list[tuple[int, str]]) -> tuple[int, float] | None:
    """(event id, overlap) of the closest story already out when the overlap reaches NEAR, else None. Two headlines naming different
    subjects are two stories however alike the wording ("Bybaz hacked" / "Zentra hacked")."""
    mine = checks.names(title)

    def comparable(t: str) -> bool:
        theirs = checks.names(t)
        return not (mine and theirs and not mine & theirs)

    best = max(((eid, same_event(title, t)) for eid, t in covered if comparable(t)), key=lambda x: x[1], default=None)
    return best if best is not None and best[1] >= NEAR else None


__all__ = ["BEATS", "BEAT_NAMES", "DIMENSIONS", "MINIMUM", "Scorecard", "assess", "beats_of", "for_event", "near_duplicate", "people_in", "safety",
           "same_event", "stored"]


def overview(db: Any, now: Any, *, hours: int = 48, limit: int = 60, minimum: int = MINIMUM) -> list[dict[str, Any]]:
    """Admin: news candidates with their scorecard, hooks, question, picture plan, accuracy result and the desk's decision."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.models.newsdesk import NdEvent

    out = []
    for ev in db.scalars(select(NdEvent).where(NdEvent.kind.in_(("news", "update")), NdEvent.created_at >= now - timedelta(hours=hours))
                         .order_by(NdEvent.id.desc()).limit(limit)):
        f = ev.facts or {}
        card = f.get("editorial") or {}
        out.append({"event": ev.id, "title": ev.title, "headline": ev.headline, "status": ev.status, "why": ev.why or "", "virality": float(ev.virality or 0),
                    "evidence": ev.evidence, "editorial": card, "below_minimum": bool(card) and int(card.get("total") or 0) < minimum,
                    "hooks": (f.get("hooks") or {}).get("hooks") or [], "chosen_hook": (f.get("hooks") or {}).get("chosen") or "",
                    "hook_why": (f.get("hooks") or {}).get("why") or "", "question": card.get("question") or (f.get("hooks") or {}).get("question") or "",
                    "question_why": card.get("question_why") or "", "visual": card.get("visual") or {}, "accuracy": f.get("accuracy") or {"ok": True, "problems": []},
                    "released_at": ev.released_at.isoformat() if ev.released_at else None})
    return out
