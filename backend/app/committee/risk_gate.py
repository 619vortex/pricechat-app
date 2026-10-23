"""The Kiwi risk gate: execution approval for a committee verdict, from the REAL deterministic risk code only.

The verdict's plan (copied from the evidence scenarios, never typed by a model) becomes a hypothetical entry on a reference
PAPER account and goes through the same code a member's trade goes through: app.tradingos.risk.plan_trade (sizing from the
loss to the stop, leverage caps incl. HARD_MAX_LEVERAGE, position / daily-loss limits, entry zone, liquidation vs stop,
Hyperliquid's minimum), risk.portfolio_checks (spread, slippage, exposure, correlation, margin, costs, 7-day drawdown, stop
distance; ENFORCED here even when the owner runs them advisory for members) and the Kiwi Risk Team desks
(app.tradingos.riskteam). Kill switches: the Trading OS global pause (TsSettings.paused, "trading is paused for everyone")
and the reference account's own STOP NEW TRADES.

Liquidity and instrument restrictions (always checked, before and independent of the sizing; a failure REJECTS):
  - the live signal path's liquidity rule (app.tradingos.signals.analyze, same settings and constants): 24h volume >= TsSettings
    min_volume_usd, the larger of the impact spread and the book spread <= TsSettings max_spread_bps, and >= signals.MIN_DEPTH_USD
    within 0.5% on each side of the book when the evidence carries the book. Volume missing from the evidence: REJECTED (liquidity
    can't be shown). Spread / depth missing: reported "unknown" (never blocks on its own, as risk.portfolio_checks does).
  - instrument restrictions: the owner's allowed list (strategies["committee"]["allowed_coins"], else TsSettings.allowed_coins;
    empty = any), the committee's blocked list (strategies["committee"]["blocked_coins"]), a delisted flag, an isolated-only
    market vs the reference account's margin mode (isolated, as live execution uses), and the market's max leverage (leverage cap).
  - the reference account's daily trade count (risk.HARD_MAX_TRADES_PER_DAY), daily loss limit and 7-day drawdown, from the
    committee PAPER book (app.committee.paper), whose equity is also the reference equity unless the owner fixes one.

Read-only: no order, no intent, no position, no member account is created or changed; ts_settings is read, never created.
The AI cannot override anything here: the gate never reads model text, only (action, plan, evidence). WAIT → NOT_APPLICABLE
("no trade proposed"); a market that isn't a Hyperliquid perpetual → NOT_APPLICABLE (informational research only).

Reference account (owner-tunable: TsSettings.strategies["committee"]["reference"]): REFERENCE below, the defaults of a new
member trading account with risk per trade = risk_pct of equity.

Lot size: Hyperliquid's szDecimals from the evidence (fundamental facts), else from the context, else from Hyperliquid's
market list (the shared, cached market.data() snapshot). None known -> REJECTED with that reason (never a guessed default: a
wrong lot size rounds an order to zero or oversizes it).

Context (GateContext): everything the gate reads besides the evidence - the moment (as_of), a settings snapshot, the reference
account, the paper book's state and the Risk Team's view of the kitchen signals. Live (ctx omitted): context(db) reads them all
from the database now, exactly as before. A backtest passes its own point-in-time context (its simulated book at the decision
time) and the gate then never touches a database.
"""

import types
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.committee import paper
from app.db.base import utcnow
from app.models.tradingos import TsSettings
from app.tradingos import risk, riskteam, signals

NOTE = "Research only — nothing was traded."
REFERENCE: dict[str, Any] = {"equity": 10000.0, "risk_pct": 0.5, "leverage": 3, "max_leverage": 3, "max_allocation_pct": 10.0,
                             "max_positions": 2, "daily_loss_limit_usd": 150.0, "paused": False, "hold_h": 24.0,
                             "margin_mode": "isolated"}


def _settings(db: Session) -> TsSettings | None:
    return db.scalar(select(TsSettings).limit(1))


def snapshot(s: TsSettings | None) -> Any:
    """A detached copy of the settings row (attribute access like the row), or None."""
    if s is None:
        return None
    return types.SimpleNamespace(**{c.name: getattr(s, c.name) for c in TsSettings.__table__.columns})


