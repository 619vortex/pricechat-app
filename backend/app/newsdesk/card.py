"""The branded Kiwi news card for major stories: a 1200x675 PNG, drawn here from the post's own words, never fetched.

The Kiwi is the canonical brand image (frontend/public/brand/kiwi.webp, copied beside this file), never redrawn. The card says nothing the post
doesn't: the headline as posted, its tickers, the outlets and the time. Fonts: Anton and Inter (SIL Open Font License, assets/OFL.txt).
"""

import io
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.newsdesk.brief import ALERT, BREAKING, JUST_IN, Brief

W, H = 1200, 675
ASSETS = Path(__file__).with_name("assets")
BG, TEXT, MUTED, KIWI_GREEN = (10, 12, 11), (246, 247, 246), (150, 160, 155), (132, 204, 22)
ACCENT = {BREAKING: (230, 36, 36), ALERT: (230, 36, 36), JUST_IN: (250, 190, 20)}
WORD = {BREAKING: "BREAKING", ALERT: "ALERT", JUST_IN: "JUST IN"}


@lru_cache(maxsize=16)
def _anton(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "Anton-Regular.ttf"), size)


@lru_cache(maxsize=16)
def _inter(size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(ASSETS / "Inter-Variable.ttf"), size)
    f.set_variation_by_name(weight)
    return f


def _wrap(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and d.textlength(f"{lines[-1]} {word}", font=font) <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


def render(headline: str, brief: Brief, sources: list[str], at: datetime) -> bytes:
    accent = ACCENT.get(brief.label, KIWI_GREEN)
    img = Image.new("RGB", (W, H), BG)
    # a soft glow of the label's colour from the top left, so a red card reads as red at a glance
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    for r in range(520, 0, -8):
        gd.ellipse([-r, -r, r, r], fill=int(46 * (1 - r / 520)))
    img.paste(Image.new("RGB", (W, H), accent), (0, 0), glow)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 14, H], fill=accent)
    x0 = 64
    # top: the label pill (only when earned), then the brand on the right
    if brief.label in WORD:
        f = _inter(30, "Black")
        word = WORD[brief.label]
        tw = d.textlength(word, font=f)
        d.rounded_rectangle([x0, 52, x0 + tw + 44, 104], radius=10, fill=accent)
        d.text((x0 + 22, 78), word, font=f, fill=(15, 15, 15) if brief.label == JUST_IN else (255, 255, 255), anchor="lm")
    kiwi = Image.open(ASSETS / "kiwi.webp").convert("RGBA")
    kiwi.thumbnail((84, 84))
    brand = _inter(30, "Black")
    bw = d.textlength("PRICE ALERT", font=brand)
    bx = W - 64 - bw
    kx, ky = int(bx - kiwi.width - 22), 36
    d.ellipse([kx - 6, ky - 4, kx + kiwi.width + 6, ky + kiwi.width + 8], fill=(236, 246, 228))  # the Kiwi is dark: it sits on a light badge
    img.paste(kiwi, (kx, ky + (kiwi.width - kiwi.height) // 2 + 2), kiwi)
    d.text((bx, 64), "PRICE ALERT", font=brand, fill=TEXT, anchor="lm")
    d.text((bx, 96), "by Price Chat", font=_inter(20, "Medium"), fill=MUTED, anchor="lm")
    # the headline, as large as fits in four lines
    text = headline.upper()
    size, lines = 96, []
    for size in (96, 88, 80, 72, 64, 58, 52):
        lines = _wrap(d, text, _anton(size), W - x0 - 64)
        if len(lines) <= 4:
            break
    lines = lines[:4]
    f = _anton(size)
    step = int(size * 1.12)
    y = 140 + (H - 140 - 110 - step * len(lines)) // 2
    for line in lines:
        d.text((x0, y), line, font=f, fill=TEXT)
        y += step
    # the foot: tickers on the left, the outlets and the time on the right
    d.line([x0, H - 92, W - 64, H - 92], fill=(40, 46, 43), width=2)
    if brief.tickers:
        d.text((x0, H - 50), "   ".join(f"${t}" for t in brief.tickers), font=_inter(30, "ExtraBold"), fill=KIWI_GREEN, anchor="lm")
    right = f"{' · '.join(sources[:2])}  ·  {at.strftime('%d %b %Y, %H:%M')} UTC"
    foot = _inter(22, "Medium")
    d.text((W - 64, H - 50), right, font=foot, fill=MUTED, anchor="rm")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
