"""Signal Research Engine: deterministic, testable rules over real Hyperliquid data. It answers NO TRADE whenever any
condition fails, and says which. It does not try to produce many signals.

Strategy trend_pullback_v1 (on the configured timeframe, 1h by default):
  trend      EMA20 vs EMA50 aligned, EMA50 sloping the same way, price on the trend side of EMA50
  pullback   price within 0.6 ATR of EMA20 (buying a dip in an uptrend / selling a rally in a downtrend)
  momentum   RSI 40-65 for longs, 35-60 for shorts (not stretched); MACD histogram on the trend side or turning
             back towards it (the pullback ending)
  market     BTC above (longs) / below (shorts) its own EMA50 on the same timeframe (alts only)
  crowding   funding not heavily paying the trend side (longs pay > 0.01%/h: skip; shorts mirrored)
  liquidity  24h volume >= the minimum, impact spread <= the maximum, >= $100k depth within 0.5% each side
  volatility ATR between 0.25% and 5% of price
  levels     entry zone = price ± 0.2 ATR; stop 1.5 ATR beyond the zone's mid; target 3 ATR; the nearest swing
             high (longs) / low (shorts) must not sit within 0.5 ATR of the entry
  reward     risk/reward after estimated fees (0.045% taker each way) and the spread >= the minimum
  leverage   3-5x suggested, lowered so a 5x liquidation (with maintenance margin) stays beyond the stop

Strategy breakout_4h_v1 (always on CLOSED 4h candles; owner-approved, from the research in tradingos/refine.py):
  breakout   the last closed 4h candle closed beyond the previous 20 candles' high (longs) / low (shorts): a FRESH break only
  trend      that close is on the EMA50 side of the break (longs above EMA50, shorts below)
  liquidity, volatility and funding: the same filters as trend_pullback_v1
  levels     entry zone = price ± 0.2 ATR(4h); stop 2 ATR(4h) from the breakout close; target 2R (the research model used a
             trailing exit; the live system needs a fixed target, so this is an adaptation, not the backtested rule)
  research   about break-even after costs over 208 days (+0.01R to +0.03R a trade; the confidence interval includes zero)

Status 2026-10-16 (tradingos/lab.py, replayed with the LIVE trade management: half at TP1, rest at TP2, real funding):
  breakout_4h_v1      -0.09R a trade over 2.3 years (288 trades); selection -0.01R, last 11 months -0.20R
  trend_pullback_v1   -0.16R a trade over 208 days (395 trades), negative in every fold
  No pre-declared variant passed the out-of-sample rule, so both are SWITCHED OFF in the settings (the code stays, so the
  results can be re-checked; switch one back on only with a new strategy that passes lab.py's rule).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.trading import TrPosition
from app.models.tradingos import TsAudit, TsSettings, TsSignal, TsSignalAction
from app.tradingos import indicators as ind
from app.tradingos.market import MarketData, MarketError, fmt_price
from app.tradingos.tickers import cashtag

TAKER_FEE = 0.00045  # per side, Hyperliquid base tier
MIN_DEPTH_USD = 100_000  # liquidity: at least this much resting within 0.5% of the mid on each side of the book
EXPIRY = {"5m": timedelta(minutes=30), "15m": timedelta(hours=1), "1h": timedelta(hours=4), "4h": timedelta(hours=16)}
LIVE_FOR = timedelta(hours=48)
HOLD = {"5m": "30 min – 2 h", "15m": "1 – 4 h", "1h": "several hours to 2 days", "4h": "1 – 5 days"}  # from the backtests  # an ACTIVE signal that hits neither level is CLOSED at market after this
OPEN = ("WAITING", "ACTIVE")
BEFORE: dict[int, str] = {}  # signal id -> its status before the last change track() reported


@dataclass
class Analysis:
    coin: str
    decision: str  # LONG | SHORT | NO TRADE
    reasons: list[str] = field(default_factory=list)  # why (or why not)
    indicators: dict[str, Any] = field(default_factory=dict)
    entry: tuple[float, float] | None = None
    stop: float | None = None
    target: float | None = None
    lev: tuple[int, int] | None = None
    rr: float | None = None
    score: float = 0.0
    strategy: str = ""
    checks: list[dict[str, Any]] = field(default_factory=list)  # every rule evaluated: node, rule, ok (True/False/None), value


@dataclass
class Watch:
    """A plan on request: the best one the chart offers when no setup passes every rule (WATCH, lower conviction), or the
    rules' own setup (full=True)."""
    side: str
    entry: tuple[float, float]
    stop: float
    target: float
    rr: float
    idea: str  # the plan in a few words
    score: float = 0.0  # how promising, for picking between plans and between markets
    full: bool = False


def _plan(side: str, at: float, atr: float, level: float | None, idea: str, score: float) -> Watch:
    """Entry zone ±0.2 ATR around `at`, the stop past the swing `level` when it's close (else 1.5 ATR), the target at 2R."""
    sign = 1 if side == "LONG" else -1
    dist = sign * (at - level) if level else 0
    risk = max(dist + 0.3 * atr, 0.8 * atr) if 0 < dist < 2.5 * atr else 1.5 * atr
    return Watch(side, (at - 0.2 * atr, at + 0.2 * atr), at - sign * risk, at + sign * 2 * risk, 2.0, idea, score)


