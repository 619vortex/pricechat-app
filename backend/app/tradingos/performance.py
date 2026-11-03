"""Signal performance report (internal, Admin → Trading OS): how the published signals really did, from their own records.

Per strategy and timeframe: outcomes, win rate, average and total result, results in R (over each signal's stop distance),
profit factor, how many expired without filling, how long trades lasted, the open signals' unrealized result at their
last tracked price, what fees cost, what slippage would add (the signal record charges taker fees only), and data-quality
checks (stale tracking, missing prices). Findings are rules applied to these numbers; nothing here changes a strategy.

    python -m app.tradingos.performance      prints it for the configured database
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import TsSignal
from app.tradingos.backtest import slip
from app.tradingos.signals import OPEN, TAKER_FEE

CLOSED = ("TARGET_HIT", "STOP_HIT", "CLOSED")
MIN_SAMPLE = 30  # fewer closed signals than this can't tell skill from luck
STALE = timedelta(minutes=10)  # an open signal not re-checked for this long has a data problem
BACKTEST_NOTE = ("2-year Binance backtest (docs/KIWI_BRAIN.md): every kitchen mode is about 0.00R before costs and about "
                 "-0.12R per trade after them; no tested variant held up out of sample.")


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _r(sig: TsSignal, pct: float | None) -> float | None:
    if pct is None:
        return None
    mid = (sig.entry_lo + sig.entry_hi) / 2
    risk = abs(mid - sig.stop) / mid * 100 if mid else 0
    return pct / risk if risk > 0 else None


def _unrealized(sig: TsSignal) -> float | None:
    """An ACTIVE signal's result at its last tracked price, unlevered, after round-trip fees (as settle() would)."""
    if sig.status != "ACTIVE" or not sig.last_price:
        return None
    mid = (sig.entry_lo + sig.entry_hi) / 2
    move = (sig.last_price - mid) / mid * (1 if sig.side == "LONG" else -1)
    return (move - 2 * TAKER_FEE) * 100


def _group(rows: list[TsSignal], now: datetime) -> dict[str, Any]:
    closed = [s for s in rows if s.status in CLOSED and s.model_pnl_pct is not None]
    pnl = [s.model_pnl_pct or 0.0 for s in closed]
    rs = [r for r in (_r(s, s.model_pnl_pct) for s in closed) if r is not None]
    slip_pct = [2 * slip(s.coin) * 100 for s in closed]
    net_slip = [p - c for p, c in zip(pnl, slip_pct)]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    filled = [s for s in rows if s.activated_at is not None]
    expired = [s for s in rows if s.status == "EXPIRED"]
    holds = [(_aware(s.closed_at) - _aware(s.activated_at)).total_seconds() / 3600 for s in closed if s.closed_at and s.activated_at]
    active = [s for s in rows if s.status == "ACTIVE"]
    unreal = [u for u in (_unrealized(s) for s in active) if u is not None]
    stale = [s.number for s in rows if s.status in OPEN and (s.last_checked_at is None or now - _aware(s.last_checked_at) > STALE)]
    no_exit = [s.number for s in closed if s.exit_price is None]
    outcomes = {k: sum(1 for s in rows if s.status == k) for k in ("TARGET_HIT", "STOP_HIT", "CLOSED", "EXPIRED", "CANCELLED", "WAITING", "ACTIVE")}
    eq = peak = mdd = 0.0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return {
        "signals": len(rows), "closed": len(closed), "outcomes": outcomes,
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_pct": round(sum(pnl) / len(pnl), 3) if pnl else None, "median_pct": round(median(pnl), 3) if pnl else None,
        "total_pct": round(sum(pnl), 3), "max_drawdown_pct": round(mdd, 3),
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None, "total_r": round(sum(rs), 2) if rs else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_win_pct": round(sum(wins) / len(wins), 3) if wins else None, "avg_loss_pct": round(sum(losses) / len(losses), 3) if losses else None,
        "fees_pct_per_trade": round(2 * TAKER_FEE * 100, 3),
        "slippage_pct_per_trade": round(sum(slip_pct) / len(slip_pct), 3) if slip_pct else None,
        "avg_pct_after_slippage": round(sum(net_slip) / len(net_slip), 3) if net_slip else None,
        "fill_rate": round(len(filled) / len(rows) * 100, 1) if rows else None,
        "expiry_rate": round(len(expired) / len(rows) * 100, 1) if rows else None,
        "median_hold_h": round(median(holds), 2) if holds else None,
        "open": len(active), "unrealized_pct": round(sum(unreal), 3) if unreal else 0.0,
        "data_quality": {"stale_open_signals": stale, "closed_without_exit_price": no_exit},
    }


