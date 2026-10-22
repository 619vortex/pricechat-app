"""Kiwi Brain 2.0: the services behind the trading command center. Everything is read from real records (kb_events, signals,
positions, scheduled jobs) or from Hyperliquid; what isn't available is reported as unavailable, never filled in.

  overview()     health per component (exchange, market feed, strategy engine, position monitoring, protection), the
                 member's positions with their verified protection and the strategy behind them, exposure
  evaluate()     a live, rule-by-rule evaluation of one market by the real strategy code, grouped into pipeline nodes
  Flow           an order-flow recorder (Hyperliquid l2Book + recentTrades, sampled while someone watches): liquidity heatmap,
                 buy/sell volume, cumulative volume delta, large trades. Only the history actually recorded is returned.
  performance()  the member's closed positions (live and paper kept apart) for the analytics
  replay()       a historical signal/position: candles fetched for that window, its logged events, MAE/MFE
  config         Teach Your Kiwi: versioned per-member filters on the published signals, applied to AUTO entries only
                 (they can only REMOVE trades; the risk engine still checks everything)
  evidence()     the grounded context for Ask Kiwi (ids the answer must cite)
"""

import math
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.members import Member
from app.models.routing import ScheduledJob
from app.models.trading import TrAccount, TrPosition
from app.models.tradingos import KbConfig, KbEvent, TsSignal
from app.tradingos import brain, execution, market, risk, signals, terminal

STRATEGIES = {"trend_pullback_v1": "Trend pullback (1h)", "breakout_4h_v1": "4h breakout", "intraday_15m_v1": "15m / 30m intraday (Kiwi Kitchen)",
              "test": "Manual setup", "manual": "Manual setup"}


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime | None) -> str | None:
    d = _aware(d)
    return d.isoformat() if d else None


def _age(d: datetime | None) -> float | None:
    d = _aware(d)
    return round((utcnow() - d).total_seconds(), 1) if d else None


# ---------------------------------------------------------------- overview: health per component, positions, exposure
def _job(db: Session, name: str) -> dict[str, Any] | None:
    j = db.get(ScheduledJob, name)
    if j is None:
        return None
    return {"enabled": j.enabled, "every_s": j.interval_seconds, "last_status": j.last_status, "last_run_at": _iso(j.last_run_at),
            "last_finished_at": _iso(j.last_finished_at), "next_run_at": _iso(j.next_run_at), "last_error": (j.last_error or "")[:300] or None,
            "duration_ms": j.last_duration_ms, "age_s": _age(j.last_finished_at or j.last_run_at)}


def engine_state(db: Session) -> dict[str, Any]:
    """Why the strategy engine is (or isn't) evaluating, from its settings and its job's real state."""
    s = signals.settings(db)
    job = _job(db, "tradingos.scan")
    on = [k for k in ("trend_pullback_v1", "breakout_4h_v1") if s.strategies.get(k)]
    late = bool(job and job["age_s"] is not None and job["age_s"] > 2.5 * job["every_s"])
    if s.paused:
        st, why = "paused", "Trading is paused for everyone by the owner."
    elif not s.scanner_enabled:
        st, why = "off", "The market scanner is switched off by the owner."
    elif job is None or not job["enabled"]:
        st, why = "off", "The scan job is disabled."
    elif job["last_status"] == "failed":
        st, why = "failing", f"The last scan failed: {job['last_error'] or 'unknown error'}"
    elif late:
        st, why = "late", f"No scan for {int(job['age_s'] // 60)} min (expected every {job['every_s'] // 60} min)."
    elif not on:
        st, why = "shadow", ("Shadow mode: both strategies are switched off for trading (they lost money out of sample), but every "
                             "market is still evaluated every 15 min and logged. No signals, no trades.")
    else:
        st, why = "active", f"Evaluating every {job['every_s'] // 60} min: {', '.join(STRATEGIES[k] for k in on)}."
    last_eval = db.scalar(select(KbEvent.at).where(KbEvent.kind == "strategy").order_by(KbEvent.id.desc()).limit(1))
    return {"state": st, "reason": why, "strategies": [{"id": k, "name": STRATEGIES[k], "on": bool(s.strategies.get(k))}
                                                       for k in ("trend_pullback_v1", "breakout_4h_v1")],
            "job": job, "next_at": job["next_run_at"] if job and st in ("active", "shadow") else None, "last_evaluation_at": _iso(last_eval)}


