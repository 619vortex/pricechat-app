"""Viral data posts and the daily MARKETS GONE MAD (Viral Engine 2.0, owner 2026-11-11).

Built only from daily closes we fetch ourselves (Yahoo Finance's chart endpoint, the same source as the desk's live prices), so every
figure is measured, never reported or estimated:

  anomaly     an asset's move today against its own history: "the biggest one-day drop since April 2025" (we find the date)
  battle      two assets people argue about, over the same window: Bitcoin vs gold, Nvidia vs Bitcoin, silver vs gold
  then_now    a year ago against today
  ranking     the week's best and worst of the assets we follow
  rotation    the week's best and worst US sectors (the SPDR sector funds)

Each post carries an interest score (0-100, explained) and its told text, which holds every figure exactly as the post prints it, so the
desk's checks can verify the words against it. Billionaire wealth isn't here: there is no licensed live source for it, and we don't
copy published rankings as if we measured them.

MARKETS GONE MAD: once a day, after the US close, the single most remarkable of these, only when it clears MGM_BAR. Most days nothing
clears it and nothing is posted: "only when genuinely compelling".
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.newsdesk import markethours, safety, sources

logger = logging.getLogger(__name__)

ASSETS: list[tuple[str, str, str]] = [  # (Yahoo symbol, name, cashtag)
    ("BTC-USD", "Bitcoin", "$BTC"), ("ETH-USD", "Ether", "$ETH"), ("SOL-USD", "Solana", "$SOL"), ("^GSPC", "the S&P 500", ""), ("^IXIC", "the Nasdaq", ""),
    ("GC=F", "gold", ""), ("SI=F", "silver", ""), ("CL=F", "WTI crude", ""), ("NVDA", "Nvidia", "$NVDA"), ("TSLA", "Tesla", "$TSLA"), ("AAPL", "Apple", "$AAPL"),
    ("MSTR", "Strategy", "$MSTR"), ("COIN", "Coinbase", "$COIN"),
]
SECTORS: list[tuple[str, str]] = [("XLK", "Tech"), ("XLE", "Energy"), ("XLF", "Financials"), ("XLV", "Health care"), ("XLU", "Utilities"), ("XLI", "Industrials"),
                                  ("XLY", "Consumer discretionary"), ("XLP", "Consumer staples")]
BATTLES = [("BTC-USD", "GC=F"), ("BTC-USD", "^IXIC"), ("NVDA", "BTC-USD"), ("SI=F", "GC=F"), ("ETH-USD", "BTC-USD"), ("TSLA", "NVDA"), ("GC=F", "^GSPC")]
NAME = {s: n for s, n, _ in ASSETS} | {s: n for s, n in SECTORS}
TAG = {s: t for s, _, t in ASSETS}
CRYPTO = {"BTC-USD", "ETH-USD", "SOL-USD"}
DATA_BAR = 55  # a data post takes a slot only above this
MGM_BAR = 75
TTL = 20 * 60  # the last bar can be a live price: never older than this when a post is built (was 6 hours until 2026-11-19)
_cache: dict[str, tuple[float, list[tuple[datetime, float]]]] = {}


@dataclass
class DataPost:
    kind: str  # anomaly | battle | then_now | ranking | rotation
    subject: str  # the key it's deduplicated on
    headline: str
    lines: list[str]
    interest: int
    why: dict[str, float] = field(default_factory=dict)
    series: dict[str, list[list[float]]] = field(default_factory=dict)  # symbol: [[unix, close], ...] for the card
    bars: list[list[Any]] = field(default_factory=list)  # [name, pct] for rankings
    symbols: list[str] = field(default_factory=list)
    as_of: str = ""
    # every figure the chart shows, for the checks (owner, 2026-11-15: the caption is the headline and one takeaway; the chart carries the
    # data, so the full list is never in the caption, but the checks still verify the words and the card against all of it)
    table: list[str] = field(default_factory=list)
    last: dict[str, Any] = field(default_factory=dict)  # Last.out(): what the final value is (intraday, close, session, utc_day)

    @property
    def told(self) -> str:
        return " ".join([self.headline, *self.lines, *self.table])

    def out(self) -> dict[str, Any]:
        return {**asdict(self), "told": self.told}


# --- prices -----------------------------------------------------------------------------------------------------------------------


LIVE_MAX_AGE = markethours.FRESH  # an intraday figure older than this is not posted: the one limit every path uses (was 90 minutes)


@dataclass
class Last:
    """What the last value of a daily series IS (owner, 2026-11-19: "distinguish intraday price, daily change, official close and
    settlement. Never call an intraday quote a closing price."). The 2026-11-18 gold post said "closed at 3,724.8" at 20:00 UTC; it was a
    live futures price, and the session's final value was 3,741.9.

    kind: "intraday"  the session is still trading (Yahoo's regularMarketTime is inside currentTradingPeriod.regular): a live price;
          "close"     a stock or index session that has ended: its closing price;
          "session"   a futures session that has ended: the last value of the session as Yahoo reports it. It is NOT called a
                      settlement: the exchange's official settlement price is published separately and we don't read it;
          "utc_day"   crypto, which never closes: the price now, against the 00:00 UTC daily open.
    The daily change is always against the previous daily bar (`prev`), named as such in the words."""
    kind: str
    price: float
    prev: float
    at: datetime  # when the price was (regularMarketTime), not when we read it
    instrument: str = ""

    @property
    def change(self) -> float:
        return (self.price / self.prev - 1) * 100

    def words(self, name: str, symbol: str) -> str:
        p, ref = price(self.price, symbol), price(self.prev, symbol)
        if self.kind == "intraday":
            return f"{cap(name)} at {p} at {self.at:%H:%M} UTC, {pct(self.change)} from the previous close ({ref}), with the session still trading."
        if self.kind == "utc_day":
            return f"{cap(name)} at {p} at {self.at:%H:%M} UTC, {pct(self.change)} since the 00:00 UTC daily open ({ref})."
        if self.kind == "session":
            return f"{cap(name)} ended the session at {p}, {pct(self.change)} from the previous session ({ref})."
        return f"{cap(name)} closed at {p}, {pct(self.change)} from the previous close ({ref})."

    def out(self) -> dict[str, Any]:
        return {"kind": self.kind, "price": self.price, "prev": self.prev, "at": self.at.isoformat(), "instrument": self.instrument}


_last: dict[str, Last] = {}


def last_of(payload: dict[str, Any], series: list[tuple[datetime, float]]) -> Last | None:
    """The last value's kind from Yahoo's chart metadata; None when the metadata can't say (the post then makes no claim about it)."""
    try:
        meta = payload["chart"]["result"][0]["meta"]
        at = datetime.fromtimestamp(int(meta["regularMarketTime"]), tz=timezone.utc)
        period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
        start, end = int(period["start"]), int(period["end"])
        kind_of = str(meta.get("instrumentType") or "").upper()
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if len(series) < 2:
        return None
    price_now, prev = series[-1][1], series[-2][1]
    live_price = float(meta.get("regularMarketPrice") or price_now)
    if abs(live_price / price_now - 1) > 0.005:  # the quote and the bar disagree: we can't say which the post should print
        return None
    if kind_of == "CRYPTOCURRENCY":
        kind = "utc_day"
    elif start <= at.timestamp() < end:
        kind = "intraday"
    else:
        kind = "session" if kind_of == "FUTURE" else "close"
    return Last(kind, price_now, prev, at, kind_of)


def fresh_problem(post: dict[str, Any], now: datetime) -> str | None:
    """Right before a data post goes out: a live (intraday or crypto) figure must still be recent, or the post waits for a new one."""
    last = (post or {}).get("last") or {}
    if last.get("kind") not in ("intraday", "utc_day"):
        return None
    at = datetime.fromisoformat(str(last["at"]))
    age = now - (at if at.tzinfo else at.replace(tzinfo=timezone.utc))
    if age > LIVE_MAX_AGE:
        return f"its live price is from {at:%H:%M} UTC, {int(age.total_seconds() // 60)} min ago"
    return None


def parse_daily(payload: dict[str, Any]) -> list[tuple[datetime, float]]:
    try:
        res = payload["chart"]["result"][0]
        ts, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return []
    return [(datetime.fromtimestamp(t, tz=timezone.utc), float(c)) for t, c in zip(ts, closes) if c is not None and c > 0]


async def fetch_daily(client: httpx.AsyncClient, symbol: str, range_: str = "2y", max_age: float = TTL) -> list[tuple[datetime, float]]:
    """The daily closes (cached for max_age seconds; app/newsdesk/livedata re-reads them right before a post goes out)."""
    hit = _cache.get(f"{symbol}:{range_}")
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    try:
        r = await client.get(sources.CHART.format(symbol=symbol), params={"range": range_, "interval": "1d"}, headers=sources.UA, timeout=15)
        payload = r.json() if r.status_code == 200 else {}
        got = parse_daily(payload)
        last = last_of(payload, got)
        if last is not None:
            _last[symbol] = last
        else:
            _last.pop(symbol, None)
    except (httpx.HTTPError, ValueError) as e:
        logger.info("newsdesk datapost: %s %s", symbol, type(e).__name__)
        got = []
    if got:
        _cache[f"{symbol}:{range_}"] = (time.time(), got)
    return got


async def fetch_all(transport: Any = None) -> dict[str, list[tuple[datetime, float]]]:
    out = {}
    async with httpx.AsyncClient(transport=transport, timeout=15) as client:
        for sym in [s for s, _, _ in ASSETS] + [s for s, _ in SECTORS]:  # one at a time: a courtesy endpoint
            got = await fetch_daily(client, sym)
            if got:
                out[sym] = got
    return out


# --- formatting (the post prints exactly these strings, and they are the told text) ---------------------------------------------------


def pct(x: float) -> str:
    if abs(x) < 0.05:
        return "0.0%"
    return f"{x:+.1f}%".replace("-", "−") if abs(x) < 100 else f"{x:+,.0f}%".replace("-", "−")


def price(x: float, symbol: str = "") -> str:
    """Dollars for what trades in dollars; an index is points, without a dollar sign."""
    unit = "" if symbol.startswith("^") else "$"
    return f"{unit}{x:,.0f}" if x >= 1000 else f"{unit}{x:,.2f}"


def month(t: datetime) -> str:
    return t.strftime("%B %Y")


RECORD_MIN_DAYS = 60  # "the biggest since ..." is only a record when the last bigger move is at least this old (the rarity score's own "since" floor)


def since_words(last: datetime, day: datetime) -> str:
    """When the last bigger move was: the month and year a year or more back ("April 2025"), else the day ("5 September", "5 September 2025"
    when the year differs), so a recent mark never reads as the current month."""
    if (day - last).days >= 365:
        return month(last)
    return f"{last.day} {last:%B}" + (f" {last.year}" if last.year != day.year else "")


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _pts(series: list[tuple[datetime, float]], days: int) -> list[list[float]]:
    cut = series[-1][0] - timedelta(days=days)
    return [[t.timestamp(), round(c, 6)] for t, c in series if t >= cut]


def _ret(series: list[tuple[datetime, float]], days: int) -> float | None:
    if not series:
        return None
    want = series[-1][0] - timedelta(days=days)
    then = [c for t, c in series if t <= want]
    if not then:
        return None
    return (series[-1][1] / then[-1] - 1) * 100


def _fresh(series: list[tuple[datetime, float]], now: datetime, days: int = 4) -> bool:
    return bool(series) and now - series[-1][0] <= timedelta(days=days)


# --- the generators ---------------------------------------------------------------------------------------------------------------


def reference_problem(symbol: str, series: list[tuple[datetime, float]], last: Last | None) -> str | None:
    """Strict stale-data rule (owner, 2026-11-24): a day's change is published only when the daily bars are the price's own session and the
    session immediately before it. Production printed three day figures against the wrong reference:
      - silver 880 (11-20 01:40 UTC, "-6.7% so far today"): Yahoo still dated the bar 11-19 (New York's calendar) while CME was trading the
        11-20 session, so the live price had replaced 11-19's close and the "previous close" was 11-18's ($45.00, not $44.32);
      - bitcoin 1046 (11-21 04:03 UTC, "-9.1% since the 00:00 open"): the newest daily bar was still 11-20's, so the "open" was a day old;
      - (X 145, Nvidia, a 20:47 price posted at 00:11, is the age rule in livedata.problem.)
    None when the bars line up; otherwise why not, and nothing is posted (the next read tries again)."""
    if last is None or len(series) < 2:
        return "no daily bars to measure the day from"
    group = markethours.group_of(symbol)
    want = markethours.trading_day(last.at, group, symbol)
    have = markethours.bar_day(series[-1][0], group, symbol)
    if have != want:
        return (f"stale daily bars: the newest {symbol} bar is for {have:%d %b} but the price ({last.at:%d %b %H:%M} UTC) belongs to the "
                f"{want:%d %b} session, so the previous close would be a day old")
    prev_day = markethours.bar_day(series[-2][0], group, symbol)
    expected = markethours.previous_trading_day(want, group, symbol)
    if prev_day != expected:
        return (f"stale daily bars: the {symbol} reference bar is for {prev_day:%d %b}, not the previous session ({expected:%d %b})")
    return None


def anomaly(symbol: str, series: list[tuple[datetime, float]], now: datetime, last: Last | None = None) -> DataPost | None:
    """Today's move against the asset's own last 90 daily moves, and the last time it moved this much the same way. `last` says what the
    final value is (fetch_daily reads it from Yahoo); without it nothing is posted, because the words would have to guess."""
    quote = last if last is not None else _last.get(symbol)
    if len(series) < 120 or not _fresh(series, now, 2) or quote is None:
        return None
    if abs(quote.price - series[-1][1]) > 1e-9 or abs(quote.prev - series[-2][1]) > 1e-9:
        return None  # the description is of another fetch
    if quote.kind in ("intraday", "utc_day") and now - quote.at > LIVE_MAX_AGE:
        return None
    if why := reference_problem(symbol, series, quote):
        # a provider a session behind is a delay, not a wrong figure: logged, never counted toward the safety stop (app/newsdesk/safety)
        logger.info("newsdesk datapost: %s not measured: %s", symbol, why)
        return None
    rets = [(series[i][0], (series[i][1] / series[i - 1][1] - 1) * 100) for i in range(1, len(series))]
    day, r = rets[-1]
    group = markethours.group_of(symbol)
    why = safety.plausible_move(group, "day", r)
    if why:  # a broken daily bar is a data error, never a record move
        safety.note("implausible_move", symbol, why, window="day", change=round(r, 3), price=quote.price, ref=quote.prev)
        return None
    sd = statistics.pstdev([x for _, x in rets[-91:-1]]) or 1.0
    z = abs(r) / sd
    if z < 2.5:
        return None
    up = r > 0
    last = next((t for t, x in reversed(rets[:-1]) if (x >= r if up else x <= r)), None)
    name = NAME[symbol]
    move = "gain" if up else "drop"
    days = (day - last).days if last else 730
    if days < RECORD_MIN_DAYS:
        # production 2026-11-22 (held by the market pause): "The S&P 500's biggest one-day drop since November 2026: -3.7%". The bigger drop
        # was 5 November, 15 days before: a two-week-old mark is no record, and the month alone read as if it were long ago
        return None
    since = f"since {since_words(last, day)}" if last else "in at least two years"
    so_far = quote.kind in ("intraday", "utc_day")  # a live price: the day isn't over, and the words say so
    # a session that has ended is named as the session it was: Friday's close read on Saturday is "on Friday", never "today"
    when = "today" if so_far else markethours.day_word(quote.at, now, group)
    head = f"{cap(name)}'s biggest one-day {move} {since}{', so far today' if so_far else ('' if when == 'today' else f', {when}')}: {pct(r)}"
    lines = [f"A typical day over the last 90 moved it {sd:.1f}%: {when} {'is' if so_far else 'was'} {z:.1f} times that."]
    table = [quote.words(name, symbol)]
    why = {"rarity": round(min(60.0, 12 * z), 1), "since": 25.0 if days >= 365 else 15.0 if days >= 180 else 5.0 if days >= 60 else 0.0, "reach": 15.0 if symbol in ("BTC-USD", "^GSPC", "^IXIC", "NVDA", "GC=F") else 8.0}
    return DataPost("anomaly", f"anomaly:{symbol}", head, lines, int(min(100, sum(why.values()))), why, {symbol: _pts(series, 120)}, symbols=[symbol],
                    as_of=quote.at.isoformat(), table=table, last=quote.out())


def battle(a: str, b: str, data: dict[str, list[tuple[datetime, float]]], now: datetime, days: int = 30) -> DataPost | None:
    sa, sb = data.get(a) or [], data.get(b) or []
    if not (_fresh(sa, now) and _fresh(sb, now)):
        return None
    ra, rb = _ret(sa, days), _ret(sb, days)
    if ra is None or rb is None:
        return None
    gap = abs(ra - rb)
    if gap < 5:
        return None
    period = {30: "past 30 days", 90: "past three months", 365: "past year"}.get(days, f"past {days} days")
    win, lose = (a, b) if ra > rb else (b, a)
    head = f"{cap(NAME[a])} vs {NAME[b]} over the {period}: {pct(ra)} against {pct(rb)}"
    lines = [f"{cap(NAME[win])} leads by {gap:.1f} percentage points."]
    table = [f"{cap(NAME[a])} {pct(ra)}, {NAME[b]} {pct(rb)} over the {period}."]
    why = {"gap": round(min(55.0, gap * 2.2), 1), "rivalry": 20.0 if {a, b} & CRYPTO and {a, b} - CRYPTO else 12.0, "opposite": 15.0 if (ra > 0) != (rb > 0) else 0.0}
    if days >= 365:
        why["slow"] = -15.0  # a year-long gap is a good chart, rarely news today
    return DataPost("battle", f"battle:{a}:{b}:{days}", head, lines, int(min(100, sum(why.values()))), why, {a: _pts(sa, days), b: _pts(sb, days)}, symbols=[a, b],
                    as_of=max(sa[-1][0], sb[-1][0]).isoformat(), table=table)


def then_now(symbol: str, series: list[tuple[datetime, float]], now: datetime, days: int = 365) -> DataPost | None:
    if not _fresh(series, now):
        return None
    r = _ret(series, days)
    if r is None or abs(r) < 25:
        return None
    then = [c for t, c in series if t <= series[-1][0] - timedelta(days=days)][-1]
    head = f"{cap(NAME[symbol])} a year ago: {price(then, symbol)}. Today: {price(series[-1][1], symbol)}"
    lines = [f"That is {pct(r)} in a year."]
    why = {"size": round(min(60.0, abs(r) / 2), 1), "reach": 20.0 if symbol in ("BTC-USD", "GC=F", "NVDA", "^GSPC") else 10.0}
    return DataPost("then_now", f"then_now:{symbol}", head, lines, int(min(100, sum(why.values()))), why, {symbol: _pts(series, days)}, symbols=[symbol],
                    as_of=series[-1][0].isoformat())


def ranking(data: dict[str, list[tuple[datetime, float]]], now: datetime, days: int = 7) -> DataPost | None:
    rows = [(s, r) for s, _, _ in ASSETS if _fresh(data.get(s) or [], now) and (r := _ret(data[s], days)) is not None]
    if len(rows) < 6:
        return None
    rows.sort(key=lambda x: -x[1])
    (top, rt), (bottom, rb) = rows[0], rows[-1]
    spread = rt - rb
    if spread < 8:
        return None
    head = f"The week's winners and losers: {cap(NAME[top])} {pct(rt)}, {NAME[bottom]} {pct(rb)}"
    lines = [x for x in (group_line(rows),) if x]
    table = [", ".join(f"{cap(NAME[s])} {pct(r)}" for s, r in rows) + " (7 days)."]
    why = {"spread": round(min(60.0, spread * 2), 1), "breadth": 10.0}
    shown = rows if len(rows) <= 10 else rows[:5] + rows[-5:]  # the card has room for ten: the five best and the five worst
    return DataPost("ranking", "ranking:7d", head, lines, int(min(100, sum(why.values()))), why, bars=[[cap(NAME[s]), round(r, 1)] for s, r in shown], symbols=[s for s, _ in rows],
                    as_of=max(data[s][-1][0] for s, _ in rows).isoformat(), table=table)


GROUPS: list[tuple[str, set[str]]] = [("US stocks", {"^GSPC", "^IXIC"}), ("crypto", CRYPTO), ("commodities", {"GC=F", "SI=F", "CL=F"})]


def group_line(rows: list[tuple[str, float]]) -> str:
    """The week's takeaway in words, measured: which group did best and which worst (median of its members), "rose" / "fell" only when
    every member of that group did. No figures (the chart has them) and no asset names (the headline has the two that matter)."""
    by = {label: [r for s, r in rows if s in members] for label, members in GROUPS}
    med = {k: statistics.median(v) for k, v in by.items() if len(v) >= 2}
    if len(med) < 2:
        return ""
    best, worst = max(med, key=med.get), min(med, key=med.get)
    up, down = all(r > 0 for r in by[best]), all(r < 0 for r in by[worst])
    if up and down:
        return f"{cap(best)} rose while {worst} fell the most."
    if down:
        return f"{cap(worst)} fell the most; {best} held up best."
    if up:
        return f"{cap(best)} rose the most; {worst} did worst."
    return f"{cap(best)} did best and {worst} worst."


def rotation(data: dict[str, list[tuple[datetime, float]]], now: datetime, days: int = 7) -> DataPost | None:
    rows = [(s, r) for s, _ in SECTORS if _fresh(data.get(s) or [], now) and (r := _ret(data[s], days)) is not None]
    if len(rows) < 6:
        return None
    rows.sort(key=lambda x: -x[1])
    (top, rt), (bottom, rb) = rows[0], rows[-1]
    spread = rt - rb
    if spread < 4:
        return None
    head = f"Sector rotation this week: {NAME[top]} {pct(rt)}, {NAME[bottom]} {pct(rb)}"
    rose = sum(1 for _, r in rows if r > 0)
    lines = [f"{rose} of the {len(rows)} sectors we track rose." if 0 < rose < len(rows) else ("Every sector we track rose." if rose else "Every sector we track fell.")]
    table = [", ".join(f"{NAME[s]} {pct(r)}" for s, r in rows) + " (SPDR sector funds, 7 days)."]
    why = {"spread": round(min(55.0, spread * 5), 1), "opposite": 15.0 if rt > 0 > rb else 0.0}
    return DataPost("rotation", "rotation:7d", head, lines, int(min(100, sum(why.values()))), why, bars=[[NAME[s], round(r, 1)] for s, r in rows], symbols=[s for s, _ in rows],
                    as_of=max(data[s][-1][0] for s, _ in rows).isoformat(), table=table)


def candidates(data: dict[str, list[tuple[datetime, float]]], now: datetime) -> list[DataPost]:
    out: list[DataPost | None] = [anomaly(s, data[s], now) for s, _, _ in ASSETS if s in data]
    out += [battle(a, b, data, now, d) for a, b in BATTLES for d in (30, 365)]
    out += [then_now(s, data[s], now) for s, _, _ in ASSETS if s in data]
    out += [ranking(data, now), rotation(data, now)]
    got = [p for p in out if p is not None and not any(math.isnan(x) for x in p.why.values())]
    return sorted(got, key=lambda p: -p.interest)


MGM_KINDS = ("anomaly", "ranking", "rotation")


def markets_gone_mad(posts: list[DataPost]) -> DataPost | None:
    """The day's single most remarkable measured fact, or None: most days nothing qualifies. Only what happened today or this week: a
    one-day anomaly, the week's spread. A battle is a trend that stays true for days, so it is a data post, never the day's madness."""
    best = next((p for p in posts if p.kind in MGM_KINDS), None)
    return best if best is not None and best.interest >= MGM_BAR else None


# --- offering them to the desk ---------------------------------------------------------------------------------------------

MGM_KICKER = "MARKETS GONE MAD"
# Bitcoin vs gold over the past year (post 115), Bitcoin vs the Nasdaq over the past year (post 124): evergreen comparisons that said nothing
# new that day. A data post is only something measured today or this week: a one-day anomaly, the week's ranking or sector rotation.
EVERGREEN = ("battle", "then_now")
DATA_DAYS = 7  # the same subject (Bitcoin vs gold, the weekly ranking) at most once a week
LINK = "https://finance.yahoo.com/quote/{symbol}"


def _event(post: DataPost, key: str, now: datetime, score: float, expires: datetime, kicker: str = "") -> Any:
    from app.models.newsdesk import NdEvent

    sym = post.symbols[0] if post.symbols else "BTC-USD"
    category = "crypto" if set(post.symbols) & CRYPTO and len(post.symbols) <= 2 else "stocks" if post.kind in ("rotation",) else "markets"
    facts = {"data": post.out(), "told": post.told, "kicker": kicker, "assets": [s for s in post.symbols[:2]], "tickers": [TAG[s] for s in post.symbols[:2] if TAG.get(s)],
             "data_source": "Yahoo Finance", "outlets": ["Yahoo Finance"], "published_at": now.isoformat()}
    return NdEvent(key=key[:200], kind="data", title=post.headline[:300], headline=post.headline[:300], summary=" ".join(post.lines)[:600], topic="data",
                   label="", evidence="price data", category=category, sources=[{"name": "Yahoo Finance", "url": LINK.format(symbol=sym), "at": now.isoformat()}],
                   facts=facts, virality=float(score), parts={"interest": float(post.interest), **post.why}, first_seen=now, expires_at=expires, status="candidate")


def offer(db: Any, now: datetime, data: dict[str, list[tuple[datetime, float]]], recent: list[Any], bar: int) -> list[Any]:
    """At most one data post a day, when one is interesting enough (it ranks just above the slot bar, so verified news that clears the
    bar with room to spare still comes first), and the day's MARKETS GONE MAD after the US close when something truly qualifies."""
    if not data:
        return []
    posts = candidates(data, now)
    keys = {e.key for e in recent}
    made = []
    day_ago, week_ago = now - timedelta(hours=24), now - timedelta(days=DATA_DAYS)

    def when(e: Any) -> datetime:
        t = e.released_at or e.created_at or now
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    data_events = [e for e in recent if e.kind == "data"]
    shown = {((e.facts or {}).get("data") or {}).get("subject") for e in data_events if e.status == "released" and when(e) >= week_ago}
    # owner, 2026-11-20 (no duplicate crash stories): a one-day anomaly of an asset whose live move the desk already has (waiting or out in
    # the last 24 h) is the same story told twice. Production: silver went out as #870 "falls 6.4% in 24 hours" and #880 "biggest one-day drop"
    from app.newsdesk import editorial

    moving = {editorial.canon(a) for e in recent if e.kind in ("market", "update") and e.status in ("candidate", "released") and when(e) >= day_ago
              for a in ((e.facts or {}).get("assets") or [])}
    posts = [p for p in posts if not (p.kind == "anomaly" and {editorial.canon(s) for s in p.symbols} & moving)]
    today = now.date().isoformat()
    mgm = markets_gone_mad(posts)
    us_day = (now - timedelta(hours=4)).date().isoformat()  # the US session's date: 21:00-03:59 UTC belongs to the day that just closed
    mad_now = mgm is not None and (now.hour >= 21 or now.hour < 4) and f"mgm:{us_day}" not in keys and mgm.subject not in shown
    if not any(e.status in ("released", "candidate") and not (e.facts or {}).get("kicker") and when(e) >= day_ago for e in data_events):
        for p in posts:
            if p.interest < DATA_BAR:
                break
            if p.kind in EVERGREEN:
                continue  # a year-long comparison is true any day: it never fills a slot (owner, 2026-11-16)
            if p.subject in shown or f"data:{today}:{p.subject}" in keys or (mad_now and p.subject == mgm.subject):
                continue  # the MARKETS GONE MAD subject is posted once, as that
            ev = _event(p, f"data:{today}:{p.subject}", now, bar + 1 + (p.interest - DATA_BAR) / 5, now + timedelta(hours=8))
            db.add(ev)
            made.append(ev)
            break
    if mad_now and mgm is not None:
        end = (now + timedelta(days=1 if now.hour >= 21 else 0)).replace(hour=4, minute=30, second=0, microsecond=0)
        ev = _event(mgm, f"mgm:{us_day}", now, bar + 14, end, MGM_KICKER)
        db.add(ev)
        made.append(ev)
    if made:
        db.flush()
    return made


__all__ = ["DataPost", "candidates", "fetch_all", "markets_gone_mad", "offer"]