def findings(g: dict[str, Any], name: str) -> list[str]:
    out = []
    n = g["closed"]
    if n < MIN_SAMPLE:
        out.append(f"{name}: only {n} closed signals. Too few to judge accuracy either way (need {MIN_SAMPLE}+).")
    if g["avg_pct_after_slippage"] is not None and g["avg_pct_after_slippage"] < 0:
        out.append(f"{name}: loses {g['avg_pct_after_slippage']:+.3f}% a trade once slippage is added "
                   f"(fees {g['fees_pct_per_trade']}% + slippage {g['slippage_pct_per_trade']}% per round trip).")
    if g["avg_pct"] is not None and g["avg_pct_after_slippage"] is not None and g["avg_pct"] > 0 > g["avg_pct_after_slippage"]:
        out.append(f"{name}: positive on paper but negative after slippage: the edge is smaller than the costs.")
    if g["win_rate"] is not None and g["avg_win_pct"] and g["avg_loss_pct"]:
        be = abs(g["avg_loss_pct"]) / (g["avg_win_pct"] + abs(g["avg_loss_pct"])) * 100
        if g["win_rate"] < be:
            out.append(f"{name}: win rate {g['win_rate']}% is below the {be:.0f}% needed at these average win / loss sizes.")
    if g["expiry_rate"] is not None and g["expiry_rate"] > 40 and g["signals"] >= 10:
        out.append(f"{name}: {g['expiry_rate']}% of signals expired unfilled: entries are too far from price, or the setups are late.")
    if g["outcomes"]["CLOSED"] > max(g["outcomes"]["TARGET_HIT"], g["outcomes"]["STOP_HIT"]) and n >= 10:
        out.append(f"{name}: most trades end on the time limit, not the target or stop: the targets may be too far for the hold time.")
    dq = g["data_quality"]
    if dq["stale_open_signals"]:
        out.append(f"{name}: open signals not re-checked for 10+ minutes: #{', #'.join(f'{x:03d}' for x in dq['stale_open_signals'])} "
                   "(tracking job or market data problem).")
    if dq["closed_without_exit_price"]:
        out.append(f"{name}: closed signals without an exit price: {len(dq['closed_without_exit_price'])}.")
    return out


def report(db: Session, now: datetime | None = None, days: int | None = None) -> dict[str, Any]:
    now = now or utcnow()
    q = select(TsSignal).where(TsSignal.published_at.is_not(None), TsSignal.strategy != "test")
    if days:
        q = q.where(TsSignal.created_at >= now - timedelta(days=days))
    rows = db.scalars(q.order_by(TsSignal.number)).all()
    groups: dict[tuple[str, str], list[TsSignal]] = defaultdict(list)
    for s in rows:
        groups[(s.strategy, s.timeframe)].append(s)
    by = []
    notes: list[str] = []
    for (strategy, tf), sigs in sorted(groups.items()):
        g = _group(sigs, now)
        name = f"{strategy} {tf}"
        by.append({"strategy": strategy, "timeframe": tf, **g, "findings": findings(g, name)})
        notes += by[-1]["findings"]
    overall = _group(list(rows), now)
    recs = []
    if overall["closed"] < MIN_SAMPLE:
        recs.append(f"Keep collecting: {overall['closed']} closed signals so far. Judge accuracy at {MIN_SAMPLE}+ per strategy.")
    if any(b["avg_pct_after_slippage"] is not None and b["avg_pct_after_slippage"] < 0 and b["closed"] >= MIN_SAMPLE for b in by):
        recs.append("A strategy with 30+ closed signals loses after costs: pause it or keep it paper-only. Don't tune it on these same "
                    "signals; test any change on data after today first (forward test).")
    recs.append("Any change must be backtested and then pass a holdout it was never tuned on before it goes live (docs/KIWI_BRAIN.md).")
    return {"label": "Published model signals (unlevered, taker fees included; slippage shown separately): not executed trades",
            "generated_at": now.isoformat(), "window_days": days, "overall": overall, "by_strategy": by,
            "findings": notes, "recommendations": recs, "backtest_reference": BACKTEST_NOTE}