def overview(db: Session, member: Member, md: market.MarketData) -> dict[str, Any]:
    t = terminal.terminal(db, member)
    acc = execution.account(db, member)
    s = signals.settings(db)
    ov, sync = t["overview"], t["sync"]
    # market feed: a live read now (2 s cache), separate from the engine's own snapshots
    try:
        at, _ = md.mids()
        feed = {"state": "live", "at": datetime.fromtimestamp(at, tz=timezone.utc).isoformat(), "age_s": round(time.time() - at, 1),
                "source": "hyperliquid:info allMids"}
        if feed["age_s"] > 30:
            feed["state"] = "stale"
    except market.MarketError as e:
        feed = {"state": "down", "error": str(e)[:200], "source": "hyperliquid:info allMids"}
    last_snap = db.scalar(select(KbEvent).where(KbEvent.kind == "market").order_by(KbEvent.id.desc()).limit(1))
    feed["engine_snapshot_at"] = _iso(last_snap.at) if last_snap else None
    if acc.mode == "PAPER":
        exch = {"state": "paper", "label": "Paper trading: no exchange orders", "at": sync.get("at")}
    elif sync.get("error") and not sync.get("at"):
        exch = {"state": "down", "label": sync["error"]}
    else:
        exch = {"state": "stale" if sync.get("stale") else "connected", "label": f"Hyperliquid {acc.mode.lower()}", "at": sync.get("at"),
                "age_s": sync.get("age_s"), "error": sync.get("error")}
    track = _job(db, "tradingos.track")
    mon_ok = bool(track and track["last_status"] in ("success", "running") and track["age_s"] is not None and track["age_s"] < 180)
    monitoring = {"state": "ok" if mon_ok else "late" if track else "off", "job": track,
                  "label": "Positions reconciled with the exchange every minute" if mon_ok else "Position monitoring is late or not running"}
    sig_ids = [p["signal_id"] for p in t["positions"]]
    sigs = {x.id: x for x in db.scalars(select(TsSignal).where(TsSignal.id.in_(sig_ids)))} if sig_ids else {}
    positions, at_stop, notional = [], 0.0, 0.0
    for p in t["positions"]:
        sig = sigs.get(p["signal_id"])
        protected = p["sl_status"] in ("ACTIVE", "SIMULATED")
        loss = abs((p["entry_px"] or 0) - (p["stop"] or 0)) * p["size"]
        at_stop += loss
        notional += p["size_usd"] or (p["size"] * (p["entry_px"] or 0))
        positions.append(p | {
            "strategy": sig.strategy if sig else None, "strategy_name": STRATEGIES.get(sig.strategy, sig.strategy) if sig else None,
            "signal_number": sig.number if sig else None, "signal_status": sig.status if sig else None,
            "age_s": _age(datetime.fromisoformat(p["opened_at"])) if p["opened_at"] else None,
            "protection": {"stop": p["sl_status"], "tp1": p["tp1_status"], "tp2": p["tp2_status"],
                           "verified": p["sl_status"] == "ACTIVE", "label": (
                               "Stop-loss resting on Hyperliquid (verified in the open orders)" if p["sl_status"] == "ACTIVE" else
                               "Simulated stop (paper)" if p["sl_status"] == "SIMULATED" else
                               "Stop-loss not verified just now: Hyperliquid couldn't be read" if p["sl_status"] == "UNVERIFIED" else
                               f"Stop-loss {p['sl_status'].replace('_', ' ').lower()}: NOT protected")},
            "managed": ("The exchange-side stop and take-profits close it; the monitor reconciles every minute. "
                        + ("New entries from this strategy are off (shadow mode)." if sig and not s.strategies.get(sig.strategy) else "")),
            "loss_at_stop": round(loss, 2), "protected": protected})
    equity = ov.get("equity")
    exposure = {"notional": round(notional, 2), "loss_at_stop": round(at_stop, 2), "equity": equity,
                "notional_pct": round(notional / equity * 100, 1) if equity else None,
                "loss_at_stop_pct": round(at_stop / equity * 100, 2) if equity else None,
                "unrealized": ov.get("unrealized"), "realized_today": ov.get("realized_today")}
    protection = {"state": "none" if not positions else "ok" if all(x["protected"] for x in positions) else
                  "unverified" if all(x["protection"]["stop"] in ("ACTIVE", "SIMULATED", "UNVERIFIED") for x in positions) else "unprotected",
                  "open": len(positions), "verified": sum(1 for x in positions if x["protection"]["verified"])}
    return {"mode": acc.mode, "auto": bool(acc.auto_enabled), "auto_until": _iso(acc.auto_until), "paused": bool(acc.paused),
            "global_paused": bool(s.paused),
            "health": {"exchange": exch, "market_feed": feed, "engine": engine_state(db), "monitoring": monitoring, "protection": protection},
            "positions": positions, "other_positions": t["other_positions"], "exposure": exposure,
            "limits": {"max_leverage": acc.max_leverage, "max_loss_per_trade_usd": acc.max_loss_per_trade_usd, "max_positions": acc.max_positions,
                       "daily_loss_limit_usd": acc.daily_loss_limit_usd, "allowed_coins": acc.allowed_coins, "max_allocation_pct": acc.max_allocation_pct},
            "config": current_config(db, member)["config"]}


# ---------------------------------------------------------------- live evaluation (the real strategy code, rule by rule)
NODES = ("market", "trend", "momentum", "orderflow", "signal", "risk", "execution", "position")


def evaluate(db: Session, member: Member, md: market.MarketData, coin: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    m = md.market(coin)
    book = md.book(m.coin)
    t_data = (time.perf_counter() - t0) * 1000
    s = signals.settings(db)
    runs = []
    for sid, fn in (("trend_pullback_v1", lambda: signals.analyze(md, m.coin, s.timeframe, s.min_volume_usd, s.max_spread_bps, s.min_rr)),
                    ("breakout_4h_v1", lambda: signals.analyze_breakout(md, m.coin, s.min_volume_usd, s.max_spread_bps, s.min_rr))):
        t1 = time.perf_counter()
        a = fn()
        runs.append({"strategy": sid, "name": STRATEGIES[sid], "enabled": bool(s.strategies.get(sid)), "decision": a.decision,
                     "reasons": a.reasons, "checks": a.checks, "indicators": a.indicators, "entry": list(a.entry) if a.entry else None,
                     "stop": a.stop, "target": a.target, "rr": a.rr, "ms": round((time.perf_counter() - t1) * 1000, 1)})
    bd, ad = book.get("bid_depth_usd") or 0, book.get("ask_depth_usd") or 0
    imb = (bd - ad) / (bd + ad) if bd + ad else None
    acc = execution.account(db, member)
    prop = next((r for r in runs if r["decision"] != "NO TRADE"), None)
    risk_node: dict[str, Any] = {"status": "waiting", "output": "No proposal to check."}
    if prop:
        eq = execution.paper_equity(db, acc) if acc.mode == "PAPER" else None
        if eq is None:
            data, _, _ = terminal.snapshot(acc.mode, acc.master_address) if acc.master_address else (None, None, None)
            eq = float(((data or {}).get("state") or {}).get("marginSummary", {}).get("accountValue") or 0) or None
        if eq:
            open_n = db.scalar(select(func.count()).select_from(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.status == "OPEN")) or 0
            plan = risk.plan_trade(coin=m.coin, side=prop["decision"], mark=m.mark, entry_zone=tuple(prop["entry"]), stop=prop["stop"],  # type: ignore[arg-type]
                                   target=prop["target"], sig_lev_hi=5, market_max_lev=m.max_leverage, sz_decimals=m.sz_decimals, equity=eq,
                                   max_leverage=acc.max_leverage, max_loss_usd=acc.max_loss_per_trade_usd, max_allocation_pct=acc.max_allocation_pct,
                                   max_positions=acc.max_positions, open_positions=int(open_n), daily_pnl=execution.daily_pnl(db, acc),
                                   daily_loss_limit=acc.daily_loss_limit_usd, allowed=[c.strip().upper() for c in acc.allowed_coins.split(",") if c.strip()],
                                   paused=acc.paused, global_paused=s.paused)
            risk_node = {"status": "pass" if plan.ok else "fail", "output": "approved (dry run, nothing sent)" if plan.ok else "; ".join(plan.reasons),
                         "plan": plan.as_dict() if plan.ok else None}
        else:
            risk_node = {"status": "unavailable", "output": "Account equity couldn't be read."}
    my_pos = [p for p in db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.coin == m.coin,
                                                             TrPosition.status.in_(("OPEN", "PROTECTION_FAILED"))))]
    last_order = db.scalar(select(KbEvent).where(KbEvent.member_id == member.id, KbEvent.kind.in_(("order", "exchange")), KbEvent.coin == m.coin)
                           .order_by(KbEvent.id.desc()).limit(1))

    def node(name: str) -> dict[str, Any]:
        cs = [c for r in runs for c in r["checks"] if c["node"] == name]
        oks = [c["ok"] for c in cs if c["ok"] is not None]
        st = "pass" if oks and all(oks) else "fail" if oks else "waiting"
        return {"status": st, "checks": [c | {"strategy": r["strategy"]} for r in runs for c in r["checks"] if c["node"] == name]}

    nodes = {
        "market": {"status": "pass", "output": f"{m.coin} {market.fmt_price(m.mark)} · funding {m.funding * 100:.4f}%/h · OI ${m.oi_usd / 1e6:,.0f}M",
                   "ms": round(t_data, 1), "source": "hyperliquid:info metaAndAssetCtxs + l2Book", **{"checks": node("market")["checks"]}},
        "trend": node("trend"), "momentum": node("momentum"),
        "orderflow": node("orderflow") | {"output": f"spread {book.get('spread_bps', 0):.1f} bps · book imbalance "
                                          + (f"{imb * 100:+.0f}% (bids vs asks within 0.5%)" if imb is not None else "unavailable")},
        "signal": {"status": "pass" if prop else "fail", "output": f"{prop['name']} → {prop['decision']}" if prop else "NO TRADE from both strategies",
                   "checks": node("signal")["checks"]},
        "risk": risk_node,
        "execution": {"status": "waiting" if not last_order else "done", "output": last_order.title if last_order else "No order on this market.",
                      "at": _iso(last_order.at) if last_order else None,
                      "note": "Execution only happens for published signals of switched-on strategies; nothing is sent from this page."},
        "position": {"status": "open" if my_pos else "none", "output": ", ".join(f"{p.side} {p.size:g} @ {p.entry_px:g}" for p in my_pos) or "No position."},
    }
    return {"coin": m.coin, "at": utcnow().isoformat(), "runs": runs, "nodes": nodes, "book": {k: book.get(k) for k in ("bid", "ask", "spread_bps",
            "bid_depth_usd", "ask_depth_usd")} | {"imbalance": imb}, "engine": engine_state(db)["state"]}


