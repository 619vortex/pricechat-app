"""The News Desk's post image: one 16:9 picture, a small "PRICE CHAT NEWS" mark, ONE bold headline, a small credit. The post text beneath
it is the same headline in bold and the source in small italics; nothing else (owner, 2026-11-09, 2026-11-10).

Four kinds of picture, chosen per story (`plan`), all honest:
- chart: an exceptional market move, drawn from our own price data (Hyperliquid candles or Yahoo bars). The big figure is the measured move;
  the headline stays in the caption, so the chart is never crowded.
- artwork: a major breaking story (a hack, an exchange failure, a scam, a flush; or a story the desk labelled BREAKING). Editorial artwork
  composed here from light, texture and abstract motifs (fracture lines, pulses, ridgelines, light beams) with Pillow. No people, no logos,
  no invented scenes, and credited "Illustration: Price Chat" so nobody mistakes it for a photograph.
- photo: a company, a government, a country, a world event. A real photograph from Wikimedia Commons, only under a licence that allows
  reuse (public domain, CC0, CC BY, CC BY-SA), only one whose title names the subject (a company's headquarters, a parliament, a city), in
  colour and recent, credited on the image, not reused within USED_DAYS. Never a generic stand-in: the server room of some other
  institution is not a picture of a hacked exchange (the 2026-11-09 Mercado Bitcoin post carried CERN's data centre).
- graphic: everything else. A premium branded typographic picture: the headline large on a rich field, with the story's own figure.

Variety (`plan`): the last few pictures are remembered (Telegram's settings state, "recent_visuals"); a palette, motif or layout used in the
last ones is avoided, and when the last two posts were the same kind and the story allows another, the other is used. A chart is never
swapped for variety: accuracy first.

Mobile: a phone shows the picture about 390 px wide, a quarter of its 1600. The headline is set no smaller than MIN_TYPE px (about 17 px on
the phone), at most four lines, inside 96 px safe margins, white on a darkened field (contrast checked by `contrast`). An optional AI artwork
provider can be plugged in (`ArtworkProvider`), off by default: no key exists, and nothing here needs one.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from app import price_axis

logger = logging.getLogger(__name__)

W, H = 1600, 900
MARGIN = 96
MIN_TYPE = 72  # headline px at 1600 wide: ~17.5 px on a 390 px phone
ASSETS = Path(__file__).with_name("assets")
TEXT, MUTED = (246, 247, 246), (170, 178, 174)
DOWN, UP = (236, 52, 44), (120, 214, 40)
COMMONS = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "PriceChatNewsDesk/1.0 (https://pricechat.live)"}
OLD = re.compile(r"construction|drawing|engraving|\bmap\b|\bplan\b|diagram|logo|sketch|lithograph|LCCN|postcard|stereograph|interior|lobby|cafeteria|toilet|parking", re.I)
FREE = re.compile(r"^(cc0|public domain|pd|cc[- ]by(-sa)?( \d(\.\d)?)?)", re.I)

PALETTES: dict[str, dict[str, tuple[int, int, int]]] = {
    "ember": {"bg": (12, 6, 5), "a": (255, 86, 40), "b": (255, 196, 110)},
    "crimson": {"bg": (12, 4, 9), "a": (230, 38, 72), "b": (255, 150, 140)},
    "cobalt": {"bg": (4, 8, 22), "a": (52, 112, 255), "b": (140, 214, 255)},
    "violet": {"bg": (10, 6, 22), "a": (140, 92, 246), "b": (246, 120, 190)},
    "emerald": {"bg": (3, 14, 12), "a": (20, 190, 132), "b": (196, 244, 110)},
    "gold": {"bg": (14, 10, 4), "a": (240, 170, 10), "b": (255, 232, 160)},
    "ice": {"bg": (5, 12, 20), "a": (110, 200, 250), "b": (226, 244, 255)},
}
MOTIFS = ("fracture", "pulse", "ridges", "beams")
LAYOUTS = ("lower", "left", "center")
# what each kind of story may look like (motifs, palettes), most fitting first
STYLE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "incident": (("fracture", "beams", "pulse"), ("ember", "crimson", "violet", "gold")),
    "geopolitics": (("pulse", "beams", "ridges"), ("ember", "gold", "ice", "crimson")),
    "market": (("ridges", "beams", "pulse"), ("emerald", "crimson", "cobalt", "gold")),
    "regulation": (("beams", "ridges", "pulse"), ("cobalt", "ice", "gold", "violet")),
    "structure": (("ridges", "beams"), ("cobalt", "emerald", "ice", "gold")),
    "corporate": (("beams", "pulse", "ridges"), ("violet", "cobalt", "ice", "emerald")),
    "flows": (("ridges", "beams"), ("emerald", "crimson", "gold", "cobalt")),
    "macro": (("ridges", "pulse", "beams"), ("gold", "ice", "cobalt", "emerald")),
    "news": (MOTIFS, tuple(PALETTES)),
}
RECENT = 6  # how many past pictures variety looks at


@lru_cache(maxsize=32)
def _anton(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "Anton-Regular.ttf"), size)


@lru_cache(maxsize=16)
def _inter(size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(ASSETS / "Inter-Variable.ttf"), size)
    f.set_variation_by_name(weight)
    return f


@lru_cache(maxsize=1)
def _kiwi() -> Image.Image:
    return Image.open(ASSETS / "kiwi.webp").convert("RGBA")


def _png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=90, optimize=True, progressive=True)  # a JPEG: Telegram and X both keep it sharp at ~250 KB
    return out.getvalue()


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def _vignette(img: Image.Image, strength: float = 0.75) -> Image.Image:
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    for i in range(40):
        f = i / 40
        d.ellipse([-W * 0.25 + f * W * 0.35, -H * 0.35 + f * H * 0.4, W * 1.25 - f * W * 0.35, H * 1.35 - f * H * 0.4], fill=int(255 * f))
    mask = mask.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(img, Image.blend(img, dark, strength), mask)


def _grain(img: Image.Image, amount: int = 10, seed: int = 7) -> Image.Image:
    rnd = random.Random(seed)
    noise = Image.frombytes("L", (W // 2, H // 2), bytes(rnd.randrange(128 - amount, 128 + amount) for _ in range((W // 2) * (H // 2)))).resize((W, H))
    return ImageChops.overlay(img, Image.merge("RGB", (noise, noise, noise)))


def _brand(img: Image.Image) -> None:
    """The small mark, top left: the Kiwi and PRICE CHAT NEWS."""
    k = _kiwi().resize((46, 46))
    disc = Image.new("RGBA", (54, 54), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse([0, 0, 53, 53], fill=(236, 244, 226, 235))
    disc.alpha_composite(k, (4, 4))
    img.paste(disc, (48, 42), disc)
    d = ImageDraw.Draw(img)
    d.text((114, 69), "PRICE CHAT NEWS", font=_inter(24, "Bold"), fill=TEXT, anchor="lm")


def _credit(img: Image.Image, text: str) -> None:
    d = ImageDraw.Draw(img)
    d.text((W - 36, H - 26), text, font=_inter(16, "Medium"), fill=(206, 210, 208), anchor="rs")


def _glow(img: Image.Image, layer: Image.Image, colour: tuple[int, int, int], radii: tuple[int, ...] = (28, 10), strength: float = 1.0) -> Image.Image:
    """Light a mask: soft wide glow, tighter glow, then the mask itself."""
    for r in radii:
        m = layer.filter(ImageFilter.GaussianBlur(r)).point(lambda v: int(min(255, v * strength)))
        img = ImageChops.add(img, Image.composite(Image.new("RGB", (W, H), colour), Image.new("RGB", (W, H), (0, 0, 0)), m))
    return ImageChops.add(img, Image.composite(Image.new("RGB", (W, H), colour), Image.new("RGB", (W, H), (0, 0, 0)), layer))


# --- type ------------------------------------------------------------------------------------------------------------


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    lines: list[str] = []
    for word in text.split():
        if lines and d.textlength(f"{lines[-1]} {word}", font=font) <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


def fit_headline(text: str, width: int, sizes: tuple[int, ...] = (124, 112, 100, 92, 84, 76, MIN_TYPE), max_lines: int = 4) -> tuple[int, list[str]]:
    """The largest size at which the headline fits the width in at most max_lines (3 for the big sizes): (px, lines)."""
    for size in sizes:
        lines = _wrap(text, _anton(size), width)
        if len(lines) <= (3 if size >= 100 else max_lines):
            return size, lines
    size = sizes[-1]
    lines = _wrap(text, _anton(size), width)
    return size, lines[:max_lines]


def luminance(rgb: tuple[float, float, float]) -> float:
    def ch(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(img: Image.Image, box: tuple[int, int, int, int], fg: tuple[int, int, int] = TEXT) -> float:
    """WCAG contrast of the text colour against the brightest tenth of a field (the worst case for reading). Measured before the
    headline is drawn on it."""
    region = img.convert("RGB").crop(box).resize((80, 40))
    px = sorted(region.getdata(), key=lambda c: luminance(c))
    bright = px[int(len(px) * 0.9)]
    a, b = luminance(fg), luminance(bright)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _scrim(img: Image.Image, layout: str, strength: int = 215) -> Image.Image:
    """Darken where the headline goes: a soft gradient, never a hard box."""
    m = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(m)
    if layout == "left":
        for x in range(W):
            d.line([(x, 0), (x, H)], fill=int(strength * max(0.0, min(1.0, 1.15 - x / (W * 0.62))) ** 1.2))
    else:
        top = H * (0.30 if layout == "center" else 0.36)
        for y in range(H):
            d.line([(0, y), (W, y)], fill=int(strength * max(0.0, min(1.0, (y - top) / (H - top) * 1.35)) ** 1.1))
    m = m.filter(ImageFilter.GaussianBlur(24))
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img.convert("RGB"), m)


def _headline(img: Image.Image, text: str, layout: str, accent: tuple[int, int, int]) -> dict[str, Any]:
    """Set the headline: big, white, inside the safe margins, with a short accent bar above it. Returns what was set (for the checks)."""
    width = int(W * 0.56) if layout == "left" else W - 2 * MARGIN
    size, lines = fit_headline(text, width)
    font = _anton(size)
    lead = int(size * 1.06)
    block = lead * len(lines)
    d = ImageDraw.Draw(img)
    x = MARGIN
    if layout == "left":
        y0 = max(150, (H - block) // 2 + 20)
    else:
        y0 = H - 92 - block
    if layout == "center":
        y0 = H - 100 - block
    widest = max(int(d.textlength(ln, font=font)) for ln in lines)
    left = (W - widest) // 2 if layout == "center" else x
    field = contrast(img, (left, y0, left + widest, y0 + block))  # the field the words sit on, before they go on it
    bar_x = W // 2 - 48 if layout == "center" else x
    d.rectangle([bar_x, y0 - 30, bar_x + 96, y0 - 22], fill=accent)
    for i, line in enumerate(lines):
        if layout == "center":
            d.text((W // 2, y0 + i * lead), line, font=font, fill=TEXT, anchor="ma")
        else:
            d.text((x, y0 + i * lead), line, font=font, fill=TEXT)
    return {"type_px": size, "lines": len(lines), "box": [left, y0, left + widest, y0 + block], "contrast": round(field, 2)}


# --- market moves: a real chart ------------------------------------------------------------------------------------

TONES = {"ink": (6, 7, 8), "navy": (5, 8, 17), "plum": (11, 6, 12)}


def chart(points: list[tuple[int, float]], *, name: str, ticker: str, pct: float, window: str = "24H", source: str = "Hyperliquid", tone: str = "ink") -> bytes:
    """points: (unix seconds, price), oldest first, from our own data. The picture shows only what the data says: the line, the move, the
    last price and the window's high and low."""
    if len(points) < 10:
        raise ValueError("not enough price points for a chart")
    falling = pct < 0
    accent = DOWN if falling else UP
    img = Image.new("RGB", (W, H), TONES.get(tone, TONES["ink"]))
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    cx, cy = (W * 0.78, H * 0.78) if falling else (W * 0.78, H * 0.22)
    for r in range(900, 0, -10):
        gd.ellipse([cx - r * 1.4, cy - r, cx + r * 1.4, cy + r], fill=int(70 * (1 - r / 900) ** 1.6))
    img.paste(Image.new("RGB", (W, H), accent), (0, 0), glow)
    wm = Image.new("L", (W, H), 0)
    ImageDraw.Draw(wm).text((W // 2, H // 2 + 30), ticker.upper()[:5], font=_anton(560), fill=11, anchor="mm")
    img.paste(Image.new("RGB", (W, H), TEXT), (0, 0), wm)
    d = ImageDraw.Draw(img)
    for i in range(1, 6):
        y = int(150 + i * (H - 260) / 6)
        d.line([(0, y), (W, y)], fill=(28, 30, 33), width=1)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    lo, hi = min(ys), max(ys)
    ax = price_axis.axis(lo, hi)  # log when the range is more than phi (1.618x), else linear: never a setting
    left, right, top, bottom = 0, W - 150, 200, H - 110

    def at(i: int) -> tuple[float, float]:
        x = left + (xs[i] - xs[0]) / ((xs[-1] - xs[0]) or 1) * (right - left)
        y = bottom - ax.frac(ys[i]) * (bottom - top)
        return x, y

    line = [at(i) for i in range(len(points))]
    area = Image.new("L", (W, H), 0)
    ImageDraw.Draw(area).polygon([*line, (line[-1][0], H), (line[0][0], H)], fill=255)
    fade = ImageOps.invert(Image.linear_gradient("L").resize((W, H))).point(lambda v: int(v * 0.42))
    img.paste(Image.new("RGB", (W, H), accent), (0, 0), ImageChops.multiply(area, fade))
    lay = Image.new("L", (W, H), 0)
    ImageDraw.Draw(lay).line(line, fill=255, width=14, joint="curve")
    img.paste(Image.new("RGB", (W, H), accent), (0, 0), lay.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    d.line(line, fill=tuple(min(255, c + 40) for c in accent), width=6, joint="curve")
    ex, ey = line[-1]
    for r, a in ((34, 60), (20, 120)):
        dot = Image.new("L", (W, H), 0)
        ImageDraw.Draw(dot).ellipse([ex - r, ey - r, ex + r, ey + r], fill=a)
        img.paste(Image.new("RGB", (W, H), accent), (0, 0), dot.filter(ImageFilter.GaussianBlur(6)))
    d.ellipse([ex - 9, ey - 9, ex + 9, ey + 9], fill=TEXT)
    img = _vignette(img, 0.55)
    img = _grain(img, 8)
    d = ImageDraw.Draw(img)
    # the real numbers, small: the last price at the line's end, the window's high and low on the right edge
    money = (lambda v: f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}")
    d.text((W - 40, top - 8), f"H {money(hi)}", font=_inter(22, "Medium"), fill=MUTED, anchor="rs")
    d.text((W - 40, bottom + 34), f"L {money(lo)}", font=_inter(22, "Medium"), fill=MUTED, anchor="rs")
    d.text((ex - 4, ey - 30), money(ys[-1]), font=_inter(26, "Bold"), fill=TEXT, anchor="rs")
    stat = f"{'+' if pct >= 0 else '-'}{abs(pct):.1f}%"
    first_half_high = sum(p[1] for p in points[: len(points) // 2]) >= sum(p[1] for p in points[len(points) // 2:])
    sx, anchor = (W - 70, "r") if first_half_high else (70, "l")
    sy = 265
    d.text((sx, sy), stat, font=_anton(220), fill=TEXT, anchor=anchor + "m")
    d.text((sx + (6 if anchor == "l" else -6), sy + 134), f"{name.upper()}  ·  {window}", font=_inter(34, "Bold"), fill=accent, anchor=anchor + "m")
    _brand(img)
    _credit(img, f"Data: {source}")
    return _png(img)


# --- artwork: composed here, credited as an illustration ---------------------------------------------------------------


class ArtworkProvider(Protocol):
    """Hook for generated artwork (off: ARTWORK_PROVIDER is None). A provider returns a 1600x900 image for a prompt describing the story's
    subject in abstract terms (never a real person's likeness). Wiring one in would need its API key in the encrypted secrets store, e.g. an
    image model's key; none exists, and the local artwork below needs none."""

    async def render(self, prompt: str, *, width: int, height: int) -> Image.Image: ...


ARTWORK_PROVIDER: ArtworkProvider | None = None


def _base(pal: dict[str, tuple[int, int, int]], rnd: random.Random, focus: tuple[float, float]) -> Image.Image:
    bg = pal["bg"]
    img = Image.new("RGB", (W, H), bg)
    grad = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(v * 0.55))
    img = Image.composite(Image.new("RGB", (W, H), tuple(max(0, c - 8) for c in bg)), img, grad)
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    fx, fy = focus
    for r in range(1000, 0, -12):
        gd.ellipse([fx - r * 1.5, fy - r, fx + r * 1.5, fy + r], fill=int(120 * (1 - r / 1000) ** 2.2))
    img.paste(Image.new("RGB", (W, H), pal["a"]), (0, 0), glow.filter(ImageFilter.GaussianBlur(40)))
    return img


def _dust(img: Image.Image, pal: dict[str, tuple[int, int, int]], rnd: random.Random, n: int = 260, focus: tuple[float, float] | None = None) -> Image.Image:
    """Floating motes and a few large out-of-focus discs: depth."""
    near = Image.new("L", (W, H), 0)
    far = Image.new("L", (W, H), 0)
    dn, df = ImageDraw.Draw(near), ImageDraw.Draw(far)
    fx, fy = focus or (W * 0.7, H * 0.4)
    for _ in range(n):
        x = rnd.gauss(fx, W * 0.28)
        y = rnd.gauss(fy, H * 0.3)
        r = rnd.choice((1, 1, 2, 2, 3))
        dn.ellipse([x - r, y - r, x + r, y + r], fill=rnd.randrange(90, 255))
    for _ in range(14):
        x, y, r = rnd.uniform(0, W), rnd.uniform(0, H * 0.8), rnd.uniform(18, 70)
        df.ellipse([x - r, y - r, x + r, y + r], fill=rnd.randrange(16, 46))
    img = _glow(img, near, pal["b"], radii=(4,), strength=0.9)
    return _glow(img, far.filter(ImageFilter.GaussianBlur(10)), pal["a"], radii=(), strength=1.0)


def _fracture(img: Image.Image, pal: dict[str, tuple[int, int, int]], rnd: random.Random, focus: tuple[float, float]) -> Image.Image:
    """Cracks running out of a white-hot point, like a breached pane: a hack, a failure."""
    lay = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(lay)
    fx, fy = focus

    def crack(x: float, y: float, angle: float, length: float, width: int, depth: int) -> None:
        pts = [(x, y)]
        for _ in range(int(length / 26)):
            angle += rnd.uniform(-0.35, 0.35)
            x += math.cos(angle) * 26
            y += math.sin(angle) * 26
            pts.append((x, y))
            if depth < 2 and rnd.random() < 0.12:
                crack(x, y, angle + rnd.choice((-1, 1)) * rnd.uniform(0.5, 1.1), length * 0.45, max(1, width - 2), depth + 1)
        d.line(pts, fill=255 if depth == 0 else 190, width=width, joint="curve")

    for i in range(11):
        crack(fx, fy, i / 11 * 2 * math.pi + rnd.uniform(-0.2, 0.2), rnd.uniform(500, 1100), 5, 0)
    # shards: pale facets near the impact
    facets = Image.new("L", (W, H), 0)
    fd = ImageDraw.Draw(facets)
    for _ in range(9):
        a, r = rnd.uniform(0, 2 * math.pi), rnd.uniform(60, 320)
        cx, cy = fx + math.cos(a) * r, fy + math.sin(a) * r
        fd.polygon([(cx + rnd.uniform(-90, 90), cy + rnd.uniform(-90, 90)) for _ in range(3)], fill=rnd.randrange(14, 34))
    img = ImageChops.add(img, Image.composite(Image.new("RGB", (W, H), pal["b"]), Image.new("RGB", (W, H), (0, 0, 0)), facets.filter(ImageFilter.GaussianBlur(2))))
    img = _glow(img, lay, pal["a"], radii=(36, 12), strength=1.4)
    core = Image.new("L", (W, H), 0)
    ImageDraw.Draw(core).ellipse([fx - 26, fy - 26, fx + 26, fy + 26], fill=255)
    return _glow(img, core.filter(ImageFilter.GaussianBlur(8)), pal["b"], radii=(60, 20), strength=1.6)


def _pulse(img: Image.Image, pal: dict[str, tuple[int, int, int]], rnd: random.Random, focus: tuple[float, float]) -> Image.Image:
    """A planet's rim lit from behind, with rings of a signal spreading over it: the world, a shock travelling."""
    lay = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(lay)
    R = W * 0.95
    cx, cy = W * 0.62, H + R * 0.78
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=255, width=4)
    rim = lay.copy()
    img = _glow(img, rim, pal["b"], radii=(70, 24, 8), strength=1.2)
    grid = Image.new("L", (W, H), 0)
    g = ImageDraw.Draw(grid)
    for k in range(1, 9):  # latitude arcs
        rr = R * (1 - k * 0.035)
        g.ellipse([cx - rr * 1.02, cy - rr, cx + rr * 1.02, cy + rr], outline=40, width=2)
    for k in range(-8, 9):  # meridians, squeezed toward the rim
        x = cx + R * math.sin(k / 9 * 1.2)
        g.line([(x, cy - R * math.cos(k / 9 * 1.2)), (cx + (x - cx) * 0.3, H)], fill=26, width=2)
    img = ImageChops.add(img, Image.composite(Image.new("RGB", (W, H), pal["a"]), Image.new("RGB", (W, H), (0, 0, 0)), grid))
    fx, fy = focus
    rings = Image.new("L", (W, H), 0)
    rd = ImageDraw.Draw(rings)
    for i in range(1, 8):
        r = i * 70
        rd.ellipse([fx - r * 1.6, fy - r * 0.55, fx + r * 1.6, fy + r * 0.55], outline=int(230 * (1 - i / 8)), width=3)
    img = _glow(img, rings, pal["a"], radii=(18, 6), strength=1.1)
    core = Image.new("L", (W, H), 0)
    ImageDraw.Draw(core).ellipse([fx - 14, fy - 14, fx + 14, fy + 14], fill=255)
    return _glow(img, core, pal["b"], radii=(80, 26), strength=1.8)


def _ridges(img: Image.Image, pal: dict[str, tuple[int, int, int]], rnd: random.Random, focus: tuple[float, float], series: list[float] | None = None) -> Image.Image:
    """Stacked ridgelines, a landscape of data under a low light. With a real price series, the front ridge is that series."""
    n = 22
    phase = [rnd.uniform(0, 6.28) for _ in range(4)]
    out = img
    for k in range(n):
        depth = k / (n - 1)  # 0 back, 1 front
        base = H * (0.40 + 0.5 * depth)
        amp = 30 + 150 * depth
        pts = []
        for i in range(0, W + 20, 16):
            t = i / W
            bump = math.exp(-((t - 0.55 - 0.15 * math.sin(k)) ** 2) / 0.02) * 1.2
            v = (math.sin(t * 9 + phase[0] + k * 0.6) * 0.35 + math.sin(t * 23 + phase[1] + k) * 0.18 + math.sin(t * 3 + phase[2]) * 0.3 + bump)
            if series and k == n - 1:
                j = min(len(series) - 1, int(t * (len(series) - 1)))
                lo, hi = min(series), max(series)
                v = ((series[j] - lo) / ((hi - lo) or 1)) * 1.6
            pts.append((i, base - v * amp))
        fill = Image.new("L", (W, H), 0)
        ImageDraw.Draw(fill).polygon([*pts, (W + 20, H), (0, H)], fill=255)
        out = Image.composite(Image.new("RGB", (W, H), tuple(int(c * (0.25 + 0.2 * (1 - depth))) for c in (0, 0, 0))), out, fill)
        edge = Image.new("L", (W, H), 0)
        ImageDraw.Draw(edge).line(pts, fill=int(70 + 185 * depth), width=2 if depth < 0.8 else 3)
        colour = tuple(int(pal["a"][c] * (1 - depth) + pal["b"][c] * depth) for c in range(3))
        out = _glow(out, edge, colour, radii=(6,) if depth > 0.6 else (), strength=0.9)
    return out


def _beams(img: Image.Image, pal: dict[str, tuple[int, int, int]], rnd: random.Random, focus: tuple[float, float]) -> Image.Image:
    """Shafts of light falling through haze onto a polished floor: an institution, a decision."""
    lay = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(lay)
    sx = rnd.uniform(W * 0.45, W * 0.95)
    for i in range(7):
        x0 = sx + i * 70 - 200
        spread = rnd.uniform(160, 300)
        d.polygon([(x0, -40), (x0 + 50, -40), (x0 - spread * 0.4 + 50 - 520, H), (x0 - spread - 520, H)], fill=rnd.randrange(40, 95))
    lay = lay.filter(ImageFilter.GaussianBlur(26))
    img = ImageChops.add(img, Image.composite(Image.new("RGB", (W, H), pal["b"]), Image.new("RGB", (W, H), (0, 0, 0)), lay))
    floor = Image.new("L", (W, H), 0)
    fd = ImageDraw.Draw(floor)
    hz = H * 0.72
    for k in range(14):  # floor lines receding
        y = hz + (H - hz) * (k / 13) ** 1.8
        fd.line([(0, y), (W, y)], fill=int(20 + 60 * k / 13), width=1)
    for k in range(-12, 13):
        fd.line([(W * 0.5 + k * 30, hz), (W * 0.5 + k * 260, H)], fill=26, width=1)
    img = ImageChops.add(img, Image.composite(Image.new("RGB", (W, H), pal["a"]), Image.new("RGB", (W, H), (0, 0, 0)), floor))
    pillar = Image.new("L", (W, H), 0)
    px = sx - 330
    ImageDraw.Draw(pillar).rectangle([px, H * 0.18, px + 120, hz], fill=255)
    img = Image.composite(Image.new("RGB", (W, H), (4, 4, 6)), img, pillar.filter(ImageFilter.GaussianBlur(1)))
    edge = Image.new("L", (W, H), 0)
    ImageDraw.Draw(edge).line([(px + 120, H * 0.18), (px + 120, hz)], fill=255, width=3)
    return _glow(img, edge, pal["b"], radii=(20, 6), strength=1.0)


def artwork(headline: str, *, motif: str, palette: str, layout: str, seed: int, series: list[float] | None = None) -> tuple[bytes, dict[str, Any]]:
    """An editorial illustration with the headline set on it. Deterministic for (motif, palette, layout, seed): X re-renders Telegram's."""
    pal = PALETTES.get(palette, PALETTES["ember"])
    rnd = random.Random(seed)
    focus = {"left": (W * 0.74, H * 0.42), "lower": (W * rnd.uniform(0.55, 0.75), H * 0.36), "center": (W * 0.5, H * 0.34)}.get(layout, (W * 0.7, H * 0.4))
    img = _base(pal, rnd, focus)
    draw = {"fracture": _fracture, "pulse": _pulse, "beams": _beams}.get(motif)
    img = _ridges(img, pal, rnd, focus, series) if motif == "ridges" else draw(img, pal, rnd, focus) if draw else img
    img = _dust(img, pal, rnd, focus=focus)
    img = _vignette(img, 0.6)
    img = _grain(img, 9, seed)
    img = _scrim(img, layout)
    meta = _headline(img, headline, layout, pal["a"])
    _brand(img)
    _credit(img, "Illustration: Price Chat")
    return _png(img), meta


# --- graphic: premium branded typographic picture ----------------------------------------------------------------------


def graphic(headline: str, *, palette: str, layout: str, seed: int, stat: str = "", source: str = "", motif: str = "ridges", mark: str = "") -> tuple[bytes, dict[str, Any]]:
    """The headline as the picture: a rich field of colour, soft light, a fine pattern, the story's motif kept quiet, its lead name as a
    large outlined watermark, and the story's own figure large if it has one."""
    pal = PALETTES.get(palette, PALETTES["cobalt"])
    rnd = random.Random(seed)
    deep = tuple(int(c * 0.55 + b * 0.45) for c, b in zip(pal["bg"], (0, 0, 0)))
    img = Image.new("RGB", (W, H), deep)
    light = Image.new("L", (W, H), 0)
    ld = ImageDraw.Draw(light)
    cx, cy = (W * 0.85, H * 0.15) if layout != "center" else (W * 0.5, -H * 0.1)
    for r in range(1200, 0, -14):
        ld.ellipse([cx - r * 1.3, cy - r, cx + r * 1.3, cy + r], fill=int(150 * (1 - r / 1200) ** 1.8))
    img.paste(Image.new("RGB", (W, H), pal["a"]), (0, 0), light.filter(ImageFilter.GaussianBlur(50)))
    pattern = Image.new("L", (W, H), 0)
    pd = ImageDraw.Draw(pattern)
    step = rnd.choice((34, 42, 54))
    for x in range(-H, W, step):  # fine diagonal hatching, faint
        pd.line([(x, H), (x + H, 0)], fill=12, width=1)
    img = ImageChops.add(img, Image.composite(Image.new("RGB", (W, H), pal["b"]), Image.new("RGB", (W, H), (0, 0, 0)), pattern))
    focus = (W * 0.72, H * 0.3)
    draw = {"fracture": _fracture, "pulse": _pulse, "beams": _beams}.get(motif)
    art = _ridges(img, pal, rnd, focus) if motif == "ridges" else draw(img, pal, rnd, focus) if draw else img
    img = Image.blend(img, art, 0.42)
    if mark and not stat:
        wm = Image.new("L", (W, H), 0)
        size = 300 if len(mark) <= 6 else 220
        ImageDraw.Draw(wm).text((W - 50, 40), mark.upper(), font=_anton(size), fill=0, anchor="ra", stroke_width=3, stroke_fill=255)
        img = _glow(img, wm.point(lambda v: int(v * 0.5)), pal["b"], radii=(12,), strength=0.8)
    if stat:
        wm = Image.new("L", (W, H), 0)
        ImageDraw.Draw(wm).text((W - 60, 330), stat, font=_anton(330), fill=255, anchor="rm")
        img = _glow(img, wm.point(lambda v: int(v * 0.16)), pal["b"], radii=(30,), strength=1.0)
        d = ImageDraw.Draw(img)
        d.text((W - 60, 330), stat, font=_anton(330), fill=tuple(min(255, int(c * 0.9) + 20) for c in pal["b"]), anchor="rm")
    img = _vignette(img, 0.45)
    img = _grain(img, 7, seed)
    img = _scrim(img, layout, strength=150)
    meta = _headline(img, headline, layout, pal["b"])
    _brand(img)
    if source:
        _credit(img, f"Source: {source}")
    return _png(img), meta


# --- news: a real photograph ----------------------------------------------------------------------------------------


@dataclass
class Photo:
    title: str
    url: str
    author: str
    licence: str
    page: str

    @property
    def credit(self) -> str:
        who = re.sub(r"<[^>]+>", "", self.author or "").strip()[:40]
        lic = self.licence.replace("Public domain", "public domain")
        return f"Photo: {who + ', ' if who and 'public domain' not in lic.lower() else ''}Wikimedia Commons ({lic})"


async def find_photos(client: httpx.AsyncClient, query: str, limit: int = 12) -> list[Photo]:
    """Landscape photographs on Wikimedia Commons for a query, only under licences that allow reuse with credit."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": "6", "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": str(limit * 2),
              "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime", "iiurlwidth": "1920"}
    r = await client.get(COMMONS, params=params, headers=UA, timeout=20)
    r.raise_for_status()
    out = []
    for page in (r.json().get("query") or {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if not FREE.match(lic) or info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        if info.get("width", 0) < 1400 or info.get("width", 0) < info.get("height", 1) * 1.2:  # big and landscape
            continue
        year = re.search(r"(1[89]\d\d|20\d\d)", re.sub(r"<[^>]+>", "", (meta.get("DateTimeOriginal") or {}).get("value", "")))
        if (year and int(year.group(1)) < 1995) or OLD.search(page.get("title", "")):
            continue  # today's news wants a photograph of today, not an engraving or a building site from 1857
        out.append(Photo(page.get("title", ""), info.get("thumburl") or info.get("url"), (meta.get("Artist") or {}).get("value", ""), lic,
                         info.get("descriptionurl", "")))
    return out[:limit]


async def fetch_image(client: httpx.AsyncClient, url: str) -> Image.Image:
    r = await client.get(url, headers=UA, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def is_colour(img: Image.Image) -> bool:
    """Black-and-white archive pictures read as history, not news."""
    small = img.resize((64, 36)).convert("HSV")
    return sum(small.getchannel("S").getdata()) / (64 * 36) > 28


def photo(img: Image.Image, credit: str, *, tone: str = "neutral", headline: str = "", layout: str = "lower") -> tuple[bytes, dict[str, Any]] | bytes:
    """A real photograph, cropped to 16:9 and graded like a front page: deep shadows, cooler mids, a vignette. tone "risk" adds a red cast.
    With a headline, it is set over a soft dark gradient and (bytes, what was set) is returned."""
    img = ImageOps.fit(img, (W, H), method=Image.LANCZOS, centering=(0.5, 0.42))
    img = ImageEnhance.Color(img).enhance(0.72)
    img = ImageEnhance.Contrast(img).enhance(1.28)
    img = ImageEnhance.Brightness(img).enhance(0.74)
    lum = img.convert("L").point(lambda v: 255 if v > 110 else 0).filter(ImageFilter.GaussianBlur(30))
    hi = Image.blend(img, Image.new("RGB", (W, H), (190, 40, 30) if tone == "risk" else (210, 150, 90)), 0.14)
    lo = Image.blend(img, Image.new("RGB", (W, H), (10, 30, 48)), 0.24)
    img = Image.composite(hi, lo, lum)
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(220):
        sd.line([(0, y), (W, y)], fill=int(150 * (1 - y / 220)))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade)
    img = _vignette(img, 0.5)
    img = _grain(img, 6)
    meta: dict[str, Any] = {}
    if headline:
        img = _scrim(img, layout, strength=225)
        meta = _headline(img, headline, layout, (236, 52, 44) if tone == "risk" else (246, 200, 80))
    _brand(img)
    _credit(img, credit)
    if headline:
        return _png(img), meta
    return _png(img)


# --- the words ----------------------------------------------------------------------------------------------------------

DISPLAY = {"Oil (WTI)": "WTI crude", "Oil (Brent)": "Brent crude", "S&P 500": "S&P 500", "HYPE": "Hyperliquid's HYPE"}


def move_headline(name: str, pct: float, window: str = "24 hours", cls: str = "") -> str:
    """A bold headline that says exactly what the data says. The verb is earned by the move's severity tier for its asset class (app/newsdesk/
    editorial.TIERS), never by mood: "falls"/"rises" at the alert line, "tumbles"/"jumps" a tier up, "plunges"/"soars" two tiers up."""
    from app.newsdesk import editorial

    cls = cls or editorial.asset_class({editorial.canon(name)})
    t = editorial.tier(cls, pct)
    if pct < 0:
        verb = "PLUNGES" if t >= 3 else "TUMBLES" if t >= 2 else "FALLS"
    else:
        verb = "SOARS" if t >= 3 else "JUMPS" if t >= 2 else "RISES"
    return f"{DISPLAY.get(name, name).upper()} {verb} {abs(pct):.1f}% IN {window.upper()}"


def caption_html(headline: str, source: str) -> str:
    from html import escape

    return f"<b>{escape(headline)}</b>\n\n<i>Source: {escape(source)}</i>"


def x_text(headline: str, source: str) -> str:
    return f"{headline}\n\nSource: {source}"


def post_headline(ev: Any) -> str:
    """ONE bold line: the event's own verified headline, written short ($16 billion -> $16B), no labels, no emoji, no added words.
    Over MAX_HEADLINE characters, a trailing "as ..."/"amid ..." clause is dropped (context goes, the event stays: nothing is added)."""
    f = ev.facts or {}
    if f.get("visual_headline"):
        return str(f["visual_headline"])
    h = re.sub(r"^[^\w$]+", "", ev.headline or ev.title or "").strip()
    h = re.sub(r"^(BREAKING|JUST IN|ALERT|UPDATE)\s*[:\-–—]\s*", "", h, flags=re.I)
    h = re.sub(r"\$\s?(\d[\d.,]*)\s*(billion|bn)\b", r"$\1B", h, flags=re.I)
    h = re.sub(r"\$\s?(\d[\d.,]*)\s*(million|mn)\b", r"$\1M", h, flags=re.I)
    h = re.sub(r"\s+", " ", h).upper()
    return trim(h, MAX_HEADLINE)[:140]


MAX_HEADLINE = 100
CLAUSE = re.compile(r"\s*,?\s+\b(AS|AMID|AFTER|WHILE|FOLLOWING|AHEAD OF|DESPITE|WITH)\b\s+")


def trim(h: str, limit: int) -> str:
    """Drop trailing clauses, the last first, until the headline fits; never below three words, never adding anything."""
    while len(h) > limit:
        found = list(CLAUSE.finditer(h))
        if not found:
            break
        cut = h[:found[-1].start()].rstrip(" ,:;-")
        if len(cut.split()) < 3:
            break
        h = cut
    return h


def source_name(ev: Any) -> str:
    names = [s.get("name") for s in (ev.sources or []) if s.get("name")]
    return names[0] if names else "Price Chat"


# --- the numbers gate: no chart, no figure, unless the data proves it ------------------------------------------------------

FRESH_MINUTES = 20  # the series' last point must be this close to the moment the post speaks for
SAME_SERIES_PP = 0.1  # the headline's percent against the chart's own first and last points
CROSS_SOURCE_PP = 0.5  # against a second source (Yahoo vs Hyperliquid): two feeds, two clocks, so a little wider
WINDOW_SECONDS = {"15M": 900, "1H": 3600, "4H": 14400, "24H": 86400}
HEADLINE_WINDOW = re.compile(r"\bIN (?:(\d+) HOURS|(AN) HOUR|(\d+) MINUTES)\b", re.I)
HEADLINE_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")


@dataclass
class NumberCheck:
    ok: bool
    reasons: list[str]
    series_pct: float | None = None
    detail: str = ""

    def out(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": self.reasons, "series_pct": None if self.series_pct is None else round(self.series_pct, 3), "detail": self.detail}


def _headline_window(headline: str) -> int | None:
    m = HEADLINE_WINDOW.search(headline or "")
    if not m:
        return None
    if m.group(1):
        return int(m.group(1)) * 3600
    if m.group(2):
        return 3600
    return int(m.group(3)) * 60


def looks_synthetic(points: list[tuple[int, float]]) -> str:
    """Why a series isn't real market data, or "": timestamps out of order or repeated, or a run that is a straight line point after point
    (interpolated or made up: real prices wiggle)."""
    ts = [p[0] for p in points]
    if any(b <= a for a, b in zip(ts, ts[1:])):
        return "timestamps repeat or go backwards"
    ys = [float(p[1]) for p in points]
    flat = sum(1 for i in range(1, len(ys) - 1) if abs(ys[i + 1] - 2 * ys[i] + ys[i - 1]) <= 1e-9 * max(1.0, abs(ys[i])))
    if flat >= 0.5 * (len(ys) - 2):
        return f"{flat} of {len(ys) - 2} points lie on straight lines: interpolated, not traded prices"
    return ""


def verify_series(points: list[tuple[int, float]], *, window: str, headline: str, as_of: Any, now: Any, max_age_minutes: float,
                  second_pct: float | None = None, second_name: str = "", synthetic: bool = False, headline_pp: float = SAME_SERIES_PP) -> NumberCheck:
    """The gate every chart and every measured figure passes before any post (owner, 2026-11-10). The chart's data window must be the
    headline's window; the headline's percent must equal the percent of the very series the chart draws (within SAME_SERIES_PP) and a
    second source's (within CROSS_SOURCE_PP) when there is one; the last point must be fresh; the data must be real, never interpolated."""
    reasons: list[str] = []
    pts = [(int(t), float(c)) for t, c in points]
    if len(pts) < 10:
        return NumberCheck(False, [f"only {len(pts)} price points"])
    if synthetic:
        reasons.append("the series is marked synthetic")
    fake = looks_synthetic(pts)
    if fake:
        reasons.append(f"not real data: {fake}")
    want = WINDOW_SECONDS.get(str(window).upper())
    span = pts[-1][0] - pts[0][0]
    if want is None:
        reasons.append(f"unknown window {window!r}")
    else:
        if not 0.9 * want <= span <= 1.1 * want + 900:
            reasons.append(f"the chart covers {span / 3600:.1f} h, not the {want / 3600:g} h window")
        said = _headline_window(headline)
        if said is not None and said != want:
            reasons.append(f"the headline says {said / 3600:g} h, the chart shows {want / 3600:g} h")
    series = (pts[-1][1] - pts[0][1]) / pts[0][1] * 100
    turned = direction_problem(headline, series)
    if turned:
        reasons.append(turned)
    m = HEADLINE_PCT.search(headline or "")
    if m and abs(float(m.group(1)) - abs(series)) > headline_pp:
        reasons.append(f"the headline says {m.group(1)}%, the chart's own data says {abs(series):.2f}%")
    if second_pct is not None:
        if (second_pct > 0) != (series > 0) or abs(second_pct - series) > CROSS_SOURCE_PP:
            reasons.append(f"{second_name or 'the second source'} says {second_pct:+.2f}%, the chart's data {series:+.2f}%")
    if as_of is not None:
        last_age = (as_of.timestamp() - pts[-1][0]) / 60
        if last_age > FRESH_MINUTES:
            reasons.append(f"the last price is {last_age:.0f} min older than the post's moment")
        if now is not None and (now - as_of).total_seconds() / 60 > max_age_minutes:
            reasons.append(f"the data is {(now - as_of).total_seconds() / 60:.0f} min old")
    detail = f"{len(pts)} points over {span / 3600:.1f} h, {series:+.2f}%" + (f"; {second_name} {second_pct:+.2f}%" if second_pct is not None else "; no second source")
    return NumberCheck(not reasons, reasons, series, detail)


UP_WORDS = re.compile(r"\b(rises?|rising|jumps?|soars?|skyrockets?|surges?|climbs?|gains?|rall(?:y|ies)|spikes?|pumps?|higher|breaks above)\b", re.I)
DOWN_WORDS = re.compile(r"\b(falls?|falling|tumbles?|plunges?|crash(?:es)?|drops?|slides?|sinks?|slumps?|dives?|lower|falls below|sell-?off)\b", re.I)


def direction_problem(headline: str, change: float) -> str:
    """A measured move's headline says the way it moved: never "rises" for a fall (owner, 2026-11-21: signs and directions are checked)."""
    up, down = bool(UP_WORDS.search(headline or "")), bool(DOWN_WORDS.search(headline or ""))
    if change < 0 and up and not down:
        return f"the headline says it rose; the measured change is {change:+.2f}%"
    if change > 0 and down and not up:
        return f"the headline says it fell; the measured change is {change:+.2f}%"
    return ""


QUOTE_PRICE_TOL = 0.0005  # 0.05%: a printed level against the quote it came from (the same Yahoo answer, so normally exact)


def yahoo_equity(ev: Any) -> bool:
    """A move the desk measured on a US stock or index from Yahoo (app/newsdesk/moves): its numbers are checked against Yahoo's own quote,
    not Hyperliquid candles (there are none for the Nasdaq): before 2026-11-21 every such post went out as text, "no verified series"."""
    from app.newsdesk import moves

    f = ev.facts or {}
    if getattr(ev, "kind", "") not in ("market", "update") or f.get("kind") not in ("move", "wide"):
        return False
    sym = str(f.get("symbol") or "")
    if sym == "US-STOCKS":
        return True
    # commodities too (gold, silver, oil, copper): Hyperliquid has no candles for them, so they went out text-only, figures unchecked
    return group_of(sym) in (*moves.EQUITY_GROUPS, "commodity")


def group_of(symbol: str) -> str:
    from app.newsdesk import moves, sources

    item = next((x for x in moves.watch_items(sources.DEFAULT_WATCHLIST) if x["symbol"] == symbol), None)
    return str(item.get("group") or "") if item is not None else ""


def verify_quote_move(ev: Any, quotes: dict[str, Any], now: Any = None, max_age_minutes: float = 90) -> NumberCheck:
    """Every figure a US stock or index post prints, against the Yahoo quote it was measured from: each level is the quote's price at the
    post's moment, each reference is the official previous close ("today") or the price the window ago, each percent is the one those two
    give and is the one the headline prints, and the moment is fresh. One wrong figure and the post doesn't go (NumbersRefused)."""
    from app.newsdesk import moves

    f = ev.facts or {}
    window = str(f.get("window") or "")
    figs = [dict(x) for x in (f.get("figures") or [])]
    if f.get("kind") == "move" and not figs:
        figs = [{"name": str(f.get("symbol")), "symbol": str(f.get("symbol")), "price": f.get("price"), "ref": f.get("ref"), "change": f.get("change_pct"),
                 "as_of": f.get("as_of")}]
    if not figs:
        return NumberCheck(False, ["the post carries no figures to check"])
    head = f"{getattr(ev, 'headline', '') or ''}"
    reasons: list[str] = []
    for x in figs:
        sym, name = str(x.get("symbol") or ""), str(x.get("name") or x.get("symbol"))
        price, ref, change = float(x.get("price") or 0), float(x.get("ref") or 0), float(x.get("change") or 0)
        if price <= 0 or ref <= 0:
            reasons.append(f"{name}: no level or no reference price")
            continue
        if abs((price - ref) / ref * 100 - change) > 0.01:
            reasons.append(f"{name}: {change:+.2f}% is not what {price} against {ref} gives")
        if not re.search(rf"(?<![\d.]){re.escape(f'{abs(change):.1f}')}%", head):  # "1.5%" is not inside "11.5%"
            reasons.append(f"{name}: the headline doesn't print {abs(change):.1f}%")
        q = quotes.get(sym)
        if q is None:
            reasons.append(f"{name}: no Yahoo quote to check against")
            continue
        at = datetime_of(x.get("as_of") or f.get("as_of"))
        seen = [q.price] if at is not None and abs(q.as_of.timestamp() - at.timestamp()) <= 300 else []
        if at is not None:  # the bar of that moment, whether the move stamped it with the bar's start (no live price) or its end
            width, ts = moves.bar_seconds(q), at.timestamp()
            seen += [c for t, c, _ in q.bars if abs(t - ts) <= 1 or abs(t + width - ts) <= 1]
        if not any(abs(price - s) / s <= QUOTE_PRICE_TOL for s in seen if s):
            reasons.append(f"{name}: {price} is not Yahoo's price at {at:%H:%M} UTC" if at else f"{name}: the post's moment is unknown")
        if window == "day":
            if not q.previous_close or abs(ref - q.previous_close) / q.previous_close > QUOTE_PRICE_TOL:
                reasons.append(f"{name}: {ref} is not Yahoo's previous close ({q.previous_close})")
        elif at is not None and group_of(sym) not in moves.EQUITY_GROUPS:
            secs = moves.WINDOWS.get(window, 0)  # a futures move (moves.measure): its bars are stamped with their start, as it measured them
            then = moves.price_at(q, at.timestamp() - secs, tolerance=max(600, int(secs * 0.05)))
            if not then or abs(ref - then) / then > QUOTE_PRICE_TOL:
                reasons.append(f"{name}: {ref} is not Yahoo's price {window} earlier ({then})")
        elif at is not None:
            then = moves.price_near(q, at.timestamp() - moves.WINDOWS.get(window, 0), before=at.timestamp())
            if not then or abs(ref - then) / then > QUOTE_PRICE_TOL:
                reasons.append(f"{name}: {ref} is not Yahoo's price {window} earlier ({then})")
        if at is not None and now is not None and (now - at).total_seconds() / 60 > max_age_minutes:
            reasons.append(f"{name}: the price is {(now - at).total_seconds() / 60:.0f} min old")
    lead = float(figs[0].get("change") or 0)
    turned = direction_problem(head, lead)
    if turned:
        reasons.append(turned)
    detail = "Yahoo quote check: " + ", ".join(f"{x.get('name')} {float(x.get('price') or 0):,.2f} vs {float(x.get('ref') or 0):,.2f} ({float(x.get('change') or 0):+.2f}%)" for x in figs)
    return NumberCheck(not reasons, reasons, lead, detail)


def datetime_of(v: Any) -> Any:
    from datetime import datetime, timezone

    if not v:
        return None
    t = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class NumbersRefused(ValueError):
    """A chart the numbers gate refused: the post goes without it, or not at all."""


# --- choosing the picture for an event --------------------------------------------------------------------------------

USED_DAYS = 14
# subject words -> a photograph of the actual subject (searched in order; a photo counts only if its title names it). No generic stand-ins.
SUBJECTS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(fed|federal reserve|powell|fomc|rate (?:cut|hike)s?)\b", re.I), "Marriner S. Eccles Federal Reserve Board Building", "eccles|federal reserve"),
    (re.compile(r"\b(treasury|ofac|fincen)\b", re.I), "United States Treasury Building", "treasury"),
    (re.compile(r"\b(sec|securities and exchange commission|gensler|atkins)\b", re.I), "Securities and Exchange Commission headquarters", "securities|sec "),
    (re.compile(r"\b(cftc)\b", re.I), "Commodity Futures Trading Commission", "commodity futures"),
    (re.compile(r"\b(occ|comptroller of the currency)\b", re.I), "Office of the Comptroller of the Currency", "comptroller"),
    (re.compile(r"\b(iran\w*|tehran)\b", re.I), "Tehran skyline Milad Tower", "tehran"),
    (re.compile(r"\b(israel\w*|knesset)\b", re.I), "Knesset building Jerusalem", "knesset"),
    (re.compile(r"\b(russia\w*|kremlin|putin)\b", re.I), "Moscow Kremlin view", "kremlin"),
    (re.compile(r"\b(ukrain\w*|kyiv|zelensk\w*)\b", re.I), "Kyiv Maidan Nezalezhnosti", "kyiv|maidan"),
    (re.compile(r"\b(saudi\w*|riyadh)\b", re.I), "Riyadh skyline Kingdom Centre", "riyadh"),
    (re.compile(r"\b(qatar\w*|doha)\b", re.I), "Doha skyline West Bay", "doha"),
    (re.compile(r"\b(uae|dubai|abu dhabi|emirati)\b", re.I), "Dubai skyline Burj Khalifa", "dubai"),
    (re.compile(r"\b(china|beijing|chinese)\b", re.I), "Beijing CBD skyline", "beijing"),
    (re.compile(r"\b(japan\w*|tokyo)\b", re.I), "Tokyo skyline", "tokyo"),
    (re.compile(r"\b(white house|trump|president)\b", re.I), "White House north facade", "white house"),
    (re.compile(r"\b(congress|senate|senator|house of representatives|impeach\w*)\b", re.I), "United States Capitol west front", "capitol"),
    (re.compile(r"\b(pentagon|hegseth|defen[cs]e secretary)\b", re.I), "The Pentagon aerial", "pentagon"),
    (re.compile(r"\b(nyse|new york stock exchange|wall street)\b", re.I), "New York Stock Exchange facade", "stock exchange|wall street"),
    (re.compile(r"\b(nasdaq)\b(?![- ]listed)", re.I), "Nasdaq MarketSite Times Square", "nasdaq"),
    (re.compile(r"\b(ecb|european central bank|lagarde)\b", re.I), "European Central Bank Frankfurt", "european central bank"),
    (re.compile(r"\b(bank of england)\b", re.I), "Bank of England building", "bank of england"),
    (re.compile(r"\b(bank of japan|boj)\b", re.I), "Bank of Japan head office", "bank of japan"),
    (re.compile(r"\b(european union|european commission|brussels)\b", re.I), "Berlaymont building Brussels", "berlaymont"),
    (re.compile(r"\b(court|judge|lawsuit|indict\w*|charged|guilty|sentenced)\b", re.I), "United States courthouse", "court"),
]
COMPANIES = re.compile(r"\b(nvidia|apple|tesla|microsoft|google|alphabet|amazon|meta|blackrock|coinbase|binance|tether|circle|microstrategy|"
                       r"jpmorgan|goldman sachs|robinhood|kraken|okx|bybit|mexc|bitget|openai|anthropic|samsung|tsmc|intel|nyse)\b", re.I)


def subject_queries(headline: str) -> list[tuple[str, str]]:
    """(search, word the photo's title must contain), most specific first: a named company's headquarters, then the subject."""
    out: list[tuple[str, str]] = []
    for name in dict.fromkeys(m.group(1).lower() for m in COMPANIES.finditer(headline)):
        if name == "nyse":
            continue  # its own subject below
        out.append((f"{name} headquarters", name))
        out.append((f"{name} office building", name))
    out += [(q, must) for rx, q, must in SUBJECTS if rx.search(headline)]
    return out


def story_type(ev: Any) -> str:
    from app.newsdesk import editorial

    f = ev.facts or {}
    if (f.get("importance") or {}).get("kind"):
        return str(f["importance"]["kind"])
    if ev.kind in ("recap", "market"):
        return "market"
    return editorial.FAMILY.get(ev.topic or "", "news")


def kinds_for(ev: Any) -> list[str]:
    """The kinds of picture this story may carry, most fitting first."""
    f = ev.facts or {}
    typ = story_type(ev)
    chartable = len(f.get("points") or []) >= 10 or ev.kind in ("recap", "market")
    if chartable and ev.kind in ("recap", "market"):
        return ["chart"]
    from app.newsdesk import brief

    major = typ == "incident" or ev.topic == "liquidations" or ev.label == brief.BREAKING
    if chartable:
        return ["chart", "artwork"]
    if major and subject_queries(f"{ev.headline} {ev.title}"):
        return ["photo", "artwork", "graphic"]  # owner, 2026-11-11: real photos first, when one of the actual subject exists
    if major:
        return ["artwork", "graphic"]
    if subject_queries(f"{ev.headline} {ev.title}") or typ in ("geopolitics", "corporate", "regulation", "controversy"):
        return ["photo", "artwork" if typ in ("geopolitics", "controversy") else "graphic", "graphic"]
    return ["graphic", "artwork"]


def _least_used(options: tuple[str, ...], used: list[str], seed: int) -> str:
    """The option used least in the recent pictures (the most recent counting most); ties broken by the story's own seed."""
    def weight(o: str) -> float:
        return sum(1.0 / (1 + i) for i, u in enumerate(reversed(used)) if u == o)
    rot = seed % len(options)
    ordered = options[rot:] + options[:rot]
    first = options[0]
    return min(ordered, key=lambda o: (weight(o) - (0.2 if o == first else 0.0), ordered.index(o)))


def plan(ev: Any, recent: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What the picture will be: kind, motif, palette, layout. Avoids what the last pictures used."""
    recent = list(recent or [])[-RECENT:]
    seed = _seed(f"{ev.key}|{ev.id}")
    kinds = kinds_for(ev)
    kind = kinds[0]
    last_two = [r.get("kind") for r in recent[-2:]]
    if kind != "chart" and len(kinds) > 1 and len(last_two) == 2 and all(k == kind for k in last_two):
        kind = kinds[1]  # two of the same in a row already: the story allows another
    typ = story_type(ev)
    motifs, palettes = STYLE.get(typ, STYLE["news"])
    f = ev.facts or {}
    if typ in ("market", "flows") and f.get("falling") is not None:
        palettes = tuple(p for p in palettes if p != ("emerald" if f.get("falling") else "crimson")) or palettes
    out = {"kind": kind, "motif": _least_used(motifs, [r.get("motif", "") for r in recent], seed),
           "palette": _least_used(palettes, [r.get("palette", "") for r in recent], seed >> 3),
           "layout": _least_used(LAYOUTS, [r.get("layout", "") for r in recent], seed >> 5), "seed": seed, "type": typ, "options": kinds}
    if kind == "chart":
        out["tone"] = _least_used(tuple(TONES), [r.get("tone", "") for r in recent], seed)
    return out


# the candles that chart a window with at least ten real prices across exactly that window (verify_series: the chart's span must be the
# headline's window). Before 2026-11-21 every window got 24 h of 15-minute candles, so a crypto move over 15 minutes, an hour or 4 hours
# was refused ("the chart covers 24.0 h, not the 1 h window") and the post never went out.
CHART_CANDLES = {"15M": ("1m", 15), "1H": ("5m", 12), "4H": ("15m", 16)}


async def candles_for(coin: str, market: Any = None, window: str = "24H") -> list[tuple[int, float]]:
    """(unix seconds, price) for the window. 24 h: 97 fifteen-minute closes, as always. Shorter windows: the first candle's open at its
    start, then each candle's close at its end, so the series runs from the window's first price to the latest one."""
    import asyncio

    if market is None:
        from app.tradingos.market import MarketData

        market = MarketData()
    interval, count = CHART_CANDLES.get(str(window).upper(), ("15m", 97))
    if interval == "15m" and count == 97:
        rows = await asyncio.to_thread(market.candles, coin, "15m", 97)
        return [(int(c.t) // 1000, float(c.c)) for c in rows]
    from app.tradingos.market import INTERVALS

    rows = await asyncio.to_thread(market.candles, coin, interval, count)
    step = INTERVALS[interval]
    first = rows[0] if rows else None
    out = [(int(first.t) // 1000, float(getattr(first, "o", None) or first.c))] if first is not None else []
    return out + [(int(c.t) // 1000 + step, float(c.c)) for c in rows]


def _mark(ev: Any) -> str:
    """The name a story leads with (NYSE, Coinbase), for the graphic's watermark: the first specific name in the headline, if short."""
    from app.newsdesk import editorial

    words = re.findall(r"[A-Za-z][A-Za-z0-9&]{2,}", ev.title or ev.headline or "")
    names = editorial.names_in(ev.title or "")
    for w in words:
        if w.lower() in names and len(w) <= 10:
            return w
    return ""


def _stat(ev: Any) -> str:
    f = ev.facts or {}
    s = str(f.get("stat") or "")
    return s if re.match(r"^[$+-]?\d", s) and len(s) <= 7 else ""


async def _chart_for(ev: Any, chosen: dict[str, Any], market: Any, now: Any = None) -> tuple[bytes, dict[str, Any]] | None:
    f = ev.facts or {}
    points = [tuple(p) for p in f.get("points") or []]
    coin = str(f.get("coin") or (f.get("tickers") or [""])[0] or "").lstrip("$").upper()
    window = str(f.get("window") or "24h").upper()
    other_source = False  # the series is Hyperliquid's while the headline was measured on Yahoo: two feeds, two clocks
    if len(points) < 10 and coin and ev.kind in ("recap", "market"):
        try:
            points = await candles_for(coin, market, window if ev.kind == "market" else "24H")
            other_source = ev.kind == "market"
        except Exception as e:  # noqa: BLE001
            logger.info("newsdesk visual: no candles for %s (%s)", coin, type(e).__name__)
    if len(points) < 10:
        return None
    from datetime import datetime, timezone

    stamp = f.get("as_of") or f.get("published_at")
    as_of = datetime.fromisoformat(stamp) if stamp else (now or datetime.now(timezone.utc))
    as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)  # now None (X re-rendering what Telegram checked): no age limit
    check = verify_series(points, window=window, headline=post_headline(ev), as_of=as_of, now=now,
                          max_age_minutes=90 if ev.kind in ("recap", "market") else 360, second_pct=f.get("second_pct"), second_name=str(f.get("second_source") or ""),
                          headline_pp=CROSS_SOURCE_PP if other_source else SAME_SERIES_PP,
                          synthetic=bool(f.get("synthetic")))
    ev.facts = {**f, "number_check": check.out()}
    if not check.ok:
        logger.warning("newsdesk visual: no chart for event %s: %s", getattr(ev, "id", None), "; ".join(check.reasons))
        if ev.kind in ("recap", "market"):
            raise NumbersRefused("; ".join(check.reasons))  # a market post IS its numbers: unverified, it doesn't go
        return None
    name = DISPLAY.get(str(f.get("name") or coin), str(f.get("name") or coin))
    if f.get("package"):  # Price Chat News: the clean paper chart (the figures, range and change all from this same checked series)
        from app.newsdesk import editorial_layouts as el

        series = [(datetime.fromtimestamp(int(t), timezone.utc), float(v)) for t, v in points]
        words = {"15M": "15 minutes", "1H": "an hour", "4H": "4 hours", "24H": "24 hours"}.get(window, window.lower())
        theme = "paper" if [r for r in [chosen.get("theme")] if r] != ["ink"] else "ink"
        data, meta = el.market_chart(series, name=name, ticker=coin or name[:4], window=words, headline=picture_headline(ev),
                                     sources=[str(f.get("data_source") or "Hyperliquid")], when=as_of, theme=theme)
        return data, {**chosen, "kind": "chart", "coin": coin, "number_check": check.out(), "chart_style": "paper", **meta}
    data = chart(points, name=name, ticker=coin or name[:4], pct=float(check.series_pct or 0), window=window, source=str(f.get("data_source") or "Hyperliquid"),
                 tone=chosen.get("tone", "ink"))
    return data, {**chosen, "kind": "chart", "coin": coin, "number_check": check.out()}


SECTION_FOR_TYPE = {"incident": "breaking", "market": "markets", "flows": "markets", "geopolitics": "geopolitics", "regulation": "policy",
                    "corporate": "business", "controversy": "developing", "macro": "macro", "news": "news"}


def display_headline(ev: Any) -> str:
    """The picture's headline in the outlet's own capitals (owner: a premium paper, not a shouting bot): the post's verified words, each
    written the way the source title wrote it. When the title doesn't cover the headline, headline case: small words lower, names and
    other words capitalised, acronyms and figures as they are. Never lower-cases a name it can't check."""
    head = post_headline(ev)
    title = re.sub(r"^[^\w$]+", "", getattr(ev, "title", "") or "")
    cased = {w.lower(): w for w in re.findall(r"[\w$.'’&-]+", title)} if title and not title.isupper() else {}
    parts = [(w, re.sub(r"^[\"'‘“(]+|[\"'’”),.:;!?]+$", "", w)) for w in head.split()]
    known = sum(1 for _, c in parts if c and c.lower() in cased)
    use_title = bool(parts) and known >= 0.7 * sum(1 for _, c in parts if c)
    words = []
    for i, (w, core) in enumerate(parts):
        if not core:
            words.append(w)
            continue
        got = cased.get(core.lower()) if use_title else None
        if got is None:
            if not core.isalpha() or (core.isupper() and ((len(core) <= 3 and core.lower() not in COMMON) or core in ACRONYMS)):
                got = core  # figures, tickers, acronyms (C.E.O., $16B, ETF, SEC)
            elif core.lower() in SMALL and i:
                got = core.lower()
            else:
                got = core[:1].upper() + core[1:].lower()
        if i == 0:
            got = got[:1].upper() + got[1:]
        words.append(w.replace(core, got, 1))
    return " ".join(words)


ACRONYMS = {"NATO", "OPEC", "NYSE", "FOMC", "NASA", "AMEX", "ECB", "IMF", "USDT", "USDC", "HYPE", "DOGE", "BLS", "CFTC", "FDIC", "OFAC",
            "SPAC", "NVDA", "TSLA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "MSTR", "COIN", "HOOD", "BRICS", "ASEAN", "OECD", "WTI", "LNG"}
SMALL = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "as", "by", "and", "or", "but", "vs", "via", "with", "from", "into", "over"}
COMMON = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "as", "by", "and", "or", "but", "is", "are", "was", "be", "its", "it", "has",
          "had", "new", "now", "up", "out", "off", "all", "after", "over", "into", "with", "from", "says", "say", "set", "cut", "hit", "top",
          "big", "war", "gas", "oil", "ban", "low", "high", "rise", "fall", "jump", "sink", "deal", "plan", "bid", "buy", "sell", "will", "may",
          "can", "not", "no", "hires", "fed", "law", "bank", "day", "key", "end", "two", "six", "ten", "one", "nor", "yet", "own", "than", "more", "less", "why", "how", "what", "who", "his", "her", "he", "she", "they", "we", "you"}


