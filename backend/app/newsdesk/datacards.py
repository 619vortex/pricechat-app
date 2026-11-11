"""Viral Engine 2.0 graphics: the data cards (line battles, then-vs-now, anomaly charts, ranking bars), the story timeline and the
PRICE CHAT BREAKING card. Same system as app/newsdesk/design (Source Serif headlines, Inter labels, the kiwi tag, paper or ink).

Every mark is drawn to scale from the data it shows: a line is the series itself, a bar's length is its percent on one shared axis, the
zero line is where zero is. Nothing is illustrative.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PIL import Image, ImageDraw

from app.newsdesk import design
from app.newsdesk import editorial_layouts as el

W, H, M = design.W, design.H, design.M
UP, DOWN = (22, 140, 92), (206, 52, 52)
SERIES = ((226, 140, 20), (40, 100, 210))  # first, second line
MAD = (214, 40, 40)
KICKER = {"anomaly": "THE NUMBERS", "battle": "ASSET BATTLE", "then_now": "THEN VS NOW", "ranking": "THE WEEK IN MARKETS", "rotation": "SECTOR ROTATION"}


def _frame(theme: str, kicker: str, accent: tuple[int, int, int], headline: str, when: datetime | None) -> tuple[Image.Image, ImageDraw.ImageDraw, int, tuple]:
    bg, fg, mu = (design.PAPER, design.PAPER_TEXT, design.PAPER_MUTED) if theme == "paper" else (design.INK, design.INK_TEXT, design.INK_MUTED)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    design.brand_tag(img, (M, M - 8), dark_bg=theme != "paper")
    design.kicker_row(d, (M, M + 58), kicker, accent, when, mu, W - M)
    font, lines = design.headline_lines(headline, W - 2 * M, (60, 54, 48, 44), 2)
    y = M + 100
    for ln in lines:
        d.text((M, y), ln, font=font, fill=fg)
        y += int(font.size * 1.15)
    return img, d, y + 18, (bg, fg, mu)


def _footer(d: ImageDraw.ImageDraw, colours: tuple, note: str) -> None:
    _, _, mu = colours
    f = el.sans(18, "Medium")
    d.line((M, H - M - 30, W - M, H - M - 30), fill=design.PAPER_RULE if colours[0] == design.PAPER else (40, 46, 56), width=1)
    d.text((M, H - M - 14), note, font=f, fill=mu)
    site = "pricechat.live"
    d.text((W - M - f.getlength(site), H - M - 14), site, font=el.sans(18, "SemiBold"), fill=mu)


def _lines(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], series: list[tuple[str, list[list[float]]]], colours: tuple, *, rebase: bool) -> None:
    """One or two lines on shared axes. rebase: each line as percent change from its first point (a battle compares returns, not prices)."""
    x0, y0, x1, y1 = box
    _, fg, mu = colours
    drawn = []
    for name, pts in series:
        if len(pts) < 2:
            continue
        base = pts[0][1]
        drawn.append((name, [(t, (c / base - 1) * 100 if rebase else c) for t, c in pts]))
    if not drawn:
        return
    ts = [t for _, p in drawn for t, _ in p]
    vs = [v for _, p in drawn for _, v in p]
    t0, t1, lo, hi = min(ts), max(ts), min(vs), max(vs)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    fx = lambda t: x0 + (t - t0) / ((t1 - t0) or 1) * (x1 - x0 - 150)  # noqa: E731 - room on the right for the end labels
    fy = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)  # noqa: E731
    small = el.sans(17, "Medium")
    for tick in el.nice_ticks(lo, hi, False, 4):
        y = fy(tick)
        d.line((x0, y, x1 - 150, y), fill=(200, 196, 188) if colours[0] == design.PAPER else (38, 44, 54), width=1)
        label = f"{tick:+.0f}%" if rebase else el._fmt_tick(tick)
        d.text((x1 - 140, y - 10), label, font=small, fill=mu)
    if rebase and lo < 0 < hi:
        d.line((x0, fy(0), x1 - 150, fy(0)), fill=mu, width=2)
    big = el.sans(24, "Bold")
    for i, (name, pts) in enumerate(drawn):
        col = SERIES[i % 2]
        d.line([(fx(t), fy(v)) for t, v in pts], fill=col, width=5, joint="curve")
        ex, ey = fx(pts[-1][0]), fy(pts[-1][1])
        d.ellipse((ex - 7, ey - 7, ex + 7, ey + 7), fill=col)
        if len(drawn) > 1:
            d.text((x0 + i * 330, y0 - 44), f"● {name[:1].upper() + name[1:]}", font=big, fill=col)
        else:  # one line: its first and last value, printed where they are
            sx, sy = fx(pts[0][0]), fy(pts[0][1])
            d.ellipse((sx - 6, sy - 6, sx + 6, sy + 6), fill=mu)
            first_label, last_label = el._fmt_price(pts[0][1]), el._fmt_price(pts[-1][1])
            d.text((sx + 12, sy - 34 if sy > y0 + 40 else sy + 10), first_label, font=big, fill=mu)
            d.text((ex - big.getlength(last_label) - 14, ey + 12 if ey < y1 - 40 else ey - 40), last_label, font=big, fill=fg)
    first, last = datetime.fromtimestamp(t0, tz=timezone.utc), datetime.fromtimestamp(t1, tz=timezone.utc)
    d.text((x0, y1 + 10), f"{first.day} {first:%b %Y}", font=small, fill=mu)
    end = f"{last.day} {last:%b %Y}"
    d.text((x1 - 150 - small.getlength(end), y1 + 10), end, font=small, fill=mu)


def _bars(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], bars: list[list[Any]], colours: tuple) -> None:
    """Horizontal bars on one axis through zero: length is the percent."""
    x0, y0, x1, y1 = box
    _, fg, mu = colours
    rows = bars[:10]
    lo, hi = min(0.0, *[float(b[1]) for b in rows]), max(0.0, *[float(b[1]) for b in rows])
    label_w = 300
    span = (x1 - x0 - label_w - 120)
    fx = lambda v: x0 + label_w + (v - lo) / ((hi - lo) or 1) * span  # noqa: E731
    step = min(56, (y1 - y0) // max(1, len(rows)))
    f, fv = el.sans(24, "SemiBold"), el.sans(22, "Bold")
    for i, (name, v) in enumerate(rows):
        y = y0 + i * step
        v = float(v)
        d.text((x0, y + 4), str(name), font=f, fill=fg)
        a, b = sorted((fx(0), fx(v)))
        d.rectangle((a, y + 6, max(b, a + 2), y + step - 14), fill=UP if v >= 0 else DOWN)
        txt = f"{v:+.1f}%".replace("-", "−")
        d.text((b + 12 if v >= 0 else a - 12 - fv.getlength(txt), y + 6), txt, font=fv, fill=UP if v >= 0 else DOWN)
    d.line((fx(0), y0, fx(0), y0 + len(rows) * step - 8), fill=mu, width=2)


def data_card(post: dict[str, Any], *, when: datetime | None, mad: bool = False, theme: str = "paper") -> tuple[bytes, dict[str, Any]]:
    """A data post's picture. mad: the daily MARKETS GONE MAD kicker in red."""
    kind = post.get("kind", "")
    kicker = "MARKETS GONE MAD" if mad else KICKER.get(kind, "THE NUMBERS")
    accent = MAD if mad else design.ACCENT["markets"]
    img, d, top, colours = _frame(theme, kicker, accent, str(post.get("headline") or ""), when)
    box = (M, top + 60, W - M, H - M - 70)
    if post.get("bars"):
        _bars(d, (M, top + 10, W - M, H - M - 50), post["bars"], colours)
        shape = "bars"
    else:
        from app.newsdesk import datapost

        series = [(datapost.NAME.get(s, s), pts) for s, pts in (post.get("series") or {}).items()]
        _lines(d, box, series, colours, rebase=kind == "battle")
        shape = "lines"
    _footer(d, colours, "Source: Yahoo Finance daily closes · measured by Price Chat")
    return el._jpeg(img), {"kind": "data", "format": f"data-{shape}", "layout": f"data-{kind}", "design": "data", "theme": theme, "mad": mad}