def watch_setup(a: Analysis) -> Watch | None:
    """Every plan the chart allows (trend pullback, range edges, a breakout at the level being pressed, plain momentum),
    each scored on trend, RSI, MACD, distance from the price and room to the next level; the most promising wins. Shown on
    request as a WATCH idea: never published, never traded. None without a price, or when every plan needs a price below zero."""
    i = a.indicators
    px, e20, e50 = i.get("price"), i.get("ema20"), i.get("ema50")
    if not px:
        return None
    atr = i.get("atr") or px * 0.015  # too little history for an ATR: a typical 1.5% swing
    sup, res, rsi, hist = i.get("support"), i.get("resistance"), i.get("rsi"), i.get("macd_hist")
    up = bool(e20 and e50 and e20 > e50 and px > e50)
    down = bool(e20 and e50 and e20 < e50 and px < e50)
    plans = []
    if up:
        plans.append(_plan("LONG", min(px, e20), atr, sup, "buy a pullback to EMA20 in the uptrend" if px > e20 else "buy the dip below EMA20 in the uptrend", 3))
        if res and 0 <= res - px < atr:
            plans.append(_plan("LONG", res + 0.1 * atr, atr, res, f"buy a break above resistance {fmt_price(res)}", 2.5))
    if down:
        plans.append(_plan("SHORT", max(px, e20), atr, res, "sell a bounce to EMA20 in the downtrend" if px < e20 else "sell the pop above EMA20 in the downtrend", 3))
        if sup and 0 <= px - sup < atr:
            plans.append(_plan("SHORT", sup - 0.1 * atr, atr, sup, f"sell a break below support {fmt_price(sup)}", 2.5))
    if sup and res and res > sup:
        plans.append(_plan("LONG", sup + 0.25 * atr, atr, sup, "buy the range low (support)", 2))
        plans.append(_plan("SHORT", res - 0.25 * atr, atr, res, "sell the range high (resistance)", 2))
    mom_down = hist < 0 if hist is not None else (rsi if rsi is not None else 50) < 50
    plans.append(_plan("SHORT", px, atr, res, "momentum points down", 1) if mom_down else _plan("LONG", px, atr, sup, "momentum points up", 1))
    # a wild chart (ATR a third of the price, e.g. a new DEX token on 1d) can put a short's target or a long's stop below zero
    plans = [w for w in plans if min(*w.entry, w.stop, w.target) > 0]
    if not plans:
        return None
    for w in plans:
        long = w.side == "LONG"
        mid = sum(w.entry) / 2
        if rsi is not None:
            w.score += 1 if (40 <= rsi <= 65 if long else 35 <= rsi <= 60) else -1.5 if (rsi > 70 if long else rsi < 30) else 0
        if hist is not None and (hist > 0) == long:
            w.score += 1
        if (long and down) or (not long and up):
            w.score -= 1.5  # against the trend
        w.score -= 0.3 * abs(mid - px) / atr  # a far entry may never fill
        wall = res if long else sup
        if wall and min(mid, w.target) < wall < max(mid, w.target):
            w.score -= 1  # the next level stands before the target
    return max(plans, key=lambda w: w.score)


def best_plan(a: Analysis) -> Watch | None:
    """The rules' setup when there is one (full), else the most promising WATCH idea."""
    if a.decision != "NO TRADE" and a.entry and a.stop and a.target:
        return Watch(a.decision, a.entry, a.stop, a.target, a.rr or 0.0, "passes every rule", 10 + a.score, True)
    return watch_setup(a)


def chart_indicators(candles: list[Any], timeframe: str) -> dict[str, Any]:
    """What watch_setup reads, from any market's candles (not only Hyperliquid, short histories too)."""
    cs = closed(candles, timeframe) or list(candles)
    if not cs:
        return {}
    closes, highs, lows = [c.c for c in cs], [c.h for c in cs], [c.l for c in cs]
    out: dict[str, Any] = {"price": closes[-1], "timeframe": timeframe, "atr": ind.atr(highs, lows, closes), "rsi": ind.rsi(closes),
                           "main3": ind.main_three(closes)}
    e20, e50, m = ind.ema(closes, 20), ind.ema(closes, 50), ind.macd(closes)
    if e20 and e50:
        out["ema20"], out["ema50"] = e20[-1], e50[-1]
    if m:
        out["macd_hist"] = m[2]
    if len(cs) > 5:
        out["support"], out["resistance"] = ind.swing_levels(highs[:-1], lows[:-1], 48)
    return out


def _checker(a: Analysis, fails: list[str]) -> Any:
    """Record one rule (pass or fail, with its measured value); a failing rule also adds its NO TRADE reason."""
    def chk(node: str, rule: str, ok: bool, value: str, fail: str) -> None:
        a.checks.append({"node": node, "rule": rule, "ok": bool(ok), "value": value})
        if not ok:
            fails.append(fail)
    return chk


def settings(db: Session) -> TsSettings:
    s = db.scalar(select(TsSettings).limit(1))
    if s is None:
        s = TsSettings()
        db.add(s)
        db.flush()
    return s


