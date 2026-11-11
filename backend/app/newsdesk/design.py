"""Price Chat News picture layouts for photographs and illustrations (owner, 2026-11-11: "premium Bloomberg/Reuters-style visuals; cleaner
typography, varied layouts, intelligent crops, shorter punchier headlines; headlines never obscure the photo's subject").

Three layouts, one system (Source Serif 4 headlines in sentence case, Inter labels, the kiwi and PRICE CHAT NEWS on every picture):

  band     the photo on top, a paper (or ink) band beneath it carrying the kicker, the headline and the credit. The type never touches
           the photograph: the safest layout, the default when the subject fills the frame.
  side     a solid panel on the side away from the subject, the photo on the subject's side, cropped to keep the subject whole.
  overlay  full-bleed photo, the headline set in the photo's empty zone (sky, wall, sea) behind a local gradient; used only when the
           subject box is clear of the type (covered < MAX_COVER), otherwise band.

The subject box comes from app/newsdesk/photos (saliency), in 0-1 coordinates of the source image. An AI illustration (a GPU background)
uses the same layouts with an "AI ILLUSTRATION" tag on the picture and a line in the credit, so it is never mistaken for a photograph.
Real photographs are only cropped, resized and gently toned here: no pixel of the scene is invented or removed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from app.newsdesk import editorial_layouts as el

W, H = el.W, el.H
M = 64
MAX_COVER = 0.08  # the most of the subject box the type may cover in an overlay
PAPER, PAPER_TEXT, PAPER_MUTED, PAPER_RULE = (246, 244, 239), (17, 19, 23), (96, 99, 104), (214, 209, 199)
INK, INK_TEXT, INK_MUTED = (12, 15, 20), (244, 245, 242), (150, 158, 166)
ACCENT = {"breaking": (214, 40, 40), "markets": (20, 140, 90), "crypto": (226, 140, 20), "macro": (40, 100, 210), "geopolitics": (200, 50, 40),
          "policy": (60, 90, 200), "business": (30, 110, 190), "developing": (214, 130, 20), "news": (40, 100, 210), "energy": (200, 120, 20)}
LAYOUTS = ("band", "side", "overlay")


# --- crops ----------------------------------------------------------------------------------------------------------------


def crop_to(img: Image.Image, size: tuple[int, int], subject: tuple[float, float, float, float] | None) -> tuple[Image.Image, tuple[float, float, float, float] | None]:
    """Crop `img` to the aspect of `size` keeping the subject box whole when it fits (centred on it otherwise), then resize. Returns the
    image and the subject box in the output's 0-1 coordinates."""
    tw, th = size
    iw, ih = img.size
    target = tw / th
    if iw / ih > target:  # too wide: choose x
        cw, ch = int(round(ih * target)), ih
    else:
        cw, ch = iw, int(round(iw / target))
    if subject is None:
        x0, y0 = (iw - cw) // 2, int((ih - ch) * 0.42)
    else:
        sx0, sy0, sx1, sy1 = subject[0] * iw, subject[1] * ih, subject[2] * iw, subject[3] * ih
        cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
        x0 = int(min(max(cx - cw / 2, 0), iw - cw))  # centred on the subject: a subject narrower than the crop stays whole
        y0 = int(min(max(cy - ch / 2, 0), ih - ch))
    out = img.crop((x0, y0, x0 + cw, y0 + ch)).resize((tw, th), Image.Resampling.LANCZOS)
    if subject is None:
        return out, None
    box = (max(0.0, (subject[0] * iw - x0) / cw), max(0.0, (subject[1] * ih - y0) / ch), min(1.0, (subject[2] * iw - x0) / cw), min(1.0, (subject[3] * ih - y0) / ch))
    return out, box


def tone(img: Image.Image) -> Image.Image:
    """A light, even grade: a touch more contrast, slightly less saturation. Global only: nothing in the picture is changed."""
    img = ImageEnhance.Contrast(img.convert("RGB")).enhance(1.05)
    return ImageEnhance.Color(img).enhance(0.94)


