"""committee.outcomes: what the market did after each committee run, from Hyperliquid candles (the track record).

For each finished live run on a Hyperliquid perpetual and each horizon (1h, 4h, 24h, 7d) whose time has passed:
  price_from = the close of the FIRST candle that closed after the run was created (never a price from before the decision)
  price_to   = the close of the last candle that closed by created_at + horizon
  ret_pct    = price_to / price_from - 1, in %; mfe / mae = the best / worst excursion in between, for the verdict's side
  direction_ok = LONG: ret > 0 · SHORT: ret < 0 · WAIT: None ("no position"; the move is still recorded)
Candles only from Hyperliquid's candleSnapshot (a public read). Markets elsewhere are not scored here (informational runs).
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.committee import CmOutcome, CmRun

logger = logging.getLogger(__name__)

HORIZONS = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
INTERVAL = {"1h": ("5m", 300), "4h": ("5m", 300), "24h": ("15m", 900), "7d": ("1h", 3600)}
MAX_RUNS = 40


def fetch_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    """[{"t", "o", "h", "l", "c"}] from Hyperliquid (tests replace this)."""
    from app.tradingos import market

    rows = market.data()._post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}})
    return [{"t": int(r["t"]), "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]), "c": float(r["c"])} for r in rows or []]


def score(run: CmRun, horizon: str, candles: list[dict[str, Any]], step_s: int) -> dict[str, Any] | None:
    created = run.created_at.timestamp() * 1000
    end = created + HORIZONS[horizon].total_seconds() * 1000
    closed = sorted((c for c in candles if c["t"] + step_s * 1000 > created and c["t"] + step_s * 1000 <= end), key=lambda c: c["t"])
    if len(closed) < 2:
        return None
    first, last = closed[0], closed[-1]
    if last["t"] + step_s * 1000 < end - step_s * 1000:
        return None  # the window isn't covered yet (or Hyperliquid has a gap): try again later
    p0, p1 = first["c"], last["c"]
    after = closed[1:]
    hi, lo = max(c["h"] for c in after), min(c["l"] for c in after)
    side = run.verdict or "WAIT"
    up, down = (hi / p0 - 1) * 100, (lo / p0 - 1) * 100
    mfe, mae = (up, down) if side != "SHORT" else (-down, -up)
    ret = (p1 / p0 - 1) * 100
    return {"price_from": p0, "price_to": p1, "ret_pct": round(ret, 4),
            "direction_ok": (ret > 0) if side == "LONG" else (ret < 0) if side == "SHORT" else None,
            "mfe_pct": round(mfe, 4) if side != "WAIT" else round(up, 4), "mae_pct": round(mae, 4) if side != "WAIT" else round(down, 4)}


async def resolve(db: Session, now: Any = None) -> dict[str, Any]:
    now = now or utcnow()
    runs = list(db.scalars(select(CmRun).where(CmRun.status == "done", CmRun.mode == "live", CmRun.venue == "hyperliquid",
                                               CmRun.market_type == "perp", CmRun.created_at >= now - timedelta(days=9))
                           .order_by(CmRun.id).limit(500)))
    have = {(o.run_id, o.horizon) for o in db.scalars(select(CmOutcome).where(CmOutcome.run_id.in_([r.id for r in runs])))} if runs else set()
    out = {"resolved": 0, "pending": 0, "errors": 0}
    done_runs = 0
    for r in runs:
        todo = [h for h, d in HORIZONS.items() if (r.id, h) not in have and r.created_at + d + timedelta(minutes=2) <= now]
        if not todo:
            continue
        done_runs += 1
        if done_runs > MAX_RUNS:
            break
        for h in todo:
            iv, step = INTERVAL[h]
            start = int(r.created_at.timestamp() * 1000) - step * 1000
            end = int((r.created_at + HORIZONS[h]).timestamp() * 1000) + step * 1000
            try:
                candles = await asyncio.to_thread(fetch_candles, r.market_id or r.symbol, iv, start, end)
            except Exception as e:  # noqa: BLE001 - Hyperliquid down: next run
                logger.info("committee outcomes: %s %s candles failed (%s)", r.public_id, h, type(e).__name__)
                out["errors"] += 1
                continue
            s = score(r, h, candles, step)
            if s is None:
                out["pending"] += 1
                continue
            db.add(CmOutcome(run_id=r.id, horizon=h, resolved_at=now, source=f"hyperliquid {iv} candles", **s))
            out["resolved"] += 1
        db.commit()
    return out


def track(db: Session, limit: int = 50) -> dict[str, Any]:
    """GET /api/committee/track: every scored verdict by verdict and horizon, plus the latest runs with their outcomes."""
    rows = db.execute(select(CmRun.verdict, CmOutcome.horizon, CmOutcome.ret_pct, CmOutcome.direction_ok)
                      .join(CmOutcome, CmOutcome.run_id == CmRun.id)).all()
    agg: dict[str, dict[str, dict[str, Any]]] = {}
    for verdict, h, ret, ok in rows:
        a = agg.setdefault(verdict or "WAIT", {}).setdefault(h, {"n": 0, "right": 0, "sum_ret": 0.0})
        a["n"] += 1
        a["right"] += 1 if ok else 0
        a["sum_ret"] += ret
    # "hits" next to "n" so a 100% hit rate on 2 calls reads as 2 of 2; WAIT has no direction to be right about ("no position")
    by = {v: {h: {"n": a["n"], "avg_ret_pct": round(a["sum_ret"] / a["n"], 3),
                  "hits": (a["right"] if v != "WAIT" else None),
                  "hit_rate": (round(a["right"] / a["n"], 3) if v != "WAIT" else None)} for h, a in hs.items()} for v, hs in agg.items()}
    scored = db.scalar(select(func.count(func.distinct(CmOutcome.run_id)))) or 0
    return {"by_verdict": by, "horizons": list(HORIZONS), "scored_runs": int(scored),
            "recent": [summary(db, r) for r in db.scalars(select(CmRun).where(CmRun.status == "done")
                                                          .order_by(CmRun.id.desc()).limit(limit))],
            "note": "WAIT is tracked as no position: its row shows the move that followed. Hit rate counts direction only, before costs."}


def outcomes_of(db: Session, run: CmRun) -> dict[str, Any]:
    return {o.horizon: {"ret_pct": o.ret_pct, "direction_ok": o.direction_ok, "price_from": o.price_from, "price_to": o.price_to,
                        "mfe_pct": o.mfe_pct, "mae_pct": o.mae_pct, "source": o.source,
                        "resolved_at": o.resolved_at.isoformat() if o.resolved_at else None}
            for o in db.scalars(select(CmOutcome).where(CmOutcome.run_id == run.id))}


def summary(db: Session, r: CmRun) -> dict[str, Any]:
    asset = (r.result or {}).get("asset") or {"symbol": r.symbol, "venue": r.venue, "market_type": r.market_type}
    return {"id": r.public_id, "status": r.status, "asset": asset, "timeframe": r.timeframe,
            "created_at": r.created_at.isoformat() if r.created_at else None, "price_at": r.price_at, "verdict": r.verdict,
            "rating": r.rating, "exec_status": r.exec_status, "outcomes": outcomes_of(db, r)}
