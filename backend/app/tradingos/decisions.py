"""Structured trade decisions and shadow trading (Kiwi Trading Engine v2, stage 1).

record()   every kitchen candidate (a 15m entry model fired on a closed candle) becomes one ts_decisions row: the full plan
           (entry reference, stop, targets, max hold), estimated fees / slippage / costs in R, the regime with a confidence,
           the score, every check with its measured value, and the decision: READY (TAKE), REJECTED (REJECT, with the first
           failing check) or WAITING_CONFIRMATION (WAIT, a check is still pending). The same candle updates the same row.
resolve()  the tradingos.shadow job: once the plan's max hold has passed, replays it on the real Hyperliquid 15m candles that
           followed with backtest.simulate (entry at the next open plus slippage, stop first inside a candle, TP1 half /
           TP2 rest, time exit, taker fees, real hourly funding), plus MAE / MFE. Rejected and shadow candidates get the
           same treatment as the taken ones, so the filters can be judged: a filter that rejects winners shows up here.
report()   approved vs rejected, per rejecting check, per regime, per strategy version, and score calibration.

Everything here is HYPOTHETICAL (what the plan would have done), never a member's real result, and is labelled so. Nothing
here changes a strategy, a filter or a risk limit: it measures them. Real results stay in tr_positions / ts_signals.
"""

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsDecision
from app.tradingos import backtest as bt
from app.tradingos import intraday as ix
from app.tradingos import risk
from app.tradingos.market import INTERVALS, MarketData, MarketError

M15 = INTERVALS["15m"] * 1000
M30 = INTERVALS["30m"] * 1000
KEEP_DAYS = 180
MIN_SAMPLE = 30  # fewer resolved decisions than this in a group: reported, but no conclusion drawn
STATE_OF = {"READY": ("READY", "TAKE"), "BURNT": ("REJECTED", "REJECT"), "COOKING": ("WAITING_CONFIRMATION", "WAIT")}
REGIME_NAME = {"STRONG_BULL": "bullish trend", "STRONG_BEAR": "bearish trend", "RANGE": "sideways / range",
               "VOL_EXPANSION": "high volatility", "VOL_CONTRACTION": "low volatility", "UNSTABLE": "market stress / unusual",
               "WEAK_TREND": "unclear (weak trend)"}
SCORE_BUCKETS = ((0, 45), (45, 55), (55, 65), (65, 101))
ML_BUCKETS = ((0.0, 0.45), (0.45, 0.5), (0.5, 0.55), (0.55, 1.01))


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def regime_confidence(regime: str, info: dict[str, Any]) -> float | None:
    """How clearly the measured features sit inside the regime's own thresholds (intraday.regime), 0-1. A label right at a
    threshold scores near 0, one far beyond it near 1. Deterministic, from the logged features only; not a probability."""
    if not info or "adx" not in info:
        return None
    adx, slope, rank = float(info["adx"]), float(info["slope50_atr"]), float(info["atr_pct_rank"])
    if regime in ("STRONG_BULL", "STRONG_BEAR"):
        parts = [_clamp((abs(slope) - 0.3) / 0.5), _clamp((adx - 22) / 10)]
    elif regime == "RANGE":
        parts = [_clamp((18 - adx) / 8), _clamp((0.15 - abs(slope)) / 0.15)]
    elif regime == "VOL_EXPANSION":
        parts = [_clamp((rank - 0.8) / 0.15)]
    elif regime == "VOL_CONTRACTION":
        parts = [_clamp((0.2 - rank) / 0.2)]
    elif regime == "UNSTABLE":
        parts = [1.0 if rank > 0.95 else 0.7]  # a shock candle or extreme volatility: unusual by definition
    else:  # WEAK_TREND: whatever didn't fit a clearer regime; the closer to one, the less it is "weak trend"
        parts = [_clamp(1 - max(abs(slope) / 0.3, adx / 22, abs(rank - 0.5) / 0.3) / 1.5)]
    return round(sum(parts) / len(parts), 2)


