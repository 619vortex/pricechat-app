"""The shared news engine: which stories go out, on X and Telegram alike, and the evidence for each one.

Both lanes ask this module the same question and get the same ranked answer; each then applies its own limits, deduplication and delivery.

Evidence, strongest first. A story needs one of these:
- official: the source is a regulator, a central bank or a government (tier 0).
- two outlets: a second outlet carries the same story.
- price data: the story's market claim (a move, a percent, a price level) matches our own prices at the time it was published.
  A claim the prices contradict is rejected outright.
- attributed: one reputable outlet, reporting what a named party said, confirmed, filed or disclosed. Not enough for a market figure we can't
  check, and never enough for scams, fraud or allegations: those need an official source or two outlets.

Rejected before any of that: routine PR (launches, listings, partnerships, funding, hires), stablecoin and product announcements, reassurance
("customers are safe", "not affected", "cold storage"), rumours, explainers, promotions, and anything that isn't market chaos.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.newsdesk import brief, checks, sources
from app.newsdesk.brief import BREAKING, Brief

CHAOS = {"hack", "scam", "failure", "liquidations", "move", "whale", "controversy", "macro"}
MIN_SHOCK = 30
ATTRIBUTED = re.compile(r"\b(says?|said|confirms?|confirmed|announc\w+|reports?|reported|according to|disclos(?:es|ed)|filings?|filed|data shows|shows|told|warns?|warned|"
                        r"admits?|admitted|statement|earnings|quarterly results|charges|charged|sues|sued|arrests?|arrested|seiz\w+|sentenced)\b", re.I)
REASSURE = re.compile(r"\b(not affected|unaffected|wasn[’']?t affected|weren[’']?t affected|no impact|unharmed|safe|secure|cold storage|offline|"
                      r"out of (?:\w+[’']?\s){0,2}reach|reassur\w*|fully backed|proof of reserves|safu|remains? operational|business as usual|no funds? (?:were |was )?lost)\b", re.I)
OPINION = re.compile(r"\b(?:may|might|could) (?:be|become|soon|face|see|hit|reach|lock|trigger|crash|soar|spark|end)\b|\b(predicts?|forecasts?|expects?|likely to)\b", re.I)
CRYPTO_WORDS = re.compile(r"\b(crypto\w*|tokens?|coins?|blockchain|stablecoins?|defi|world liberty|binance|coinbase|tether|kraken|bybit|okx|justin sun|saylor|changpeng zhao|cz|sbf|"
                          r"bankman-fried|do kwon|vitalik|mexc|bitget|kucoin|htx|huobi|gate\.io|bitfinex|bitmex|crypto\.com|gemini|upbit|bithumb|hyperliquid|ftx|celsius)\b", re.I)
RUMOUR = re.compile(r"\b(reportedly|rumou?rs?|unconfirmed|sources? (?:say|said|familiar)|speculat\w+)\b", re.I)
STRONG_TOPICS = {"scam"}  # plus any allegation
DOWN = re.compile(r"\b(crash\w*|plung\w+|tumbl\w+|nosediv\w+|sinks?|sank|slump\w*|sell-?off|slid(?:e|es)?|drops?|dropped|falls?|fell|dump\w*|erase[sd]?|wipe[sd]?|"
                  r"lows?|bloodbath|tank(?:s|ed)?|down|slips?|slipped|retreats?)\b", re.I)
UP = re.compile(r"\b(soar\w*|surg\w+|skyrocket\w*|rockets?|spik\w+|rall(?:y|ies|ied)|jumps?|jumped|climbs?|climbed|rises?|rose|gains?|highs?|pumps?|rebound\w*|tops?|up|"
                r"hits?|reclaims?|breaks? above)\b", re.I)
LEVEL = re.compile(r"\$(\d[\d,]*(?:\.\d+)?)(?![\d.,])\s?(k)?\b(?!\s*(?:million|billion|trillion|mn|bn|m|b)\b)", re.I)  # a price, never an amount ($428 million, $6.8M)
PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
# what we hold prices for, and how a headline names it
ASSETS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("BTC", "BTC-USD", re.compile(r"\b(bitcoin|btc)\b", re.I)), ("ETH", "ETH-USD", re.compile(r"\b(ether|ethereum|eth)\b", re.I)),
    ("SOL", "SOL-USD", re.compile(r"\b(Solana|SOL)\b")), ("XRP", "XRP-USD", re.compile(r"\bxrp\b", re.I)),
    ("SPX", "^GSPC", re.compile(r"s&p(?:\s?500)?", re.I)), ("IXIC", "^IXIC", re.compile(r"\bnasdaq\b(?![- ]listed)", re.I)), ("DJI", "^DJI", re.compile(r"\b(dow jones|the dow)\b", re.I)),
    ("NVDA", "NVDA", re.compile(r"\bnvidia\b", re.I)), ("TSLA", "TSLA", re.compile(r"\btesla\b", re.I)), ("AAPL", "AAPL", re.compile(r"\bApple\b")),
    ("COIN", "COIN", re.compile(r"\bcoinbase(?:'s)? (?:shares|stock)\b", re.I)), ("MSTR", "MSTR", re.compile(r"\b(microstrategy|strategy(?:'s)? (?:shares|stock)|mstr)\b", re.I)),
    ("META", "META", re.compile(r"\bMeta\b")), ("AMZN", "AMZN", re.compile(r"\bamazon\b", re.I)), ("MSFT", "MSFT", re.compile(r"\bmicrosoft\b", re.I)),
    ("GOOGL", "GOOGL", re.compile(r"\b(alphabet|google)\b", re.I)),
    ("GOLD", "GC=F", re.compile(r"\bgold\b", re.I)), ("SILVER", "SI=F", re.compile(r"\bsilver\b", re.I)),
    ("OIL", "BZ=F", re.compile(r"\bbrent\b", re.I)), ("OIL", "CL=F", re.compile(r"\b(oil|crude|wti)\b", re.I)),
    # the market as a whole, checked against its index
    ("IXIC", "^IXIC", re.compile(r"\b(tech|ai) stocks\b", re.I)), ("SPX", "^GSPC", re.compile(r"\b(us stocks|wall street|stock market|stocks)\b", re.I)),
)
GROUP = {w["symbol"]: w.get("group", "stock") for w in sources.DEFAULT_WATCHLIST} | {"BZ=F": "commodity"}
SYMBOLS = sorted({symbol for _, symbol, _ in ASSETS})


@dataclass
class Claim:
    asset: str
    symbol: str
    direction: int  # +1 up, -1 down, 0 not said
    pct: float | None
    level: float | None
    at: int = 0  # where the headline names the asset: at the start, the move is the story


@dataclass
class Verdict:
    story: Any
    status: str  # post | held | rejected
    why: str
    evidence: str = ""  # official | two outlets | price data | attributed
    brief: Brief | None = None
    others: list[Any] = field(default_factory=list)
    price_check: str = ""
    importance: Any = None  # app/newsdesk/editorial.Significance: admitted as important news, not as market chaos

    @property
    def ok(self) -> bool:
        return self.status == "post"


# --- what isn't for us ---------------------------------------------------------------------------------------------


def topic_of(title: str) -> tuple[int, str]:
    shock, topic, _, _ = brief.shock_of(title, "")
    return shock, topic


def _major_incident(title: str) -> bool:
    from app.newsdesk import incidents

    inc = incidents.detect(title)
    return inc is not None and inc.major


def rejected(story: Any) -> str | None:
    """Why a story isn't for either platform, whatever the evidence."""
    from app.newsdesk.telegram import SPAM

    title, summary = story.title or "", story.summary or ""
    if not (story.link or "").startswith("https://"):
        return "no secure source link"
    if checks.NOT_NEWS.search(title) or checks.is_explainer(title) or "?" in title or RUMOUR.search(summary):  # "Worried about hacks? Buy ..." is advice
        return "rumour, opinion or explainer"
    if OPINION.search(title):
        return "a forecast or an opinion, not news"
    from app.newsdesk.network import is_roundup

    if is_roundup(title):  # 2026-11-11: "Hot Inflation, AI Selloff & Anthropic IPO" went out as one post with Bloomberg's newsletter as its source
        return "a round-up of several stories: one post is one story"
    from app.newsdesk.viral import EMPTY

    if EMPTY.search(title):  # Viral Engine 2.0, no empty posts: a live blog, a "what we know", a briefing says nothing on its own
        return f"no news in it: '{EMPTY.search(title).group(0)}'"
    if SPAM.search(f"{title} {summary}"):
        return "promotional"
    if REASSURE.search(title) and not _major_incident(title):  # "Chase app offline for thousands" is the outage, not "cold storage offline"
        return "reassurance, not news"
    why = brief.boring(title)
    if why:
        return why
    shock, topic = topic_of(title)
    if topic not in CHAOS:
        return "not market chaos"
    if story.category != "crypto" and topic != "macro" and not any(p.search(title) for _, _, p in ASSETS) and not brief.STOCK_WORDS.search(title) \
            and not any(p.search(title) for p, _, _ in brief.TICKERS) and not CRYPTO_WORDS.search(title):
        return "not about a market"
    return None


