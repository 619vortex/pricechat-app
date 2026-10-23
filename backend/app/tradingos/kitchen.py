"""Kiwi Kitchen: the 15m / 30m intraday strategy (intraday_15m_v1) as a live state machine, one per market.

Every 45 s (job tradingos.kitchen) each kitchen market is evaluated with the SAME pure functions the backtest below uses
(intraday.series / regime / setups, plus confirm_30m), on CLOSED candles only:

  SEARCHING  no entry model fired on the last closed 15m candle (the reasons are the models' own)
  COOKING    a 15m candidate exists and its checks are being run. It only stays here while a check is genuinely pending:
             the 30m candle that confirms it hasn't closed yet.
  READY      every check passed: a signal is proposed (PENDING_REVIEW while the owner approves signals, WAITING when
             auto-publish is on). From there the existing pipeline takes over unchanged: the risk engine per member,
             ENTER / AUTO, exchange-side stop and take-profits, Telegram cards.
  BURNT      a check failed, or the proposal was cancelled or expired. The failing check is the reason.

Checks ("ingredients"), each pass / fail / pending with its measured value:
  setup        a 15m entry model fired (momentum, volume, structure and volatility are inside the models)
  confirm_30m  the 30m candle containing the setup candle agrees (trend models: close on the trend side of a rising /
               falling 30m EMA20; reversal models: the 30m candle closed in the trade's direction)
  liquidity    impact spread and 24h volume within the scanner's limits
  crowding     funding not heavily paying the trade's side (open interest is reported alongside)
  costs        fees + slippage + spread + funding over the max hold, in R, small enough, and TP1 still worth it after them
  quality      the interpretable 0-100 score from intraday.quality (NOT a probability of winning)
  ml           Kiwi ML (ml.py): a model trained on 2 years of 15m history estimates the chance this setup ends in profit after
               costs. It blocks a setup only while it filters (validated on its holdout, or the owner set "ml": "filter");
               otherwise it is advisory: the estimate is shown, the check is skipped
  risk_team    the Kiwi Risk Team (riskteam.py): correlation, loss-streak, daily-loss and volatility desks can veto; the
               edge desk warns. An AI Chief Risk Officer writes the memo afterwards (job tradingos.riskteam)
  capacity     trading not paused, room under the open-signal limit, no open proposal on this market, per-market cooldown

Owner switches (TsSettings.strategies JSON, Admin → Trading OS):
  intraday_15m_v1  on: READY creates signals. Off (the default): SHADOW. The kitchen still evaluates and streams, labelled
                   shadow, and proposes nothing.
  intraday_live    on: accounts that opted in may take 15m signals on TESTNET / LIVE. Off (the default): PAPER only.
  intraday         optional overrides of DEFAULTS (coins, models, thresholds). "mode": "degen" layers DEGEN on top: 8
                   markets, the F_scalp model, no 30m wait, at most 2 open, self-publishing, market entry (the tracker
                   opens it at the live price once its card is posted) and market exit at the max hold. "mode": "standard"
                   (Normal, the default since 2026-10-23: the owner wants less trading) is the original 15m + 30m rule set.
                   Switched in Admin → Trading OS → Settings → Trading mode. "ml": "auto" (default) | "filter" | "shadow" | "off" (see ml.py).

Every state change is one kb_events row (kind "strategy", source "kitchen", member_id NULL, so public). The Earn page
streams those rows and the Kiwi Brain timeline lists them: one record, two views.

Backtest: python -m app.tradingos.kitchen (cached Hyperliquid history, same honest rules as backtest.py / lab.py).
"""

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import KbEvent, TsKitchen, TsSignal
from app.tradingos import intraday as ix
from app.tradingos import decisions, ml, risk, riskteam, signals
from app.tradingos.indicators import main_three
from app.tradingos.market import INTERVALS, Market, MarketData, MarketError

logger = logging.getLogger(__name__)

STRATEGY = "intraday_15m_v1"
LABEL = "15m / 30m intraday"
SOURCE = "kitchen"
STATES = ("SEARCHING", "COOKING", "READY", "BURNT")
TF, CONFIRM_TF = "15m", "30m"
M15, M30 = INTERVALS["15m"] * 1000, INTERVALS["30m"] * 1000
EVERY_S = 45  # the tradingos.kitchen job's interval
SLIP = {"BTC": 0.0002, "ETH": 0.0002}  # per side, as in backtest.py; everything else 0.0005
DEFAULTS: dict[str, Any] = {"coins": ["BTC", "ETH", "SOL"], "models": list(ix.STRATEGIES), "min_quality": 55, "max_cost_r": 0.25,
                            "min_net_r": 0.8, "cooldown_min": 30, "max_funding_h": 0.0001, "min_stop_pct": ix.MIN_STOP_PCT,
                            "confirm_30m": True, "max_open": 0, "market_entry": False, "auto_publish": False, "ml": "auto"}
# DEGEN mode (the default 2026-10-18 → 2026-10-23, now opt-in from Admin): more markets, the momentum scalp, no 30m wait, at most 2 open at
# once, signals publish themselves and open / close at market. Backtest 2026-10-18 (46 days, these 8 markets, max 2 open):
# 1.6 trades a day, -0.07R a trade (90% CI -0.22R to +0.10R), so NOT a validated edge; standard mode on BTC/ETH/SOL made
# 0.16 a day at +0.43R. That's why 15m signals stay PAPER-only for members until the owner switches intraday_live on.
# Degen mode: strategies["intraday"]["mode"] = "degen"; anything else is standard (Normal).
DEGEN: dict[str, Any] = {"coins": ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "SUI", "LINK"],
                         "models": ["A_pullback", "B_retest", "C_sweep", "F_scalp"], "min_quality": 45, "max_cost_r": 0.25,
                         "min_net_r": 0.6, "min_stop_pct": 0.005, "cooldown_min": 15, "confirm_30m": False, "max_open": 2,
                         "market_entry": True, "auto_publish": True}
