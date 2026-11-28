"""Price Chat Predictions images (Pillow, drawn here, never fetched): the call card posted in the group, the result card, and the member's
shareable card. Kiwi identity: near-black #050706, Price Chat green #00e58f, Anton display type, Inter text, the canonical kiwi.

Every card names the instrument and settlement source in small type ("Hyperliquid BTC perpetual, daily close 00:00 UTC") and says what
it is ("A free game for XP. Not financial advice."). No price targets, no advice wording.
"""

from __future__ import annotations

import io
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parents[1] / "newsdesk" / "assets"
BG = (5, 7, 6)
RAISE = (13, 19, 16)
LINE = (32, 44, 38)
TEXT = (244, 245, 243)
SOFT = (195, 201, 197)
MUTED = (133, 141, 136)
GREEN = (0, 229, 143)
RED = (255, 90, 95)
GOLD = (245, 197, 66)
SS = 2  # drawn at 2x and scaled down: smooth edges
FOOT = "A free game for XP · no money involved · not financial advice"


@lru_cache(maxsize=32)
def _anton(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ASSETS / "Anton-Regular.ttf"), size * SS)


@lru_cache(maxsize=32)
def _inter(size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(ASSETS / "Inter-Variable.ttf"), size * SS)
    f.set_variation_by_name(weight)
    return f


@lru_cache(maxsize=4)
def _kiwi(px: int) -> Image.Image:
    k = Image.open(ASSETS / "kiwi.webp").convert("RGBA")
    k.thumbnail((px * SS, px * SS), Image.LANCZOS)
    return k


