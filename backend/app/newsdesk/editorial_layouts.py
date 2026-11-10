"""Price Chat editorial picture formats (owner, 2026-11-10: "premium global financial media brand, not an automated RSS bot").

Three formats, one design system, every word and figure drawn here from verified data (never by an image model):

  cinematic(...)   breaking and major news: a text-free background (the GPU PC's, or a licensed photo) under an editorial layout
  market_chart(...)  a real price series: the move, the range, a clean line with a log axis past one factor of phi (app.price_axis)
  data_board(...)  ranked comparisons: returns over a stated window, drawdowns, sector boards, with the method and dates printed

The system: 1600x900 (16:9), a 12-column grid with 72 px margins; Source Serif 4 for headlines (sentence case, never shouted),
Inter for data and labels (tabular figures); the kiwi mark and PRICE CHAT on every picture; a dateline in UTC; the sources and, for an
AI background, "Illustration" in small type. Themes, accents and layouts rotate so consecutive posts don't look alike.
"""

import io
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app import price_axis

W, H = 1600, 900
M = 72  # margin
ASSETS = Path(__file__).with_name("assets")


@dataclass(frozen=True)
class Theme:
    name: str
    bg: tuple[int, int, int]
    panel: tuple[int, int, int]
    text: tuple[int, int, int]
    muted: tuple[int, int, int]
    grid: tuple[int, int, int]


THEMES = {
    "ink": Theme("ink", (11, 14, 19), (20, 25, 33), (241, 242, 239), (146, 155, 164), (38, 45, 55)),
    "midnight": Theme("midnight", (8, 14, 30), (16, 26, 50), (236, 241, 250), (140, 156, 186), (34, 48, 78)),
    "paper": Theme("paper", (244, 241, 234), (233, 228, 218), (18, 20, 24), (98, 100, 104), (214, 208, 196)),
}
GREEN, RED, AMBER, BLUE = (104, 204, 40), (228, 66, 52), (242, 176, 52), (84, 150, 255)
BRAND = (122, 214, 42)
SECTION_ACCENT = {"breaking": RED, "markets": GREEN, "crypto": AMBER, "macro": BLUE, "geopolitics": RED, "energy": AMBER, "metals": AMBER,
                  "stocks": GREEN, "data": BLUE, "developing": AMBER}


@lru_cache(maxsize=64)
def serif(size: int, weight: str = "SemiBold") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(ASSETS / "SourceSerif4-Variable.ttf"), size)
    f.set_variation_by_name(weight)
    return f


@lru_cache(maxsize=64)
def sans(size: int, weight: str = "Medium") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(ASSETS / "Inter-Variable.ttf"), size)
    f.set_variation_by_name(weight)
    return f


@lru_cache(maxsize=1)
def kiwi() -> Image.Image:
    return Image.open(ASSETS / "kiwi.webp").convert("RGBA")


def _jpeg(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=92, optimize=True, progressive=True)
    return out.getvalue()


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if font.getlength(t) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(text: str, width: int, sizes: tuple[int, ...], max_lines: int, weight: str = "SemiBold") -> tuple[ImageFont.FreeTypeFont, list[str]]:
    for s in sizes:
        f = serif(s, weight)
        lines = wrap(text, f, width)
        if len(lines) <= max_lines:
            return f, lines
    f = serif(sizes[-1], weight)
    lines = wrap(text, f, width)[:max_lines]
    return f, lines