CHECKS = (("setup", "15m entry model"), ("confirm_30m", "30m confirmation"), ("liquidity", "Liquidity"),
          ("crowding", "Funding & open interest"), ("costs", "Costs vs risk"), ("quality", "Signal quality"), ("ml", "Kiwi ML"), ("risk_team", "AI Risk Team"),
          ("capacity", "Risk capacity"))
RISK_CHECKS = ("crowding", "costs", "risk_team", "capacity")


def slip(coin: str) -> float:
    return SLIP.get(coin.upper(), 0.0005)


def config(s: Any, mode: str | None = None) -> dict[str, Any]:
    """The kitchen's effective config. `mode` previews the other mode with the same overrides (Admin's mode switch)."""
    over = (s.strategies or {}).get("intraday") or {}
    mode = mode or ("degen" if over.get("mode") == "degen" else "standard")
    cfg = {**DEFAULTS, **(DEGEN if mode == "degen" else {}), **{k: v for k, v in over.items() if k in DEFAULTS}, "mode": mode}
    cfg["coins"] = [str(c).strip().upper() for c in (cfg["coins"] if isinstance(cfg["coins"], list) else str(cfg["coins"]).split(",")) if str(c).strip()]
    cfg["models"] = [m for m in cfg["models"] if m in ix.STRATEGIES]
    cfg["risk_desk"] = riskteam.settings(s)
    return cfg


MODE_RESEARCH = {  # the 2026-10-18 backtest quoted above (46 days, Hyperliquid 15m), shown next to the Admin mode switch
    "standard": {"trades_per_day": 0.16, "avg_r": 0.43, "note": "BTC/ETH/SOL, 46-day backtest"},
    "degen": {"trades_per_day": 1.6, "avg_r": -0.07, "note": "8 markets, 46-day backtest; 90% CI -0.22R to +0.10R: no proven edge"},
}


def modes(db: Session, s: Any, now: datetime | None = None) -> dict[str, Any]:
    """Admin's Normal / Degen switch: the current mode, what each mode does with the current overrides, the backtest behind
    it, and how many kitchen signals were actually opened in the last 24 h / 7 days."""
    now = now or utcnow()

    def opened(since: timedelta) -> int:
        return int(db.scalar(select(func.count()).select_from(TsSignal).where(
            TsSignal.strategy == STRATEGY, TsSignal.created_at >= now - since)) or 0)

    out: dict[str, Any] = {"current": config(s)["mode"], "publishing": publishing(s), "live": live_enabled(s),
                           "signals_24h": opened(timedelta(hours=24)), "signals_7d": opened(timedelta(days=7)), "modes": {}}
    for m in ("standard", "degen"):
        c = config(s, m)
        out["modes"][m] = {"coins": c["coins"], "models": c["models"], "max_open": int(c["max_open"] or 0) or s.max_open_signals,
                           "confirm_30m": bool(c["confirm_30m"]), "market_entry": bool(c["market_entry"]),
                           "auto_publish": bool(c["auto_publish"]) or bool(s.auto_publish), "cooldown_min": c["cooldown_min"],
                           "min_quality": c["min_quality"], "research": MODE_RESEARCH[m]}
    return out


def publishing(s: Any) -> bool:
    return bool((s.strategies or {}).get(STRATEGY))


def live_enabled(s: Any) -> bool:
    return bool((s.strategies or {}).get("intraday_live"))


# ---------------------------------------------------------------- pure evaluation (shared with the backtest)
def confirm_30m(f30: ix.Series, idx: int, side: str, model: str) -> tuple[bool, str]:
    """Does the closed 30m candle idx confirm a 15m setup? Trend models want the 30m trend; reversal models a 30m close
    in the trade's direction."""
    if idx < 22 or f30.e20[idx] is None or f30.e20[idx - 2] is None:
        return False, "not enough 30m history"
    c, o, e20, e20_prev = f30.c[idx], f30.o[idx], f30.e20[idx], f30.e20[idx - 2]
    assert e20 is not None and e20_prev is not None
    long = side == "LONG"
    if model in ("C_sweep", "E_range"):
        ok = c > o if long else c < o
        return ok, f"30m candle closed {'up' if c > o else 'down' if c < o else 'flat'} ({o:.6g} → {c:.6g})"
    rising = e20 >= e20_prev
    ok = (c > e20 and rising) if long else (c < e20 and not rising)
    return ok, f"30m close {c:.6g} {'above' if c > e20 else 'below'} EMA20 {e20:.6g}, EMA20 {'rising' if rising else 'falling'}"


def cost_r(ref: float, stop: float, coin: str, spread_bps: float, funding_h: float, side: str, hold_min: int) -> float:
    """Round-trip costs in R: taker fees and slippage both ways, half the spread both ways, and the funding the side
    would pay over the max hold (funding received is not counted as a gain)."""
    risk_px = abs(ref - stop)
    if risk_px <= 0:
        return 99.0
    pays = max(0.0, funding_h if side == "LONG" else -funding_h)
    frac = 2 * risk.TAKER_FEE + 2 * slip(coin) + spread_bps / 10_000 + pays * hold_min / 60
    return frac * ref / risk_px