# ---------------------------------------------------------------- order-flow recorder
RECORD_WHILE_WATCHED_S = 300
SAMPLE_EVERY_S = 3.0
RECORDER_ENABLED = True  # tests switch the background thread off and call sample() directly


@dataclass
class Flow:
    coin: str
    started: float = field(default_factory=time.time)
    last_watch: float = field(default_factory=time.time)
    books: deque = field(default_factory=lambda: deque(maxlen=600))  # (t, mid, [[px, sz]...bids], [[px, sz]...asks])
    trades: dict = field(default_factory=dict)  # tid -> (t_ms, px, sz, side)  side: B = buyer took the ask, A = seller hit the bid
    ctx: deque = field(default_factory=lambda: deque(maxlen=600))  # (t, mark, funding, oi_usd)


_flows: dict[str, Flow] = {}
_flow_lock = threading.Lock()
_thread: threading.Thread | None = None


def sample(md: market.MarketData, coin: str) -> None:
    f = _flows.get(coin)
    if f is None:
        return
    b = md._post({"type": "l2Book", "coin": coin})
    lv = b.get("levels") or [[], []]
    bids = [[float(x["px"]), float(x["sz"])] for x in lv[0][:20]]
    asks = [[float(x["px"]), float(x["sz"])] for x in lv[1][:20]]
    if bids and asks:
        f.books.append((time.time(), (bids[0][0] + asks[0][0]) / 2, bids, asks))
    for x in md._post({"type": "recentTrades", "coin": coin}) or []:
        f.trades[int(x["tid"])] = (int(x["time"]), float(x["px"]), float(x["sz"]), x["side"])
    if len(f.trades) > 20_000:
        for k in sorted(f.trades, key=lambda k: f.trades[k][0])[:5000]:
            del f.trades[k]
    try:
        mk = md.market(coin)
        f.ctx.append((time.time(), mk.mark, mk.funding, mk.oi_usd))
    except market.MarketError:
        pass


def _loop() -> None:
    global _thread
    while True:
        now = time.time()
        with _flow_lock:
            live = [c for c, f in _flows.items() if now - f.last_watch < RECORD_WHILE_WATCHED_S]
            for c in [c for c, f in _flows.items() if now - f.last_watch > 3600]:
                del _flows[c]  # an hour unwatched: the recording is dropped (bounded memory)
            if not live:
                _thread = None
                return
        for c in live:
            try:
                sample(market.data(), c)
            except Exception:  # noqa: BLE001 - a missed sample stays missing (shown as a gap), never filled in
                pass
        time.sleep(SAMPLE_EVERY_S)


def watch(coin: str) -> Flow:
    global _thread
    with _flow_lock:
        f = _flows.get(coin)
        if f is None:
            f = _flows[coin] = Flow(coin)
        f.last_watch = time.time()
        if RECORDER_ENABLED and _thread is None:
            _thread = threading.Thread(target=_loop, name="kiwi-flow", daemon=True)
            _thread.start()
    return f


