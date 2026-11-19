"""Price Chat premium visual templates (owner, 2026-11-19: "I want Price Chat to look like a premium financial breaking-news brand, not a
broken RSS bot").

Every template is drawn from real numbers the caller has already verified, in two formats:
- "x"  1600 x 900 (16:9), the X card and the wide format;
- "tg" 1280 x 1280 (1:1), Telegram: a square photo fills the width of a phone's chat, so the figures print about 1.8x larger than a
  16:9 picture would.

Design rules (and the tests that hold them, tests/test_newsdesk_visual_editor_20261119.py):
- One idea per picture: the asset or company, ONE huge verified figure, the real data behind it, the source. Minimal words.
- Market crashes are red with a downward arrow, pumps green with an upward arrow; the arrow and the colour come from the sign of the
  verified number, never from the headline's words.
- Mobile first: on a 390 px wide phone the hero figure is at least ~60 px tall and the smallest label at least ~9 px (MIN_LABEL_PX,
  MIN_HERO_PX on `readable`). Thick lines (7 px at 1600 wide), high contrast on near-black ink.
- Nothing illustrative: a line is the series itself, a bar's length is its value on one shared axis, the arrow is the sign of the change.
  Asset glyphs (a gold ingot, an oil drop, the Bitcoin sign) are icons, drawn as icons, never as photographs. The outage card's warning
  mark is a symbol, not a timeline: it plots nothing.
- When the data is missing or does not agree with the words, the template refuses (TemplateRefused) and the post goes without a
  picture rather than with an invented chart.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.newsdesk import editorial_layouts as el

FORMATS = {"x": (1600, 900), "tg": (1280, 1280)}
INK = (7, 9, 13)
PANEL = (16, 20, 27)
TEXT = (246, 247, 249)
MUTED = (146, 155, 168)
RULE = (38, 44, 54)
RED = (255, 64, 64)
GREEN = (22, 214, 122)
AMBER = (255, 178, 36)
BLUE = (70, 140, 255)
SERIES = (AMBER, BLUE)
MIN_LABEL_PX = 8.0  # the smallest ESSENTIAL type (name, sublines, chips), measured on a 390 px wide phone
MIN_NOTE_PX = 5.0  # footnotes (source line, axis labels, the brand tag): still legible when zoomed, never the message
MIN_HERO_PX = 56.0  # the hero figure on that phone
PHONE = 390
# a company's name set as a wordmark, or two figures side by side, read at a smaller size than one lone percentage
HERO_FLOOR = {"outage": 32.0, "battle": 34.0, "then_now": 44.0}
VERSION = "pc-premium-1"


class TemplateRefused(ValueError):
    """The template's numbers are missing or disagree with what the post says: no picture rather than a wrong one."""


@dataclass
class Sheet:
    img: Image.Image
    d: ImageDraw.ImageDraw
    fmt: str
    w: int
    h: int
    m: int
    sizes: list[int]
    notes: list[int] = None  # type: ignore[assignment]

    def font(self, size: int, weight: str = "SemiBold", note: bool = False) -> ImageFont.FreeTypeFont:
        if note:
            self.notes = [*(self.notes or []), size]
        else:
            self.sizes.append(size)
        return el.sans(size, weight)

    def note(self, size: int, weight: str = "Medium") -> ImageFont.FreeTypeFont:
        return self.font(size, weight, note=True)

    def hero(self, size: int) -> ImageFont.FreeTypeFont:
        from app.newsdesk.visual import _anton

        self.sizes.append(size)
        return _anton(size)

    def meta(self, template: str, hero: int, **extra: Any) -> dict[str, Any]:
        small = min(self.sizes) if self.sizes else 0
        note = min(self.notes or [small])
        scale = PHONE / self.w
        return {"template": template, "template_version": VERSION, "format": self.fmt, "size": [self.w, self.h], "min_type": small,
                "hero_type": hero, "phone_min_px": round(small * scale, 1), "phone_note_px": round(note * scale, 1),
                "phone_hero_px": round(hero * scale, 1), **extra}


def readable(meta: dict[str, Any]) -> str | None:
    """Why a template picture would be hard to read on a phone, or None."""
    if meta.get("template_version") != VERSION:
        return "not a current Price Chat template"
    if float(meta.get("phone_min_px") or 0) < MIN_LABEL_PX:
        return f"smallest type {meta.get('phone_min_px')} px on a phone (< {MIN_LABEL_PX})"
    if float(meta.get("phone_note_px") or 0) < MIN_NOTE_PX:
        return f"footnote type {meta.get('phone_note_px')} px on a phone (< {MIN_NOTE_PX})"
    floor = HERO_FLOOR.get(str(meta.get("template")), MIN_HERO_PX)
    if meta.get("hero_type") and float(meta.get("phone_hero_px") or 0) < floor:
        return f"hero figure {meta.get('phone_hero_px')} px on a phone (< {floor})"
    return None


# ------------------------------------------------------------------------------------------------------------------- frame
def _jpeg(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=93, optimize=True, progressive=True)
    return out.getvalue()