RECORD_LABEL = "Model signal results (no leverage, after estimated fees and slippage): not executed trades. Past results don't predict the next signal."


def track_record(db: Session, only: tuple[str, str] | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """What members see next to a signal: how this setup's published signals ended so far, losers included. A record, never a
    probability for the next one; `enough` is False until MIN_SAMPLE closed signals exist."""
    q = select(TsSignal.strategy, TsSignal.timeframe, TsSignal.coin, TsSignal.model_pnl_pct).where(
        TsSignal.published_at.is_not(None), TsSignal.strategy != "test", TsSignal.status.in_(CLOSED), TsSignal.model_pnl_pct.is_not(None))
    if only:
        q = q.where(TsSignal.strategy == only[0], TsSignal.timeframe == only[1])
    groups: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for strategy, tf, coin, pnl in db.execute(q):
        groups[(strategy, tf)].append((pnl, pnl - 2 * slip(coin) * 100))
    out = {}
    for key, rows in groups.items():
        wins = sum(1 for pnl, _ in rows if pnl > 0)
        out[key] = {"closed": len(rows), "wins": wins, "losses": len(rows) - wins, "win_rate": round(wins / len(rows) * 100, 1),
                    "avg_pct_after_costs": round(sum(net for _, net in rows) / len(rows), 3), "enough": len(rows) >= MIN_SAMPLE,
                    "min_sample": MIN_SAMPLE, "label": RECORD_LABEL}
    return out


def record_of(db: Session, sig: TsSignal) -> dict[str, Any] | None:
    return None if sig.strategy == "test" else track_record(db, (sig.strategy, sig.timeframe)).get((sig.strategy, sig.timeframe))


def record_line(rec: dict[str, Any] | None) -> str | None:
    """The record on a Telegram card."""
    if not rec:
        return None
    judge = "" if rec["enough"] else " · too few to judge"
    return f"📊 This setup so far: {rec['wins']}W {rec['losses']}L · {rec['avg_pct_after_costs']:+.2f}%/trade after costs (model){judge}"


def text(rep: dict[str, Any]) -> str:
    o = rep["overall"]
    lines = [rep["label"], f"generated {rep['generated_at']}", "",
             f"ALL  {o['signals']} signals · {o['closed']} closed · win {o['win_rate']}% · avg {o['avg_pct']}% · after slippage "
             f"{o['avg_pct_after_slippage']}% · avg R {o['avg_r']} · PF {o['profit_factor']} · expired {o['expiry_rate']}%"]
    for b in rep["by_strategy"]:
        lines.append(f"{b['strategy']} {b['timeframe']}  {b['closed']} closed · win {b['win_rate']}% · avg {b['avg_pct']}% · after slip "
                     f"{b['avg_pct_after_slippage']}% · R {b['avg_r']} · {b['outcomes']}")
    lines += ["", "Findings:", *(f"- {f}" for f in rep["findings"] or ["none"]), "", "Recommendations:",
              *(f"- {r}" for r in rep["recommendations"]), "", rep["backtest_reference"]]
    return "\n".join(lines)


if __name__ == "__main__":
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        print(text(report(db)))