class _C:
    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * SS, h * SS), BG)
        self.d = ImageDraw.Draw(self.img)

    def glow(self, cx: float, cy: float, r: float, color: tuple[int, int, int], alpha: int) -> None:
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), fill=(*color, alpha))
        layer = layer.filter(ImageFilter.GaussianBlur(r * SS * 0.45))
        self.img.paste(layer, (0, 0), layer)
        self.d = ImageDraw.Draw(self.img)

    def text(self, xy: tuple[float, float], s: str, font: ImageFont.FreeTypeFont, fill: tuple[int, int, int], anchor: str = "la") -> None:
        self.d.text((xy[0] * SS, xy[1] * SS), s, font=font, fill=fill, anchor=anchor)

    def tw(self, s: str, font: ImageFont.FreeTypeFont) -> float:
        return self.d.textlength(s, font=font) / SS

    def rect(self, box: tuple[float, float, float, float], fill: Any, r: float = 0, outline: Any = None, width: int = 0) -> None:
        self.d.rounded_rectangle(tuple(v * SS for v in box), radius=r * SS, fill=fill, outline=outline, width=width * SS)  # type: ignore[arg-type]

    def kiwi(self, x: float, y: float, px: int) -> None:
        k = _kiwi(px)
        self.img.paste(k, (int(x * SS), int(y * SS)), k)

    def medal(self, cx: float, cy: float, r: float) -> None:
        """The kiwi on a Price Chat green disc (the kiwi itself is dark: on the dark ground it needs the disc)."""
        self.d.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), fill=GREEN)
        k = _kiwi(int(r * 1.5))
        self.img.paste(k, (int(cx * SS - k.width / 2), int(cy * SS - k.height / 2 + r * 0.06 * SS)), k)

    def fit(self, s: str, maxw: float, size: int, minsize: int) -> ImageFont.FreeTypeFont:
        while size > minsize and self.tw(s, _anton(size)) > maxw:
            size -= 2
        return _anton(size)

    def wrap(self, s: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
        lines: list[str] = []
        for word in s.split():
            if lines and self.tw(f"{lines[-1]} {word}", font) <= width:
                lines[-1] = f"{lines[-1]} {word}"
            else:
                lines.append(word)
        return lines

    def png(self) -> bytes:
        out = self.img.resize((self.w, self.h), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def _brand(c: _C, x: float, y: float, label: str) -> None:
    c.medal(x + 18, y + 13, 20)
    c.text((x + 50, y + 3), label.upper(), _inter(17, "Bold"), GREEN)


def _footer(c: _C, pad: float, source: str) -> None:
    c.d.line((pad * SS, (c.h - 70) * SS, (c.w - pad) * SS, (c.h - 70) * SS), fill=LINE, width=2 * SS)
    c.text((pad, c.h - 52), "Price Chat Predictions", _inter(18, "Bold"), TEXT)
    c.text((pad, c.h - 28), FOOT, _inter(14, "Medium"), MUTED)
    c.text((c.w - pad, c.h - 40), source, _inter(14, "Medium"), MUTED, anchor="rm")


def call_card(q: dict[str, Any], w: int = 1280, h: int = 720) -> bytes:
    """The group post's image: the question, the reference, the lock time. q: {kind, title, ref_label, lock_label, options:[labels],
    settle_label, day_label}. The vote split is never drawn on it (hidden until lock)."""
    c = _C(w, h)
    c.glow(w * 0.86, h * 0.12, 300, GREEN, 46)
    pad = 64
    _brand(c, pad, 56, f"{'Daily' if q['kind'] == 'daily' else 'Weekly'} call · {q['day_label']}")
    font = _anton(86)
    lines = c.wrap(q["title"].upper(), font, w - 2 * pad - 200)
    y = 124
    for ln in lines[:3]:
        c.text((pad, y), ln, font, TEXT)
        y += 96
    c.text((pad, y + 16), q["ref_label"], _inter(26, "SemiBold"), SOFT)
    # the options as pills
    x, py = pad, y + 74
    for i, lab in enumerate(q["options"]):
        f = _inter(26, "Bold")
        shown = ("▲ " if q["kind"] == "daily" and i == 0 else "▼ " if q["kind"] == "daily" else "") + lab
        pw = c.tw(shown, f) + 56  # measured with its arrow, so the label sits inside its pill
        if x + pw > w - pad:
            x, py = pad, py + 70
        col = GREEN if (q["kind"] == "daily" and i == 0) else (RED if q["kind"] == "daily" else SOFT)
        c.rect((x, py, x + pw, py + 54), RAISE, r=27, outline=LINE, width=2)
        c.text((x + 28, py + 13), shown, f, col)
        x += pw + 14
    c.rect((w - pad - 300, 60, w - pad, 108), RAISE, r=24, outline=LINE, width=2)
    c.text((w - pad - 150, 84), f"Locks {q['lock_label']}", _inter(20, "Bold"), GOLD, anchor="mm")
    c.medal(w - pad - 95, h - 205, 95)
    c.text((pad, h - 112), "Tap a button under this post. Your call stays private until voting locks.", _inter(20, "Medium"), MUTED)
    _footer(c, pad, q["settle_label"])
    return c.png()


def result_card(r: dict[str, Any], w: int = 1280, h: int = 720) -> bytes:
    """A settled call. r: {kind, title, outcome_label, close_label, ref_label, right_share (0..1 or None), players, day_label, settle_label}."""
    c = _C(w, h)
    up = r.get("direction") != "down"
    c.glow(w * 0.82, h * 0.2, 320, GREEN if up else RED, 50)
    pad = 64
    _brand(c, pad, 56, f"Result · {r['day_label']}")
    c.medal(w - pad - 95, 250, 95)
    c.text((pad, 120), r["title"].upper(), c.fit(r["title"].upper(), w - 2 * pad, 52, 30), SOFT)
    big = r["outcome_label"].upper()
    c.text((pad, 190), big, c.fit(big, w - 2 * pad, 190, 90), GREEN if up else RED)
    c.text((pad, 420), r["close_label"], _inter(30, "Bold"), TEXT)
    c.text((pad, 462), r["ref_label"], _inter(24, "Medium"), SOFT)
    if r.get("right_share") is not None:
        share = float(r["right_share"])
        c.text((pad, 528), f"{share:.0%} of {r['players']:,} callers got it right", _inter(26, "SemiBold"), TEXT)
        c.rect((pad, 574, w - pad, 592), LINE, r=9)
        c.rect((pad, 574, pad + (w - 2 * pad) * max(0.02, min(1.0, share)), 592), GREEN, r=9)
    _footer(c, pad, r["settle_label"])
    return c.png()


def share_card(s: dict[str, Any], portrait: bool = True) -> bytes:
    """A member's shareable card. s: {name, level, headline (e.g. "6 of 7"), kicker ("Called it"), sub, dots: [True/False/None...],
    period_label, accuracy_label, streak}. Portrait 1080x1350 (Stories, Telegram) or landscape 1600x900 (X)."""
    w, h = (1080, 1350) if portrait else (1600, 900)
    c = _C(w, h)
    c.glow(w * 0.85, h * 0.05, 520 if portrait else 460, GREEN, 70)
    c.glow(w * 0.1, h * 0.95, 360, GREEN, 26)
    pad = 72
    _brand(c, pad, 72, "Price Chat Predictions")
    c.medal(w - pad - (130 if portrait else 110), (250 if portrait else 210), 130 if portrait else 110)
    y = 190 if portrait else 160
    c.text((pad, y), s["kicker"].upper(), _anton(120 if portrait else 104), TEXT)
    y += 140 if portrait else 120
    c.text((pad, y), s["headline"].upper(), _anton(250 if portrait else 200), GREEN)
    y += 330 if portrait else 256
    c.text((pad, y), s["sub"], _inter(36 if portrait else 32, "SemiBold"), SOFT)
    y += 90 if portrait else 70
    x = pad
    size = 54 if portrait else 46
    for d in s.get("dots", [])[:14]:
        col = GREEN if d is True else ((90, 30, 34) if d is False else LINE)
        c.rect((x, y, x + size, y + size), col, r=12)
        x += size + 14
    stats_y = h - (330 if portrait else 250)
    for i, (v, lab) in enumerate(((s.get("accuracy_label") or "—", "accuracy"), (str(s.get("streak", 0)), "streak"), (s["level"], "Predictor"))):
        bx = pad + i * ((w - 2 * pad) / 3)
        c.text((bx, stats_y), v.upper(), _anton(64 if portrait else 56), TEXT)
        c.text((bx, stats_y + (86 if portrait else 74)), lab.upper(), _inter(20, "Bold"), MUTED)
    c.text((pad, h - 120), s["name"], _inter(34, "Bold"), TEXT)
    c.text((pad, h - 74), s["period_label"], _inter(22, "Medium"), MUTED)
    c.text((w - pad, h - 46), "Free game · XP only · not financial advice", _inter(16, "Medium"), MUTED, anchor="rs")
    return c.png()


def day_label(d: Any) -> str:
    return f"{d:%a} {d:%b} {d.day}"