def _glow(w: int, h: int, centre: tuple[float, float], colour: tuple[int, int, int], radius: float, strength: int) -> Image.Image:
    """A soft coloured light on the ink, behind the data: the mood of the move, not a mark."""
    mask = Image.new("L", (w, h), 0)
    cx, cy = centre
    ImageDraw.Draw(mask).ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=strength)
    mask = mask.filter(ImageFilter.GaussianBlur(radius * 0.45))
    layer = Image.new("RGB", (w, h), colour)
    base = Image.new("RGB", (w, h), INK)
    base.paste(layer, (0, 0), mask)
    return base


def sheet(fmt: str, accent: tuple[int, int, int], glow_at: tuple[float, float] = (0.78, 0.55), glow: int = 70) -> Sheet:
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    w, h = FORMATS[fmt]
    img = _glow(w, h, (w * glow_at[0], h * glow_at[1]), accent, max(w, h) * 0.42, glow)
    m = 72 if fmt == "x" else 76
    s = Sheet(img, ImageDraw.Draw(img), fmt, w, h, m, [])
    return s


def brand_row(s: Sheet, when: datetime | None, kicker: str = "", accent: tuple[int, int, int] = RED) -> int:
    """The kiwi and PRICE CHAT on the left, the kicker pill and the UTC time on the right. Returns the y below it."""
    brand_mark(s, s.m, s.m - 10)
    y = s.m - 4
    right = s.w - s.m
    if when is not None:
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        t = f"{when.day} {when:%b %Y} · {when:%H:%M} UTC".upper()
        f = s.note(22, "SemiBold")
        tw = f.getlength(t)
        s.d.text((right - tw, y + 8), t, font=f, fill=MUTED)
        right -= tw + 24
    if kicker:
        f = s.note(24, "ExtraBold")
        tw = el.tracked_len(kicker, f, 2.2)
        s.d.rounded_rectangle((right - tw - 32, y + 2, right, y + 42), radius=8, fill=accent)
        el.tracked(s.d, (right - tw - 16, y + 9), kicker, f, (255, 255, 255) if accent != AMBER else INK, 2.2)
    return s.m + 60


def brand_mark(s: Sheet, x: int, y: int) -> None:
    """The kiwi in its disc and PRICE CHAT NEWS, sized for a phone (design.brand_tag's 20 px type prints under 5 px there)."""
    r = 27
    disc = Image.new("RGBA", (2 * r, 2 * r), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=(236, 244, 226, 255))
    k = el.kiwi().resize((2 * r - 10, 2 * r - 10), Image.Resampling.LANCZOS)
    disc.alpha_composite(k, (5, 5))
    s.img.paste(disc, (x, y), disc)
    el.tracked(s.d, (x + 2 * r + 14, y + r - 15), "PRICE CHAT NEWS", s.note(26, "ExtraBold"), TEXT, 2.6)


def footer(s: Sheet, note: str) -> None:
    f = s.note(22, "Medium")
    y = s.h - s.m + 4
    s.d.line((s.m, y - 22, s.w - s.m, y - 22), fill=RULE, width=2)
    s.d.text((s.m, y - 6), note, font=f, fill=MUTED)
    site = "pricechat.live"
    fs = s.note(22, "Bold")
    s.d.text((s.w - s.m - fs.getlength(site), y - 6), site, font=fs, fill=TEXT)


# ------------------------------------------------------------------------------------------------------------------- marks
def arrow(d: ImageDraw.ImageDraw, x: float, y: float, size: float, up: bool, colour: tuple[int, int, int]) -> None:
    """A solid triangle: up for a gain, down for a fall."""
    if up:
        d.polygon([(x, y + size), (x + size / 2, y), (x + size, y + size)], fill=colour)
    else:
        d.polygon([(x, y), (x + size, y), (x + size / 2, y + size)], fill=colour)


def signed(p: float, digits: int = 1) -> str:
    """+3.4% / −3.6% with a true minus sign."""
    return f"{p:+.{digits}f}%".replace("-", "−")


GLYPHS = {"gold": "ingot", "silver": "ingot", "oil": "drop", "brent": "drop", "wti": "drop", "crude": "drop", "bitcoin": "₿", "btc": "₿",
          "ether": "eth", "eth": "eth", "ethereum": "eth"}


def glyph(s: Sheet, cx: float, cy: float, r: float, name: str, ticker: str = "") -> None:
    """The asset's icon in a ring: an ingot for gold and silver, a drop for oil, ₿ for Bitcoin, Ether's diamond, else the ticker."""
    key = next((v for k, v in GLYPHS.items() if re.search(rf"\b{k}\b", f"{name} {ticker}", re.I)), "")
    d = s.d
    metal = (226, 178, 64) if not re.search(r"silver", name, re.I) else (200, 206, 214)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=PANEL, outline=(70, 78, 92), width=3)
    if key == "ingot":
        for i, dy in enumerate((0.28, -0.12)):
            top, bot = cy + dy * r - 0.16 * r, cy + dy * r + 0.16 * r
            off = 0.22 * r if i else 0
            d.polygon([(cx - 0.5 * r + off * 0.3, top), (cx + 0.5 * r - off * 0.3, top), (cx + 0.62 * r - off * 0.3, bot), (cx - 0.62 * r + off * 0.3, bot)],
                      fill=metal, outline=(120, 90, 30))
    elif key == "drop":
        pts = []
        for k in range(60):
            a = math.pi * 2 * k / 60
            x = math.sin(a) * 0.42 * r
            y = -math.cos(a) * 0.42 * r
            if y < 0:
                x *= (1 + y / (0.42 * r)) ** 0.9  # narrowing to the tip
                y *= 1.6
            pts.append((cx + x, cy + 0.15 * r + y))
        d.polygon(pts, fill=(30, 30, 34), outline=(120, 120, 130))
    elif key == "eth":
        d.polygon([(cx, cy - 0.6 * r), (cx + 0.38 * r, cy + 0.02 * r), (cx, cy + 0.22 * r), (cx - 0.38 * r, cy + 0.02 * r)], fill=(150, 160, 235))
        d.polygon([(cx, cy + 0.3 * r), (cx + 0.38 * r, cy + 0.1 * r), (cx, cy + 0.62 * r), (cx - 0.38 * r, cy + 0.1 * r)], fill=(110, 120, 210))
    else:
        label = key or (ticker or name)[:4].upper().lstrip("$^")
        f = s.note(int(r * (0.95 if len(label) <= 1 else 0.62 if len(label) <= 3 else 0.5)), "Black")  # an icon, not the message
        tw = f.getlength(label)
        d.text((cx - tw / 2, cy - f.size * 0.62), label, font=f, fill=(247, 147, 26) if key == "₿" else TEXT)