def tracked(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont, fill: Any, tracking: float = 1.5) -> float:
    """Letter-spaced small caps labels. Returns the end x."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking
    return x


def tracked_len(text: str, font: ImageFont.FreeTypeFont, tracking: float = 1.5) -> float:
    return sum(font.getlength(ch) + tracking for ch in text) - tracking


def dateline(when: datetime) -> str:
    w = when.astimezone(timezone.utc)
    return f"{w.day} {w.strftime('%b %Y').upper()} · {w.strftime('%H:%M')} UTC"


def header(img: Image.Image, theme: Theme, section: str, when: datetime, accent: tuple[int, int, int], on_photo: bool = False) -> None:
    d = ImageDraw.Draw(img)
    disc = Image.new("RGBA", (46, 46), (0, 0, 0, 0))
    if on_photo or theme.name != "paper":  # the black kiwi needs a light disc on dark pictures
        ImageDraw.Draw(disc).ellipse((0, 0, 45, 45), fill=(236, 244, 226, 255))
    k = kiwi().resize((38, 38), Image.Resampling.LANCZOS)
    disc.alpha_composite(k, (4, 4))
    img.paste(disc, (M, M - 9), disc)
    fg = (245, 246, 244) if on_photo else theme.text
    mu = (214, 218, 222) if on_photo else theme.muted
    x = tracked(d, (M + 54, M - 1), "PRICE CHAT", sans(22, "ExtraBold"), fg, 2.2)
    d.line((x + 18, M + 2, x + 18, M + 26), fill=mu, width=2)
    label = tuple(min(255, int(c + (255 - c) * 0.35)) for c in accent) if (on_photo or theme.name != "paper") else accent  # lifted on dark
    tracked(d, (x + 36, M - 1), section.upper(), sans(22, "Bold"), label, 2.0)
    t = dateline(when)
    f = sans(21, "Medium")
    tracked(d, (W - M - tracked_len(t, f, 1.2), M), t, f, mu, 1.2)


def footer(img: Image.Image, theme: Theme, sources: list[str], note: str = "", on_photo: bool = False) -> None:
    d = ImageDraw.Draw(img)
    mu = (206, 210, 214) if on_photo else theme.muted
    y = H - M + 8
    f = sans(19, "Medium")
    src = "Source: " + ", ".join(sources[:3]) if sources else ""
    text = "  ·  ".join(x for x in (src, note) if x)
    d.text((M, y), text, font=f, fill=mu)
    site = "pricechat.live"
    d.text((W - M - f.getlength(site), y), site, font=sans(19, "SemiBold"), fill=mu)


def pill(d: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, colour: tuple[int, int, int], dark: bool = True) -> int:
    f = sans(21, "Bold")
    tw = tracked_len(text, f, 1.6)
    x, y = xy
    d.rounded_rectangle((x, y, x + tw + 28, y + 38), radius=6, fill=colour)
    tracked(d, (x + 14, y + 6), text, f, (12, 12, 12) if dark else (255, 255, 255), 1.6)
    return int(x + tw + 28)


# --- format 1: cinematic -------------------------------------------------------------------------------------------------


def _cover(bg: Image.Image) -> Image.Image:
    s = max(W / bg.width, H / bg.height)
    r = bg.resize((math.ceil(bg.width * s), math.ceil(bg.height * s)), Image.Resampling.LANCZOS)
    x, y = (r.width - W) // 2, (r.height - H) // 2
    return r.crop((x, y, x + W, y + H)).convert("RGB")


def _grade(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(0.86)
    return ImageEnhance.Contrast(img).enhance(1.06)


def _scrim(img: Image.Image, layout: str) -> Image.Image:
    """Darkens where the type sits so it reads at any background: a left-to-right ramp or a bottom ramp, plus a top band for the header."""
    mask = Image.new("L", (W, H), 0)
    px = mask.load()
    for y in range(H):
        top = max(0.0, 1 - y / 230) ** 0.8 * 205  # the header band
        foot = max(0.0, (y - (H - 150)) / 150) ** 1.2 * 215  # the source line
        for x in range(0, W, 4):
            if layout == "left":
                v = max(0.0, 1 - x / (W * 0.70)) ** 1.35 * 232
            else:
                v = max(0.0, (y - H * 0.38) / (H * 0.62)) ** 1.25 * 238
            a = int(min(255, max(v, top, foot)))
            for k in range(4):
                if x + k < W:
                    px[x + k, y] = a
    dark = Image.new("RGB", (W, H), (6, 8, 12))
    return Image.composite(dark, img, mask)


def cinematic(background: Image.Image, *, headline: str, deck: str = "", kicker: str = "BREAKING", section: str = "breaking", sources: list[str],
              when: datetime, layout: str = "lower", illustration: str = "", credit: str = "") -> tuple[bytes, dict[str, Any]]:
    """The headline over a cinematic background. illustration: the model, for "Illustration: AI-generated (...)"; credit: a photo's credit."""
    accent = SECTION_ACCENT.get(section, RED)
    img = _scrim(_grade(_cover(background)), layout)
    d = ImageDraw.Draw(img)
    header(img, THEMES["ink"], section, when, accent, on_photo=True)
    width = 980 if layout == "left" else 1240
    font, lines = fit(headline, width, (84, 78, 72, 66, 60, 56), 3)
    for narrower in (0.92, 0.85, 0.78):  # no lone word on the last line: a slightly narrower measure, same size, same line count
        if len(lines) < 2 or len(lines[-1].split()) > 1:
            break
        again = wrap(headline, font, int(width * narrower))
        if len(again) == len(lines):
            lines = again
    lh = int(font.size * 1.12)
    deck_f = sans(30, "Regular")
    deck_lines = wrap(deck, deck_f, width)[:2] if deck else []
    block = 38 + 26 + len(lines) * lh + (18 + len(deck_lines) * 42 if deck_lines else 0)
    y = (H - M - 70 - block) if layout == "lower" else (H - block) // 2 + 30
    pill(d, (M, y), kicker, accent, dark=accent != RED)
    y += 38 + 26
    # a soft shadow under the type: it reads over any background (bright skies, banknotes, snow)
    shadow = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shadow)
    yy = y
    for ln in lines:
        sd.text((M, yy + 3), ln, font=font, fill=255)
        yy += lh
    if deck_lines:
        yy += 18
        for ln in deck_lines:
            sd.text((M, yy + 2), ln, font=deck_f, fill=255)
            yy += 42
    shadow = shadow.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(min(255, v * 2.2)))
    img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shadow)
    d = ImageDraw.Draw(img)
    for ln in lines:
        d.text((M, y), ln, font=font, fill=(248, 248, 246))
        y += lh
    if deck_lines:
        y += 18
        for ln in deck_lines:
            d.text((M, y), ln, font=deck_f, fill=(222, 226, 230))
            y += 42
    note = credit or (f"Illustration: AI-generated ({illustration}), not a photo of the event" if illustration else "")
    footer(img, THEMES["ink"], sources, note, on_photo=True)
    return _jpeg(img), {"format": "cinematic", "layout": layout, "lines": len(lines), "headline_px": font.size}