def flow(db: Session, md: market.MarketData, coin: str) -> dict[str, Any]:
    m = md.market(coin)
    f = watch(m.coin)
    if not f.books:
        try:
            sample(md, m.coin)
        except Exception:  # noqa: BLE001
            pass
    books = list(f.books)
    step = max(1, math.ceil(len(books) / 200))
    heat = [{"t": b[0], "mid": b[1], "bids": b[2], "asks": b[3]} for b in books[::step]]
    trades = sorted(f.trades.values())
    bars: dict[int, list[float]] = {}
    for t, px, sz, side in trades:
        k = t // 10_000 * 10_000
        bar = bars.setdefault(k, [0.0, 0.0])
        bar[0 if side == "B" else 1] += px * sz
    cvd, series = 0.0, []
    for k in sorted(bars):
        cvd += bars[k][0] - bars[k][1]
        series.append({"t": k, "buy": round(bars[k][0], 2), "sell": round(bars[k][1], 2), "cvd": round(cvd, 2)})
    big = sorted(({"t": t, "px": px, "sz": sz, "usd": round(px * sz, 2), "side": "buy" if side == "B" else "sell"} for t, px, sz, side in trades),
                 key=lambda x: -x["usd"])[:15]
    last = books[-1] if books else None
    bd = sum(px * sz for px, sz in last[2]) if last else 0  # the top 20 levels each side (what Hyperliquid's l2Book returns)
    ad = sum(px * sz for px, sz in last[3]) if last else 0
    profile: dict[float, float] = {}
    if trades:
        lo_, hi_ = min(x[1] for x in trades), max(x[1] for x in trades)
        width = (hi_ - lo_) / 24 or (hi_ * 0.0005)
        for _, px, sz, _ in trades:
            k = round(lo_ + math.floor((px - lo_) / width) * width, 10)
            profile[k] = profile.get(k, 0) + px * sz
    hist = []
    if m.coin in ("BTC", "ETH"):  # the engine's own 15-min observations (7 days kept)
        for e in db.scalars(select(KbEvent).where(KbEvent.kind == "market", KbEvent.coin == m.coin).order_by(KbEvent.id.desc()).limit(700)):
            mk = (e.data or {}).get("market") or {}
            if mk.get("open_interest_usd") is not None:
                hist.append({"t": _aware(e.at).timestamp(), "oi_usd": mk.get("open_interest_usd"), "funding_h": mk.get("funding_h"), "mark": mk.get("mark")})
        hist.reverse()
    return {"coin": m.coin, "recording_since": f.started, "now": time.time(), "samples": len(books), "sample_every_s": SAMPLE_EVERY_S,
            "source": "hyperliquid:info l2Book + recentTrades, sampled while this page is open",
            "book": {"bids": last[2], "asks": last[3], "mid": last[1], "t": last[0]} if last else None,
            "imbalance": round((bd - ad) / (bd + ad), 4) if bd + ad else None, "depth_top20": {"bid_usd": round(bd, 2), "ask_usd": round(ad, 2)},
            "heat": heat, "volume": series, "large_trades": big, "trades_recorded": len(trades),
            "profile": [{"px": k, "usd": round(v, 2)} for k, v in sorted(profile.items())],
            "ctx": [{"t": t, "mark": mk, "funding_h": fu, "oi_usd": oi} for t, mk, fu, oi in f.ctx][-300:], "ctx_history": hist,
            "liquidations": {"available": False, "why": "Hyperliquid's public info API doesn't publish other traders' liquidations. OKX-reported liquidations "
                                                         "are under Market structure."}}


# ---------------------------------------------------------------- performance (the member's own closed positions)
def performance(db: Session, member: Member) -> dict[str, Any]:
    acc = execution.account(db, member)
    rows = db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.status == "CLOSED").order_by(TrPosition.closed_at)).all()
    sigs = {s.id: s for s in db.scalars(select(TsSignal).where(TsSignal.id.in_([p.signal_id for p in rows])))} if rows else {}
    trades = []
    for p in rows:
        sig = sigs.get(p.signal_id)
        opened, closed = _aware(p.created_at), _aware(p.closed_at)
        trades.append({"id": p.id, "coin": p.coin, "side": p.side, "mode": p.mode, "strategy": sig.strategy if sig else None,
                       "signal_number": sig.number if sig else None, "test": bool(sig and sig.strategy == "test"),
                       "entry": p.entry_px, "exit": p.exit_px, "net": round(p.realized_pnl, 6), "fees": round(p.fees, 6),
                       "funding": round(p.funding or 0, 6) if p.mode != "PAPER" else None,
                       "notional": round((p.entry_px or 0) * _entry_size(db, p), 2), "reason": p.close_reason,
                       "opened_at": _iso(opened), "closed_at": _iso(closed),
                       "duration_s": int((closed - opened).total_seconds()) if opened and closed else None,
                       "regime": _regime(sig), "slippage_bps": _slip(sig, p)})
    return {"trades": trades, "label": "Your closed bot positions, after fees (and exchange funding for live). Live and paper are separate.",
            "backtest": BACKTEST, "unrealized": None}


def _regime(sig: TsSignal | None) -> str | None:
    """The trend at the signal, from the indicators logged with it (EMA20 vs EMA50, or price vs EMA50)."""
    a = (sig.analysis or {}) if sig else {}
    e20, e50, px = a.get("ema20"), a.get("ema50"), a.get("price")
    if isinstance(e20, (int, float)) and isinstance(e50, (int, float)):
        return "uptrend" if e20 > e50 else "downtrend"
    if isinstance(px, (int, float)) and isinstance(e50, (int, float)):
        return "uptrend" if px > e50 else "downtrend"
    return None


def _slip(sig: TsSignal | None, p: TrPosition) -> float | None:
    """Entry fill vs the signal's entry-zone middle, in bps; positive = worse for the trade."""
    if sig is None or not p.entry_px:
        return None
    mid = (sig.entry_lo + sig.entry_hi) / 2
    return round((p.entry_px - mid) / mid * 1e4 * (1 if p.side == "LONG" else -1), 1)


def _entry_size(db: Session, p: TrPosition) -> float:
    from app.models.trading import TrOrder

    o = db.scalar(select(TrOrder).where(TrOrder.position_id == p.id, TrOrder.kind == "entry").limit(1))
    return o.filled_size if o else 0.0


