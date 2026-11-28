"""Kiwi Passport 2.0 share cards (Pillow, drawn here from the member's public page, never fetched): the link preview (1200x630, X and
Telegram) and the Story (1080x1920). Same identity and renderer as the Predictions cards: near-black ground, the member's frame colour,
Anton display type, Inter text, the canonical kiwi. Only what the member made public is drawn.
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from app.predictions.cards import BG, LINE, MUTED, SOFT, SS, TEXT, _anton, _C, _inter, _kiwi

ACCENT_RGB = {"green": (0, 229, 143), "gold": (245, 197, 66), "ice": (127, 214, 255), "ember": (255, 122, 69), "violet": (185, 140, 255)}
TIER_RGB = {"bronze": (201, 138, 82), "silver": (199, 208, 214), "gold": (245, 197, 66), "mythic": (185, 140, 255)}
FOOT = "Kiwi Passport · community reputation, no money value"


def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _medal(c: _C, cx: float, cy: float, r: float, color: tuple[int, int, int]) -> None:
    c.d.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), fill=color)
    k = _kiwi(int(r * 1.5))
    c.img.paste(k, (int(cx * SS - k.width / 2), int(cy * SS - k.height / 2 + r * 0.06 * SS)), k)


def _badge(c: _C, cx: float, cy: float, r: float, b: dict[str, Any]) -> None:
    """A collectible badge: the kiwi on its family colour, ringed in its tier's metal."""
    fam, ring = _hex(b.get("color") or "#9aa7a0"), TIER_RGB.get(b.get("tier") or "bronze", TIER_RGB["bronze"])
    c.d.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), fill=ring)
    inner = r * 0.84
    c.d.ellipse(((cx - inner) * SS, (cy - inner) * SS, (cx + inner) * SS, (cy + inner) * SS), fill=tuple(int(v * 0.7) for v in fam))
    k = _kiwi(int(inner * 1.25))
    c.img.paste(k, (int(cx * SS - k.width / 2), int(cy * SS - k.height / 2)), k)


