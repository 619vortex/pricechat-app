"""One story, written for each platform: the shared news format for X and Telegram, tuned for market chaos.

Everything here is worked out from what the outlets wrote, or from verified prices. Nothing is added: the headline is the outlet's (or the
model's, checked against the outlets), set in capitals; tickers only when the story names the coin or company; a flag only when the headline
names the country; every figure is one a source gave.

What leads: hacks, rugs and scams, exchange failures, liquidations, sharp pumps and dumps, whale moves, billionaire fights, arrests and lawsuits,
stock crashes and big gold or silver moves. What's turned away: PR, stablecoin and product launches, routine listings, partnerships, funding
rounds and hires, unless the story is also one of the above (an exchange delisting a coin after a hack is news; a listing isn't).

Labels are earned, never decoration:
- 🚨 BREAKING: shock score 65+, under an hour old, an official source or two other outlets carrying it.
- 🔴 ALERT: a risk to people's money (hack, exploit, rug, scam, frozen withdrawals, insolvency, depeg), under three hours old.
- ⚡ JUST IN: shock score 30+, under 90 minutes old.
- Anything else carries no label. An allegation is always marked as one: a lawsuit or a charge is reported, not proven.
"""

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.newsdesk import checks

BRAND = "🥝 PRICE ALERT"
BREAKING, ALERT, JUST_IN, UPDATE = "🚨 BREAKING", "🔴 ALERT", "⚡ JUST IN", "🔄 UPDATE"
X_LEN = 280
ALLEGED = "⚖️ Allegations, not proven"


def _rx(pattern: str, flags: int = re.I) -> re.Pattern[str]:
    return re.compile(pattern, flags)


# (topic, pattern, shock weight, a risk to people's money)
TOPICS: tuple[tuple[str, re.Pattern[str], int, bool], ...] = (
    ("hack", _rx(r"\b(hack(?:ed|s)?|exploit(?:ed|s)?|drain(?:ed|s|er)?|stolen|steal(?:s|ing)?|thefts?|heist|breach(?:ed)?|cyberattack\w*|compromised)\b"), 40, True),
    ("scam", _rx(r"\b(rug ?pull(?:ed|s)?|rugged|scam(?:s|mer|mers)?|fraud(?:ulent)?|ponzi|exit scam|phishing|pig butchering|wash trading|pump[- ]and[- ]dump)\b"), 35, True),
    ("failure", _rx(r"\b(insolven\w*|bankrupt\w*|collaps\w*|outage|(?:halt|paus|suspend|freez)\w* (?:all )?(?:withdrawals|deposits|trading)|frozen|depeg(?:s|ged)?|liquidity crisis)\b"), 40, True),
    ("liquidations", _rx(r"\b(liquidat\w+|wiped out|margin call)\b"), 30, False),
    ("move", _rx(r"\b(crash(?:es|ed)?|plung\w+|tumbl\w+|nosediv\w+|sink(?:s)?|sank|slump\w*|sell-?off|bloodbath|flash crash|soar\w*|surg\w+|skyrocket\w*|rockets?|spik\w+|"
                 r"rall(?:y|ies|ied)|all-time high|record high|record low|worst (?:day|week|month)|best (?:day|week|month)|"
                 r"(?:correction|bear market) territory|trillions? (?:\w+ ){0,3}(?:wiped|erased))\b"), 28, False),
    ("whale", _rx(r"\b(whales?|dormant|satoshi-era|moves? \$|transfers? \$|dumps? \$|sold \$|buys? \$|bought \$)"), 22, False),
    ("controversy", _rx(r"\b(arrest\w*|sentenc\w*|jail\w*|prison|charged|charges|indict\w*|sues|sued|lawsuit|accus\w+|alleg\w+|probe|subpoena\w*|raid\w*|seiz\w+|"
                        r"bans?|banned|fined|sanction\w*|cuts? (?:\w+ ){0,4}off from|cuts? off|fired|ousted|resign\w*|steps? down|stepped down|feud|slams?|blasts?|lashes out|clash\w*)\b"), 25, False),
    ("macro", _rx(r"\b(rate (?:cut|hike)s?|cuts? (?:interest )?rates?|raises? (?:interest )?rates?|emergency (?:cut|meeting)|tariffs?|trade war|recession|"
                  r"government shutdown|debt ceiling|default(?:s|ed)?|circuit breaker|bank run|bailout)\b"), 25, False),
    ("etf", _rx(r"\betfs?\b"), 12, False),
)
SCAM = TOPICS[1][1]
DRAMA = _rx(r"\b(musk|elon|saylor|cz|changpeng zhao|sbf|bankman-fried|do kwon|justin sun|trump|buffett|dimon|thiel|ackman|cathie wood|vitalik|billionaires?)\b")
STOCK_WORDS = _rx(r"\b(stocks?|shares|nasdaq|s&p|dow|wall street|equities)\b")
METALS = _rx(r"\b(gold|silver)\b")
# what this channel doesn't carry, unless the title is also chaos (shock 25+ from the title alone)
BORING = _rx(r"\b(stablecoins?|partners? with|partnership|teams up|collaborat\w+|integrat\w+|to list|lists|listing|adds support|now available|goes live|"
             r"launch(?:es|ed|ing)?|rolls? out|unveil\w*|introduc\w+|debuts?|expands? (?:to|into)|raises \$|funding round|seed round|series [a-d]\b|"
             r"appoint\w*|hires?|joins as|names .{0,30}\b(?:ceo|cfo|cto|chief)|announc\w+|hackathon|conference|webinar|ama\b|roadmap|testnet|mainnet|upgrade|milestone)\b")
