"""Risk Engine: position size from the loss to the stop (never from margin), within the owner's limits and hard caps.
Every refusal says why. Never sizes up to recover losses: limits only ever shrink a trade."""

import math
from dataclasses import asdict, dataclass, field
from typing import Any

HARD_MAX_LEVERAGE = 5  # system cap whatever the user sets (3x by default for the first live rollout)
HARD_MAX_POSITIONS = 3
HARD_MAX_TRADES_PER_DAY = 12  # new entries per account per UTC day, whatever the member sets
MIN_NOTIONAL = 10.0  # Hyperliquid's minimum order value (USD)
TAKER_FEE = 0.00045
SLIPPAGE = 0.0008  # assumed for market entries/exits; the entry order's price limit is set from this


@dataclass
class Plan:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    coin: str = ""
    side: str = ""
    entry: float = 0.0
    stop: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    size: float = 0.0
    leverage: int = 1
    notional: float = 0.0
    margin: float = 0.0
    max_loss_usd: float = 0.0  # to the stop, including fees and slippage
    est_fees: float = 0.0
    note: str = ""  # e.g. "raised to Hyperliquid's $10 minimum" (shown in the preview)
    checks: list[dict[str, Any]] = field(default_factory=list)  # portfolio_checks() results (Engine v2)
    risk_mode: str = ""  # "advisory" | "enforce" for those checks

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def tp2_of(entry: float, target: float) -> float:
    """TP2 = 1.5x the TP1 distance (TP1 is the signal's target)."""
    return entry + (target - entry) * 1.5


def round_size(size: float, sz_decimals: int) -> float:
    q = 10 ** sz_decimals
    return math.floor(size * q) / q


def round_px(px: float, sz_decimals: int) -> float:
    """Hyperliquid perp prices: at most 5 significant figures and at most (6 - szDecimals) decimals; integers always OK."""
    if px >= 100_000:
        return float(round(px))
    sig = float(f"{px:.5g}")
    return round(sig, max(0, 6 - sz_decimals))


