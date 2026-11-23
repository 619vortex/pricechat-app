"""The News Desk's editorial judgement: how important a story is, with its reasons, in ONE place (owner, 2026-11-23: "Make the Terminal
behave like a competent, fast, independent financial news editor ... Stop patching individual symptoms").

Before this module a story had to survive a chain of separate keyword gates (newsworthy's consequence lists per story type, weak_story, the
score bar, routine controversy, filler, the held-story recheck, the breaking bar and breaking_worthy), each able to refuse on its own and
several applied twice. Production 2026-11-16 to 11-23: 25 of 32 slots empty; refused as "no concrete consequence": Nvidia +27% on its CEO's
return, a $44B takeover, a $12M hack, a Russian warship sunk, an attack injuring two at a Bahrain plant (docs/newsdesk/2026-11-23-diagnosis.md).

What an editor asks, and what `judge` computes:
1. Did something happen? A fact the headline states: a figure that moved (money; a % move of a market or a major company), people killed
   or hurt, or a completed hard event (an attack that hit, a hack, a default, an arrest, a rate decision, a takeover agreed, a leader
   ousted, sanctions imposed, an outage). No fact: noise ("nothing happened that the sources state").
2. How big? magnitude, from the largest fact, on one scale for every kind of story: a $44B deal, a 27% move in Nvidia and a 5% S&P day are
   measured, not matched against a list for their story type.
3. Who does it touch? reach: a major state, a central bank, a mega-cap, a major market (indices, BTC, ETH, gold, silver, oil), a chokepoint.
4. How sure are we? credibility: independent outlets and official sources. One unofficial outlet is never MAJOR; hedged reports and plans
   with nothing done yet lose points.
5. Is it still news? recency: the score fades after 90 minutes; a news story older than STALE_MIN is stale.
6. Is it new? novelty is decided before the judgement, by the one repeat check (events.memory): a repeat never reaches this.
Never news, whatever its figures: opinion, explainers, features, live pages, round-ups, reaction pieces, questions.

Tiers, and what the scheduler (app/newsdesk/events) does with them:
- MAJOR: verified and exceptional: the only tier that goes at once (owner, 2026-11-23), outside the slot plan and the diversity rule;
  never outside the factual checks, the repeat check or the safety controls (emergency stop, market pause, owner block).
- SIGNIFICANT: the next of the six slots, best first; diversity only ranks it lower.
- NOTABLE: fills a slot when nothing better waits; two posts in a row from one desk hold it.
- NOISE: never posted; the reason is recorded on the event.

Factual verification (claims, numbers, second source, the final check) is not here and no tier weakens it: it runs after the judgement, on
every post."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.newsdesk import newsworthy as nw

MAJOR, SIGNIFICANT, NOTABLE, NOISE = "major", "significant", "notable", "noise"
ORDER = {MAJOR: 3, SIGNIFICANT: 2, NOTABLE: 1, NOISE: 0}
TIER_AT = ((MAJOR, 72.0), (SIGNIFICANT, 54.0), (NOTABLE, 42.0))  # score -> tier (score = BASE + size + reach + sources - doubt - age)
BASE = 30.0  # something happened (a stated fact); a story with no fact never gets a score
STALE_MIN = 12 * 60  # a news story first reported longer ago than this is no longer news at a slot


def _rx(p: str) -> re.Pattern[str]:
    return re.compile(p, re.I)


# --- what moved ---------------------------------------------------------------------------------------------------------------------
INDEX = _rx(r"\b(s&p(?: 500)?|nasdaq(?:-100| 100| composite)?|dow(?: jones)?|stocks|stock market|wall street|global stocks|world stocks|"
            r"equities|stoxx|ftse|nikkei|hang seng|kospi|dax)\b")
CRYPTO_MAJOR = _rx(r"\b(bitcoin|btc|ether(?:eum)?(?![.\w])|eth|crypto market)\b")
METAL_OIL = _rx(r"\b(gold|silver|copper|oil|brent|wti|crude|natural gas)\b")
MEGA = _rx(r"\b(nvidia|apple|microsoft|alphabet|google|amazon|meta|tesla|berkshire|jpmorgan|broadcom|tsmc|samsung|netflix|oracle|"
           r"eli lilly|visa|mastercard|walmart|exxon|saudi aramco|aramco|openai|anthropic|spacex|binance|coinbase|tether|circle|strategy|microstrategy)\b")
MOVE = _rx(r"\b(falls?|fell|drops?|dropped|slides?|slid|plunges?|plunged|tumbles?|tumbled|sinks?|sank|slumps?|slumped|crash\w*|craters?|"
           r"rises?|rose|jumps?|jumped|surges?|surged|soars?|soared|gains?|gained|climbs?|climbed|rall(?:y|ies|ied)|rebounds?|rebounded|"
           r"spikes?|spiked|skyrockets?|skyrocketed|rockets?|rocketed|leaps?|leapt|adds?|added|wipes?|wiped|erases?|erased|sheds?|shed|"
           r"lowest|highest|record high|all-time high|best day|worst day|routs?|selloff|sell-off|drags?|dragged|"
           r"(?:down|up|lower|higher) \d)\b")
EXTREME = _rx(r"\b(record high|all-time high|(?:lowest|highest) (?:level |close )?since|(?:best|worst|biggest) (?:day|week|drop|gain|rally|fall)\w* since|"
              r"52-week (?:high|low))\b")
LEVEL = _rx(r"\b(?:bitcoin|btc|ether(?:eum)?|eth|s&p(?: 500)?|nasdaq(?: composite)?|dow(?: jones)?|stocks)(?:\s+\$[a-z]{2,6})?\s+"
            r"(breaks?|broke|falls?|fell|drops?|dropped|slips?|slipped|sinks?|sank|plunges?|plunged|tumbles?|tumbled|rises?|rose|climbs?|"
            r"climbed|jumps?|jumped|surges?|surged|soars?|soared|tops|topped|crosses|crossed|reclaims?|reclaimed)\s+(?:back\s+)?"
            r"(?:below|above|under|over|past|through)\s+\$?\s?\d")
BACKGROUND_MOVE = _rx(r"['’]s\s+(?:\w+\s+)?(?:plunge|crash|surge|rout|selloff|sell-off|slump|rally|tumble|meltdown|freefall)\b")
DOW_POINTS = re.compile(r"\bdow\b.{0,40}?\b(\d[\d,]{2,})\s*points?\b", re.I)

# --- what happened: completed hard events (points, name, pattern); the first match names the event, the largest fact sets the size -----
HARD = [
    (22.0, "an attack that hit", _rx(r"\b(strikes? (?:on|at|hit)|struck|attack(?:s|ed)? (?:on|at|hits?)|attacked|bombed|shelled|shot down|downed|"
                                     r"seiz(?:es|ed)|explosions?|blasts?|ablaze|on fire|drone strikes?|missile strikes?|"
                                     r"(?:drone|missile) attacks?)\b")),
    (22.0, "a hack or theft", _rx(r"\b(hack(?:ed|s)?|exploit(?:ed)?|drained|stolen|(?<!grand )theft|breach(?:ed)?|heist|compromised)\b")),
    (22.0, "a failure", _rx(r"\b(defaults?|defaulted|bankrupt\w*|insolven\w*|collaps(?:es|ed)|halts? withdrawals|freezes? withdrawals|"
                              r"depegs?|depegged)\b")),
    (20.0, "a central-bank decision", _rx(r"\b(fed|federal reserve|ecb|bank of england|boe|bank of japan|boj|pboc|snb|rba)\b.{0,40}"
                                          r"\b(cuts?|hikes?|raises?|lowers?|holds?|keeps?|leaves?|pauses?)\b.{0,20}\brates?\b")),
    (18.0, "a takeover or deal", _rx(r"\b(to (?:buy|acquire)|acquires?|acquired|agrees? to (?:buy|acquire|merge)|takeover|merger|buyout)\b")),
    (18.0, "an enforcement action", _rx(r"\b(arrest(?:ed|s)?|charged|indicted|convicted|sentenced|sues|sued|fines?|fined|raid(?:s|ed)|"
                                        r"sanctions? on|imposes? sanctions|sanctioned|bans?|banned|blocks?|blocked|curbs?|export controls?)\b")),
    (16.0, "a leadership change", _rx(r"\b(ousted|fired|resigns?|resigned|steps? down|stepped down|reinstat\w*|rehired|appointed|names? (?:new )?ceo)\b")),
    (18.0, "a natural disaster", _rx(r"\b(earthquake|quake|tsunami|hurricane|typhoon|cyclone|volcan\w+ erupt\w*|eruption|wildfires?|floods?)\b")),
    (16.0, "an outage", _rx(r"\b(outages?|goes down|went down|offline|ground stop|grounded)\b")),
    (16.0, "a deployment or escalation", _rx(r"\b(troops? (?:are |were )?in|deploy(?:s|ed)|mobiliz\w*|declares? (?:war|emergency)|evacuat\w*|"
                                             r"blockade|closes? (?:the )?(?:strait|airspace|border))\b")),
    (14.0, "a ceasefire or agreement", _rx(r"\b(ceasefire|truce|peace deal|trade deal|agreement)\b.{0,30}\b(agreed|reached|signed|takes effect)\b|"
                                           r"\b(agree|agrees|agreed|signs?|signed)\b.{0,30}\b(ceasefire|truce|deal|agreement)\b")),
    # a routine print counts little on its own (owner, 2026-11-18: one outlet's "UK inflation accelerated to 3.6%" is not a post); a
    # multi-year extreme adds 8 below
    (10.0, "official economic data", _rx(r"\b(inflation|cpi|consumer prices|unemployment|jobless claims|payrolls|jobs report|gdp|retail sales)\b"
                                         r".{0,40}\b(rises?|rose|falls?|fell|jumps?|slows?|slowed|accelerat\w*|cools?|cooled|hits?|beats?|misses?|\d)")),
]
# "Iran attacks Kuwait", "Ukrainian drones strike two Russian oil refineries", "Tankers hit in Strait of Hormuz": the verb of an attack with
# an actor or a target the desk knows (a state, a weapon, a force, a ship or a facility)
ATTACK_VERB = _rx(r"\b(attacks?|strikes?|struck|hits?|bombs?|bombed|shells?|shelled|pounds?|pounded|targets?|targeted|damaged|destroyed|sinks?|sank|sunk)\b")
ACTOR_OR_TARGET = _rx(r"\b(drones?|missiles?|rockets?|forces|troops|army|navy|military|jets?|warships?|ships?|tankers?|vessels?|refiner(?:y|ies)|"
                      r"plant|facility|base|airport|port|pipeline|power (?:plant|station|grid)|embassy|city|region|capital)\b")
# attacks talked about, not one that happened: "Europeans brace for more Russian attacks", "Bridges Become Targets in Russia's War"
NOT_AN_ATTACK = _rx(r"\b(brace for|braces for|fears? (?:of|more)|risk of|threat of|more \w+ attacks|wave of|become targets?|becomes? a target|"
                    r"prepares? for|warns? of|calls? for)\b")
INVESTIGATION = _rx(r"\b(probe|probes|probing|investigat(?:es|ing|ion|ed)|subpoena\w*)\b")
SHARP = _rx(r"\b(plunges?|plunged|crash(?:es|ed)?|craters?|cratered|soars?|soared|skyrockets?|skyrocketed|surges?|surged|tumbles?|tumbled|"
            r"routs?|selloff|sell-off|meltdown|freefall)\b")
FINANCE = _rx(r"\b(markets?|stocks?|shares?|bonds?|yields?|treasur\w+|dollar|currency|banks?|exchanges?|crypto\w*|tokens?|coins?|stablecoins?|"
              r"etfs?|funds?|investors?|traders?|prices?|tariffs?|trade|economy|economic|rates?|inflation|oil|gas|gold|silver|chips?|ai|ipo|"
              r"earnings|revenue|profits?|valuation|sec|cftc|ceo|company|companies|deal)\b")
FINANCIAL_EVENTS = {"a natural disaster", "a hack or theft", "a failure", "a central-bank decision", "a takeover or deal", "an enforcement action", "an outage",
                    "an official investigation", "official economic data"}
# the aftermath of an older event: "survivors begin receiving payments from $305m bankruptcy"
AFTER = _rx(r"\b(?:from|after|over|since)\s+(?:the |its |a |an )?(?:\$[\d.,]+\s*(?:b|bn|billion|m|mn|million)\s+)?(?:\w+\s+){0,2}"
            r"(bankruptcy|settlement|hack|crash|collapse|scandal|attack|war|deal|lawsuit)\b")
NATURAL_AT = next(i for i, h in enumerate(HARD) if h[1] == "a natural disaster")  # "Floods hit Valencia region" is weather, not an attack
INJURED = _rx(r"\b(injur(?:ed|es|ing|ies)|wounded|hurt|casualties)\b")
# a threat or warning about an attack is not an attack that hit
THREAT = _rx(r"\b(threat\w*|threatens?|warns? of|could|would|will|vows?|plans?|prepar\w*|ready to|to (?:attack|strike|hit|seize|sink))\b")

# --- reach ------------------------------------------------------------------------------------------------------------------------
BIG_STATE = {"US", "CHINA", "RUSSIA", "IRAN", "ISRAEL", "UKRAINE", "SAUDI", "NATO", "TAIWAN", "NKOREA", "INDIA"}
CENTRAL_BANK = _rx(r"\b(fed|federal reserve|ecb|bank of england|bank of japan|pboc)\b")
CHOKEPOINT = _rx(r"\b(hormuz|red sea|bab el-mandeb|suez|oil (?:terminal|refinery|field|facility|tanker|port)|refiner(?:y|ies)|pipelines?|"
                 r"lng|tankers?|industrial (?:plant|facility))\b")

# an attack on energy or shipping moves oil, gold and every market at once ("Missile strike hits Saudi oil terminal")
ENERGY_OR_SHIPPING = _rx(r"\b(oil|gas|lng|crude|refiner(?:y|ies)|pipelines?|terminal|tankers?|shipping|strait|hormuz|red sea|suez|aramco|"
                         r"power (?:plant|station|grid))\b")

# --- never news, whatever the figures -----------------------------------------------------------------------------------------------
OPINION = _rx(r"^(?:opinion|analysis|explainer|review|letters?|comment|editorial|podcast|watch|video|quiz)\s*[:|\-–—]|"
              r"\b(what (?:it means|to know|we know|you need to know)|why (?:it|this) matters|here'?s (?:why|what|how)|explained|"
              r"how to|should you|is it time to|winners and losers|the week in|things to watch|in charts?)\b")
QUESTION = re.compile(r"\?\s*['\"’”]?\s*$")
REACTION = _rx(r"\b(reacts?|reacted|reaction|experts? (?:say|warn)|analysts? (?:say|warn)|could everything|breaks? silence|downplays?|"
               r"shrugs? off|shrugged off|is proof|proves that|shows why|lessons? from|what .{1,30} means)\b")
HEDGE = _rx(r"\b(reportedly|allegedly|rumou?r\w*|unconfirmed|sources say|according to sources|people familiar|report:|: report\b|said to|"
            r"- report\b|considering|weighs?|mulls?|could|may|might)\b")
# a forecast or plan with nothing done yet ("set to add $2 trillion" DURING a rally is a move under way: a stated move still counts)
NOT_YET = _rx(r"\b(set to|expected to|poised to|on track to|projected to|forecast to|plans? to|planning to|aims? to|seeks? to|prepares?|preparing|readies|"
              r"backs?|backed|endorses?|endorsed|"
              r"proposes?|proposal|to vote on|will (?:consider|decide|meet)|ahead of|preview)\b")


@dataclass
class Decision:
    tier: str
    score: float
    reason: str  # one line the owner can read: why this tier, or why never
    facts: list[str] = field(default_factory=list)  # what it rests on
    parts: dict[str, float] = field(default_factory=dict)  # the score, part by part
    verified: bool = False  # two independent outlets, an official source, or the desk's own checked market data

    @property
    def rank(self) -> int:
        return ORDER[self.tier]

    def as_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "score": round(self.score, 1), "reason": self.reason, "facts": list(self.facts),
                "parts": {k: round(v, 1) for k, v in self.parts.items()}, "verified": self.verified}


def tier_of(score: float) -> str:
    return next((t for t, at in TIER_AT if score >= at), NOISE)


def _money_points(usd: float) -> float:
    return 30.0 if usd >= 1e10 else 26.0 if usd >= 1e9 else 20.0 if usd >= 1e8 else 14.0 if usd >= 1e7 else 8.0 if usd >= 1e6 else 0.0


def _move_points(head: str) -> tuple[float, str]:
    """A stated move of a market or a company, scaled by what is normal for that kind of asset (a 5% day is huge for the S&P, routine for a
    small token)."""
    if LEVEL.search(head) and (CRYPTO_MAJOR.search(head) or INDEX.search(head)) and not nw.pct_of(head):
        # "Bitcoin breaks below $70,000": a round level of bitcoin, ether or an index (a commodity's price level is routine: owner,
        # 2026-11-18, "Brent falls below $90" never fills a slot)
        return 14.0, "a round-level break in a major market"
    if not MOVE.search(head) and not nw.INDEX_REGIME.search(head):
        return 0.0, ""
    pct = nw.pct_of(head)
    m = DOW_POINTS.search(head)
    if m:
        pct = max(pct, int(m.group(1).replace(",", "")) / 450.0)  # a ~45,000 Dow: 450 points is about 1%
    if INDEX.search(head):
        lines, what = ((4.0, 30.0), (2.5, 26.0), (1.5, 18.0), (0.8, 10.0)), "a stock-market move"
    elif CRYPTO_MAJOR.search(head):
        lines, what = ((10.0, 28.0), (6.0, 22.0), (4.0, 16.0), (2.5, 8.0)), "a bitcoin/ether move"
    elif METAL_OIL.search(head):
        lines, what = ((6.0, 28.0), (4.0, 22.0), (2.5, 14.0), (1.5, 8.0)), "a commodity move"
    elif MEGA.search(head):
        # 3% is a routine day for a mega-cap (owner, 2026-11-18: "Nvidia NVDA stock up 3% as earnings beat" is not a post)
        lines, what = ((20.0, 28.0), (10.0, 22.0), (6.0, 14.0)), "a major company's share move"
    else:
        lines, what = ((30.0, 16.0), (15.0, 8.0)), "a share move"
    for at, points in lines:
        if pct >= at:
            return points, f"{what} of {pct:g}%"
    if EXTREME.search(head) and (INDEX.search(head) or CRYPTO_MAJOR.search(head) or METAL_OIL.search(head) or MEGA.search(head)
                                 or nw.MAJOR_ASSET.search(head)):  # "XRP crashes to its lowest since November 2024"
        return 18.0, "a record or multi-year extreme in a major market"
    if nw.INDEX_REGIME.search(head) and (INDEX.search(head) or CRYPTO_MAJOR.search(head)):
        return 32.0, "a market regime (a correction, a bear market, a halt)"  # an index shock: major (owner's replay, 2026-11-18)
    background = BACKGROUND_MOVE.search(head)
    if SHARP.search(head) and (INDEX.search(head) or CRYPTO_MAJOR.search(head) or METAL_OIL.search(head) or MEGA.search(head)) and not pct \
            and not background:  # "Bitcoin's plunge below $60,000 triggers a buying spree": the plunge is background, not the event
        return 12.0, "a sharp move in a major market (no figure stated)"
    return 0.0, ""


def states_named(head: str) -> set[str]:
    plain = re.sub(r"\bUS\$", "$", head)
    return {k for k, rx in nw.STATES.items() if rx.search(plain)}


def _hard(head: str) -> tuple[float, str]:
    states = [k for k, rx in nw.STATES.items() if rx.search(head)]
    m = ATTACK_VERB.search(head)
    if m and (states or ACTOR_OR_TARGET.search(head)) and not THREAT.search(head[:m.start()]) and not HEDGE.search(head) \
            and not NOT_AN_ATTACK.search(head) and not HARD[NATURAL_AT][2].search(head):
        return 22.0, "an attack that hit"
    for points, name, rx in HARD:
        m = rx.search(head)
        if m is None:
            continue
        if name == "an attack that hit" and THREAT.search(head[:m.start()]):
            continue  # "Iran threatens to strike ..." is a threat, not an attack
        return points, name
    if INVESTIGATION.search(head) and re.search(r"\b(sec|cftc|doj|justice department|fbi|ftc|fca|eu|european commission|regulators?|prosecutors?|"
                                                r"congress|senate)\b", head, re.I):
        return 12.0, "an official investigation"
    from app.newsdesk import editorial

    sig = editorial.significance(head)
    if sig is not None and sig.kind == "structure" and not nw.NOT_YET.search(head) and not NOT_YET.search(head):  # "NYSE picks Avalanche to settle tokenized stocks": how markets work changes
        return 16.0, "a market-structure decision"
    return 0.0, ""


def _noise(head: str) -> str:
    from app.newsdesk import quality

    t = head.strip()
    if not t:
        return "no headline"
    if OPINION.search(t):
        return "opinion, analysis or an explainer, not an event"
    if QUESTION.search(t):
        return "a question, not a report"
    if quality.is_roundup(t):
        return "a round-up of several stories, not one story"
    why = quality.feature(t)
    if why:
        return why
    if REACTION.search(t) and not _hard(t)[0]:
        return "a reaction piece, not the event"
    return ""


def judge(title: str, summary: str = "", *, outlets: int = 1, official: bool = False, age_min: float | None = None, kind: str = "news",
          facts: dict[str, Any] | None = None) -> Decision:
    """The verdict on one story or measured move. Pure: no database, no network. Only the headline's own facts count (a summary's
    background never makes a story bigger than its headline says), except the death toll a summary states for the headline's event."""
    head = re.sub(r"\s+", " ", (title or "").strip())
    live = re.match(r"^[\w .'’-]{2,40}\b(?:latest|live)\s*[:\-–—]\s*(.+)$", head, re.I)
    if live:  # "Middle East latest: Bahrain says civilian industrial facility damaged in Iran attack": the event after the label
        head = live.group(1)
    if kind in ("market", "data", "recap"):
        return _judge_move(head, facts or {}, age_min)
    noise = _noise(head)
    if noise:
        return Decision(NOISE, 0.0, noise)
    found: list[str] = []
    parts: dict[str, float] = {}
    later = nw.AFTERMATH.search(head) or AFTER.search(head)  # "... begin receiving payments from $305m bankruptcy": an old event's aftermath, not new money
    usd = 0.0 if later else nw.money_of(head)
    if usd and nw.HOLDINGS.search(head):
        usd /= 10  # what a fund holds is not money that moved: "cuts Bitmine's holdings to $11.8 billion"
    money = _money_points(usd)
    if money:
        found.append(f"${usd / 1e12:,.1f} trillion" if usd >= 1e12 else f"${usd / 1e9:,.1f}B" if usd >= 1e9 else f"${usd / 1e6:,.0f}M")
    move, move_what = _move_points(head)
    if move:
        found.append(move_what)
    deaths = nw.deaths_of(head) or (nw.deaths_of(summary or "") if re.search(r"\b(kill\w*|dead|deaths?|death toll)\b", head, re.I) else 0)
    people = 30.0 if deaths >= 50 else 24.0 if deaths >= 10 else 18.0 if deaths >= 1 else 0.0
    if deaths:
        found.append(f"{deaths} killed")
    elif re.search(r"\b(kill\w*|dead|deaths?|death toll)\b", head, re.I) and not THREAT.search(head):
        people = 16.0
        found.append("people killed")
    if INJURED.search(head) and not people:
        people = 10.0
        found.append("people hurt")
    hard, hard_name = _hard(head)
    if hard_name == "an attack that hit" and ENERGY_OR_SHIPPING.search(head):
        hard += 10.0
        hard_name = "an attack on energy or shipping"
    if hard_name.startswith("an attack") and "US" in states_named(head) and len(states_named(head) & BIG_STATE) >= 2:
        hard += 8.0  # US forces striking (or struck by) another major power: an escalation (owner, 2026-11-18: never held by the gap)
        hard_name = "a US strike involving another major power" if hard_name == "an attack that hit" else hard_name
    if hard_name == "an outage" and nw.SCALE_DISRUPTION.search(head):
        hard += 16.0  # "widespread Apple outage", "nationwide ground stop": millions of people at once (a major outage, owner's replay 2026-11-18)
    if hard_name == "a natural disaster" and (m := re.search(r"\bmagnitude[- ](\d(?:\.\d)?)", head, re.I)) and float(m.group(1)) >= 7.0:
        hard += 6.0  # a magnitude 7+ earthquake
    if hard_name == "official economic data" and (EXTREME.search(head) or nw.MACRO_EXTREME.search(head)):
        hard += 12.0  # "US inflation jumps to 4.1%, highest in two years": data at a multi-year extreme moves every market
    if later and hard_name == "a failure":
        hard, hard_name = 0.0, ""
    if hard:
        found.append(hard_name)
    sizes = sorted((money, move, people, hard), reverse=True)
    if sizes[0] == 0:
        return Decision(NOISE, 0.0, "nothing happened that the headline states: no figure that moved, nobody hurt, no completed event")
    parts["size"] = sizes[0] + 0.35 * sizes[1]  # the largest fact sets the size; a second one adds a little
    plain = re.sub(r"\bUS\$", "$", head)
    states = {k for k, rx in nw.STATES.items() if rx.search(plain)}
    reach = (6.0 if states & BIG_STATE else 0.0) + (2.0 if len(states) >= 2 else 0.0) + (5.0 if MEGA.search(head) or CENTRAL_BANK.search(head) else 0.0) \
        + (5.0 if INDEX.search(head) or CRYPTO_MAJOR.search(head) or METAL_OIL.search(head) or CHOKEPOINT.search(head) or nw.MAJOR_ASSET.search(head) else 0.0)
    if not reach and not FINANCE.search(head) and hard_name not in FINANCIAL_EVENTS:
        reach = -12.0  # not about markets, money or a major power: a local or human-interest story for this audience
        found.append("off-topic for markets")
    parts["reach"] = min(reach, 12.0)
    n = max(int(outlets or 0), 1)
    parts["sources"] = min((8.0 if n >= 3 else 5.0 if n == 2 else 0.0) + (4.0 if official else 0.0), 10.0)
    verified = bool(official) or n >= 2
    doubt = 0.0
    if HEDGE.search(head):
        doubt += 12.0
        found.append("hedged")
    if NOT_YET.search(head) and not move:
        doubt += 14.0
        found.append("nothing done yet")
    if doubt:
        parts["doubt"] = -doubt
    age = float(age_min or 0)
    fade = 0.0 if age <= 90 else min(14.0, (age - 90) / 60 * 2.0)
    if fade:
        parts["age"] = -fade
    score = BASE + sum(parts.values())
    tier = tier_of(score)
    if tier == MAJOR and not verified:
        tier = SIGNIFICANT
        found.append("one outlet: major only once confirmed")
    if kind == "news" and age > STALE_MIN:
        return Decision(NOISE, score, f"stale: first reported {int(age // 60)} h ago", found, parts, verified)
    detail = ", ".join(f"{k} {v:+.0f}" for k, v in parts.items())
    if tier == NOISE:
        return Decision(NOISE, score, f"too small to post: {', '.join(found)} (score {score:.0f} of {TIER_AT[-1][1]:.0f}; {detail})", found, parts, verified)
    return Decision(tier, score, f"{tier}: {', '.join(found)} (score {score:.0f}; {detail})", found, parts, verified)


