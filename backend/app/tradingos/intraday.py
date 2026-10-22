"""Intraday strategy engine (research; not live until the owner switches a validated strategy on).

One set of pure functions used by BOTH the backtester and (when enabled) the live scanner, so what's tested is what runs:

  series(candles)          causal indicator series: value i only uses candles 0..i (no look-ahead)
  regime(h1, h4, j, k)     the market regime from CLOSED 1h / 4h candles
  setups(f, i, ctx)        entry models evaluated on the CLOSED 15m candle i; each returns a full trade plan
                           (side, structural stop, R-based targets, max hold) or a NO TRADE reason

Everything works on closed candles only: the live scanner must drop the forming candle before calling these.
Strategies (each switchable, none on by default):
  A pullback   trend pullback continuation (strong 1h trend, 4h agrees; dip to EMA20/VWAP; momentum resumes)
  B retest     breakout and retest of a 15m consolidation (relative-volume breakout, held retest)
  C sweep      liquidity sweep and reversal at a 15m swing level (wick beyond, close back inside, volume)
  D momentum   momentum continuation (trend, 12-bar breakout with volume, not exhausted, not extended)
  E range      range mean reversion (1h range regime only; boundary rejection with RSI extreme; target VWAP/mid)
  F scalp      momentum scalp (15m 2h-range burst with volume, 15m + 1h trend; tight stop, 90 min max hold)
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.tradingos.market import Candle

STRATEGIES = ("A_pullback", "B_retest", "C_sweep", "D_momentum", "E_range", "F_scalp")
LABEL = {"A_pullback": "Trend pullback", "B_retest": "Breakout retest", "C_sweep": "Liquidity sweep reversal",
         "D_momentum": "Momentum continuation", "E_range": "Range mean reversion",
         "F_scalp": "Momentum scalp"}


# ---------------------------------------------------------------- causal series
def ema_s(v: Sequence[float], p: int) -> list[float | None]:
    out: list[float | None] = [None] * len(v)
    if len(v) < p:
        return out
    k = 2 / (p + 1)
    x = sum(v[:p]) / p
    out[p - 1] = x
    for i in range(p, len(v)):
        x = v[i] * k + x * (1 - k)
        out[i] = x
    return out


def rsi_s(c: Sequence[float], p: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(c)
    if len(c) <= p:
        return out
    g = l_ = 0.0
    for i in range(1, p + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0)
        l_ += max(-d, 0)
    g, l_ = g / p, l_ / p
    out[p] = 100 - 100 / (1 + g / l_) if l_ else 100.0
    for i in range(p + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (p - 1) + max(d, 0)) / p
        l_ = (l_ * (p - 1) + max(-d, 0)) / p
        out[i] = 100 - 100 / (1 + g / l_) if l_ else 100.0
    return out


def atr_adx_s(h: Sequence[float], lo: Sequence[float], c: Sequence[float], p: int = 14) -> tuple[list[float | None], list[float | None]]:
    """Wilder ATR and ADX series."""
    n = len(c)
    atr: list[float | None] = [None] * n
    adx: list[float | None] = [None] * n
    if n <= 2 * p:
        return atr, adx
    tr, pdm, mdm = [0.0] * n, [0.0] * n, [0.0] * n
    for i in range(1, n):
        tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
        up, dn = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        mdm[i] = dn if dn > up and dn > 0 else 0.0
    a, sp, sm = sum(tr[1:p + 1]), sum(pdm[1:p + 1]), sum(mdm[1:p + 1])
    atr[p] = a / p
    dxs: list[float] = []
    ax = None
    for i in range(p + 1, n):
        a, sp, sm = a - a / p + tr[i], sp - sp / p + pdm[i], sm - sm / p + mdm[i]
        atr[i] = a / p
        pdi, mdi = (100 * sp / a if a else 0), (100 * sm / a if a else 0)
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0
        if ax is None:
            dxs.append(dx)
            if len(dxs) == p:
                ax = sum(dxs) / p
                adx[i] = ax
        else:
            ax = (ax * (p - 1) + dx) / p
            adx[i] = ax
    return atr, adx


@dataclass
class Series:
    t: list[int]
    o: list[float]
    h: list[float]
    l: list[float]  # noqa: E741
    c: list[float]
    v: list[float]
    e20: list[float | None]
    e50: list[float | None]
    e200: list[float | None]
    rsi: list[float | None]
    atr: list[float | None]
    adx: list[float | None]
    vwap: list[float]  # session VWAP (UTC day)
    relvol: list[float | None]  # volume / mean of the previous 20 volumes
    interval_ms: int


def series(cs: Sequence[Candle], interval_ms: int) -> Series:
    t, o, h, lo, c, v = ([x.t for x in cs], [x.o for x in cs], [x.h for x in cs], [x.l for x in cs], [x.c for x in cs], [x.v for x in cs])
    atr, adx = atr_adx_s(h, lo, c)
    vw: list[float] = []
    day, pv, vv = -1, 0.0, 0.0
    for i in range(len(c)):
        d = t[i] // 86_400_000
        if d != day:
            day, pv, vv = d, 0.0, 0.0
        tp = (h[i] + lo[i] + c[i]) / 3
        pv, vv = pv + tp * v[i], vv + v[i]
        vw.append(pv / vv if vv else c[i])
    rv: list[float | None] = [None] * len(c)
    for i in range(20, len(c)):
        m = sum(v[i - 20:i]) / 20
        rv[i] = v[i] / m if m else None
    return Series(t, o, h, lo, c, v, ema_s(c, 20), ema_s(c, 50), ema_s(c, 200), rsi_s(c), atr, adx, vw, rv, interval_ms)


def last_closed(s: Series, at_ms: int) -> int:
    """Index of the last candle of `s` that had CLOSED by time `at_ms` (-1 if none). Binary search."""
    lo, hi, ans = 0, len(s.t) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if s.t[mid] + s.interval_ms <= at_ms:
            ans, lo = mid, mid + 1
        else:
            hi = mid - 1
    return ans


# ---------------------------------------------------------------- regime
REGIMES = ("STRONG_BULL", "STRONG_BEAR", "WEAK_TREND", "RANGE", "VOL_EXPANSION", "VOL_CONTRACTION", "UNSTABLE")


def regime(h1: Series, h4: Series, j: int, k: int) -> tuple[str, dict[str, Any]]:
    """Classify from the closed 1h candle j and closed 4h candle k: EMA 20/50/200 alignment and slope, ADX, ATR percentile,
    the last candle's range (shock), and the 4h trend."""
    if j < 210 or k < 55 or None in (h1.e20[j], h1.e50[j], h1.e200[j], h1.adx[j], h1.atr[j], h1.e50[j - 5], h4.e50[k]):
        return "UNSTABLE", {"why": "not enough history"}
    e20, e50, e200, adx, atr = h1.e20[j], h1.e50[j], h1.e200[j], h1.adx[j], h1.atr[j]
    assert e20 and e50 and e200 and adx is not None and atr
    slope = (e50 - (h1.e50[j - 5] or e50)) / atr
    pct = [x / h1.c[i] for i in range(j - 99, j + 1) if (x := h1.atr[i])]
    now = atr / h1.c[j]
    rank = sum(1 for x in pct if x <= now) / len(pct) if pct else 0.5
    shock = (h1.h[j] - h1.l[j]) > 3 * atr
    up4 = h4.c[k] > (h4.e50[k] or h4.c[k])
    info = {"adx": round(adx, 1), "slope50_atr": round(slope, 2), "atr_pct_rank": round(rank, 2), "atr_pct": round(now * 100, 3),
            "ema_stack": "bull" if e20 > e50 > e200 else "bear" if e20 < e50 < e200 else "mixed", "h4": "up" if up4 else "down"}
    if shock or rank > 0.95:
        return "UNSTABLE", info
    if e20 > e50 > e200 and slope > 0.3 and adx >= 22 and up4:
        return "STRONG_BULL", info
    if e20 < e50 < e200 and slope < -0.3 and adx >= 22 and not up4:
        return "STRONG_BEAR", info
    if adx < 18 and abs(slope) < 0.15:
        return "RANGE", info
    if rank > 0.8:
        return "VOL_EXPANSION", info
    if rank < 0.2:
        return "VOL_CONTRACTION", info
    return "WEAK_TREND", info