def plan_trade(*, coin: str, side: str, mark: float, entry_zone: tuple[float, float], stop: float, target: float, sig_lev_hi: int,
               market_max_lev: int, sz_decimals: int, equity: float, max_leverage: int, max_loss_usd: float, max_allocation_pct: float,
               max_positions: int, open_positions: int, daily_pnl: float, daily_loss_limit: float, allowed: list[str], paused: bool,
               global_paused: bool, min_bump: bool = False, zone_tolerance: float = 0.25, trades_today: int = 0,
               max_trades_today: int = HARD_MAX_TRADES_PER_DAY, cooldown_left_min: float = 0.0) -> Plan:
    """min_bump (default limits only): when the limits size an order under Hyperliquid's $10 minimum, raise it to just above the
    minimum IF the loss at the stop stays within max_loss_usd and the margin fits in the account. Never more than that."""
    p = Plan(ok=False, coin=coin, side=side)
    long = side == "LONG"
    if global_paused:
        p.reasons.append("trading is paused for everyone")
    if paused:
        p.reasons.append("your new trades are stopped (STOP NEW TRADES is on)")
    if allowed and coin.upper() not in allowed:
        p.reasons.append(f"{coin} isn't in your allowed contracts")
    if open_positions >= min(max_positions, HARD_MAX_POSITIONS):
        p.reasons.append(f"you already have {open_positions} bot positions open (limit {min(max_positions, HARD_MAX_POSITIONS)})")
    if daily_pnl <= -abs(daily_loss_limit):
        p.reasons.append(f"daily loss limit reached (${daily_pnl:,.2f} today)")
    cap = min(max_trades_today, HARD_MAX_TRADES_PER_DAY)
    if trades_today >= cap:
        p.reasons.append(f"{trades_today} bot trades opened today (limit {cap} a day)")
    if cooldown_left_min > 0:
        p.reasons.append(f"cooling down on {coin}: {cooldown_left_min:.0f} min left since your last {coin} trade closed")
    lo, hi = min(entry_zone), max(entry_zone)
    tol = (hi - lo) * zone_tolerance  # how far outside the entry zone a market entry is still accepted (in zone widths)
    if not lo - tol <= mark <= hi + tol:
        p.reasons.append(f"price {mark:g} is outside the entry zone")
    if equity <= 0:
        p.reasons.append("no equity in the account")
    if (long and stop >= mark) or (not long and stop <= mark):
        p.reasons.append("the stop is on the wrong side of the price")
    elif (long and target <= mark) or (not long and target >= mark):  # its take-profit would trigger at once
        p.reasons.append("the price already passed the target")
    if p.reasons:
        return p
    entry = mark
    per_unit = abs(entry - stop) + entry * (2 * TAKER_FEE + 2 * SLIPPAGE)
    lev = max(1, min(max_leverage, sig_lev_hi, market_max_lev, HARD_MAX_LEVERAGE))
    size = round_size(max_loss_usd / per_unit, sz_decimals)
    cap = equity * max_allocation_pct / 100 * lev / entry  # the most size the margin allocation allows
    if size > cap:
        size = round_size(cap, sz_decimals)
    notional = size * entry
    if notional < MIN_NOTIONAL and min_bump:
        q = 10 ** sz_decimals
        bumped = math.ceil(MIN_NOTIONAL * 1.02 / entry * q) / q  # a hair above the minimum, so rounding can't push it under
        margin_needed = bumped * entry / lev
        if bumped * per_unit <= max_loss_usd and margin_needed <= equity * 0.9:
            p.note = (f"Raised to Hyperliquid's ${MIN_NOTIONAL:.0f} minimum (your default limits gave ${notional:,.2f}); "
                      f"margin ${margin_needed:,.2f}, loss at the stop ${bumped * per_unit:,.2f}.")
            size, notional = bumped, bumped * entry
    if notional < MIN_NOTIONAL:
        p.reasons.append(f"at your limits the order is ${notional:,.2f}, under Hyperliquid's ${MIN_NOTIONAL:.0f} minimum"
                         + (" (even the minimum wouldn't fit your balance or max loss)" if min_bump else ""))
        return p
    mm = 1 / (2 * market_max_lev)  # maintenance margin estimate
    liq_dist = 1 / lev - mm
    if abs(entry - stop) / entry > 0.8 * liq_dist:
        p.reasons.append(f"at {lev}x the liquidation price would be too close to the stop")
        return p
    p.ok, p.entry, p.size, p.leverage, p.notional, p.margin = True, entry, size, lev, round(notional, 2), round(notional / lev, 2)
    p.stop, p.tp1, p.tp2 = stop, target, tp2_of(entry, target)
    p.max_loss_usd = round(size * per_unit, 2)
    p.est_fees = round(notional * 2 * TAKER_FEE, 4)
    return p


# ---------------------------------------------------------------- portfolio checks (Kiwi Trading Engine v2)
# Run after plan_trade() approved a sized plan. Each check is deterministic and reports its measured value and limit.
# "advisory" (the default, so existing accounts trade exactly as before): a failed check is recorded on the preview and the
# member's Kiwi Brain as "would block"; "enforce" (owner switch strategies["risk_v2"]["mode"]): a failed check refuses the
# entry. An unknown reading (e.g. no order book) is reported as unknown and never blocks on its own.
V2_DEFAULTS: dict[str, Any] = {"mode": "advisory", "max_spread_bps": 8.0, "max_slippage_bps": 15.0, "max_exposure_x": 5.0,
                               "max_same_side": 2, "max_cost_r": 0.35, "max_week_loss_pct": 10.0, "min_stop_pct": 0.15}
V2_LABELS = {"spread": "Spread", "slippage": "Estimated slippage", "exposure": "Account exposure", "correlation": "Correlated positions",
             "margin": "Available margin", "costs": "Fees, slippage & funding", "drawdown": "7-day loss limit", "stop": "Stop-loss distance"}


def v2_settings(strategies: dict[str, Any] | None) -> dict[str, Any]:
    over = (strategies or {}).get("risk_v2") or {}
    cfg = {**V2_DEFAULTS, **{k: v for k, v in over.items() if k in V2_DEFAULTS}}
    cfg["mode"] = "enforce" if cfg["mode"] == "enforce" else "advisory"
    return cfg