# --- format 2: market chart ----------------------------------------------------------------------------------------------


def nice_ticks(lo: float, hi: float, log: bool, target: int = 5) -> list[float]:
    """Round gridline values inside [lo, hi]: 1-2-5 steps on a linear axis, 1-2-5 decades on a log axis."""
    if hi <= lo:
        return [lo]
    if log:
        out = []
        for e in range(math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 1):
            for m in (1, 2, 5):
                v = m * 10 ** e
                if lo <= v <= hi:
                    out.append(float(v))
        if len(out) < 3:  # a narrow log range: round linear steps, still placed on the log axis
            return nice_ticks(lo, hi, False, target)
        return out
    raw = (hi - lo) / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.ceil(lo / step) * step
    return [round(start + i * step, 10) for i in range(int((hi - start) / step) + 1)]


def _fmt_price(v: float) -> str:
    if v >= 10000:
        return f"{v:,.0f}"
    if v >= 100:
        return f"{v:,.2f}" if v < 1000 else f"{v:,.1f}"
    if v >= 1:
        return f"{v:,.2f}"
    return f"{v:,.4f}"


def _fmt_tick(v: float) -> str:
    if v >= 1000:
        return f"{v / 1000:,.1f}k".replace(".0k", "k") if v < 100000 else f"{v / 1000:,.0f}k"
    if v >= 10:
        return f"{v:,.0f}"
    if v >= 1:
        return f"{v:,.2f}"
    return f"{v:,.3f}"