CONVICTED = _rx(r"\b(convicted|found guilty|pleads? guilty|pleaded guilty|sentenced)\b")
ALLEGATION = _rx(r"\b(alleg\w+|accus\w+|charged|charges|indict\w*|sues|sued|lawsuit|complaint|claims|blames?|ties (?:it|them|\w+) to|seeks? [$\w.,]* ?sanctions|motion to|court filing)\b")
TICKERS: tuple[tuple[re.Pattern[str], str, int], ...] = (  # (how the story names it, ticker, weight as a market mover)
    (_rx(r"\b(bitcoin|btc)\b"), "BTC", 10), (_rx(r"\b(ether|ethereum|eth)\b"), "ETH", 10),
    (_rx(r"\b(Solana|SOL)\b", 0), "SOL", 4), (_rx(r"\bxrp\b"), "XRP", 4), (_rx(r"\bbnb\b"), "BNB", 4),
    (_rx(r"\b(dogecoin|doge)\b"), "DOGE", 3), (_rx(r"\b(Cardano|ADA)\b", 0), "ADA", 3), (_rx(r"\b(Toncoin|TON)\b", 0), "TON", 3),
    (_rx(r"\b(Tron|TRX)\b", 0), "TRX", 3), (_rx(r"\b(Chainlink|LINK)\b", 0), "LINK", 3), (_rx(r"\b(avalanche|avax)\b"), "AVAX", 3),
    (_rx(r"\b(litecoin|ltc)\b"), "LTC", 2), (_rx(r"\b(Hyperliquid|HYPE)\b", 0), "HYPE", 3), (_rx(r"\b(Sui|SUI)\b", 0), "SUI", 2),
    (_rx(r"\busdc\b"), "USDC", 3), (_rx(r"\b(tether|usdt)\b"), "USDT", 3),
    (_rx(r"\b(microstrategy|mstr)\b"), "MSTR", 5), (_rx(r"\bcoinbase\b"), "COIN", 5), (_rx(r"\bCircle\b", 0), "CRCL", 3), (_rx(r"\brobinhood\b"), "HOOD", 3),
    (_rx(r"\b(nvidia|nvda)\b"), "NVDA", 6), (_rx(r"\b(tesla|tsla)\b"), "TSLA", 6), (_rx(r"\bApple\b", 0), "AAPL", 5),
    (_rx(r"\bgold\b"), "GOLD", 6), (_rx(r"\bsilver\b"), "SILVER", 5),
)
FLAGS: tuple[tuple[re.Pattern[str], str], ...] = (  # only a country the headline names (or a body only that country has)
    (_rx(r"(?:\bU\.S\.|\bUS\b|\bUSA\b|\bFed\b|\bCFTC\b)", 0), "🇺🇸"), (_rx(r"\b(united states|federal reserve|white house|congress|senate)\b"), "🇺🇸"),
    (_rx(r"\b(UK|U\.K\.)\b|\b(britain|british|fca)\b"), "🇬🇧"), (_rx(r"\b(EU)\b|\b(european union|ecb)\b"), "🇪🇺"),
    (_rx(r"\b(china|chinese|beijing|pboc)\b"), "🇨🇳"), (_rx(r"\bhong kong\b"), "🇭🇰"), (_rx(r"\b(japan|japanese)\b"), "🇯🇵"),
    (_rx(r"\b(south korea|south korean)\b"), "🇰🇷"), (_rx(r"\b(russia|russian)\b"), "🇷🇺"), (_rx(r"\b(india|indian)\b"), "🇮🇳"),
    (_rx(r"\bsingapore\b"), "🇸🇬"), (_rx(r"\b(uae|dubai|abu dhabi)\b"), "🇦🇪"), (_rx(r"\b(brazil|brazilian)\b"), "🇧🇷"),
    (_rx(r"\b(germany|german)\b"), "🇩🇪"), (_rx(r"\b(france|french)\b"), "🇫🇷"), (_rx(r"\b(australia|australian)\b"), "🇦🇺"),
    (_rx(r"\b(canada|canadian)\b"), "🇨🇦"), (_rx(r"\b(switzerland|swiss)\b"), "🇨🇭"), (_rx(r"\bel salvador\b"), "🇸🇻"),
    (_rx(r"\bargentina\b"), "🇦🇷"), (_rx(r"\b(turkey|türkiye)\b"), "🇹🇷"), (_rx(r"\bpakistan\b"), "🇵🇰"),
)
TOPIC_EMOJI = {"macro": "🏛️", "hack": "⚠️", "scam": "⚠️", "failure": "⚠️", "liquidations": "💥", "move": "📉", "whale": "🐋", "controversy": "⚖️", "etf": "📊"}
AMOUNT = _rx(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(billion|bn|b|million|mn|m)\b")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
UP_WORDS = _rx(r"\b(soar\w*|surg\w+|skyrocket\w*|rockets?|spik\w+|rall(?:y|ies|ied)|all-time high|record high|jumps?|best (?:day|week|month)|pump\w*)\b")
OUTLET_TAIL = re.compile(r"\s+[-|–—]\s+[A-Z][\w .'&]{1,30}$")
OUTLET_HEAD = _rx(r"^(exclusive|breaking|just in|update[d]?|alert|watch)\s*[:\-–—]\s*")
SOURCE_WORD = ("Source", "Via", "Reported by", "More from")
QUESTIONS = ("What's your take? 👇", "How do you see this playing out?", "Does this change your view on ${t}?", "Overreaction or just the start?",
             "Would you buy this dip, or wait?", "How will the market react?", "Big deal or noise?", "Who wins from this?")
QUESTION_EVERY = 4  # about one post in four asks something


@dataclass
class Brief:
    """What a story is worth, and how it's labelled. Built once and shared by both platforms."""
    shock: int
    rank: float
    label: str  # one of the labels above, or ""
    topic: str  # the strongest topic, or ""
    tickers: list[str]
    flag: str
    card: bool  # a major story: it gets a branded Kiwi card
    alleged: bool  # it reports an accusation or a suit, not a finding
    age_minutes: int
    reasons: list[str] = field(default_factory=list)
    falling: bool = False


def _amount_usd(text: str) -> float:
    best = 0.0
    for n, unit in AMOUNT.findall(text):
        best = max(best, float(n.replace(",", "")) * (1e9 if unit.lower() in ("billion", "bn", "b") else 1e6))
    return best


def tickers(story_text: str, limit: int = 3) -> list[str]:
    found = [(m.start(), tk) for pattern, tk, _ in TICKERS for m in [pattern.search(story_text)] if m]
    explicit = [(m.start(), m.group(1).upper()) for m in re.finditer(r"\$([A-Za-z]{2,6})\b", story_text)]
    out: list[str] = []
    for _, tk in sorted(found + explicit):
        if tk not in out:
            out.append(tk)
    return out[:limit]


def flag(title: str) -> str:
    """A flag only when the headline leads with the country: "UAE disclosure" deep in a headline about a US court case isn't the UAE's story."""
    lead = " ".join(title.split()[:4])
    return next((f for pattern, f in FLAGS if pattern.search(lead)), "")


def shock_of(title: str, summary: str) -> tuple[int, str, bool, list[str]]:
    """(shock score, strongest topic, a risk to money, why). The title counts in full, the summary for half."""
    score, best, topic, risk, why = 0, 0, "", False, []
    for name, pattern, weight, money in TOPICS:
        w = weight if pattern.search(title) else weight // 2 if pattern.search(summary) else 0
        if w:
            score += w
            why.append(f"{name} +{w}")
            risk = risk or (money and bool(pattern.search(title)))
            if w > best:
                topic, best = name, w
    extras = (("drama", DRAMA.search(title) is not None and score > 0, 15), ("stocks", STOCK_WORDS.search(title) is not None and topic == "move", 10),
              ("metals", METALS.search(title) is not None and topic == "move", 12))
    for name, hit, w in extras:
        if hit:
            score += w
            why.append(f"{name} +{w}")
    usd = _amount_usd(f"{title} {summary}")
    size = 15 if usd >= 1e9 else 10 if usd >= 1e8 else 5 if usd >= 1e7 else 0
    pct = max((float(p) for p in PERCENT.findall(title)), default=0.0) if topic in ("move", "liquidations") else 0.0  # a move's size, not "99% in cold storage"
    size += 15 if pct >= 20 else 10 if pct >= 10 else 5 if pct >= 5 else 0
    if size:
        score += size
        why.append(f"size +{size}")
    return score, topic, risk, why


def boring(title: str) -> str | None:
    """PR, launches, listings, partnerships: not for this channel, unless the title is also chaos."""
    m = BORING.search(title)
    if m and shock_of(title, "")[0] < 25:
        return f"routine ({m.group(0).lower()})"
    return None


def assess(story: Any, others: list[Any], now: datetime) -> Brief:
    """Shock from what the story is about, impact from who carries it and what it moves; rank adds freshness. All from the outlets' own words."""
    title, summary = story.title or "", story.summary or ""
    at = story.published_at if story.published_at is None or story.published_at.tzinfo else story.published_at.replace(tzinfo=now.tzinfo)
    age = max(0, int((now - at).total_seconds() // 60)) if at else 9999
    outlets = len({o.source for o in others if o.source != story.source})
    score, topic, risk, reasons = shock_of(title, summary)
    movers = [w for pattern, _, w in TICKERS if pattern.search(title)]
    if movers:
        score += max(movers)
        reasons.append(f"asset +{max(movers)}")
    if story.tier == 0:
        score += 10
        reasons.append("official +10")
    if outlets:
        score += min(outlets, 3) * 6
        reasons.append(f"outlets +{min(outlets, 3) * 6}")
    shock = min(score, 100)
    strongly_verified = story.tier == 0 or outlets >= 2
    if shock >= 65 and age <= 60 and strongly_verified:
        label = BREAKING
    elif risk and age <= 180:
        label = ALERT
    elif shock >= 30 and age <= 90:
        label = JUST_IN
    else:
        label = ""
    alleged = bool(ALLEGATION.search(title)) and not CONVICTED.search(title)
    falling = topic == "move" and not UP_WORDS.search(title)
    return Brief(shock, round(shock / (1 + age / 120), 2), label, topic, tickers(f"{title} {summary}"), flag(title), label == BREAKING or shock >= 60, alleged, age, reasons, falling)


def relabel(b: Brief, *, strong: bool) -> Brief:
    """BREAKING only with strong evidence (official, two outlets, or our own prices); otherwise the best label it still earns."""
    if b.label == BREAKING and not strong:
        b.label = ALERT if b.topic in ("hack", "scam", "failure") and b.age_minutes <= 180 else JUST_IN if b.age_minutes <= 90 else ""
    elif b.label != BREAKING and strong and b.shock >= 65 and b.age_minutes <= 60:
        b.label = BREAKING
    b.card = b.label == BREAKING or b.shock >= 60
    return b


def market_brief(item: dict[str, Any], change: float, ratio: float, now_age_minutes: int = 0) -> Brief:
    """A verified price move (two price sources agree). Shock grows with how far past the alert threshold it is."""
    shock = min(100, int(30 + ratio * 18 + (10 if item.get("group") in ("crypto_major", "index") else 0)))
    big = ratio >= 1.5
    label = BREAKING if ratio >= 2.5 else JUST_IN if big else ""
    tk = {"BTC-USD": "BTC", "ETH-USD": "ETH", "SOL-USD": "SOL", "XRP-USD": "XRP", "GC=F": "GOLD", "SI=F": "SILVER", "NVDA": "NVDA", "TSLA": "TSLA", "AAPL": "AAPL"}.get(item["symbol"])
    return Brief(shock, float(shock), label, "move", [tk] if tk else [], "", label == BREAKING or shock >= 60, False, now_age_minutes, [f"move x{ratio:.1f} threshold"], change < 0)


def move_verb(change: float, ratio: float) -> str:
    """How strong a word a verified move earns, by how far past its alert threshold it went. The figure itself always follows."""
    up = change > 0
    if ratio >= 2:
        return "SOARS" if up else "PLUNGES"
    if ratio >= 1.5:
        return "JUMPS" if up else "TUMBLES"
    return "RISES" if up else "FALLS"


def clean_headline(title: str) -> str:
    """The outlet's headline without its own banner words or a trailing outlet name, in capitals. The words and figures stay as written."""
    t = OUTLET_HEAD.sub("", OUTLET_TAIL.sub("", (title or "").strip())).strip()
    return re.sub(r"\s+", " ", t).rstrip(".").upper()


def _pick(key: str, options: tuple[str, ...], avoid: str | None = None) -> str:
    n = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    choice = options[n % len(options)]
    return options[(n + 1) % len(options)] if choice == avoid and len(options) > 1 else choice


def question(key: str, brief: Brief, recent: list[str] | None = None) -> str:
    """Now and then, an open question. Never on an alert (people may have lost money) or an allegation, never one that asserts anything."""
    if brief.label == ALERT or brief.topic in ("hack", "scam", "failure") or brief.alleged or int(hashlib.sha256(f"q:{key}".encode()).hexdigest(), 16) % QUESTION_EVERY:
        return ""
    pool = tuple(q for q in QUESTIONS if ("{t}" not in q or brief.tickers) and ("dip" not in q or brief.falling) and q not in (recent or []))
    return _pick(key, pool or ("What's your take? 👇",)).replace("{t}", brief.tickers[0] if brief.tickers else "")


def headline_line(headline: str, brief: Brief) -> str:
    emoji = "📈" if brief.topic == "move" and not brief.falling else TOPIC_EMOJI.get(brief.topic, "📰")
    lead = f"{brief.label}: " if brief.label else f"{emoji} "
    return f"{lead}{brief.flag + ' ' if brief.flag else ''}{headline}"


def summary_short(summary: str, limit: int = 320) -> str:
    """Concise: whole sentences only, as many as fit, two at most."""
    from app.newsdesk.telegram import sentences

    kept: list[str] = []
    for s in sentences(summary):
        if kept and len(" ".join([*kept, s])) > limit:
            break
        kept.append(s)
        if len(kept) == 2:
            break
    return " ".join(kept)


def telegram_html(headline: str, summary: str, sources: list[dict[str, Any]], brief: Brief, key: str, recent: list[str] | None = None) -> tuple[str, str, dict[str, str]]:
    """(html, plain, the choices made) for the channel. Links are the outlets' own articles."""
    word = _pick(f"s:{key}", SOURCE_WORD, (recent or [None])[-1] if recent else None)
    q = question(key, brief, recent)
    tick = " ".join(f"${t}" for t in brief.tickers)
    links = " · ".join(f'<a href="{html.escape(x["url"], quote=True)}">{html.escape(x["name"], quote=False)}</a>' for x in sources)
    names = " · ".join(x["name"] for x in sources)
    top = headline_line(headline, brief)
    parts_h = [f"<b>{BRAND}</b>", f"<b>{html.escape(top, quote=False)}</b>"]
    parts_p = [BRAND, top]
    if brief.alleged:
        parts_h.append(f"<i>{ALLEGED}</i>")
        parts_p.append(ALLEGED)
    if summary:
        parts_h.append(html.escape(summary, quote=False))
        parts_p.append(summary)
    if tick:
        parts_h.append(tick)
        parts_p.append(tick)
    parts_h.append(f"🔗 {word}: {links}")
    parts_p.append(f"🔗 {word}: {names}")
    if q:
        parts_h.append(f"💬 {html.escape(q, quote=False)}")
        parts_p.append(f"💬 {q}")
    return "\n\n".join(parts_h), "\n\n".join(parts_p), {"source_word": word, "question": q}


def x_text(headline: str, sources: list[dict[str, Any]], brief: Brief, key: str, *, with_link: bool = False) -> str:
    """X: brand, headline, tickers, the outlet named. No link by default: on X a link costs $0.20 a post instead of $0.015."""
    names = [x["name"] for x in sources][:2]
    tick = " ".join(f"${t}" for t in brief.tickers)
    tail = f"{BRAND} · via {' & '.join(names)}" if names else BRAND
    link = f"\n{sources[0]['url']}" if with_link and sources else ""
    q = question(key, brief)
    top = headline_line(headline, brief)
    alleged = ALLEGED if brief.alleged else ""
    text = ""
    for parts in ([top, alleged, tick, f"{tail}{link}", q], [top, alleged, tick, f"{tail}{link}"], [top, alleged, f"{BRAND} · via {names[0]}{link}" if names else f"{BRAND}{link}"]):
        text = "\n\n".join(p for p in parts if p).strip()
        if len(text) <= X_LEN:
            return text
    return text[:X_LEN]


def check(text: str, *, source_text: str, names: list[str], brief: Brief, max_len: int) -> checks.Result:
    """The desk's checks, on the text as posted: capitals don't change a word or a figure. Tickers are the story's own names as symbols."""
    plain = re.sub(r"https://\S+", "", text)
    told = f"{source_text} {' '.join(brief.tickers)}"
    return checks.check_post(plain, source_text=told, source_names=names, breaking=brief.label == BREAKING, max_len=max_len)