def area_chart(s: Sheet, box: tuple[float, float, float, float], pts: list[tuple[float, float]], colour: tuple[int, int, int], *,
               label_ends: bool = True, price_fmt: Any = None) -> None:
    """The series as a thick line over a fading fill, its first value as a dotted reference line, the last point lit."""
    x0, y0, x1, y1 = box
    if len(pts) < 2:
        raise TemplateRefused("fewer than two points")
    ts = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    lo, hi = min(vs), max(vs)
    pad = (hi - lo) * 0.12 or abs(hi) * 0.01 or 1
    lo, hi = lo - pad, hi + pad
    t0, t1 = ts[0], ts[-1]
    fx = lambda t: x0 + (t - t0) / ((t1 - t0) or 1) * (x1 - x0)  # noqa: E731
    fy = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)  # noqa: E731
    line = [(fx(t), fy(v)) for t, v in pts]
    fill = Image.new("L", (s.w, s.h), 0)
    fd = ImageDraw.Draw(fill)
    fd.polygon([*line, (x1, y1), (x0, y1)], fill=255)
    grad = Image.new("L", (1, int(y1 - y0) + 1))
    for i in range(grad.height):
        grad.putpixel((0, i), int(110 * (1 - i / grad.height) ** 1.6))
    grad = grad.resize((s.w, grad.height))
    mask = Image.new("L", (s.w, s.h), 0)
    mask.paste(grad, (0, int(y0)))
    from PIL import ImageChops

    mask = ImageChops.multiply(mask, fill)
    s.img.paste(Image.new("RGB", (s.w, s.h), colour), (0, 0), mask)
    d = s.d
    first_y = fy(vs[0])
    for x in range(int(x0), int(x1), 18):
        d.line((x, first_y, x + 8, first_y), fill=(90, 98, 112), width=2)
    width = max(5, round(7 * s.w / 1600))
    d.line(line, fill=colour, width=width, joint="curve")
    lx, ly = line[-1]
    halo = Image.new("L", (s.w, s.h), 0)
    ImageDraw.Draw(halo).ellipse((lx - 26, ly - 26, lx + 26, ly + 26), fill=160)
    halo = halo.filter(ImageFilter.GaussianBlur(9))
    s.img.paste(Image.new("RGB", (s.w, s.h), colour), (0, 0), halo)
    d.ellipse((lx - 11, ly - 11, lx + 11, ly + 11), fill=colour, outline=(255, 255, 255), width=3)
    if label_ends:
        fmt = price_fmt or el._fmt_price
        f = s.note(26, "Bold")
        first = fmt(vs[0])
        d.text((x0, first_y - 36 if first_y - 36 > y0 else first_y + 8), first, font=f, fill=MUTED)
        last = fmt(vs[-1])
        tw = f.getlength(last)
        d.text((min(lx - tw / 2, x1 - tw), ly + 22 if ly + 52 < y1 + 40 else ly - 52), last, font=f, fill=TEXT)


def _when_label(t0: float, t1: float) -> tuple[str, str]:
    a, b = datetime.fromtimestamp(t0, tz=timezone.utc), datetime.fromtimestamp(t1, tz=timezone.utc)
    if t1 - t0 <= 2 * 86400:
        return f"{a:%H:%M} UTC", f"{b:%H:%M} UTC"
    return f"{a.day} {a:%b}", f"{b.day} {b:%b}"


def time_axis(s: Sheet, box: tuple[float, float, float, float], t0: float, t1: float) -> None:
    a, b = _when_label(t0, t1)
    f = s.note(22, "Medium")
    s.d.text((box[0], box[3] + 12), a, font=f, fill=MUTED)
    s.d.text((box[2] - f.getlength(b), box[3] + 12), b, font=f, fill=MUTED)


