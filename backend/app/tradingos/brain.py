"""Kiwi Brain: the durable decision timeline and the market snapshot behind it.

Every event is a row in kb_events with the evidence that produced it (numbers, reasons) and where the data came from
(source + its own timestamp), so the dashboard can show what really happened, also after a restart. Nothing here invents
data: a value Hyperliquid didn't give is absent (None), and a snapshot whose last closed candle is too old says "stale".

Privacy: events with member_id=None are public (market, strategy, signal); every risk decision, order, exchange answer and
position change belongs to one member and is only ever returned to that member (or an admin). No secrets in `data`.

Retention: market observations and shadow evaluations are kept 7 days, everything else 180 days (prune() runs from the scanner job).
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import KbEvent
from app.tradingos import indicators as ind
from app.tradingos.market import INTERVALS, MarketData

KINDS = ("market", "strategy", "signal", "risk", "order", "exchange", "position", "system")
KEEP = {"market": timedelta(days=7)}
KEEP_DEFAULT = timedelta(days=180)
SECRET_WORDS = ("key", "secret", "private", "seed", "mnemonic", "signature", "token")


def _clean(data: dict[str, Any] | None) -> dict[str, Any]:
    """Defence in depth: never store anything that looks like a credential, whatever a caller passes."""
    out: dict[str, Any] = {}
    for k, v in (data or {}).items():
        if any(w in str(k).lower() for w in SECRET_WORDS):
            continue
        out[k] = _clean(v) if isinstance(v, dict) else v
    return out


def log(db: Session, kind: str, title: str, *, member_id: int | None = None, signal_id: int | None = None, coin: str | None = None,
        data: dict[str, Any] | None = None, source: str | None = None, source_at: datetime | None = None, commit: bool = False) -> KbEvent:
    """Record one event. It joins the caller's transaction (commit=True to persist it on its own, e.g. before raising)."""
    if kind not in KINDS:
        raise ValueError(f"unknown event kind {kind}")
    ev = KbEvent(at=utcnow(), kind=kind, member_id=member_id, signal_id=signal_id, coin=coin, title=title[:240], data=_clean(data),
                 source=source, source_at=source_at)
    db.add(ev)
    if commit:
        db.commit()
    return ev


def _ts(seconds: float | None) -> datetime | None:
    return datetime.fromtimestamp(seconds, tz=timezone.utc) if seconds else None


def market_snapshot(md: MarketData, coin: str, timeframe: str = "1h") -> dict[str, Any]:
    """Real Hyperliquid numbers for one market, each group tagged with its source and timestamp. Indicators come from CLOSED
    candles only. `stale` is True when the last closed candle is older than two intervals (missing data is never filled in)."""
    from app.tradingos.signals import closed

    fetched_markets, ms = md.markets()
    m = md.market(coin)
    raw = md.candles(m.coin, timeframe, 250)
    candles = closed(raw, timeframe)
    book = md.book(m.coin)
    book_at = (md._cache.get(f"book:{m.coin}") or (time.time(), None))[0]
    span = INTERVALS.get(timeframe, 3600)
    last_close_ms = (candles[-1].t + span * 1000) if candles else None
    age = (time.time() - last_close_ms / 1000) if last_close_ms else None
    closes, highs, lows, vols = [c.c for c in candles], [c.h for c in candles], [c.l for c in candles], [c.v for c in candles]
    e20, e50, e200 = ind.ema(closes, 20), ind.ema(closes, 50), ind.ema(closes, 200)
    macd = ind.macd(closes)
    bb = ind.bollinger(closes)
    support, resistance = ind.swing_levels(highs[:-1], lows[:-1], 48) if len(candles) > 10 else (None, None)
    vol_now, vol_avg = (vols[-1], sum(vols[-21:-1]) / 20) if len(vols) > 21 else (None, None)
    trend = None
    if e20 and e50:
        trend = "up" if e20[-1] > e50[-1] and closes[-1] > e50[-1] else "down" if e20[-1] < e50[-1] and closes[-1] < e50[-1] else "mixed"
    return {
        "coin": m.coin, "timeframe": timeframe, "stale": age is None or age > 2 * span,
        "market": {"source": "hyperliquid:info metaAndAssetCtxs", "at": _ts(fetched_markets),
                   "mark": m.mark, "oracle": m.oracle, "prev_day": m.prev_day, "funding_h": m.funding,
                   "open_interest": m.open_interest, "open_interest_usd": m.open_interest * m.mark, "volume_24h_usd": m.day_volume,
                   "max_leverage": m.max_leverage},
        "book": {"source": "hyperliquid:info l2Book", "at": _ts(book_at), **{k: book.get(k) for k in
                 ("bid", "ask", "mid", "spread_bps", "bid_depth_usd", "ask_depth_usd")}},
        "candles": {"source": f"hyperliquid:info candleSnapshot {timeframe}", "at": _ts(last_close_ms / 1000) if last_close_ms else None,
                    "closed": len(candles), "age_s": round(age, 1) if age is not None else None, "last_close": closes[-1] if closes else None},
        "indicators": {
            "rsi14": ind.rsi(closes), "ema20": e20[-1] if e20 else None, "ema50": e50[-1] if e50 else None,
            "ema200": e200[-1] if len(closes) >= 200 and e200 else None,
            "macd": macd[0] if macd else None, "macd_signal": macd[1] if macd else None, "macd_hist": macd[2] if macd else None,
            "atr14": ind.atr(highs, lows, closes), "vwap24": ind.vwap(highs, lows, closes, vols),
            "bb_mid": bb[0] if bb else None, "bb_upper": bb[1] if bb else None, "bb_lower": bb[2] if bb else None,
            "volume_vs_20": round(vol_now / vol_avg, 2) if vol_now is not None and vol_avg else None,
            "support": support, "resistance": resistance, "trend": trend,
        },
    }