LONG_PERIOD = _rx(r"\bover the past (?:year|\d+ (?:days|weeks|months|years))\b|\byear-to-date\b|\bytd\b|\bthis year so far\b|"
                  r"\b(?:in|over) (?:the last )?(?:\d+|two|three|six|twelve) (?:months|years)\b|\bvs\.? the \w+ over\b")
MARKET_CORE = {"SPX", "IXIC", "DJI", "NDX", "BTC", "ETH", "GOLD", "SILVER", "OIL", "US-STOCKS"}


def _canon(symbol: str) -> str:
    from app.newsdesk import editorial

    s = str(symbol or "")
    return {"^GSPC": "SPX", "^IXIC": "IXIC", "^DJI": "DJI", "^NDX": "NDX", "BTC-USD": "BTC", "ETH-USD": "ETH"}.get(s) or editorial.canon(s) or s


def _judge_move(head: str, f: dict[str, Any], age_min: float | None) -> Decision:
    """A move the desk measured itself (app/newsdesk/moves, cross-checked upstream). Its size against the asset's own history
    (app/newsdesk/urgency) is the magnitude. No cause is ever added here: a cause is only ever told from a corroborated story."""
    level = str((f.get("urgency") or {}).get("level") or "")
    context = str((f.get("urgency") or {}).get("why") or "")
    change = abs(float(f.get("change_pct") or 0)) or nw.pct_of(head)  # a data post states its move in its headline
    assets = {_canon(a) for a in (f.get("assets") or [])}
    core = bool(assets & MARKET_CORE)
    size = {"major": 36.0, "significant": 26.0}.get(level, 16.0 if f.get("exceptional") else 6.0)
    if assets & {"SPX", "IXIC", "DJI", "NDX", "US-STOCKS"}:
        lines: tuple[tuple[float, float], ...] = ((4.0, 34.0), (2.5, 26.0), (1.5, 16.0))
    elif assets & {"BTC", "ETH"}:
        lines = ((10.0, 34.0), (6.0, 26.0), (4.0, 16.0))
    elif assets & {"GOLD", "SILVER", "OIL"}:
        lines = ((6.0, 34.0), (4.0, 26.0), (2.5, 16.0))
    elif assets:
        lines = ((20.0, 30.0), (10.0, 24.0), (6.0, 14.0))  # a single stock the desk watches (Nvidia +36% on 2026-11-23)
    else:
        lines = ()
    size = max(size, next((pts for at, pts in lines if change >= at), 0.0))  # the move's size for its asset class, when history is short
    if size < 14.0 and not EXTREME.search(head):
        # neither major nor significant for its own history, not exceptional, under its asset's lines: a routine move never takes a slot
        # (production: "BTC longs liquidated: $8 million in an hour")
        return Decision(NOISE, BASE + size, f"a routine move for this asset ({change:g}%" + (f", urgency {level}" if level else "") + "): "
                        "only a major or significant move, or an exceptional one, is a post", [f"{change:g}% move"], {"size": size}, True)
    if EXTREME.search(head):
        size += 4.0  # "biggest one-day drop since March": rare for this asset
    if LONG_PERIOD.search(head):
        size = min(size, 8.0)  # "Bitcoin vs the Nasdaq over the past year": a comparison over months, not a move that just happened
    parts = {"size": size, "reach": 8.0 if core else 2.0, "sources": 8.0}
    age = float(age_min or 0)
    if age > 40:
        parts["age"] = -min(20.0, (age - 40) / 10 * 2.0)  # a market move is old fast
    score = BASE + sum(parts.values())
    tier = tier_of(score)
    found = [f"{change:g}% move"] + ([f"urgency {level}"] if level else []) + ([context] if context else [])
    detail = ", ".join(f"{k} {v:+.0f}" for k, v in parts.items())
    if tier == NOISE:
        return Decision(NOISE, score, f"a routine move: {', '.join(found)} (score {score:.0f}; {detail})", found, parts, True)
    return Decision(tier, score, f"{tier}: {', '.join(found)} (score {score:.0f}; {detail})", found, parts, True)