# ---------------------------------------------------------------- write side (called by the kitchen)
def record(db: Session, ev: Any, cfg: dict[str, Any], strategy: str, signal_id: int | None, shadow: bool) -> TsDecision | None:
    """Store / update the structured decision for this evaluation. Only candidates (a setup with a complete plan) count."""
    st = ev.setup
    if not st or ev.candle_t is None or ev.state not in STATE_OF:
        return None
    state, decision = STATE_OF[ev.state]
    row = db.scalar(select(TsDecision).where(TsDecision.strategy == strategy, TsDecision.coin == ev.coin, TsDecision.candle_t == ev.candle_t))
    if row is None:
        row = TsDecision(strategy=strategy, coin=ev.coin, candle_t=ev.candle_t, timeframe="15m", outcome="PENDING")
        db.add(row)
    elif row.state == "READY" and state != "READY":
        return row  # a decision that was taken stays taken (later re-checks of the same candle don't rewrite history)
    ref, stop = float(st["ref"]), float(st["stop"])
    risk_px = abs(ref - stop) or 1e-12
    sl = bt.slip(ev.coin)
    fail = next((c for c in ev.checks if c["status"] == "fail"), None)
    info = (ev.market or {}).get("regime_info") or {}
    row.version = str(cfg.get("mode", "standard"))
    row.model, row.direction = st["model"], st["side"]
    row.regime, row.regime_confidence = ev.regime, regime_confidence(ev.regime or "", info)
    row.state, row.decision = state, decision
    row.rejected_by, row.reason = (fail["id"] if fail else None), ev.reason[:2000]
    row.shadow, row.signal_id = bool(shadow or signal_id is None), signal_id
    row.entry_reasons = list(st.get("reasons") or [])
    row.ref, row.stop, row.tp1, row.tp2 = ref, stop, float(st["tp1"]), float(st["tp2"])
    row.r1, row.r2, row.max_hold_min = float(st["r1"]), float(st["r2"]), int(st["max_hold_min"])
    row.est_cost_r = st.get("cost_r")
    row.est_fees_r = round(2 * risk.TAKER_FEE * ref / risk_px, 3)
    row.est_slippage_r = round(2 * sl * ref / risk_px, 3)
    row.rr_net = round(row.r1 - (row.est_cost_r or 0), 3)
    row.score, row.ml_p = ev.strength, st.get("ml_p")
    row.checks = [{"id": c["id"], "label": c["label"], "status": c["status"], "detail": c["detail"]} for c in ev.checks]
    row.market = {k: v for k, v in (ev.market or {}).items() if k != "regime_info"} | {"regime_info": info}
    db.flush()
    return row