def kicker_for(ev: Any, section: str) -> str:
    from app.newsdesk import brief

    label = getattr(ev, "label", "") or ""
    if label == brief.BREAKING:
        return "BREAKING"
    if label == brief.ALERT:
        return "ALERT"
    if label == brief.UPDATE:
        return "UPDATE"
    if label == brief.JUST_IN:
        return "JUST IN"
    return section.upper()


def cinematic_for(ev: Any, background: Any, bg_meta: dict[str, Any], chosen: dict[str, Any], now: Any = None) -> tuple[bytes, dict[str, Any]]:
    from datetime import datetime, timezone

    from app.newsdesk import editorial_layouts as el

    typ = chosen.get("type") or story_type(ev)
    section = SECTION_FOR_TYPE.get(typ, "news")
    when = getattr(ev, "released_at", None) or now or datetime.now(timezone.utc)
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    layout = "left" if (chosen.get("seed", 0) >> 7) % 2 else "lower"
    model = str(bg_meta.get("model") or "")
    short = "Qwen-Image" if "qwen" in model.lower() else "FLUX.2 klein" if "klein" in model.lower() else "Z-Image" if "z-image" in model.lower() else "AI model"
    data, typeset = el.cinematic(background, headline=display_headline(ev), kicker=kicker_for(ev, section), section=section, sources=[source_name(ev)],
                                 when=when, layout=layout, illustration=short)
    return data, {**chosen, "kind": "cinematic", "render_job": bg_meta.get("job"), "scene": bg_meta.get("scene"), "model": model, **typeset}


