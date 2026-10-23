"""Technical section of the evidence pack: indicators per timeframe, support / resistance, the long and short scenarios
and the section's lean. Pure functions over CLOSED candles (the caller has already dropped every candle that hadn't
closed by as_of), so the same inputs always give the same pack.

Per timeframe (indicators.py): EMA 20/50/200, RSI 14 (Wilder), MACD 12/26/9, Bollinger 20 ±2σ (width = (upper-lower)/mid
in %), ATR 14 (Wilder) and ATR % of close, volume ratio (last candle / mean of the 20 before), realized volatility (σ of
the last 20 log returns, % per candle, not annualised), trend (readout's rule: EMA20 vs EMA50 aligned, EMA50 sloping the
same way over 5 candles, close on that side of EMA50 -> up / down, else range), swing structure from k=2 pivots (last two
pivot highs and lows both rising HH/HL, both falling LH/LL, else mixed), breakout (last close beyond the previous 20
candles' high / low).

Levels: pivot highs / lows (k=2) of the last 120 run-timeframe candles, the last 120 4h candles and the daily candles, plus
the run timeframe's 120-candle extreme and the daily window extreme; clustered 0.4% apart; nearest three each side.

Scenarios (every number says how it was made; null when it can't be derived, e.g. fewer than 20 candles or no ATR):
  long   trigger      the nearest resistance above the price, else the highest high of the last 20 run-timeframe candles
         invalidation the nearest support below the trigger minus 0.25 x ATR14(run timeframe)
         targets      the next two resistances above the trigger; missing ones filled with trigger + 2 x ATR, + 3 x ATR
  short  mirrored (nearest support below the price / 20-candle low; nearest resistance above + 0.25 ATR; supports or - ATR)

Lean / score (deterministic): +1 / -1 for each of the 1h, 4h, 1d trends that is up / down; +1 / -1 for the run
timeframe's MACD histogram above / below zero; RSI14 >= 70 takes 1 off a positive total, <= 30 adds 1 to a negative one
(stretched). score = the total halved and rounded half away from zero, clamped to -2..2; lean bullish > 0, bearish < 0,
neutral 0, unknown without any frame."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.committee.evidence.common import iso, rnd
from app.tradingos import indicators as ind
from app.tradingos.market import INTERVALS, Candle, fmt_price
from app.tradingos.readout import _trend

FRAMES = ("15m", "1h", "4h", "1d")
ALIGN = ("1h", "4h", "1d")
CANDLES = 300  # per frame: EMA200 plus warm-up
LEVEL_WINDOW = 120
MIN_SCENARIO = 20


def frames_for(timeframe: str) -> list[str]:
    """The run timeframe plus the alignment frames, shortest first."""
    out = {timeframe, *ALIGN}
    return sorted(out, key=lambda t: INTERVALS.get(t, 0))


def closed_by(candles: list[Candle], tf: str, as_of_ms: int) -> list[Candle]:
    """Only candles whose close time (open + span) is at or before as_of."""
    span = INTERVALS[tf] * 1000
    return sorted((c for c in candles if c.t + span <= as_of_ms), key=lambda c: c.t)


def frame_facts(cs: list[Candle], tf: str) -> dict[str, Any] | None:
    if not cs:
        return None
    closes, highs, lows, vols = [c.c for c in cs], [c.h for c in cs], [c.l for c in cs], [c.v for c in cs]
    last = lambda xs: xs[-1] if xs else None  # noqa: E731
    e20, e50, e200 = last(ind.ema(closes, 20)), last(ind.ema(closes, 50)), last(ind.ema(closes, 200))
    rsi = ind.rsi(closes)
    m = ind.macd(closes)
    bb = ind.bollinger(closes)
    atr = ind.atr(highs, lows, closes)
    trend = {"Bullish": "up", "Bearish": "down"}.get(_trend(closes)[0], "range") if len(cs) >= 56 else None
    breakout = None
    if len(cs) >= 21:
        if closes[-1] > max(highs[-21:-1]):
            breakout = "up"
        elif closes[-1] < min(lows[-21:-1]):
            breakout = "down"
    close = closes[-1]
    return {"close": rnd(close), "ema20": rnd(e20), "ema50": rnd(e50), "ema200": rnd(e200),
            "rsi14": round(rsi, 1) if rsi is not None else None,
            "macd": rnd(m[0]) if m else None, "macd_signal": rnd(m[1]) if m else None, "macd_hist": rnd(m[2]) if m else None,
            "bb_upper": rnd(bb[1]) if bb else None, "bb_mid": rnd(bb[0]) if bb else None, "bb_lower": rnd(bb[2]) if bb else None,
            "bb_width_pct": round((bb[1] - bb[2]) / bb[0] * 100, 2) if bb and bb[0] else None,
            "atr14": rnd(atr), "atr_pct": round(atr / close * 100, 2) if atr and close else None,
            "vol_ratio20": round(v, 2) if (v := ind.volume_ratio(vols)) is not None else None,
            "trend": trend, "structure": ind.swing_structure(highs, lows), "breakout": breakout,
            "realized_vol_pct": round(rv, 3) if (rv := ind.realized_vol_pct(closes)) is not None else None,
            "candles": len(cs), "last_closed": iso((cs[-1].t + INTERVALS[tf] * 1000) / 1000)}


# ---------------------------------------------------------------- levels
def level_pool(frames: dict[str, list[Candle]], timeframe: str) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
    """(support candidates, resistance candidates) as (price, where it comes from)."""
    lows: list[tuple[float, str]] = []
    highs: list[tuple[float, str]] = []
    for tf in dict.fromkeys((timeframe, "4h", "1d")):
        cs = frames.get(tf) or []
        if tf != "1d":
            cs = cs[-LEVEL_WINDOW:]
        if not cs:
            continue
        pl, ph = ind.pivots([c.h for c in cs], [c.l for c in cs])
        lows += [(x, f"{tf} pivot low") for x in pl]
        highs += [(x, f"{tf} pivot high") for x in ph]
        if tf in (timeframe, "1d"):  # the window's own extremes, when no swing stands on that side (a one-way market)
            lows.append((min(c.l for c in cs), f"{tf} {len(cs)}-candle low"))
            highs.append((max(c.h for c in cs), f"{tf} {len(cs)}-candle high"))
    return lows, highs


def nearest(pool: list[tuple[float, str]], ref: float, below: bool, n: int) -> list[tuple[float, str]]:
    """indicators.nearest_levels over labelled levels (the first label of a cluster wins)."""
    vals = ind.nearest_levels([p for p, _ in pool], ref, below, n)
    out = []
    for v in vals:
        out.append((v, next(lbl for p, lbl in pool if p == v)))
    return out


def scenario(side: str, price: float, cs: list[Candle], tf: str, atr: float | None, lows: list[tuple[float, str]],
             highs: list[tuple[float, str]]) -> dict[str, Any] | None:
    """One scenario per the module docstring, or None when its levels can't be derived."""
    if len(cs) < MIN_SCENARIO or not atr or price <= 0:
        return None
    long = side == "long"
    f = fmt_price
    atr_txt = f"ATR14({tf}) {f(atr)}"
    above, below = (highs, lows) if long else (lows, highs)
    near = nearest(above, price, not long, 1)
    if near:
        trigger, lbl = near[0]
        trigger_rule = f"nearest {'resistance above' if long else 'support below'} the price {f(price)}: {lbl} {f(trigger)}"
    else:
        trigger = max(c.h for c in cs[-20:]) if long else min(c.l for c in cs[-20:])
        trigger_rule = (f"no {'resistance above' if long else 'support below'} the price: {'highest high' if long else 'lowest low'} "
                        f"of the last 20 closed {tf} candles {f(trigger)} ({'breakout' if long else 'breakdown'} level)")
    guard = nearest(below, trigger, long, 1)
    if not guard:
        return None
    g, glbl = guard[0]
    sign = 1 if long else -1
    invalidation = g - sign * 0.25 * atr
    inv_rule = (f"nearest {'support below' if long else 'resistance above'} the trigger ({glbl} {f(g)}) "
                f"{'minus' if long else 'plus'} 0.25 x {atr_txt}")
    targets: list[float] = []
    parts: list[str] = []
    for v, lbl in nearest(above, trigger, not long, 2):
        targets.append(v)
        parts.append(f"{lbl} {f(v)}")
    for mult in (2, 3):
        if len(targets) >= 2:
            break
        v = trigger + sign * mult * atr
        if (not targets or sign * (v - targets[-1]) > 0) and v > 0:
            targets.append(v)
            parts.append(f"trigger {'+' if long else '-'} {mult} x {atr_txt} = {f(v)}")
    if not targets or invalidation <= 0:
        return None
    return {"trigger": rnd(trigger), "trigger_rule": trigger_rule, "invalidation": rnd(invalidation), "invalidation_rule": inv_rule,
            "targets": [rnd(t) for t in targets],
            "target_rule": "; ".join(f"T{i + 1} = {p}" for i, p in enumerate(parts))
            + f" (next {'resistances above' if long else 'supports below'} the trigger, then ATR multiples)"}


