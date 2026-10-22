"""Candlestick charts as PNG (for Telegram and the website): real candles, EMA 20/50, and optional entry zone, stop
loss and take profit overlays. Pure drawing: the numbers come from the candles and the signal record passed in."""

import io
import math
from datetime import datetime, timezone
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.tradingos.indicators import ema
from app.tradingos.market import Candle, fmt_price

W, H = 1200, 680
PAD_L, PAD_R, PAD_T, PAD_B = 20, 136, 70, 50
BG, GRID, TEXT, MUTED = (8, 11, 10), (30, 36, 33), (232, 236, 233), (130, 140, 135)
UP, DOWN = (0, 229, 143), (255, 92, 92)
EMA20, EMA50 = (155, 92, 255), (255, 196, 0)
ENTRY, STOP, TARGET = (110, 170, 255), (255, 92, 92), (0, 229, 143)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def render(candles: list[Candle], title: str, entry: tuple[float, float] | None = None, stop: float | None = None,
           target: float | None = None, subtitle: str = "") -> bytes:
    if len(candles) < 2:
        raise ValueError("Not enough candles to draw a chart.")
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    closes = [c.c for c in candles]
    levels = [x for x in (stop, target, *(entry or ())) if x]
    lo = min(min(c.l for c in candles), *levels) if levels else min(c.l for c in candles)
    hi = max(max(c.h for c in candles), *levels) if levels else max(c.h for c in candles)
    span = (hi - lo) or hi * 0.01
    lo, hi = lo - span * 0.05, hi + span * 0.05
    x0, x1, y0, y1 = PAD_L, W - PAD_R, PAD_T, H - PAD_B
    y = lambda p: y1 - (p - lo) / (hi - lo) * (y1 - y0)  # noqa: E731
    step = (x1 - x0) / len(candles)

    # grid + price axis
    for i in range(6):
        p = lo + (hi - lo) * i / 5
        yy = y(p)
        d.line([(x0, yy), (x1, yy)], fill=GRID)
        d.text((x1 + 8, yy - 8), fmt_price(p), fill=MUTED, font=_font(15))

    # entry zone / stop / target
    if entry:
        d.rectangle([x0, y(max(entry)), x1, y(min(entry))], fill=(18, 34, 58))
    for level, colour, label in ((stop, STOP, "SL"), (target, TARGET, "TP"), (entry[0] if entry else None, ENTRY, "ENTRY")):
        if level:
            yy = y(level)
            for xx in range(int(x0), int(x1), 14):
                d.line([(xx, yy), (min(xx + 8, x1), yy)], fill=colour, width=2)
            d.rectangle([x1 + 2, yy - 11, W - 4, yy + 11], fill=colour)
            d.text((x1 + 6, yy - 9), f"{label} {fmt_price(level)}", fill=BG, font=_font(14))

    # candles
    body = max(1.0, step * 0.62)
    for i, c in enumerate(candles):
        cx = x0 + step * (i + 0.5)
        colour = UP if c.c >= c.o else DOWN
        d.line([(cx, y(c.h)), (cx, y(c.l))], fill=colour, width=1)
        top, bot = y(max(c.o, c.c)), y(min(c.o, c.c))
        d.rectangle([cx - body / 2, top, cx + body / 2, max(bot, top + 1)], fill=colour)

    # EMAs
    for period, colour in ((20, EMA20), (50, EMA50)):
        e = ema(closes, period)
        if e:
            pts = [(x0 + step * (i + 0.5), y(v)) for i, v in enumerate(e) if i >= period - 1]
            if len(pts) > 1:
                d.line(pts, fill=colour, width=2)

    # header / footer
    last = candles[-1]
    d.text((PAD_L, 14), title, fill=TEXT, font=_font(28))
    d.text((PAD_L, 46), subtitle or f"Last {fmt_price(last.c)}", fill=MUTED, font=_font(16))
    d.text((x1 - 220, 20), "EMA20", fill=EMA20, font=_font(15))
    d.text((x1 - 150, 20), "EMA50", fill=EMA50, font=_font(15))
    stamp = datetime.fromtimestamp(last.t / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    d.text((PAD_L, H - 32), f"Hyperliquid data · {stamp} · Price Chat", fill=MUTED, font=_font(14))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- /chart with options: price + optional panels
RSI_C, MACD_C, SIG_C, BB_C, OI_C = (110, 170, 255), (110, 170, 255), (255, 196, 0), (120, 130, 160), (0, 200, 230)
FUND_UP, FUND_DN = (255, 160, 60), (90, 160, 255)
EMA100, EMA200 = (255, 120, 200), (240, 240, 240)
CVD_C, AGG_C, LIQ_L, LIQ_S, WALL_C, TWAP_C = (240, 240, 240), (0, 200, 230), (255, 92, 92), (0, 229, 143), (255, 196, 0), (190, 120, 255)
LEVEL_S, LEVEL_R = (0, 170, 110), (220, 90, 90)


def _label(d: ImageDraw.ImageDraw, at: tuple[float, float], text: str, colour: tuple[int, int, int]) -> None:
    """A small label on a dark plate, readable over candles."""
    f = _font(15)
    d.rectangle([at[0] - 3, at[1] - 1, at[0] + d.textlength(text, font=f) + 3, at[1] + 18], fill=BG)
    d.text(at, text, fill=colour, font=f)


def volume_panel(panels: list[tuple[str, Any]]) -> bool:
    return any(k == "volume" for k, _ in panels)


PANEL_H = {"volume": 120, "rsi": 150, "macd": 160, "funding": 140, "oi": 150, "cvd": 160, "aggoi": 160, "liq": 140}
PANEL_NAME = {"volume": "Volume", "rsi": "RSI 14", "macd": "MACD 12/26/9", "funding": "Funding %/h", "oi": "Open interest (Price Chat record)",
              "cvd": "CVD · taker buys - sells", "aggoi": "Open interest · aggregated", "liq": "Liquidations · reported"}


def _heat_colour(k: float) -> tuple[int, int, int, int]:
    """0..1 -> faint navy .. strong yellow (resting liquidity)."""
    k = max(0.0, min(1.0, k))
    stops = ((0.0, (20, 30, 90)), (0.5, (40, 160, 170)), (1.0, (255, 230, 80)))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if k <= b:
            f = (k - a) / (b - a)
            return (int(ca[0] + (cb[0] - ca[0]) * f), int(ca[1] + (cb[1] - ca[1]) * f), int(ca[2] + (cb[2] - ca[2]) * f), int(30 + 190 * k))
    return (255, 230, 80, 220)


def _blend(img: Image.Image, layer: Image.Image) -> ImageDraw.ImageDraw:
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"))
    return ImageDraw.Draw(img)


def _usd(v: float) -> str:
    a, sign = abs(v), "-" if v < 0 else ""
    return sign + (f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}")


def _line(d: ImageDraw.ImageDraw, pts: list[tuple[float, float | None]], colour: tuple[int, int, int], width: int = 2) -> None:
    """A line through the points; None breaks it (no value yet)."""
    run: list[tuple[float, float]] = []
    for x, yv in [*pts, (0.0, None)]:
        if yv is None:
            if len(run) > 1:
                d.line(run, fill=colour, width=width)
            run = []
        else:
            run.append((x, yv))


def render_study(candles: list[Candle], title: str, subtitle: str, *, show: int, interval_ms: int, wide: bool = False, ma: bool = False,
                 bb: bool = False, panels: list[tuple[str, Any]] | None = None,
                 depth: dict[str, list[tuple[float, float]]] | None = None, heat: list[Any] | None = None,
                 zones: list[dict[str, Any]] | None = None, walls: list[dict[str, Any]] | None = None,
                 twap: list[dict[str, Any]] | None = None, sources: list[str] | None = None, source: str = "Hyperliquid",
                 levels: tuple[float | None, float | None] | None = None, plan: Any = None, change: float | None = None) -> bytes:
    """Candles with EMA 20/50 (+ EMA 100/200 with `ma`, Bollinger Bands with `bb`), an order-book depth profile on the right
    (`depth`), and stacked panels below: volume, RSI, MACD, funding (hourly rates, ms), open interest (ms, USD), CVD
    (cvd: structure.cvd()), aggregated OI (aggoi: structure.open_interest()) and reported liquidations (liq). On the price
    panel: a liquidity heatmap behind the candles (`heat`: structure.heatmap() snapshots), ESTIMATED liquidation zones on
    the right edge (`zones`, labelled as estimates), liquidity walls (`walls`) and TWAP-like runs (`twap`, labelled inferred).
    Indicators are computed over all the candles given and the last `show` are drawn. Large text for phone screens.
    Always: the last price as a tagged line on the axis. With `levels` (support, resistance) dashed level lines, with `plan`
    (signals.Watch) the entry zone, stop and target on the right, with `change` the 24h move as a coloured badge."""
    from app.tradingos import indicators as ind

    if len(candles) < 2:
        raise ValueError("Not enough candles to draw a chart.")
    panels = panels or []
    closes = [c.c for c in candles]
    vis = candles[-show:]
    off = len(candles) - len(vis)
    w = 1600 if wide else 1200
    price_h = 560 if wide else 520
    top_pad, gap, foot = 92, 14, 46
    foot += 22 * len(sources or [])
    h = top_pad + price_h + sum(PANEL_H[k] + gap for k, _ in panels) + foot
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    depth_w = 170 if depth else 0
    x0, x1 = PAD_L, w - PAD_R - depth_w
    xr = x1  # the right edge of the price panel; with a plan, the candles stop short of it and the plan gets the room
    if plan is not None:
        x1 = x0 + (xr - x0) * 0.8
    step = (x1 - x0) / len(vis)
    t0, t1 = vis[0].t, vis[-1].t + interval_ms

    def cx(i: int) -> float:
        return x0 + step * (i + 0.5)

    def xt(t: float) -> float:
        return x0 + (t - t0) / (t1 - t0) * (x1 - x0)

    bands = ind.bollinger_series(closes)[off:] if bb else []
    lo, hi = min(c.l for c in vis), max(c.h for c in vis)
    for b in bands:
        if b:
            lo, hi = min(lo, b[2]), max(hi, b[1])
    if plan is not None:  # the whole plan stays in view
        lo, hi = min(lo, plan.stop, plan.target, *plan.entry), max(hi, plan.stop, plan.target, *plan.entry)
    span = (hi - lo) or hi * 0.01
    lo, hi = lo - span * 0.04, hi + span * 0.04
    py0, py1 = top_pad, top_pad + price_h

    def y(p: float) -> float:
        return py1 - (p - lo) / (hi - lo) * (py1 - py0)

    for i in range(6):
        p = lo + (hi - lo) * i / 5
        d.line([(x0, y(p)), (xr + depth_w, y(p))], fill=GRID)
        d.text((w - PAD_R + 8, y(p) - 9), fmt_price(p), fill=MUTED, font=_font(17))
    if heat:  # recorded resting liquidity: each snapshot paints its bins until the next one (at most 6 minutes)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        vals = sorted(u for _, _, _, bins in heat for px, u in bins if lo <= px <= hi)
        ref = vals[int(len(vals) * 0.97)] if vals else 0.0
        for i, (t, _mid, stp, bins) in enumerate(heat):
            nt = heat[i + 1][0] if i + 1 < len(heat) else t + 120_000
            xa, xb = max(x0, xt(t)), min(x1, xt(min(nt, t + 360_000)))
            if xb <= xa or not ref:
                continue
            for px, u in bins:
                if lo <= px <= hi:
                    ld.rectangle([xa, y(px + stp), xb, y(px)], fill=_heat_colour(math.log1p(u) / math.log1p(ref)))
        d = _blend(img, layer)
    if bands:
        for k, width in ((1, 2), (2, 2), (0, 1)):
            _line(d, [(cx(i), y(b[k]) if b else None) for i, b in enumerate(bands)], BB_C, width)
    wm = _font(96 if wide else 80)  # a faint mark behind the candles
    d.text(((x0 + x1) / 2, (py0 + py1) / 2), "PRICE CHAT", fill=(16, 21, 19), font=wm, anchor="mm")
    if not heat and not volume_panel(panels):  # a light volume profile along the bottom of the price panel
        vmax = max((c.v for c in vis), default=0) or 1.0
        for i, c in enumerate(vis):
            vh = (py1 - py0) * 0.14 * c.v / vmax
            d.rectangle([cx(i) - step * 0.31, py1 - vh, cx(i) + step * 0.31, py1], fill=(16, 52, 38) if c.c >= c.o else (60, 24, 24))
    plan_x = x1 + 4
    if plan is not None:  # entry zone, stop and target over the last part of the chart
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        e_lo, e_hi = min(plan.entry), max(plan.entry)
        ld.rectangle([plan_x, y(e_hi), xr, y(e_lo)], fill=(*ENTRY, 70))
        ld.rectangle([plan_x, min(y(plan.target), y(e_hi)), xr, max(y(plan.target), y(e_hi))] if plan.side == "LONG" else
                     [plan_x, min(y(plan.target), y(e_lo)), xr, max(y(plan.target), y(e_lo))], fill=(*TARGET, 34))
        ld.rectangle([plan_x, min(y(plan.stop), y(e_lo)), xr, max(y(plan.stop), y(e_lo))] if plan.side == "LONG" else
                     [plan_x, min(y(plan.stop), y(e_hi)), xr, max(y(plan.stop), y(e_hi))], fill=(*STOP, 34))
        d = _blend(img, layer)
    tags: list[tuple[tuple[float, float], str, tuple[int, int, int]]] = []  # labels drawn last, over the candles
    if levels:  # support / resistance as dashed lines, labelled on the left
        for px_, colour, name in ((levels[0], LEVEL_S, "S"), (levels[1], LEVEL_R, "R")):
            if px_ and lo <= px_ <= hi:
                for xx in range(int(x0), int(xr), 16):
                    d.line([(xx, y(px_)), (xx + 8, y(px_))], fill=colour, width=1)
                tags.append(((x0 + 6, y(px_) - 20), f"{name} {fmt_price(px_)}", colour))
    body = max(1.0, step * 0.62)
    for i, c in enumerate(vis):
        colour = UP if c.c >= c.o else DOWN
        d.line([(cx(i), y(c.h)), (cx(i), y(c.l))], fill=colour, width=1)
        top, bot = y(max(c.o, c.c)), y(min(c.o, c.c))
        d.rectangle([cx(i) - body / 2, top, cx(i) + body / 2, max(bot, top + 1)], fill=colour)
    legend: list[tuple[str, tuple[int, int, int]]] = []
    for period, colour, on in ((20, EMA20, True), (50, EMA50, True), (100, EMA100, ma), (200, EMA200, ma)):
        e = ind.ema(closes, period) if on else []
        if e:
            _line(d, [(cx(i), y(e[off + i]) if off + i >= period - 1 else None) for i in range(len(vis))], colour, 2)
            legend.append((f"EMA{period}", colour))
    if bands:
        legend.append(("BB 20/2", BB_C))

    if zones:  # ESTIMATED liquidation zones: translucent bars from the right edge, length = estimated USD
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        shown = [z for z in zones if lo <= z["px"] <= hi]
        big = max((z["usd"] for z in shown), default=0.0)
        for z in shown:
            ln = (x1 - x0) * 0.28 * z["usd"] / big if big else 0
            ld.rectangle([x1 - ln, y(z["px_hi"]), x1, y(z["px"])], fill=(*(DOWN if z["side"] == "long" else UP), 70))
        d = _blend(img, layer)
        for z in sorted(shown, key=lambda z: -z["usd"])[:3]:
            d.text((x1 - 260, y(z["px_hi"]) - 2), f"est. {z['side']} liq {_usd(z['usd'])}", fill=DOWN if z["side"] == "long" else UP, font=_font(14))
        legend.append(("EST. LIQ ZONES", (200, 200, 200)))
    for wl in walls or []:
        if lo <= wl["px"] <= hi:
            yw = y(wl["px"])
            for xx in range(int(x0), int(x1), 14):
                d.line([(xx, yw), (xx + 7, yw)], fill=WALL_C, width=2)
            d.text((x0 + 6, yw - 18), f"{wl['venue']} {wl['side']} wall {_usd(wl['usd'])} @ {fmt_price(wl['px'])}", fill=WALL_C, font=_font(14))
    for k, pat in enumerate(twap or []):  # a marker under each slice of a time-sliced run
        c = UP if pat["side"] == "buy" else DOWN
        for t in pat["times"]:
            if t0 <= t <= t1:
                xx = xt(t)
                tri = [(xx, py1 - 14), (xx - 5, py1 - 4), (xx + 5, py1 - 4)] if pat["side"] == "buy" else [(xx, py1 - 4), (xx - 5, py1 - 14), (xx + 5, py1 - 14)]
                d.polygon(tri, fill=c)
        if t0 <= pat["start"] <= t1:
            d.text((min(xt(pat["start"]), x1 - 340), py1 - 34 - 18 * k), f"TWAP-like {pat['side']}s (inferred) · {pat['prints']}x every {pat['interval_s']:.0f}s",
                   fill=TWAP_C, font=_font(14))
    if depth:  # resting liquidity at each price (bucketed by Hyperliquid), as bars to the right of the candles
        dx0 = xr + 6
        rows = [(px, px * sz, UP) for px, sz in depth["bids"] if lo <= px <= hi] + [(px, px * sz, DOWN) for px, sz in depth["asks"] if lo <= px <= hi]
        biggest = max((u for _, u, _ in rows), default=0)
        bar_h = max(2.0, min(10.0, price_h / max(1, len(rows)) * 0.8))
        for px, usd, colour in rows:
            ln = (depth_w - 12) * usd / biggest if biggest else 0
            d.rectangle([dx0, y(px) - bar_h / 2, dx0 + ln, y(px) + bar_h / 2], fill=colour)
        d.text((dx0, py0 - 22), "Book depth", fill=TEXT, font=_font(16))
        d.text((dx0, py1 + 4), f"bids {_usd(sum(px * sz for px, sz in depth['bids']))}", fill=UP, font=_font(15))
        d.text((dx0, py1 + 22), f"asks {_usd(sum(px * sz for px, sz in depth['asks']))}", fill=DOWN, font=_font(15))

    yy = py1 + gap
    for kind, data in panels:
        ph = PANEL_H[kind]
        a0, a1 = yy + 24, yy + ph
        d.line([(x0, yy), (x1, yy)], fill=GRID)
        d.text((x0 + 4, yy + 2), PANEL_NAME[kind], fill=MUTED, font=_font(17))

        def scale(vlo: float, vhi: float, a0: float = a0, a1: float = a1) -> Any:
            r = (vhi - vlo) or 1.0
            return lambda v: a1 - (v - vlo) / r * (a1 - a0)

        if kind == "volume":
            sy = scale(0, max(c.v for c in vis) or 1.0)
            for i, c in enumerate(vis):
                d.rectangle([cx(i) - body / 2, sy(c.v), cx(i) + body / 2, a1], fill=UP if c.c >= c.o else DOWN)
            d.text((x0 + 90, yy + 2), f"last {_usd(vis[-1].v * vis[-1].c)}", fill=MUTED, font=_font(15))
        elif kind == "rsi":
            r = ind.rsi_series(closes)[off:]
            sy = scale(0, 100)
            for lvl in (30, 50, 70):
                d.line([(x0, sy(lvl)), (x1, sy(lvl))], fill=GRID if lvl == 50 else (60, 66, 63))
                d.text((w - PAD_R + 8, sy(lvl) - 9), str(lvl), fill=MUTED, font=_font(15))
            _line(d, [(cx(i), sy(v) if v is not None else None) for i, v in enumerate(r)], RSI_C, 2)
            last = next((v for v in reversed(r) if v is not None), None)
            if last is not None:
                d.text((x0 + 90, yy + 2), f"{last:.1f}", fill=RSI_C, font=_font(17))
        elif kind == "macd":
            line, sig = ind.macd_series(closes)
            line, sig = line[off:], sig[off:]
            hist = [(a - b) if a is not None and b is not None else None for a, b in zip(line, sig)]
            m = max((abs(v) for v in (*line, *sig, *hist) if v is not None), default=0.0) or 1.0
            sy = scale(-m, m)
            d.line([(x0, sy(0)), (x1, sy(0))], fill=GRID)
            for i, hv in enumerate(hist):
                if hv is not None:
                    d.rectangle([cx(i) - body / 2, min(sy(0), sy(hv)), cx(i) + body / 2, max(sy(0), sy(hv))], fill=UP if hv >= 0 else DOWN)
            _line(d, [(cx(i), sy(v) if v is not None else None) for i, v in enumerate(line)], MACD_C, 2)
            _line(d, [(cx(i), sy(v) if v is not None else None) for i, v in enumerate(sig)], SIG_C, 2)
        elif kind == "funding":
            pts = [(t, r * 100) for t, r in data if t0 - 3_600_000 <= t <= t1]
            m = max((abs(r) for _, r in pts), default=0.0) or 0.001
            sy = scale(-m, m)
            d.line([(x0, sy(0)), (x1, sy(0))], fill=GRID)
            bw = max(2.0, (x1 - x0) * 3_600_000 / (t1 - t0) * 0.7)
            for t, r in pts:
                xx = min(max(xt(t), x0 + bw / 2), x1 - bw / 2)
                d.rectangle([xx - bw / 2, min(sy(0), sy(r)), xx + bw / 2, max(sy(0), sy(r))], fill=FUND_UP if r >= 0 else FUND_DN)
            d.text((w - PAD_R + 8, a0 - 4), f"{m:+.4f}%", fill=MUTED, font=_font(14))
            d.text((w - PAD_R + 8, a1 - 16), f"{-m:+.4f}%", fill=MUTED, font=_font(14))
            if pts:
                d.text((x0 + 140, yy + 2), f"now {pts[-1][1]:+.4f}%/h · + longs pay · - shorts pay", fill=MUTED, font=_font(15))
        elif kind == "cvd":
            pts = [(t, v) for t, v in data["points"] if t0 <= t < t1]
            if pts:  # restart the cumulation at the chart's left edge
                first = next((b - sl for t, b, sl in data["flow"] if t == pts[0][0]), 0.0)
                base = pts[0][1] - first
                pts = [(t, v - base) for t, v in pts]
                m = max(abs(v) for _, v in pts) or 1.0
                sy = scale(-m, m)
                d.line([(x0, sy(0)), (x1, sy(0))], fill=GRID)
                _line(d, [(min(max(xt(t + interval_ms / 2), x0), x1), sy(v)) for t, v in pts], CVD_C, 3)
                d.text((x0 + 250, yy + 2), f"{_usd(pts[-1][1])} in view · {' + '.join(data['venues'])} perps", fill=UP if pts[-1][1] >= 0 else DOWN,
                       font=_font(16))
                d.text((w - PAD_R + 8, sy(m) - 9), _usd(m), fill=MUTED, font=_font(14))
                d.text((w - PAD_R + 8, sy(-m) - 9), _usd(-m), fill=MUTED, font=_font(14))
        elif kind == "aggoi":
            pts = [(t, v) for t, v in data["history"] if t0 <= t <= t1] or list(data["history"])
            if pts:
                vlo, vhi = min(v for _, v in pts), max(v for _, v in pts)
                pad = (vhi - vlo) * 0.1 or vhi * 0.01
                sy = scale(vlo - pad, vhi + pad)
                _line(d, [(min(max(xt(t), x0), x1), sy(v)) for t, v in pts], AGG_C, 3)
                ch = (pts[-1][1] - pts[0][1]) / pts[0][1] * 100 if pts[0][1] else 0.0
                d.text((x0 + 280, yy + 2), f"{_usd(pts[-1][1])} {'+'.join(data['history_venues'])} · {ch:+.1f}% in view · all venues now "
                       f"{_usd(data['total_usd'])}", fill=AGG_C, font=_font(16))
                d.text((w - PAD_R + 8, sy(vhi) - 9), _usd(vhi), fill=MUTED, font=_font(14))
                d.text((w - PAD_R + 8, sy(vlo) - 9), _usd(vlo), fill=MUTED, font=_font(14))
        elif kind == "liq":  # per candle: long liquidations down (red), short liquidations up (green)
            per: dict[int, list[float]] = {}
            for e in data["events"]:
                if t0 <= e["t"] < t1:
                    per.setdefault(int((e["t"] - t0) // interval_ms), [0.0, 0.0])[0 if e["side"] == "long" else 1] += e["usd"]
            m = max((max(a) for a in per.values()), default=0.0) or 1.0
            sy = scale(-m, m)
            d.line([(x0, sy(0)), (x1, sy(0))], fill=GRID)
            for i, (lg, sh) in per.items():
                if lg:
                    d.rectangle([cx(i) - body / 2, sy(0), cx(i) + body / 2, sy(-lg)], fill=LIQ_L)
                if sh:
                    d.rectangle([cx(i) - body / 2, sy(sh), cx(i) + body / 2, sy(0)], fill=LIQ_S)
            d.text((x0 + 250, yy + 2), f"{' + '.join(data['venues'])} · longs {_usd(sum(a[0] for a in per.values()))} · shorts {_usd(sum(a[1] for a in per.values()))} in view",
                   fill=MUTED, font=_font(16))
        elif kind == "oi":
            pts = [(t, v) for t, v in data if t0 <= t <= t1] or list(data)
            vlo, vhi = min(v for _, v in pts), max(v for _, v in pts)
            pad = (vhi - vlo) * 0.1 or vhi * 0.01
            sy = scale(vlo - pad, vhi + pad)
            _line(d, [(min(max(xt(t), x0), x1), sy(v)) for t, v in pts], OI_C, 3)
            ch = (pts[-1][1] - pts[0][1]) / pts[0][1] * 100 if pts[0][1] else 0.0
            d.text((x0 + 330, yy + 2), f"{_usd(pts[-1][1])} · {ch:+.1f}% in view", fill=OI_C, font=_font(17))
            d.text((w - PAD_R + 8, sy(vhi) - 9), _usd(vhi), fill=MUTED, font=_font(14))
            d.text((w - PAD_R + 8, sy(vlo) - 9), _usd(vlo), fill=MUTED, font=_font(14))
        yy += ph + gap

    if plan is not None:  # the plan's prices, tagged on the right of the zone
        for px_, colour, name in ((plan.target, TARGET, "TARGET"), (sum(plan.entry) / 2, ENTRY, "ENTRY"), (plan.stop, STOP, "STOP")):
            d.line([(plan_x, y(px_)), (xr, y(px_))], fill=colour, width=2)
            _label(d, (plan_x + 6, y(px_) - 20), f"{name} {fmt_price(px_)}", colour)
        badge = f"{plan.side} {'SETUP' if plan.full else 'IDEA'}"
        d.text((xr, 66), badge, fill=UP if plan.side == "LONG" else DOWN, font=_font(19), anchor="rm")
    for at, text, colour in tags:
        _label(d, at, text, colour)
    last = vis[-1]
    tag_c = UP if last.c >= last.o else DOWN
    yl = y(last.c)
    for xx in range(int(x0), int(xr), 10):  # the last price: a dotted line and a filled tag on the axis
        d.line([(xx, yl), (xx + 4, yl)], fill=tag_c, width=1)
    d.rectangle([w - PAD_R + 2, yl - 13, w - 6, yl + 13], fill=tag_c)
    d.text((w - PAD_R + 8, yl), fmt_price(last.c), fill=BG, font=_font(17), anchor="lm")
    tf = _font(32)
    d.text((PAD_L, 14), title, fill=TEXT, font=tf)
    if change is not None:  # the 24h move as a coloured badge after the title
        bx = PAD_L + d.textlength(title, font=tf) + 14
        label = f"{change:+.2f}% 24h"
        bw = d.textlength(label, font=_font(18)) + 18
        d.rounded_rectangle([bx, 20, bx + bw, 46], radius=8, fill=(12, 70, 48) if change >= 0 else (90, 28, 28))
        d.text((bx + 9, 33), label, fill=UP if change >= 0 else DOWN, font=_font(18), anchor="lm")
    d.text((PAD_L, 56), subtitle, fill=MUTED, font=_font(18))
    lx = w - PAD_R
    for name, colour in reversed(legend):
        lx -= 16 + len(name) * 10
        d.text((lx, 22), name, fill=colour, font=_font(17))
    stamp = datetime.fromtimestamp(vis[-1].t / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for i, line in enumerate(sources or []):  # what each structure layer is and where it came from, on the picture itself
        d.text((PAD_L, h - 32 - 22 * (len(sources or []) - i)), line, fill=MUTED, font=_font(15))
    d.text((PAD_L, h - 32), f"Candles: {source} · {stamp} · Price Chat", fill=MUTED, font=_font(15))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