# --- Price Chat News layouts (app/newsdesk/design): real photos first, then the GPU's illustration, then the desk's own --------------

SENSITIVE_TOPICS = ("hack", "scam", "failure", "controversy", "lawsuit", "crime")


def picture_headline(ev: Any) -> str:
    """The short headline the network package wrote for the picture (sentence case, <= 64 characters), else the display headline."""
    pkg = (ev.facts or {}).get("package") or {}
    short = str(((pkg.get("copy") or {}).get("short")) or "").strip()
    return short or display_headline(ev)


def _when(ev: Any, now: Any) -> Any:
    from datetime import datetime, timezone

    when = getattr(ev, "released_at", None) or now or datetime.now(timezone.utc)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _design_args(ev: Any, chosen: dict[str, Any], now: Any) -> dict[str, Any]:
    typ = chosen.get("type") or story_type(ev)
    section = SECTION_FOR_TYPE.get(typ, "news")
    pkg = (ev.facts or {}).get("package") or {}
    kicker = kicker_for(ev, section)
    if kicker == "BREAKING" and section == "breaking":  # an incident's section name isn't a label: only a story the desk labelled BREAKING says so
        kicker = "SECURITY" if (getattr(ev, "topic", "") or "") in ("hack", "scam", "exploit") else "MARKETS"
    if (getattr(ev, "label", "") or "") in ("BREAKING", "🚨 BREAKING") or kicker == "BREAKING":
        kicker = "PRICE CHAT BREAKING"  # Viral Engine 2.0: the breaking card says whose it is
    return {"headline": picture_headline(ev), "kicker": kicker, "section": section, "when": _when(ev, now), "line": str(pkg.get("line") or "")}