def as_record(d: TsDecision) -> dict[str, Any]:
    """The decision in the engine's structured format (see the v2 spec); values are the logged ones, never live ones."""
    at = datetime.fromtimestamp((d.candle_t + M15) / 1000, tz=timezone.utc).isoformat()
    return {"id": d.id, "symbol": d.coin, "timeframe": d.timeframe, "strategy": d.strategy, "strategy_version": d.version,
            "model": d.model, "direction": d.direction, "market_regime": d.regime,
            "market_regime_label": REGIME_NAME.get(d.regime or "", d.regime), "regime_confidence": d.regime_confidence,
            "signal_status": d.state, "decision": d.decision, "rejected_by": d.rejected_by, "reason": d.reason,
            "entry_reason": d.entry_reasons, "entry_price": d.ref, "stop_loss": d.stop, "take_profit": [d.tp1, d.tp2],
            "estimated_fees_r": d.est_fees_r, "estimated_slippage_r": d.est_slippage_r, "estimated_cost_r": d.est_cost_r,
            "risk_reward_ratio": d.rr_net, "score": d.score, "ml_p": d.ml_p, "risk_checks": d.checks, "market": d.market,
            "shadow": d.shadow, "signal_id": d.signal_id, "timestamp": at,
            "outcome": {"status": d.outcome, "hypothetical": True, "r_net": d.r_net, "r_gross": d.r_gross, "cost_r": d.cost_r,
                        "mae_r": d.mae_r, "mfe_r": d.mfe_r, "exit": d.exit_kind, "hold_min": d.hold_min,
                        "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None}}


def recent(db: Session, limit: int = 50, coin: str | None = None, state: str | None = None) -> list[dict[str, Any]]:
    q = select(TsDecision).order_by(TsDecision.id.desc()).limit(max(1, min(limit, 200)))
    if coin:
        q = q.where(TsDecision.coin == coin.upper())
    if state:
        q = q.where(TsDecision.state == state.upper())
    return [as_record(d) for d in db.scalars(q)]


# ---------------------------------------------------------------- shadow outcomes
def _entry_index(f: ix.Series, d: TsDecision) -> int:
    """Where the plan would have entered: after the 30m confirmation when the strategy waited for one (as the backtest does),
    otherwise right after the setup candle."""
    close_t = d.candle_t + M15
    waited = any(c["id"] == "confirm_30m" and c["status"] in ("pass", "fail", "pending") for c in (d.checks or []))
    ready_t = max(close_t, d.candle_t - d.candle_t % M30 + M30) if waited else close_t
    return ix.last_closed(f, ready_t)


def excursions(f: ix.Series, side: str, entry: float, stop: float, t_entry: int, t_exit: int) -> tuple[float, float]:
    """MAE (<= 0) and MFE (>= 0) in R over the candles the trade was open."""
    R = abs(entry - stop) or 1e-12
    long = side == "LONG"
    worst = best = 0.0
    for b in range(len(f.t)):
        if f.t[b] < t_entry or f.t[b] >= t_exit:
            continue
        adverse = (f.l[b] - entry) / R if long else (entry - f.h[b]) / R
        favorable = (f.h[b] - entry) / R if long else (entry - f.l[b]) / R
        worst, best = min(worst, adverse), max(best, favorable)
    return round(max(worst, -1.5), 3), round(best, 3)  # a gap can overshoot the stop; MAE is capped at -1.5R for readability


def resolve_one(d: TsDecision, f: ix.Series, fund: list[tuple[int, float]], now: datetime) -> str:
    i = _entry_index(f, d)
    if i < 0 or f.t[i] < d.candle_t:
        d.outcome = "NO_DATA"
    else:
        r = bt.simulate(f, i, d.direction, d.stop, d.r1, d.r2, d.max_hold_min, d.coin, fund)
        if r is None:
            # simulate returns None for an entry beyond the stop, or when the candles end before the trade did
            nxt = i + 1 < len(f.t)
            beyond = nxt and ((d.direction == "LONG" and f.o[i + 1] <= d.stop) or (d.direction == "SHORT" and f.o[i + 1] >= d.stop))
            if not beyond and now.timestamp() * 1000 - d.candle_t < 3 * 86_400_000:
                return "PENDING"
            d.outcome = "NO_FILL" if beyond else "NO_DATA"
        else:
            d.outcome = "DONE"
            d.r_net, d.r_gross, d.cost_r = round(r["r_net"], 4), round(r["r_gross"], 4), round(r["cost_r"], 4)
            d.exit_kind, d.hold_min = r["exit"], round(r["hold_min"], 1)
            d.mae_r, d.mfe_r = excursions(f, d.direction, r["entry"], d.stop, r["t_entry"], r["t_exit"])
    d.resolved_at = now
    return d.outcome


def resolve(db: Session, md: MarketData, now: datetime | None = None, limit: int = 200) -> dict[str, Any]:
    """The tradingos.shadow job: resolve every decision whose max hold is over, from the candles that followed it."""
    now = now or utcnow()
    now_ms = int(now.timestamp() * 1000)
    out: dict[str, Any] = {"resolved": 0, "outcomes": defaultdict(int)}
    pending = list(db.scalars(select(TsDecision).where(TsDecision.outcome == "PENDING", TsDecision.state != "WAITING_CONFIRMATION")
                              .order_by(TsDecision.candle_t).limit(limit)))
    # a candidate still WAITING when its candle is over a day old never got its final check (e.g. a restart): judge it anyway
    stale = list(db.scalars(select(TsDecision).where(TsDecision.outcome == "PENDING", TsDecision.state == "WAITING_CONFIRMATION",
                                                     TsDecision.candle_t < now_ms - 86_400_000).limit(limit)))
    by_coin: dict[str, list[TsDecision]] = defaultdict(list)
    for d in pending + stale:
        if d.candle_t + M15 + M30 + d.max_hold_min * 60_000 + M15 <= now_ms:
            by_coin[d.coin].append(d)
    for coin, ds in by_coin.items():
        oldest = min(d.candle_t for d in ds)
        n = min(5000, int((now_ms - oldest) / M15) + 8)
        try:
            candles = [c for c in md.candles(coin, "15m", n) if c.t + M15 <= now_ms]  # closed candles only
            fund = md.funding_history(coin, min(720, int((now_ms - oldest) / 3_600_000) + 3))
        except MarketError as e:
            out.setdefault("errors", {})[coin] = str(e)[:200]
            continue
        f = ix.series(candles, M15)
        for d in ds:
            res = resolve_one(d, f, fund, now)
            if res != "PENDING":
                out["resolved"] += 1
                out["outcomes"][res] += 1
    cut = int((now - timedelta(days=KEEP_DAYS)).timestamp() * 1000)
    out["pruned"] = db.execute(delete(TsDecision).where(TsDecision.candle_t < cut)).rowcount or 0
    db.commit()
    out["outcomes"] = dict(out["outcomes"])
    return out


# ---------------------------------------------------------------- report
def metrics(rs: list[float]) -> dict[str, Any]:
    n = len(rs)
    if not n:
        return {"n": 0}
    mean = sum(rs) / n
    sd = statistics.stdev(rs) if n > 1 else 0.0
    half = 1.645 * sd / math.sqrt(n) if n > 1 else None
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"n": n, "win_rate": round(len(wins) / n, 3), "avg_r": round(mean, 3),
            "ci90": [round(mean - half, 3), round(mean + half, 3)] if half is not None else None, "total_r": round(sum(rs), 2),
            "avg_win_r": round(sum(wins) / len(wins), 3) if wins else None, "avg_loss_r": round(sum(losses) / len(losses), 3) if losses else None,
            "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None, "max_drawdown_r": round(dd, 2),
            "enough": n >= MIN_SAMPLE}


def _m(ds: list[TsDecision]) -> dict[str, Any]:
    out = metrics([d.r_net for d in ds if d.r_net is not None])
    if ds:
        mae = [d.mae_r for d in ds if d.mae_r is not None]
        mfe = [d.mfe_r for d in ds if d.mfe_r is not None]
        cost = [d.cost_r for d in ds if d.cost_r is not None]
        out |= {"avg_mae_r": round(sum(mae) / len(mae), 3) if mae else None, "avg_mfe_r": round(sum(mfe) / len(mfe), 3) if mfe else None,
                "avg_cost_r": round(sum(cost) / len(cost), 3) if cost else None}
    return out


def _bucket(v: float | None, buckets: tuple[tuple[float, float], ...]) -> str | None:
    if v is None:
        return None
    for lo, hi in buckets:
        if lo <= v < hi:
            return f"{lo:g}–{min(hi, 100 if hi > 1.5 else 1):g}"
    return None


def calibration(ds: list[TsDecision], attr: str, buckets: tuple[tuple[float, float], ...]) -> dict[str, Any]:
    """Do higher scores actually do better? Per bucket results, and a verdict that only says "validated" when every bucket has
    enough samples AND the results rise with the score AND the top bucket's 90% interval is above the bottom one's mean."""
    rows = []
    for lo, hi in buckets:
        part = [d for d in ds if getattr(d, attr) is not None and lo <= getattr(d, attr) < hi]
        rows.append({"bucket": _bucket(lo, buckets), **_m(part)})
    filled = [r for r in rows if r["n"]]
    if not filled or any(not r.get("enough") for r in filled) or len(filled) < 2:
        verdict = "not enough data"
    else:
        rising = all(a["avg_r"] <= b["avg_r"] for a, b in zip(filled, filled[1:]))
        top, bottom = filled[-1], filled[0]
        sep = top.get("ci90") and top["ci90"][0] > bottom["avg_r"]
        verdict = "validated: higher scores did better" if rising and sep else "not validated: higher scores did not reliably do better"
    return {"by": attr, "buckets": rows, "verdict": verdict}


def report(db: Session, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    since = int((now - timedelta(days=days)).timestamp() * 1000)
    all_ds = list(db.scalars(select(TsDecision).where(TsDecision.candle_t >= since)))
    done = [d for d in all_ds if d.outcome == "DONE"]
    counts: dict[str, int] = defaultdict(int)
    for d in all_ds:
        counts[d.state] += 1
        counts["outcome_" + d.outcome.lower()] += 1
    taken = [d for d in done if d.state == "READY" and d.signal_id is not None]
    ready_shadow = [d for d in done if d.state == "READY" and d.signal_id is None]
    rejected = [d for d in done if d.state == "REJECTED"]
    by_check: dict[str, list[TsDecision]] = defaultdict(list)
    for d in rejected:
        by_check[d.rejected_by or "?"].append(d)
    approved_m = _m(taken + ready_shadow)
    filters = []
    for check, ds in sorted(by_check.items(), key=lambda kv: -len(kv[1])):
        m = _m(ds)
        if not m.get("enough"):
            verdict = f"not enough data ({m['n']} resolved, need {MIN_SAMPLE})"
        elif m["avg_r"] > 0 and (not approved_m.get("n") or m["avg_r"] > approved_m["avg_r"]):
            verdict = "may be too restrictive: what it rejected did better than what passed (research it, don't switch it off on this alone)"
        elif m["ci90"] and m["ci90"][1] < 0:
            verdict = "doing its job: what it rejected lost money"
        else:
            verdict = "no clear effect"
        filters.append({"check": check, **m, "verdict": verdict})
    regimes = defaultdict(list)
    for d in done:
        regimes[d.regime or "?"].append(d)
    versions = defaultdict(list)
    for d in done:
        versions[f"{d.strategy}:{d.version}"].append(d)
    exits = defaultdict(int)
    for d in done:
        exits[d.exit_kind or "?"] += 1
    return {"hypothetical": True, "days": days, "since": datetime.fromtimestamp(since / 1000, tz=timezone.utc).isoformat(),
            "note": ("SHADOW results: what each plan would have done on the real candles that followed, after taker fees, "
                     "slippage and real funding. Hypothetical, not members' trading results; spread and fill quality at the moment "
                     "of entry can't be replayed exactly."),
            "counts": dict(counts), "approved": {"taken": _m(taken), "ready_shadow": _m(ready_shadow), "all": approved_m},
            "rejected": _m(rejected), "filters": filters,
            "by_regime": [{"regime": k, "label": REGIME_NAME.get(k, k), **_m(v)} for k, v in sorted(regimes.items(), key=lambda kv: -len(kv[1]))],
            "by_version": [{"version": k, **_m(v)} for k, v in sorted(versions.items())],
            "exits": dict(exits),
            "calibration": {"score": calibration(taken + ready_shadow + rejected, "score", SCORE_BUCKETS),
                            "ml_p": calibration(taken + ready_shadow + rejected, "ml_p", ML_BUCKETS)},
            "rules": ["Hypothetical results are never shown as trading performance.",
                      "A filter is only changed after research → backtest → out-of-sample → paper → owner approval.",
                      f"Groups under {MIN_SAMPLE} resolved decisions draw no conclusion."]}