# ---------------------------------------------------------------- lean
def lean(frames: dict[str, dict[str, Any] | None], timeframe: str) -> tuple[str, int | None, list[str]]:
    """(lean, score, reasons in words) per the module docstring."""
    have = {tf: f for tf, f in frames.items() if f}
    if not have:
        return "unknown", None, []
    pts = 0
    why: list[str] = []
    for tf in ALIGN:
        t = (have.get(tf) or {}).get("trend")
        if t in ("up", "down"):
            pts += 1 if t == "up" else -1
            why.append(f"{tf} trend {t} (EMA20/EMA50 aligned, EMA50 sloping, close on that side)")
        elif t == "range":
            why.append(f"{tf} trend range (EMA20/EMA50 not aligned)")
    run = have.get(timeframe) or {}
    h = run.get("macd_hist")
    if h:
        pts += 1 if h > 0 else -1
        why.append(f"{timeframe} MACD histogram {'above' if h > 0 else 'below'} zero ({h:+.4g})")
    r = run.get("rsi14")
    if r is not None and r >= 70 and pts > 0:
        pts -= 1
        why.append(f"{timeframe} RSI14 {r:.1f} overbought (stretched)")
    elif r is not None and r <= 30 and pts < 0:
        pts += 1
        why.append(f"{timeframe} RSI14 {r:.1f} oversold (stretched)")
    score = max(-2, min(2, int(abs(pts) / 2 + 0.5) * (1 if pts > 0 else -1))) if pts else 0
    return ("bullish" if score > 0 else "bearish" if score < 0 else "neutral"), score, why


