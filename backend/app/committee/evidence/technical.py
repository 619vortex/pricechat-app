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
the run timeframe's 120-candle extreme and the daily window extreme. Levels within 0.25 x ATR14(run timeframe) of each other
are merged (grouped in price order, each group spanning at most 0.25 ATR, repeated until no two are closer); the one kept
has the most touches of the last 120 run-timeframe candles (range within 0.25 ATR of it), then is the most recent.
Nearest three each side.

Scenarios (every number's rule string says how it was made; null when it can't be derived: < 20 candles or no ATR):
  long   trigger      the nearest resistance above the price, else the highest high of the last 20 run-timeframe candles
         invalidation the nearest support at least 0.75 ATR below the trigger, minus 0.25 x ATR (so >= 1 ATR from the
                      trigger); no such support -> trigger - 1.5 x ATR
         targets      T1 = the first resistance >= max(1R, 1 x ATR) above the trigger (R = trigger - invalidation), else
                      trigger + max(2 x ATR, 1R); T2 = the next resistance >= 1 x ATR above T1, else trigger + 3 x ATR when
                      that is >= T1 + 1 x ATR, else T1 + 1 x ATR
  short  mirrored (supports and resistances swapped, signs flipped; targets must stay above zero)

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
Level = tuple[float, str, int]  # (price, where it comes from, open time of the candle that made it, ms)


def level_pool(frames: dict[str, list[Candle]], timeframe: str) -> tuple[list[Level], list[Level]]:
    """(support candidates, resistance candidates), before clustering. Pivots as indicators.pivots (k=2)."""
    lows: list[Level] = []
    highs: list[Level] = []
    k = 2
    for tf in dict.fromkeys((timeframe, "4h", "1d")):
        cs = frames.get(tf) or []
        if tf != "1d":
            cs = cs[-LEVEL_WINDOW:]
        if not cs:
            continue
        for i in range(k, len(cs) - k):
            win = cs[i - k:i + k + 1]
            if cs[i].l == min(c.l for c in win):
                lows.append((cs[i].l, f"{tf} pivot low", cs[i].t))
            if cs[i].h == max(c.h for c in win):
                highs.append((cs[i].h, f"{tf} pivot high", cs[i].t))
        if tf in (timeframe, "1d"):  # the window's own extremes, when no swing stands on that side (a one-way market)
            lo, hi = min(cs, key=lambda c: c.l), max(cs, key=lambda c: c.h)
            lows.append((lo.l, f"{tf} {len(cs)}-candle low", lo.t))
            highs.append((hi.h, f"{tf} {len(cs)}-candle high", hi.t))
    return lows, highs


def touches(v: float, cs: list[Candle], tol: float) -> int:
    """Run-timeframe candles whose range reaches within tol of the level."""
    return sum(1 for c in cs if c.l - tol <= v <= c.h + tol)


def cluster(pool: list[Level], cs: list[Candle], atr: float | None, tf: str) -> list[Level]:
    """Levels within 0.25 x ATR14(tf) of each other become one: in price order, a group spans at most 0.25 ATR from its
    lowest member (no chaining) and keeps the member with the most touches of the last LEVEL_WINDOW run-timeframe candles,
    then the most recent; repeated until no two kept levels are within 0.25 ATR. Its label says what was merged."""
    if not pool or not atr:
        return list(pool)
    tol = 0.25 * atr
    recent = cs[-LEVEL_WINDOW:]
    items = [(v, lbl, t, 1) for v, lbl, t in pool]  # + how many raw levels it stands for
    while True:
        groups: list[list[tuple[float, str, int, int]]] = []
        for lv in sorted(items, key=lambda x: x[0]):
            if groups and lv[0] - groups[-1][0][0] <= tol:
                groups[-1].append(lv)
            else:
                groups.append([lv])
        merged = []
        for g in groups:
            best = max(g, key=lambda x: (touches(x[0], recent, tol), x[2]))
            merged.append((best[0], best[1], best[2], sum(x[3] for x in g)))
        if len(merged) == len(items):
            break
        items = merged
    out = []
    for v, lbl, t, n in items:
        if n > 1:
            lbl += f" (kept from {n} levels within 0.25 x ATR14({tf}) {fmt_price(atr)}: most touches ({touches(v, recent, tol)}), then most recent)"
        out.append((v, lbl, t))
    return out


def nearest(pool: list[Level], ref: float, below: bool, n: int, min_dist: float = 0.0) -> list[Level]:
    """The n levels nearest to ref on one side (strictly), at least min_dist away from it."""
    side = [x for x in pool if (ref - x[0] if below else x[0] - ref) > 0 and abs(ref - x[0]) >= min_dist]
    return sorted(side, key=lambda x: abs(ref - x[0]))[:n]