def _stats(p: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = [(f"Rank {p['rank']['level']}", p["rank"]["name"])]
    if p.get("community"):
        out.append((f"{p['community']['score']:,}", f"Community · {p['community']['level']}"))
    pr = p.get("predictions")
    if pr:
        out.append((f"{pr['accuracy']:.0%}" if pr.get("accuracy") is not None else f"{pr['calls']}", "Calls right" if pr.get("accuracy") is not None else "Predictions calls"))
    if p.get("streak") and p["streak"]["current"]:
        out.append((str(p["streak"]["current"]), "Day streak"))
    out.append((str(p.get("badge_count", 0)), "Badges"))
    return out[:4]


def _bottom(font: Any, s: str) -> float:
    """How far below its top-left anchor a line of text reaches (Anton's tall caps need the real measure)."""
    return font.getbbox(s, anchor="la")[3] / SS


def _since(p: dict[str, Any]) -> str:
    from datetime import date

    d = date.fromisoformat(p["joined_at"][:10])
    return f"In Price Chat since {d:%b %Y}"


def _rar(b: dict[str, Any]) -> str:
    if b.get("serial") and b.get("limit"):
        return f"No. {b['serial']:03d} of {b['limit']['cap']}"
    return str(b["rarity"]["label"])


def _name(p: dict[str, Any]) -> str:
    return str(p.get("display_name") or p["username"])


def link_card(p: dict[str, Any], site: str = "pricechat.live") -> bytes:
    w, h = 1200, 630
    c = _C(w, h)
    acc = ACCENT_RGB.get(p.get("accent") or "green", ACCENT_RGB["green"])
    c.glow(w * 0.88, h * 0.05, 380, acc, 62)
    c.glow(w * 0.05, h * 1.0, 260, acc, 20)
    pad = 64
    c.text((pad, 58), "KIWI PASSPORT", _inter(18, "Bold"), acc)
    _medal(c, w - pad - 130, 220, 130, acc)
    y = 112
    if p.get("title"):
        c.text((pad, y), str(p["title"]).upper(), _inter(20, "Bold"), acc)
        y += 34
    name = _name(p).upper()
    nf = c.fit(name, w - 2 * pad - 300, 112, 56)
    c.text((pad, y), name, nf, TEXT)
    y += _bottom(nf, name) + 14
    c.text((pad, y), f"@{p['username']}" if p.get("display_name") else _since(p), _inter(24, "SemiBold"), SOFT)
    y += 34
    x = pad
    for b in (p.get("featured") or [])[:3]:
        _badge(c, x + 30, y + 52, 30, b)
        c.text((x + 70, y + 36), str(b["name"]), _inter(17, "Bold"), TEXT)
        c.text((x + 70, y + 60), _rar(b), _inter(14, "Medium"), MUTED)
        x += 70 + max(150, c.tw(str(b["name"]), _inter(17, "Bold"))) + 28
    st = _stats(p)
    sy = h - 150
    colw = (w - 2 * pad) / len(st)
    for i, (v, lab) in enumerate(st):
        bx = pad + i * colw
        if i:
            c.d.line((bx * SS - 20 * SS, sy * SS, bx * SS - 20 * SS, (sy + 62) * SS), fill=LINE, width=2 * SS)
        vf = c.fit(v.upper(), colw - 40, 40, 24)
        c.text((bx, sy), v.upper(), vf, TEXT)
        c.text((bx, sy + _bottom(vf, v.upper()) + 8), lab.upper(), _inter(13, "Bold"), MUTED)
    c.d.line((pad * SS, (h - 64) * SS, (w - pad) * SS, (h - 64) * SS), fill=LINE, width=2 * SS)
    c.text((pad, h - 44), f"{site}/u/{p['username']}", _inter(16, "Bold"), TEXT)
    c.text((w - pad, h - 36), FOOT, _inter(14, "Medium"), MUTED, anchor="rm")
    return c.png()


def story_card(p: dict[str, Any], site: str = "pricechat.live") -> bytes:
    w, h = 1080, 1920
    c = _C(w, h)
    acc = ACCENT_RGB.get(p.get("accent") or "green", ACCENT_RGB["green"])
    c.glow(w * 0.8, h * 0.02, 640, acc, 80)
    c.glow(w * 0.1, h * 0.98, 420, acc, 26)
    pad = 88
    c.text((pad, 120), "KIWI PASSPORT", _inter(30, "Bold"), acc)
    _medal(c, w / 2, 520, 240, acc)
    y = 840
    if p.get("title"):
        c.text((w / 2, y), str(p["title"]).upper(), _inter(34, "Bold"), acc, anchor="ma")
        y += 64
    name = _name(p).upper()
    nf = c.fit(name, w - 2 * pad, 170, 70)
    c.text((w / 2, y), name, nf, TEXT, anchor="ma")
    y += _bottom(nf, name) + 26
    c.text((w / 2, y), f"{p['rank']['name']} · Rank {p['rank']['level']}", _inter(36, "SemiBold"), SOFT, anchor="ma")
    feats = (p.get("featured") or [])[:3]
    if feats:
        gap = (w - 2 * pad) / len(feats)
        for i, b in enumerate(feats):
            cx = pad + gap * (i + 0.5)
            _badge(c, cx, y + 190, 66, b)
            c.text((cx, y + 280), str(b["name"]), _inter(24, "Bold"), TEXT, anchor="ma")
            c.text((cx, y + 314), _rar(b), _inter(20, "Medium"), MUTED, anchor="ma")
    st = _stats(p)
    sy = h - 330
    colw = (w - 2 * pad) / len(st)
    for i, (v, lab) in enumerate(st):
        cx = pad + colw * (i + 0.5)
        vf = c.fit(v.upper(), colw - 24, 64, 30)
        c.text((cx, sy), v.upper(), vf, TEXT, anchor="ma")
        c.text((cx, sy + _bottom(vf, v.upper()) + 12), lab.upper(), _inter(18, "Bold"), MUTED, anchor="ma")
    c.text((w / 2, h - 130), f"{site}/u/{p['username']}", _inter(30, "Bold"), TEXT, anchor="ma")
    c.text((w / 2, h - 82), FOOT, _inter(20, "Medium"), MUTED, anchor="ma")
    return c.png()


_ = (BG, Image, ImageDraw, _anton)  # same palette and fonts as app/predictions/cards.py
