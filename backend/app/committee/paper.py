"""committee.paper: the Kiwi AI Investment Committee's PAPER book (owner's Phase 10). Simulated, never real money.

No live trading path exists here or anywhere in app/committee: nothing places, changes or cancels an order, nothing touches
tr_positions or a member's account. The book is its own table (cm_paper_trades) and its P&L is never merged with real P&L.

Booking (owner switch TsSettings.strategies["committee"]["paper_enabled"], default on; off = runs are recorded as "skipped" and
nothing new is simulated, trades already in the book are still resolved; also "paper_start_equity" (10,000) and "paper_risk_pct" (0.5)):
  every finished LIVE run on a Hyperliquid perpetual whose verdict is LONG or SHORT gets exactly one row (keyed by run_id, so
  running the job twice never books twice):
    risk gate APPROVED -> "pending" (waiting for its entry) with the plan (roles.plan_from: entry zone, stop, targets) and the
                          gate's own sizing (risk.plan_trade on the reference paper account; see risk_gate.reference)
    risk gate REJECTED -> "rejected" with the gate's reasons, plus a HYPOTHETICAL replay of the same plan (risk_check["hypothetical"],
                          sized at paper_risk_pct of the paper equity) so the filters can be judged. Rejected rows never move the
                          paper equity and never occupy a coin.
    WAIT               -> no row.
  One active (pending / open) paper trade per coin. A newer approved run on the same coin and side while one is active ->
  "skipped" (recorded, so it isn't re-booked). A newer LONG / SHORT verdict on the same coin with the OPPOSITE side (approved
  or not: the research view flipped) -> the active trade is cut at the new decision time: still pending -> "cancelled";
  open -> closed at the open of the first candle at / after that time (exit_reason "reversed"). WAIT changes nothing (the
  stop and targets stay).

Fills (post-hoc, from real Hyperliquid candles; replayed from the decision every job run, so there is no polling lag):
  candles      1m when the decision is inside Hyperliquid's 1m window (5000 candles back, minus a margin), else 5m, else 15m,
               else 1h; the resolution used is recorded (risk_check["sim"]["resolution"]) and never gets finer later.
               Only candles that OPEN at / after the decision time and have CLOSED count (no look-ahead).
  entry        the first candle within the validity window (VALID_CANDLES run-timeframe candles after the decision) whose range
               touches the entry zone fills at the zone edge it reached (the open itself when it opened inside the zone), plus
               slippage against us (backtest.slip: 0.02% BTC/ETH, 0.05% others). Never touched -> "expired".
               A fill on the wrong side of the stop -> "expired" (no trade).
  management   like live execution: the stop for the full size; TP1 (the plan's first target) for half; TP2 = the fill + 1.5x
               the TP1 distance (risk.tp2_of) for the rest. Stop first when a candle touches both the stop and a target. On the
               entry candle only the stop is checked (we can't know the order inside it: the conservative case). A candle that
               opens through the stop fills at its open (gap). TP1 and TP2 in the same candle both fill.
  time exit    MAX_HOLD after the entry (by the run's timeframe) -> the rest closes at that candle's close.
  costs        taker fee 0.045% on every fill (backtest.FEE); slippage per fill (inside the prices; reported as slippage_usd);
               real hourly funding from Hyperliquid fundingHistory on the size still open at each funding time, valued at the
               last closed candle's price (funding_usd > 0 = paid, < 0 = received; longs pay positive rates, shorts receive).
  results      pnl_usd = gross - fees - funding; pnl_r = pnl_usd / (size x |entry fill - stop|); MAE / MFE in R over the candles
               the trade was open (risk_check["sim"]).
Statuses: pending, open, closed, expired, cancelled, skipped, rejected.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.committee import CmPaperTrade, CmRun
from app.tradingos import backtest as bt
from app.tradingos import risk

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {"paper_enabled": True, "paper_start_equity": 10_000.0, "paper_risk_pct": 0.5}
ACTIVE = ("pending", "open")
FINAL = ("closed", "expired", "cancelled", "skipped")
VALID_CANDLES = 3  # the plan's entry is valid for this many run-timeframe candles after the decision
TF_S = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
MAX_HOLD = {"15m": timedelta(hours=6), "1h": timedelta(hours=24), "4h": timedelta(days=4), "1d": timedelta(days=14)}
RESOLUTIONS = (("1m", 60), ("5m", 300), ("15m", 900), ("1h", 3600))
HL_WINDOW = 4800  # candles back Hyperliquid's candleSnapshot still serves (5000), minus a margin
BOOK_WITHIN = timedelta(days=14)  # runs older than this are never booked late
MAX_RESOLVE = 60
LABEL = "PAPER"
NOTE = ("PAPER trading: simulated fills on real Hyperliquid candles (fees, slippage and real funding included). No real money, "
        "never merged with real trading results. Rejected trades' results are hypothetical and never touch the paper equity.")


# ---------------------------------------------------------------- settings and book state
def config(db: Session) -> dict[str, Any]:
    from app.models.tradingos import TsSettings

    s = db.scalar(select(TsSettings).limit(1))
    c = (((s.strategies or {}) if s else {}).get("committee") or {})
    return {"paper_enabled": c.get("paper_enabled", True) is not False,
            "paper_start_equity": float(c.get("paper_start_equity", DEFAULTS["paper_start_equity"])),
            "paper_risk_pct": float(c.get("paper_risk_pct", DEFAULTS["paper_risk_pct"]))}


def equity(db: Session, cfg: dict[str, Any] | None = None) -> float:
    """Starting equity + the realised P&L of CLOSED paper trades (rejected / hypothetical results never count)."""
    cfg = cfg or config(db)
    pnl = db.scalar(select(func.coalesce(func.sum(CmPaperTrade.pnl_usd), 0.0)).where(CmPaperTrade.status == "closed")) or 0.0
    return round(float(cfg["paper_start_equity"]) + float(pnl), 2)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime | None) -> str | None:
    return _aware(d).isoformat().replace("+00:00", "Z") if d else None


def book_state(db: Session, now: datetime) -> dict[str, Any]:
    """What the risk gate needs from the reference paper account: active trades (and their exposure), today's and the last
    7 days' realised P&L, and the trades opened today (pending / open / closed / expired / cancelled; not rejected / skipped)."""
    now = _aware(now)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active = list(db.scalars(select(CmPaperTrade).where(CmPaperTrade.status.in_(ACTIVE))))

    def realised(since: datetime) -> float:
        return float(db.scalar(select(func.coalesce(func.sum(CmPaperTrade.pnl_usd), 0.0)).where(
            CmPaperTrade.status == "closed", CmPaperTrade.closed_at >= since)) or 0.0)

    today = db.scalar(select(func.count()).select_from(CmPaperTrade).where(
        CmPaperTrade.status.in_(ACTIVE + ("closed", "expired", "cancelled")), CmPaperTrade.decided_at >= day0)) or 0
    exp = [{"side": t.side, "notional": float(t.size_usd or 0),
            "margin": float(t.size_usd or 0) / float(((t.risk_check or {}).get("sizing") or {}).get("leverage") or 3)} for t in active]
    return {"open": len(active), "day_pnl": realised(day0), "week_pnl": realised(now - timedelta(days=7)), "exposure": exp,
            "trades_today": int(today)}