def market_chart(series: list[tuple[datetime, float]], *, name: str, ticker: str, window: str, headline: str, sources: list[str], when: datetime,
                 unit: str = "$", theme: str = "ink", section: str = "markets", method: str = "") -> tuple[bytes, dict[str, Any]]:
    """A real price series. The change is last vs first point of the series; high and low are the series' own."""
    if len(series) < 10:
        raise ValueError("a chart needs at least 10 points")
    th = THEMES[theme]
    first, last = series[0][1], series[-1][1]
    pct = (last / first - 1) * 100
    colour = GREEN if pct >= 0 else RED
    hi, lo = max(v for _, v in series), min(v for _, v in series)
    img = Image.new("RGB", (W, H), th.bg)
    d = ImageDraw.Draw(img)
    header(img, th, section, when, SECTION_ACCENT.get(section, GREEN))
    # headline across the top
    font, lines = fit(headline, W - 2 * M, (60, 56, 52, 48), 2)
    y = M + 64
    for ln in lines:
        d.text((M, y), ln, font=font, fill=th.text)
        y += int(font.size * 1.12)
    top = y + 44
    # left column: the numbers
    lx = M
    tracked(d, (lx, top), f"{name.upper()}  ·  {ticker}", sans(22, "Bold"), th.muted, 1.6)
    d.text((lx, top + 38), f"{unit}{_fmt_price(last)}", font=sans(76, "Bold"), fill=th.text)
    arrow = "▲" if pct >= 0 else "▼"
    d.text((lx, top + 136), f"{arrow} {abs(pct):.1f}%", font=sans(48, "Bold"), fill=colour)
    d.text((lx, top + 196), window, font=sans(24, "Medium"), fill=th.muted)
    stats = [("High", hi), ("Low", lo), ("Start", first)]
    sy = top + 260
    for label, v in stats:
        d.text((lx, sy), label, font=sans(22, "Medium"), fill=th.muted)
        d.text((lx + 110, sy), f"{unit}{_fmt_price(v)}", font=sans(22, "SemiBold"), fill=th.text)
        sy += 36
    # right: the chart
    cx0, cx1, cy0, cy1 = 520, W - M - 120, top + 6, H - M - 60
    ax = price_axis.axis(lo, hi, pad=0.06)
    for t in nice_ticks(ax.lo, ax.hi, ax.log):
        yy = cy1 - ax.frac(t) * (cy1 - cy0)
        if cy0 - 2 <= yy <= cy1 + 2:
            d.line((cx0, yy, cx1, yy), fill=th.grid, width=1)
            d.text((cx1 + 34, yy - 12), _fmt_tick(t), font=sans(19, "Medium"), fill=th.muted)
    t0, t1 = series[0][0].timestamp(), series[-1][0].timestamp()
    pts = [(cx0 + (t.timestamp() - t0) / max(1, t1 - t0) * (cx1 - cx0), cy1 - ax.frac(v) * (cy1 - cy0)) for t, v in series]
    # area under the line
    area = Image.new("L", (W, H), 0)
    ImageDraw.Draw(area).polygon([(pts[0][0], cy1), *pts, (pts[-1][0], cy1)], fill=255)
    grad = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(grad)
    for yy in range(int(cy0), int(cy1) + 1):
        gd.line((cx0, yy, cx1, yy), fill=int(70 * (1 - (yy - cy0) / max(1, cy1 - cy0))))
    from PIL import ImageChops

    img.paste(Image.new("RGB", (W, H), colour), (0, 0), ImageChops.multiply(area, grad))
    d = ImageDraw.Draw(img)
    d.line(pts, fill=colour, width=4, joint="curve")
    ex, ey = pts[-1]
    d.ellipse((ex - 8, ey - 8, ex + 8, ey + 8), fill=colour)
    d.ellipse((ex - 16, ey - 16, ex + 16, ey + 16), outline=colour, width=2)
    # time axis: first, middle, last dates
    span_days = (t1 - t0) / 86400
    for frac in (0.0, 0.5, 1.0):
        tt = datetime.fromtimestamp(t0 + frac * (t1 - t0), timezone.utc)
        label = tt.strftime("%b %Y") if span_days > 400 else (f"{tt.day} {tt:%b}" if span_days > 2 else tt.strftime("%H:%M"))
        lw = sans(19, "Medium").getlength(label)
        xx = cx0 + frac * (cx1 - cx0) - (0 if frac == 0 else lw if frac == 1 else lw / 2)
        d.text((xx, cy1 + 14), label, font=sans(19, "Medium"), fill=th.muted)
    if ax.log:
        tracked(d, (cx0, cy0 - 30), "LOG SCALE", sans(16, "Bold"), th.muted, 1.4)
    footer(img, th, sources, method)
    return _jpeg(img), {"format": "chart", "pct": round(pct, 2), "log": ax.log, "points": len(series), "theme": theme}