# the strategy lab (tradingos/lab.py, run 2026-10-16): hypothetical, replayed with the live trade management
BACKTEST = {"label": "HYPOTHETICAL backtest (strategy lab, 16 Oct 2026): fees, slippage and real funding included; not real results",
            "rows": [
                {"strategy": "breakout_4h_v1", "name": "4h breakout (live rules)", "trades": 288, "expectancy_r": -0.09, "ci90": [-0.23, 0.05],
                 "win_rate": 32.3, "holdout_r": -0.20, "max_dd_pct": 21.2, "period": "Jul 2024 – Oct 2026"},
                {"strategy": "trend_pullback_v1", "name": "Trend pullback 1h (live rules)", "trades": 395, "expectancy_r": -0.16, "ci90": [-0.27, -0.04],
                 "win_rate": 34.2, "holdout_r": -0.16, "max_dd_pct": 39.3, "period": "Mar – Oct 2026"},
                {"strategy": "breakout_4h_v1", "name": "4h breakout + volume 1.5x (variant)", "trades": 263, "expectancy_r": 0.11, "ci90": [-0.04, 0.26],
                 "win_rate": 37.6, "holdout_r": -0.03, "max_dd_pct": 9.1, "period": "Jul 2024 – Oct 2026"},
                {"strategy": "meanrev", "name": "Mean reversion 4h (candidate)", "trades": 36, "expectancy_r": -0.43, "ci90": [-0.68, -0.17],
                 "win_rate": 19.4, "holdout_r": -0.46, "max_dd_pct": 8.2, "period": "Jul 2024 – Oct 2026"}]}