def audit(db: Session, actor: str, action: str, signal: TsSignal | None = None, **details: Any) -> None:
    db.add(TsAudit(actor=actor[:64], action=action[:48], signal_id=signal.id if signal else None, details=details))


def closed(candles: list[Any], timeframe: str) -> list[Any]:
    """Only candles that have CLOSED: Hyperliquid also returns the one still forming, and deciding on it lets a signal
    appear (or vanish) before the candle is final."""
    from app.tradingos.market import INTERVALS

    now_ms = utcnow().timestamp() * 1000
    span = INTERVALS.get(timeframe, 3600) * 1000
    return [c for c in candles if c.t + span <= now_ms]


def analyze(md: MarketData, coin: str, timeframe: str = "1h", min_volume: float = 50_000_000, max_spread_bps: float = 8.0,
            min_rr: float = 1.6) -> Analysis:
    """One market, one decision. Every number comes from Hyperliquid; any failed condition means NO TRADE."""
    m = md.market(coin)
    a = Analysis(coin=m.coin, decision="NO TRADE", strategy="trend_pullback_v1")
    candles = closed(md.candles(m.coin, timeframe, 201), timeframe)[-200:]
    if len(candles) < 120:
        a.reasons.append("not enough price history")
        return a
    closes, highs, lows = [c.c for c in candles], [c.h for c in candles], [c.l for c in candles]
    price = closes[-1]
    e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
    rsi, macd, atr = ind.rsi(closes), ind.macd(closes), ind.atr(highs, lows, closes)
    macd_prev = ind.macd(closes[:-1])
    if not (e20 and e50 and rsi is not None and macd and atr):
        a.reasons.append("indicators unavailable")
        return a
    support, resistance = ind.swing_levels(highs[:-1], lows[:-1], 48)
    book = md.book(m.coin)
    a.indicators = {"price": price, "ema20": e20[-1], "ema50": e50[-1], "rsi": round(rsi, 1), "macd_hist": macd[2], "atr": atr,
                    "atr_pct": atr / price * 100, "funding_h": m.funding, "volume_24h": m.day_volume, "oi_usd": m.oi_usd,
                    "spread_bps": book["spread_bps"], "impact_spread_bps": m.spread_bps, "depth_bid": book["bid_depth_usd"],
                    "depth_ask": book["ask_depth_usd"], "support": support, "resistance": resistance, "timeframe": timeframe,
                    "main3": ind.main_three(closes)}
    up = e20[-1] > e50[-1] and e50[-1] > e50[-6] and price > e50[-1]
    down = e20[-1] < e50[-1] and e50[-1] < e50[-6] and price < e50[-1]
    side = "LONG" if up else "SHORT" if down else None
    fails: list[str] = []
    chk = _checker(a, fails)
    chk("trend", "EMA20/EMA50 aligned, EMA50 sloping, price on the trend side", side is not None,
        f"EMA20 {fmt_price(e20[-1])} · EMA50 {fmt_price(e50[-1])} · price {fmt_price(price)}", "no clear trend (EMA20/EMA50 not aligned)")
    chk("market", "24h volume at least the minimum", m.day_volume >= min_volume, f"${m.day_volume / 1e6:,.0f}M (min ${min_volume / 1e6:,.0f}M)",
        f"24h volume ${m.day_volume / 1e6:,.0f}M below ${min_volume / 1e6:,.0f}M")
    spread = max(book["spread_bps"], m.spread_bps or 0)
    chk("orderflow", "spread at most the maximum", spread <= max_spread_bps, f"{spread:.1f} bps (max {max_spread_bps:g})", f"spread {spread:.1f} bps too wide")
    depth = min(book["bid_depth_usd"], book["ask_depth_usd"])
    chk("orderflow", "at least $100k depth within 0.5% each side", depth >= MIN_DEPTH_USD, f"${depth / 1e3:,.0f}k on the thinner side", "thin order book")
    atr_pct = atr / price * 100
    chk("market", "volatility (ATR) between 0.25% and 5% of price", 0.25 <= atr_pct <= 5, f"{atr_pct:.2f}%", f"volatility {atr_pct:.2f}% out of range")
    if side:
        dist = abs(price - e20[-1]) / atr
        chk("trend", "pullback: price within 0.6 ATR of EMA20", dist <= 0.6, f"{dist:.2f} ATR away", "price stretched away from EMA20 (no pullback)")
        lo_, hi_ = (40, 65) if side == "LONG" else (35, 60)
        chk("momentum", f"RSI {lo_}–{hi_} for a {side.lower()}", lo_ <= rsi <= hi_, f"RSI {rsi:.1f}", f"RSI {rsi:.0f} out of range for a {side.lower()}")
        turning = macd_prev is not None and ((side == "LONG" and macd[2] > macd_prev[2]) or (side == "SHORT" and macd[2] < macd_prev[2]))
        ok_macd = turning or (side == "LONG" and macd[2] > 0) or (side == "SHORT" and macd[2] < 0)
        chk("momentum", "MACD histogram on the trend side or turning back to it", ok_macd,
            f"hist {macd[2]:.4g} (prev {macd_prev[2]:.4g})" if macd_prev else f"hist {macd[2]:.4g}", "MACD momentum against the trend")
        crowded = side == "LONG" and m.funding > 0.0001 or side == "SHORT" and m.funding < -0.0001
        chk("orderflow", "funding not heavily paying the trade's side", not crowded, f"{m.funding * 100:.4f}%/h", "funding shows a crowded trade")
        if m.coin != "BTC":
            btc = closed(md.candles("BTC", timeframe, 121), timeframe)
            bc = [c.c for c in btc]
            b50 = ind.ema(bc, 50)
            if b50:
                agree = not (side == "LONG" and bc[-1] < b50[-1] or side == "SHORT" and bc[-1] > b50[-1])
                chk("market", "BTC on the same side of its EMA50", agree, f"BTC {fmt_price(bc[-1])} vs EMA50 {fmt_price(b50[-1])}", "BTC trend disagrees")
    else:
        a.checks.append({"node": "momentum", "rule": "RSI / MACD (checked once there is a trend)", "ok": None, "value": f"RSI {rsi:.1f} · MACD hist {macd[2]:.4g}"})
    if fails:
        a.reasons = fails
        return a

    assert side is not None
    mid = price
    zone = (mid - 0.2 * atr, mid + 0.2 * atr)
    sign = 1 if side == "LONG" else -1
    stop = mid - sign * 1.5 * atr
    target = mid + sign * 3.0 * atr
    wall = resistance if side == "LONG" else support
    near = 0 < sign * (wall - mid) < 0.5 * atr
    chk("signal", f"no {'resistance' if side == 'LONG' else 'support'} within 0.5 ATR of the entry", not near, f"nearest {fmt_price(wall)}",
        f"{'resistance' if side == 'LONG' else 'support'} at {fmt_price(wall)} within 0.5 ATR")
    if fails:
        a.reasons = fails
        return a
    cost = mid * (2 * TAKER_FEE) + mid * spread / 10_000
    rr = (abs(target - mid) - cost) / (abs(mid - stop) + cost)
    chk("signal", f"risk/reward at least {min_rr} after costs", rr >= min_rr, f"{rr:.2f}", f"risk/reward {rr:.2f} below {min_rr} after costs")
    if fails:
        a.reasons = fails
        return a
    stop_pct = abs(mid - stop) / mid
    lev_hi = min(5, m.max_leverage)
    while lev_hi > 1 and (1 / lev_hi) * 0.8 <= stop_pct * 1.5:  # keep the estimated liquidation well beyond the stop
        lev_hi -= 1
    lev_lo = min(3, lev_hi)
    chk("signal", "liquidation at 2x+ stays well beyond the stop", lev_hi >= 2, f"stop {stop_pct * 100:.2f}% away, max {lev_hi}x",
        "stop too wide for leveraged entry")
    if fails:
        a.reasons = fails
        return a
    a.decision, a.entry, a.stop, a.target, a.rr, a.lev = side, zone, stop, target, round(rr, 2), (lev_lo, lev_hi)
    a.reasons = [f"{'Up' if side == 'LONG' else 'Down'}trend on {timeframe} (closed candles): EMA20 {'above' if side == 'LONG' else 'below'} "
                 f"EMA50, pullback to EMA20, RSI {rsi:.0f}, momentum turning back, BTC aligned."]
    a.score = rr + abs(e20[-1] - e50[-1]) / atr + min(m.day_volume / 1e9, 2)
    return a