# --- format 3: data board --------------------------------------------------------------------------------------------------


def data_board(rows: list[tuple[str, float, str]], *, title: str, subtitle: str, sources: list[str], when: datetime, method: str,
               theme: str = "paper", section: str = "data", suffix: str = "%") -> tuple[bytes, dict[str, Any]]:
    """Ranked bars from verified values: rows of (label, value, note). Positive bars run right in green, negative left in red, from one
    zero line; every value is printed at its bar's end."""
    th = THEMES[theme]
    rows = sorted(rows, key=lambda r: -r[1])
    img = Image.new("RGB", (W, H), th.bg)
    d = ImageDraw.Draw(img)
    header(img, th, section, when, SECTION_ACCENT.get(section, BLUE))
    font, lines = fit(title, W - 2 * M, (64, 58, 54, 50), 2, "Bold")
    y = M + 62
    for ln in lines:
        d.text((M, y), ln, font=font, fill=th.text)
        y += int(font.size * 1.1)
    d.text((M, y + 14), subtitle, font=sans(26, "Regular"), fill=th.muted)
    top = y + 78
    bottom = H - M - 56
    n = len(rows)
    row_h = min(78, (bottom - top) / max(1, n))
    label_w = 300
    vmax = max(abs(v) for _, v, _ in rows) or 1
    neg = any(v < 0 for _, v, _ in rows)
    x0 = M + label_w
    x1 = W - M - 150
    pos_max = max((v for _, v, _ in rows if v > 0), default=0.0)
    neg_max = max((-v for _, v, _ in rows if v < 0), default=0.0)
    if not neg:
        zero, scale = x0, (x1 - x0) / max(pos_max, 1e-9)
    elif pos_max == 0:
        zero, scale = x1, (x1 - x0) / max(neg_max, 1e-9)  # all losses: bars run left from the right edge
    else:
        zero = x0 + (x1 - x0) * neg_max / (neg_max + pos_max)
        scale = (x1 - x0) / (neg_max + pos_max)
    for i, (label, v, note) in enumerate(rows):
        yy = top + i * row_h
        bh = row_h * 0.56
        d.text((M, yy + bh / 2 - 16), label, font=sans(30, "SemiBold"), fill=th.text)
        if note:
            d.text((M, yy + bh / 2 + 18), note, font=sans(17, "Medium"), fill=th.muted)
        colour = GREEN if v >= 0 else RED
        bx = zero + v * scale
        d.rounded_rectangle((min(zero, bx), yy, max(zero, bx), yy + bh), radius=4, fill=colour)
        txt = f"{'+' if v > 0 else '−' if v < 0 else ''}{abs(v):,.1f}{suffix}"
        tf = sans(30, "Bold")
        if v >= 0:
            d.text((bx + 14, yy + bh / 2 - 18), txt, font=tf, fill=th.text)
        else:
            tw = tf.getlength(txt)
            tx = bx - tw - 14
            d.text((tx if tx > x0 - 10 else bx + 14, yy + bh / 2 - 18), txt, font=tf, fill=th.text)
    d.line((zero, top - 10, zero, top + n * row_h - row_h * 0.3), fill=th.muted, width=2)
    footer(img, th, sources, method)
    return _jpeg(img), {"format": "data", "rows": n, "theme": theme}