def judge_event(ev: Any, age_min: float | None = None) -> Decision:
    from app.newsdesk import events

    return judge(ev.title or ev.headline or "", ev.summary or "", outlets=len(events.outlets_of(ev)), official=(getattr(ev, "evidence", "") == "official"),
                 age_min=age_min, kind=getattr(ev, "kind", "news") or "news", facts=ev.facts or {})


def record(ev: Any, d: Decision | None, stage: str, outcome: str, at: Any, why: str = "") -> None:
    """The event's decision history (newest last, at most 20 lines): what the desk decided at each step and why. The same verdict again
    updates the last line's time instead of adding one. ev.why always carries the latest line in words."""
    f = dict(ev.facts or {})
    hist = list(f.get("decisions") or [])
    line = {"at": at.isoformat() if hasattr(at, "isoformat") else str(at), "stage": stage, "outcome": outcome, "why": why or (d.reason if d else "")}
    if d is not None:
        line |= {"tier": d.tier, "score": round(d.score, 1)}
    if hist and all(hist[-1].get(k) == line.get(k) for k in ("stage", "outcome", "why", "tier")):
        hist[-1]["last_at"] = line["at"]
    else:
        hist.append(line)
    f["decisions"] = hist[-20:]
    if d is not None:
        f["editor"] = d.as_dict()
    ev.facts = f
    ev.why = (line["why"] or outcome)[:1000]