def reference_of(s: Any, equity: float, paper_risk_pct: float) -> dict[str, Any]:
    """The reference account from a settings row / snapshot; `equity` and `paper_risk_pct` are the paper book's (used unless the
    owner fixed them in strategies["committee"]["reference"])."""
    over = (((s.strategies or {}) if s else {}).get("committee") or {}).get("reference") or {}
    out = {**REFERENCE, **{k: type(REFERENCE[k])(v) for k, v in over.items() if k in REFERENCE}}
    if "equity" not in over:  # the paper book's own equity (starting equity + realised paper P&L)
        out["equity"] = float(equity)
        if "risk_pct" not in over:
            out["risk_pct"] = float(paper_risk_pct)
    return out


def reference(db: Session) -> dict[str, Any]:
    s = _settings(db)
    over = (((s.strategies or {}) if s else {}).get("committee") or {}).get("reference") or {}
    if "equity" in over and "risk_pct" in over:
        return reference_of(s, 0.0, 0.0)
    pc = paper.config(db)
    return reference_of(s, paper.equity(db, pc) if "equity" not in over else 0.0, pc["paper_risk_pct"])


@dataclass
class GateContext:
    """What the gate reads besides the evidence. book = {"open", "day_pnl", "week_pnl", "exposure", "trades_today"}, the shape
    of paper.book_state."""

    as_of: datetime
    settings: Any  # TsSettings row or snapshot(); None = the column defaults
    reference: dict[str, Any]
    book: dict[str, Any]
    team: Any = None  # riskteam.Context; None = no kitchen signals known at as_of
    lot_sizes: dict[str, int] = field(default_factory=dict)  # coin -> szDecimals, used when the evidence lacks it

    def __post_init__(self) -> None:
        if self.team is None:
            self.team = riskteam.Context(self.as_of)


def context(db: Session, as_of: datetime | None = None) -> GateContext:
    """The live context: settings, reference account, paper book and Risk Team view, read from the database at `as_of` (now)."""
    now = as_of or utcnow()
    return GateContext(now, _settings(db), reference(db), paper.book_state(db, now), riskteam.context(db, now))


def lot_size(coin: str, fund: dict[str, Any], ctx: GateContext) -> tuple[int | None, str]:
    """(szDecimals, where it came from): the evidence, the context, Hyperliquid's market list - or (None, why not)."""
    v = fund.get("sz_decimals")
    if isinstance(v, int) and not isinstance(v, bool):
        return v, "evidence"
    if coin.upper() in ctx.lot_sizes:
        return int(ctx.lot_sizes[coin.upper()]), "context"
    from app.tradingos import market

    try:
        _, ms = market.data().markets()
    except Exception as e:  # noqa: BLE001 - unreadable: unknown, the trade is refused
        return None, f"not in the evidence and Hyperliquid's market list is unreadable ({type(e).__name__})"
    m = ms.get(coin.upper())
    if m is None:
        return None, f"not in the evidence and {coin.upper()} isn't in Hyperliquid's market list"
    return int(m.sz_decimals), "Hyperliquid market list"


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