def covered(subject: tuple[float, float, float, float] | None, rect: tuple[float, float, float, float]) -> float:
    """Fraction of the subject box (0-1 coordinates) inside rect (0-1 coordinates)."""
    if subject is None:
        return 0.0
    x0, y0 = max(subject[0], rect[0]), max(subject[1], rect[1])
    x1, y1 = min(subject[2], rect[2]), min(subject[3], rect[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = max(1e-6, (subject[2] - subject[0]) * (subject[3] - subject[1]))
    return (x1 - x0) * (y1 - y0) / area


# --- pieces ---------------------------------------------------------------------------------------------------------------


def brand_tag(img: Image.Image, xy: tuple[int, int], *, dark_bg: bool) -> None:
    """The kiwi and PRICE CHAT NEWS, small."""
    d = ImageDraw.Draw(img)
    x, y = xy
    disc = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, 39, 39), fill=(236, 244, 226, 255) if dark_bg else (20, 22, 26, 255))
    k = el.kiwi().resize((32, 32), Image.Resampling.LANCZOS)
    if not dark_bg:
        k = Image.merge("RGBA", (*[Image.eval(k.getchannel(c), lambda v: 255 - v) for c in "RGB"], k.getchannel("A")))
    disc.alpha_composite(k, (4, 4))
    img.paste(disc, (x, y), disc)
    el.tracked(d, (x + 50, y + 9), "PRICE CHAT NEWS", el.sans(20, "ExtraBold"), (246, 246, 244) if dark_bg else PAPER_TEXT, 2.4)


def photo_brand(img: Image.Image) -> None:
    """The brand tag over a photograph: on a small soft dark plate so it reads over sky or snow."""
    plate = Image.new("L", (W, H), 0)
    ImageDraw.Draw(plate).rounded_rectangle((M - 14, M - 14, M + 300, M + 54), radius=28, fill=150)
    plate = plate.filter(ImageFilter.GaussianBlur(10))
    img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), plate)
    brand_tag(img, (M, M), dark_bg=True)