# --- figures in words: every number a data post states comes from the data ------------------------------------------------

import re as _re  # noqa: E402

NUMBER = _re.compile(r"[-+−]?\$?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    out = []
    for m in NUMBER.finditer(text or ""):
        s = m.group(0).replace("$", "").replace(",", "").replace("−", "-")
        try:
            out.append(abs(float(s)))
        except ValueError:
            continue
    return out


NAMES_WITH_NUMBERS = ("S&P 500", "Nasdaq 100", "FTSE 100", "Nikkei 225", "Dow 30", "DAX 40", "Russell 2000", "Stoxx 600")


def check_figures(text: str, figures: list[float], *, years: tuple[int, ...] = (), names: tuple[str, ...] = NAMES_WITH_NUMBERS) -> list[str]:
    """Numbers in text that no computed figure supports (at the precision written). Years named in the window, and numbers that are
    part of an index's name ("S&P 500"), are allowed."""
    for n in names:
        text = (text or "").replace(n, "")
    bad = []
    for n in numbers_in(text):
        if n in years or (n.is_integer() and int(n) in (1, 2, 3, 5, 10) and _re.search(rf"\b{int(n)}\s+(?:years?|months?|days?)\b", text or "")):
            continue
        decimals = 0 if float(n).is_integer() else len(str(n).split(".")[1])
        if not any(round(abs(f), decimals) == n or (decimals == 0 and round(abs(f)) == n) for f in figures):
            bad.append(f"{n:g}")
    return bad


def max_drawdown(series: list[tuple[datetime, float]]) -> tuple[float, datetime, datetime]:
    peak, peak_at, worst, worst_at, worst_peak_at = series[0][1], series[0][0], 0.0, series[0][0], series[0][0]
    for t, v in series:
        if v > peak:
            peak, peak_at = v, t
        dd = v / peak - 1
        if dd < worst:
            worst, worst_at, worst_peak_at = dd, t, peak_at
    return worst * 100, worst_peak_at, worst_at


def chart_headline(name: str, series: list[tuple[datetime, float]], window_words: str, unit: str = "$") -> tuple[str, list[float]]:
    """A hook written from the series alone: the change over the window, and the swing or crash inside it when that is the story."""
    first, last = series[0][1], series[-1][1]
    pct = (last / first - 1) * 100
    hi, lo = max(v for _, v in series), min(v for _, v in series)
    dd, _, _ = max_drawdown(series)
    p = f"{abs(pct):.1f}" if abs(pct) < 10 else f"{abs(pct):.0f}"
    move = f"up {p}%" if pct >= 0.05 else (f"down {p}%" if pct <= -0.05 else "flat")
    swing = (hi / lo - 1) * 100
    if dd <= -40:
        text = f"{name} is {move} in {window_words}, after a {abs(dd):.0f}% crash on the way"
    elif abs(pct) < 10 and swing >= 25:
        text = f"{name} is {move} in {window_words}, but it hit {unit}{_fmt_price(hi)} and {unit}{_fmt_price(lo)} on the way"
    else:
        text = f"{name} is {move} in {window_words}, at {unit}{_fmt_price(last)}"
    return text[0].upper() + text[1:], [pct, dd, hi, lo, last, first]


def board_title(rows: list[tuple[str, float, str]], frame: str) -> tuple[str, list[float]]:
    """The board's hook from its own top and bottom rows."""
    s = sorted(rows, key=lambda r: -r[1])
    top, bottom = s[0], s[-1]
    sign = lambda v: f"+{v:.0f}%" if v >= 0 else f"−{abs(v):.0f}%"  # noqa: E731
    return f"{frame}: {top[0]} {sign(top[1])}, {bottom[0]} {sign(bottom[1])}", [top[1], bottom[1]]