def _fmt_t(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M UTC")


@dataclass
class Evaluation:
    coin: str
    state: str
    candle_t: int | None
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    regime: str | None = None
    market: dict[str, Any] = field(default_factory=dict)
    indicators: dict[str, Any] = field(default_factory=dict)
    setup: dict[str, Any] | None = None
    strength: int | None = None
    risk_team: dict[str, Any] | None = None

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c["status"] == "pass")


def _checks(**given: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"id": k, "label": label, "status": given.get(k, ("skip", ""))[0], "detail": given.get(k, ("skip", ""))[1]} for k, label in CHECKS]


def evaluate(coin: str, f15: ix.Series, f30: ix.Series, h1: ix.Series, h4: ix.Series, mkt: Market | None, now_ms: int,
             cfg: dict[str, Any], capacity: tuple[bool, str] = (True, "room for a new trade"), max_spread_bps: float = 8.0,
             min_volume: float = 50_000_000, candle_t: int | None = None, desk: riskteam.Context | None = None) -> Evaluation:
    """One market, now. Deterministic: the same candles, market and settings give the same result. `candle_t` judges that
    closed 15m candle instead of the latest (a COOKING candidate is confirmed on its own candle, as in the backtest).
    `desk` = the book as the risk team sees it (riskteam.context); without it the risk team isn't consulted."""
    i = ix.last_closed(f15, now_ms if candle_t is None else candle_t + M15)
    if candle_t is not None and (i < 0 or f15.t[i] != candle_t):
        i = -1
    if i < 0:
        return Evaluation(coin, "SEARCHING", None, "No closed 15m candles yet.", _checks(setup=("fail", "no closed 15m candles")))
    t = f15.t[i]
    if now_ms - (t + M15) > 2 * M15:
        return Evaluation(coin, "SEARCHING", t, "15m candles are stale: waiting for fresh market data.",
                          _checks(setup=("fail", f"last closed 15m candle opened {_fmt_t(t)}")))
    close_t = t + M15
    j, k = ix.last_closed(h1, close_t), ix.last_closed(h4, close_t)
    reg, rinfo = ix.regime(h1, h4, j, k) if j >= 0 and k >= 0 else ("UNSTABLE", {"why": "no closed 1h / 4h candles"})
    h1_dir = "up" if j >= 0 and (h1.e20[j] or 0) > (h1.e50[j] or 0) else "down"
    ind = {"close": f15.c[i], "ema20": f15.e20[i], "ema50": f15.e50[i], "rsi": f15.rsi[i], "atr": f15.atr[i], "adx": f15.adx[i],
           "relvol": f15.relvol[i], "vwap": f15.vwap[i], "h1_trend": h1_dir}
    ind = {key: (round(v, 6) if isinstance(v, float) else v) for key, v in ind.items()}
    spread = mkt.spread_bps if mkt else None
    market = {"mark": mkt.mark if mkt else None, "funding_h": mkt.funding if mkt else None, "oi_usd": round(mkt.oi_usd) if mkt else None,
              "spread_bps": round(spread, 2) if spread is not None else None, "day_volume": round(mkt.day_volume) if mkt else None,
              "change_24h": round(mkt.change_24h, 2) if mkt else None, "regime": reg, "regime_info": rinfo}
    found, why = ix.setups(f15, i, reg, h1_dir, cfg["models"], float(cfg["min_stop_pct"]))
    if not found:
        reasons = list(dict.fromkeys(why))
        return Evaluation(coin, "SEARCHING", t, "Kiwi is looking for ingredients: " + "; ".join(reasons[:3]),
                          _checks(setup=("fail", "; ".join(reasons))), reg, market, ind)
    sp = spread if spread is not None else slip(coin) * 2e4
    fund = mkt.funding if mkt else 0.0
    best = max(found, key=lambda s: ix.quality(s, sp, cost_r(s.ref, s.stop, coin, sp, fund, s.side, s.max_hold_min)))
    c_r = cost_r(best.ref, best.stop, coin, sp, fund, best.side, best.max_hold_min)
    q = ix.quality(best, sp, c_r)
    tp1, tp2 = best.targets(best.ref)
    setup = {"model": best.strategy, "label": ix.LABEL[best.strategy], "side": best.side, "ref": best.ref, "stop": best.stop,
             "tp1": tp1, "tp2": tp2, "r1": best.r1, "r2": best.r2, "max_hold_min": best.max_hold_min, "reasons": best.reasons,
             "cost_r": round(c_r, 3), "atr": f15.atr[i]}
    m_status, m_detail, p = ml.check(cfg, ml.features(f15, i, best, reg, h1, j, h4, k, fund, c_r, q) if j >= 0 and k >= 0 else None)
    setup["ml_p"] = round(p, 3) if p is not None else None
    given: dict[str, tuple[str, str]] = {"setup": ("pass", f"{ix.LABEL[best.strategy]} {best.side}: {best.reasons[0]}")}
    t30 = t - t % M30
    if not cfg.get("confirm_30m", True):
        given["confirm_30m"] = ("skip", "off in degen mode: trades the 15m close, no 30m wait")
    elif now_ms < t30 + M30:
        given["confirm_30m"] = ("pending", f"waiting for the 30m candle closing at {_fmt_t(t30 + M30)}")
    else:
        idx = ix.last_closed(f30, t30 + M30)
        if idx < 0 or f30.t[idx] != t30:
            given["confirm_30m"] = ("fail", "the confirming 30m candle is missing from Hyperliquid's answer")
        else:
            ok, detail = confirm_30m(f30, idx, best.side, best.strategy)
            given["confirm_30m"] = ("pass" if ok else "fail", detail)
    if mkt is None:
        given["liquidity"] = ("fail", "market data unavailable")
    else:
        bad = []
        if spread is None:
            bad.append("no spread reading from Hyperliquid")
        elif spread > max_spread_bps:
            bad.append(f"spread {spread:.1f} bps above {max_spread_bps:g}")
        if mkt.day_volume < min_volume:
            bad.append(f"24h volume ${mkt.day_volume / 1e6:,.0f}M under ${min_volume / 1e6:,.0f}M")
        given["liquidity"] = ("fail", "; ".join(bad)) if bad else ("pass", f"spread {spread:.1f} bps, 24h volume ${mkt.day_volume / 1e6:,.0f}M")
    oi = f", open interest ${mkt.oi_usd / 1e6:,.0f}M" if mkt else ""
    against = fund if best.side == "LONG" else -fund
    given["crowding"] = (("fail", f"{'longs' if best.side == 'LONG' else 'shorts'} pay {against * 100:.4f}%/h funding (crowded){oi}")
                         if against > cfg["max_funding_h"] else ("pass", f"funding {fund * 100:+.4f}%/h{oi}"))
    net = best.r1 - c_r
    given["costs"] = (("pass" if c_r <= cfg["max_cost_r"] and net >= cfg["min_net_r"] else "fail"),
                      f"costs {c_r:.2f}R (max {cfg['max_cost_r']:g}R), TP1 {net:.2f}R after costs (min {cfg['min_net_r']:g}R)")
    given["quality"] = ("pass" if q >= cfg["min_quality"] else "fail", f"score {q}/100 (min {cfg['min_quality']})")
    given["ml"] = (m_status, m_detail)
    dcfg = cfg.get("risk_desk") or riskteam.DEFAULTS
    rv = riskteam.review(desk, dcfg, coin, best.side, f15.h[i] - f15.l[i], f15.atr[i]) if desk is not None and dcfg.get("enabled", True) else None
    given["risk_team"] = riskteam.check(dcfg, rv)
    given["capacity"] = ("pass" if capacity[0] else "fail", capacity[1])
    checks = _checks(**given)
    fail = next((c for c in checks if c["status"] == "fail"), None)
    if fail:
        state, reason = "BURNT", f"{fail['label']}: {fail['detail']}"
    elif any(c["status"] == "pending" for c in checks):
        state, reason = "COOKING", f"{coin} {best.side} candidate ({ix.LABEL[best.strategy]}): {given['confirm_30m'][1]}"
    else:
        state, reason = "READY", f"{coin} {best.side}: every check passed ({ix.LABEL[best.strategy]}, score {q})"
    return Evaluation(coin, state, t, reason, checks, reg, market, ind, setup, q, rv)