def jsonable(x: Any) -> Any:
    """datetimes → ISO strings (for storing a snapshot as event data)."""
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, list):
        return [jsonable(v) for v in x]
    return x


def events_for(db: Session, member_id: int | None, *, limit: int = 100, before_id: int | None = None, kinds: list[str] | None = None,
               admin: bool = False, signal_id: int | None = None) -> list[KbEvent]:
    """Newest first. A member sees the public events and their OWN; an admin sees everything (or one member's with member_id)."""
    q = select(KbEvent)
    if admin:
        if member_id is not None:
            q = q.where(KbEvent.member_id == member_id)
    else:
        q = q.where(or_(KbEvent.member_id.is_(None), KbEvent.member_id == member_id))
    if before_id:
        q = q.where(KbEvent.id < before_id)
    if kinds:
        q = q.where(KbEvent.kind.in_(kinds))
    if signal_id:
        q = q.where(KbEvent.signal_id == signal_id)
    return list(db.scalars(q.order_by(KbEvent.id.desc()).limit(max(1, min(limit, 500)))))


def event_out(e: KbEvent) -> dict[str, Any]:
    return {"id": e.id, "at": e.at.isoformat(), "kind": e.kind, "coin": e.coin, "signal_id": e.signal_id, "title": e.title, "data": e.data,
            "source": e.source, "source_at": e.source_at.isoformat() if e.source_at else None, "mine": e.member_id is not None}


SCAN_EVERY_S = 900  # the tradingos.scan job's interval (app/jobs.py)
STRATEGY_INFO = {
    "trend_pullback_v1": {"name": "Trend pullback", "timeframe": "1h"},
    "breakout_4h_v1": {"name": "4h breakout", "timeframe": "4h"},
}


def _perf(rows: list[Any]) -> dict[str, Any]:
    """Results of CLOSED positions, net of fees (and funding where the exchange reported it). Losers included."""
    pnl = [float(p.realized_pnl or 0) for p in rows]
    wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x <= 0]
    eq = peak = dd = 0.0
    for x in pnl:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"trades": len(pnl), "net_pnl": round(sum(pnl), 2), "fees": round(sum(float(p.fees or 0) for p in rows), 2),
            "funding": round(sum(float(p.funding or 0) for p in rows if p.mode != "PAPER"), 4),
            "win_rate": round(len(wins) / len(pnl) * 100, 1) if pnl else None,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None, "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "profit_factor": round(sum(wins) / -sum(losses), 2) if wins and losses and sum(losses) < 0 else None,
            "max_drawdown": round(dd, 2)}