def timeline_card(*, headline: str, entries: list[tuple[datetime, str]], when: datetime | None, theme: str = "ink") -> tuple[bytes, dict[str, Any]]:
    """A story's evolution: each post we made about it, oldest first, the newest highlighted."""
    img, d, top, colours = _frame(theme, "DEVELOPING STORY", design.ACCENT["developing"], headline, when)
    _, fg, mu = colours
    rows = entries[-5:]
    x = M + 30
    step = min(110, (H - M - 80 - top) // max(1, len(rows)))
    d.line((x, top + 20, x, top + 20 + step * (len(rows) - 1)), fill=mu, width=3)
    f, ft = el.sans(20, "SemiBold"), el.serif(30)
    for i, (t, text) in enumerate(rows):
        y = top + 20 + i * step
        last = i == len(rows) - 1
        col = design.ACCENT["developing"] if last else mu
        d.ellipse((x - 11, y - 11, x + 11, y + 11), fill=col)
        d.text((x + 34, y - 30), f"{t.day} {t:%b} · {t:%H:%M} UTC" + ("  ·  NEW" if last else ""), font=f, fill=col)
        lines = el.wrap(text, ft, W - x - M - 40)[:1]
        d.text((x + 34, y - 4), lines[0] if lines else "", font=ft, fill=fg if last else mu)
    _footer(d, colours, "Each step was a post on this channel, from the outlets' reporting")
    return el._jpeg(img), {"kind": "timeline", "format": "timeline", "layout": "timeline", "design": "timeline", "theme": theme}


__all__ = ["data_card", "timeline_card"]