def timeline_of(ev: Any, db: Any) -> list[tuple[Any, str]]:
    """The posts this channel made about the story an update belongs to, oldest first, then the update itself."""
    from datetime import timezone as _tz

    from app.models.newsdesk import NdEvent

    if db is None or not getattr(ev, "parent_id", None):
        return []
    parent = db.get(NdEvent, ev.parent_id)
    if parent is None or parent.released_at is None:
        return []
    kids = [k for k in db.query(NdEvent).filter(NdEvent.parent_id == parent.id, NdEvent.status == "released", NdEvent.id != ev.id) if k.released_at]

    def utc(t: Any) -> Any:
        return t if t.tzinfo else t.replace(tzinfo=_tz.utc)

    told = [(utc(parent.released_at), picture_headline(parent))] + sorted(((utc(k.released_at), picture_headline(k)) for k in kids), key=lambda x: x[0])
    return [*told, (utc(getattr(ev, "released_at", None) or told[-1][0]), picture_headline(ev))]


def viral_visual(ev: Any, recent: list[dict[str, Any]] | None, now: Any, db: Any) -> tuple[bytes, dict[str, Any]] | None:
    """Formats beyond the photo and the illustration (owner: "rotate photos, cinematic illustrations, charts, comparison cards, timelines,
    stat graphics"): a data post is always its data card; a story's third telling or later is a timeline of what we posted, unless the
    last two pictures were already timelines. Drawn from the data each shows, nothing illustrative."""
    from app.newsdesk import datacards

    f = ev.facts or {}
    when = getattr(ev, "released_at", None) or now  # the post's own time: X redraws exactly what Telegram showed
    if getattr(ev, "kind", "") == "data" and f.get("data"):
        return datacards.data_card(f["data"], when=when, mad=bool(f.get("kicker")))
    if getattr(ev, "kind", "") == "update":
        steps = timeline_of(ev, db)
        lately = [str(r.get("design") or r.get("kind") or "") for r in (recent or [])][-2:]
        if len(steps) >= 3 and lately.count("timeline") < 2:
            img, meta = datacards.timeline_card(headline=picture_headline(ev), entries=steps, when=when)
            return img, {**meta, "steps": len(steps)}
    return None