# ------------------------------------------------------------------------------------------------------------------- move card
def hero_text(s: Sheet, x: float, top: float, text: str, size: int, colour: tuple[int, int, int], *, up: bool | None = None,
              centre: bool = False) -> float:
    """The hero figure (Anton), its visible top at `top`, with the direction arrow before it when `up` is given. Returns its visible
    bottom, so nothing below can touch it."""
    f = s.hero(size)
    bb = s.d.textbbox((0, 0), text, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    aw = int(size * 0.36) if up is not None else 0
    gap = int(size * 0.1) if up is not None else 0
    if centre:
        x = (s.w - tw - aw - gap) / 2
    if up is not None:
        arrow(s.d, x, top + (th - aw) / 2, aw, up, colour)
    s.d.text((x + aw + gap - bb[0], top - bb[1]), text, font=f, fill=colour)
    return top + th


def _who(s: Sheet, top: float, name: str, ticker: str, centre: bool = False) -> float:
    """The asset's glyph and name. Returns the y below them."""
    r = 46 if s.fmt == "x" else 52
    nf = s.font(56 if s.fmt == "x" else 62, "ExtraBold")
    label = name.upper()[:18]
    width = 2 * r + 22 + nf.getlength(label)
    x = (s.w - width) / 2 if centre else s.m
    glyph(s, x + r, top + r, r, name, ticker)
    bb = s.d.textbbox((0, 0), label, font=nf)
    s.d.text((x + 2 * r + 22, top + r - (bb[3] + bb[1]) / 2), label, font=nf, fill=TEXT)
    return top + 2 * r


def move_card(fmt: str, *, name: str, ticker: str, pct: float, points: list[tuple[float, float]], window: str, source: str,
              when: datetime | None, context: str = "", context_points: list[tuple[float, float]] | None = None) -> tuple[bytes, dict[str, Any]]:
    """A market crash or pump: the asset, ONE huge verified percentage, the real series it comes from, the source.

    `pct` must be the series' own change (first to last point) to within 0.15 percentage points: the caller passes the verified figure,
    the template re-measures it from the very points it draws."""
    pts = [(float(t), float(v)) for t, v in points]
    if len(pts) < 8:
        raise TemplateRefused(f"only {len(pts)} price points")
    measured = (pts[-1][1] / pts[0][1] - 1) * 100
    if abs(measured - pct) > 0.15:
        raise TemplateRefused(f"the figure {pct:+.2f}% is not the drawn series' {measured:+.2f}%")
    up = pct >= 0
    colour = GREEN if up else RED
    s = sheet(fmt, colour, (0.72, 0.62) if fmt == "x" else (0.5, 0.8), 64)
    # "SELL-OFF" / "RALLY" only for a day's move: a fall over weeks is not a sell-off
    short = bool(re.search(r"\b(15 minutes|hour|4 hours|24 hours|24h|one day|today)\b", window, re.I))
    kicker = ("RALLY" if up else "SELL-OFF") if short and abs(pct) >= 2 else "MARKETS"
    top = brand_row(s, when, kicker, colour)
    d = s.d
    prices = f"{el._fmt_price(pts[0][1])} → {el._fmt_price(pts[-1][1])}"
    sub = f"{window.upper()}  ·  {prices}"
    if fmt == "x":
        y = _who(s, top + 26, name, ticker)
        hero_size = 230
        bottom = hero_text(s, s.m, y + 56, signed(pct), hero_size, colour, up=up)
        wf = s.font(36, "Bold")
        if wf.getlength(sub) <= 740:
            d.text((s.m, bottom + 40), sub, font=wf, fill=TEXT)
            yb = bottom + 92
        else:
            d.text((s.m, bottom + 40), window.upper(), font=wf, fill=TEXT)
            d.text((s.m, bottom + 90), prices, font=s.font(36, "SemiBold"), fill=MUTED)
            yb = bottom + 142
        if context:
            cf = s.font(34, "Medium")
            for i, ln in enumerate(el.wrap(context, cf, 700)[:2]):
                d.text((s.m, yb + i * 44), ln, font=cf, fill=MUTED)
        box = (850, top + 90, s.w - s.m - 10, s.h - s.m - 96)
    else:
        y = _who(s, top + 30, name, ticker, centre=True)
        hero_size = 290
        bottom = hero_text(s, 0, y + 70, signed(pct), hero_size, colour, up=up, centre=True)
        wf = s.font(40, "Bold")
        d.text(((s.w - wf.getlength(sub)) / 2, bottom + 44), sub, font=wf, fill=TEXT)
        box = (s.m + 10, bottom + 170, s.w - s.m - 10, s.h - s.m - 96)
    ctx = [(float(t), float(v)) for t, v in (context_points or [])]
    if len(ctx) > len(pts) and ctx[-1] == pts[-1]:
        lead_in(s, box, ctx, pts, colour)
        time_axis(s, box, ctx[0][0], ctx[-1][0])
    else:
        area_chart(s, box, pts, colour)
        time_axis(s, box, pts[0][0], pts[-1][0])
    footer(s, f"Data: {source}")
    return _jpeg(s.img), s.meta("move", hero_size, pct=round(pct, 3), points=len(pts), up=up)


def lead_in(s: Sheet, box: tuple[float, float, float, float], ctx: list[tuple[float, float]], pts: list[tuple[float, float]],
            colour: tuple[int, int, int]) -> None:
    """The whole series on one scale: the run-up in grey, the measured window in the move's colour from its first point (the peak),
    which is marked. Same data, same axes: the colour only says which part the figure measures."""
    x0, y0, x1, y1 = box
    vs = [v for _, v in ctx]
    lo, hi = min(vs), max(vs)
    pad = (hi - lo) * 0.12 or 1
    lo, hi = lo - pad, hi + pad
    t0, t1 = ctx[0][0], ctx[-1][0]
    fx = lambda t: x0 + (t - t0) / ((t1 - t0) or 1) * (x1 - x0)  # noqa: E731
    fy = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)  # noqa: E731
    width = max(5, round(7 * s.w / 1600))
    before = [(fx(t), fy(v)) for t, v in ctx if t <= pts[0][0]]
    s.d.line(before, fill=(110, 118, 134), width=width, joint="curve")
    window = [(fx(t), fy(v)) for t, v in pts]
    s.d.line(window, fill=colour, width=width, joint="curve")
    px, py = window[0]
    s.d.ellipse((px - 12, py - 12, px + 12, py + 12), fill=TEXT, outline=colour, width=4)
    f = s.note(26, "Bold")
    peak = f"PEAK {el._fmt_price(pts[0][1])}"
    s.d.text((max(x0, min(px - f.getlength(peak) / 2, x1 - f.getlength(peak))), py - 46), peak, font=f, fill=TEXT)
    lx, ly = window[-1]
    s.d.ellipse((lx - 11, ly - 11, lx + 11, ly + 11), fill=colour, outline=(255, 255, 255), width=3)
    last = el._fmt_price(pts[-1][1])
    s.d.text((min(lx - f.getlength(last) / 2, x1 - f.getlength(last)), ly + 20), last, font=f, fill=TEXT)


