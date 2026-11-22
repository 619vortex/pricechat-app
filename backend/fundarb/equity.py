"""Module 4: the equity bucket. One strategy only: an index perp against a 2-3 name basket of its heaviest constituents,
traded as relative value on the z-scored spread. Never a funding trade, never paired with crypto, never ranked in the same
table as crypto (rule 2). Read-only: nothing here can place an order.

Universe: INDEX markets on the builder dexes (Module 1 classification) against the EQUITY markets that passed every Module 2
gate. No index, constituent or weight is written in code:
- basket: the operator's published weights for that index (equity_operator.json index_baskets) pick the heaviest names
  present in the gated pool; without them the basket is chosen statistically (forward selection of up to `max_names`
  equities that best explain the index's hourly returns) and labelled "statistical" in every row.
- hedge ratios: OLS of the index's hourly log returns on the basket names' returns over `fit_days` (non-negative): $beta_i
  of name i per $1 of index. spread_t = log I_t - sum beta_i log P_i,t; z = (spread_now - mean) / sd over the trailing
  `z_window_hours`; AR(1) half-life on the same window.
- signal: |z| >= entry_z -> trade toward the mean (z > 0: short index / long basket); |z| <= exit_z -> exit zone; else
  hold zone (no new entry). Exit at |z| < exit_z or `time_stop_hours`.

Entry gates (every one stored with the decision; a blocked signal is still shown, labelled):
- rule 5 event calendar (fundarb.events): unknown or an event in the window -> blocked
- rule 4 oracle policy for the current session (fundarb.oracle_study's measured policy; without a study, only regular
  hours are trusted and that is labelled an assumption): "blocked" sessions block entries and flag funding unreliable;
  the spread stop is widened by the policy's stop_mult
- rule 6 funding at the cap (FUNDARB_FUNDING_CAP_HOURLY x FUNDARB_FUNDING_CAP_SHARE) on any leg: do not add, half size,
  stop tightened to 0.75x
- a spread that is not mean-reverting over the window (AR(1) slope >= 0)
Rule 7: builder dexes publish no predicted funding: the forward estimate is an EWMA of the last settled hourly rates, one
interval behind, labelled "estimate". Rule 8: max leverage is read live and used only for margin and liquidation
distance; size comes from the risk budget (loss at the spread stop = FUNDARB_EQUITY_RISK_BUDGET_USD).
Capital (rule 2): every leg is its own isolated bucket: capital = sum_i |notional_i| / L_i x (1 + buffer), L_i =
min(leverage, the leg's live max leverage). capital_apr is a model estimate (reversion to exit_z at the AR(1) half-life, net
of round-trip fees + half-spread and the funding carry over that hold), not a backtest."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from fundarb.candles import hourly_closes
from fundarb.events import calendar_status
from fundarb.models import HOURS_PER_YEAR
from fundarb.oracle_study import leg_policy
from fundarb.universe import AssetClass, Market
from fundarb.venues import HyperliquidVenue


@dataclass(frozen=True)
class IndexConfig:
    fit_days: float = 30.0
    min_overlap_hours: int = 240
    max_names: int = 3
    min_r2: float = 0.5  # the basket must explain at least this much of the index's hourly variance
    min_r2_gain: float = 0.02  # forward selection stops when another name adds less
    z_window_hours: int = 168
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    time_stop_hours: float = 72.0
    leverage: float = 2.0  # prices margin only; capped per leg by its live max leverage
    buffer: float = 0.25
    risk_budget_usd: float = 1000.0
    funding_cap_hourly: float = 0.04
    funding_cap_share: float = 0.9
    funding_est_hours: int = 24
    funding_est_halflife_hours: float = 6.0
    event_buffer_hours: float = 24.0
    top_n: int = 20

    @classmethod
    def from_settings(cls, s: Any, **kw: Any) -> IndexConfig:
        return cls(buffer=s.capital_buffer, risk_budget_usd=s.equity_risk_budget_usd, funding_cap_hourly=s.funding_cap_hourly,
                   funding_cap_share=s.funding_cap_share, event_buffer_hours=s.equity_event_buffer_hours, **kw)


# --- pure maths ---------------------------------------------------------------------------------------------------------

def _aligned(closes: dict[str, dict[int, float]], names: list[str]) -> tuple[list[int], dict[str, np.ndarray]]:
    hours = sorted(set.intersection(*(set(closes[n]) for n in names)))
    return hours, {n: np.log(np.array([closes[n][h] for h in hours])) for n in names}


def ols(y: np.ndarray, xs: list[np.ndarray]) -> tuple[np.ndarray, float]:
    """y ~ a + sum b_i x_i -> (b, R^2)."""
    X = np.column_stack([np.ones(len(y)), *xs])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    tss = float(((y - y.mean()) ** 2).sum())
    return coef[1:], (1 - float((resid ** 2).sum()) / tss) if tss > 0 else 0.0


def select_basket(index: str, closes: dict[str, dict[int, float]], pool: list[str], cfg: IndexConfig,
                  weights: dict[str, float] | None = None) -> dict[str, Any]:
    """Pure. Operator weights -> the heaviest `max_names` of them in the pool; else forward selection on hourly returns."""
    usable = [n for n in pool if n != index and n in closes and len(set(closes[n]) & set(closes.get(index, {}))) >= cfg.min_overlap_hours]
    if weights:
        names = sorted((n for n in weights if n in usable), key=lambda n: -weights[n])[: cfg.max_names]
        missing = [n for n in sorted(weights, key=lambda n: -weights[n])[: cfg.max_names] if n not in usable]
        return {"names": names, "source": "operator weights", "steps": [], "missing_heaviest": missing}
    chosen: list[str] = []
    steps: list[dict[str, Any]] = []
    best_r2 = 0.0
    while len(chosen) < cfg.max_names:
        best: tuple[float, str] | None = None
        for c in usable:
            if c in chosen:
                continue
            hours, lp = _aligned(closes, [index, *chosen, c])
            if len(hours) < cfg.min_overlap_hours:
                continue
            b, r2 = ols(np.diff(lp[index]), [np.diff(lp[n]) for n in [*chosen, c]])
            if (b > 0).all() and (best is None or r2 > best[0]):
                best = (r2, c)
        if best is None or best[0] - best_r2 < cfg.min_r2_gain:
            break
        best_r2 = best[0]
        chosen.append(best[1])
        steps.append({"name": best[1], "r2": round(best_r2, 4)})
    return {"names": chosen, "source": "statistical: forward selection on hourly returns (not published index weights)", "steps": steps,
            "missing_heaviest": []}


def half_life(s: np.ndarray) -> float | None:
    """AR(1) on the spread: ds_t = a + b s_{t-1}; half-life = -ln 2 / ln(1 + b) hours for -1 < b < 0, else None."""
    if len(s) < 10:
        return None
    b, _ = ols(np.diff(s), [s[:-1]])
    return -math.log(2) / math.log(1 + float(b[0])) if -1 < float(b[0]) < 0 else None


def ewma(xs: list[float], halflife: float) -> float | None:
    if not xs:
        return None
    w = [0.5 ** ((len(xs) - 1 - i) / halflife) for i in range(len(xs))]
    return sum(a * b for a, b in zip(xs, w)) / sum(w)


def index_metrics(x: dict[str, Any], cfg: IndexConfig) -> dict[str, Any]:
    """Every number for one index market from its stored inputs. Pure: a stored run replays exactly."""
    out: dict[str, Any] = {"status": "rejected", "reason": x.get("rejected"), "session": x["session"], "basket_source": x.get("basket_source")}
    if x.get("rejected"):
        return out
    idx, names = x["index"], x["basket"]
    hours = x["hours"]
    lp = {n: np.array(v) for n, v in x["log_px"].items()}
    if len(hours) < max(cfg.min_overlap_hours, cfg.z_window_hours + 2):
        return {**out, "reason": f"only {len(hours)} shared hours (need {max(cfg.min_overlap_hours, cfg.z_window_hours + 2)})"}
    beta, r2 = ols(np.diff(lp[idx]), [np.diff(lp[n]) for n in names])
    out.update({"r2": round(r2, 4), "betas": {n: round(float(b), 4) for n, b in zip(names, beta)}, "hours": len(hours)})
    if r2 < cfg.min_r2 or not (beta > 0).all():
        return {**out, "reason": f"basket explains R2 {r2:.2f} of the index (min {cfg.min_r2}) or a hedge ratio <= 0: no constituent hedge"}
    spread = lp[idx] - sum(float(b) * lp[n] for n, b in zip(names, beta))
    win = spread[-(cfg.z_window_hours + 1):-1]
    mean, sd = float(win.mean()), float(win.std(ddof=1))
    z = (float(spread[-1]) - mean) / sd if sd > 0 else 0.0
    hl = half_life(spread[-(cfg.z_window_hours + 1):])
    out.update({"spread_now": round(float(spread[-1]), 6), "spread_mean": round(mean, 6), "spread_sd_bps": round(sd * 1e4, 2), "z": round(z, 3),
                "half_life_h": round(hl, 1) if hl else None})
    if abs(z) >= cfg.entry_z:
        sig = "short index / long basket" if z > 0 else "long index / short basket"
    elif abs(z) <= cfg.exit_z:
        sig = "exit zone"
    else:
        sig = "hold zone (no new entry)"
    out["signal"] = sig
    sides = {idx: -1 if z > 0 else 1, **{n: 1 if z > 0 else -1 for n in names}}  # +1 long, -1 short
    weights = {idx: 1.0, **{n: float(b) for n, b in zip(names, beta)}}  # $ per $1 of index notional

    flags: list[str] = []
    blockers: list[str] = []
    pol = leg_policy(x["policy"], x["session"], [idx, *names])
    cite = (x["policy"].get("citation") or {}).get("study_id") or "no study (assumption)"
    funding: dict[str, Any] = {}
    at_cap = False
    for n in [idx, *names]:
        leg = x["legs"][n]
        now_h = leg["funding_hourly_now"]
        est = ewma(leg["funding_recent_hourly"], cfg.funding_est_halflife_hours)
        cap_line = cfg.funding_cap_share * cfg.funding_cap_hourly
        capped = any(r is not None and abs(r) >= cap_line for r in [now_h, *leg["funding_recent_hourly"]])
        at_cap = at_cap or capped
        funding[n] = {"now_hourly": now_h, "estimate_hourly": est, "estimate": f"EWMA (half-life {cfg.funding_est_halflife_hours:g}h) of the last "
                      f"{len(leg['funding_recent_hourly'])} settled hourly rates: no predicted funding on builder dexes, one interval behind",
                      "at_cap": capped}
    if not pol["funding_reliable"]:
        flags.append(f"{x['session']}: premium-derived funding unreliable on {', '.join(pol['stale_legs'])} (oracle {pol['verdict']}, {cite})")
    if pol["entries"] == "blocked":
        blockers.append(f"session {x['session']}: oracle {pol['verdict']} ({cite}): entries wait for a tracked session")
    elif pol["entries"] == "allowed":
        flags.append(f"session {x['session']}: entries allowed, regular hours preferred")
    cal = calendar_status(x["calendar"], [idx, *names], x["ts"], cfg.event_buffer_hours)
    if cal["status"] != "clear":
        blockers.append(f"event calendar {cal['status']}: {cal['reason']}")
    if at_cap:
        blockers.append(f"funding at the cap (>= {cfg.funding_cap_share:.0%} of {cfg.funding_cap_hourly:.0%}/h) on a leg: a squeeze forming, do not add")
    if hl is None:
        blockers.append("spread not mean-reverting over the window (AR(1) slope >= 0)")
    stop_z = cfg.stop_z * pol["stop_mult"] * (0.75 if at_cap else 1.0)
    out.update({"funding": funding, "calendar": cal, "oracle_policy": {**pol, "citation": cite}, "stop_z": round(stop_z, 3)})

    # sizing (rule 8: from the risk budget; leverage only for margin and liquidation distance)
    z_in = max(abs(z), cfg.entry_z)
    stop_dist = max(stop_z - z_in, 0.5) * sd
    notional = cfg.risk_budget_usd / stop_dist * (0.5 if at_cap else 1.0) if stop_dist > 0 else 0.0
    legs_out, capital = {}, 0.0
    for n, w in weights.items():
        leg = x["legs"][n]
        lev = min(cfg.leverage, leg["max_leverage"])
        margin = w * notional / lev
        capital += margin * (1 + cfg.buffer)
        legs_out[n] = {"side": "long" if sides[n] > 0 else "short", "notional_usd": round(w * notional, 2), "leverage": lev,
                       "max_leverage": leg["max_leverage"], "margin_usd": round(margin, 2), "bucket": leg["capital_bucket"],
                       "liq_distance_pct": round((1 / lev - 1 / (2 * leg["max_leverage"])) * 100, 1)}
    cap_per = capital / notional if notional else None
    out.update({"sizing": {"index_notional_usd": round(notional, 2), "risk_budget_usd": cfg.risk_budget_usd, "stop_distance_bps": round(stop_dist * 1e4, 2),
                           "legs": legs_out, "capital_usd": round(capital, 2), "capital_per_index_notional": round(cap_per, 4) if cap_per else None,
                           "liq_note": "liquidation distance ~ 1/L - 1/(2 x max leverage): Hyperliquid's maintenance margin is half the "
                                       "initial margin at max leverage (native docs); assumed the same on builder dexes"}})
    cost = sum(w * 2 * ((x["legs"][n]["taker_bps"] or 0) + (x["legs"][n]["spread_bps"] or 0) / 2) for n, w in weights.items()) / 1e4
    fee_known = all(x["legs"][n]["taker_bps"] is not None for n in weights)
    if x.get("fee_note"):
        flags.append(x["fee_note"])
    if abs(z) >= cfg.entry_z and cap_per:
        hold = min(cfg.time_stop_hours, hl * math.log(abs(z) / cfg.exit_z) / math.log(2)) if hl else cfg.time_stop_hours
        carry_h = sum(-sides[n] * w * (funding[n]["estimate_hourly"] or 0) for n, w in weights.items())
        rev = (abs(z) - cfg.exit_z) * sd
        pnl = rev - cost + carry_h * hold
        out["expected"] = {"reversion_bps": round(rev * 1e4, 2), "cost_bps": round(cost * 1e4, 2), "carry_bps": round(carry_h * hold * 1e4, 2),
                           "hold_h": round(hold, 1), "net_bps": round(pnl * 1e4, 2), "fees_known": fee_known,
                           "capital_apr_pct": round(pnl / cap_per * HOURS_PER_YEAR / hold * 100, 2) if fee_known else None,
                           "model": "reversion to |z| = exit at the AR(1) half-life (time stop cap), minus round-trip fees + half-spread, "
                                    "plus funding carry at the estimate; a model, not a backtest"}
    else:
        out["expected"] = None
    out["history"] = episodes(spread, cfg, cost)
    if "statistical" in (x.get("basket_source") or ""):
        flags.append("statistical basket: names chosen by fit, not index membership (may include ETFs or non-constituents); "
                     "operator weights in equity_operator.json index_baskets make it a constituent basket")
    out["blockers"], out["flags"] = blockers, flags
    out["entry_allowed"] = abs(z) >= cfg.entry_z and not blockers
    out["status"] = "entry" if out["entry_allowed"] else "signal_blocked" if abs(z) >= cfg.entry_z else "no_signal"
    out["reason"] = None
    return out


def rank_equity(rows: list[dict[str, Any]], cfg: IndexConfig) -> list[dict[str, Any]]:
    """Equities ranked only against equities: entries, then blocked signals (by estimated capital APR), then no signal (by
    |z|), then rejected."""
    order = {"entry": 0, "signal_blocked": 1, "no_signal": 2, "rejected": 3}

    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        m = r["metrics"]
        apr = ((m.get("expected") or {}).get("capital_apr_pct"))
        return (order[m["status"]], -(apr if apr is not None else -1e9), -abs(m.get("z") or 0), r["index"])

    scored = [{**r, "metrics": index_metrics(r["inputs"], cfg)} for r in rows]
    return sorted(scored, key=key)[: cfg.top_n]


# --- data ---------------------------------------------------------------------------------------------------------------

async def gather_equity(settings: Any, markets: list[Market], gate: dict[str, dict[str, Any]], cfg: IndexConfig, operator: dict[str, Any],
                        policy: dict[str, Any], session: str, ts: str, transport: Any = None) -> dict[str, Any]:
    """Every index market's inputs: rejected ones with the reason, the rest with the basket's aligned price history,
    funding, leverage, fees and the policy/calendar in force (stored with the decision)."""
    hl = HyperliquidVenue(settings, transport)
    await hl.connect()
    try:
        by = {m.raw_name: m for m in markets}
        indexes = [m for m in markets if m.asset_class is AssetClass.INDEX and m.listing_live]
        pool = [m.raw_name for m in markets if m.asset_class is AssetClass.EQUITY and gate.get(m.raw_name, {}).get("passed")]
        user = settings.hl_account_address
        fees = await hl._info({"type": "userFees", "user": user or "0x" + "0" * 40})
        taker = float(fees["userCrossRate"] if user else fees["feeSchedule"]["cross"]) * 1e4
        fee_note = (f"taker {taker:.2f} bps from userFees {'(your tier)' if user else 'feeSchedule (base tier)'}: a lower bound on builder dexes, "
                    "whose deployer fee share is not modelled")
        live_idx = [m.raw_name for m in indexes if gate.get(m.raw_name, {}).get("passed")]
        closes: dict[str, dict[int, float]] = {}
        sem = asyncio.Semaphore(4)

        async def candle(n: str) -> None:
            async with sem:
                closes[n] = await hourly_closes(hl, n, cfg.fit_days)

        await asyncio.gather(*(candle(n) for n in [*live_idx, *pool]))
        rows: list[dict[str, Any]] = []
        for m in indexes:
            g = gate.get(m.raw_name, {})
            base = {"index": m.raw_name, "ts": ts, "session": session, "policy": policy, "calendar": operator["calendar"].to_dict(),
                    "pool_size": len(pool), "fee_note": fee_note}
            if not g.get("passed"):
                rows.append({"index": m.raw_name, "inputs": {**base, "rejected": f"gate {g.get('failed_gate')}: {g.get('reason', '')}".strip()}})
                continue
            ob = operator["index_baskets"].get(m.raw_name)
            sel = select_basket(m.raw_name, closes, pool, cfg, (ob or {}).get("weights"))
            if not sel["names"]:
                why = ("none of the operator's heaviest constituents passed the gates: " + ", ".join(sel["missing_heaviest"]) if ob
                       else f"no gated equity improves the fit by {cfg.min_r2_gain} R2 (pool {len(pool)})")
                rows.append({"index": m.raw_name, "inputs": {**base, "rejected": why, "basket_source": sel["source"]}})
                continue
            hours, lp = _aligned(closes, [m.raw_name, *sel["names"]])
            legs: dict[str, Any] = {}
            start = int(time.time() * 1000) - cfg.funding_est_hours * 3_600_000
            for n in [m.raw_name, *sel["names"]]:
                mk = by[n]
                hist = await hl._info({"type": "fundingHistory", "coin": n, "startTime": start})
                ih = mk.funding_interval_hours or 1.0
                spread_chk = next((c for c in g.get("checks", []) if c["gate"] == "spread"), None) if n == m.raw_name else \
                    next((c for c in gate[n].get("checks", []) if c["gate"] == "spread"), None)
                legs[n] = {"max_leverage": mk.max_leverage, "capital_bucket": mk.capital_bucket, "taker_bps": taker,
                           "spread_bps": spread_chk["value"] if spread_chk else mk.spread_bps, "interval_hours": mk.funding_interval_hours,
                           "funding_hourly_now": mk.funding_rate_hourly,
                           "funding_recent_hourly": [float(r["fundingRate"]) / ih for r in hist or []][-cfg.funding_est_hours:],
                           "premium_recent": [float(r["premium"]) for r in hist or [] if r.get("premium") is not None][-cfg.funding_est_hours:]}
            rows.append({"index": m.raw_name, "inputs": {**base, "basket": sel["names"], "basket_source": sel["source"], "selection": sel["steps"],
                                                         "missing_heaviest": sel["missing_heaviest"], "hours": hours,
                                                         "log_px": {k: [round(float(v), 8) for v in a] for k, a in lp.items()}, "legs": legs}})
        return {"rows": rows, "pool": pool, "calls": hl.calls}
    finally:
        await hl.disconnect()


def config_dict(cfg: IndexConfig) -> dict[str, Any]:
    return asdict(cfg)