# ---------------------------------------------------------------- live state machine
def _last_closed_open(now_ms: int) -> int:
    return now_ms // M15 * M15 - M15


def _capacity(db: Session, s: Any, coin: str, cfg: dict[str, Any], row: TsKitchen, candle_t: int, publish: bool) -> tuple[bool, str]:
    if s.paused:
        return False, "trading is paused for everyone"
    if row.data.get("proposed_t") == candle_t:
        return False, "already proposed from this candle"
    open_here = db.scalar(select(TsSignal).where(TsSignal.coin == coin, TsSignal.status.in_((*signals.OPEN, "PENDING_REVIEW"))).limit(1))
    if open_here is not None:
        return False, f"signal #{open_here.number:03d} on {coin} is still {signals.STATUS_LABEL.get(open_here.status, open_here.status).lower()}"
    last = db.scalar(select(TsSignal).where(TsSignal.coin == coin, TsSignal.strategy == STRATEGY, TsSignal.closed_at.is_not(None))
                     .order_by(TsSignal.closed_at.desc()).limit(1))
    if last is not None and cfg["cooldown_min"]:
        left = (signals._aware(last.closed_at) + timedelta(minutes=cfg["cooldown_min"]) - utcnow()).total_seconds() / 60  # type: ignore[operator]
        if left > 0:
            return False, f"cooling down after #{last.number:03d}: {left:.0f} min left"
    if publish:
        n = db.scalar(select(func.count()).select_from(TsSignal).where(TsSignal.status.in_((*signals.OPEN, "PENDING_REVIEW")))) or 0
        limit = int(cfg.get("max_open") or 0) or s.max_open_signals
        if n >= limit:
            return False, f"{n} signal{'s' if n != 1 else ''} already open (limit {limit})"
    return True, "room for a new trade" + ("" if publish else " (shadow: nothing is proposed)")


def payload(row: TsKitchen) -> dict[str, Any]:
    d = row.data or {}
    return {"symbol": row.coin, "timeframe": TF, "confirm_timeframe": CONFIRM_TF, "state": row.state, **d}


def _emit(db: Session, row: TsKitchen, title: str) -> KbEvent:
    at = row.data.get("candle_t")
    ev = signals_brain().log(db, "strategy", title, coin=row.coin, signal_id=row.signal_id, source=SOURCE,
                             source_at=datetime.fromtimestamp((at + M15) / 1000, tz=timezone.utc) if at else None, data={"kitchen": payload(row)})
    db.flush()
    row.event_id = ev.id
    return ev