def analyze_breakout(md: MarketData, coin: str, min_volume: float = 50_000_000, max_spread_bps: float = 8.0, min_rr: float = 1.6) -> Analysis:
    """breakout_4h_v1 for one market (see the module docstring). Any failed condition means NO TRADE."""
    m = md.market(coin)
    a = Analysis(coin=m.coin, decision="NO TRADE", strategy="breakout_4h_v1")
    candles = closed(md.candles(m.coin, "4h", 121), "4h")[-120:]
    if len(candles) < 60:
        a.reasons.append("not enough 4h history")
        return a
    closes, highs, lows = [c.c for c in candles], [c.h for c in candles], [c.l for c in candles]
    price, atr, e50 = closes[-1], ind.atr(highs, lows, closes), ind.ema(closes, 50)
    if not (atr and e50):
        a.reasons.append("indicators unavailable")
        return a
    hi20, lo20 = max(highs[-21:-1]), min(lows[-21:-1])
    book = md.book(m.coin)
    spread = max(book["spread_bps"], m.spread_bps or 0)
    a.indicators = {"price": price, "ema50": e50[-1], "atr": atr, "atr_pct": atr / price * 100, "high20": hi20, "low20": lo20,
                    "funding_h": m.funding, "volume_24h": m.day_volume, "spread_bps": spread, "timeframe": "4h", "main3": ind.main_three(closes)}
    side = "LONG" if price > hi20 and price > e50[-1] else "SHORT" if price < lo20 and price < e50[-1] else None
    fails: list[str] = []
    chk = _checker(a, fails)
    chk("trend", "closed beyond the 20-candle high/low on the EMA50 side", side is not None,
        f"close {fmt_price(price)} · high20 {fmt_price(hi20)} · low20 {fmt_price(lo20)} · EMA50 {fmt_price(e50[-1])}",
        "no fresh 4h breakout in the trend direction")
    chk("market", "24h volume at least the minimum", m.day_volume >= min_volume, f"${m.day_volume / 1e6:,.0f}M (min ${min_volume / 1e6:,.0f}M)",
        f"24h volume ${m.day_volume / 1e6:,.0f}M below ${min_volume / 1e6:,.0f}M")
    chk("orderflow", "spread at most the maximum", spread <= max_spread_bps, f"{spread:.1f} bps (max {max_spread_bps:g})", f"spread {spread:.1f} bps too wide")
    depth = min(book["bid_depth_usd"], book["ask_depth_usd"])
    chk("orderflow", "at least $100k depth within 0.5% each side", depth >= MIN_DEPTH_USD, f"${depth / 1e3:,.0f}k on the thinner side", "thin order book")
    vp = atr / price * 100
    chk("market", "4h volatility (ATR) between 0.4% and 8%", 0.4 <= vp <= 8, f"{vp:.2f}%", f"4h volatility {vp:.2f}% out of range")
    crowded = side == "LONG" and m.funding > 0.0001 or side == "SHORT" and m.funding < -0.0001
    chk("orderflow", "funding not heavily paying the trade's side", not crowded, f"{m.funding * 100:.4f}%/h", "funding shows a crowded trade")
    if fails:
        a.reasons = fails
        return a
    assert side is not None
    sign = 1 if side == "LONG" else -1
    stop = price - sign * 2.0 * atr
    target = price + sign * 2.0 * abs(price - stop)
    cost = price * (2 * TAKER_FEE) + price * spread / 10_000
    rr = (abs(target - price) - cost) / (abs(price - stop) + cost)
    chk("signal", f"risk/reward at least {min_rr} after costs", rr >= min_rr, f"{rr:.2f}", f"risk/reward {rr:.2f} below {min_rr} after costs")
    if fails:
        a.reasons = fails
        return a
    stop_pct = abs(price - stop) / price
    lev_hi = min(5, m.max_leverage)
    while lev_hi > 1 and (1 / lev_hi) * 0.8 <= stop_pct * 1.5:
        lev_hi -= 1
    chk("signal", "liquidation at 2x+ stays well beyond the stop", lev_hi >= 2, f"stop {stop_pct * 100:.2f}% away, max {lev_hi}x",
        "stop too wide for leveraged entry")
    if fails:
        a.reasons = fails
        return a
    a.decision, a.entry, a.stop, a.target, a.rr, a.lev = side, (price - 0.2 * atr, price + 0.2 * atr), stop, target, round(rr, 2), (min(3, lev_hi), lev_hi)
    a.reasons = [f"4h breakout (closed candle): closed {'above the 20-candle high' if side == 'LONG' else 'below the 20-candle low'} "
                 f"{fmt_price(hi20 if side == 'LONG' else lo20)}, {'above' if side == 'LONG' else 'below'} EMA50. Stop 2 ATR, target 2R."]
    a.score = rr + min(m.day_volume / 1e9, 2)
    a.indicators["strategy"] = "breakout_4h_v1"
    return a