def build(frames: dict[str, list[Candle]], timeframe: str, price: float | None, as_of: datetime) -> dict[str, Any]:
    """facts (frames, support, resistance, scenarios), status, limitations, lean, score, reason texts."""
    facts_frames = {tf: frame_facts(cs, tf) for tf, cs in frames.items()}
    lim: list[str] = []
    run_cs = frames.get(timeframe) or []
    if not run_cs:
        return {"status": "unavailable", "limitations": [f"no closed {timeframe} candles"], "facts": {"frames": {}}, "lean": "unknown",
                "score": None, "why": []}
    status = "ok"
    span = INTERVALS[timeframe]
    age = as_of.timestamp() - (run_cs[-1].t / 1000 + span)
    if age > 2 * span:
        status = "stale"
        lim.append(f"last closed {timeframe} candle is {age / 3600:.1f} h old (more than 2 periods): stale market data")
    for tf, cs in frames.items():
        if not cs:
            lim.append(f"no closed {tf} candles")
        elif len(cs) < 200:
            lim.append(f"only {len(cs)} closed {tf} candles: EMA200 and some indicators unavailable")
        elif tf != timeframe and as_of.timestamp() - (cs[-1].t / 1000 + INTERVALS[tf]) > 2 * INTERVALS[tf]:
            lim.append(f"{tf} candles are stale")
    if status == "ok" and lim:
        status = "partial"
    ref_price = price or run_cs[-1].c
    atr = ind.atr([c.h for c in run_cs], [c.l for c in run_cs], [c.c for c in run_cs])
    lows, highs = level_pool(frames, timeframe)
    support = [rnd(v) for v, _ in nearest(lows, ref_price, True, 3)]
    resistance = [rnd(v) for v, _ in nearest(highs, ref_price, False, 3)]
    scen = {"long": scenario("long", ref_price, run_cs, timeframe, atr, lows, highs),
            "short": scenario("short", ref_price, run_cs, timeframe, atr, lows, highs)}
    if len(run_cs) < MIN_SCENARIO or not atr:
        lim.append(f"fewer than {MIN_SCENARIO} closed {timeframe} candles (or no ATR): scenarios can't be derived")
    lean_, score, why = lean(facts_frames, timeframe)
    return {"status": status, "limitations": lim,
            "facts": {"frames": facts_frames, "support": support, "resistance": resistance, "scenarios": scen},
            "lean": lean_, "score": score, "why": why}