# --- prices --------------------------------------------------------------------------------------------------------


def claim_of(title: str) -> Claim | None:
    """The first asset the headline names that we hold prices for, and what it says that asset did."""
    found = sorted(((m.start(), key, symbol) for key, symbol, p in ASSETS for m in [p.search(title)] if m))
    if not found:
        return None
    at, key, symbol = found[0]
    rest = title[at:]
    words = sorted([(m.start(), -1) for m in DOWN.finditer(rest)] + [(m.start(), 1) for m in UP.finditer(rest)])
    direction = words[0][1] if words else 0
    pct = next((float(m.group(1)) for m in PCT.finditer(rest)), None)
    level = None
    for m in LEVEL.finditer(rest):
        level = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        break
    if direction == 0 and pct is None and level is None:
        return None
    return Claim(key, symbol, direction, pct, level, at)


def _price_at(q: sources.Quote, ts: float) -> float | None:
    """The price at a moment: the nearest bar within 30 minutes, or else the last one in the two hours before (a gap in the data, a closed market)."""
    if not q.bars:
        return None
    bar = min(q.bars, key=lambda b: abs(b[0] - ts))
    if abs(bar[0] - ts) <= 1800 and bar[1] > 0:
        return bar[1]
    before = [b for b in q.bars if ts - 7200 <= b[0] <= ts and b[1] > 0]
    return before[-1][1] if before else None