def signals_brain() -> Any:
    from app.tradingos import brain

    return brain


def _set(row: TsKitchen, state: str, ev: Evaluation | None, reason: str, shadow: bool, proposal: dict[str, Any] | None = None,
         keep: dict[str, Any] | None = None) -> None:
    now = utcnow()
    checks = ev.checks if ev else (row.data or {}).get("checks", [])
    data = {"at": now.isoformat(), "candle_t": ev.candle_t if ev else (row.data or {}).get("candle_t"), "reason": reason,
            "strength": ev.strength if ev else (row.data or {}).get("strength"), "checks": checks,
            "passed": sum(1 for c in checks if c["status"] == "pass"), "total": len(checks),
            "risk": [c for c in checks if c["id"] in RISK_CHECKS], "indicators": ev.indicators if ev else (row.data or {}).get("indicators", {}),
            "market": ev.market if ev else (row.data or {}).get("market", {}), "setup": ev.setup if ev else (row.data or {}).get("setup"),
            "proposal": proposal, "shadow": shadow, "strategy": STRATEGY,
            "risk_team": ev.risk_team if ev else (row.data or {}).get("risk_team"), **(keep or {})}
    row.state, row.data = state, data
    row.candle_t = data["candle_t"]


def _propose(db: Session, s: Any, ev: Evaluation, md: MarketData, cfg: dict[str, Any] | None = None) -> TsSignal:
    """READY with publishing on: the proposal becomes an ordinary signal for the existing pipeline. With market entry (degen
    mode) the plan is moved to the live mark, same distances in R, and the tracker opens it at market once it is published."""
    assert ev.setup
    cfg = cfg or DEFAULTS
    st, m = ev.setup, md.market(ev.coin)
    ref, stop, atr = st["ref"], st["stop"], st["atr"] or abs(st["ref"] - st["stop"])
    tp1 = st["tp1"]
    market_entry = bool(cfg.get("market_entry")) and m.mark > 0 and (m.mark - stop) * (1 if st["side"] == "LONG" else -1) > 0
    if market_entry:
        delta = m.mark - ref
        ref, stop, tp1 = m.mark, stop + delta, tp1 + delta
    zone = (ref - (0.05 if market_entry else 0.15) * atr, ref + (0.05 if market_entry else 0.15) * atr)
    stop_pct = abs(ref - stop) / ref
    lev_hi = min(5, m.max_leverage)
    while lev_hi > 1 and (1 / lev_hi) * 0.8 <= stop_pct * 1.5:  # the estimated liquidation stays well beyond the stop
        lev_hi -= 1
    confirmed = [f"30m confirmed: {ev.checks[1]['detail']}"] if ev.checks[1]["status"] == "pass" else []
    a = signals.Analysis(coin=ev.coin, decision=st["side"], reasons=[*st["reasons"], *confirmed],
                         indicators={"price": ref, "strategy": STRATEGY, "model": st["model"], "quality": ev.strength, "cost_r": st["cost_r"],
                                     "max_hold_min": st["max_hold_min"], "regime": ev.regime, "candle_t": ev.candle_t, "ml_p": st.get("ml_p"), "ml_filter": ml.filtering(cfg),
                                     "risk_team": {"verdict": ev.risk_team["verdict"], "reason": ev.risk_team["reason"]} if ev.risk_team else None,
                                     "market_entry": market_entry, "mode": cfg.get("mode", "standard"),
                                     "checks": [{"id": c["id"], "status": c["status"], "detail": c["detail"]} for c in ev.checks], **ev.indicators},
                         entry=zone, stop=stop, target=tp1, lev=(min(3, lev_hi), lev_hi), rr=round(st["r1"] - st["cost_r"], 2),
                         score=float(ev.strength or 0), strategy=STRATEGY)
    try:  # the three main indicators for the card, from the same closed 15m candles
        a.indicators["main3"] = main_three([c.c for c in signals.closed(md.candles(ev.coin, TF, 120), TF)])
    except MarketError:
        a.indicators["main3"] = None
    auto = s.auto_publish or bool(cfg.get("auto_publish"))
    return signals.create(db, a, TF, "WAITING" if auto else "PENDING_REVIEW", SOURCE, strategy=STRATEGY)


def _proposal(sig: TsSignal | None, ev: Evaluation | None = None, shadow: bool = False) -> dict[str, Any] | None:
    if sig is None:
        if not (shadow and ev and ev.setup):
            return None
        return {"signal_id": None, "number": None, "side": ev.setup["side"], "entry": ev.setup["ref"], "stop": ev.setup["stop"],
                "target": ev.setup["tp1"], "status": "SHADOW", "approval_required": False, "shadow": True}
    return {"signal_id": sig.id, "number": sig.number, "side": sig.side, "entry": [sig.entry_lo, sig.entry_hi], "stop": sig.stop,
            "target": sig.target, "tp2": risk.tp2_of((sig.entry_lo + sig.entry_hi) / 2, sig.target), "status": sig.status,
            "approval_required": sig.status == "PENDING_REVIEW", "shadow": False}


def _decide(db: Session, ev: Evaluation, cfg: dict[str, Any], signal_id: int | None, shadow: bool) -> None:
    """The structured decision record (decisions.py) for a candidate. Its own savepoint: a failure here never stops the kitchen."""
    if not ev.setup:
        return
    try:
        with db.begin_nested():
            decisions.record(db, ev, cfg, STRATEGY, signal_id, shadow)
    except Exception:  # noqa: BLE001 - measuring must never block trading
        logger.exception("kitchen: decision record failed for %s", ev.coin)