def manual_signal(db: Session, md: MarketData, coin: str, actor: str) -> TsSignal:
    """The owner's manual push (Admin → Trading → Push a signal now): a REAL signal, the same real prices and ATR levels as the
    pipeline check below, long / short from the 1h trend. Unlike a test it counts in the track record, and members with AUTO
    on enter it within their own limits in their own mode (PAPER, TESTNET or LIVE). The card says "Manual setup"."""
    sig = test_signal(db, md, coin, actor)
    sig.strategy = MANUAL
    sig.reason = "Manual setup by Price Chat."
    db.commit()
    audit(db, actor, "manual_signal", sig, coin=sig.coin, side=sig.side)
    db.commit()
    return sig


MANUAL = "manual"


def test_signal(db: Session, md: MarketData, coin: str, actor: str) -> TsSignal:
    """A TEST signal to check the pipeline end to end (chart, Telegram card, website sync, status tracking). Real price and
    real ATR-based levels, side from the EMA20/EMA50 trend, but labelled TEST everywhere and excluded from performance."""
    m = md.market(coin)
    candles = md.candles(m.coin, "1h", 120)
    closes, highs, lows = [c.c for c in candles], [c.h for c in candles], [c.l for c in candles]
    atr = ind.atr(highs, lows, closes) or closes[-1] * 0.01
    e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
    side = "LONG" if e20 and e50 and e20[-1] >= e50[-1] else "SHORT"
    sign, mid = (1 if side == "LONG" else -1), closes[-1]
    a = Analysis(coin=m.coin, decision=side, entry=(mid - 0.2 * atr, mid + 0.2 * atr), stop=mid - sign * 1.5 * atr,
                 target=mid + sign * 3 * atr, lev=(3, min(5, m.max_leverage)), rr=2.0,
                 reasons=["TEST: pipeline check (real prices and ATR levels), not a trade idea."], indicators={"price": mid, "atr": atr})
    sig = create(db, a, "1h", "WAITING", actor)
    sig.strategy = "test"
    db.commit()
    return sig


def next_number(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.max(TsSignal.number), 0))) or 0) + 1