def book_slippage_bps(levels: dict[str, list[tuple[float, float]]] | None, side: str, notional: float) -> float | None:
    """The average fill price of a market order of `notional` USD walking the book, vs the mid, in bps. None = no book;
    inf = the visible book is thinner than the order."""
    if not levels or not levels.get("bids") or not levels.get("asks"):
        return None
    mid = (levels["bids"][0][0] + levels["asks"][0][0]) / 2
    left, cost, qty = notional, 0.0, 0.0
    for px, sz in levels["asks"] if side == "LONG" else levels["bids"]:
        take = min(left, px * sz)
        cost, qty, left = cost + take, qty + take / px, left - take
        if left <= 1e-9:
            break
    if left > 1e-9 or qty <= 0:
        return math.inf
    return abs(cost / qty - mid) / mid * 10_000


def portfolio_checks(cfg: dict[str, Any], *, side: str, entry: float, stop: float, notional: float, margin: float, equity: float,
                     open_positions: list[dict[str, Any]], spread_bps: float | None, levels: dict[str, list[tuple[float, float]]] | None,
                     funding_h: float, hold_h: float, week_pnl: float) -> list[dict[str, Any]]:
    """open_positions: [{"side", "notional", "margin"}] of the account's open bot positions."""
    out: list[dict[str, Any]] = []

    def add(cid: str, ok: bool | None, detail: str) -> None:
        out.append({"id": cid, "label": V2_LABELS[cid], "status": "unknown" if ok is None else "pass" if ok else "fail", "detail": detail})

    if spread_bps is None:
        add("spread", None, "no spread reading from Hyperliquid")
    else:
        add("spread", spread_bps <= cfg["max_spread_bps"], f"{spread_bps:.1f} bps (max {cfg['max_spread_bps']:g})")
    sl = book_slippage_bps(levels, side, notional)
    if sl is None:
        add("slippage", None, "order book unavailable")
    elif math.isinf(sl):
        add("slippage", False, f"the visible book is thinner than ${notional:,.0f}")
    else:
        add("slippage", sl <= cfg["max_slippage_bps"], f"{sl:.1f} bps for ${notional:,.0f} (max {cfg['max_slippage_bps']:g})")
    total = notional + sum(p["notional"] for p in open_positions)
    x = total / equity if equity > 0 else math.inf
    add("exposure", x <= cfg["max_exposure_x"], f"{x:.2f}x equity with this trade (max {cfg['max_exposure_x']:g}x)")
    same = sum(1 for p in open_positions if p["side"] == side)
    add("correlation", same < cfg["max_same_side"],
        f"{same} open {side.lower()} position{'s' if same != 1 else ''} already (crypto moves together; max {cfg['max_same_side']} on one side)")
    free = equity - sum(p["margin"] for p in open_positions)
    add("margin", margin <= free * 0.95, f"needs ${margin:,.2f}, about ${free:,.2f} free")
    risk_px = abs(entry - stop)
    pays = max(0.0, funding_h if side == "LONG" else -funding_h)
    used_sl = (sl if sl is not None and not math.isinf(sl) else SLIPPAGE * 10_000) / 10_000
    cost = (2 * TAKER_FEE + 2 * used_sl + pays * hold_h) * entry / risk_px if risk_px > 0 else math.inf
    add("costs", cost <= cfg["max_cost_r"], f"{cost:.2f}R round trip incl. {pays * 100:.4f}%/h funding over {hold_h:g} h (max {cfg['max_cost_r']:g}R)")
    lim = equity * cfg["max_week_loss_pct"] / 100
    add("drawdown", week_pnl > -lim, f"${week_pnl:,.2f} realized in 7 days (stop at -${lim:,.2f}, {cfg['max_week_loss_pct']:g}% of equity)")
    dist = risk_px / entry * 100 if entry else 0
    add("stop", dist >= cfg["min_stop_pct"], f"stop {dist:.2f}% away (min {cfg['min_stop_pct']:g}%: tighter is market noise)")
    return out