# ------------------------------------------------------------------------------------------------------------------- anomaly (the gold card)
def anomaly_card(fmt: str, *, name: str, symbol: str, closes: list[tuple[float, float]], headline_pct: float | None, since: str,
                 when: datetime | None, days: int = 60, live: bool = False) -> tuple[bytes, dict[str, Any]]:
    """An unusual one-day move: the day's verified change, huge, over the asset's last `days` daily moves as bars, today's lit in red or
    green, the typical day's band (one standard deviation of the 90 days before) drawn where it is. Every bar is one real daily
    close-to-close change: the picture SHOWS that today was not a normal day. `live`: the last value is today's price so far, not a
    settled close (2026-11-18: the gold card said "closed at 3,724.8" at 20:00 UTC; the settled close was 3,741.9), so it is labelled
    with its time."""
    import statistics

    pts = [(float(t), float(c)) for t, c in closes]
    if len(pts) < days + 2:
        raise TemplateRefused(f"only {len(pts)} closes")
    rets = [(pts[i][0], (pts[i][1] / pts[i - 1][1] - 1) * 100) for i in range(1, len(pts))]
    today = rets[-1][1]
    if headline_pct is not None and abs(round(today, 1) - headline_pct) > 0.051:
        raise TemplateRefused(f"the headline's {headline_pct:+.1f}% is not the closes' {today:+.2f}%")
    typical = statistics.pstdev([x for _, x in rets[-91:-1]]) or 1.0
    up = today >= 0
    colour = GREEN if up else RED
    s = sheet(fmt, colour, (0.76, 0.62) if fmt == "x" else (0.5, 0.8), 64)
    top = brand_row(s, when, "UNUSUAL MOVE", colour)
    d = s.d
    shown = rets[-days:]
    times = abs(today) / typical
    at = f"at {el._fmt_price(pts[-1][1])}" + (f" ({when:%H:%M} UTC)" if live and when is not None else " at the close")
    l1 = f"Biggest one-day {'gain' if up else 'drop'} {since}"
    l2 = f"{times:.1f}× a typical day · {at}"
    if fmt == "x":
        y = _who(s, top + 26, name, symbol)
        hero_size = 230
        bottom = hero_text(s, s.m, y + 56, signed(today), hero_size, colour, up=up)
        f1, f2 = s.font(38, "Bold"), s.font(34, "Medium")
        d.text((s.m, bottom + 40), l1[:40], font=f1, fill=TEXT)
        for i, ln in enumerate(el.wrap(l2, f2, 720)[:2]):
            d.text((s.m, bottom + 94 + i * 44), ln, font=f2, fill=MUTED)
        box = (880, top + 150, s.w - s.m - 10, s.h - s.m - 110)
    else:
        y = _who(s, top + 30, name, symbol, centre=True)
        hero_size = 290
        bottom = hero_text(s, 0, y + 64, signed(today), hero_size, colour, up=up, centre=True)
        f1, f2 = s.font(42, "Bold"), s.font(36, "Medium")
        d.text(((s.w - f1.getlength(l1)) / 2, bottom + 44), l1, font=f1, fill=TEXT)
        d.text(((s.w - f2.getlength(l2)) / 2, bottom + 104), l2, font=f2, fill=MUTED)
        box = (s.m + 10, bottom + 220, s.w - s.m - 10, s.h - s.m - 110)
    _return_bars(s, box, shown, typical, colour, today)
    footer(s, f"Each bar is one day's change · last {days} trading days · Yahoo Finance")
    return _jpeg(s.img), s.meta("anomaly", hero_size, pct=round(today, 3), typical=round(typical, 3), bars=len(shown), live=live)