def scan(db: Session, md: MarketData, actor: str = "scanner") -> dict[str, Any]:
    """Analyze the top markets; store the best candidates (up to the open-signal limit) as PENDING_REVIEW, or publish-ready
    WAITING when auto-publish is on. Returns a summary with every NO TRADE reason.

    SHADOW mode: when every strategy is switched off (but the scanner isn't paused), the markets are still evaluated with the
    same rules and every evaluation is logged for the Kiwi Brain, labelled shadow, but nothing is ever created or traded."""
    s = settings(db)
    out: dict[str, Any] = {"checked": 0, "created": [], "no_trade": {}}
    pullback_on, breakout_on = bool(s.strategies.get("trend_pullback_v1")), bool(s.strategies.get("breakout_4h_v1"))
    shadow = not (pullback_on or breakout_on)
    if s.paused or not s.scanner_enabled:
        out["skipped"] = "paused" if s.paused else "scanner off"
        return out
    out["shadow"] = shadow
    if shadow:
        pullback_on = breakout_on = True  # evaluated for the record only
    open_now = db.scalars(select(TsSignal).where(TsSignal.status.in_((*OPEN, "PENDING_REVIEW")))).all()
    room = s.max_open_signals - len(open_now)
    busy = {x.coin for x in open_now}
    allowed = [c.strip().upper() for c in s.allowed_coins.split(",") if c.strip()]
    coins = allowed or [m.coin for m in md.top(s.universe_size, s.min_volume_usd)]
    found: list[Analysis] = []
    from app.tradingos import brain

    for coin in coins:
        if coin in busy and not shadow:
            continue
        out["checked"] += 1
        why: list[str] = []
        for on, run in ((pullback_on, lambda c: analyze(md, c, s.timeframe, s.min_volume_usd, s.max_spread_bps, s.min_rr)),
                        (breakout_on, lambda c: analyze_breakout(md, c, s.min_volume_usd, s.max_spread_bps, s.min_rr))):
            if not on:
                continue
            try:
                a = run(coin)
            except MarketError as e:
                why.append(str(e))
                continue
            if shadow:  # every evaluation, rule by rule, for the record
                brain.log(db, "strategy", f"{a.coin}: {a.strategy} (shadow) → {a.decision}"
                          + (f" · {'; '.join(a.reasons)[:150]}" if a.decision == "NO TRADE" else ""), coin=a.coin, source="hyperliquid:info",
                          data={"decision": a.decision, "strategy": a.strategy, "shadow": True, "reasons": a.reasons, "checks": a.checks,
                                "indicators": a.indicators, "rr": a.rr, "entry": list(a.entry) if a.entry else None, "stop": a.stop, "target": a.target})
                continue
            if a.decision == "NO TRADE":
                why += a.reasons
            else:
                found.append(a)
                break  # one signal per coin
        if why and not shadow and not any(f.coin.upper() == coin.upper() for f in found):
            out["no_trade"][coin] = list(dict.fromkeys(why))  # both strategies can give the same reason: say it once
            brain.log(db, "strategy", f"{coin}: no trade · {'; '.join(out['no_trade'][coin])[:180]}", coin=coin, source="hyperliquid:info",
                      data={"decision": "NO TRADE", "reasons": out["no_trade"][coin],
                            "strategies": [k for k, on in (("trend_pullback_v1", pullback_on), ("breakout_4h_v1", breakout_on)) if on]})
    for a in sorted(found, key=lambda x: -x.score)[:max(0, room)]:
        bo = a.indicators.get("strategy") == "breakout_4h_v1"
        sig = create(db, a, "4h" if bo else s.timeframe, "PENDING_REVIEW" if not s.auto_publish else "WAITING", actor,
                     strategy="breakout_4h_v1" if bo else "trend_pullback_v1")
        out["created"].append(sig.number)
    db.commit()
    return out


def create(db: Session, a: Analysis, timeframe: str, status: str, actor: str, strategy: str = "trend_pullback_v1") -> TsSignal:
    assert a.entry and a.stop and a.target and a.lev and a.rr
    now = utcnow()
    sig = TsSignal(number=next_number(db), coin=a.coin, side=a.decision, timeframe=timeframe, strategy=strategy,
                   entry_lo=min(a.entry), entry_hi=max(a.entry), stop=a.stop, target=a.target, lev_lo=a.lev[0], lev_hi=a.lev[1],
                   rr=a.rr, status=status, reason=" ".join(a.reasons), analysis=a.indicators,
                   expires_at=now + EXPIRY.get(timeframe, timedelta(hours=4)), last_price=a.indicators.get("price"), last_checked_at=now)
    db.add(sig)
    db.flush()
    audit(db, actor, "signal_created", sig, coin=a.coin, side=a.decision, status=status)
    from app.tradingos import brain

    brain.log(db, "strategy", f"{a.coin}: {strategy} → {a.decision} proposed", signal_id=sig.id, coin=a.coin, source="hyperliquid:info",
              data={"decision": a.decision, "strategy": strategy, "timeframe": timeframe, "reasons": a.reasons, "indicators": a.indicators, "checks": a.checks,
                    "score": round(a.score, 3), "entry": [sig.entry_lo, sig.entry_hi], "stop": sig.stop, "target": sig.target, "rr": sig.rr})
    brain.log(db, "signal", f"Signal #{sig.number:03d} {a.coin} {a.decision} created ({status})", signal_id=sig.id, coin=a.coin, source=actor,
              data={"status": status, "strategy": strategy})
    return sig


