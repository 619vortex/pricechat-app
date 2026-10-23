"""The Kiwi risk gate: execution approval for a committee verdict, from the REAL deterministic risk code only.

The verdict's plan (copied from the evidence scenarios, never typed by a model) becomes a hypothetical entry on a reference
PAPER account and goes through the same code a member's trade goes through: app.tradingos.risk.plan_trade (sizing from the
loss to the stop, leverage caps incl. HARD_MAX_LEVERAGE, position / daily-loss limits, entry zone, liquidation vs stop,
Hyperliquid's minimum), risk.portfolio_checks (spread, slippage, exposure, correlation, margin, costs, 7-day drawdown, stop
distance; ENFORCED here even when the owner runs them advisory for members) and the Kiwi Risk Team desks
(app.tradingos.riskteam). Kill switches: the Trading OS global pause (TsSettings.paused, "trading is paused for everyone")
and the reference account's own STOP NEW TRADES.

Read-only: no order, no intent, no position, no member account is created or changed; ts_settings is read, never created.
The AI cannot override anything here: the gate never reads model text, only (action, plan, evidence). WAIT → NOT_APPLICABLE
("no trade proposed"); a market that isn't a Hyperliquid perpetual → NOT_APPLICABLE (informational research only).

Reference account (owner-tunable: TsSettings.strategies["committee"]["reference"]): REFERENCE below, the defaults of a new
member trading account with risk per trade = risk_pct of equity.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.committee import CmPaperTrade
from app.models.tradingos import TsSettings
from app.tradingos import risk, riskteam

NOTE = "Research only — nothing was traded."
REFERENCE: dict[str, Any] = {"equity": 1000.0, "risk_pct": 0.5, "leverage": 3, "max_leverage": 3, "max_allocation_pct": 10.0,
                             "max_positions": 2, "daily_loss_limit_usd": 15.0, "paused": False, "hold_h": 24.0}


def _settings(db: Session) -> TsSettings | None:
    return db.scalar(select(TsSettings).limit(1))


def reference(db: Session) -> dict[str, Any]:
    s = _settings(db)
    over = (((s.strategies or {}) if s else {}).get("committee") or {}).get("reference") or {}
    return {**REFERENCE, **{k: type(REFERENCE[k])(v) for k, v in over.items() if k in REFERENCE}}


def _result(status: str, reasons: list[str], checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": status, "reasons": reasons, "checks": checks, "engine": "kiwi-risk", "note": NOTE}


def _check(name: str, ok: bool, value: Any = None, limit: Any = None, why: str = "") -> dict[str, Any]:
    """`why` (the plain reason when it fails) is moved into the result's reasons, never left on the check."""
    return {"name": name, "ok": bool(ok), "value": None if value is None else str(value), "limit": None if limit is None else str(limit),
            "_why": why}


def _reasons(checks: list[dict[str, Any]]) -> list[str]:
    out = []
    for c in checks:
        why = c.pop("_why", "")
        if not c["ok"]:
            out.append(why or f"{c['name']}: {c['value']}" + (f" (limit {c['limit']})" if c["limit"] else ""))
    return out


def _paper_book(db: Session) -> tuple[int, float, float, list[dict[str, Any]]]:
    """The committee paper book's open count, today's and the week's realised P&L, open exposure (stage 2 fills it)."""
    now = utcnow()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    opened = list(db.scalars(select(CmPaperTrade).where(CmPaperTrade.status == "open")))
    day = float(db.scalar(select(func.coalesce(func.sum(CmPaperTrade.pnl_usd), 0.0)).where(
        CmPaperTrade.status == "closed", CmPaperTrade.closed_at >= day0)) or 0.0)
    week = float(db.scalar(select(func.coalesce(func.sum(CmPaperTrade.pnl_usd), 0.0)).where(
        CmPaperTrade.status == "closed", CmPaperTrade.closed_at >= now - timedelta(days=7))) or 0.0)
    exp = [{"side": t.side, "notional": float(t.size_usd or 0), "margin": float(t.size_usd or 0) / 3} for t in opened]
    return len(opened), day, week, exp


def _hl_perp(ev: dict[str, Any]) -> bool:
    a = ev.get("asset") or {}
    return a.get("venue") == "hyperliquid" and a.get("market_type") == "perp" and bool(a.get("hl_tradable", True))