def _return_bars(s: Sheet, box: tuple[float, float, float, float], rets: list[tuple[float, float]], typical: float,
                 colour: tuple[int, int, int], today: float) -> None:
    x0, y0, x1, y1 = box
    vals = [v for _, v in rets]
    span = max(abs(min(vals)), abs(max(vals)), typical * 1.5) * 1.08
    mid = (y0 + y1) / 2
    fy = lambda v: mid - v / span * (y1 - y0) / 2  # noqa: E731
    d = s.d
    band = Image.new("L", (s.w, s.h), 0)
    ImageDraw.Draw(band).rectangle((x0, fy(typical), x1, fy(-typical)), fill=40)
    s.img.paste(Image.new("RGB", (s.w, s.h), (120, 130, 150)), (0, 0), band)
    step = (x1 - x0) / len(rets)
    bw = max(3.0, step * 0.62)
    for i, (_, v) in enumerate(rets[:-1]):  # every other day dim and alike: the eye goes to today
        cx = x0 + step * (i + 0.5)
        a, b = sorted((fy(0), fy(v)))
        d.rectangle((cx - bw / 2, a, cx + bw / 2, max(b, a + 2)), fill=(74, 82, 98))
    cx = x1 - step / 2
    a, b = sorted((fy(0), fy(today)))
    halo = Image.new("L", (s.w, s.h), 0)
    ImageDraw.Draw(halo).rectangle((cx - bw * 1.6, a - 6, cx + bw * 1.6, b + 6), fill=170)
    halo = halo.filter(ImageFilter.GaussianBlur(12))
    s.img.paste(Image.new("RGB", (s.w, s.h), colour), (0, 0), halo)
    d.rectangle((cx - bw * 0.9, a, cx + bw * 0.9, max(b, a + 2)), fill=colour)
    d.line((x0, mid, x1, mid), fill=(120, 128, 142), width=2)
    f = s.note(24, "SemiBold")
    lbl = f"shaded: a typical day (±{typical:.1f}%)"
    d.rectangle((x0, y0 - 40, x0 + 22, y0 - 18), fill=(70, 76, 92))
    d.text((x0 + 32, y0 - 46), lbl, font=f, fill=MUTED)
    tf = s.font(34, "ExtraBold")
    tag = f"TODAY {signed(today)}"
    ty = b + 12 if today < 0 else a - 48
    d.text((x1 - tf.getlength(tag), ty), tag, font=tf, fill=colour)


# ------------------------------------------------------------------------------------------------------------------- data posts
_PCT = re.compile(r"([+−-])\s?(\d+(?:\.\d+)?)%")


def pcts_in(text: str) -> list[float]:
    return [(-1 if m.group(1) in "−-" else 1) * float(m.group(2)) for m in _PCT.finditer(text or "")]


def battle_card(fmt: str, *, names: tuple[str, str], pcts: tuple[float, float], series: list[list[tuple[float, float]]], period: str,
                when: datetime | None) -> tuple[bytes, dict[str, Any]]:
    """Two assets' returns over one period: both figures huge in their own colour, both lines rebased to 0% on shared axes."""
    if len(series) != 2 or any(len(p) < 5 for p in series):
        raise TemplateRefused("a battle needs two series")
    rebased = [[(float(t), (float(c) / float(p[0][1]) - 1) * 100) for t, c in p] for p in series]
    for (nm, pct), line in zip(zip(names, pcts), rebased):
        if abs(line[-1][1] - pct) > 1.5:  # the stated return and the drawn line must tell the same story
            raise TemplateRefused(f"{nm}: stated {pct:+.1f}%, line ends {line[-1][1]:+.1f}%")
    lead = 0 if pcts[0] >= pcts[1] else 1
    s = sheet(fmt, SERIES[lead], (0.7, 0.6), 46)
    top = brand_row(s, when, "ASSET BATTLE", BLUE)
    d = s.d
    hero_size = 150 if fmt == "x" else 190
    nf = s.font(42 if fmt == "x" else 46, "ExtraBold")
    col_w = (s.w - 2 * s.m) / 2
    bottom = top
    for i, (nm, p) in enumerate(zip(names, pcts)):
        x = s.m + i * col_w + (40 if i else 0)
        d.rounded_rectangle((x, top + 30, x + 18, top + 74), radius=4, fill=SERIES[i])
        d.text((x + 32, top + 26), nm.upper()[:16], font=nf, fill=TEXT)
        bottom = max(bottom, hero_text(s, x, top + 104, signed(p), hero_size, GREEN if p >= 0 else RED, up=p >= 0))
    vs = s.font(40, "Black")
    d.text((s.w / 2 - vs.getlength("VS") / 2, top + 104 + hero_size * 0.25), "VS", font=vs, fill=MUTED)
    box = (s.m + 10, bottom + 90, s.w - s.m - 120, s.h - s.m - 90)
    _rebased_lines(s, box, rebased)
    footer(s, f"Return over the {period} · daily closes · Yahoo Finance")
    return _jpeg(s.img), s.meta("battle", hero_size, pcts=list(pcts))