def status(db: Session, member: Any) -> dict[str, Any]:
    """What the member's Kiwi is doing, from real state only: mode, the scanner and strategies, the last data update, the
    next scheduled evaluation, and results split by PAPER / LIVE (never one combined figure)."""
    from app.models.trading import TrPosition
    from app.models.tradingos import TsSignal
    from app.tradingos import execution, signals

    s = signals.settings(db)
    acc = execution.account(db, member)
    on = {k: bool(s.strategies.get(k)) for k in STRATEGY_INFO}
    last_mkt = db.scalar(select(KbEvent).where(KbEvent.kind == "market").order_by(KbEvent.id.desc()).limit(1))
    last_eval = db.scalar(select(KbEvent).where(KbEvent.kind == "strategy").order_by(KbEvent.id.desc()).limit(1))
    test_ids = set(db.scalars(select(TsSignal.id).where(TsSignal.strategy == "test")))
    pos = db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id).order_by(TrPosition.closed_at, TrPosition.id)).all()
    open_pos = [p for p in pos if p.status in ("OPEN", "PROTECTION_FAILED") and p.size > 0]
    closed_ = [p for p in pos if p.status == "CLOSED" and p.signal_id not in test_ids]
    open_sigs = db.scalars(select(TsSignal).where(TsSignal.status.in_(signals.OPEN), TsSignal.published_at.is_not(None))
                           .order_by(TsSignal.id.desc())).all()
    scanning = s.scanner_enabled and not s.paused and any(on.values())
    if s.paused or acc.paused:
        state, why = "Blocked", "Trading is paused for everyone." if s.paused else "Your new trades are stopped (STOP NEW TRADES)."
    elif open_pos:
        state, why = "Position open", f"{len(open_pos)} bot position{'s' if len(open_pos) != 1 else ''} open, protected on the exchange."
    elif open_sigs:
        state, why = "Trade proposed", f"Signal #{open_sigs[0].number:03d} {open_sigs[0].coin} {open_sigs[0].side} is open."
    elif scanning:
        state, why = "Monitoring", "Scanning the markets every 15 minutes; no setup passes every rule right now."
    else:
        state, why = "Waiting", "Automatic strategies are switched off: they lost money in out-of-sample tests. No new automatic trades."
    nxt = None
    if scanning and last_mkt is not None:
        nxt = (last_mkt.at if last_mkt.at.tzinfo else last_mkt.at.replace(tzinfo=timezone.utc)) + timedelta(seconds=SCAN_EVERY_S)
    return {
        "mode": acc.mode, "auto": bool(acc.auto_enabled), "paused": bool(acc.paused), "global_paused": bool(s.paused),
        "state": state, "state_reason": why,
        "strategies": [{"id": k, **v, "on": on[k]} for k, v in STRATEGY_INFO.items()],
        "scanner": {"on": scanning, "every_s": SCAN_EVERY_S, "next_at": nxt.isoformat() if nxt else None,
                    "last_evaluation_at": last_eval.at.isoformat() if last_eval else None},
        "last_data": {"at": last_mkt.at.isoformat(), "source": last_mkt.source, "source_at": last_mkt.source_at.isoformat() if last_mkt.source_at else None,
                      "title": last_mkt.title} if last_mkt else None,
        "markets": sorted({p.coin for p in open_pos} | {x.coin for x in open_sigs}) or ["BTC", "ETH"],
        "performance": {"label": "Closed bot positions, after fees. Paper and live are never combined.",
                        "paper": _perf([p for p in closed_ if p.mode == "PAPER"]), "live": _perf([p for p in closed_ if p.mode == "LIVE"]),
                        "open": len(open_pos)},
    }


def candles(md: MarketData, db: Session, coin: str, timeframe: str = "1h", n: int = 120) -> dict[str, Any]:
    """CLOSED candles for the chart plus the published signals on this market as markers (real records only)."""
    from app.models.tradingos import TsSignal
    from app.tradingos.signals import closed

    m = md.market(coin)
    cs = closed(md.candles(m.coin, timeframe, n + 2), timeframe)[-n:]
    first = cs[0].t / 1000 if cs else time.time()

    def _sec(d: datetime) -> float:
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()

    sigs = [x for x in db.scalars(select(TsSignal).where(TsSignal.coin == m.coin, TsSignal.published_at.is_not(None)).order_by(TsSignal.id.desc())
                                  .limit(50)) if _sec(x.created_at) >= first][::-1]
    return {"coin": m.coin, "timeframe": timeframe, "source": f"hyperliquid:info candleSnapshot {timeframe}",
            "candles": [[c.t, c.o, c.h, c.l, c.c, c.v] for c in cs],
            "markers": [{"signal_id": x.id, "number": x.number, "side": x.side, "at": x.created_at.isoformat(), "entry": (x.entry_lo + x.entry_hi) / 2,
                         "stop": x.stop, "target": x.target, "status": x.status, "test": x.strategy == "test"} for x in sigs]}


def prune(db: Session) -> int:
    """Retention: market observations 7 days, the rest 180 days."""
    now = utcnow()
    n = db.execute(delete(KbEvent).where(KbEvent.kind == "market", KbEvent.at < now - KEEP["market"])).rowcount or 0
    n += db.execute(delete(KbEvent).where(KbEvent.kind == "strategy", KbEvent.title.like("%(shadow)%"), KbEvent.at < now - KEEP["market"])).rowcount or 0
    n += db.execute(delete(KbEvent).where(KbEvent.kind != "market", KbEvent.at < now - KEEP_DEFAULT)).rowcount or 0
    db.commit()
    return int(n)