def _style_of(stored: dict[str, Any], db: Any) -> str:
    """The picture's style: what it was first drawn in (X redraws Telegram's), else the desk's setting (network.post_style). Without a
    database (older callers, tests) the classic layout."""
    if stored.get("style"):
        return stored["style"] if stored["style"] in ("clean", "wire") else "classic"
    if stored.get("design") or stored.get("kind"):
        return "classic"
    if db is None:
        return "classic"
    from app.newsdesk import engine, network

    try:
        return network.post_style(engine.settings_row(db).state)
    except Exception:  # noqa: BLE001
        return "classic"


def recent_layouts(recent: list[dict[str, Any]] | None) -> list[str]:
    return [str(r.get("design") or "") for r in (recent or []) if r.get("design")]


def is_sensitive(ev: Any) -> bool:
    """A scandal, allegation, crime, hack or casualty story: its photo must show no people (app/newsdesk/photos)."""
    try:
        from app.newsdesk import photos

        if photos.is_sensitive(f"{getattr(ev, 'title', '')} {getattr(ev, 'headline', '')}"):
            return True
    except (ImportError, AttributeError):
        pass
    f = ev.facts or {}
    return (getattr(ev, "topic", "") or "") in SENSITIVE_TOPICS or bool(f.get("risk") or f.get("alleged"))