def verify(c: Claim, q: sources.Quote | None, at: datetime) -> tuple[str, str]:
    """("confirmed" | "contradicted" | "unchecked", what the prices showed). Moves are measured over the hours before the story was published."""
    if q is None or not q.bars:
        return "unchecked", "no price data"
    ts = at.timestamp()
    now_price = _price_at(q, ts)
    if now_price is None:
        return "unchecked", "no price at the story's time"
    limits = sources.THRESHOLDS.get(GROUP.get(c.symbol, "stock"), sources.THRESHOLDS["stock"])
    least = limits["24h"] * 0.5
    moves = []
    for hours in (1, 4, 24, 48):
        then = _price_at(q, ts - hours * 3600)
        if then:
            moves.append((hours, (now_price - then) / then * 100))
    if not moves:
        return "unchecked", "not enough price history"
    shown = ", ".join(f"{h}h {m:+.1f}%" for h, m in moves)
    same_way = [(h, m) for h, m in moves if c.direction == 0 or (m > 0) == (c.direction > 0)]
    # confirmed: a price level close to the headline's, a window matching its percent, or (no figure given) a real move its way
    if c.level is not None and abs(now_price - c.level) / c.level * 100 <= 3:
        return "confirmed", f"{c.asset} at {now_price:,.2f}, near the ${c.level:,.0f} in the headline"
    if c.pct is not None:
        hit = next(((h, m) for h, m in same_way if abs(abs(m) - c.pct) <= max(1.5, c.pct * 0.35)), None)
        if hit:
            return "confirmed", f"{c.asset} {hit[0]}h {hit[1]:+.1f}% against {c.pct:g}% in the headline"
    elif c.direction and c.level is None and any(abs(m) >= least for _, m in same_way):
        return "confirmed", f"{c.asset} {'up' if c.direction > 0 else 'down'}: {shown}"
    # contradicted: a level well away from the real price, or the biggest move going the other way
    if c.level is not None and abs(now_price - c.level) / c.level * 100 > 8:
        return "contradicted", f"{c.asset} was at {now_price:,.2f}, not ${c.level:,.0f}"
    biggest = max(moves, key=lambda hm: abs(hm[1]))
    if c.direction and (biggest[1] > 0) != (c.direction > 0) and abs(biggest[1]) >= least:
        return "contradicted", f"{c.asset} moved the other way ({shown})"
    return "unchecked", f"the prices don't settle it ({shown})"


# --- the verdict ---------------------------------------------------------------------------------------------------