# ---------------------------------------------------------------- setups (on the closed 15m candle i)
@dataclass
class Setup:
    strategy: str
    side: str
    ref: float  # the closed candle's close (entry is at the next candle's open, never this close)
    stop: float
    r1: float  # TP1 in R
    r2: float  # TP2 in R
    max_hold_min: int
    reasons: list[str] = field(default_factory=list)
    quality: dict[str, float] = field(default_factory=dict)  # the score's factors (interpretable)

    def targets(self, entry: float) -> tuple[float, float]:
        r = abs(entry - self.stop)
        sgn = 1 if self.side == "LONG" else -1
        return entry + sgn * self.r1 * r, entry + sgn * self.r2 * r


def _bull_close(f: Series, i: int) -> bool:
    rng = f.h[i] - f.l[i]
    return f.c[i] > f.o[i] and rng > 0 and (f.c[i] - f.l[i]) / rng >= 0.6


def _bear_close(f: Series, i: int) -> bool:
    rng = f.h[i] - f.l[i]
    return f.c[i] < f.o[i] and rng > 0 and (f.h[i] - f.c[i]) / rng >= 0.6


MIN_STOP_PCT = 0.0025  # module default; setups(min_stop_pct=...) overrides it


def _stop_ok(ref: float, stop: float, atr: float, min_pct: float = MIN_STOP_PCT) -> bool:
    """A practical stop: between 0.5 and 2.5 ATR(15m) away, and at least `min_pct` of price (so costs stay small next to the risk)."""
    d = abs(ref - stop)
    return 0.5 * atr <= d <= 2.5 * atr and d / ref >= min_pct