def evaluate(db: Session, ev: dict[str, Any], action: str, plan: dict[str, Any] | None, *, stale_reason: str | None = None,
             scenarios: dict[str, dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """The execution block of a run. WAIT: NOT_APPLICABLE, and each evidence scenario (scenarios = {"LONG": plan, "SHORT":
    plan}) is still run through the engine so the run can say why no trade would be approved right now."""
    if not _hl_perp(ev):
        return _result("NOT_APPLICABLE", (["no trade proposed"] if action not in ("LONG", "SHORT") else [])
                       + ["execution only on Hyperliquid perpetuals"], [])
    if action not in ("LONG", "SHORT"):
        reasons, checks = ["no trade proposed"] + ([stale_reason] if stale_reason else []), []
        for side, p in (scenarios or {}).items():
            if not p:
                continue
            sub = _trade(db, ev, side, p, None)
            checks += [{**c, "name": f"{side.lower()} scenario · {c['name']}"} for c in sub["checks"]]
            reasons.append(f"{side.lower()} scenario now: " + ("the risk engine would approve it" if sub["status"] == "APPROVED"
                                                               else "; ".join(sub["reasons"][:3])))
        return _result("NOT_APPLICABLE", reasons, checks)
    return _trade(db, ev, action, plan, stale_reason)


def _trade(db: Session, ev: dict[str, Any], action: str, plan: dict[str, Any] | None, stale_reason: str | None) -> dict[str, Any]:
    asset, price = ev.get("asset") or {}, ev.get("price") or {}
    if plan is None:
        checks = [_check("stop-loss and targets from the evidence", False, why="no plan from the evidence scenarios")]
        return _result("REJECTED", _reasons(checks), checks)
    s = _settings(db)
    ref = reference(db)
    fund = (((ev.get("sections") or {}).get("fundamental") or {}).get("facts") or {})
    senti = (((ev.get("sections") or {}).get("sentiment") or {}).get("facts") or {})
    tech = (((ev.get("sections") or {}).get("technical") or {}))
    coin = asset.get("market_id") or asset.get("symbol") or ""
    mark = float(price.get("value") or 0)
    market_max = int(fund.get("max_leverage") or ref["max_leverage"])
    cap = min(int(ref["max_leverage"]), risk.HARD_MAX_LEVERAGE, market_max)
    checks = [
        _check("global kill switch (Trading OS pause)", not (s and s.paused), "paused" if s and s.paused else "off",
               why="the global kill switch is on (Trading OS paused)"),
        _check("reference account STOP NEW TRADES", not ref["paused"], "on" if ref["paused"] else "off",
               why="STOP NEW TRADES is on for the reference account"),
        _check("market data fresh", not price.get("stale") and mark > 0, price.get("ts"),
               why=f"market data is stale (price from {price.get('ts') or 'an unknown time'})" if mark > 0 else "no current price"),
        _check("technical data fresh", tech.get("status") in ("ok", "partial"), tech.get("status"), why=f"technical data is {tech.get('status')}"),
        _check("leverage within caps", int(ref["leverage"]) <= cap, f"{int(ref['leverage'])}x", f"{cap}x (owner {int(ref['max_leverage'])}x, "
               f"system {risk.HARD_MAX_LEVERAGE}x, market {market_max}x)", why=f"leverage {int(ref['leverage'])}x is over the cap of {cap}x"),
        _check("stop-loss set", plan.get("stop") is not None, plan.get("stop"), why="no stop-loss"),
    ]
    reasons = []
    if stale_reason:
        reasons.append(stale_reason)
    n_open, day_pnl, week_pnl, exposure = _paper_book(db)
    equity = float(ref["equity"])
    max_loss = round(equity * float(ref["risk_pct"]) / 100, 2)
    p = risk.plan_trade(coin=coin, side=action, mark=mark, entry_zone=(plan["entry_zone"][0], plan["entry_zone"][1]), stop=float(plan["stop"]),
                        target=float(plan["targets"][0]), sig_lev_hi=int(ref["leverage"]), market_max_lev=market_max,
                        sz_decimals=int(fund.get("sz_decimals") or 2), equity=equity, max_leverage=int(ref["max_leverage"]),
                        max_loss_usd=max_loss, max_allocation_pct=float(ref["max_allocation_pct"]), max_positions=int(ref["max_positions"]),
                        open_positions=n_open, daily_pnl=day_pnl, daily_loss_limit=float(ref["daily_loss_limit_usd"]), allowed=[],
                        paused=bool(ref["paused"]), global_paused=bool(s and s.paused))
    checks.append(_check("risk engine sizing (risk.plan_trade)", p.ok,
                         f"${p.notional:,.2f} at {p.leverage}x, loss at stop ${p.max_loss_usd:,.2f}" if p.ok else "; ".join(p.reasons),
                         f"max loss ${max_loss:,.2f} ({ref['risk_pct']:g}% of ${equity:,.0f})", why="; ".join(p.reasons)))
    if p.ok:
        cfg = risk.v2_settings(s.strategies if s else None)
        if s and "max_spread_bps" not in ((s.strategies or {}).get("risk_v2") or {}):
            cfg["max_spread_bps"] = s.max_spread_bps
        spread = fund.get("spread_bps") if isinstance(fund.get("spread_bps"), (int, float)) else None
        fh = senti.get("funding_1h_pct")
        for c in risk.portfolio_checks(cfg, side=action, entry=p.entry, stop=p.stop, notional=p.notional, margin=p.margin, equity=equity,
                                       open_positions=exposure, spread_bps=spread, levels=None,
                                       funding_h=float(fh) / 100 if isinstance(fh, (int, float)) else 0.0, hold_h=float(ref["hold_h"]),
                                       week_pnl=week_pnl):
            ok = c["status"] != "fail"
            checks.append(_check(c["label"], ok, c["detail"] if c["status"] != "unknown" else f"unknown: {c['detail']} (never blocks)",
                                 why=f"{c['label'].lower()}: {c['detail']}"))
    rcfg = riskteam.settings(s)
    if rcfg.get("enabled", True):
        fr = ((tech.get("facts") or {}).get("frames") or {}).get(ev.get("timeframe") or "1h") or {}
        rng = fr.get("last_range")
        rv = riskteam.review(riskteam.context(db), rcfg, coin, action, float(rng or 0), float(fr["atr14"]) if rng and fr.get("atr14") else None)
        for v in rv["votes"]:
            checks.append(_check(f"risk team: {v['label']}", v["vote"] != "veto", f"{v['vote']}: {v['detail']}", why=f"{v['label']}: {v['detail']}"))
    ok = all(c["ok"] for c in checks)
    reasons += [r for r in _reasons(checks) if r not in reasons]
    return _result("APPROVED" if ok else "REJECTED", [] if ok else reasons, checks)
