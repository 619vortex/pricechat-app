"""Scene images, 1080×1920 (9:16), drawn with Pillow from the brief: no generative video, no stock footage.

Layout respects TikTok's on-screen UI: content stays in x 80-900 and y 150-1220; captions sit around y 1250-1500;
nothing important below y 1550 (TikTok's caption + buttons). No logo/watermark unless the owner turned it on
(TikTok's sharing guidelines forbid apps from adding brand watermarks to shared content)."""

import io
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
LEFT, RIGHT = 80, 900

FONT_CANDIDATES = {
    "bold": ["FONT_BOLD", "C:/Windows/Fonts/seguibl.ttf", "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"],
    "regular": ["FONT_REGULAR", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf"],
}


def font_path(kind: str) -> str:
    for c in FONT_CANDIDATES[kind]:
        p = os.environ.get(c) if c.isupper() else c
        if p and Path(p).exists():
            return p
    raise RuntimeError(f"No {kind} font found: set {FONT_CANDIDATES[kind][0]} to a .ttf file.")


_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    k = (kind, size)
    if k not in _cache:
        _cache[k] = ImageFont.truetype(font_path(kind), size)
    return _cache[k]


def hex_rgb(h: str, default: tuple[int, int, int] = (0, 229, 143)) -> tuple[int, int, int]:
    h = (h or "").lstrip("#")
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) if len(h) == 6 else default
    except ValueError:
        return default


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=f) <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def fit_text(draw: ImageDraw.ImageDraw, text: str, kind: str, start: int, minimum: int, width: int, max_lines: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    size = start
    while size > minimum:
        f = font(kind, size)
        lines = wrap(draw, text, f, width)
        if len(lines) <= max_lines:
            return f, lines
        size -= 4
    f = font(kind, minimum)
    return f, wrap(draw, text, f, width)[:max_lines]


def draw_lines(draw: ImageDraw.ImageDraw, lines: list[str], f: ImageFont.FreeTypeFont, x: int, y: int, fill: Any, spacing: float = 1.12,
               center: bool = False) -> int:
    lh = int(f.size * spacing)
    for ln in lines:
        tx = x if not center else (W - draw.textlength(ln, font=f)) / 2
        draw.text((tx, y), ln, font=f, fill=fill)
        y += lh
    return y


def _is_number(word: str) -> bool:
    return any(ch.isdigit() for ch in word) or word.strip(".,!?:;").lower() in ("percent", "million", "billion", "thousand", "trillion")


def draw_rich(draw: ImageDraw.ImageDraw, lines: list[str], f: ImageFont.FreeTypeFont, x: int, y: int, fill: Any, accent: Any,
              spacing: float = 1.12, center: bool = False) -> int:
    """Like draw_lines, but numbers ($150,000 · 3.2% · 1999) are drawn in the accent colour: the eye lands on them first."""
    lh = int(f.size * spacing)
    space = draw.textlength(" ", font=f)
    for ln in lines:
        tx = x if not center else (W - draw.textlength(ln, font=f)) / 2
        for word in ln.split(" "):
            draw.text((tx, y), word, font=f, fill=accent if _is_number(word) else fill)
            tx += draw.textlength(word, font=f) + space
        y += lh
    return y


class Painter:
    def __init__(self, job: dict[str, Any], workdir: Path) -> None:
        self.job = job
        self.dir = workdir
        style = job.get("template_style") or {}
        colors = job["brand"].get("colors") or []
        self.accent = hex_rgb(style.get("accent") or (colors[0] if colors else "#00e58f"))
        self.bg = hex_rgb(colors[1] if len(colors) > 1 else "#050706", (5, 7, 6))
        self.banner = style.get("banner") or ""
        self.images: dict[str, Image.Image] = {}
        self._background = self._make_background()

    # --- assets ----------------------------------------------------------------------------------------------------------

    def image(self, url: str | None) -> Image.Image | None:
        if not url or not url.startswith("https://"):
            return None
        if url not in self.images:
            try:
                r = httpx.get(url, timeout=20.0, follow_redirects=True, headers={"User-Agent": "pricechat-worker/1.0"})
                if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/") or len(r.content) > 12_000_000:
                    return None
                self.images[url] = Image.open(io.BytesIO(r.content)).convert("RGBA")
            except (httpx.HTTPError, OSError):
                return None
        return self.images[url]

    def _make_background(self) -> Image.Image:
        img = Image.new("RGB", (W, H), self.bg)
        glow = Image.new("RGB", (W, H), (0, 0, 0))
        g = ImageDraw.Draw(glow)
        g.ellipse((-300, -500, W + 300, 900), fill=tuple(int(c * 0.35) for c in self.accent))
        g.ellipse((300, 1300, W + 600, 2500), fill=tuple(int(c * 0.12) for c in self.accent))
        glow = glow.filter(ImageFilter.GaussianBlur(220))
        img = Image.blend(img, glow, 0.55)
        d = ImageDraw.Draw(img)
        for x in range(-H, W, 90):  # faint diagonal texture
            d.line((x, 0, x + H, H), fill=tuple(min(255, c + 7) for c in self.bg), width=1)
        return img

    def base(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        img = self._background.copy()
        d = ImageDraw.Draw(img)
        if self.banner:
            f = font("bold", 40)
            tw = d.textlength(self.banner, font=f)
            d.rounded_rectangle((LEFT, 170, LEFT + tw + 56, 236), radius=33, fill=self.accent)
            d.text((LEFT + 28, 178), self.banner, font=f, fill=(10, 10, 10))
        name = self.job["brand"]["name"]
        d.text((LEFT, 256), name.upper(), font=font("bold", 30), fill=(200, 205, 202))
        if self.job["strategy"].get("watermark"):
            logo = self.image(self.job["brand"].get("logo_url"))
            if logo:
                lg = logo.copy()
                lg.thumbnail((120, 120))
                img.paste(lg, (RIGHT - lg.width, 160), lg)
        return img, d

    def panel(self, d: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
        d.rounded_rectangle(box, radius=36, fill=(16, 22, 19), outline=(40, 52, 46), width=2)
        d.rounded_rectangle((box[0], box[1] + 30, box[0] + 10, box[3] - 30), radius=5, fill=self.accent)

    # --- scenes ----------------------------------------------------------------------------------------------------------

    def scene(self, kind: str, text: str, index: int, n_list: int = 0) -> Image.Image:
        fn = getattr(self, f"_{kind}", self._facts)
        return fn(text, index, n_list)

    def _glow_paste(self, img: Image.Image, pic: Image.Image, x: int, y: int, strength: int = 130) -> None:
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle((x - 30, y - 30, x + pic.width + 30, y + pic.height + 30), radius=40, fill=(*self.accent, strength))
        glow = glow.filter(ImageFilter.GaussianBlur(45))
        img.paste(glow, (0, 0), glow)
        img.paste(pic, (x, y), pic if pic.mode == "RGBA" else None)

    def _hook(self, text: str, i: int, _: int) -> Image.Image:
        """The first second decides the view: the story's picture big (a card, our thumbnail) or, without one, the hook
        words huge in the middle of the screen, numbers in the accent colour."""
        img, d = self.base()
        pic = self.image((self.job["brief"].get("images") or [{}])[0].get("url"))
        if pic:
            p = pic.copy()
            p.thumbnail((560, 640) if p.height >= p.width else (RIGHT - LEFT, 520))
            x, y = (W - p.width) // 2 - 10, 320
            p = p.rotate(-4 if p.height >= p.width else 0, resample=Image.BICUBIC, expand=True) if p.mode == "RGBA" else p
            self._glow_paste(img, p, x, y, 150)
            f, lines = fit_text(d, text, "bold", 112, 64, RIGHT - LEFT, 3)
            top = min(y + p.height + 40, 1230 - len(lines) * int(f.size * 1.05))
        else:
            f, lines = fit_text(d, text, "bold", 150, 80, RIGHT - LEFT, 5)
            top = max(360, 780 - len(lines) * int(f.size * 1.05) // 2)
        y2 = draw_rich(d, lines, f, LEFT, top, (255, 255, 255), self.accent, 1.05)
        d.rounded_rectangle((LEFT, y2 + 24, LEFT + 220, y2 + 38), radius=7, fill=self.accent)
        return img

    def _facts(self, text: str, i: int, _: int) -> Image.Image:
        img, d = self.base()
        f, lines = fit_text(d, text, "bold", 104, 56, RIGHT - LEFT - 90, 6)
        h = len(lines) * int(f.size * 1.14) + 130
        pic = self.image((self.job["brief"].get("images") or [{}])[0].get("url"))
        pts = (self.job["brief"].get("chart") or {}).get("points") or []
        top = 360 if (pic or len(pts) >= 2) else max(360, 780 - h // 2)
        self.panel(d, (LEFT, top, RIGHT, top + h))
        draw_rich(d, lines, f, LEFT + 60, top + 65, (245, 247, 245), self.accent, 1.14)
        room = (top + h + 50, 1220)  # the story's picture under the words, so the screen never sits half empty
        if pic and room[1] - room[0] > 220:
            p = pic.copy()
            p.thumbnail((RIGHT - LEFT, room[1] - room[0]))
            self._glow_paste(img, p, (W - p.width) // 2, room[0], 90)
        elif len(pts) >= 2 and room[1] - room[0] > 220:
            self._spark(img, d, (LEFT, room[0], RIGHT, room[1]), pts)
        return img

    def _spark(self, img: Image.Image, d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], pts: list[Any]) -> None:
        self.panel(d, box)
        ys = [p[1] for p in pts]
        lo, hi = min(ys), max(ys)
        span = (hi - lo) or 1.0
        x0, x1, y0, y1 = box[0] + 50, box[2] - 40, box[1] + 50, box[3] - 70
        line = [(x0 + (x1 - x0) * k / (len(pts) - 1), y1 - (p[1] - lo) / span * (y1 - y0)) for k, p in enumerate(pts)]
        col = (22, 199, 132) if ys[-1] >= ys[0] else (234, 57, 67)
        fill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(fill).polygon(line + [(x1, y1), (x0, y1)], fill=(*col, 50))
        img.paste(fill, (0, 0), fill)
        d.line(line, fill=col, width=7, joint="curve")
        d.text((x0, box[3] - 55), (self.job["brief"].get("chart") or {}).get("label", ""), font=font("regular", 30), fill=(150, 160, 155))

    def _source(self, text: str, i: int, _: int) -> Image.Image:
        img, d = self.base()
        src = (self.job["brief"].get("sources") or [{}])[0]
        d.text((LEFT, 470), "SOURCE", font=font("bold", 38), fill=self.accent)
        f, lines = fit_text(d, src.get("name") or "", "bold", 110, 60, RIGHT - LEFT, 2)
        y = draw_lines(d, lines, f, LEFT, 530, (255, 255, 255))
        f2, l2 = fit_text(d, self.job["brief"].get("headline") or "", "regular", 50, 38, RIGHT - LEFT, 5)
        draw_lines(d, l2, f2, LEFT, y + 40, (200, 205, 202), 1.25)
        return img

    def _chart(self, text: str, i: int, _: int) -> Image.Image:
        img, d = self.base()
        brief = self.job["brief"]
        nums = brief.get("numbers") or {}
        coin = brief.get("coin") or {}
        price = nums.get("price_usd")
        ch = nums.get("change_24h_pct")
        d.text((LEFT, 400), f"{coin.get('name', '')} ({coin.get('symbol', '')})", font=font("bold", 54), fill=(220, 225, 222))
        if price is not None:
            d.text((LEFT, 470), f"${price:,.2f}" if price < 100 else f"${price:,.0f}", font=font("bold", 120), fill=(255, 255, 255))
        if ch is not None:
            col = (22, 199, 132) if ch >= 0 else (234, 57, 67)
            label = f"{'▲' if ch >= 0 else '▼'} {abs(ch):.2f}% · 24h"
            fw = font("bold", 50)
            tw = d.textlength(label, font=fw)
            d.rounded_rectangle((LEFT, 625, LEFT + tw + 50, 700), radius=20, fill=col)
            d.text((LEFT + 25, 633), label, font=fw, fill=(255, 255, 255))
        pts = (brief.get("chart") or {}).get("points") or []
        if len(pts) >= 2:
            self._spark(img, d, (LEFT, 760, RIGHT, 1180), pts)
        else:
            self.panel(d, (LEFT, 760, RIGHT, 1180))
        return img

    def _card(self, text: str, i: int, _: int) -> Image.Image:
        img, d = self.base()
        brief = self.job["brief"]
        c = brief.get("card") or {}
        pic = self.image((brief.get("images") or [{}])[0].get("url"))
        if pic:
            p = pic.copy()
            p.thumbnail((520, 728))
            x, y = (W - p.width) // 2 - 30, 330
            glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(glow).rounded_rectangle((x - 30, y - 30, x + p.width + 30, y + p.height + 30), radius=40, fill=(*self.accent, 120))
            glow = glow.filter(ImageFilter.GaussianBlur(40))
            img.paste(glow, (0, 0), glow)
            img.paste(p, (x, y), p)
            y2 = y + p.height + 40
        else:
            y2 = 420
        f, lines = fit_text(d, c.get("name") or text, "bold", 72, 48, RIGHT - LEFT, 2)
        y2 = draw_lines(d, lines, f, LEFT, y2, (255, 255, 255))
        sub = " · ".join(str(v) for v in (c.get("set_name"), c.get("year")) if v)
        if sub:
            d.text((LEFT, y2 + 4), sub, font=font("regular", 40), fill=(200, 205, 202))
        return img

    def _list(self, text: str, i: int, n: int) -> Image.Image:
        img, d = self.base()
        num = f"#{max(1, n - i + 1)}" if n else f"#{i}"
        d.text((LEFT, 400), num, font=font("bold", 200), fill=self.accent)
        f, lines = fit_text(d, text, "bold", 100, 54, RIGHT - LEFT, 6)
        draw_rich(d, lines, f, LEFT, 660, (255, 255, 255), self.accent, 1.12)
        return img

    def _thumbnail(self, text: str, i: int, _: int) -> Image.Image:
        img, d = self.base()
        pic = self.image((self.job["brief"].get("images") or [{}])[0].get("url"))
        y = 420
        if pic:
            p = pic.copy().convert("RGB")
            scale = (RIGHT - LEFT) / p.width  # fill the width (YouTube thumbnails are often 480 px wide)
            p = p.resize((RIGHT - LEFT, int(p.height * scale)), Image.LANCZOS)
            if p.height > 620:
                p = p.crop((0, (p.height - 620) // 2, p.width, (p.height - 620) // 2 + 620))
            mask = Image.new("L", p.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, p.width, p.height), radius=28, fill=255)
            img.paste(p, (LEFT, y), mask)
            y += p.height + 40
        f, lines = fit_text(d, text, "bold", 64, 44, RIGHT - LEFT, 4)
        y = draw_lines(d, lines, f, LEFT, y, (255, 255, 255))
        d.text((LEFT, y + 20), "Full video on our YouTube channel", font=font("bold", 38), fill=self.accent)
        return img

    def _outro(self, text: str, i: int, _: int) -> Image.Image:
        img, d = self.base()
        b = self.job["brand"]
        if self.job["strategy"].get("outro_cta"):
            fp = font("bold", 46)
            d.rounded_rectangle((LEFT, 430, LEFT + d.textlength("FOLLOW", font=fp) + 64, 500), radius=35, outline=self.accent, width=4)
            d.text((LEFT + 32, 438), "FOLLOW", font=fp, fill=self.accent)
        f, lines = fit_text(d, b["name"], "bold", 130, 80, RIGHT - LEFT, 2)
        y = draw_lines(d, lines, f, LEFT, 530, (255, 255, 255))
        if self.job["strategy"].get("outro_cta") and b.get("cta"):
            f2, l2 = fit_text(d, b["cta"], "regular", 58, 40, RIGHT - LEFT, 4)
            y = draw_lines(d, l2, f2, LEFT, y + 30, (215, 220, 217), 1.2)
        site = (b.get("website") or "").replace("https://", "").replace("http://", "").rstrip("/")
        if site:
            d.text((LEFT, y + 40), site, font=font("bold", 52), fill=self.accent)
        return img


def render_scenes(job: dict[str, Any], segments: list[dict[str, Any]], workdir: Path) -> list[Path]:
    painter = Painter(job, workdir)
    n_list = sum(1 for s in segments if s.get("scene") == "list")
    out = []
    li = 0
    for k, seg in enumerate(segments):
        kind = seg.get("scene") or "facts"
        if kind == "list":
            li += 1
        text = str(seg.get("screen") or seg.get("text") or "")
        img = painter.scene(kind, text, li if kind == "list" else k, n_list)
        p = workdir / f"scene_{k:02d}.png"
        img.convert("RGB").save(p, optimize=False)
        out.append(p)
    return out


def platform_fonts_note() -> str:
    return "windows" if sys.platform == "win32" else "linux"