def ai_tag(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    f = el.sans(18, "Bold")
    text = "AI ILLUSTRATION"
    tw = el.tracked_len(text, f, 1.8)
    x1, y0 = W - M, M - 4
    d.rounded_rectangle((x1 - tw - 28, y0, x1, y0 + 34), radius=5, fill=(250, 250, 248))
    el.tracked(d, (x1 - tw - 14, y0 + 7), text, f, (20, 20, 20), 1.8)


def kicker_row(d: ImageDraw.ImageDraw, xy: tuple[int, int], kicker: str, accent: tuple[int, int, int], when: datetime | None, muted: tuple[int, int, int],
               right: int) -> None:
    x, y = xy
    d.rectangle((x, y + 2, x + 6, y + 24), fill=accent)
    end = el.tracked(d, (x + 18, y), kicker.upper(), el.sans(21, "Bold"), accent, 2.0)
    if when is not None:
        t = el.dateline(when)
        f = el.sans(19, "Medium")
        tx = right - el.tracked_len(t, f, 1.1)
        if tx > end + 30:
            el.tracked(d, (tx, y + 2), t, f, muted, 1.1)


def headline_lines(text: str, width: int, sizes: tuple[int, ...], max_lines: int) -> tuple[Any, list[str]]:
    font, lines = el.fit(text, width, sizes, max_lines)
    for narrower in (0.94, 0.88, 0.82):  # no lone last word
        if len(lines) < 2 or len(lines[-1].split()) > 1:
            break
        again = el.wrap(text, font, int(width * narrower))
        if len(again) == len(lines):
            lines = again
    return font, lines


def _credit_text(credit: str, illustration: str) -> str:
    if illustration:
        return f"AI illustration ({illustration}), not a photo of the event"
    return credit


# --- layouts --------------------------------------------------------------------------------------------------------------


def band(photo: Image.Image, *, subject: tuple[float, float, float, float] | None, headline: str, kicker: str, section: str, credit: str,
         when: datetime | None, line: str = "", illustration: str = "", theme: str = "paper") -> tuple[bytes, dict[str, Any]]:
    accent = ACCENT.get(section, ACCENT["news"])
    bg, fg, mu = (PAPER, PAPER_TEXT, PAPER_MUTED) if theme == "paper" else (INK, INK_TEXT, INK_MUTED)
    width = W - 2 * M
    font, lines = headline_lines(headline, width, (62, 58, 54, 50, 46), 2)
    lh = int(font.size * 1.14)
    band_h = 34 + 30 + 18 + len(lines) * lh + 22 + 30 + 26
    band_h = max(250, min(360, band_h))
    ph = H - band_h
    img = Image.new("RGB", (W, H), bg)
    pic, box = crop_to(photo, (W, ph), subject)
    img.paste(tone(pic), (0, 0))
    photo_brand(img)
    if illustration:
        ai_tag(img)
    d = ImageDraw.Draw(img)
    d.rectangle((0, ph, W, ph + 4), fill=accent)
    y = ph + 30
    kicker_row(d, (M, y), kicker, accent, when, mu, W - M)
    y += 30 + 18
    for ln in lines:
        d.text((M, y), ln, font=font, fill=fg)
        y += lh
    f = el.sans(18, "Medium")
    foot = "  ·  ".join(x for x in (line, _credit_text(credit, illustration)) if x)
    d.text((M, H - 26 - 22), foot, font=f, fill=mu)
    box_out = None if box is None else (box[0], box[1] * ph / H, box[2], box[3] * ph / H)
    return el._jpeg(img), {"format": "band", "layout": "band", "theme": theme, "lines": len(lines), "headline_px": font.size, "photo_h": ph,
                           "subject": box_out, "covered": 0.0}


def side(photo: Image.Image, *, subject: tuple[float, float, float, float] | None, headline: str, kicker: str, section: str, credit: str,
         when: datetime | None, line: str = "", illustration: str = "", panel_side: str | None = None) -> tuple[bytes, dict[str, Any]]:
    accent = ACCENT.get(section, ACCENT["news"])
    pw = 620
    if panel_side is None:
        cx = 0.5 if subject is None else (subject[0] + subject[2]) / 2
        panel_side = "left" if cx >= 0.5 else "right"
    img = Image.new("RGB", (W, H), INK)
    pic, box = crop_to(photo, (W - pw, H), subject)
    px = pw if panel_side == "left" else 0
    img.paste(tone(pic), (px, 0))
    d = ImageDraw.Draw(img)
    tx = (0 if panel_side == "left" else W - pw) + 56
    tw = pw - 56 * 2
    brand_tag(img, (tx, M), dark_bg=True)
    if illustration:
        ai_tag(img)
    font, lines = headline_lines(headline, tw, (60, 56, 52, 48, 44), 5)
    lh = int(font.size * 1.16)
    block = 30 + 22 + len(lines) * lh
    y = max(M + 90, (H - block) // 2 - 10)
    kicker_row(d, (tx, y), kicker, tuple(min(255, int(c * 1.25)) for c in accent), None, INK_MUTED, tx + tw)
    y += 30 + 22
    for ln in lines:
        d.text((tx, y), ln, font=font, fill=INK_TEXT)
        y += lh
    f = el.sans(17, "Medium")
    yb = H - M - 46
    if when is not None:
        d.text((tx, yb), el.dateline(when), font=f, fill=INK_MUTED)
    if line:
        d.text((tx, yb + 24), line, font=f, fill=INK_MUTED)
    cred = _credit_text(credit, illustration)
    if cred:
        cf = el.sans(15, "Medium")
        cw = cf.getlength(cred)
        cx0 = (px + (W - pw) - 18 - cw) if panel_side == "left" else (W - pw - 18 - cw)
        shadow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(shadow).text((cx0, H - 30), cred, font=cf, fill=255)
        img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(4)).point(lambda v: min(255, v * 3)))
        ImageDraw.Draw(img).text((cx0, H - 30), cred, font=cf, fill=(232, 232, 230))
    if box is not None:
        fx = px / W
        box = (fx + box[0] * (W - pw) / W, box[1], fx + box[2] * (W - pw) / W, box[3])
    return el._jpeg(img), {"format": "side", "layout": f"side-{panel_side}", "lines": len(lines), "headline_px": font.size, "subject": box, "covered": 0.0}