def evaluate(story: Any, rows: list[Any], quotes: dict[str, sources.Quote], now: datetime, *, at: datetime | None = None) -> Verdict:
    """at: when to judge freshness from (now, unless replaying the day). Price claims are always judged at the story's own time."""
    from app.newsdesk import engine

    why = rejected(story)
    if why:
        return Verdict(story, "rejected", why)
    at = at or now
    others = engine.corroboration(story, rows)
    b = brief.assess(story, others, at)
    if b.shock < MIN_SHOCK:
        return Verdict(story, "rejected", f"not enough market impact (shock {b.shock})", brief=b, others=others)
    published = story.published_at if story.published_at is None or story.published_at.tzinfo else story.published_at.replace(tzinfo=timezone.utc)
    claim = claim_of(story.title)
    lead = len(" ".join(story.title.split()[:3]))
    subject = claim is not None and claim.at < lead  # "Shares in SpaceX plunge as tech stocks fall": the Nasdaq proves nothing about SpaceX
    price_state, price_note = ("unchecked", "")
    if claim is not None and published is not None:
        price_state, price_note = verify(claim, quotes.get(claim.symbol), published)
        if price_state == "contradicted":
            return Verdict(story, "rejected", f"the prices contradict it: {price_note}", brief=b, others=others, price_check=price_note)
    strong_needed = b.alleged or b.topic in STRONG_TOPICS or bool(brief.SCAM.search(story.title))
    outlets = {o.source for o in others if o.source != story.source}
    evidence = "official" if story.tier == 0 else "two outlets" if outlets else "price data" if price_state == "confirmed" and subject else ""
    if strong_needed and evidence not in ("official", "two outlets"):
        return Verdict(story, "held", "a scam, fraud or allegation needs an official source or a second outlet", brief=b, others=others, price_check=price_note)
    if not evidence:
        market_figure = (claim is not None and (claim.pct is not None or claim.level is not None)) or (claim is not None and not subject and b.topic == "move")
        if market_figure or (PCT.search(story.title) and b.topic == "move"):
            return Verdict(story, "held", f"a market figure we couldn't check ({price_note or 'no prices for it'}): needs a second outlet", brief=b, others=others, price_check=price_note)
        if ATTRIBUTED.search(story.title) or ATTRIBUTED.search(" ".join(_first_sentence(story.summary))):
            evidence = "attributed"
        else:
            return Verdict(story, "held", "one outlet, and it doesn't say who says so", brief=b, others=others, price_check=price_note)
    if claim is not None and claim.at == 0 and claim.direction and b.topic != "move":
        # "Bitcoin rebounds as markets shrug off the hack": the story is the price, not the hack
        b.topic, b.falling = "move", claim.direction < 0
        if b.label == brief.ALERT:
            b.label = brief.JUST_IN if b.age_minutes <= 90 else ""
    b = brief.relabel(b, strong=evidence in ("official", "two outlets", "price data"))
    return Verdict(story, "post", "", evidence, b, others, price_note)


def _first_sentence(summary: str | None) -> list[str]:
    from app.newsdesk.telegram import sentences

    return sentences(summary or "")[:1]


def candidates(rows: list[Any], quotes: dict[str, sources.Quote], now: datetime, *, max_age: timedelta, categories: dict[str, bool] | None = None) -> tuple[list[Verdict], list[Verdict]]:
    """(what may go out, ranked; everything else, with why). Only fresh stories are judged; one verdict per story."""
    ok, other = [], []
    known = [r for r in rows if r.published_at is not None and _utc(r.published_at, now) <= now + timedelta(minutes=10)]  # nothing from after `now` counts
    for story in known:
        if now - _utc(story.published_at, now) > max_age or (categories is not None and not categories.get(story.category or "", False)):
            continue
        v = evaluate(story, known, quotes, now)
        (ok if v.ok else other).append(v)
    ok.sort(key=lambda v: -v.brief.rank)
    return ok, other


def _utc(at: datetime, now: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=now.tzinfo)


def needed_symbols(rows: list[Any], now: datetime, max_age: timedelta) -> set[str]:
    """The prices worth fetching: only for fresh headlines that make a market claim."""
    out = set()
    for r in rows:
        if r.published_at is not None and now - _utc(r.published_at, now) <= max_age:
            c = claim_of(r.title or "")
            if c is not None:
                out.add(c.symbol)
    return out


def moved_lately(headline: str, recent: list[str]) -> bool:
    """One move per asset per cooldown, as news or as a price post: `recent` are the headlines carried in that time."""
    keys = asset_keys(headline)
    if not keys or claim_of(headline or "") is None:
        return False
    return any(keys & asset_keys(r) for r in recent if claim_of(r or "") is not None)


def asset_keys(text: str) -> set[str]:
    """Which priced assets a post is about: one move per asset per cooldown, whether it came as news or as a price post."""
    return {key for key, _, p in ASSETS if p.search(text or "")}


__all__ = ["BREAKING", "Claim", "Verdict", "asset_keys", "candidates", "claim_of", "evaluate", "rejected", "verify"]