def _aware(d: datetime | None) -> datetime | None:
    from datetime import timezone

    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def settle(sig: TsSignal, status: str, price: float) -> None:
    """Close a signal at `price`: the model result, unlevered, after estimated round-trip fees."""
    entry = (sig.entry_lo + sig.entry_hi) / 2
    move = (price - entry) / entry * (1 if sig.side == "LONG" else -1)
    sig.status, sig.exit_price, sig.closed_at = status, price, utcnow()
    sig.model_pnl_pct = round((move - 2 * TAKER_FEE) * 100, 3)


def open_at_market(sig: TsSignal, price: float) -> None:
    """A market-entry signal opens at `price`: the whole plan moves with it (same stop and target distances), so the
    record, the card and the result all use the price it really opened at."""
    delta = price - (sig.entry_lo + sig.entry_hi) / 2
    sig.entry_lo, sig.entry_hi, sig.stop, sig.target = sig.entry_lo + delta, sig.entry_hi + delta, sig.stop + delta, sig.target + delta
    sig.status = "ACTIVE"


def track(db: Session, md: MarketData) -> list[TsSignal]:
    """Move signals along WAITING → ACTIVE → TARGET HIT / STOP HIT / CLOSED, or EXPIRED, from real 1-minute candles.
    Returns the signals whose status changed (so their Telegram cards can be updated)."""
    changed = []
    now = utcnow()
    for sig in db.scalars(select(TsSignal).where(TsSignal.status.in_((*OPEN, "PENDING_REVIEW")))):
        try:
            candles = md.candles(sig.coin, "1m", 60)
        except MarketError:
            continue  # stale-data protection: no update without real prices
        since = _aware(sig.last_checked_at) or _aware(sig.created_at) or now
        recent = [c for c in candles if c.t / 1000 >= since.timestamp() - 60] or candles[-1:]
        sig.last_price, sig.last_checked_at = candles[-1].c, now
        before = sig.status
        long = sig.side == "LONG"
        if sig.status in ("PENDING_REVIEW", "WAITING"):
            if now > _aware(sig.expires_at):
                sig.status, sig.closed_at = "EXPIRED", now
            elif sig.status == "WAITING" and sig.published_at is None:
                pass  # not posted yet: it can't open before its card (and the ORDER UP announcement) exists
            elif sig.status == "WAITING" and isinstance(sig.analysis, dict) and sig.analysis.get("market_entry"):
                open_at_market(sig, candles[-1].c)  # degen mode: opened at market as soon as it is published
                sig.activated_at = now
            elif sig.status == "WAITING" and any(c.l <= sig.entry_hi and c.h >= sig.entry_lo for c in recent):
                sig.status, sig.activated_at = "ACTIVE", now
        if sig.status == "ACTIVE":
            for c in recent:  # stop checked first within a candle: the conservative assumption
                if (long and c.l <= sig.stop) or (not long and c.h >= sig.stop):
                    settle(sig, "STOP_HIT", sig.stop)
                    break
                if (long and c.h >= sig.target) or (not long and c.l <= sig.target):
                    settle(sig, "TARGET_HIT", sig.target)
                    break
            else:
                hold = sig.analysis.get("max_hold_min") if isinstance(sig.analysis, dict) else None  # 15m signals: their own max hold
                if now - (_aware(sig.activated_at) or now) > (timedelta(minutes=int(hold)) if hold else LIVE_FOR):
                    settle(sig, "CLOSED", candles[-1].c)
        if sig.status != before:
            BEFORE[sig.id] = before  # for the open/close announcement
            audit(db, "tracker", "status", sig, before=before, after=sig.status, price=sig.last_price)
            from app.tradingos import brain

            brain.log(db, "signal", f"Signal #{sig.number:03d} {sig.coin}: {STATUS_LABEL.get(before, before)} → {STATUS_LABEL.get(sig.status, sig.status)}"
                      f" @ {fmt_price(sig.last_price or 0)}", signal_id=sig.id, coin=sig.coin, source="hyperliquid:info candleSnapshot 1m",
                      data={"before": before, "after": sig.status, "price": sig.last_price, "model_pnl_pct": sig.model_pnl_pct})
            changed.append(sig)
    db.commit()
    return changed


MIN_BACKERS_FOR_USD = 3  # below this a dollar total would come close to showing one member's position size


def participation(db: Session, sig: TsSignal) -> dict[str, int]:
    """How many verified Kiwis pressed each button, plus who really has a filled position, real money and practice apart (counts
    and a rounded total only; never who). live_open_usd is the LIVE notional still open, to the nearest $100, and only once
    MIN_BACKERS_FOR_USD accounts hold it."""
    rows = db.execute(select(TsSignalAction.action, func.count()).where(TsSignalAction.signal_id == sig.id, TsSignalAction.verified.is_(True))
                      .group_by(TsSignalAction.action)).all()
    out = {a: int(n) for a, n in rows}
    live = practice = holders = 0
    open_usd = 0.0
    for mode, status, size, entry in db.execute(select(TrPosition.mode, TrPosition.status, TrPosition.size, TrPosition.entry_px).where(
            TrPosition.signal_id == sig.id, TrPosition.entry_px.is_not(None), TrPosition.status.in_(("OPEN", "PROTECTION_FAILED", "CLOSED")))):
        if mode != "LIVE":
            practice += 1
            continue
        live += 1
        if status != "CLOSED" and size > 0:
            holders += 1
            open_usd += size * entry
    out |= {"live": live, "practice": practice}
    if holders >= MIN_BACKERS_FOR_USD and open_usd >= 100:
        out["live_open_usd"] = int(round(open_usd, -2))
    return out