def setups(f: Series, i: int, reg: str, h1_dir: str, enabled: Sequence[str] = STRATEGIES,
           min_stop_pct: float = MIN_STOP_PCT) -> tuple[list[Setup], list[str]]:
    """Every enabled entry model on the closed 15m candle i. Returns (setups, reasons for no trade)."""
    out: list[Setup] = []
    why: list[str] = []
    if i < 210 or None in (f.e20[i], f.e50[i], f.atr[i], f.rsi[i], f.relvol[i], f.e20[i - 3]):
        return out, ["not enough 15m history"]
    if reg == "UNSTABLE":
        return out, ["unstable market (volatility shock)"]
    e20, e50, atr, rsi, rv = f.e20[i], f.e50[i], f.atr[i], f.rsi[i], f.relvol[i]
    assert e20 and e50 and atr and rsi is not None and rv is not None
    c = f.c[i]
    vw = f.vwap[i]
    ext = abs(c - e20) / atr  # extension from EMA20 in ATRs

    # A: trend pullback continuation
    if "A_pullback" in enabled:
        if reg in ("STRONG_BULL", "STRONG_BEAR"):
            long = reg == "STRONG_BULL"
            trend15 = (e20 > e50) if long else (e20 < e50)
            touched = any((f.l[x] <= max(f.e20[x] or 0, f.vwap[x]) + 0.1 * atr) if long else
                          (f.h[x] >= min(f.e20[x] or 1e18, f.vwap[x]) - 0.1 * atr) for x in range(i - 3, i + 1))
            resumed = (_bull_close(f, i) and c > e20) if long else (_bear_close(f, i) and c < e20)
            rsi_ok = 45 <= rsi <= 68 if long else 32 <= rsi <= 55
            if trend15 and touched and resumed and rsi_ok and ext <= 1.0:
                sw = min(f.l[i - 5:i + 1]) if long else max(f.h[i - 5:i + 1])
                stop = sw - 0.2 * atr if long else sw + 0.2 * atr
                if _stop_ok(c, stop, atr, min_stop_pct):
                    out.append(Setup("A_pullback", "LONG" if long else "SHORT", c, stop, 1.0, 2.0, 180,
                                     [f"{'Up' if long else 'Down'}trend on 1h/4h ({reg.replace('_', ' ').lower()}), 15m pulled back to "
                                      f"EMA20/VWAP and closed back {'above' if long else 'below'} it with a strong candle, RSI {rsi:.0f}"],
                                     {"htf": 1.0, "regime": 1.0, "relvol": min(rv, 3) / 3, "ext": 1 - ext}))
                else:
                    why.append("A: stop distance impractical")
            else:
                why.append("A: no pullback-and-resume")
        else:
            why.append(f"A: needs a strong trend ({reg})")

    # B: breakout and retest of a 24-bar consolidation, broken within the last 2-8 bars
    if "B_retest" in enabled:
        hit = False
        for b in range(i - 8, i - 1):
            lo_r, hi_r = min(f.l[b - 24:b]), max(f.h[b - 24:b])
            a_b = f.atr[b] or atr
            if hi_r - lo_r > 4 * a_b:
                continue  # not a consolidation
            rvb = f.relvol[b] or 0
            for long in (True, False):
                lvl = hi_r if long else lo_r
                broke = (f.c[b] > lvl and _bull_close(f, b)) if long else (f.c[b] < lvl and _bear_close(f, b))
                if not broke or rvb < 1.5:
                    continue
                if long and h1_dir == "down" or not long and h1_dir == "up":
                    continue  # against the 1h trend
                held = all((f.c[x] > lvl - 0.1 * atr) if long else (f.c[x] < lvl + 0.1 * atr) for x in range(b + 1, i + 1))
                retest = (f.l[i] <= lvl + 0.25 * atr and c > lvl and _bull_close(f, i)) if long else \
                         (f.h[i] >= lvl - 0.25 * atr and c < lvl and _bear_close(f, i))
                if held and retest:
                    stop = min(f.l[i], lvl) - 0.3 * atr if long else max(f.h[i], lvl) + 0.3 * atr
                    if _stop_ok(c, stop, atr, min_stop_pct):
                        out.append(Setup("B_retest", "LONG" if long else "SHORT", c, stop, 1.0, 2.0, 180,
                                         [f"15m broke a {24 * 15 // 60}h range {'high' if long else 'low'} at {lvl:.6g} on {rvb:.1f}x volume, "
                                          f"then retested and held it"], {"htf": 1.0 if h1_dir in ("up", "down") else 0.5, "regime": 0.7,
                                                                           "relvol": min(rvb, 3) / 3, "ext": 1 - min(ext, 1)}))
                        hit = True
                        break
            if hit:
                break
        if not hit:
            why.append("B: no breakout-retest")

    # C: liquidity sweep of the 48-bar swing level and close back inside
    if "C_sweep" in enabled:
        swing_hi, swing_lo = max(f.h[i - 48:i - 2]), min(f.l[i - 48:i - 2])
        took = False
        for long in (True, False):
            lvl = swing_lo if long else swing_hi
            swept = (f.l[i] < lvl - 0.1 * atr and c > lvl and _bull_close(f, i)) if long else (f.h[i] > lvl + 0.1 * atr and c < lvl and _bear_close(f, i))
            against = (reg == "STRONG_BEAR" and long) or (reg == "STRONG_BULL" and not long)
            if swept and rv >= 1.3 and not against:
                stop = f.l[i] - 0.2 * atr if long else f.h[i] + 0.2 * atr
                if _stop_ok(c, stop, atr, min_stop_pct):
                    out.append(Setup("C_sweep", "LONG" if long else "SHORT", c, stop, 1.5, 2.5, 180,
                                     [f"15m swept the {48 * 15 // 60}h {'low' if long else 'high'} ({lvl:.6g}) and closed back inside on "
                                      f"{rv:.1f}x volume"], {"htf": 0.5, "regime": 0.7, "relvol": min(rv, 3) / 3, "ext": 1 - min(ext, 1)}))
                    took = True
        if not took:
            why.append("C: no sweep")

    # D: momentum continuation
    if "D_momentum" in enabled:
        if reg in ("STRONG_BULL", "STRONG_BEAR"):
            long = reg == "STRONG_BULL"
            brk = c > max(f.h[i - 12:i]) if long else c < min(f.l[i - 12:i])
            rsi_ok = 55 <= rsi <= 72 if long else 28 <= rsi <= 45
            if brk and rv >= 1.5 and rsi_ok and ext <= 1.2 and ((_bull_close(f, i)) if long else _bear_close(f, i)):
                stop = max(e20 - 0.3 * atr, c - 1.5 * atr) if long else min(e20 + 0.3 * atr, c + 1.5 * atr)
                if _stop_ok(c, stop, atr, min_stop_pct):
                    out.append(Setup("D_momentum", "LONG" if long else "SHORT", c, stop, 1.0, 2.0, 120,
                                     [f"{reg.replace('_', ' ').lower()}: 15m broke its 3h {'high' if long else 'low'} on {rv:.1f}x volume, "
                                      f"RSI {rsi:.0f} (not exhausted)"], {"htf": 1.0, "regime": 1.0, "relvol": min(rv, 3) / 3, "ext": 1 - min(ext, 1)}))
                else:
                    why.append("D: stop distance impractical")
            else:
                why.append("D: no clean momentum breakout")
        else:
            why.append(f"D: needs a strong trend ({reg})")

    # E: range mean reversion (only in a 1h range)
    if "E_range" in enabled:
        if reg == "RANGE":
            hi_r, lo_r = max(f.h[i - 48:i]), min(f.l[i - 48:i])
            for long in (True, False):
                at_edge = (f.l[i] <= lo_r + 0.2 * atr and rsi <= 35 and _bull_close(f, i)) if long else \
                          (f.h[i] >= hi_r - 0.2 * atr and rsi >= 65 and _bear_close(f, i))
                if at_edge:
                    stop = min(f.l[i], lo_r) - 0.3 * atr if long else max(f.h[i], hi_r) + 0.3 * atr
                    target = vw if (long and vw > c) or (not long and vw < c) else (hi_r + lo_r) / 2
                    r = abs(c - stop)
                    r1 = abs(target - c) / r if r else 0
                    if _stop_ok(c, stop, atr, min_stop_pct) and r1 >= 1.0:
                        out.append(Setup("E_range", "LONG" if long else "SHORT", c, stop, min(r1, 1.5), min(r1 * 1.5, 2.5), 180,
                                         [f"1h range (ADX low); 15m rejected the range {'low' if long else 'high'} with RSI {rsi:.0f}; "
                                          f"target VWAP / range middle"], {"htf": 0.5, "regime": 1.0, "relvol": min(rv, 3) / 3, "ext": 0.5}))
            if not out or out[-1].strategy != "E_range":
                why.append("E: no range-edge rejection")
        else:
            why.append(f"E: needs a range ({reg})")

    # F: momentum scalp (the "degen" model): a 15m candle that bursts out of its 2h range with volume, with the 15m trend
    # and the 1h direction, short hold. Fires far more often than A-E; the risk is kept small instead (tight stop, 90 min).
    if "F_scalp" in enabled:
        took = False
        rising, falling = e20 > (f.e20[i - 3] or e20), e20 < (f.e20[i - 3] or e20)
        for long in (True, False):
            trend = (c > e20 > e50 and rising and h1_dir == "up") if long else (c < e20 < e50 and falling and h1_dir == "down")
            brk = c > max(f.h[i - 8:i]) if long else c < min(f.l[i - 8:i])
            rsi_ok = 52 <= rsi <= 78 if long else 22 <= rsi <= 48
            strong = _bull_close(f, i) if long else _bear_close(f, i)
            if not (trend and brk and rsi_ok and strong and rv >= 1.2 and ext <= 2.0):
                continue
            room = min(max(c - f.l[i] if long else f.h[i] - c, 0.8 * atr), 1.6 * atr) + 0.1 * atr
            stop = c - room if long else c + room
            if _stop_ok(c, stop, atr, min_stop_pct):
                out.append(Setup("F_scalp", "LONG" if long else "SHORT", c, stop, 1.0, 1.8, 90,
                                 [f"15m burst {'above' if long else 'below'} its 2h {'high' if long else 'low'} on {rv:.1f}x volume, with the "
                                  f"15m and 1h trend, RSI {rsi:.0f}"], {"htf": 1.0, "regime": 0.7, "relvol": min(rv, 3) / 3, "ext": 1 - min(ext, 2) / 2}))
                took = True
        if not took:
            why.append("F: no momentum burst")
    return out, why


def quality(s: Setup, spread_bps: float, cost_r: float) -> int:
    """0-100 signal quality: an interpretable ranking (HTF alignment, regime fit, relative volume, not extended, cost vs risk).
    Equal weights, NOT fitted and NOT a probability of winning (the backtest reports outcomes by score bucket)."""
    q = s.quality
    parts = [q.get("htf", 0.5), q.get("regime", 0.5), q.get("relvol", 0.3), max(0.0, q.get("ext", 0.5)),
             max(0.0, 1 - cost_r / 0.25), max(0.0, 1 - spread_bps / 8)]
    return round(100 * sum(parts) / len(parts))