async def photo_for(ev: Any, chosen: dict[str, Any], *, used: set[str], transport: Any, recent: list[dict[str, Any]] | None,
                    now: Any) -> tuple[bytes, dict[str, Any]] | None:
    """A verified, licensed photograph of the story's actual subject in a Price Chat News layout, or None. Licence, relevance and (for a
    sensitive story) the no-people rule are app/newsdesk/photos'; the crop keeps the subject whole and the type never covers it."""
    from app.newsdesk import design

    queries = subject_queries(f"{ev.headline} {ev.title}")
    if not queries:
        return None
    terms = list(dict.fromkeys(must for _, must in queries))
    sensitive = is_sensitive(ev)
    seed = int(chosen.get("seed") or 0)
    try:
        from app.newsdesk import photos
    except ImportError:  # the older Commons-only search
        photos = None  # type: ignore[assignment]
    async with httpx.AsyncClient(transport=transport) as client:
        found: list[Any] = []
        try:
            if photos is not None:
                # the main subject first (a company's own headquarters before the city in the story): a later subject only when the
                # first has no licensed photo (2026-11-11: a Beijing skyline won over Nvidia's headquarters for an Nvidia story)
                for term in terms:
                    found = await photos.discover(client, [q for q, must in queries if must == term], [term], used=used, sensitive=sensitive)
                    if found:
                        break
            elif not sensitive:
                for query, must in queries:
                    found += [p for p in await find_photos(client, query) if re.search(must, p.title, re.I) and p.url not in used]
        except Exception as e:  # noqa: BLE001 - no photo: the illustration or the desk's own picture
            logger.info("newsdesk visual: photo discovery failed (%s)", type(e).__name__)
            return None
        for p in found[:4]:
            try:
                img = await fetch_image(client, p.url)
            except Exception:  # noqa: BLE001
                continue
            if not (photos.is_colour(img) and not photos.looks_like_graphic(img) if photos is not None else is_colour(img)):
                continue
            box = tuple(photos.subject_box(img)) if photos is not None else None
            data, meta = design.render(img, subject=box, credit=p.credit, recent=recent_layouts(recent), seed=seed, **_design_args(ev, chosen, now))
            info = p.to_meta() if hasattr(p, "to_meta") else {"url": p.url, "page": getattr(p, "page", ""), "title": p.title, "licence": getattr(p, "licence", "")}
            return data, {**chosen, "kind": "photo", "url": p.url, "credit": p.credit, "page": info.get("page", ""), "title": info.get("title", ""), "photo": info,
                          "source_subject": list(box) if box else None, "design": meta["format"], "design_seed": seed, **meta}
    return None