def stats(db: Session) -> dict[str, Any]:
    """Model signal performance from completed signals, losers included. Labelled as model results, not executed trades."""
    done = db.scalars(select(TsSignal).where(TsSignal.status.in_(("TARGET_HIT", "STOP_HIT", "CLOSED")), TsSignal.published_at.is_not(None),
                                             TsSignal.strategy != "test")
                      .order_by(TsSignal.closed_at)).all()
    pnl = [s.model_pnl_pct or 0.0 for s in done]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    equity, peak, mdd = 0.0, 0.0, 0.0
    for p in pnl:
        equity += p
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    real = (TsSignal.published_at.is_not(None), TsSignal.strategy != "test")
    total = db.scalar(select(func.count()).select_from(TsSignal).where(*real)) or 0
    open_n = db.scalar(select(func.count()).select_from(TsSignal).where(TsSignal.status.in_(OPEN), *real)) or 0
    return {"label": "Model signal results (unlevered, after estimated fees): not executed trades", "total": int(total), "open": int(open_n),
            "closed": len(done), "win_rate": round(len(wins) / len(done) * 100, 1) if done else None,
            "avg_gain": round(sum(wins) / len(wins), 3) if wins else None, "avg_loss": round(sum(losses) / len(losses), 3) if losses else None,
            "net_return": round(sum(pnl), 3), "max_drawdown": round(mdd, 3)}


STATUS_LABEL = {"PENDING_REVIEW": "IN REVIEW", "WAITING": "WAITING", "ACTIVE": "ACTIVE", "TARGET_HIT": "TARGET HIT", "STOP_HIT": "STOP HIT",
                "CLOSED": "CLOSED", "EXPIRED": "EXPIRED", "CANCELLED": "CANCELLED"}


def crowd_line(counts: dict[str, int]) -> str | None:
    """Filled entries (manual or AUTO), real money and practice never added together."""
    live, practice = counts.get("live", 0), counts.get("practice", 0)
    parts = []
    if live:
        usd = f" (≈${counts['live_open_usd']:,} open)" if counts.get("live_open_usd") else ""
        parts.append(f"{live} Kiwi{'s' if live != 1 else ''} with real money{usd}")
    if practice:
        parts.append(f"{practice} practising")
    return "👥 " + " · ".join(parts) if parts else None


def card(sig: TsSignal, counts: dict[str, int] | None = None, record: str | None = None) -> str:
    """The Telegram card: a compact trade ticket. Every number is the signal record's; the % are distances from the entry
    zone's middle. A manual (owner) signal says so instead of showing strategy reasons it doesn't have."""
    mid = (sig.entry_lo + sig.entry_hi) / 2
    tp2 = mid + (sig.target - mid) * 1.5  # risk.tp2_of from the zone's middle

    def pct(px: float) -> str:
        return f"{(px - mid) / mid * 100:+.1f}%"

    icon = "🟢" if sig.side == "LONG" else "🔴"
    lev = f"{sig.lev_lo}x" if sig.lev_lo == sig.lev_hi else f"{sig.lev_lo}–{sig.lev_hi}x"
    lines = [f"{icon} {cashtag(sig.coin)} {sig.side} · #{sig.number:03d}", "",
             f"🎯 Entry  {fmt_price(sig.entry_lo)} – {fmt_price(sig.entry_hi)}",
             f"🛑 Stop   {fmt_price(sig.stop)}  ({pct(sig.stop)})",
             f"✅ TP1    {fmt_price(sig.target)}  ({pct(sig.target)}) · half",
             f"✅ TP2    {fmt_price(tp2)}  ({pct(tp2)})",
             "", f"⚡ {lev} · R/R 1:{sig.rr:.1f} · hold {HOLD.get(sig.timeframe, 'hours')}"]
    main3 = ind.main_three_lines((sig.analysis or {}).get("main3"))
    if main3:  # the three main indicators at the moment the signal was made
        lines += ["", "📈 Indicators", *(f"· {x}" for x in main3)]
    if sig.strategy in ("test", MANUAL):
        lines.append("✍️ Manual setup")
    elif sig.reason:
        lines.append(f"💬 {sig.reason.replace(' (closed candles)', '')}"[:220])
    status = STATUS_LABEL.get(sig.status, sig.status)
    lines += ["", f"📍 {status}" + (f" · now {fmt_price(sig.last_price)}" if sig.last_price else "")]
    if sig.model_pnl_pct is not None:
        lines.append(f"🏁 Result {sig.model_pnl_pct:+.2f}% (no leverage, after fees)")
    if record:
        lines.append(record)
    if counts and (crowd := crowd_line(counts)):
        lines.append(crowd)
    lines += ["", "Price Chat · not financial advice"]
    return "\n".join(lines)