def tick_coin(db: Session, md: MarketData, coin: str, s: Any, cfg: dict[str, Any], now: datetime | None = None) -> str | None:
    """Advance one market's kitchen. Returns the new state when it changed (an event was written), else None."""
    now = now or utcnow()
    now_ms = int(now.timestamp() * 1000)
    publish = publishing(s)
    row = db.scalar(select(TsKitchen).where(TsKitchen.coin == coin))
    if row is None:
        row = TsKitchen(coin=coin, state="SEARCHING", data={})
        db.add(row)
        db.flush()
    keep = {"proposed_t": row.data.get("proposed_t")} if row.data.get("proposed_t") else {}
    # a served dish: follow its signal until it's done
    if row.state == "READY" and row.signal_id:
        sig = db.get(TsSignal, row.signal_id)
        if sig is not None and sig.status in (*signals.OPEN, "PENDING_REVIEW"):
            prop = row.data.get("proposal") or {}
            if prop.get("status") != sig.status:
                _set(row, "READY", None, f"Signal #{sig.number:03d} {sig.coin} {sig.side} is {signals.STATUS_LABEL.get(sig.status, sig.status)}",
                     False, _proposal(sig), keep)
                _emit(db, row, f"🥝 {coin} kitchen: #{sig.number:03d} {sig.side} {signals.STATUS_LABEL.get(sig.status, sig.status)}")
                return "READY"
            return None
        label = signals.STATUS_LABEL.get(sig.status, sig.status) if sig else "gone"
        if sig is None or sig.status == "CANCELLED" or (sig.status == "EXPIRED" and not sig.activated_at):
            why = ("the proposal was cancelled by the reviewer" if sig and sig.status == "CANCELLED" else
                   "expired before approval" if sig and not sig.published_at else "the entry zone was never reached" if sig else "the signal is gone")
            _set(row, "BURNT", None, f"#{sig.number:03d}: {why}" if sig else why, False, _proposal(sig), keep)
            row.signal_id = sig.id if sig else None
            _emit(db, row, f"🔥 {coin} kitchen: burnt · {why}")
            return "BURNT"
        res = f" ({sig.model_pnl_pct:+.2f}% signal result after fees)" if sig.model_pnl_pct is not None else ""
        _set(row, "SEARCHING", None, f"#{sig.number:03d} finished: {label}{res}. Kiwi is looking for ingredients.", not publish, _proposal(sig), keep)
        row.signal_id = None
        _emit(db, row, f"🍽️ {coin} kitchen: #{sig.number:03d} {label}{res}")
        return "SEARCHING"
    # nothing new to judge: the same closed candle was already decided (only a pending COOKING is re-checked)
    if row.state != "COOKING" and row.candle_t == _last_closed_open(now_ms) and not row.data.get("error"):
        return None
    try:
        f15 = ix.series(md.candles(coin, "15m", 300), M15)
        f30 = ix.series(md.candles(coin, "30m", 120), M30)
        h1 = ix.series(md.candles(coin, "1h", 260), INTERVALS["1h"] * 1000)
        h4 = ix.series(md.candles(coin, "4h", 80), INTERVALS["4h"] * 1000)
        mkt = md.market(coin)
    except MarketError as e:
        msg = f"market data unavailable: {e}"[:200]
        if row.data.get("error") != msg:
            _set(row, "SEARCHING", None, f"Kiwi can't see the market right now ({msg}).", not publish, None, {**keep, "error": msg})
            _emit(db, row, f"{coin} kitchen: {msg}")
            return "SEARCHING"
        return None
    at = row.candle_t if row.state == "COOKING" else None  # a pending candidate is finished on its own candle first
    i = ix.last_closed(f15, now_ms if at is None else at + M15)
    candle_t = f15.t[i] if i >= 0 else None
    cap = _capacity(db, s, coin, cfg, row, candle_t or -1, publish)
    ev = evaluate(coin, f15, f30, h1, h4, mkt, now_ms, cfg, cap, s.max_spread_bps, s.min_volume_usd, candle_t=at, desk=riskteam.context(db, now))
    same = ev.state == row.state and ev.candle_t == row.candle_t and [c["status"] for c in ev.checks] == [c["status"] for c in row.data.get("checks", [])]
    if same and not row.data.get("error"):
        return None
    if ev.state == "READY":
        sig = _propose(db, s, ev, md, cfg) if publish else None
        _set(row, "READY", ev, ev.reason + ("" if publish else " · SHADOW: the strategy isn't switched on, nothing was proposed"),
             not publish, _proposal(sig, ev, shadow=not publish), {**keep, "proposed_t": ev.candle_t} if sig else keep)
        row.signal_id = sig.id if sig else None
        _decide(db, ev, cfg, sig.id if sig else None, not publish)
        st = ev.setup or {}
        tag = f"#{sig.number:03d} " if sig else ""
        _emit(db, row, f"🔔 {coin} kitchen: READY {tag}{st.get('side')} ({st.get('label')}, {ev.passed}/{len(ev.checks)} checks)"
                       + ("" if publish else " · shadow"))  # a shadow dish isn't followed: the next candle is judged afresh
        return "READY"
    _set(row, ev.state, ev, ev.reason, not publish, None, keep)
    _decide(db, ev, cfg, None, not publish)
    icon = {"SEARCHING": "🔎", "COOKING": "🍳", "BURNT": "🔥"}[ev.state]
    _emit(db, row, f"{icon} {coin} kitchen: {ev.state} · {ev.reason}"[:240])
    return ev.state