def _coins(v: Any) -> list[str]:
    if isinstance(v, str):
        v = v.split(",")
    return [str(c).strip().upper() for c in (v or []) if str(c).strip()]


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _liquidity(s: TsSettings | None, fund: dict[str, Any]) -> list[dict[str, Any]]:
    """signals.analyze's liquidity rule on the evidence's numbers (same settings, same constants)."""
    min_vol = float(s.min_volume_usd) if s else 50_000_000.0
    max_sp = float(s.max_spread_bps) if s else 8.0
    out = []
    vol = _num(fund.get("volume_24h_usd"))
    out.append(_check("24h volume at least the minimum", vol is not None and vol >= min_vol,
                      f"${vol / 1e6:,.0f}M" if vol is not None else "unknown (not in the evidence)", f"${min_vol / 1e6:,.0f}M (signal liquidity rule)",
                      why=f"liquidity: 24h volume ${vol / 1e6:,.0f}M below the ${min_vol / 1e6:,.0f}M minimum" if vol is not None
                      else "liquidity: 24h volume unknown, the volume minimum can't be shown"))
    book = fund.get("book") if isinstance(fund.get("book"), dict) else {}
    spreads = [x for x in (_num(fund.get("spread_bps")), _num(fund.get("impact_spread_bps")), _num(book.get("spread_bps"))) if x is not None]
    if spreads:
        sp = max(spreads)
        out.append(_check("spread at most the maximum", sp <= max_sp, f"{sp:.1f} bps", f"{max_sp:g} bps (signal liquidity rule)",
                          why=f"liquidity: spread {sp:.1f} bps above the {max_sp:g} bps maximum"))
    else:
        out.append(_check("spread at most the maximum", True, "unknown: no spread in the evidence (never blocks)", f"{max_sp:g} bps"))
    bid = _num(book.get("bid_depth_usd", fund.get("depth_bid_usd")))
    ask = _num(book.get("ask_depth_usd", fund.get("depth_ask_usd")))
    if bid is not None and ask is not None:
        d = min(bid, ask)
        out.append(_check("depth within 0.5% each side", d >= signals.MIN_DEPTH_USD, f"${d / 1e3:,.0f}k on the thinner side",
                          f"${signals.MIN_DEPTH_USD / 1e3:,.0f}k (signal liquidity rule)",
                          why=f"liquidity: thin order book (${d / 1e3:,.0f}k within 0.5% on the thinner side, minimum "
                              f"${signals.MIN_DEPTH_USD / 1e3:,.0f}k)"))
    else:
        out.append(_check("depth within 0.5% each side", True, "unknown: no order book in the evidence (never blocks)",
                          f"${signals.MIN_DEPTH_USD / 1e3:,.0f}k"))
    return out


def _instrument(s: TsSettings | None, coin: str, asset: dict[str, Any], fund: dict[str, Any], ref: dict[str, Any]) -> list[dict[str, Any]]:
    c = (((s.strategies or {}) if s else {}).get("committee") or {})
    allowed = _coins(c["allowed_coins"]) if "allowed_coins" in c else _coins(s.allowed_coins if s else "")
    blocked = _coins(c.get("blocked_coins"))
    cu = coin.upper()
    delisted = bool(fund.get("delisted") or fund.get("is_delisted") or asset.get("delisted") or asset.get("is_delisted"))
    iso_only = fund.get("only_isolated")
    return [
        _check("coin allowed by the owner", not allowed or cu in allowed, cu, ", ".join(allowed) if allowed else "any",
               why=f"{cu} isn't in the owner's allowed coins"),
        _check("coin not blocked by the owner", cu not in blocked, cu, ", ".join(blocked) if blocked else "none blocked",
               why=f"{cu} is on the owner's blocked list"),
        _check("market listed (not delisted)", not delisted, "delisted" if delisted else "listed", why=f"{cu} is delisted on Hyperliquid"),
        _check("margin mode allowed by the market", not (iso_only is True and ref["margin_mode"] != "isolated"),
               f"{ref['margin_mode']}" + ("" if iso_only is not None else " (isolated-only flag unknown)"),
               "isolated only" if iso_only else "cross or isolated", why=f"{cu} is isolated-only and the reference account uses cross margin"),
    ]


def _hl_perp(ev: dict[str, Any]) -> bool:
    a = ev.get("asset") or {}
    return a.get("venue") == "hyperliquid" and a.get("market_type") == "perp" and bool(a.get("hl_tradable", True))


def evaluate(db: Session | None, ev: dict[str, Any], action: str, plan: dict[str, Any] | None, *, stale_reason: str | None = None,
             scenarios: dict[str, dict[str, Any] | None] | None = None, ctx: GateContext | None = None) -> dict[str, Any]:
    """The execution block of a run. WAIT: NOT_APPLICABLE, and each evidence scenario (scenarios = {"LONG": plan, "SHORT":
    plan}) is still run through the engine so the run can say why no trade would be approved right now. `ctx` omitted: the
    live context(db), built once and only when a trade is evaluated; given: the database is never read."""
    box: list[GateContext] = [ctx] if ctx is not None else []

    def get_ctx() -> GateContext:
        if not box:
            if db is None:
                raise ValueError("the risk gate needs a database or a GateContext")
            box.append(context(db))
        return box[0]

    if not _hl_perp(ev):
        return _result("NOT_APPLICABLE", (["no trade proposed"] if action not in ("LONG", "SHORT") else [])
                       + ["execution only on Hyperliquid perpetuals"], [])
    if action not in ("LONG", "SHORT"):
        reasons, checks = ["no trade proposed"] + ([stale_reason] if stale_reason else []), []
        for side, p in (scenarios or {}).items():
            if not p:
                continue
            sub = _trade(get_ctx(), ev, side, p, None)
            checks += [{**c, "name": f"{side.lower()} scenario · {c['name']}"} for c in sub["checks"]]
            reasons.append(f"{side.lower()} scenario now: " + ("the risk engine would approve it" if sub["status"] == "APPROVED"
                                                               else "; ".join(sub["reasons"][:6])))
        return _result("NOT_APPLICABLE", reasons, checks)
    return _trade(get_ctx(), ev, action, plan, stale_reason)


