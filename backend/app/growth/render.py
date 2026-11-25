"""Price Chat "Original Intelligence" renderer: one ChartSpec in, one branded PNG out.

Stable API (the G1 Originals queue calls these):

    from app.growth.render import render, render_info, RENDERER_VERSION
    png: bytes = render(spec, fmt="x")            # fmt: "x" (1600x900) | "portrait" (1080x1350); theme from spec.theme
    png, meta = render_info(spec, fmt="x", theme=None)   # meta: template, version, size, theme, phone type sizes, label_overlaps

`render` validates the spec first (app.growth.chart_spec.validate) and raises chart_spec.SpecError instead of drawing anything
misleading: a NaN or infinite value, an annotation pointing at a date that is not in the data, a histogram marker outside the
observed range. Store `meta["template"]` and `meta["version"]` on the post so the growth learner can tell visuals apart.

The visual system (one look for every kind):
- The finding is the headline: a kicker (the detector, tracked caps, with a square in the highlight colour), the HERO figure in
  Anton (the brand's display face), and the claim beside it in Inter ExtraBold. A muted dek says exactly what is measured.
- The chart is quiet: horizontal hairline grid, axis labels in muted Inter, no legend: every line is labelled at its end, in its own
  colour, with its last value. Context is grey; the point that matters is in the highlight colour, with a ringed dot and a label
  placed where it collides with nothing (labels are positioned by a search that avoids the plotted lines and other labels).
- The footer carries the brand, small: the Kiwi on the Price Chat green disc (the favicon mark; the Kiwi itself is never redrawn
  or recoloured), the "Price Chat" wordmark, and the source with its as-of time.
- Dark theme is the default (the brand's ink, highest contrast on X's dark timeline); light is a warm paper alternative.
- Everything is drawn at 2x and downsampled, so lines and type are antialiased at 1600x900 / 1080x1350.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.growth.chart_spec import FORMATS, Annotation, ChartSpec, Row, Series, SpecError, parse_date, validate

RENDERER_VERSION = "pc-intel-render-1"
ASSETS = Path(__file__).resolve().parent.parent / "newsdesk" / "assets"
SS = 2  # supersampling factor
PHONE = 390  # px, X timeline image width on a phone

RGB = tuple[int, int, int]


# ------------------------------------------------------------------------------------------------------------------ palette
@dataclass(frozen=True)
class Palette:
    bg: RGB
    glow: RGB
    text: RGB
    text2: RGB
    muted: RGB
    faint: RGB
    grid: RGB
    zero: RGB
    context: RGB
    context2: RGB
    highlight: RGB
    pos: RGB
    neg: RGB
    blue: RGB
    brand: RGB
    ring: RGB

    def role(self, role: str, value: float | None = None) -> RGB:
        if role == "auto":
            return self.pos if (value or 0) >= 0 else self.neg
        return {
            "highlight": self.highlight,
            "context": self.context,
            "pos": self.pos,
            "neg": self.neg,
            "accent": self.highlight,
            "blue": self.blue,
        }[role]


DARK = Palette(
    bg=(9, 11, 15), glow=(20, 24, 32), text=(244, 246, 248), text2=(196, 202, 211), muted=(132, 140, 153), faint=(86, 93, 106),
    grid=(33, 38, 47), zero=(96, 104, 118), context=(104, 113, 128), context2=(70, 78, 92), highlight=(255, 186, 46),
    pos=(30, 216, 130), neg=(255, 82, 82), blue=(96, 158, 255), brand=(0, 229, 143), ring=(9, 11, 15),
)
LIGHT = Palette(
    bg=(248, 246, 241), glow=(240, 236, 226), text=(17, 19, 24), text2=(58, 64, 74), muted=(108, 114, 124), faint=(166, 170, 176),
    grid=(225, 221, 212), zero=(140, 140, 136), context=(150, 156, 166), context2=(196, 199, 204), highlight=(214, 124, 0),
    pos=(0, 148, 84), neg=(212, 44, 44), blue=(36, 99, 215), brand=(0, 196, 122), ring=(248, 246, 241),
)
PALETTES = {"dark": DARK, "light": LIGHT}


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))  # type: ignore[return-value]


# ------------------------------------------------------------------------------------------------------------------ fonts
@lru_cache(maxsize=256)
def _font(family: str, size: int, weight: str) -> ImageFont.FreeTypeFont:
    if family == "hero":
        return ImageFont.truetype(str(ASSETS / "Anton-Regular.ttf"), size)
    f = ImageFont.truetype(str(ASSETS / "Inter-Variable.ttf"), size)
    f.set_variation_by_name(weight)
    return f


@lru_cache(maxsize=4)
def _kiwi() -> Image.Image:
    return Image.open(ASSETS / "kiwi.webp").convert("RGBA")


# ------------------------------------------------------------------------------------------------------------------ number formats
MINUS = "−"


def signed(v: float, digits: int = 1, suffix: str = "%") -> str:
    s = f"{v:+.{digits}f}"
    if float(s) == 0:
        s = f"{0:.{digits}f}"
    return s.replace("-", MINUS) + suffix


def fmt_value(v: float, y_format: str, digits: int | None = None) -> str:
    if y_format == "pct":
        return signed(v, 1 if digits is None else digits)
    if y_format == "pts":
        return f"{v:.{1 if digits is None else digits}f} pts".replace("-", MINUS)
    if y_format == "corr":
        return f"{v:.2f}".replace("-", MINUS)
    if y_format == "oz":
        return f"{v:.1f} oz"
    if y_format == "ratio":
        return f"{v:.2f}"
    if y_format == "rank":
        return f"#{v:,.0f}"
    if y_format == "price":
        return f"${v:,.0f}"
    if y_format == "index":
        return f"{v:.0f}"
    return f"{v:,.{1 if digits is None else digits}f}".replace("-", MINUS)


def fmt_tick(v: float, y_format: str, step: float) -> str:
    dec = 0
    while dec < 3 and abs(step * 10 ** dec - round(step * 10 ** dec)) > 1e-9:
        dec += 1
    if abs(v) < step * 1e-6:
        v = 0.0
    if y_format == "pct":
        return (f"{v:+.{dec}f}%" if v else "0%").replace("-", MINUS)
    if y_format == "pts":
        return f"{v:.{dec}f}".replace("-", MINUS)
    if y_format == "corr":
        return f"{v:.1f}".replace("-", MINUS) if step >= 0.1 else f"{v:.2f}".replace("-", MINUS)
    if y_format == "oz":
        return f"{v:.{dec}f}"
    if y_format == "price":
        return f"${v / 1000:,.0f}k" if abs(v) >= 1000 else f"${v:,.{dec}f}"
    if y_format == "rank":
        return f"#{v:,.0f}"
    return f"{v:,.{dec}f}".replace("-", MINUS)


def nice_step(span: float, n: int) -> float:
    raw = span / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def cover_ticks(lo: float, hi: float, n: int = 5) -> tuple[list[float], float]:
    """Ticks that cover [lo, hi]: the first tick <= lo, the last >= hi."""
    if hi <= lo:
        hi = lo + 1
    step = nice_step(hi - lo, n)
    a = math.floor(lo / step + 1e-9) * step
    b = math.ceil(hi / step - 1e-9) * step
    k = int(round((b - a) / step))
    return [round(a + i * step, 10) for i in range(k + 1)], step


def nice_ticks(lo: float, hi: float, n: int = 5) -> tuple[list[float], float]:
    if hi <= lo:
        hi = lo + 1
    step = nice_step(hi - lo, n)
    start = math.ceil(lo / step - 1e-9) * step
    ticks = []
    v = start
    while v <= hi + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks, step


# ------------------------------------------------------------------------------------------------------------------ canvas
Box = tuple[float, float, float, float]


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class Canvas:
    """Design-pixel drawing on a 2x supersampled RGBA image."""

    def __init__(self, fmt: str, theme: str):
        if fmt not in FORMATS:
            raise SpecError(f"unknown format {fmt!r}")
        self.fmt = fmt
        self.w, self.h = FORMATS[fmt]
        self.p = PALETTES[theme]
        self.theme = theme
        self.img = Image.new("RGBA", (self.w * SS, self.h * SS), self.p.bg + (255,))
        self.d = ImageDraw.Draw(self.img)
        self.sizes: list[float] = []  # essential text sizes (design px)
        self.notes: list[float] = []  # footnote sizes
        self.labels: list[Box] = []  # placed label boxes (collision memory)
        self.paths: list[list[tuple[float, float]]] = []  # plotted polylines (design px)
        self.overlaps = 0

    # fonts -------------------------------------------------------------------------------------------------------
    def font(self, size: float, weight: str = "SemiBold", note: bool = False, hero: bool = False) -> ImageFont.FreeTypeFont:
        (self.notes if note else self.sizes).append(size)
        return _font("hero" if hero else "sans", int(round(size * SS)), weight)

    @staticmethod
    def tw(text: str, f: ImageFont.FreeTypeFont) -> float:
        return f.getlength(text) / SS

    @staticmethod
    def ink(text: str, f: ImageFont.FreeTypeFont, anchor: str = "ls") -> Box:
        b = f.getbbox(text, anchor=anchor)
        return (b[0] / SS, b[1] / SS, b[2] / SS, b[3] / SS)

    # drawing -----------------------------------------------------------------------------------------------------
    def text(self, xy: tuple[float, float], text: str, f: ImageFont.FreeTypeFont, fill: RGB, anchor: str = "ls") -> None:
        self.d.text((xy[0] * SS, xy[1] * SS), text, font=f, fill=fill, anchor=anchor)

    def tracked(self, xy: tuple[float, float], text: str, f: ImageFont.FreeTypeFont, fill: RGB, tracking: float) -> float:
        x, y = xy
        for ch in text:
            self.text((x, y), ch, f, fill)
            x += self.tw(ch, f) + tracking
        return x - tracking

    def tracked_len(self, text: str, f: ImageFont.FreeTypeFont, tracking: float) -> float:
        return sum(self.tw(ch, f) + tracking for ch in text) - tracking

    def line(self, pts: list[tuple[float, float]], fill: RGB, width: float, caps: bool = True) -> None:
        if len(pts) < 2:
            return
        sp = [(x * SS, y * SS) for x, y in pts]
        self.d.line(sp, fill=fill, width=max(1, int(round(width * SS))), joint="curve")
        if caps and width >= 2:
            r = width * SS / 2
            for x, y in (sp[0], sp[-1]):
                self.d.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    def dashed(self, a: tuple[float, float], b: tuple[float, float], fill: RGB, width: float, dash: float = 10, gap: float = 8) -> None:
        (x0, y0), (x1, y1) = a, b
        L = math.hypot(x1 - x0, y1 - y0)
        if L == 0:
            return
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        t = 0.0
        while t < L:
            e = min(L, t + dash)
            self.line([(x0 + ux * t, y0 + uy * t), (x0 + ux * e, y0 + uy * e)], fill, width, caps=False)
            t = e + gap

    def rect(self, box: Box, fill: RGB, radius: float = 0, outline: RGB | None = None, width: float = 0) -> None:
        b = tuple(v * SS for v in box)
        if radius:
            self.d.rounded_rectangle(b, radius=radius * SS, fill=fill, outline=outline, width=int(width * SS))
        else:
            self.d.rectangle(b, fill=fill, outline=outline, width=int(width * SS))

    def circle(self, cx: float, cy: float, r: float, fill: RGB, ring: RGB | None = None, ring_w: float = 0) -> None:
        if ring is not None:
            R = r + ring_w
            self.d.ellipse(((cx - R) * SS, (cy - R) * SS, (cx + R) * SS, (cy + R) * SS), fill=ring)
        self.d.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), fill=fill)

    def polygon(self, pts: list[tuple[float, float]], fill: RGB, alpha: int) -> None:
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon([(x * SS, y * SS) for x, y in pts], fill=fill + (alpha,))
        self.img.alpha_composite(layer)
        self.d = ImageDraw.Draw(self.img)

    def rect_alpha(self, box: Box, fill: RGB, alpha: int) -> None:
        x0, y0, x1, y1 = box
        self.polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], fill, alpha)

    def glow(self, cx: float, cy: float, radius: float, colour: RGB, strength: int) -> None:
        small = 8
        w, h = self.w // small, self.h // small
        mask = Image.new("L", (w, h), 0)
        r = radius / small
        ImageDraw.Draw(mask).ellipse((cx / small - r, cy / small - r, cx / small + r, cy / small + r), fill=strength)
        mask = mask.filter(ImageFilter.GaussianBlur(r * 0.5)).resize(self.img.size, Image.Resampling.BICUBIC)
        layer = Image.new("RGBA", self.img.size, colour + (255,))
        layer.putalpha(mask)
        self.img.alpha_composite(layer)
        self.d = ImageDraw.Draw(self.img)

    # label placement ---------------------------------------------------------------------------------------------
    def place(self, w: float, h: float, candidates: list[tuple[float, float]], bounds: Box, avoid_paths: bool = True,
              anchor: tuple[float, float] | None = None) -> Box:
        """The first-best top-left of a w x h label among candidates: inside bounds, clear of other labels and plotted lines."""
        best, best_score = None, None
        for i, (x, y) in enumerate(candidates):
            box = (x, y, x + w, y + h)
            score = i * 0.01
            if box[0] < bounds[0] or box[2] > bounds[2] or box[1] < bounds[1] or box[3] > bounds[3]:
                score += 1000
            score += 100 * sum(1 for b in self.labels if _overlap(box, _pad(b, 4)))
            if avoid_paths:
                score += sum(_path_hits(p, _pad(box, 3)) for p in self.paths)
            if anchor is not None:  # a label far from its point costs about one line crossing per 120 px
                ax, ay = anchor
                dx = max(box[0] - ax, 0, ax - box[2])
                dy = max(box[1] - ay, 0, ay - box[3])
                score += math.hypot(dx, dy) / 120
            if best_score is None or score < best_score:
                best, best_score = box, score
        assert best is not None
        if best_score is not None and best_score >= 100:
            self.overlaps += 1
        self.labels.append(best)
        return best


def _pad(b: Box, p: float) -> Box:
    return (b[0] - p, b[1] - p, b[2] + p, b[3] + p)


def _path_hits(path: list[tuple[float, float]], box: Box) -> int:
    hits = 0
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        if max(x0, x1) < box[0] or min(x0, x1) > box[2]:
            continue
        # sample the segment
        n = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / 4))
        for k in range(n + 1):
            t = k / n
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                hits += 1
                break
    return hits


# ------------------------------------------------------------------------------------------------------------------ layout
@dataclass
class Layout:
    m: float
    kicker: float
    hero: float
    headline: float
    headline_lh: float
    dek: float
    axis: float
    label: float
    value: float
    note: float
    line: float
    footer_h: float


LAYOUTS = {
    "x": Layout(m=64, kicker=22, hero=148, headline=40, headline_lh=50, dek=25, axis=22, label=24, value=32, note=21, line=5.5,
                footer_h=92),
    "portrait": Layout(m=58, kicker=24, hero=196, headline=46, headline_lh=56, dek=27, axis=24, label=25, value=33, note=21, line=5.5,
                       footer_h=104),
}


def _wrap(c: Canvas, text: str, f: ImageFont.FreeTypeFont, width: float) -> list[str]:
    out: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            t = f"{cur} {word}".strip()
            if c.tw(t, f) <= width or not cur:
                cur = t
            else:
                out.append(cur)
                cur = word
        out.append(cur)
    return out


def _wrap_parts(c: Canvas, text: str, f: ImageFont.FreeTypeFont, width: float) -> list[str]:
    """Wrap a ' · '-separated line at its separators (falls back to word wrap for an over-long part)."""
    out: list[str] = []
    cur = ""
    for part in text.split(" · "):
        t = f"{cur} · {part}" if cur else part
        if c.tw(t, f) <= width or not cur:
            cur = t
        else:
            out.append(cur)
            cur = part
    out.append(cur)
    return [ln for o in out for ln in (_wrap(c, o, f, width) if c.tw(o, f) > width else [o])]


def _hero_width(c: Canvas, text: str, f: ImageFont.FreeTypeFont) -> float:
    w = 0.0
    for ch in text:
        w += _minus_w(c, f) if ch in (MINUS, "-") else c.tw(ch, f)
    return w


def _minus_w(c: Canvas, f: ImageFont.FreeTypeFont) -> float:
    return c.tw("0", f) * 0.86


def _draw_hero(c: Canvas, x: float, top: float, text: str, f: ImageFont.FreeTypeFont, fill: RGB) -> Box:
    """Anton figures with a true-width minus (Anton's minus is hyphen-short). `top` is the ink top of the digits."""
    _, t0, _, b0 = c.ink("0", f)  # relative to baseline
    base = top - t0
    cx = x
    for ch in text:
        if ch in (MINUS, "-"):
            mw = _minus_w(c, f)
            th = (b0 - t0) * 0.125
            cy = base + (t0 + b0) / 2
            c.rect((cx + mw * 0.08, cy - th / 2, cx + mw * 0.92, cy + th / 2), fill)
            cx += mw
        else:
            c.text((cx, base), ch, f, fill)
            cx += c.tw(ch, f)
    return (x, top, cx, base + b0)


def _header(c: Canvas, spec: ChartSpec, L: Layout) -> float:
    """Kicker, hero, headline, dek. Returns the y where the chart may start."""
    p = c.p
    hero_col = p.role(spec.hero_role) if spec.hero else p.highlight
    x0, right = L.m, c.w - L.m
    y = L.m - 6
    # kicker: a square in the hero colour + tracked caps
    if spec.kicker:
        kf = c.font(L.kicker, "ExtraBold", note=True)
        _, t, _, b = c.ink("H", kf)
        sq = (b - t)
        base = y + sq
        c.rect((x0, base - sq, x0 + sq, base), hero_col)
        c.tracked((x0 + sq + 14, base), spec.kicker.upper(), kf, p.text2, 2.4)
        y = base + 30
    else:
        y += 6
    if c.fmt == "x":
        if spec.hero:
            hf = c.font(L.hero, hero=True)
            hw = _hero_width(c, spec.hero, hf)
            avail = right - x0 - hw - 40
            size = L.headline
            while True:
                tf = c.font(size, "ExtraBold")
                lines = _wrap(c, spec.headline, tf, avail)
                if len(lines) <= 3 or size <= 30:
                    break
                size -= 2
            lh = size * 1.24
            _, t0, _, b0 = c.ink("0", hf)
            hero_h = b0 - t0
            block = len(lines) * lh
            hero_top = y + 4
            box = _draw_hero(c, x0, hero_top, spec.hero, hf, hero_col)
            # vertically centre the claim on the hero's ink
            _, ta, _, _ = c.ink("H", tf)
            cap = -ta
            ty = hero_top + (hero_h - (block - (lh - cap))) / 2
            ty = max(ty, hero_top)
            for i, ln in enumerate(lines[:4]):
                c.text((box[2] + 40, ty + cap + i * lh), ln, tf, p.text)
            y = max(box[3], ty + block - (lh - cap)) + 22
            y = max(y, _hero_note(c, spec, L, x0, box) + 16)
        else:
            tf = c.font(54, "ExtraBold")
            lines = _wrap(c, spec.headline, tf, right - x0)
            for i, ln in enumerate(lines[:3]):
                c.text((x0, y + 50 + i * 64), ln, tf, p.text)
            y = y + 50 + (len(lines[:3]) - 1) * 64 + 26
    else:  # portrait: stacked
        if spec.hero:
            hf = c.font(L.hero, hero=True)
            while _hero_width(c, spec.hero, hf) > right - x0 and hf.size > 80 * SS:
                hf = c.font(hf.size / SS - 8, hero=True)
            box = _draw_hero(c, x0, y + 6, spec.hero, hf, hero_col)
            y = max(box[3], _hero_note(c, spec, L, x0, box)) + 28
        tf = c.font(L.headline, "ExtraBold")
        lines = _wrap(c, spec.headline, tf, right - x0)
        _, ta, _, _ = c.ink("H", tf)
        for i, ln in enumerate(lines[:4]):
            c.text((x0, y - ta + i * L.headline_lh), ln, tf, p.text)
        y = y - ta + (len(lines[:4]) - 1) * L.headline_lh + 22
    if spec.dek:
        df = c.font(L.dek, "Medium")
        for ln in _wrap(c, spec.dek, df, right - x0)[:2]:
            y += L.dek * 1.3
            c.text((x0, y), ln, df, p.muted)
        y += 8
    return y + 30


def _hero_note(c: Canvas, spec: ChartSpec, L: Layout, x: float, box: Box) -> float:
    """options.hero_note: an attribution set directly under the hero figure (e.g. 'Fear & Greed Index: alternative.me')."""
    note = spec.options.get("hero_note")
    if not note:
        return box[3]
    f = c.font(L.note + 1, "SemiBold", note=True)
    base = box[3] + 14 + L.note
    c.text((x, base), note, f, c.p.text2)
    return base + 6


def _footer(c: Canvas, spec: ChartSpec, L: Layout) -> float:
    """Kiwi on the brand disc + wordmark (left), source / as-of (right). Returns the top of the footer."""
    p = c.p
    top = c.h - L.footer_h
    c.line([(L.m, top), (c.w - L.m, top)], p.grid, 1.5, caps=False)
    r = 21 if c.fmt == "x" else 22
    cy = top + (c.h - top) / 2 - 2
    disc = Image.new("RGBA", (2 * r * SS, 2 * r * SS), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, 2 * r * SS - 1, 2 * r * SS - 1), fill=p.brand + (255,))
    k = _kiwi()
    kw = int(2 * r * SS * 0.80)
    kh = int(kw * k.height / k.width)
    ks = k.resize((kw, kh), Image.Resampling.LANCZOS)
    disc.alpha_composite(ks, ((2 * r * SS - kw) // 2, (2 * r * SS - kh) // 2 + int(r * SS * 0.04)))
    c.img.alpha_composite(disc, (int((L.m) * SS), int((cy - r) * SS)))
    c.d = ImageDraw.Draw(c.img)
    wf = c.font(25 if c.fmt == "x" else 26, "ExtraBold", note=True)
    _, t, _, b = c.ink("P", wf)
    base = cy + (b - t) / 2
    wx = L.m + 2 * r + 14
    c.text((wx, base), "Price Chat", wf, p.text)
    wend = wx + c.tw("Price Chat", wf)
    sf = c.font(L.note, "Medium", note=True)
    avail = c.w - L.m - wend - 40
    lines = _wrap_parts(c, spec.source, sf, avail)
    lh = L.note * 1.35
    y0 = base - (len(lines) - 1) * lh
    for i, ln in enumerate(lines[:2]):
        c.text((c.w - L.m, y0 + i * lh), ln, sf, p.muted, anchor="rs")
    return top


# ------------------------------------------------------------------------------------------------------------------ axes
class Scale:
    def __init__(self, lo: float, hi: float, a: float, b: float, log: bool = False):
        self.lo, self.hi, self.a, self.b, self.log = lo, hi, a, b, log

    def __call__(self, v: float) -> float:
        if self.log:
            lo, hi, v = math.log10(self.lo), math.log10(self.hi), math.log10(max(v, 1e-12))
        else:
            lo, hi = self.lo, self.hi
        return self.a + (v - lo) / ((hi - lo) or 1) * (self.b - self.a)


def _xnum(v: Any, dates: bool) -> float:
    return float(parse_date(v).toordinal()) if dates else float(v)


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _date_ticks(lo: float, hi: float) -> list[tuple[float, str, str]]:
    """(ordinal, label, kind) candidates for a date axis, coarse to fine by span."""
    d0, d1 = date.fromordinal(int(lo)), date.fromordinal(int(math.ceil(hi)))
    span = hi - lo
    out: list[tuple[float, str, str]] = []
    if span > 3 * 365:
        for y in range(d0.year, d1.year + 1):
            d = date(y, 1, 1)
            if lo <= d.toordinal() <= hi:
                out.append((d.toordinal(), str(y), "year"))
    elif span > 75:
        y, m = d0.year, d0.month
        while date(y, m, 1) <= d1:
            d = date(y, m, 1)
            if lo <= d.toordinal() <= hi:
                out.append((d.toordinal(), (f"{MONTHS[m - 1]} {y}" if m == 1 else MONTHS[m - 1]), "month"))
            m += 1
            if m == 13:
                y, m = y + 1, 1
    else:
        d = d0
        while d <= d1:
            if d.weekday() == 0 and lo <= d.toordinal() <= hi:
                out.append((d.toordinal(), f"{MONTHS[d.month - 1]} {d.day}", "day"))
            d += timedelta(days=1)
    return out


def _x_axis(c: Canvas, L: Layout, xs: Scale, dates: bool, y: float, *, unit: str = "", ticks: list[float] | None = None,
            first_year_label: bool = True) -> None:
    p = c.p
    f = c.font(L.axis, "Medium", note=True)
    if dates:
        cand = _date_ticks(xs.lo, xs.hi)
        # month axis: add the year to the first label
        if cand and cand[0][2] == "month" and first_year_label and " " not in cand[0][1]:
            d = date.fromordinal(int(cand[0][0]))
            cand[0] = (cand[0][0], f"{cand[0][1]} {d.year}", "month")
        items = [(xs(v), lab) for v, lab, _ in cand]
    else:
        tv = ticks if ticks is not None else nice_ticks(xs.lo, xs.hi, 6)[0]
        items = [(xs(v), fmt_tick(v, "num", 1) + unit) for v in tv]
    # thin out until labels have room
    for k in range(1, 13):
        sel = items[::k]
        ok = all(b[0] - c.tw(b[1], f) / 2 - (a[0] + c.tw(a[1], f) / 2) >= 28 for a, b in zip(sel, sel[1:]))
        if ok:
            items = sel
            break
    for x, lab in items:
        w = c.tw(lab, f)
        tx = min(max(x, xs.a + w / 2 if x - w / 2 < xs.a - 30 else x), xs.b + 30 - w / 2)
        c.line([(x, y), (x, y + 8)], p.faint, 1.5, caps=False)
        c.text((tx, y + 12 + L.axis), lab, f, p.muted, anchor="ms")


def _y_grid(c: Canvas, L: Layout, ys: Scale, ticks: list[float], step: float, y_format: str, x0: float, x1: float, *,
            zero: bool = True, label_x: float | None = None) -> None:
    p = c.p
    f = c.font(L.axis, "Medium", note=True)
    for v in ticks:
        y = ys(v)
        is_zero = abs(v) < step * 1e-6 and zero
        c.line([(x0, y), (x1, y)], p.zero if is_zero else p.grid, 2 if is_zero else 1.4, caps=False)
        lab = fmt_tick(v, y_format, step)
        c.text((label_x if label_x is not None else x0 - 14, y + L.axis * 0.36), lab, f, p.muted, anchor="rs")


def _y_label_w(c: Canvas, L: Layout, ticks: list[float], step: float, y_format: str) -> float:
    f = _font("sans", int(L.axis * SS), "Medium")
    return max(c.tw(fmt_tick(v, y_format, step), f) for v in ticks) + 16


# ------------------------------------------------------------------------------------------------------------------ line family
def _series_points(spec: ChartSpec) -> tuple[list[list[tuple[float, float]]], bool]:
    kind = spec.base_kind
    dates = kind != "drawdown_overlay" or bool(spec.options.get("x_dates"))
    out = []
    for s in spec.series:
        vals = list(map(float, s.values))
        if kind == "rebased_lines":
            v0 = vals[0]
            vals = [(v / v0 - 1) * 100 for v in vals]
        out.append([(_xnum(x, dates), v) for x, v in zip(s.x, vals)])
    return out, dates


def _ann_y(spec: ChartSpec, pts: list[list[tuple[float, float]]], a: Annotation, dates: bool) -> float:
    xk = _xnum(a.x, dates)
    for x, v in pts[a.series]:
        if x == xk:
            return v
    raise SpecError(f"annotation {a.text!r}: x={a.x!r} not in series")


def _label_block(c: Canvas, L: Layout, text: str, colour: RGB, big: bool = True,
                 halo: bool = False) -> tuple[float, float, Callable[[float, float, str], None]]:
    """A label: first line bold in colour (the figure), further lines muted. Returns (w, h, draw(x, y, align))."""
    lines = [ln for ln in text.split("\n")]
    fonts = [c.font(L.value if (i == 0 and big) else L.label, "ExtraBold" if i == 0 else "SemiBold") for i in range(len(lines))]
    sizes = [L.value if (i == 0 and big) else L.label for i in range(len(lines))]
    lhs = [s * 1.22 for s in sizes]
    w = max(c.tw(ln, f) for ln, f in zip(lines, fonts))
    h = sum(lhs) - (lhs[-1] - sizes[-1] * 0.78)

    def draw(x: float, y: float, align: str = "l") -> None:
        cy = y
        for i, (ln, f) in enumerate(zip(lines, fonts)):
            cy += sizes[i] * 0.78 if i == 0 else lhs[i]
            col = colour if i == 0 else c.p.text2
            if halo:  # knock the plot out behind the text so a crossing line never cuts through a figure
                bx = c.ink(ln, f, "rs" if align == "r" else "ls")
                ax = x + w if align == "r" else x
                c.rect_alpha((ax + bx[0] - 5, cy + bx[1] - 4, ax + bx[2] + 5, cy + bx[3] + 4), c.p.bg, 205)
            if align == "r":
                c.text((x + w, cy), ln, f, col, anchor="rs")
            else:
                c.text((x, cy), ln, f, col)

    return w, h, draw


def _draw_line_family(c: Canvas, spec: ChartSpec, L: Layout, box: Box) -> None:
    p = c.p
    kind = spec.base_kind
    pts, dates = _series_points(spec)
    x_lo = min(x for s in pts for x, _ in s)
    x_hi = max(x for s in pts for x, _ in s)
    if spec.options.get("x_max") is not None:
        x_hi = max(x_hi, _xnum(spec.options["x_max"], dates))
    vals = [v for s in pts for _, v in s]
    for a in spec.annotations:
        if a.kind in ("hline", "band"):
            vals.append(float(a.y))  # type: ignore[arg-type]
            if a.y2 is not None:
                vals.append(float(a.y2))
    y_lo, y_hi = min(vals), max(vals)
    if kind in ("rebased_lines", "spread_lines", "drawdown_overlay") or spec.options.get("include_zero"):
        y_lo, y_hi = min(y_lo, 0.0), max(y_hi, 0.0)
    if spec.options.get("y_min") is not None:
        y_lo = min(y_lo, float(spec.options["y_min"]))
    if spec.options.get("y_max") is not None:
        y_hi = max(y_hi, float(spec.options["y_max"]))
    pad = (y_hi - y_lo) * 0.04 or 1
    ticks, step = cover_ticks(y_lo - pad * (0 if y_lo == 0 else 1), y_hi + pad * (0 if y_hi == 0 else 1), 5 if c.fmt == "x" else 6)
    if spec.options.get("y_min") is not None:
        ticks = [t for t in ticks if t >= float(spec.options["y_min"])]
    if spec.options.get("y_max") is not None:
        ticks = [t for t in ticks if t <= float(spec.options["y_max"])]
    lo_p, hi_p = y_lo - pad * (0 if y_lo == 0 else 1), y_hi + pad * (0 if y_hi == 0 else 1)
    if len(ticks) > 3 and ticks[-1] - hi_p > step * 0.5:  # an outer tick that would only frame empty space
        ticks = ticks[:-1]
    if len(ticks) > 3 and lo_p - ticks[0] > step * 0.5:
        ticks = ticks[1:]
    t_lo, t_hi = min(ticks[0], lo_p), max(ticks[-1], hi_p)
    # ---- end labels decide the right gutter
    show_end = spec.options.get("end_labels", True)
    end_items = []
    for i, (s, sp) in enumerate(zip(spec.series, pts)):
        if not show_end or s.label == "":
            continue
        txt = s.label or s.name
        if spec.options.get("end_values", kind in ("rebased_lines", "ratio_line", "spread_lines", "rolling_line")):
            txt = f"{fmt_value(sp[-1][1], spec.y_format)}\n{txt}"
        colour = _context_tone(p, spec, i) if s.role == "context" else _series_colour(p, s, sp[-1][1])
        end_items.append((i, txt, colour, sp[-1][0] >= x_hi - 1e-9))
    gutter = 0.0
    for i, txt, colour, at_right in end_items:
        if at_right:
            w, _, _ = _label_block(c, L, txt, colour)
            gutter = max(gutter, w + 22)
    # a marker on a series' last point (align auto/right) reads like an end label: it gets the gutter too
    gutter_marks: set[int] = set()
    for k, a in enumerate(spec.annotations):
        if a.kind == "marker" and a.align in ("auto", "right"):
            sp = pts[a.series]
            if _xnum(a.x, dates) == sp[-1][0] and sp[-1][0] >= x_hi - 1e-9:
                w, _, _ = _label_block(c, L, a.text, p.role(a.role))
                gutter = max(gutter, w + 44)
                gutter_marks.add(k)
    ylw = _y_label_w(c, L, ticks, step, spec.y_format)
    x0, x1 = box[0] + ylw, box[2] - gutter
    y0, y1 = box[1] + 6, box[3] - L.axis - 22
    keys = [a for a in spec.annotations if a.kind == "band" and a.text]
    if keys:  # a band is explained by a key above the plot, never by text on top of the data
        kf = c.font(L.label, "Bold")
        kx = x1
        for a in reversed(keys):
            col = p.role(a.role)
            tw = c.tw(a.text, kf)
            c.text((kx, y0 + L.label * 0.8), a.text, kf, col, anchor="rs")
            c.rect((kx - tw - 30, y0 + L.label * 0.8 - 17, kx - tw - 12, y0 + L.label * 0.8 + 1), col, radius=3)
            kx -= tw + 60
        y0 += L.label * 1.9
    xs = Scale(x_lo, x_hi, x0, x1)
    ys = Scale(t_lo, t_hi, y1, y0)
    # ---- bands under the grid
    for a in spec.annotations:
        if a.kind == "band":
            col = p.role(a.role)
            c.rect_alpha((x0, ys(max(a.y, a.y2)), x1, ys(min(a.y, a.y2))), col, 34 if c.theme == "dark" else 40)  # type: ignore[arg-type]
        if a.kind == "xband":
            col = p.role(a.role)
            c.rect_alpha((xs(_xnum(a.x, dates)), y0, xs(_xnum(a.x2, dates)), y1), col, 26)
    _y_grid(c, L, ys, [t for t in ticks if t_lo <= t <= t_hi], step, spec.y_format, x0, x1, zero=kind != "series_with_marker")
    _x_axis(c, L, xs, dates, y1, unit=spec.options.get("x_unit", ""))
    # ---- reference lines
    for a in spec.annotations:
        if a.kind == "hline":
            col = p.role(a.role) if a.role != "context" else p.muted
            yy = ys(float(a.y))  # type: ignore[arg-type]
            c.dashed((x0, yy), (x1, yy), col, 2.2)
            c.paths += [[(x0, yy), (x1, yy)]] * 3  # a reference line weighs like three crossings
        if a.kind == "vline":
            xx = xs(_xnum(a.x, dates))
            c.dashed((xx, y0), (xx, y1), p.muted, 2)
    # ---- fills
    scr = [[(xs(x), ys(v)) for x, v in sp] for sp in pts]
    if kind == "spread_lines":
        s0 = scr[0]
        zy = ys(0)
        col = p.role(spec.series[0].role, pts[0][-1][1])
        c.polygon([(s0[0][0], zy), *s0, (s0[-1][0], zy)], col, 40 if c.theme == "dark" else 46)
    sb = spec.options.get("shade_between")
    if kind == "rebased_lines" and sb:
        i, j = sb
        a_pts, b_pts = scr[i], scr[j]
        col = p.role(spec.options.get("shade_role", "neg"))
        c.polygon([*a_pts, *reversed(b_pts)], col, 46 if c.theme == "dark" else 52)
    # ---- lines: context first, highlight last
    order = sorted(range(len(spec.series)), key=lambda k: spec.series[k].role != "context")
    for k in order:
        s = spec.series[k]
        sp = scr[k]
        col = _series_colour(p, s, pts[k][-1][1])
        if s.role == "context":
            col = _context_tone(p, spec, k)
        w = s.width or (L.line if s.role != "context" else 3.2)
        c.line(sp, col, w)
        c.paths.append(sp)
    below = spec.options.get("highlight_below")
    if below is not None:
        for (x, v), (sx, sy) in zip(pts[0], scr[0]):
            if v <= below:
                c.circle(sx, sy, 2.6, p.role(spec.options.get("below_role", "neg")))
                c.paths.append([(sx - 3, sy), (sx + 3, sy)])
    # ---- end labels
    _end_labels(c, L, spec, end_items, scr, (x0, y0, x1, y1), box)
    # ---- hline labels (right-aligned above the line, inside the plot)
    lf = c.font(L.label, "Bold")
    for a in spec.annotations:
        if a.kind == "hline" and a.text:
            col = p.role(a.role) if a.role != "context" else p.text2
            yy = ys(float(a.y if a.kind == "hline" else max(a.y, a.y2)))  # type: ignore[arg-type]
            w = c.tw(a.text, lf)
            h = L.label * 1.1
            cands = [(x1 - w - 8, yy - h - 8), (x1 - w - 8, yy + 8), (x0 + 12, yy - h - 8), (x0 + 12, yy + 8),
                     ((x0 + x1) / 2 - w / 2, yy - h - 8), ((x0 + x1) / 2 - w / 2, yy + 8)]
            bx = c.place(w, h, cands, (x0, y0 - 4, x1, y1))
            c.text((bx[0], bx[1] + L.label * 0.86), a.text, lf, col)
    # ---- markers: every dot is an obstacle before any label is placed
    marks = []
    for k, a in enumerate(spec.annotations):
        if k in gutter_marks:
            a = replace(a, align="right")
        if a.kind in ("marker", "point_label"):
            v = _ann_y(spec, pts, a, dates)
            sx, sy = xs(_xnum(a.x, dates)), ys(v)
            marks.append((a, sx, sy))
            c.labels.append((sx - 12, sy - 12, sx + 12, sy + 12))
    for a, sx, sy in marks:
        _marker(c, L, a, sx, sy, (x0, y0 - 4, x1 + gutter, y1), big=a.kind == "marker")


def _series_colour(p: Palette, s: Series, last: float) -> RGB:
    return p.role(s.role, last)


def _context_tone(p: Palette, spec: ChartSpec, k: int) -> RGB:
    """Several context lines get distinct greys (older = darker) so each can be matched to its label."""
    ctx = [i for i, s in enumerate(spec.series) if s.role == "context"]
    if len(ctx) < 2:
        return p.context
    t = ctx.index(k) / (len(ctx) - 1)
    return mix(p.context2, p.text2, 0.15 + 0.6 * t)


def _end_labels(c: Canvas, L: Layout, spec: ChartSpec, items: list, scr: list, plot: Box, box: Box) -> None:
    right = [(i, t, col) for i, t, col, at in items if at]
    inline = [(i, t, col) for i, t, col, at in items if not at]
    blocks = []
    for i, txt, col in right:
        w, h, draw = _label_block(c, L, txt, col)
        y = scr[i][-1][1] - L.value * 0.5
        blocks.append([y, h, w, draw, i])
    blocks.sort(key=lambda b: b[0])
    # resolve collisions: push down, then pull up from the bottom
    lo, hi = plot[1] - 10, box[3] - 4
    for k in range(1, len(blocks)):
        prev = blocks[k - 1]
        blocks[k][0] = max(blocks[k][0], prev[0] + prev[1] + 12)
    for k in range(len(blocks) - 1, -1, -1):
        lim = hi if k == len(blocks) - 1 else blocks[k + 1][0] - 12
        blocks[k][0] = min(blocks[k][0], lim - blocks[k][1])
        blocks[k][0] = max(blocks[k][0], lo)
    for y, h, w, draw, i in blocks:
        x = plot[2] + 18
        draw(x, y, "l")
        c.labels.append((x, y, x + w, y + h))
    for i, txt, col in inline:
        w, h, draw = _label_block(c, L, txt, col, big=False)
        ex, ey = scr[i][-1]
        cands = [(ex + 14, ey - h / 2), (ex + 14, ey - h - 6), (ex + 14, ey + 6), (ex - w / 2, ey + 14), (ex - w / 2, ey - h - 14),
                 (ex - w - 14, ey - h / 2)]
        bx = c.place(w, h, cands, (plot[0], plot[1] - 6, box[2], plot[3]))
        draw(bx[0], bx[1], "l")


def _marker(c: Canvas, L: Layout, a: Annotation, sx: float, sy: float, bounds: Box, big: bool = True) -> None:
    p = c.p
    col = p.role(a.role)
    if big:
        c.circle(sx, sy, 9, col, ring=p.ring, ring_w=5)
    else:
        c.circle(sx, sy, 6, col, ring=p.ring, ring_w=3.5)
    if not a.text:
        return
    w, h, draw = _label_block(c, L, a.text, col if big else p.text, big=big, halo=True)
    gap = 22 if big else 14
    al = a.align

    def cands_at(g: float, dx: float) -> dict[str, tuple[float, float]]:
        return {
            "above_r": (sx + dx, sy - h - g),
            "above_l": (sx - w - dx, sy - h - g),
            "below_r": (sx + dx, sy + g),
            "below_l": (sx - w - dx, sy + g),
            "right": (sx + g + 4, sy - h / 2),
            "left": (sx - w - g - 4, sy - h / 2),
            "above": (sx - w / 2, sy - h - g),
            "below": (sx - w / 2, sy + g),
        }

    pref = {
        "above": ["above", "above_l", "above_r"],
        "below": ["below", "below_l", "below_r"],
        "left": ["left", "above_l", "below_l"],
        "right": ["right", "above_r", "below_r"],
    }.get(al, [])
    keys = pref + [k for k in ("above_l", "above_r", "left", "right", "below_l", "below_r", "above", "below") if k not in pref]
    cands = []
    for g, dx in ((gap, 10), (gap * 2.5, 30), (gap * 4.5, 60), (gap * 7, 110), (gap * 10, 170), (gap * 4, 280), (gap * 2, 420)):
        ca = cands_at(g, dx)
        cands += [ca[k] for k in keys]
    bx = c.place(w, h, cands, bounds, anchor=(sx, sy))
    align = "r" if bx[2] <= sx else "l"
    # leader from the dot towards the label's nearest corner/edge
    lx = min(max(sx, bx[0]), bx[2])
    ly = bx[3] + 4 if bx[3] <= sy else (bx[1] - 4 if bx[1] >= sy else sy)
    if math.hypot(lx - sx, ly - sy) > 16:
        c.line([(sx, sy), (lx, ly)], col if big else p.muted, 1.8, caps=False)
        c.paths.append([(sx, sy), (lx, ly)])
    draw(bx[0], bx[1], align)


# ------------------------------------------------------------------------------------------------------------------ histogram
def _draw_histogram(c: Canvas, spec: ChartSpec, L: Layout, box: Box) -> None:
    p = c.p
    obs = [float(v) for v in spec.series[0].values]
    bw = float(spec.options["bin_width"])
    marker = next((a for a in spec.annotations if a.kind == "marker"), None)
    tail = spec.options.get("tail", "left")
    lo_b = math.floor(min(obs) / bw) * bw
    hi_b = math.ceil(max(obs) / bw) * bw
    if hi_b == lo_b:
        hi_b += bw
    nb = int(round((hi_b - lo_b) / bw))
    counts = [0] * nb
    for v in obs:
        k = min(nb - 1, int((v - lo_b) // bw))
        counts[k] += 1
    total = len(obs)
    ticks, step = cover_ticks(0, max(counts) / total * 100 * 1.3, 4)
    ylw = _y_label_w(c, L, ticks, step, "pct")
    x0, x1 = box[0] + ylw, box[2] - 8
    y0, y1 = box[1] + 6, box[3] - L.axis - 22
    xs = Scale(lo_b, hi_b, x0, x1)
    ys = Scale(0, ticks[-1], y1, y0)
    f = c.font(L.axis, "Medium", note=True)
    for v in ticks:
        y = ys(v)
        c.line([(x0, y), (x1, y)], p.zero if v == 0 else p.grid, 2 if v == 0 else 1.4, caps=False)
        c.text((x0 - 14, y + L.axis * 0.36), f"{v:.0f}%" if step >= 1 else f"{v:.1f}%", f, p.muted, anchor="rs")
    mv = float(marker.x) if marker else None
    col_hi = p.role(marker.role) if marker else p.highlight
    for k, n in enumerate(counts):
        a, b = lo_b + k * bw, lo_b + (k + 1) * bw
        in_tail = mv is not None and ((tail == "left" and b <= mv + 1e-9) or (tail == "right" and a >= mv - 1e-9))
        contains = mv is not None and a <= mv < b
        colour = col_hi if (in_tail or contains) else p.context2 if c.theme == "dark" else p.context
        if n:
            c.rect((xs(a) + 1.5, ys(n / total * 100), xs(b) - 1.5, y1), colour)
    _x_axis(c, L, xs, False, y1, unit="%", ticks=nice_ticks(lo_b, hi_b, 8)[0])
    # fix the percent tick text: _x_axis printed numbers + "%"; zero line
    zx = xs(0) if lo_b < 0 < hi_b else None
    if zx is not None:
        c.line([(zx, y0), (zx, y1)], p.zero, 1.5, caps=False)
        c.paths += [[(zx, y0), (zx, y1)]] * 3
    for a in spec.annotations:
        if a.kind == "vline":
            xx = xs(float(a.x))
            c.dashed((xx, y0), (xx, y1), p.muted, 2)
    if marker:
        mx = xs(mv)  # type: ignore[arg-type]
        top = y0 + 8
        c.line([(mx, top), (mx, y1)], col_hi, 3, caps=False)
        c.circle(mx, top, 9, col_hi, ring=p.ring, ring_w=5)
        c.paths.append([(mx, top), (mx, y1)])
        # the bars are obstacles for the label (only while placing it)
        saved = list(c.labels)
        for k, n in enumerate(counts):
            if n:
                c.labels.append((xs(lo_b + k * bw), ys(n / total * 100), xs(lo_b + (k + 1) * bw), y1))
        w, h, draw = _label_block(c, L, marker.text, col_hi, halo=True)
        bx = c.place(w, h, [(mx + 22, top - 4), (mx - w - 22, top - 4), (mx + 22, top + 40), (mx - w - 22, top + 40)],
                     (x0, y0 - 10, x1, y1))
        c.labels = saved + [bx]
        draw(bx[0], bx[1], "r" if bx[2] <= mx else "l")
    for a in spec.annotations:
        if a.kind == "point_label" and a.text:
            xx = xs(float(a.x))
            lf = c.font(L.label, "SemiBold")
            c.text((xx, y0 + L.label), a.text, lf, p.text2, anchor="ms")


# ------------------------------------------------------------------------------------------------------------------ seasonality
def _draw_bars(c: Canvas, spec: ChartSpec, L: Layout, box: Box) -> None:
    p = c.p
    s = spec.series[0]
    vals = [float(v) for v in s.values]
    cats = [str(x) for x in s.x]
    partial = set(map(str, spec.options.get("partial", [])))
    hl = set(map(str, spec.options.get("highlight", partial)))
    lo, hi = min(0.0, *vals), max(0.0, *vals)
    ticks, step = cover_ticks(lo, hi, 5)
    plot_h = max(1.0, box[3] - box[1] - L.axis - 32)
    need_top = L.label * 1.7 + (L.label * 1.2 if any(c_ in partial and v >= 0 for c_, v in zip(cats, vals)) else 0)
    need_bot = L.label * 1.7 + (L.label * 1.2 if any(c_ in partial and v < 0 for c_, v in zip(cats, vals)) else 0)
    for _ in range(4):  # add a tick only while a value label would not fit inside the range
        ppu = plot_h / (ticks[-1] - ticks[0])
        if hi > 0 and (ticks[-1] - hi) * ppu < need_top:
            ticks.append(round(ticks[-1] + step, 10))
        elif lo < 0 and (lo - ticks[0]) * ppu < need_bot:
            ticks.insert(0, round(ticks[0] - step, 10))
        else:
            break
    ylw = _y_label_w(c, L, ticks, step, spec.y_format)
    x0, x1 = box[0] + ylw, box[2] - 4
    y0, y1 = box[1] + 6, box[3] - L.axis - 26
    ys = Scale(ticks[0], ticks[-1], y1, y0)
    _y_grid(c, L, ys, ticks, step, spec.y_format, x0, x1)
    n = len(vals)
    slot = (x1 - x0) / n
    bw = slot * 0.62
    vf = c.font(L.label + (2 if c.fmt == "x" else 0), "ExtraBold")
    cf = c.font(L.axis + 2, "SemiBold", note=True)
    zy = ys(0)
    for k, (cat, v) in enumerate(zip(cats, vals)):
        cx = x0 + slot * (k + 0.5)
        is_hl = cat in hl
        if is_hl:
            col = p.role(spec.hero_role if spec.hero_role != "highlight" else "highlight", v)
        else:
            col = mix(p.bg, p.pos if v >= 0 else p.neg, 0.42 if c.theme == "dark" else 0.5)
        top, bot = sorted((ys(v), zy))
        if cat in partial:
            c.rect((cx - bw / 2, top, cx + bw / 2, bot), mix(p.bg, col, 0.28))
            # hatch
            step_h = 11
            xx = cx - bw / 2 - (bot - top)
            while xx < cx + bw / 2:
                a = (max(xx, cx - bw / 2), top + max(0, (cx - bw / 2) - xx))
                e_x = min(xx + (bot - top), cx + bw / 2)
                b_ = (e_x, top + (e_x - xx))
                c.line([a, b_], col, 2.2, caps=False)
                xx += step_h
            c.rect((cx - bw / 2, top, cx + bw / 2, bot), None, outline=col, width=2.5)  # type: ignore[arg-type]
        else:
            c.rect((cx - bw / 2, top, cx + bw / 2, bot), col)
        lab = fmt_value(v, spec.y_format, spec.options.get("digits", 0))
        tcol = col if is_hl else p.text2
        if v >= 0:
            c.text((cx, top - 12), lab, vf, tcol, anchor="ms")
        else:
            c.text((cx, bot + 12 + L.label * 0.8), lab, vf, tcol, anchor="ms")
        c.text((cx, y1 + 14 + L.axis), cat, c.font(L.axis + 2, "Bold" if is_hl else "Medium", note=True), p.text if is_hl else p.muted, anchor="ms")
        if cat in partial:
            sf = c.font(L.axis, "SemiBold", note=True)
            note = spec.options.get("partial_note", "so far")
            c.text((cx, (bot + 12 + L.label * 0.8 + L.axis + 4) if v < 0 else top - 16 - L.label), note, sf, tcol, anchor="ms")
    _ = cf


# ------------------------------------------------------------------------------------------------------------------ rank table
def _draw_rank(c: Canvas, spec: ChartSpec, L: Layout, box: Box) -> None:
    p = c.p
    rows = spec.rows
    n = len(rows)
    log = spec.options.get("scale") == "log"
    if spec.options.get("ref_label"):
        box = (box[0], box[1] + 18, box[2], box[3])
    step = (box[3] - box[1]) / n
    size = min(34, max(20, step * 0.56))
    nf = c.font(size, "SemiBold")
    sf = c.font(size * 0.78, "Medium")
    vf = c.font(size * 1.02, "ExtraBold")
    label_w = max(c.tw(r.label, nf) + (c.tw("  " + r.sublabel, sf) if r.sublabel else 0) for r in rows) + 28
    disp = [r.display or fmt_value(r.value, spec.y_format) for r in rows]
    val_w = max(c.tw(d, vf) for d in disp) + 20
    vals = [r.value for r in rows]
    ref = spec.options.get("ref_value")
    if log:
        lo, hi = min(vals + ([ref] if ref else [])) / 1.5, max(vals + ([ref] if ref else [])) * 1.15
        xa, xb = box[0] + label_w, box[2] - val_w
        xs = Scale(lo, hi, xa, xb, log=True)
        zero_x = xa
    else:
        lo, hi = min(0.0, *vals), max(0.0, *vals)
        neg_room = val_w if lo < 0 else 0
        xa, xb = box[0] + label_w + neg_room, box[2] - (val_w if hi > 0 else 8)
        xs = Scale(lo, hi, xa, xb)
        zero_x = xs(0)
    for k, r in enumerate(rows):
        y = box[1] + k * step
        mid = y + step / 2
        col = p.role(r.role, r.value)
        if not r.highlight and spec.options.get("mute_unhighlighted"):
            col = p.context
        if k % 2 == 0:
            c.rect_alpha((box[0] - 12, y + 1, box[2] + 12, y + step - 1), p.text, 7 if c.theme == "dark" else 10)
        _, t, _, b = c.ink("Hg", nf)
        base = mid + (-t) / 2 - 1
        lx = box[0]
        c.text((lx, base), r.label, nf, p.text if (r.highlight or not spec.options.get("mute_unhighlighted")) else p.text2)
        if r.sublabel:
            c.text((lx + c.tw(r.label, nf) + c.tw("  ", sf), base), r.sublabel, sf, p.muted)
        bh = min(step * 0.56, 30)
        v = r.value
        if log:
            a_, b_ = xa, xs(v)
        else:
            a_, b_ = sorted((zero_x, xs(v)))
        c.rect((a_, mid - bh / 2, max(b_, a_ + 3), mid + bh / 2), col, radius=3)
        d = disp[k]
        if log or v >= 0:
            c.text((max(b_, a_ + 3) + 12, base), d, vf, col if (r.highlight or not spec.options.get("mute_unhighlighted")) else p.text2)
        else:
            c.text((a_ - 12, base), d, vf, col, anchor="rs")
    if not log and lo < 0 < hi:
        c.line([(zero_x, box[1] - 6), (zero_x, box[3] + 6)], p.zero, 2, caps=False)
    if ref is not None:
        rx = xs(float(ref))
        c.dashed((rx, box[1] - 4), (rx, box[3] + 4), p.text2, 2.2, dash=8, gap=7)
        if spec.options.get("ref_label"):
            lf = c.font(L.axis, "Bold", note=True)
            c.text((rx, box[1] - 14), spec.options["ref_label"], lf, p.text2, anchor="ms")


# ------------------------------------------------------------------------------------------------------------------ heat grid
def _draw_heat(c: Canvas, spec: ChartSpec, L: Layout, box: Box) -> None:
    p = c.p
    rows = spec.rows
    cols = int(spec.options.get("columns") or 0)
    if not cols:
        lo_c, hi_c = (11, 15) if c.fmt == "x" else (7, 9)
        cols = min(range(lo_c, hi_c + 1), key=lambda k: (math.ceil(len(rows) / k) * k - len(rows), -k))
    nrows = math.ceil(len(rows) / cols)
    gap = 6
    legend_h = 44
    tw_ = (box[2] - box[0] - gap * (cols - 1)) / cols
    th = min((box[3] - box[1] - legend_h - gap * (nrows - 1)) / nrows, tw_ * 0.9)
    cap = float(spec.options.get("cap", 20))
    sym_size = min(26, th * 0.32)
    sf = c.font(sym_size, "ExtraBold")
    vf = c.font(sym_size * 0.86, "SemiBold")
    for k, r in enumerate(rows):
        i, j = divmod(k, cols)
        x = box[0] + j * (tw_ + gap)
        y = box[1] + i * (th + gap)
        t = min(1.0, abs(r.value) / cap)
        base = p.pos if r.value >= 0 else p.neg
        col = mix(mix(p.bg, p.text, 0.06), base, 0.22 + 0.78 * t)
        c.rect((x, y, x + tw_, y + th), col, radius=5)
        if r.highlight:
            c.rect((x - 1, y - 1, x + tw_ + 1, y + th + 1), None, radius=6, outline=p.text, width=3)  # type: ignore[arg-type]
        txt_col = (255, 255, 255) if c.theme == "dark" or t > 0.45 else p.text
        lab = r.label[:6]
        while c.tw(lab, sf) > tw_ - 10 and len(lab) > 2:
            lab = lab[:-1]
        c.text((x + tw_ / 2, y + th * 0.47), lab, sf, txt_col, anchor="ms")
        c.text((x + tw_ / 2, y + th * 0.47 + sym_size * 1.12), r.display or fmt_value(r.value, spec.y_format, 0), vf,
               mix(txt_col, col, 0.18), anchor="ms")
    # legend: a diverging ramp
    ly = box[1] + nrows * (th + gap) + 14
    lw = min(420, box[2] - box[0])
    lx = box[2] - lw
    nstep = 40
    for k in range(nstep):
        v = -cap + 2 * cap * (k + 0.5) / nstep
        t = min(1.0, abs(v) / cap)
        col = mix(mix(p.bg, p.text, 0.06), p.pos if v >= 0 else p.neg, 0.22 + 0.78 * t)
        c.rect((lx + lw * k / nstep, ly, lx + lw * (k + 1) / nstep + 0.5, ly + 12), col)
    f = c.font(L.axis, "Medium", note=True)
    c.text((lx, ly + 16 + L.axis), signed(-cap, 0) + " or less", f, p.muted)
    c.text((lx + lw, ly + 16 + L.axis), signed(cap, 0) + " or more", f, p.muted, anchor="rs")
    if spec.options.get("legend_note"):
        c.text((box[0], ly + 14 + L.axis * 0.5), spec.options["legend_note"], c.font(L.axis, "Medium", note=True), p.muted)


# ------------------------------------------------------------------------------------------------------------------ move card
def _draw_move(c: Canvas, spec: ChartSpec, L: Layout, box: Box) -> None:
    """A lollipop timeline of every comparable event (series[0]: dates, values), today in the highlight colour."""
    p = c.p
    s = spec.series[0]
    pts = [(_xnum(x, True), float(v)) for x, v in zip(s.x, s.values)]
    vals = [v for _, v in pts]
    thr = spec.options.get("threshold")
    lo = min(vals + ([float(thr)] if thr is not None else []))
    ticks, step = cover_ticks(lo * 1.04, 0, 4)
    ylw = _y_label_w(c, L, ticks, step, spec.y_format)
    x0, x1 = box[0] + ylw, box[2] - 12
    y0, y1 = box[1] + 10, box[3] - L.axis - 22
    x_lo = _xnum(spec.options.get("x_min", s.x[0]), True)
    x_hi = _xnum(spec.options.get("x_max", s.x[-1]), True)
    xs = Scale(x_lo - 30, x_hi + 30, x0 + 10, x1 - 10)
    ys = Scale(ticks[0], 0, y1, y0)
    _y_grid(c, L, ys, ticks, step, spec.y_format, x0, x1)
    _x_axis(c, L, xs, True, y1)
    if thr is not None:
        ty = ys(float(thr))
        c.dashed((x0, ty), (x1, ty), p.muted, 2)
    mk = {_xnum(a.x, True): a for a in spec.annotations if a.kind == "marker"}
    for x, v in pts:
        if x in mk:
            continue
        sx, sy = xs(x), ys(v)
        c.line([(sx, ys(0)), (sx, sy)], p.context, 2.4, caps=False)
        c.circle(sx, sy, 6.5, p.context)
        c.paths.append([(sx, ys(0)), (sx, sy)])
    for x, a in mk.items():
        v = next(vv for xx, vv in pts if xx == x)
        sx, sy = xs(x), ys(v)
        col = p.role(a.role)
        c.line([(sx, ys(0)), (sx, sy)], col, 4, caps=False)
        c.paths.append([(sx, ys(0)), (sx, sy)])
        _marker(c, L, a, sx, sy, (x0, y0, x1, y1))
    for a in spec.annotations:
        if a.kind == "point_label":
            x = _xnum(a.x, True)
            v = next(vv for xx, vv in pts if xx == x)
            _marker(c, L, a, xs(x), ys(v), (x0, y0, x1, y1), big=False)
    if thr is not None and spec.options.get("threshold_label"):
        lf = c.font(L.label, "Bold")
        t = spec.options["threshold_label"]
        w = c.tw(t, lf)
        ty = ys(float(thr))
        bx = c.place(w, L.label * 1.1, [(x0 + 12, ty - L.label * 1.1 - 8), (x1 - w - 8, ty - L.label * 1.1 - 8), (x0 + 12, ty + 8)],
                     (x0, y0, x1, y1))
        c.text((bx[0], bx[1] + L.label * 0.86), t, lf, p.text2)


# ------------------------------------------------------------------------------------------------------------------ entry
DRAW: dict[str, Callable[[Canvas, ChartSpec, Layout, Box], None]] = {
    "rank_table": _draw_rank,
    "rebased_lines": _draw_line_family,
    "spread_lines": _draw_line_family,
    "rolling_line": _draw_line_family,
    "ratio_line": _draw_line_family,
    "series_with_marker": _draw_line_family,
    "drawdown_overlay": _draw_line_family,
    "histogram_marker": _draw_histogram,
    "seasonality_bars": _draw_bars,
    "heat_grid": _draw_heat,
    "move_card": _draw_move,
}


def render_info(spec: ChartSpec, fmt: str = "x", theme: str | None = None) -> tuple[bytes, dict[str, Any]]:
    """Validate, draw, and return (PNG bytes, meta). Raises SpecError when the spec cannot be drawn honestly."""
    validate(spec)
    theme = theme or spec.theme
    if theme not in PALETTES:
        raise SpecError(f"unknown theme {theme!r}")
    c = Canvas(fmt, theme)
    L = LAYOUTS[fmt]
    hero_col = c.p.role(spec.hero_role) if spec.hero else c.p.highlight
    c.glow(c.w * 0.12, c.h * 0.08, max(c.w, c.h) * 0.42, hero_col, 20 if theme == "dark" else 14)
    top = _header(c, spec, L)
    ftop = _footer(c, spec, L)
    box = (L.m, top, c.w - L.m, ftop - (34 if spec.base_kind not in ("rank_table", "heat_grid") else 30))
    if box[3] - box[1] < 200:
        raise SpecError("the headline leaves no room for the chart: shorten it")
    DRAW[spec.base_kind](c, spec, L, box)
    out = c.img.resize((c.w, c.h), Image.Resampling.LANCZOS).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    scale = PHONE / c.w
    meta = {
        "template": f"intel/{spec.base_kind}",
        "version": RENDERER_VERSION,
        "spec_version": spec.version,
        "format": fmt,
        "theme": theme,
        "size": [c.w, c.h],
        "phone_min_text_px": round(min(c.sizes) * scale, 1) if c.sizes else None,
        "phone_note_px": round(min(c.notes) * scale, 1) if c.notes else None,
        "label_overlaps": c.overlaps,
    }
    return buf.getvalue(), meta


def render(spec: ChartSpec, fmt: str = "x") -> bytes:
    """PNG bytes for `spec` in `fmt` ("x" 1600x900 | "portrait" 1080x1350). Raises chart_spec.SpecError on a bad spec."""
    return render_info(spec, fmt)[0]


__all__ = ["render", "render_info", "RENDERER_VERSION", "PALETTES", "fmt_value", "signed"]

_ = Row