def _rebased_lines(s: Sheet, box: tuple[float, float, float, float], lines: list[list[tuple[float, float]]]) -> None:
    x0, y0, x1, y1 = box
    ts = [t for p in lines for t, _ in p]
    vs = [v for p in lines for _, v in p] + [0.0]
    t0, t1, lo, hi = min(ts), max(ts), min(vs), max(vs)
    pad = (hi - lo) * 0.1 or 1
    lo, hi = lo - pad, hi + pad
    fx = lambda t: x0 + (t - t0) / ((t1 - t0) or 1) * (x1 - x0)  # noqa: E731
    fy = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)  # noqa: E731
    d = s.d
    f = s.note(22, "SemiBold")
    for tick in el.nice_ticks(lo, hi, False, 4):
        d.line((x0, fy(tick), x1, fy(tick)), fill=RULE, width=1)
        d.text((x1 + 14, fy(tick) - 12), f"{tick:+.0f}%".replace("-", "−"), font=f, fill=MUTED)
    d.line((x0, fy(0), x1, fy(0)), fill=(120, 128, 142), width=2)
    width = max(5, round(7 * s.w / 1600))
    for i, p in enumerate(lines):
        pts = [(fx(t), fy(v)) for t, v in p]
        d.line(pts, fill=SERIES[i], width=width, joint="curve")
        lx, ly = pts[-1]
        d.ellipse((lx - 10, ly - 10, lx + 10, ly + 10), fill=SERIES[i], outline=(255, 255, 255), width=3)
    time_axis(s, box, t0, t1)


def then_now_card(fmt: str, *, name: str, symbol: str, then: float, now_price: float, series: list[tuple[float, float]],
                  when: datetime | None) -> tuple[bytes, dict[str, Any]]:
    pts = [(float(t), float(c)) for t, c in series]
    if len(pts) < 10:
        raise TemplateRefused("too few points")
    if abs(pts[-1][1] / now_price - 1) > 0.02:
        raise TemplateRefused("today's price is not the series' last close")
    pct = (now_price / then - 1) * 100
    up = pct >= 0
    colour = GREEN if up else RED
    s = sheet(fmt, colour, (0.7, 0.6), 60)
    top = brand_row(s, when, "THEN VS NOW", colour)
    d = s.d
    gx = s.m + 46
    glyph(s, gx, top + 92, 46, name, symbol)
    d.text((gx + 66, top + 58), name.upper()[:18], font=s.font(54, "ExtraBold"), fill=TEXT)
    lf, pf = s.font(34, "Bold"), s.font(64, "ExtraBold")
    hero_size = 200 if fmt == "x" else 230
    y = top + 160
    d.text((s.m, y), "A YEAR AGO", font=lf, fill=MUTED)
    d.text((s.m, y + 36), el._fmt_price(then), font=pf, fill=MUTED)
    x2 = s.m + (440 if fmt == "x" else 520)
    d.text((x2, y), "TODAY", font=lf, fill=TEXT)
    d.text((x2, y + 36), el._fmt_price(now_price), font=pf, fill=TEXT)
    hx = s.m if fmt == "tg" else 900
    hy = y + 150 if fmt == "tg" else top + 60
    bottom = hero_text(s, hx, hy, signed(pct, 0 if abs(pct) >= 100 else 1), hero_size, colour, up=up)
    box = (s.m + 10, (bottom + 80) if fmt == "tg" else y + 200, s.w - s.m - 10, s.h - s.m - 90)
    area_chart(s, box, pts, colour, label_ends=False)
    time_axis(s, box, pts[0][0], pts[-1][0])
    footer(s, "Daily closes · Yahoo Finance")
    return _jpeg(s.img), s.meta("then_now", hero_size, pct=round(pct, 2))


def ranking_card(fmt: str, *, title: str, rows: list[tuple[str, float]], when: datetime | None, kicker: str = "THE WEEK") -> tuple[bytes, dict[str, Any]]:
    """Winners and losers on one axis through zero; each bar's length is its percent; figures large enough to read on a phone."""
    rows = [(str(n), float(v)) for n, v in rows][:10]
    if len(rows) < 3:
        raise TemplateRefused("too few rows")
    s = sheet(fmt, BLUE, (0.5, 0.6), 40)
    top = brand_row(s, when, kicker, BLUE)
    d = s.d
    tf = s.font(52 if fmt == "x" else 56, "ExtraBold")
    for i, ln in enumerate(el.wrap(title, tf, s.w - 2 * s.m)[:2]):
        d.text((s.m, top + 10 + i * 64), ln, font=tf, fill=TEXT)
    y0 = top + 10 + 64 * min(2, len(el.wrap(title, tf, s.w - 2 * s.m))) + 30
    y1 = s.h - s.m - 70
    step = (y1 - y0) / len(rows)
    nf, vf = s.font(int(min(36, step * 0.5)), "Bold"), s.font(int(min(36, step * 0.5)), "ExtraBold")
    label_w = 330 if fmt == "x" else 300
    lo, hi = min(0.0, *[v for _, v in rows]), max(0.0, *[v for _, v in rows])
    span = s.w - 2 * s.m - label_w - 170
    fx = lambda v: s.m + label_w + (v - lo) / ((hi - lo) or 1) * span  # noqa: E731
    for i, (n, v) in enumerate(rows):
        y = y0 + i * step
        d.text((s.m, y + step * 0.18), n[:18], font=nf, fill=TEXT)
        a, b = sorted((fx(0), fx(v)))
        col = GREEN if v >= 0 else RED
        d.rounded_rectangle((a, y + step * 0.16, max(b, a + 3), y + step * 0.84), radius=6, fill=col)
        txt = signed(v)
        d.text((b + 14 if v >= 0 else a - 14 - vf.getlength(txt), y + step * 0.18), txt, font=vf, fill=col)
    d.line((fx(0), y0, fx(0), y1), fill=(120, 128, 142), width=2)
    footer(s, "7-day return · daily closes · Yahoo Finance")
    return _jpeg(s.img), s.meta("ranking", 0, rows=len(rows))