def _trade(ctx: GateContext, ev: dict[str, Any], action: str, plan: dict[str, Any] | None, stale_reason: str | None) -> dict[str, Any]:
    asset, price = ev.get("asset") or {}, ev.get("price") or {}
    if plan is None:
        checks = [_check("stop-loss and targets from the evidence", False, why="no plan from the evidence scenarios")]
        return _result("REJECTED", _reasons(checks), checks)
    s, ref = ctx.settings, ctx.reference
    fund = (((ev.get("sections") or {}).get("fundamental") or {}).get("facts") or {})
    senti = (((ev.get("sections") or {}).get("sentiment") or {}).get("facts") or {})
    tech = (((ev.get("sections") or {}).get("technical") or {}))
    coin = asset.get("market_id") or asset.get("symbol") or ""
    mark = float(price.get("value") or 0)
    market_max = int(fund.get("max_leverage") or ref["max_leverage"])
    cap = min(int(ref["max_leverage"]), risk.HARD_MAX_LEVERAGE, market_max)
    checks = _instrument(s, coin, asset, fund, ref) + _liquidity(s, fund) + [
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
    sz, sz_from = lot_size(coin, fund, ctx)
    checks.append(_check("lot size known (szDecimals)", sz is not None, f"{sz} decimals ({sz_from})" if sz is not None else "unknown",
                         why=f"lot size (szDecimals) unknown: {sz_from}"))
    reasons = []
    if stale_reason:
        reasons.append(stale_reason)
    bk = ctx.book
    n_open, day_pnl, week_pnl, exposure = bk["open"], bk["day_pnl"], bk["week_pnl"], bk["exposure"]
    equity = float(ref["equity"])
    max_loss = round(equity * float(ref["risk_pct"]) / 100, 2)
    if sz is None:  # no sizing without the lot size: refused by the check above, nothing guessed
        reasons += [r for r in _reasons(checks) if r not in reasons]
        return _result("REJECTED", reasons, checks)
    p = risk.plan_trade(coin=coin, side=action, mark=mark, entry_zone=(plan["entry_zone"][0], plan["entry_zone"][1]), stop=float(plan["stop"]),
                        target=float(plan["targets"][0]), sig_lev_hi=int(ref["leverage"]), market_max_lev=market_max,
                        sz_decimals=sz, equity=equity, max_leverage=int(ref["max_leverage"]),
                        max_loss_usd=max_loss, max_allocation_pct=float(ref["max_allocation_pct"]), max_positions=int(ref["max_positions"]),
                        open_positions=n_open, daily_pnl=day_pnl, daily_loss_limit=float(ref["daily_loss_limit_usd"]), allowed=[],
                        paused=bool(ref["paused"]), global_paused=bool(s and s.paused), trades_today=int(bk.get("trades_today", 0)))
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
        rv = riskteam.review(ctx.team, rcfg, coin, action, float(rng or 0), float(fr["atr14"]) if rng and fr.get("atr14") else None)
        for v in rv["votes"]:
            checks.append(_check(f"risk team: {v['label']}", v["vote"] != "veto", f"{v['vote']}: {v['detail']}", why=f"{v['label']}: {v['detail']}"))
    ok = all(c["ok"] for c in checks)
    reasons += [r for r in _reasons(checks) if r not in reasons]
    out = _result("APPROVED" if ok else "REJECTED", [] if ok else reasons, checks)
    if ok:  # the engine's sizing, which the paper book trades (never a number a model wrote)
        out["sizing"] = {"entry": p.entry, "size": p.size, "notional": p.notional, "leverage": p.leverage, "margin": p.margin,
                         "max_loss_usd": p.max_loss_usd, "equity": round(equity, 2), "risk_pct": float(ref["risk_pct"])}
    return out