# ---------------------------------------------------------------- Time Machine
def replay(db: Session, member: Member, md: market.MarketData, signal_id: int | None = None, position_id: int | None = None) -> dict[str, Any]:
    acc = execution.account(db, member)
    pos = None
    if position_id:
        pos = db.get(TrPosition, position_id)
        if pos is None or pos.account_id != acc.id:
            raise LookupError("No such position of yours.")
        signal_id = pos.signal_id
    sig = db.get(TsSignal, signal_id) if signal_id else None
    if sig is None or sig.published_at is None:
        raise LookupError("No such signal.")
    if pos is None:
        pos = db.scalar(select(TrPosition).where(TrPosition.account_id == acc.id, TrPosition.signal_id == sig.id).order_by(TrPosition.id.desc()).limit(1))
    start = _aware(sig.created_at)
    end = _aware((pos.closed_at if pos and pos.closed_at else None) or sig.closed_at) or utcnow()
    span = (end - start).total_seconds()
    iv = "1m" if span < 5 * 3600 else "5m" if span < 24 * 3600 else "15m" if span < 3 * 86400 else "1h"
    sec = market.INTERVALS[iv]
    a_ms = int((start.timestamp() - 80 * sec) * 1000)
    b_ms = int(min(time.time(), end.timestamp() + 20 * sec) * 1000)
    rows = md._post({"type": "candleSnapshot", "req": {"coin": sig.coin, "interval": iv, "startTime": a_ms, "endTime": b_ms}}) or []
    candles = [[int(r["t"]), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"])] for r in rows]
    evs = sorted(brain.events_for(db, member.id, limit=500, signal_id=sig.id), key=lambda e: e.id)
    report: dict[str, Any] = {"strategy": sig.strategy, "strategy_name": STRATEGIES.get(sig.strategy, sig.strategy), "version": sig.strategy,
                              "signal_reason": sig.reason, "signal_result_pct": sig.model_pnl_pct, "signal_status": sig.status}
    if pos and pos.entry_px:
        o_ms = _aware(pos.created_at).timestamp() * 1000
        c_ms = (_aware(pos.closed_at).timestamp() * 1000) if pos.closed_at else time.time() * 1000
        inside = [c for c in candles if c[0] + sec * 1000 > o_ms and c[0] <= c_ms]
        long = pos.side == "LONG"
        if inside:
            worst = min(c[3] for c in inside) if long else max(c[2] for c in inside)
            best = max(c[2] for c in inside) if long else min(c[3] for c in inside)
            sg = 1 if long else -1
            size = _entry_size(db, pos)
            report |= {"mae_pct": round((worst - pos.entry_px) / pos.entry_px * 100 * sg, 3), "mfe_pct": round((best - pos.entry_px) / pos.entry_px * 100 * sg, 3),
                       "mae_usd": round((worst - pos.entry_px) * size * sg, 4), "mfe_usd": round((best - pos.entry_px) * size * sg, 4),
                       "mae_note": f"from {iv} candle highs/lows between entry and exit"}
        mid = (sig.entry_lo + sig.entry_hi) / 2
        report |= {"position_id": pos.id, "mode": pos.mode, "entry": pos.entry_px, "exit": pos.exit_px, "net": pos.realized_pnl, "fees": pos.fees,
                   "funding": pos.funding if pos.mode != "PAPER" else None, "close_reason": pos.close_reason, "status": pos.status,
                   "entry_vs_zone_bps": round((pos.entry_px - mid) / mid * 1e4 * (1 if long else -1), 1),
                   "stop": pos.stop, "tp1": pos.tp1, "tp2": pos.tp2, "opened_at": _iso(pos.created_at), "closed_at": _iso(pos.closed_at)}
    gap = None
    first_ev = db.scalar(select(func.min(KbEvent.at)))
    if first_ev and _aware(first_ev) > start:
        gap = ("This signal predates the Kiwi Brain log (started 16 Oct 2026): its strategy evaluation snapshot wasn't recorded, so only the "
               "events that exist are shown. Indicators on the chart are recomputed from the candles available at each step.")
    return {"signal": {"id": sig.id, "number": sig.number, "coin": sig.coin, "side": sig.side, "entry": [sig.entry_lo, sig.entry_hi], "stop": sig.stop,
                       "target": sig.target, "created_at": _iso(sig.created_at), "activated_at": _iso(sig.activated_at), "closed_at": _iso(sig.closed_at),
                       "status": sig.status, "timeframe": sig.timeframe, "test": sig.strategy == "test"},
            "interval": iv, "candles": candles, "source": f"hyperliquid:info candleSnapshot {iv}", "events": [brain.event_out(e) for e in evs],
            "report": report, "gap": gap}


def replayable(db: Session, member: Member) -> list[dict[str, Any]]:
    acc = execution.account(db, member)
    out = []
    pos = {p.signal_id: p for p in db.scalars(select(TrPosition).where(TrPosition.account_id == acc.id).order_by(TrPosition.id))}
    for s in db.scalars(select(TsSignal).where(TsSignal.published_at.is_not(None)).order_by(TsSignal.id.desc()).limit(40)):
        p = pos.get(s.id)
        out.append({"signal_id": s.id, "number": s.number, "coin": s.coin, "side": s.side, "status": s.status, "test": s.strategy == "test",
                    "created_at": _iso(s.created_at), "position": {"id": p.id, "mode": p.mode, "status": p.status, "net": p.realized_pnl} if p else None})
    return out


# ---------------------------------------------------------------- Teach Your Kiwi: versioned per-member config
DEFAULT_CONFIG: dict[str, Any] = {"strategies": {"trend_pullback_v1": True, "breakout_4h_v1": True, "test": True}, "sides": "both", "coins": [],
                                  "timeframes": ["1h", "4h"], "min_rr": None, "funding": "any", "max_volatility_pct": None,
                                  "schedule": None}
SIDES, FUNDING, TFS = ("both", "long", "short"), ("any", "negative", "positive"), ("1h", "4h")


class ConfigError(ValueError):
    pass


def validate_config(c: dict[str, Any]) -> dict[str, Any]:
    """Only known fields with checked values; anything else is refused (never stored, never executed)."""
    if not isinstance(c, dict):
        raise ConfigError("The configuration must be an object.")
    unknown = set(c) - set(DEFAULT_CONFIG)
    if unknown:
        raise ConfigError(f"Unsupported setting(s): {', '.join(sorted(unknown))}.")
    out = {**DEFAULT_CONFIG, **c}
    st = out["strategies"]
    if not isinstance(st, dict) or set(st) - set(DEFAULT_CONFIG["strategies"]) or not all(isinstance(v, bool) for v in st.values()):
        raise ConfigError("strategies: on/off per known strategy only.")
    out["strategies"] = {**DEFAULT_CONFIG["strategies"], **st}
    if out["sides"] not in SIDES:
        raise ConfigError("sides: both, long or short.")
    coins = out["coins"]
    if not isinstance(coins, list) or len(coins) > 30 or not all(isinstance(x, str) and x.isalnum() and len(x) <= 12 for x in coins):
        raise ConfigError("coins: a list of up to 30 tickers.")
    out["coins"] = sorted({x.upper() for x in coins})
    if not isinstance(out["timeframes"], list) or not out["timeframes"] or set(out["timeframes"]) - set(TFS):
        raise ConfigError("timeframes: 1h and/or 4h.")
    if out["min_rr"] is not None and not (isinstance(out["min_rr"], (int, float)) and 1.0 <= out["min_rr"] <= 5.0):
        raise ConfigError("min_rr: between 1 and 5 (or none).")
    if out["funding"] not in FUNDING:
        raise ConfigError("funding: any, negative or positive.")
    if out["max_volatility_pct"] is not None and not (isinstance(out["max_volatility_pct"], (int, float)) and 0.1 <= out["max_volatility_pct"] <= 20):
        raise ConfigError("max_volatility_pct: between 0.1 and 20 (or none).")
    sc = out["schedule"]
    if sc is not None:
        if not isinstance(sc, dict) or set(sc) - {"days", "from_h", "to_h"}:
            raise ConfigError("schedule: days (0=Mon..6=Sun), from_h and to_h (UTC hours).")
        days = sc.get("days", list(range(7)))
        fh, th = sc.get("from_h", 0), sc.get("to_h", 24)
        if not (isinstance(days, list) and days and all(isinstance(d, int) and 0 <= d <= 6 for d in days)
                and isinstance(fh, int) and isinstance(th, int) and 0 <= fh < th <= 24):
            raise ConfigError("schedule: days 0–6 and hours 0–24 (from before to).")
        out["schedule"] = {"days": sorted(set(days)), "from_h": fh, "to_h": th}
    return out


def current_config(db: Session, member: Member) -> dict[str, Any]:
    row = db.scalar(select(KbConfig).where(KbConfig.member_id == member.id).order_by(KbConfig.version.desc()).limit(1))
    return {"version": row.version if row else 0, "config": validate_config(row.config) if row else dict(DEFAULT_CONFIG),
            "note": row.note if row else "Default: every published signal, within your risk limits.", "at": _iso(row.created_at) if row else None}


def config_versions(db: Session, member: Member) -> list[dict[str, Any]]:
    return [{"version": r.version, "config": r.config, "note": r.note, "at": _iso(r.created_at)}
            for r in db.scalars(select(KbConfig).where(KbConfig.member_id == member.id).order_by(KbConfig.version.desc()).limit(50))]


def save_config(db: Session, member: Member, config: dict[str, Any], note: str | None) -> dict[str, Any]:
    clean = validate_config(config)
    v = int(db.scalar(select(func.coalesce(func.max(KbConfig.version), 0)).where(KbConfig.member_id == member.id)) or 0) + 1
    db.add(KbConfig(member_id=member.id, version=v, config=clean, note=(note or "")[:500] or None))
    brain.log(db, "system", f"Kiwi config v{v} saved", member_id=member.id, source="teach-your-kiwi", data={"version": v, "config": clean, "note": note})
    db.commit()
    return current_config(db, member)


def rollback_config(db: Session, member: Member, version: int) -> dict[str, Any]:
    row = db.scalar(select(KbConfig).where(KbConfig.member_id == member.id, KbConfig.version == version))
    if row is None:
        raise ConfigError("No such version.")
    return save_config(db, member, row.config, f"Restored version {version}")


def config_allows(cfg: dict[str, Any], sig: TsSignal, now: datetime | None = None) -> list[str]:
    """Why this member's Kiwi config would skip the signal (empty = allowed). Filters only: they can remove trades, never add."""
    why = []
    a = sig.analysis or {}
    if not cfg["strategies"].get("test" if sig.strategy == "manual" else sig.strategy, False):  # one "Manual setup" switch for both
        why.append(f"strategy {STRATEGIES.get(sig.strategy, sig.strategy)} is off in your config")
    if cfg["sides"] != "both" and sig.side.lower() != cfg["sides"]:
        why.append(f"your config trades {cfg['sides']}s only")
    if cfg["coins"] and sig.coin.upper() not in cfg["coins"]:
        why.append(f"{sig.coin} isn't in your config's coins")
    if sig.timeframe not in cfg["timeframes"]:
        why.append(f"the {sig.timeframe} timeframe is off in your config")
    if cfg["min_rr"] and (sig.rr or 0) < cfg["min_rr"]:
        why.append(f"R/R {sig.rr} below your minimum {cfg['min_rr']}")
    fr = a.get("funding_h")
    if cfg["funding"] == "negative" and not (fr is not None and fr < 0):
        why.append("your config needs negative funding")
    if cfg["funding"] == "positive" and not (fr is not None and fr > 0):
        why.append("your config needs positive funding")
    vol = a.get("atr_pct")
    if cfg["max_volatility_pct"] and (vol is None or vol > cfg["max_volatility_pct"]):
        why.append(f"volatility {vol if vol is not None else '?'}% above your cap {cfg['max_volatility_pct']}%")
    sc = cfg.get("schedule")
    if sc:
        n = _aware(now or utcnow())
        if n.weekday() not in sc["days"] or not sc["from_h"] <= n.hour < sc["to_h"]:
            why.append("outside your trading schedule (UTC)")
    return why


def config_impact(db: Session, old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """What a change would have done: of the last 50 published signals, how many each version would have let through."""
    rows = db.scalars(select(TsSignal).where(TsSignal.published_at.is_not(None)).order_by(TsSignal.id.desc()).limit(50)).all()
    o = [s for s in rows if not config_allows(old, s, _aware(s.created_at))]
    n = [s for s in rows if not config_allows(new, s, _aware(s.created_at))]
    return {"signals": len(rows), "allowed_before": len(o), "allowed_after": len(n),
            "removed": [f"#{s.number:03d} {s.coin} {s.side}" for s in o if s not in n][:20],
            "added": [f"#{s.number:03d} {s.coin} {s.side}" for s in n if s not in o][:20]}


# ---------------------------------------------------------------- Ask Kiwi: the evidence (the answer must cite these ids)
def evidence(db: Session, member: Member, md: market.MarketData) -> tuple[str, set[str]]:
    lines, ids = [], set()

    def add(i: str, text: str) -> None:
        ids.add(i)
        lines.append(f"[{i}] {text}")

    ov = overview(db, member, md)
    h = ov["health"]
    add("S", f"Account mode {ov['mode']}, AUTO {'on' if ov['auto'] else 'off'}, new trades {'STOPPED' if ov['paused'] else 'allowed'}; "
             f"engine {h['engine']['state']}: {h['engine']['reason']} Exchange {h['exchange']['state']}; market feed {h['market_feed']['state']}; "
             f"position monitoring {h['monitoring']['state']}; protection {h['protection']['state']}.")
    ex = ov["exposure"]
    add("X", f"Exposure: notional ${ex['notional']}, loss if every stop hits ${ex['loss_at_stop']} "
             f"({ex['loss_at_stop_pct']}% of equity ${ex['equity']}); unrealized ${ex['unrealized']}; realized today ${ex['realized_today']}.")
    lim = ov["limits"]
    add("L", f"Risk limits: max leverage {lim['max_leverage']}x, max loss per trade ${lim['max_loss_per_trade_usd']}, max positions "
             f"{lim['max_positions']}, daily loss limit ${lim['daily_loss_limit_usd']}, allowed coins '{lim['allowed_coins'] or 'any'}'.")
    add("C", f"Kiwi config: {ov['config']}")
    for p in ov["positions"]:
        add(f"P{p['id']}", f"OPEN {p['mode']} {p['coin']} {p['side']} size {p['size']} entry {p['entry_px']} mark {p['mark']} uPnL {p['upnl']} "
                           f"stop {p['stop']} ({p['protection']['label']}) TP1 {p['tp1']} TP2 {p['tp2']} liq {p['liq_px']} fees {p['fees']} "
                           f"funding {p['funding']} strategy {p['strategy_name']} signal #{p['signal_number']}.")
    perf = performance(db, member)
    for tr in perf["trades"][-25:]:
        add(f"T{tr['id']}", f"CLOSED {tr['mode']} {tr['coin']} {tr['side']} {tr['strategy']}{' (manual/test)' if tr['test'] else ''} entry {tr['entry']} "
                            f"exit {tr['exit']} net ${tr['net']} fees ${tr['fees']} funding {tr['funding']} reason {tr['reason']} closed {tr['closed_at']}.")
    fund = sum(t["funding"] or 0 for t in perf["trades"])
    add("F", f"Total funding on your closed live positions: ${fund:.4f} (positive = paid). Paper positions have no funding.")
    evs = list(db.scalars(select(KbEvent).where(((KbEvent.member_id == member.id) | (KbEvent.member_id.is_(None))), KbEvent.kind != "market")
                          .order_by(KbEvent.id.desc()).limit(60)))
    for e in evs:
        d = e.data or {}
        extra = ""
        if d.get("checks"):
            extra = " · rules: " + "; ".join(f"{c['rule']} = {c['value']} {'PASS' if c['ok'] else 'FAIL' if c['ok'] is False else ''}" for c in d["checks"])[:600]
        add(f"E{e.id}", f"{_iso(e.at)} {e.kind}: {e.title}{extra}")
    for coin in sorted({"BTC", "ETH", *(p["coin"] for p in ov["positions"])}):
        try:
            sn = brain.market_snapshot(md, coin, "1h")
            i = sn["indicators"]
            add(f"M{coin}", f"{coin} now {sn['market']['mark']}, funding {sn['market']['funding_h']}/h, OI ${sn['market']['open_interest_usd']:,.0f}, "
                            f"1h RSI {i['rsi14']}, MACD hist {i['macd_hist']}, EMA20 {i['ema20']}, EMA50 {i['ema50']}, trend {i['trend']}, "
                            f"ATR {i['atr14']}{' (STALE)' if sn['stale'] else ''}.")
        except market.MarketError:
            add(f"M{coin}", f"{coin} market data unavailable right now.")
    for r in BACKTEST["rows"]:
        add(f"B{r['strategy'][:6]}{r['trades']}", f"HYPOTHETICAL backtest {r['name']}: {r['trades']} trades, {r['expectancy_r']}R per trade "
                                                 f"(90% CI {r['ci90']}), holdout {r['holdout_r']}R.")
    return "\n".join(lines), ids


def direct_answer(db: Session, member: Member, md: market.MarketData, question: str) -> dict[str, Any] | None:
    """Ask Kiwi without a language model: the common questions answered straight from the records (same evidence ids). None
    when the question isn't one of these (then only the AI model can answer it)."""
    import re

    q = question.lower()
    try:
        listed = set(md.markets()[1])
    except market.MarketError:
        listed = set()
    skip = {"WHY", "NOT", "YOU", "THE", "GO", "LONG", "SHORT", "WHAT", "MAKE", "DID", "DIDN", "WOULD", "ME", "MY"}
    coins = [w.upper() for w in re.findall(r"[a-z0-9]{2,10}", q) if w.upper() in listed and w.upper() not in skip]
    if re.search(r"why (didn.?t|did not|no|not)|what would make|would you (go )?(long|short)|go (long|short)", q) and coins:
        coin = coins[0]
        ev = evaluate(db, member, md, coin)
        lines, cited = [], []
        for r in ev["runs"]:
            fails = [c for c in r["checks"] if c["ok"] is False]
            oks = [c for c in r["checks"] if c["ok"] is True]
            if r["decision"] == "NO TRADE":
                lines.append(f"{r['name']}: NO TRADE right now. Failing: " + "; ".join(f"{c['rule']} ({c['value']})" for c in fails)
                             + (f". Passing: {len(oks)} rule(s)." if oks else "."))
            else:
                lines.append(f"{r['name']}: the rules say {r['decision']} (entry {r['entry']}, stop {r['stop']}, R/R {r['rr']})"
                             + (" but this strategy is switched off, so it isn't traded." if not r["enabled"] else "."))
        if engine_state(db)["state"] == "shadow":
            lines.append("Both strategies are switched off for trading (they lost money out of sample), so even a passing setup isn't traded.")
            cited.append("S")
        return {"answer": f"{coin}, evaluated just now on live Hyperliquid data:\n" + "\n".join(lines)
                + "\nFor a trade, every failing rule above has to flip to passing.", "cited": cited + [f"M{coin}"], "sufficient": True, "proposal": None}
    ov = overview(db, member, md)
    if re.search(r"risk|exposure|how much (can|could) i lose", q):
        ex = ov["exposure"]
        ps = "; ".join(f"{p['coin']} {p['side']}: ${p['loss_at_stop']} to the stop, {p['protection']['label']}" for p in ov["positions"]) or "no open positions"
        return {"answer": f"If every stop is hit you lose about ${ex['loss_at_stop']} ({ex['loss_at_stop_pct']}% of ${ex['equity']} equity). "
                          f"Open notional ${ex['notional']}. Positions: {ps}. Limits: max {ov['limits']['max_leverage']}x, ${ov['limits']['max_loss_per_trade_usd']} "
                          f"max loss per trade, {ov['limits']['max_positions']} positions, ${ov['limits']['daily_loss_limit_usd']} daily loss limit.",
                "cited": ["X", "L", *(f"P{p['id']}" for p in ov["positions"])], "sufficient": True, "proposal": None}
    perf = performance(db, member)
    if "funding" in q:
        live = [t for t in perf["trades"] if t["mode"] == "LIVE"]
        paid = sum(t["funding"] or 0 for t in live)
        open_f = sum(p["funding"] or 0 for p in ov["positions"] if p["mode"] != "PAPER")
        return {"answer": f"Closed live positions: ${paid:.4f} funding ({'paid' if paid > 0 else 'received' if paid < 0 else 'none'}) over {len(live)} trade(s). "
                          f"Open live positions so far: ${open_f:.4f}. Paper positions have no funding.", "cited": ["F", *(f"P{p['id']}" for p in ov["positions"])],
                "sufficient": True, "proposal": None}
    if re.search(r"reject|skip|refus", q):
        rows = list(db.scalars(select(KbEvent).where(((KbEvent.member_id == member.id) | KbEvent.member_id.is_(None)), KbEvent.kind.in_(("risk", "strategy")))
                               .order_by(KbEvent.id.desc()).limit(200)))
        rej = [e for e in rows if (e.data or {}).get("decision") in ("rejected", "skipped", "NO TRADE")][:5]
        if not rej:
            return {"answer": "No rejected signals are recorded.", "cited": [], "sufficient": False, "proposal": None}
        return {"answer": "The last rejections:\n" + "\n".join(f"• {(_iso(e.at) or '')[:16].replace('T', ' ')} {e.title}" for e in rej),
                "cited": [f"E{e.id}" for e in rej], "sufficient": True, "proposal": None}
    if re.search(r"best|perform|win rate|p&l|pnl|profit", q):
        by: dict[str, list[float]] = {}
        for t in perf["trades"]:
            by.setdefault(f"{t['mode']} {STRATEGIES.get(t['strategy'] or '', t['strategy'])}", []).append(t["net"])
        if not by:
            return {"answer": "You have no closed bot trades yet, so there's nothing to rank. The (hypothetical) backtests put both live strategies below zero.",
                    "cited": [f"B{r['strategy'][:6]}{r['trades']}" for r in BACKTEST["rows"][:2]], "sufficient": True, "proposal": None}
        parts = [f"{k}: {len(v)} trade(s), net ${sum(v):.2f}" + (" (too few to judge)" if len(v) < 5 else "") for k, v in sorted(by.items(), key=lambda kv: -sum(kv[1]))]
        return {"answer": "Your closed trades by strategy (live and paper kept apart):\n" + "\n".join(parts), "cited": [f"T{t['id']}" for t in perf["trades"][-25:]],
                "sufficient": True, "proposal": None}
    if re.search(r"why .*clos|closed", q) and perf["trades"]:
        t = perf["trades"][-1]
        return {"answer": f"Your last closed trade: {t['mode']} {t['coin']} {t['side']} closed by {t['reason'] or 'unknown'} at {t['exit']} "
                          f"(entry {t['entry']}), net ${t['net']:.4f} after ${t['fees']:.4f} fees.", "cited": [f"T{t['id']}"], "sufficient": True, "proposal": None}
    return None


_asks: dict[int, deque] = {}


def ask_allowed(member_id: int, per_hour: int = 30) -> bool:
    q = _asks.setdefault(member_id, deque())
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= per_hour:
        return False
    q.append(now)
    return True


def stats_ci(values: list[float]) -> dict[str, Any]:
    """Mean with a rough 90% interval (normal approximation); flagged when the sample is too small to mean anything."""
    n = len(values)
    if n == 0:
        return {"n": 0}
    mean = statistics.fmean(values)
    if n < 5:
        return {"n": n, "mean": mean, "ci90": None, "note": "too few trades to judge"}
    sd = statistics.stdev(values)
    return {"n": n, "mean": mean, "ci90": [mean - 1.645 * sd / n ** 0.5, mean + 1.645 * sd / n ** 0.5]}


def utc_hours_ago(h: float) -> datetime:
    return utcnow() - timedelta(hours=h)