# ------------------------------------------------------------------------------------------------------------------- outage
BRAND_COLOURS = {"Microsoft": (0, 120, 212), "AWS": (255, 153, 0), "Google": (66, 133, 244), "Cloudflare": (244, 129, 32), "Apple": (230, 230, 235),
                 "Meta": (0, 129, 251), "OpenAI": (230, 230, 235), "CrowdStrike": (252, 0, 0), "GitHub": (230, 230, 235), "Salesforce": (0, 161, 224),
                 "X": (230, 230, 235), "Zoom": (11, 92, 255), "JPMorgan Chase": (17, 122, 202), "Bank of America": (227, 24, 55),
                 "Wells Fargo": (215, 30, 40), "Citi": (0, 107, 182), "Visa": (26, 31, 113), "Mastercard": (235, 0, 27), "PayPal": (0, 112, 186),
                 "Coinbase": (0, 82, 255), "Binance": (240, 185, 11), "Kraken": (87, 65, 217), "Robinhood": (0, 200, 5), "AT&T": (0, 168, 224),
                 "Verizon": (205, 4, 11), "T-Mobile": (226, 0, 116), "Starlink": (230, 230, 235)}
STATUS = {"outage": ("OUTAGE", RED), "disruption": ("DISRUPTION", RED), "recovering": ("RECOVERING", AMBER), "cyberattack": ("CYBERATTACK", RED)}


def outage_card(fmt: str, *, company: str, services: list[str], status: str, sector: str, when: datetime | None, source: str,
                stock: tuple[str, float, str] | None = None) -> tuple[bytes, dict[str, Any]]:
    """A service outage: the company's name set large in its brand colour (our type, not their logo), the services the reports name,
    the status the reports give (OUTAGE / DISRUPTION / RECOVERING / CYBERATTACK) and a warning mark. The stock chip appears only when the
    caller measured the move from real prices AND the story is about the shares."""
    label, colour = STATUS.get(status, STATUS["outage"])
    brand = BRAND_COLOURS.get(company, TEXT)
    s = sheet(fmt, colour, (0.82, 0.42) if fmt == "x" else (0.5, 0.36), 58)
    top = brand_row(s, when, label, colour)
    d = s.d
    # the warning mark with its pulse rings: a symbol, no data
    cx, cy = ((s.w - s.m - 190, top + 230) if fmt == "x" else (s.w / 2, top + 250))
    for i, (r, a) in enumerate(((210, 26), (160, 46), (112, 80))):
        ring = Image.new("L", (s.w, s.h), 0)
        ImageDraw.Draw(ring).ellipse((cx - r, cy - r, cx + r, cy + r), outline=a, width=6)
        s.img.paste(Image.new("RGB", (s.w, s.h), colour), (0, 0), ring)
    r = 84
    d.polygon([(cx, cy - r), (cx + r * 1.05, cy + r * 0.78), (cx - r * 1.05, cy + r * 0.78)], fill=colour)
    ef = s.hero(120)
    d.text((cx - ef.getlength("!") / 2, cy - 74), "!", font=ef, fill=(255, 255, 255))
    name = company.upper()
    max_w = (s.w - 2 * s.m - 470) if fmt == "x" else (s.w - 2 * s.m)
    size = 200 if fmt == "x" else 230
    while size > 96 and s.hero(size).getlength(name) > max_w:
        s.sizes.pop()
        size -= 8
    hf = s.hero(size)
    bb = d.textbbox((0, 0), name, font=hf)
    nw, nh = bb[2] - bb[0], bb[3] - bb[1]
    top_y = top + 150 if fmt == "x" else top + 560
    nx = s.m if fmt == "x" else (s.w - nw) / 2
    d.text((nx - bb[0], top_y - bb[1]), name, font=hf, fill=brand)
    svc = " · ".join(dict.fromkeys(x.upper() for x in services if x and x.upper() != name))[:48]
    sf = s.font(50 if fmt == "x" else 56, "ExtraBold")
    line = f"{svc} {label}" if svc else f"{sector.upper()} {label}"
    sy = top_y + nh + 44
    d.text((nx if fmt == "x" else (s.w - sf.getlength(line)) / 2, sy), line, font=sf, fill=TEXT)
    if stock is not None:
        tick, pct, win = stock
        cf = s.font(40, "ExtraBold")
        chip = f"{tick} {signed(pct)} · {win}"
        cw = cf.getlength(chip) + 60
        x = nx if fmt == "x" else (s.w - cw) / 2
        y = sy + 100
        c = GREEN if pct >= 0 else RED
        d.rounded_rectangle((x, y, x + cw, y + 70), radius=12, outline=c, width=3, fill=PANEL)
        d.text((x + 30, y + 12), chip, font=cf, fill=c)
    footer(s, f"Reported by {source}"[:70] if source else "Price Chat News")
    return _jpeg(s.img), s.meta("outage", size, company=company, status=label, stock=list(stock) if stock else None)


__all__ = ["FORMATS", "TemplateRefused", "anomaly_card", "battle_card", "move_card", "outage_card", "pcts_in", "ranking_card", "readable",
           "then_now_card"]