def overlay_rect(zone: str, lines: int, lh: int) -> tuple[int, int, int, int]:
    block = 30 + 18 + lines * lh + 20
    if zone == "left":
        return (M, (H - block) // 2, int(W * 0.52), (H - block) // 2 + block)
    if zone == "right":
        return (int(W * 0.48), (H - block) // 2, W - M, (H - block) // 2 + block)
    if zone == "upper":
        return (M, M + 80, W - M, M + 80 + block)
    return (M, H - M - 40 - block, W - M, H - M - 40)


def overlay(photo: Image.Image, *, subject: tuple[float, float, float, float] | None, zone: str, headline: str, kicker: str, section: str, credit: str,
            when: datetime | None, line: str = "", illustration: str = "") -> tuple[bytes, dict[str, Any]] | None:
    """None when the type would cover the subject: the caller uses band instead."""
    accent = ACCENT.get(section, ACCENT["news"])
    pic, box = crop_to(photo, (W, H), subject)
    width = int(W * 0.46) if zone in ("left", "right") else W - 2 * M
    font, lines = headline_lines(headline, width, (64, 58, 54, 50), 3)
    lh = int(font.size * 1.14)
    rect = overlay_rect(zone, len(lines), lh)
    norm = (rect[0] / W, rect[1] / H, rect[2] / W, rect[3] / H)
    cov = covered(box, norm)
    if cov > MAX_COVER:
        return None
    img = tone(pic)
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.rectangle((rect[0] - 80, rect[1] - 70, rect[2] + 80, rect[3] + 70), fill=185)
    mask = mask.filter(ImageFilter.GaussianBlur(70))
    img = Image.composite(Image.new("RGB", (W, H), (6, 8, 12)), img, mask)
    photo_brand(img)
    if illustration:
        ai_tag(img)
    d = ImageDraw.Draw(img)
    x, y = rect[0], rect[1]
    kicker_row(d, (x, y), kicker, tuple(min(255, int(c * 1.3)) for c in accent), None, INK_MUTED, rect[2])
    y += 30 + 18
    for ln in lines:
        d.text((x, y), ln, font=font, fill=(250, 250, 248))
        y += lh
    foot = "  ·  ".join(t for t in (el.dateline(when) if when else "", line, _credit_text(credit, illustration)) if t)
    f = el.sans(17, "Medium")
    shade = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shade).rectangle((0, H - 64, W, H), fill=150)
    img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shade.filter(ImageFilter.GaussianBlur(20)))
    ImageDraw.Draw(img).text((M, H - 40), foot, font=f, fill=(226, 228, 230))
    return el._jpeg(img), {"format": "overlay", "layout": f"overlay-{zone}", "lines": len(lines), "headline_px": font.size, "subject": box, "covered": round(cov, 3)}


