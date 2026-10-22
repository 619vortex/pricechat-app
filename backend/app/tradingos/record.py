"""/signal track record: every plan /signal shows is stored (no user ids) and graded on the real candles that followed.

Grading (closed candles of the plan's own timeframe, opening at or after it was shown):
  FILLED   a candle traded through the entry zone (within 24 candles, else EXPIRED unfilled)
  STOP     the stop traded after the fill; also when stop and target sit inside one candle, and a stop inside the fill
           candle itself (conservative: the order within a candle is unknown, so a target there isn't counted)
  TARGET   the target traded first
  EXPIRED  filled but neither level within 72 candles: R at that candle's close
R is measured from the zone's mid (the stop's distance = 1R). No fees: these are chart ideas, not trades.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsSignalIdea
from app.tradingos.market import INTERVALS, MarketData

logger = logging.getLogger(__name__)

FILL_WITHIN = 24  # candles to reach the entry zone
RESOLVE_WITHIN = 72  # candles after the fill to reach a level
GIVE_UP = 2 * (FILL_WITHIN + RESOLVE_WITHIN)  # candles: still open by then, its candles can't be read (delisted, venue down): SKIPPED
MAX_READ = {"okx": 300}  # candles one read returns (else 1000): an older idea's opening candles are out of reach
DEDUPE = timedelta(hours=1)
SAME_ENTRY = 0.0025  # entries within 0.25% count as the same plan (the next candle nudges the zone a little)
GRADED_VENUES = ("hyperliquid", "binance", "bybit", "okx")  # DEX pools: SKIPPED
OPEN = ("OPEN", "FILLED")
DONE = ("TARGET", "STOP", "EXPIRED")
FAMILIES = ("pullback", "breakout", "range", "momentum", "full")
LABEL = {"pullback": "pullback ideas", "breakout": "breakout ideas", "range": "range ideas", "momentum": "momentum ideas",
         "full": "full setups", "other": "other ideas"}
MIN_GRADED = 10
WINDOW = 50  # the record line: the last N graded ideas


def family(idea: str, full: bool) -> str:
    """signals.watch_setup's idea wording → its family."""
    if full:
        return "full"
    t = idea.lower()
    for words, fam in ((("pullback", "dip", "bounce", "pop"), "pullback"), (("break",), "breakout"), (("range",), "range"),
                       (("momentum",), "momentum")):
        if any(w in t for w in words):
            return fam
    return "other"


def log_plan(db: Session, listing: Any, tf: str, plan: Any, chat_type: str) -> TsSignalIdea:
    """Store a shown plan, or return the one already stored: same market, timeframe, side and entry within the hour."""
    lo, hi = min(plan.entry), max(plan.entry)
    mid = (lo + hi) / 2
    since = utcnow() - DEDUPE
    for x in db.scalars(select(TsSignalIdea).where(TsSignalIdea.coin == listing.symbol, TsSignalIdea.venue == listing.venue,
                                                   TsSignalIdea.timeframe == tf, TsSignalIdea.side == plan.side,
                                                   TsSignalIdea.created_at >= since)):
        if abs((x.entry_lo + x.entry_hi) / 2 - mid) <= SAME_ENTRY * mid:
            return x
    row = TsSignalIdea(venue=listing.venue, kind=getattr(listing, "kind", "perp") or "perp", coin=listing.symbol, tag=listing.tag[:64],
                       timeframe=tf, side=plan.side, entry_lo=lo, entry_hi=hi, stop=plan.stop, target=plan.target, idea=plan.idea or "",
                       family=family(plan.idea or "", plan.full), full=bool(plan.full), score=float(plan.score), chat_type=chat_type,
                       status="OPEN" if listing.venue in GRADED_VENUES else "SKIPPED")
    db.add(row)
    db.commit()
    return row


def _ms(d: datetime) -> float:
    return d.timestamp() * 1000


def grade(x: TsSignalIdea, candles: list[Any], now: datetime | None = None) -> bool:
    """Replay the idea on `candles` (any order; only closed ones opening after it was shown count). True when it changed."""
    span = INTERVALS[x.timeframe] * 1000
    now_ms = _ms(now or utcnow())
    cs = sorted((c for c in candles if c.t >= int(_ms(x.created_at)) and c.t + span <= now_ms), key=lambda c: c.t)
    long = x.side == "LONG"
    mid = (x.entry_lo + x.entry_hi) / 2
    risk = abs(mid - x.stop) or mid * 1e-9
    before = (x.status, x.r)

    def hit_stop(c: Any) -> bool:
        return c.l <= x.stop if long else c.h >= x.stop

    def hit_target(c: Any) -> bool:
        return c.h >= x.target if long else c.l <= x.target

    def done(status: str, r: float, c: Any) -> None:
        x.status, x.r = status, round(r, 3)
        x.resolved_at = datetime.fromtimestamp((c.t + span) / 1000, tz=timezone.utc)

    fill = next((i for i, c in enumerate(cs[:FILL_WITHIN]) if c.l <= x.entry_hi and c.h >= x.entry_lo), None)
    if fill is None:
        if len(cs) >= FILL_WITHIN:
            done("EXPIRED", 0.0, cs[FILL_WITHIN - 1])
            x.r = None  # never filled: no result
        return (x.status, x.r) != before
    x.status = "FILLED"
    x.filled_at = datetime.fromtimestamp(cs[fill].t / 1000, tz=timezone.utc)
    if hit_stop(cs[fill]):  # stopped inside the fill candle (a target there is not counted: order unknown)
        done("STOP", -1.0, cs[fill])
        return True
    after = cs[fill + 1:fill + 1 + RESOLVE_WITHIN]
    for c in after:
        if hit_stop(c):  # both levels in one candle: STOP (conservative)
            done("STOP", -1.0, c)
            return True
        if hit_target(c):
            done("TARGET", abs(x.target - mid) / risk, c)
            return True
    if len(after) >= RESOLVE_WITHIN:
        last = after[-1]
        done("EXPIRED", (last.c - mid) / risk * (1 if long else -1), last)
    return (x.status, x.r) != before


