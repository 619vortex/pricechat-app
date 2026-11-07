"""The compact market readout under every chart: price, trend, the three main indicators (RSI, MACD, EMA 20/50), support /
resistance, the signal, one Kiwi Take and the update time. Every value is computed from the market's real candles (closed candles for the indicators) and its live
price; nothing is estimated.

Signal: on a Hyperliquid perpetual it is the published signal rules (signals.analyze, the same answer /signal gives); on
other markets those rules can't run (they need Hyperliquid's book, funding and volume), so the candle-only part of the
same rules decides and the line says "chart rules". WAIT means no setup passes every rule right now, with the first reason.

Kiwi Take: one observation picked by code from the numbers (no language model), never a recommendation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.tradingos import indicators as ind
from app.tradingos.market import Candle, MarketData, MarketError, fmt_price
from app.tradingos.signals import closed

MIN_CANDLES = 60  # for EMA 50 + RSI 14
RULE_CANDLES = 120  # what the signal rules need


@dataclass
class Readout:
    price: float
    change_24h: float | None
    trend: str  # Bullish | Bearish | Neutral
    rsi: float | None
    support: float | None
    resistance: float | None
    signal: str  # LONG | SHORT | WAIT
    signal_note: str  # why WAIT, or the setup in a few words
    rules: str  # "signal rules" | "chart rules"
    take: str
    updated: datetime
    indicators: tuple[str, ...] = ()  # the three main indicators in words (indicators.main_three_lines)
    plan: Any = None  # signals.Watch: the rules' setup (full) or the most promising idea; drawn on the chart and in the caption
    market: list[str] = field(default_factory=list)  # funding / OI / volume, when known


def _trend(closes: list[float]) -> tuple[str, list[float], list[float]]:
    e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
    if not (e20 and e50 and len(e50) > 6):
        return "Neutral", e20, e50
    price = closes[-1]
    if e20[-1] > e50[-1] and e50[-1] > e50[-6] and price > e50[-1]:
        return "Bullish", e20, e50
    if e20[-1] < e50[-1] and e50[-1] < e50[-6] and price < e50[-1]:
        return "Bearish", e20, e50
    return "Neutral", e20, e50


def chart_rules(c: list[Candle]) -> tuple[str, str]:
    """The candle-only checks of signals.analyze (trend, pullback, RSI, MACD, room to the next level). The market checks it
    can't make here (volume, spread, depth, funding, BTC, costs) are left out, which the caption's "chart rules" says."""
    if len(c) < RULE_CANDLES:
        return "WAIT", "not enough price history"
    closes, highs, lows = [x.c for x in c], [x.h for x in c], [x.l for x in c]
    trend, e20, e50 = _trend(closes)
    rsi, macd, atr, prev = ind.rsi(closes), ind.macd(closes), ind.atr(highs, lows, closes), ind.macd(closes[:-1])
    if not (rsi is not None and macd and atr):
        return "WAIT", "indicators unavailable"
    side = {"Bullish": "LONG", "Bearish": "SHORT"}.get(trend)
    if side is None:
        return "WAIT", "no clear trend"
    price = closes[-1]
    if abs(price - e20[-1]) / atr > 0.6:
        return "WAIT", "price stretched away from EMA20"
    lo, hi = (40, 65) if side == "LONG" else (35, 60)
    if not lo <= rsi <= hi:
        return "WAIT", f"RSI {rsi:.0f} out of range for a {side.lower()}"
    turning = prev is not None and ((side == "LONG" and macd[2] > prev[2]) or (side == "SHORT" and macd[2] < prev[2]))
    if not (turning or (side == "LONG" and macd[2] > 0) or (side == "SHORT" and macd[2] < 0)):
        return "WAIT", "momentum against the trend"
    support, resistance = ind.swing_levels(highs[:-1], lows[:-1], 48)
    sign = 1 if side == "LONG" else -1
    wall = resistance if side == "LONG" else support
    if 0 < sign * (wall - price) < 0.5 * atr:
        return "WAIT", f"{'resistance' if side == 'LONG' else 'support'} right ahead at {fmt_price(wall)}"
    return side, f"{'up' if side == 'LONG' else 'down'}trend pullback to EMA20"


def kiwi_take(price: float, trend: str, rsi: float | None, support: float | None, resistance: float | None, atr: float | None,
              e20: float | None, change_24h: float | None) -> str:
    """One short observation from the numbers, most notable first."""
    near = 0.5 * atr if atr else price * 0.004
    if resistance and 0 <= resistance - price <= near:
        return f"Pressing resistance at {fmt_price(resistance)}" + (f" with RSI {rsi:.0f}: momentum is stretched." if rsi and rsi >= 65 else ".")
    if support and 0 <= price - support <= near:
        return f"Sitting on support at {fmt_price(support)}" + (f" with RSI {rsi:.0f}: selling looks stretched." if rsi and rsi <= 35 else ".")
    if rsi is not None and rsi >= 70:
        return f"RSI {rsi:.0f}: overbought on this timeframe after the run-up."
    if rsi is not None and rsi <= 30:
        return f"RSI {rsi:.0f}: oversold on this timeframe after the drop."
    if e20 and atr and trend != "Neutral" and abs(price - e20) / atr <= 0.6:
        return f"{'Uptrend' if trend == 'Bullish' else 'Downtrend'} intact; price is back near the 20 EMA."
    if e20 and atr and trend != "Neutral":
        return f"{'Uptrend' if trend == 'Bullish' else 'Downtrend'} intact, {abs(price - e20) / atr:.1f} ATR from the 20 EMA."
    if change_24h is not None and abs(change_24h) >= 8:
        return f"{change_24h:+.1f}% in 24 hours: a big move for one day."
    if support and resistance:
        return f"Ranging between {fmt_price(support)} and {fmt_price(resistance)}."
    return "No clear direction on this timeframe."


def read(listing: Any, candles: list[Candle], tf: str, md: MarketData | None = None, settings: Any = None,
         signal: bool = True) -> Readout:
    """The readout for one market. `listing` is a venues.Listing; on Hyperliquid the signal comes from signals.analyze.
    `signal=False` ("fc"): the chart read only. The signal rules, chart rules and best plan are never run (signal "", plan None)."""
    c = closed(candles, tf)
    closes, highs, lows = [x.c for x in c], [x.h for x in c], [x.l for x in c]
    trend, e20, e50 = _trend(closes) if len(c) >= MIN_CANDLES else ("Neutral", [], [])
    rsi = ind.rsi(closes) if len(c) >= 15 else None
    atr = ind.atr(highs, lows, closes) if len(c) >= 15 else None
    support, resistance = ind.swing_levels(highs, lows, 48) if c else (None, None)
    from app.tradingos import signals

    price = listing.price or (candles[-1].c if candles else 0.0)
    market: list[str] = []
    if listing.venue == "hyperliquid" and md is not None:
        try:
            m = md.market(listing.symbol)
            market = [f"funding {m.funding * 100:+.4f}%/h", f"OI {usd_short(m.oi_usd)}", f"24h vol {usd_short(m.day_volume)}"]
        except MarketError:
            pass
    elif listing.volume_24h:
        market = [f"24h vol {usd_short(listing.volume_24h)}"]
    take = kiwi_take(price, trend, rsi, support, resistance, atr, e20[-1] if e20 else None, listing.change_24h)
    if not signal:
        return Readout(price=price, change_24h=listing.change_24h, trend=trend, rsi=round(rsi, 1) if rsi is not None else None, support=support,
                       resistance=resistance, signal="", signal_note="", rules="", take=take, updated=datetime.now(timezone.utc),
                       indicators=tuple(ind.main_three_lines(ind.main_three(closes))), plan=None, market=market)
    a = signals.Analysis(coin=listing.tag, decision="NO TRADE")
    if listing.venue == "hyperliquid" and md is not None:
        try:
            kw = {} if settings is None else {"min_volume": settings.min_volume_usd, "max_spread_bps": settings.max_spread_bps, "min_rr": settings.min_rr}
            a = signals.analyze(md, listing.symbol, tf, **kw)
            sig, note = ("WAIT", a.reasons[0] if a.reasons else "no setup") if a.decision == "NO TRADE" else (a.decision, "setup passes every rule")
            if a.indicators.get("support") is not None:  # the same levels the rules used
                support, resistance = a.indicators["support"], a.indicators["resistance"]
        except MarketError as e:
            sig, note = "WAIT", f"signal rules unavailable ({e})"
        rules = "signal rules"
    else:
        sig, note = chart_rules(c)
        rules = "chart rules"
    a.indicators = {**signals.chart_indicators(candles, tf), **a.indicators}
    plan = signals.best_plan(a)
    take = kiwi_take(price, trend, rsi, support, resistance, atr, e20[-1] if e20 else None, listing.change_24h)  # the rules' levels
    return Readout(price=price, change_24h=listing.change_24h, trend=trend, rsi=round(rsi, 1) if rsi is not None else None, support=support,
                   resistance=resistance, signal=sig, signal_note=note, rules=rules, take=take, updated=datetime.now(timezone.utc),
                   indicators=tuple(ind.main_three_lines(ind.main_three(closes))), plan=plan, market=market)


def tf_label(tf: str) -> str:
    return tf.upper() if tf[-1] in "hdw" else tf  # 4H, 1D, 15m (TradingView style: minutes stay lowercase)


def caption(listing: Any, tf: str, r: Readout, brief: bool = False) -> str:
    """The chart caption, Telegram HTML: price and market, the trend and the three main indicators, the levels, the signal
    and the plan (the rules' setup, or the most promising idea), one Kiwi Take. Fits a photo caption with the footer.
    `brief` ("fc sui"): the chart and the read only; the signal and plan are left to /signal, which the caption points to."""
    from app.tradingos.bot import esc

    def away(p: float) -> str:
        return f" ({(p - r.price) / r.price * 100:+.1f}%)" if r.price else ""
    chg = f" · {'🟢' if r.change_24h >= 0 else '🔻'} {r.change_24h:+.2f}% 24h" if r.change_24h is not None else ""
    trend = {"Bullish": "🟢 Bullish", "Bearish": "🔴 Bearish"}.get(r.trend, "⚪ Neutral")
    lines = [f"📊 <b>{esc(listing.tag)}</b> | {tf_label(tf)} | {esc(listing.label)}",
             f"💵 Price: <b>{fmt_price(r.price)}</b> {listing.quote}{chg}"]
    if r.market:
        lines.append("⛽ " + " · ".join(r.market))
    icons = ("📈 ", "〽️ ", "➰ ")
    lines += ["", f"🧭 Trend: {trend}",
              *(f"{icons[k] if k < 3 else ''}{x}" for k, x in enumerate(r.indicators or ([f"RSI: {r.rsi:.1f}"] if r.rsi is not None else ["RSI: n/a"]))),
              f"🧱 Support: {fmt_price(r.support)}{away(r.support)}" if r.support else "🧱 Support: n/a",
              f"🧱 Resistance: {fmt_price(r.resistance)}{away(r.resistance)}" if r.resistance else "🧱 Resistance: n/a", ""]
    if brief:  # "fc": the chart read only. No signal, side, entry, stop, target or R/R ever (that's /signal)
        lines += [f"🥝 Kiwi Take: {esc(r.take)}", f"🕒 Updated: {r.updated:%H:%M} UTC",
                  f"ℹ️ Trade analysis: /signal {esc(listing.symbol if listing.venue == 'hyperliquid' else listing.tag.lstrip('$'))}"]
        return "\n".join(lines)
    lines.append(f"🎯 Signal: {r.signal} ({r.rules})" + (f" · {esc(r.signal_note)}" if r.signal == "WAIT" else " · passes every rule"))
    p = r.plan
    if p is not None:
        lo, hi = p.entry
        head = f"{'🟢' if p.side == 'LONG' else '🔴'} Plan: {p.side} setup" if p.full else f"🟡 Best idea: {p.side} · {esc(p.idea)}"
        lines += [head, f"   Entry {fmt_price(lo)}–{fmt_price(hi)} · Stop {fmt_price(p.stop)} · Target {fmt_price(p.target)} · R/R 1:{p.rr:.1f}"]
    lines += [f"🥝 Kiwi Take: {esc(r.take)}", f"🕒 Updated: {r.updated:%H:%M} UTC"]
    return "\n".join(lines)


def usd_short(x: float | None) -> str:
    """$6.56B · $312K · $940 (volumes and liquidity at a glance; prices keep fmt_price)."""
    if x is None:
        return "n/a"
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:,.2f}{unit}" if abs(x) < 100 * div else f"${x / div:,.0f}{unit}"
    return f"${x:,.0f}"