def card(*, headline: str, kicker: str, section: str, when: datetime | None, line: str = "", source: str = "", theme: str = "ink",
         figure: str = "") -> tuple[bytes, dict[str, Any]]:
    """No photo and no GPU picture (the PC offline, nothing licensed of the subject): a clean typographic front page, never a fake photo.
    A wide accent rule, the kicker, the headline in Source Serif, an optional verified figure, the byline line and the source."""
    accent = ACCENT.get(section, ACCENT["news"])
    bg, fg, mu = (INK, INK_TEXT, INK_MUTED) if theme == "ink" else (PAPER, PAPER_TEXT, PAPER_MUTED)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    # a quiet texture: fine horizontal rules, like newsprint columns
    for y in range(0, H, 6):
        d.line((0, y, W, y), fill=tuple(min(255, max(0, c + (3 if theme == "ink" else -3))) for c in bg))
    d.rectangle((0, 0, 14, H), fill=accent)
    brand_tag(img, (M + 20, M), dark_bg=theme == "ink")
    width = W - 2 * M - 40 - (360 if figure else 0)
    font, lines = headline_lines(headline, width, (78, 72, 66, 60, 54), 3)
    lh = int(font.size * 1.14)
    block = 30 + 26 + len(lines) * lh
    y = max(M + 110, (H - block) // 2)
    kicker_row(d, (M + 20, y), kicker, accent if theme == "paper" else tuple(min(255, int(c * 1.3)) for c in accent), when, mu, W - M)
    y += 30 + 26
    for ln in lines:
        d.text((M + 20, y), ln, font=font, fill=fg)
        y += lh
    if figure:
        ff = el.sans(96, "Bold")
        d.text((W - M - ff.getlength(figure), (H - 96) // 2 + 40), figure, font=ff, fill=accent if theme == "paper" else tuple(min(255, int(c * 1.3)) for c in accent))
    d.line((M + 20, H - M - 40, W - M, H - M - 40), fill=PAPER_RULE if theme == "paper" else (40, 46, 56), width=1)
    f = el.sans(18, "Medium")
    d.text((M + 20, H - M - 22), "  ·  ".join(t for t in (line, f"Source: {source}" if source else "") if t), font=f, fill=mu)
    site = "pricechat.live"
    d.text((W - M - f.getlength(site), H - M - 22), site, font=el.sans(18, "SemiBold"), fill=mu)
    return el._jpeg(img), {"format": "card", "layout": f"card-{theme}", "theme": theme, "lines": len(lines), "headline_px": font.size, "subject": None, "covered": 0.0}


def zone_for(subject: tuple[float, float, float, float] | None) -> str:
    """Where a headline block fits away from the subject: the widest empty side."""
    if subject is None:
        return "lower"
    x0, y0, x1, y1 = subject
    free = {"left": x0, "right": 1 - x1, "lower": 1 - y1, "upper": y0 - 0.12}
    best = max(free, key=lambda k: free[k] * (1.0 if k in ("left", "right") else 1.7))
    return best


def choose(subject: tuple[float, float, float, float] | None, recent: list[str] | None = None, *, seed: int = 0) -> str:
    """The layout for this picture: overlay only with a clearly empty zone, side when the subject sits to one side, band otherwise; the
    last layouts used are avoided when another one fits."""
    recent = list(recent or [])[-2:]
    fits = ["band"]
    if subject is not None:
        cx = (subject[0] + subject[2]) / 2
        w = subject[2] - subject[0]
        if w <= 0.62 and (cx <= 0.42 or cx >= 0.58):
            fits.append("side")
        z = zone_for(subject)
        room = {"left": subject[0], "right": 1 - subject[2], "lower": 1 - subject[3], "upper": subject[1]}[z]
        if room >= (0.5 if z in ("left", "right") else 0.38):
            fits.append("overlay")
    fresh = [f for f in fits if f not in recent]
    pick = fresh or fits
    return pick[seed % len(pick)] if len(pick) > 1 else pick[0]


def render(photo: Image.Image, *, subject: tuple[float, float, float, float] | None, headline: str, kicker: str, section: str, credit: str,
           when: datetime | None, line: str = "", illustration: str = "", layout: str | None = None, recent: list[str] | None = None,
           seed: int = 0) -> tuple[bytes, dict[str, Any]]:
    layout = layout or choose(subject, recent, seed=seed)
    common = {"headline": headline, "kicker": kicker, "section": section, "credit": credit, "when": when, "line": line, "illustration": illustration}
    if layout == "overlay":
        got = overlay(photo, subject=subject, zone=zone_for(subject), **common)
        if got is not None:
            return got
        layout = "band"
    if layout == "side":
        return side(photo, subject=subject, **common)
    return band(photo, subject=subject, theme="ink" if seed % 3 == 2 else "paper", **common)


__all__ = ["LAYOUTS", "band", "choose", "covered", "crop_to", "overlay", "render", "side", "zone_for"]