def grade_all(db: Session, md: MarketData, vs: Any = None) -> dict[str, int]:
    """The tradingos.ideas job: grade every open idea, one candle read per market and timeframe. An idea still open after
    GIVE_UP candles can't be graded (no candles for it): SKIPPED, so it isn't read again every run."""
    from app.tradingos import venues

    rows = list(db.scalars(select(TsSignalIdea).where(TsSignalIdea.status.in_(OPEN))))
    groups: dict[tuple[str, str, str, str], list[TsSignalIdea]] = {}
    for x in rows:
        groups.setdefault((x.venue, x.kind, x.coin, x.timeframe), []).append(x)
    now, changed, errors, skipped = utcnow(), 0, 0, 0
    for (venue, kind, coin, tf), xs in groups.items():
        age = {x.id: int((now - x.created_at).total_seconds() // INTERVALS[tf]) for x in xs}  # candles since it was shown
        readable = [x for x in xs if age[x.id] + 3 <= MAX_READ.get(venue, 1000)]
        if readable:
            n = max(age[x.id] for x in readable) + 3
            try:
                if venue == "hyperliquid":
                    cs = md.candles(coin, tf, n)
                else:
                    listing = venues.Listing(venue=venue, kind=kind, base=coin, quote="", symbol=coin, price=0.0, change_24h=None, volume_24h=None)
                    cs = venues.candles(listing, tf, n, md, vs or venues.client())
                changed += sum(grade(x, cs, now) for x in readable)
            except Exception as e:  # noqa: BLE001 - one market's odd answer never stops the others; retried next run
                logger.info("idea grading: no %s %s candles (%s: %s)", coin, tf, type(e).__name__, e)
                errors += 1
        for x in xs:
            if x.status in OPEN and age[x.id] > GIVE_UP:
                x.status = "SKIPPED"
                skipped += 1
    db.commit()
    return {"open": len(rows), "changed": changed, "errors": errors, "skipped": skipped}


def _graded(db: Session, fam: str | None, limit: int = WINDOW) -> list[TsSignalIdea]:
    """The last `limit` ideas that filled and resolved (the ones a 'hit target' rate is about)."""
    q = select(TsSignalIdea).where(TsSignalIdea.status.in_(DONE), TsSignalIdea.filled_at.is_not(None))
    if fam:
        q = q.where(TsSignalIdea.family == fam)
    return list(db.scalars(q.order_by(TsSignalIdea.resolved_at.desc(), TsSignalIdea.id.desc()).limit(limit)))


def record_line(db: Session, fam: str | None) -> str:
    """'📈 Track record: pullback ideas 58% hit target (last 50 graded)': the family's when it has ≥10 graded, else overall, else ''."""
    for f in (fam, None) if fam else (None,):
        xs = _graded(db, f)
        if len(xs) >= MIN_GRADED:
            hit = sum(x.status == "TARGET" for x in xs) / len(xs) * 100
            return f"📈 Track record: {LABEL.get(f, f) if f else '/signal ideas'} {hit:.0f}% hit target (last {len(xs)} graded)"
    return ""


def record_table(db: Session) -> str:
    """/record: per family, over everything graded: how many filled, the win rate of the filled ones, and the average R."""
    x = TsSignalIdea
    filled = x.filled_at.is_not(None)
    # per family, counted in the database (the graded ideas only ever grow): n, filled, wins of the filled, sum and count of R
    q = (select(x.family, func.count(), func.count(x.filled_at), func.sum(case((and_(filled, x.status == "TARGET"), 1), else_=0)),
                func.sum(case((filled, x.r))), func.count(case((filled, x.r))))
         .where(x.status.in_(DONE)).group_by(x.family))
    by = {fam: [int(n or 0), int(f or 0), int(w or 0), float(rs or 0.0), int(rn or 0)] for fam, n, f, w, rs, rn in db.execute(q)}
    if not by:
        pending = int(db.scalar(select(func.count()).select_from(x).where(x.status.in_(OPEN))) or 0)
        return ("📈 <b>/signal track record</b>\nNothing graded yet" + (f" ({pending} idea{'s' if pending != 1 else ''} being followed)." if pending else ".")
                + "\nEvery /signal plan is followed on real candles: filled when price reaches the entry, then target or stop.")
    by[None] = [sum(v[i] for v in by.values()) for i in range(5)]  # type: ignore[index]
    lines = ["📈 <b>/signal track record</b>", "<code>family      n  fill  win   avgR</code>"]
    for fam in (*FAMILIES, "other", None):
        if fam not in by:
            continue
        n, f, wins, r_sum, r_n = by[fam]
        name = "all" if fam is None else fam
        win = f"{wins / f * 100:3.0f}%" if f else "   –"
        avg = f"{r_sum / r_n:+.2f}" if r_n else "  –"
        lines.append(f"<code>{name:<10}{n:>3} {f / n * 100:4.0f}% {win} {avg:>6}</code>")
    lines += ["", "fill = reached the entry zone · win = target before stop (of the filled) · avgR = result in risk units.",
              "Chart ideas on request, not trades: no fees. Not financial advice."]
    return "\n".join(lines)