def scenario(side: str, price: float, cs: list[Candle], tf: str, atr: float | None, lows: list[Level],
             highs: list[Level]) -> dict[str, Any] | None:
    """One scenario per the module docstring (lows / highs already clustered), or None when it can't be derived."""
    if len(cs) < MIN_SCENARIO or not atr or price <= 0:
        return None
    long = side == "long"
    sign = 1 if long else -1
    pm = "+" if long else "-"
    f = fmt_price
    a = f"ATR14({tf}) {f(atr)}"
    res, sup = ("resistance", "support") if long else ("support", "resistance")
    ahead, behind = (highs, lows) if long else (lows, highs)
    near = nearest(ahead, price, not long, 1)
    if near:
        trigger, lbl, _ = near[0]
        trigger_rule = f"nearest {res} {'above' if long else 'below'} the price {f(price)}: {lbl} {f(trigger)}"
    else:
        trigger = max(c.h for c in cs[-20:]) if long else min(c.l for c in cs[-20:])
        trigger_rule = (f"no {res} {'above' if long else 'below'} the price: {'highest high' if long else 'lowest low'} "
                        f"of the last 20 closed {tf} candles {f(trigger)} ({'breakout' if long else 'breakdown'} level)")
    # invalidation: past the nearest opposite level that still leaves >= 1 ATR to the trigger after the 0.25 ATR buffer
    guard = nearest(behind, trigger, long, 1, min_dist=0.75 * atr)
    if guard:
        g, glbl, _ = guard[0]
        invalidation = g - sign * 0.25 * atr
        inv_rule = (f"nearest {sup} {'below' if long else 'above'} the trigger at least 0.75 x ATR away ({glbl} {f(g)}) "
                    f"{'minus' if long else 'plus'} 0.25 x {a} = {f(invalidation)} (>= 1 x ATR from the trigger)")
    else:
        invalidation = trigger - sign * 1.5 * atr
        inv_rule = (f"no {sup} at least 0.75 x ATR {'below' if long else 'above'} the trigger: "
                    f"trigger {'-' if long else '+'} 1.5 x {a} = {f(invalidation)}")
    if invalidation <= 0:
        return None
    r = abs(trigger - invalidation)
    t1_min = max(r, atr)
    parts: list[str] = []
    lv = nearest(ahead, trigger, not long, 1, min_dist=t1_min)
    if lv:
        t1 = lv[0][0]
        parts.append(f"T1 = {lv[0][1]} {f(t1)} (first {res} at least max(1R, 1 x ATR) = {f(t1_min)} beyond the trigger; "
                     f"R = |trigger - invalidation| = {f(r)})")
    else:
        d = max(2 * atr, r)
        t1 = trigger + sign * d
        how = f"2 x {a}" if d == 2 * atr else f"1R (R = |trigger - invalidation| = {f(r)})"
        parts.append(f"T1 = trigger {pm} {how} = {f(t1)} (no {res} at least max(1R, 1 x ATR) = {f(t1_min)} beyond the trigger)")
    lv = nearest(ahead, t1, not long, 1, min_dist=atr)
    if lv:
        t2 = lv[0][0]
        parts.append(f"T2 = {lv[0][1]} {f(t2)} (next {res} at least 1 x ATR beyond T1)")
    elif sign * (trigger + sign * 3 * atr - t1) >= atr:
        t2 = trigger + sign * 3 * atr
        parts.append(f"T2 = trigger {pm} 3 x {a} = {f(t2)} (no {res} at least 1 x ATR beyond T1)")
    else:
        t2 = t1 + sign * atr
        parts.append(f"T2 = T1 {pm} 1 x {a} = {f(t2)} (no {res} at least 1 x ATR beyond T1)")
    if t1 <= 0:
        return None
    targets, parts = ([t1, t2], parts) if t2 > 0 else ([t1], parts[:1])
    return {"trigger": rnd(trigger), "trigger_rule": trigger_rule, "invalidation": rnd(invalidation), "invalidation_rule": inv_rule,
            "targets": [rnd(t) for t in targets], "target_rule": "; ".join(parts)}


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
    raw_lows, raw_highs = level_pool(frames, timeframe)
    lows, highs = cluster(raw_lows, run_cs, atr, timeframe), cluster(raw_highs, run_cs, atr, timeframe)
    support = [rnd(v) for v, _, _ in nearest(lows, ref_price, True, 3)]
    resistance = [rnd(v) for v, _, _ in nearest(highs, ref_price, False, 3)]
    scen = {"long": scenario("long", ref_price, run_cs, timeframe, atr, lows, highs),
            "short": scenario("short", ref_price, run_cs, timeframe, atr, lows, highs)}
    if len(run_cs) < MIN_SCENARIO or not atr:
        lim.append(f"fewer than {MIN_SCENARIO} closed {timeframe} candles (or no ATR): scenarios can't be derived")
    lean_, score, why = lean(facts_frames, timeframe)
    return {"status": status, "limitations": lim,
            "facts": {"frames": facts_frames, "support": support, "resistance": resistance, "scenarios": scen},
            "lean": lean_, "score": score, "why": why}