def illustration_for(ev: Any, background: Any, bg_meta: dict[str, Any], chosen: dict[str, Any], now: Any, recent: list[dict[str, Any]] | None = None,
                     layout: str | None = None) -> tuple[bytes, dict[str, Any]]:
    """A GPU background as a clearly labelled AI illustration in a Price Chat News layout."""
    from app.newsdesk import design

    model = str(bg_meta.get("model") or "")
    short = "Qwen-Image" if "qwen" in model.lower() else "FLUX.2 klein" if "klein" in model.lower() else "Z-Image" if "z-image" in model.lower() else "AI model"
    seed = int(chosen.get("seed") or 0)
    if layout is None:
        last = recent_layouts(recent)[-1:]
        layout = "band" if last == ["overlay"] else "overlay" if last == ["band"] else ("band" if seed % 2 else "overlay")
    data, meta = design.render(background, subject=None, credit="", illustration=short, layout=layout, seed=seed, **_design_args(ev, chosen, now))
    return data, {**chosen, "kind": "cinematic", "render_job": bg_meta.get("job"), "scene": bg_meta.get("scene"), "model": model, "ai_illustration": True,
                  "design": meta["format"], "design_seed": seed, **meta}


async def _again(ev: Any, chosen: dict[str, Any], *, transport: Any, now: Any, db: Any) -> tuple[bytes, dict[str, Any]] | None:
    """The same Price Chat News picture again (X re-renders what Telegram showed): same photo or background, same layout and seed."""
    from app.newsdesk import design

    if chosen.get("kind") == "photo" and chosen.get("url"):
        async with httpx.AsyncClient(transport=transport) as client:
            img = await fetch_image(client, chosen["url"])
        box = tuple(chosen["source_subject"]) if chosen.get("source_subject") else None
        data, _ = design.render(img, subject=box, credit=str(chosen.get("credit") or ""), layout=str(chosen.get("design")), seed=int(chosen.get("design_seed") or 0),
                                **_design_args(ev, chosen, now))
        return data, chosen
    if chosen.get("kind") == "cinematic" and db is not None and getattr(ev, "id", None):
        from app.newsdesk import render_queue

        bg = render_queue.background_for(db, ev.id)
        if bg is not None:
            return illustration_for(ev, bg[0], bg[1], chosen, now, layout=str(chosen.get("design")))
    return None