# ---------------------------------------------------------------- market data (tests replace these)
def fetch_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    from app.committee import outcomes

    return outcomes.fetch_candles(coin, interval, start_ms, end_ms)


def fetch_funding(coin: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Hyperliquid fundingHistory: [(ms, rate per hour)], oldest first (a public read)."""
    from app.tradingos import market

    rows = market.data()._post({"type": "fundingHistory", "coin": coin, "startTime": start_ms, "endTime": end_ms})
    return sorted((int(r["time"]), float(r["fundingRate"])) for r in rows or [])


def resolution_for(start: datetime, now: datetime, used: str | None = None) -> tuple[str, int]:
    """The finest candle interval whose Hyperliquid window still reaches back to `start` (never finer than one used before)."""
    age_s = max(0.0, (_aware(now) - _aware(start)).total_seconds())
    floor = next((i for i, (n, _) in enumerate(RESOLUTIONS) if n == used), 0)
    for name, step in RESOLUTIONS[floor:]:
        if age_s / step <= HL_WINDOW:
            return name, step
    return RESOLUTIONS[-1]


# ---------------------------------------------------------------- the replay (pure: synthetic candles in the tests)
def units_for(eq: float, risk_pct: float, entry: float, stop: float, coin: str) -> float:
    """Size from the loss to the stop (fees and slippage included), as the risk engine does."""
    per_unit = abs(entry - stop) + entry * 2 * (bt.FEE + bt.slip(coin))
    return eq * risk_pct / 100 / per_unit if per_unit > 0 else 0.0


def replay(candles: list[dict[str, Any]], *, coin: str, side: str, zone: tuple[float, float], stop: float, tp1: float, units: float,
           decided_ms: int, step_ms: int, valid_ms: int, max_hold_ms: int, now_ms: int, fund: list[tuple[int, float]] | None = None,
           cut_ms: int | None = None) -> dict[str, Any]:
    """One paper trade on candles [{"t","o","h","l","c"}] (t = open time, ms). cut_ms: the time an opposite verdict cuts it.
    Returns {"state": pending | open | closed | expired | cancelled, ...} with fills and accounting when there is an entry."""
    long, sg, sl = side == "LONG", (1 if side == "LONG" else -1), bt.slip(coin)
    lo, hi = min(zone), max(zone)
    cs = sorted((c for c in candles if c["t"] >= decided_ms and c["t"] + step_ms <= now_ms), key=lambda c: c["t"])
    seen_to = cs[-1]["t"] + step_ms if cs else decided_ms
    valid_until = decided_ms + valid_ms
    out: dict[str, Any] = {"state": "pending", "fills": [], "valid_until_ms": valid_until}
    e = None
    for i, c in enumerate(cs):
        if cut_ms is not None and c["t"] >= cut_ms:
            break
        if c["t"] >= valid_until:
            break
        if c["l"] <= hi and c["h"] >= lo:
            e = i
            break
    if e is None:
        if cut_ms is not None and seen_to >= min(cut_ms, valid_until) and cut_ms <= valid_until:
            return {**out, "state": "cancelled", "reason": "superseded by an opposite verdict before the entry", "t_end": cut_ms}
        if seen_to >= valid_until:
            return {**out, "state": "expired", "reason": f"price never traded into the entry zone within {VALID_CANDLES} candles",
                    "t_end": valid_until}
        return out
    c0 = cs[e]
    raw = min(max(c0["o"], lo), hi)
    entry = raw * (1 + sg * sl)
    if (long and entry <= stop) or (not long and entry >= stop):
        return {**out, "state": "expired", "reason": "the entry would fill beyond the stop", "t_end": c0["t"]}
    t_entry = c0["t"]
    fills: list[dict[str, Any]] = [{"kind": "entry", "t": t_entry, "raw": raw, "px": entry, "frac": 1.0}]
    tp2 = risk.tp2_of(entry, tp1)
    rem, tp1_done, reason, t_exit = 1.0, False, "", None
    worst = best = 0.0
    R = abs(entry - stop) or 1e-12

    def exit_fill(kind: str, t: int, raw_px: float, frac: float) -> None:
        fills.append({"kind": kind, "t": t, "raw": raw_px, "px": raw_px * (1 - sg * sl), "frac": frac})

    for b in range(e, len(cs)):
        c = cs[b]
        tc = c["t"] + step_ms
        if b > e and cut_ms is not None and c["t"] >= cut_ms:
            exit_fill("reversed", c["t"], c["o"], rem)
            reason, t_exit, rem = ("tp1+" if tp1_done else "") + "reversed", c["t"], 0.0
            break
        worst = min(worst, ((c["l"] - entry) if long else (entry - c["h"])) / R)
        best = max(best, ((c["h"] - entry) if long else (entry - c["l"])) / R) if b > e else best
        if (long and c["l"] <= stop) or (not long and c["h"] >= stop):
            gap = b > e and ((long and c["o"] < stop) or (not long and c["o"] > stop))
            exit_fill("stop", tc, c["o"] if gap else stop, rem)
            reason, t_exit, rem = ("tp1+" if tp1_done else "") + ("gap-stop" if gap else "stop"), tc, 0.0
            break
        if b > e:
            if not tp1_done and ((long and c["h"] >= tp1) or (not long and c["l"] <= tp1)):
                exit_fill("tp1", tc, tp1, 0.5)
                rem, tp1_done = 0.5, True
            if tp1_done and ((long and c["h"] >= tp2) or (not long and c["l"] <= tp2)):
                exit_fill("tp2", tc, tp2, rem)
                reason, t_exit, rem = "tp1+tp2", tc, 0.0
                break
        if tc - t_entry >= max_hold_ms:
            exit_fill("time", tc, c["c"], rem)
            reason, t_exit, rem = ("tp1+" if tp1_done else "") + "time", tc, 0.0
            break
    res = {**out, "entry_raw": raw, "entry_fill": entry, "t_entry": t_entry, "tp1": tp1, "tp2": tp2, "tp1_done": tp1_done,
           "mae_r": round(max(worst, -3.0), 3), "mfe_r": round(best, 3)}
    if rem > 0:
        res.update(state="open", fills=fills)
        return res
    gross = sum((f["px"] - entry) * sg * f["frac"] * units for f in fills[1:])
    fees = sum(f["px"] * f["frac"] * units * bt.FEE for f in fills)
    slip_usd = sum(abs(f["px"] - f["raw"]) * f["frac"] * units for f in fills)
    funding = 0.0
    for t, rate in fund or []:
        if t_entry < t <= t_exit:
            left = sum(f["frac"] for f in fills[1:] if f["t"] >= t)
            px = next((c["c"] for c in reversed(cs) if c["t"] + step_ms <= t), entry)
            funding += rate * units * left * px * sg  # > 0: paid (longs pay positive funding), < 0: received
    pnl = gross - fees - funding
    exit_px = sum(f["px"] * f["frac"] for f in fills[1:])
    res.update(state="closed", fills=fills, exit_reason=reason, t_exit=t_exit, exit_fill=exit_px, gross_usd=gross, fees_usd=fees,
               funding_usd=funding, slippage_usd=slip_usd, pnl_usd=pnl, pnl_r=pnl / (units * R) if units > 0 else None)
    return res


# ---------------------------------------------------------------- booking
def _plan(run: CmRun) -> dict[str, Any] | None:
    from app.committee import roles

    ev = (run.result or {}).get("evidence") or {}
    return roles.plan_from(ev, run.verdict or "") if ev else None


def _decided(run: CmRun) -> datetime:
    return _aware(run.finished_at or run.created_at)


def _models(run: CmRun) -> dict[str, Any]:
    m = (run.result or {}).get("meta") or {}
    return {"backend": m.get("backend") or run.backend, "models": m.get("models") or {}, "prompt_version": m.get("prompt_version"),
            "framework": m.get("framework"), "run": run.public_id}


def _cut(t: CmPaperTrade, run: CmRun, at: datetime) -> None:
    rc = dict(t.risk_check or {})
    rc["cut"] = {"at": _iso(at), "by_run": run.public_id, "rule": f"opposite verdict ({run.verdict}) on {run.symbol}"}
    t.risk_check = rc


def book(db: Session, now: datetime | None = None, cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """New finished live runs -> paper rows (one per run; see the module doc)."""
    now = _aware(now or utcnow())
    cfg = cfg or config(db)
    booked = select(CmPaperTrade.run_id).where(CmPaperTrade.run_id.is_not(None))
    runs = list(db.scalars(select(CmRun).where(
        CmRun.status == "done", CmRun.mode == "live", CmRun.venue == "hyperliquid", CmRun.market_type == "perp",
        CmRun.verdict.in_(("LONG", "SHORT")), CmRun.exec_status.in_(("APPROVED", "REJECTED")),
        CmRun.created_at >= now - BOOK_WITHIN, CmRun.id.not_in(booked)).order_by(CmRun.id).limit(200)))
    out = {"pending": 0, "rejected": 0, "skipped": 0, "cut": 0}
    for run in runs:
        at = _decided(run)
        coin = (run.market_id or run.symbol).upper()
        ex = (run.result or {}).get("execution") or {}
        plan = _plan(run)
        active = db.scalar(select(CmPaperTrade).where(CmPaperTrade.symbol == coin, CmPaperTrade.status.in_(ACTIVE))
                           .order_by(CmPaperTrade.id.desc()).limit(1))
        if active is not None and "cut" in (active.risk_check or {}):
            active = None  # already being cut by an earlier opposite verdict
        if active is not None and active.side != run.verdict:
            _cut(active, run, at)
            out["cut"] += 1
            active = None
        base = dict(run_id=run.id, symbol=coin, side=run.verdict, decided_at=at, model_versions=_models(run),
                    research_cost_usd=float(run.cost_usd or 0.0),
                    stop=plan["stop"] if plan else None, targets=plan["targets"] if plan else None)
        rc = {"gate": {k: ex.get(k) for k in ("status", "reasons", "checks", "engine")}, "plan": plan, "timeframe": run.timeframe,
              "sizing": ex.get("sizing")}
        if not cfg["paper_enabled"]:  # switched off: recorded as skipped (so it is never booked late), nothing simulated
            db.add(CmPaperTrade(status="skipped", risk_check=rc, exit_reason="paper trading was switched off", **base))
            out["skipped"] += 1
            continue
        if run.exec_status == "REJECTED":
            eq = equity(db, cfg)
            ref = float(plan["entry_zone"][0] + plan["entry_zone"][1]) / 2 if plan else None
            rc["hypothetical"] = ({"state": "pending", "label": "HYPOTHETICAL: the risk engine rejected this trade",
                                   "units": units_for(eq, cfg["paper_risk_pct"], ref, plan["stop"], coin), "equity": eq}
                                  if plan else {"state": "no plan", "label": "HYPOTHETICAL: no plan in the evidence"})
            db.add(CmPaperTrade(status="rejected", entry_ref=ref, risk_check=rc, exit_reason="rejected by the risk gate", **base))
            out["rejected"] += 1
            continue
        if plan is None or not ex.get("sizing"):
            db.add(CmPaperTrade(status="skipped", risk_check=rc, exit_reason="no plan / sizing from the risk engine", **base))
            out["skipped"] += 1
            continue
        if active is not None:
            db.add(CmPaperTrade(status="skipped", risk_check=rc, exit_reason=f"one per coin: paper trade #{active.id} is active", **base))
            out["skipped"] += 1
            continue
        sz = ex["sizing"]
        db.add(CmPaperTrade(status="pending", entry_ref=float(sz["entry"]), size_usd=float(sz["notional"]), risk_check=rc, **base))
        out["pending"] += 1
        db.flush()
    db.commit()
    return out


# ---------------------------------------------------------------- resolving
def _window(t: CmPaperTrade) -> tuple[int, int, int]:
    tf = (t.risk_check or {}).get("timeframe") or "1h"
    return VALID_CANDLES * TF_S.get(tf, 3600) * 1000, int(MAX_HOLD.get(tf, MAX_HOLD["1h"]).total_seconds() * 1000), TF_S.get(tf, 3600)


async def _replay_row(t: CmPaperTrade, now: datetime, units: float, sim_key: str) -> dict[str, Any] | None:
    rc = t.risk_check or {}
    plan = rc.get("plan")
    if not plan:
        return None
    valid_ms, hold_ms, _ = _window(t)
    decided = _aware(t.decided_at)
    prev = (rc.get(sim_key) or {}).get("resolution")
    name, step = resolution_for(decided, now, prev)
    d_ms, now_ms = int(decided.timestamp() * 1000), int(now.timestamp() * 1000)
    cut = rc.get("cut") if sim_key == "sim" else None
    cut_ms = int(datetime.fromisoformat(cut["at"].replace("Z", "+00:00")).timestamp() * 1000) if cut else None
    end = min(now_ms, d_ms + valid_ms + hold_ms + 2 * step * 1000)
    cs = await asyncio.to_thread(fetch_candles, t.symbol, name, d_ms - step * 1000, end)
    kw = dict(coin=t.symbol, side=t.side, zone=(plan["entry_zone"][0], plan["entry_zone"][1]), stop=float(plan["stop"]),
              tp1=float(plan["targets"][0]), units=units, decided_ms=d_ms, step_ms=step * 1000, valid_ms=valid_ms, max_hold_ms=hold_ms,
              now_ms=now_ms, cut_ms=cut_ms)
    r = replay(cs, **kw)
    if r["state"] == "closed":
        fund = await asyncio.to_thread(fetch_funding, t.symbol, r["t_entry"], r["t_exit"])
        r = replay(cs, **kw, fund=fund)
    r["resolution"] = name
    return r


def _dt(ms: int | None) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms is not None else None


def _sim_view(r: dict[str, Any]) -> dict[str, Any]:
    keep = ("state", "resolution", "reason", "exit_reason", "entry_fill", "exit_fill", "tp1", "tp2", "tp1_done", "mae_r", "mfe_r",
            "pnl_usd", "pnl_r", "fees_usd", "funding_usd", "slippage_usd")
    v = {k: (round(r[k], 8) if isinstance(r.get(k), float) else r.get(k)) for k in keep if k in r}
    v["fills"] = [{"kind": f["kind"], "at": _iso(_dt(f["t"])), "price": f["px"], "fraction": f["frac"]} for f in r.get("fills") or []]
    v["opened_at"], v["closed_at"] = _iso(_dt(r.get("t_entry"))), _iso(_dt(r.get("t_exit") or r.get("t_end")))
    return v


def _apply(t: CmPaperTrade, r: dict[str, Any]) -> None:
    rc = dict(t.risk_check or {})
    rc["sim"] = _sim_view(r)
    t.risk_check = rc
    st = r["state"]
    if st in ("open", "closed"):
        t.entry_fill, t.opened_at = r["entry_fill"], _dt(r["t_entry"])
    if st == "closed":
        t.status, t.exit_fill, t.exit_reason, t.closed_at = "closed", r["exit_fill"], r["exit_reason"], _dt(r["t_exit"])
        t.fees_usd, t.funding_usd, t.slippage_usd = round(r["fees_usd"], 6), round(r["funding_usd"], 6), round(r["slippage_usd"], 6)
        t.pnl_usd, t.pnl_r = round(r["pnl_usd"], 6), (round(r["pnl_r"], 4) if r["pnl_r"] is not None else None)
    elif st in ("expired", "cancelled"):
        t.status, t.exit_reason, t.closed_at = st, r.get("reason"), _dt(r.get("t_end"))
    elif st == "open":
        t.status = "open"


async def resolve(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = _aware(now or utcnow())
    out: dict[str, Any] = {"resolved": 0, "open": 0, "pending": 0, "errors": 0, "hypothetical": 0}
    rows = list(db.scalars(select(CmPaperTrade).where(CmPaperTrade.status.in_(ACTIVE)).order_by(CmPaperTrade.id).limit(MAX_RESOLVE)))
    rows += [t for t in db.scalars(select(CmPaperTrade).where(CmPaperTrade.status == "rejected").order_by(CmPaperTrade.id.desc()).limit(400))
             if ((t.risk_check or {}).get("hypothetical") or {}).get("state") in ("pending", "open")][:MAX_RESOLVE]
    for t in rows:
        hyp = t.status == "rejected"
        try:
            if hyp:
                h = t.risk_check["hypothetical"]
                r = await _replay_row(t, now, float(h["units"]), "hypothetical")
            else:
                units = float(t.size_usd or 0) / float(t.entry_ref) if t.entry_ref else 0.0
                r = await _replay_row(t, now, units, "sim")
        except Exception as e:  # noqa: BLE001 - Hyperliquid down: next run
            logger.info("committee paper: trade %s replay failed (%s)", t.id, type(e).__name__)
            out["errors"] += 1
            continue
        if r is None:
            continue
        if hyp:
            rc = dict(t.risk_check)
            rc["hypothetical"] = {**rc["hypothetical"], **_sim_view(r)}
            t.risk_check = rc
            out["hypothetical"] += 1
        else:
            _apply(t, r)
            out[{"pending": "pending", "open": "open"}.get(r["state"], "resolved")] += 1
        db.commit()
    return out


async def run_job(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """The committee.paper job: book new runs, then replay every active trade (and rejected ones, hypothetically)."""
    now = _aware(now or utcnow())
    cfg = config(db)
    booked = book(db, now, cfg)
    return {"booked": booked, **(await resolve(db, now)), "paper_enabled": cfg["paper_enabled"]}


# ---------------------------------------------------------------- the public view (GET /api/committee/paper)
def stats(pnls: list[float], start: float) -> dict[str, Any]:
    """Equity path, max drawdown (USD and % of the peak) and the longest run of losing trades, in closing order."""
    eq, peak, dd, dd_pct, streak, longest = start, start, 0.0, 0.0, 0, 0
    path = [start]
    for p in pnls:
        eq += p
        path.append(eq)
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        dd_pct = min(dd_pct, (eq - peak) / peak * 100 if peak > 0 else 0.0)
        streak = streak + 1 if p < 0 else 0
        longest = max(longest, streak)
    wins = sum(1 for p in pnls if p > 0)
    return {"equity": round(eq, 2), "path": [round(x, 2) for x in path], "max_drawdown_usd": round(dd, 2),
            "max_drawdown_pct": round(dd_pct, 3), "longest_losing_streak": longest,
            "win_rate": round(wins / len(pnls), 3) if pnls else None, "n": len(pnls)}


def _row(t: CmPaperTrade, pid: str | None) -> dict[str, Any]:
    rc = t.risk_check or {}
    plan = rc.get("plan") or {}
    sim = rc.get("sim") or {}
    return {"id": t.id, "run": pid, "symbol": t.symbol, "side": t.side, "status": t.status, "timeframe": rc.get("timeframe"),
            "decided_at": _iso(t.decided_at), "opened_at": _iso(t.opened_at), "closed_at": _iso(t.closed_at),
            "entry_zone": plan.get("entry_zone"), "stop": t.stop, "targets": t.targets, "tp2": sim.get("tp2"),
            "entry_ref": t.entry_ref, "entry_fill": t.entry_fill, "exit_fill": t.exit_fill, "exit_reason": t.exit_reason,
            "size_usd": t.size_usd, "fees_usd": t.fees_usd, "funding_usd": t.funding_usd, "slippage_usd": t.slippage_usd,
            "pnl_usd": t.pnl_usd, "pnl_r": t.pnl_r, "mae_r": sim.get("mae_r"), "mfe_r": sim.get("mfe_r"),
            "resolution": sim.get("resolution"), "tp1_done": sim.get("tp1_done"), "research_cost_usd": t.research_cost_usd,
            "model_versions": {k: v for k, v in (t.model_versions or {}).items() if k != "run"}, "cut": rc.get("cut"),
            "gate": {"status": (rc.get("gate") or {}).get("status"), "reasons": (rc.get("gate") or {}).get("reasons") or []}}


def view(db: Session, limit: int = 100) -> dict[str, Any]:
    cfg = config(db)
    rows = list(db.scalars(select(CmPaperTrade).order_by(CmPaperTrade.id)))
    pids = dict(db.execute(select(CmRun.id, CmRun.public_id).where(CmRun.id.in_([t.run_id for t in rows if t.run_id]))).all()) if rows else {}
    closed = sorted((t for t in rows if t.status == "closed"), key=lambda t: (_aware(t.closed_at), t.id))
    st = stats([float(t.pnl_usd or 0) for t in closed], cfg["paper_start_equity"])
    curve = [{"at": None, "equity": st["path"][0]}] + [{"at": _iso(t.closed_at), "equity": e, "trade": t.id}
                                                        for t, e in zip(closed, st["path"][1:])]
    worst = min(closed, key=lambda t: float(t.pnl_usd or 0)) if closed else None
    rejected = [t for t in rows if t.status == "rejected"]
    approved = [t for t in rows if t.status != "rejected"]
    hyp_done = [(t, (t.risk_check or {}).get("hypothetical") or {}) for t in rejected]
    hyp_done = [(t, h) for t, h in hyp_done if h.get("state") == "closed"]
    hyp_r = [float(h["pnl_r"]) for _, h in hyp_done if h.get("pnl_r") is not None]
    research = sum(float(t.research_cost_usd or 0) for t in rows)
    traded = [t for t in rows if t.status in ("pending", "open", "closed")]
    summary = {
        "label": LABEL, "start_equity": cfg["paper_start_equity"], "equity": st["equity"], "risk_pct": cfg["paper_risk_pct"],
        "net_pnl_usd": round(sum(float(t.pnl_usd or 0) for t in closed), 2),
        "fees_usd": round(sum(t.fees_usd for t in closed), 2), "funding_usd": round(sum(t.funding_usd for t in closed), 2),
        "slippage_usd": round(sum(t.slippage_usd for t in closed), 2), "closed": len(closed), "win_rate": st["win_rate"],
        "total_r": round(sum(float(t.pnl_r or 0) for t in closed), 3),
        "max_drawdown_usd": st["max_drawdown_usd"], "max_drawdown_pct": st["max_drawdown_pct"],
        "longest_losing_streak": st["longest_losing_streak"],
        "worst_trade": {"id": worst.id, "symbol": worst.symbol, "side": worst.side, "pnl_usd": worst.pnl_usd, "pnl_r": worst.pnl_r} if worst else None,
        "research_cost_usd": round(research, 4),
        "research_cost_per_trade_usd": round(sum(float(t.research_cost_usd or 0) for t in traded) / len(traded), 4) if traded else None,
        "net_after_research_usd": round(sum(float(t.pnl_usd or 0) for t in closed) - sum(float(t.research_cost_usd or 0) for t in traded), 2),
        "counts": {"approved": len(approved) - sum(1 for t in approved if t.status == "skipped"), "rejected": len(rejected),
                   **{s: sum(1 for t in rows if t.status == s) for s in ("pending", "open", "closed", "expired", "cancelled", "skipped")}},
        "rejected_hypothetical": {"label": "HYPOTHETICAL: what the trades the risk engine rejected would have done (never in the equity)",
                                  "resolved": len(hyp_done), "win_rate": round(sum(1 for r in hyp_r if r > 0) / len(hyp_r), 3) if hyp_r else None,
                                  "total_r": round(sum(hyp_r), 3), "avg_r": round(sum(hyp_r) / len(hyp_r), 3) if hyp_r else None},
    }

    def rej(t: CmPaperTrade) -> dict[str, Any]:
        h = (t.risk_check or {}).get("hypothetical") or {}
        return {**_row(t, pids.get(t.run_id)), "hypothetical": {k: h.get(k) for k in ("label", "state", "entry_fill", "exit_fill", "exit_reason",
                                                                                      "reason", "pnl_r", "pnl_usd", "resolution", "opened_at",
                                                                                      "closed_at")}}

    newest = lambda xs: sorted(xs, key=lambda t: -t.id)[:limit]  # noqa: E731
    return {"mode": LABEL, "paper": True, "enabled": cfg["paper_enabled"], "note": NOTE, "summary": summary, "equity_curve": curve,
            "active": [_row(t, pids.get(t.run_id)) for t in newest([t for t in rows if t.status in ACTIVE])],
            "closed": [_row(t, pids.get(t.run_id)) for t in newest([t for t in rows if t.status in ("closed", "expired", "cancelled")])],
            "rejected": [rej(t) for t in newest(rejected)],
            "rules": {"entry": f"price trades into the entry zone within {VALID_CANDLES} run-timeframe candles; filled at the zone edge "
                               "reached (or the open inside it) plus slippage (0.02% BTC/ETH, 0.05% others)",
                      "management": "stop for the full size; TP1 half at the plan's first target; TP2 the rest at 1.5x the TP1 distance; "
                                    "stop first when a candle touches both; a gap through the stop fills at the open",
                      "max_hold": {k: f"{v.total_seconds() / 3600:g} h" for k, v in MAX_HOLD.items()},
                      "costs": "taker fee 0.045% each fill, slippage per fill, real hourly Hyperliquid funding",
                      "one_per_coin": "one pending/open paper trade per coin; an opposite LONG/SHORT verdict cancels a pending one or "
                                      "closes an open one at the next candle's open"}}