def tick(db: Session, md: MarketData, now: datetime | None = None) -> dict[str, Any]:
    """The tradingos.kitchen job: every kitchen market once. A market whose data fails is reported, the others go on."""
    s = signals.settings(db)
    cfg = config(s)
    out: dict[str, Any] = {"changed": {}, "shadow": not publishing(s)}
    if not s.scanner_enabled:
        out["skipped"] = "scanner off"
        return out
    for coin in cfg["coins"]:
        try:
            st = tick_coin(db, md, coin, s, cfg, now)
        except MarketError as e:
            db.rollback()
            out.setdefault("errors", {})[coin] = str(e)[:200]
            continue
        if st:
            out["changed"][coin] = st
        db.commit()
    return out


# ---------------------------------------------------------------- read side (Earn page, Kiwi Brain, stream)
def event_out(e: KbEvent) -> dict[str, Any]:
    k = (e.data or {}).get("kitchen") or {}
    return {"id": e.id, "at": signals._aware(e.at).isoformat() if e.at else None, "title": e.title, "signal_id": e.signal_id, **k}  # type: ignore[union-attr]


def events_after(db: Session, after_id: int = 0, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.scalars(select(KbEvent).where(KbEvent.source == SOURCE, KbEvent.member_id.is_(None), KbEvent.id > after_id)
                      .order_by(KbEvent.id).limit(limit))
    return [event_out(e) for e in rows]


def snapshot(db: Session) -> dict[str, Any]:
    s = signals.settings(db)
    cfg = config(s)
    rows = {r.coin: r for r in db.scalars(select(TsKitchen).where(TsKitchen.coin.in_(cfg["coins"])))}
    markets = []
    for coin in cfg["coins"]:
        r = rows.get(coin)
        markets.append(payload(r) | {"event_id": r.event_id, "updated_at": signals._aware(r.updated_at).isoformat() if r.updated_at else None}  # type: ignore[union-attr]
                       if r else {"symbol": coin, "timeframe": TF, "confirm_timeframe": CONFIRM_TF, "state": "SEARCHING", "reason": "Kiwi is looking for ingredients.",
                                  "checks": [], "passed": 0, "total": len(CHECKS), "shadow": not publishing(s)})
    if not cfg["confirm_30m"]:
        markets = [m | {"confirm_timeframe": ""} for m in markets]
    recent = list(db.scalars(select(KbEvent).where(KbEvent.source == SOURCE, KbEvent.member_id.is_(None)).order_by(KbEvent.id.desc()).limit(20)))
    return {"strategy": STRATEGY, "label": LABEL, "timeframe": TF, "confirm_timeframe": CONFIRM_TF if cfg["confirm_30m"] else "",
            "mode": cfg["mode"], "max_open": int(cfg["max_open"] or 0) or s.max_open_signals, "every_s": EVERY_S,
            "shadow": not publishing(s), "live": live_enabled(s), "paused": s.paused, "markets": markets,
            "events": [event_out(e) for e in reversed(recent)], "last_id": recent[0].id if recent else 0,
            "checks": [{"id": k, "label": label} for k, label in CHECKS], "ml": ml_info(cfg)}


def ml_info(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Kiwi ML's state for the Earn page: is it filtering, and how it did on data it never saw (real numbers, losses included)."""
    sm = ml.summary()
    if sm is None:
        return None
    h = sm.get("holdout") or {}
    pick = lambda m: {k: (m or {}).get(k) for k in ("trades", "win_rate", "expectancy_r", "trades_per_day")}  # noqa: E731
    return {"mode": ml.mode(cfg), "filtering": ml.filtering(cfg), "validated": sm.get("validated"), "profitable": sm.get("profitable"),
            "threshold": sm.get("threshold"), "trained_at": sm.get("trained_at"), "period": sm.get("period"), "source": sm.get("source"),
            "samples": sm.get("samples"), "auc": h.get("auc"), "without_ml": pick(h.get("without_ml")), "with_ml": pick(h.get("with_ml"))}


# ---------------------------------------------------------------- backtest (research; cached history)
def aggregate(cs: list[Any], ms: int) -> list[Any]:
    """Complete higher-timeframe candles from lower ones (e.g. 30m from 15m): only buckets with every part present."""
    from app.tradingos.market import Candle

    out, bucket, start = [], [], None
    step = cs[1].t - cs[0].t if len(cs) > 1 else ms
    need = ms // step
    for c in cs:
        b = c.t - c.t % ms
        if b != start:
            if bucket and len(bucket) == need:
                out.append(Candle(start, bucket[0].o, max(x.h for x in bucket), min(x.l for x in bucket), bucket[-1].c, sum(x.v for x in bucket)))
            bucket, start = [], b
        bucket.append(c)
    if bucket and len(bucket) == need:
        out.append(Candle(start, bucket[0].o, max(x.h for x in bucket), min(x.l for x in bucket), bucket[-1].c, sum(x.v for x in bucket)))
    return out


def backtest(coins: list[str], cfg: dict[str, Any] | None = None, confirm: bool | None = None, source: str = "") -> list[Any]:
    """Every trade the kitchen would have proposed, replayed with backtest.simulate (entry at the open of the first 15m
    candle after the confirmation, stop first inside a candle, fees, slippage and real funding). Liquidity and capacity
    can't be replayed (no historical order books): they are assumed to pass, which flatters the result slightly.
    `source` = "binance" replays the 2-year Binance history (history.fetch_binance) instead of Hyperliquid's 52 days."""
    from app.tradingos import backtest as bt
    from app.tradingos import history

    cfg = {**DEFAULTS, **(cfg or {})}
    confirm = bool(cfg["confirm_30m"]) if confirm is None else confirm
    use_ml = ml.filtering(cfg)  # the same ML gate as live, when it filters
    out = []
    for coin in coins:
        try:
            c15 = history.candles(coin, "15m", source)
        except OSError:
            continue
        f15, f30 = ix.series(c15, M15), ix.series(aggregate(c15, M30), M30)
        h1, h4 = (ix.series(history.candles(coin, iv, source), INTERVALS[iv] * 1000) for iv in ("1h", "4h"))
        fund = history.funding(coin, source)
        last_exit = 0
        for i in range(210, len(f15.t) - 2):
            t = f15.t[i]
            close_t = t + M15
            if close_t < last_exit:
                continue
            ready_t = max(close_t, t - t % M30 + M30) if confirm else close_t
            j, k = ix.last_closed(h1, close_t), ix.last_closed(h4, close_t)
            reg, _ = ix.regime(h1, h4, j, k)
            h1_dir = "up" if (h1.e20[j] or 0) > (h1.e50[j] or 0) else "down"
            found, _ = ix.setups(f15, i, reg, h1_dir, cfg["models"], float(cfg["min_stop_pct"]))
            if not found:
                continue
            rate = bt._funding_rate_at(fund, close_t)
            sp = slip(coin) * 2e4
            best = max(found, key=lambda s: ix.quality(s, sp, cost_r(s.ref, s.stop, coin, sp, rate, s.side, s.max_hold_min)))
            c_r = cost_r(best.ref, best.stop, coin, sp, rate, best.side, best.max_hold_min)
            q = ix.quality(best, sp, c_r)
            against = rate if best.side == "LONG" else -rate
            if q < cfg["min_quality"] or c_r > cfg["max_cost_r"] or best.r1 - c_r < cfg["min_net_r"] or against > cfg["max_funding_h"]:
                continue
            if use_ml and ml.check(cfg, ml.features(f15, i, best, reg, h1, j, h4, k, rate, c_r, q))[0] == "fail":
                continue
            if confirm:
                idx = ix.last_closed(f30, ready_t)
                if idx < 0 or f30.t[idx] != t - t % M30 or not confirm_30m(f30, idx, best.side, best.strategy)[0]:
                    continue
            e = ix.last_closed(f15, ready_t)  # the entry is at the open of the candle after this one
            r = bt.simulate(f15, e, best.side, best.stop, best.r1, best.r2, best.max_hold_min, coin, fund)
            if r is None:
                continue
            out.append(bt.Trade(coin, best.strategy, best.side, reg, close_t, r["t_entry"], r["t_exit"], r["entry"], best.stop, r["r_net"],
                                r["r_gross"], r["cost_r"], r["exit"], r["hold_min"], q))
            last_exit = r["t_exit"]
    return out


def report(coins: list[str]) -> dict[str, Any]:
    """15m+30m (the live rule) vs the same 15m models without the 30m confirmation, on the portfolio rules of backtest.py,
    with a chronological 60/40 selection / holdout split. Nothing was tuned on this data."""
    from app.tradingos import backtest as bt

    out: dict[str, Any] = {"strategy": STRATEGY, "coins": coins, "config": DEFAULTS, "assumptions": {
        "fee_per_side": risk.TAKER_FEE, "slippage_per_side": SLIP | {"others": 0.0005}, "funding": "real hourly Hyperliquid funding",
        "entry": "open of the first 15m candle after the 30m confirmation closed", "intrabar": "stop first",
        "portfolio": "at most 2 open, one per coin, 0.5% risk per trade, daily stop -2%",
        "not_replayed": "spread / depth (no historical books) and the open-signal limit: assumed to pass"}}
    for name, confirm in (("15m+30m", True), ("15m only", False)):
        ts = bt.portfolio(backtest(coins, confirm=confirm))
        if not ts:
            out[name] = {"trades": 0}
            continue
        start, end = min(t.t_entry for t in ts), max(t.t_exit for t in ts)
        cut = int(start + (end - start) * 0.6)
        sel, hold = bt.split(ts, cut)
        out[name] = {"all": bt.metrics(ts), "selection": bt.metrics(sel), "holdout": bt.metrics(hold), "by_model": bt.by(ts, "strategy"),
                     "by_side": bt.by(ts, "side"), "period": [start, end]}
    return out


if __name__ == "__main__":
    from app.tradingos import history

    res = report(sys.argv[1:] or history.COINS)
    path = history.DIR / "kitchen.json"
    path.write_text(json.dumps(res, indent=1))
    for name in ("15m+30m", "15m only"):
        a = res[name].get("all", res[name])
        h = res[name].get("holdout", {})
        print(f"{name:9s} trades {a.get('trades')} · expectancy {a.get('expectancy_r')}R {a.get('expectancy_r_90ci')} · "
              f"win {a.get('win_rate')}% · {a.get('trades_per_day')}/day · holdout {h.get('expectancy_r')}R ({h.get('trades')} trades)")
    print(f"written to {path}")