async def for_event(ev: Any, *, used: set[str] | None = None, transport: Any = None, market: Any = None, recent: list[dict[str, Any]] | None = None,
                    now: Any = None, db: Any = None, style: str | None = None, platform: str = "telegram",
                    records: list[Any] | None = None) -> tuple[bytes, dict[str, Any]]:
    """The picture for an event after the Visual Editor's final look (app/newsdesk/editor.review, owner 2026-11-19): every style and every
    lane (Telegram, X, the Admin preview) goes through it, and a refused picture makes the post text only. `platform` "x" draws Price
    Chat's templates in 16:9, Telegram gets the square format; `records` are the photos already used ([url, at, title, key])."""
    from app.newsdesk import editor

    image, meta = await _for_event(ev, used=used, transport=transport, market=market, recent=recent, now=now, db=db, style=style,
                                   platform=platform, records=records)
    return editor.review(ev, image, meta)


async def _for_event(ev: Any, *, used: set[str] | None = None, transport: Any = None, market: Any = None, recent: list[dict[str, Any]] | None = None,
                     now: Any = None, db: Any = None, style: str | None = None, platform: str = "telegram",
                     records: list[Any] | None = None) -> tuple[bytes, dict[str, Any]]:
    """The picture for an event, and what it was (stored on the event, so X re-renders exactly what Telegram showed). Owner, 2026-11-11:
    real photos first (licensed, of the actual subject), then the GPU's illustration (labelled), then the desk's own drawn visuals; a
    chart for a measured move stays a chart (accuracy first)."""
    f = ev.facts or {}
    used = used or set()
    stored_visual = dict(f.get("visual") or {})
    chosen_style = style or _style_of(stored_visual, db)
    if chosen_style == "wire":  # app/newsdesk/wire: a real chart, a data card, the public figure's photo, or no picture (b"")
        from app.newsdesk import wire

        return await wire.for_event(ev, stored_visual, used=used, transport=transport, market=market, now=now, db=db)
    if chosen_style == "clean":  # app/newsdesk/clean: the picture without a poster around it
        from app.newsdesk import clean

        if stored_visual.get("style") == "clean":
            try:
                again = await clean.redraw(ev, stored_visual, transport=transport, db=db, now=now, platform=platform)
            except Exception as e:  # noqa: BLE001 - drawn afresh below
                logger.info("newsdesk visual: couldn't redraw clean picture for %s (%s)", getattr(ev, "id", None), type(e).__name__)
                again = None
            if again is not None:
                return again
        return await clean.picture(ev, used=used, transport=transport, market=market, now=now, db=db, platform=platform, records=records)
    special = viral_visual(ev, recent, now, db)  # Viral Engine 2.0: a data post's card, a developing story's timeline
    if special is not None:
        return special
    if stored_visual.get("design"):
        try:
            got = await _again(ev, stored_visual, transport=transport, now=now, db=db)
        except Exception as e:  # noqa: BLE001 - fall through to the desk's own picture
            logger.info("newsdesk visual: couldn't redraw event %s (%s)", getattr(ev, "id", None), type(e).__name__)
            got = None
        if got is not None:
            return got
        stored_visual = {}
    elif not stored_visual:
        first = plan(ev, recent)
        if first["kind"] != "chart" and "photo" in first.get("options", []):
            got = await photo_for(ev, first, used=used, transport=transport, recent=recent, now=now)
            if got is not None:
                return got
        if first["kind"] != "chart" and db is not None and getattr(ev, "id", None):
            from app.newsdesk import render_queue

            try:
                bg = render_queue.background_for(db, ev.id)
            except Exception as e:  # noqa: BLE001 - the desk's own visuals instead
                logger.info("newsdesk visual: no GPU background for %s (%s)", getattr(ev, "id", None), type(e).__name__)
                bg = None
            if bg is not None:
                return illustration_for(ev, bg[0], bg[1], first, now, recent)
    chosen = stored_visual or plan(ev, recent)
    head = post_headline(ev)
    order = [chosen["kind"], *[k for k in chosen.get("options", kinds_for(ev)) if k != chosen["kind"]]]
    if stored_visual and chosen.get("kind") != "photo":
        order = [chosen["kind"]]  # already shown on Telegram: the same picture
    if db is not None and getattr(ev, "id", None) and chosen.get("kind") == "cinematic":  # an older event's cinematic picture, redrawn as it was
        from app.newsdesk import render_queue

        try:
            bg = render_queue.background_for(db, ev.id)
        except Exception as e:  # noqa: BLE001 - the desk's own visuals instead
            logger.info("newsdesk visual: no GPU background for %s (%s)", getattr(ev, "id", None), type(e).__name__)
            bg = None
        if bg is not None:
            return cinematic_for(ev, bg[0], bg[1], chosen, now)
    if chosen.get("kind") == "cinematic":  # its background was dropped (kept 7 days): the story's own plan instead
        chosen = plan(ev, recent)
        order = [chosen["kind"], *[k for k in chosen.get("options", kinds_for(ev)) if k != chosen["kind"]]]
    for kind in order:
        if kind == "chart":
            got = await _chart_for(ev, chosen, market, now)
            if got:
                return got
            continue
        if kind == "photo":  # a fresh photo is found only by photo_for (licence, relevance, no people on sensitive stories)
            if chosen.get("kind") == "photo" and chosen.get("url"):  # an older event's photo picture, redrawn as it was
                tone = "risk" if ev.topic in ("hack", "scam", "failure", "controversy") or f.get("risk") else "neutral"
                async with httpx.AsyncClient(transport=transport) as client:
                    img = await fetch_image(client, chosen["url"])
                data, _ = photo(img, chosen["credit"], tone=chosen.get("tone", tone), headline=head, layout=chosen.get("layout", "lower"))
                return data, chosen
            continue
        if kind in ("artwork", "graphic") and f.get("package") and not stored_visual:
            # Price Chat News: no licensed photo of the subject and no GPU picture (the PC offline): a clean typographic front page
            from app.newsdesk import design

            seed = int(chosen.get("seed") or 0)
            theme = "paper" if (seed >> 9) % 3 == 0 else "ink"
            data, meta = design.card(source=source_name(ev), theme=theme, **_design_args(ev, chosen, now))
            return data, {**chosen, "kind": "card", "design": "card", "design_seed": seed, **meta}
        if kind == "artwork":
            series = [p[1] for p in f.get("points") or []] or None
            data, typeset = artwork(head, motif=chosen["motif"], palette=chosen["palette"], layout=chosen["layout"], seed=chosen["seed"], series=series)
            return data, {**chosen, "kind": "artwork", **typeset}
        if kind == "graphic":
            data, typeset = graphic(head, palette=chosen["palette"], layout=chosen["layout"], seed=chosen["seed"], stat=_stat(ev), source=source_name(ev),
                                    motif=chosen.get("motif", "ridges"), mark=_mark(ev))
            return data, {**chosen, "kind": "graphic", **typeset}
    data, typeset = graphic(head, palette=chosen.get("palette", "cobalt"), layout=chosen.get("layout", "lower"), seed=chosen.get("seed", 1), source=source_name(ev))
    return data, {**chosen, "kind": "graphic", **typeset}
