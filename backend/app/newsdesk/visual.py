"""The News Desk's post image (owner's format, 2026-11-09): one cinematic 16:9 picture that carries the story, a small "PRICE CHAT NEWS"
mark, and nothing else. The headline and a discreet source credit go in the post text beneath it, never on the picture.

Two kinds of picture, both real:
- market moves: a chart drawn from our own price data (Hyperliquid candles, or Yahoo bars), lit red for a fall and green for a rise;
- news: a real photograph from Wikimedia Commons, only under a licence that allows reuse (public domain, CC0, CC BY, CC BY-SA), credited
  on the image in small type, graded dark and dramatic. A photo used in the last USED_DAYS is skipped, so pictures don't repeat.
No generated people, no invented scenes, no logos drawn by us.
"""

from __future__ import annotations

import io
import logging
import math
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

logger = logging.getLogger(__name__)

W, H = 1600, 900
ASSETS = Path(__file__).with_name("assets")
TEXT, MUTED = (246, 247, 246), (170, 178, 174)
DOWN, UP = (236, 52, 44), (120, 214, 40)
COMMONS = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "PriceChatNewsDesk/1.0 (https://pricechat.live)"}
OLD = re.compile(r"construction|drawing|engraving|\bmap\b|\bplan\b|diagram|logo|sketch|lithograph|LCCN|postcard|stereograph", re.I)
FREE = re.compile(r"^(cc0|public domain|pd|cc[- ]by(-sa)?( \d(\.\d)?)?)", re.I)


@lru_cache(maxsize=16)
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


def _vignette(img: Image.Image, strength: float = 0.75) -> Image.Image:
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    for i in range(40):
        f = i / 40
        d.ellipse([-W * 0.25 + f * W * 0.35, -H * 0.35 + f * H * 0.4, W * 1.25 - f * W * 0.35, H * 1.35 - f * H * 0.4], fill=int(255 * f))
    mask = mask.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(img, Image.blend(img, dark, strength), mask)


def _grain(img: Image.Image, amount: int = 10) -> Image.Image:
    rnd = random.Random(7)
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
    d.text((W - 36, H - 26), text, font=_inter(15, "Medium"), fill=(200, 205, 202), anchor="rs")


# --- market moves: a real chart ------------------------------------------------------------------------------------


def chart(points: list[tuple[int, float]], *, name: str, ticker: str, pct: float, window: str = "24H", source: str = "Hyperliquid") -> bytes:
    """points: (unix seconds, price), oldest first, from our own data. The picture shows only what the data says: the line, the move."""
    if len(points) < 10:
        raise ValueError("not enough price points for a chart")
    falling = pct < 0
    accent = DOWN if falling else UP
    img = Image.new("RGB", (W, H), (6, 7, 8))
    # dramatic light: a coloured glow behind where the move happens
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    cx, cy = (W * 0.78, H * 0.78) if falling else (W * 0.78, H * 0.22)
    for r in range(900, 0, -10):
        gd.ellipse([cx - r * 1.4, cy - r, cx + r * 1.4, cy + r], fill=int(70 * (1 - r / 900) ** 1.6))
    img.paste(Image.new("RGB", (W, H), accent), (0, 0), glow)
    # the ticker, huge and faint, behind everything
    wm = Image.new("L", (W, H), 0)
    ImageDraw.Draw(wm).text((W // 2, H // 2 + 30), ticker.upper(), font=_anton(560), fill=11, anchor="mm")
    img.paste(Image.new("RGB", (W, H), TEXT), (0, 0), wm)
    # faint grid
    d = ImageDraw.Draw(img)
    for i in range(1, 6):
        y = int(150 + i * (H - 260) / 6)
        d.line([(0, y), (W, y)], fill=(26, 28, 30), width=1)
    # the line
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or hi * 0.01
    left, right, top, bottom = 0, W - 110, 200, H - 110
    def at(i: int) -> tuple[float, float]:
        x = left + (xs[i] - xs[0]) / ((xs[-1] - xs[0]) or 1) * (right - left)
        y = bottom - (ys[i] - lo) / span * (bottom - top)
        return x, y
    line = [at(i) for i in range(len(points))]
    # area under the line, fading down
    area = Image.new("L", (W, H), 0)
    ImageDraw.Draw(area).polygon([*line, (line[-1][0], H), (line[0][0], H)], fill=255)
    fade = Image.linear_gradient("L").resize((W, H))
    fade = ImageOps.invert(fade).point(lambda v: int(v * 0.42))
    img.paste(Image.new("RGB", (W, H), accent), (0, 0), ImageChops.multiply(area, fade))
    # glow under the line, then the line itself
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
    # the move, large, where the eye lands; the asset and the window small beneath it
    stat = f"{'+' if pct >= 0 else '-'}{abs(pct):.1f}%"
    # the stat goes in the empty corner: top right when the line fell from the left, top left when it rose to the right
    first_half_high = sum(p[1] for p in points[: len(points) // 2]) >= sum(p[1] for p in points[len(points) // 2:])
    sx, anchor = (W - 70, "r") if first_half_high else (70, "l")
    sy = 255
    d.text((sx, sy), stat, font=_anton(210), fill=TEXT, anchor=anchor + "m")
    d.text((sx + (6 if anchor == "l" else -6), sy + 128), f"{name.upper()}  ·  {window}", font=_inter(30, "Bold"), fill=accent, anchor=anchor + "m")
    _brand(img)
    _credit(img, f"Data: {source}")
    return _png(img)


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


def photo(img: Image.Image, credit: str, *, tone: str = "neutral") -> bytes:
    """A real photograph, cropped to 16:9 and graded like a front page: deep shadows, cooler mids, a vignette. tone "risk" adds a red cast."""
    img = ImageOps.fit(img, (W, H), method=Image.LANCZOS, centering=(0.5, 0.45))
    img = ImageEnhance.Color(img).enhance(0.62)
    img = ImageEnhance.Contrast(img).enhance(1.38)
    img = ImageEnhance.Brightness(img).enhance(0.62)
    # split tone: cool shadows, warm (red, for a risk story) highlights
    lum = img.convert("L").point(lambda v: 255 if v > 110 else 0).filter(ImageFilter.GaussianBlur(30))
    hi = Image.blend(img, Image.new("RGB", (W, H), (190, 40, 30) if tone == "risk" else (210, 150, 90)), 0.18)
    lo = Image.blend(img, Image.new("RGB", (W, H), (10, 30, 48)), 0.28)
    img = Image.composite(hi, lo, lum)
    # darker top for the mark, darker bottom for the credit
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(H):
        t = max(0.0, 1 - y / 220) * 150 + max(0.0, (y - (H - 260)) / 260) * 170
        sd.line([(0, y), (W, y)], fill=int(min(230, t)))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shade)
    img = _vignette(img, 0.6)
    img = _grain(img, 6)
    _brand(img)
    _credit(img, credit)
    return _png(img)


# --- the words beneath the picture ----------------------------------------------------------------------------------


def move_headline(name: str, pct: float, window: str = "24 hours") -> str:
    """A bold headline that says exactly what the data says. The verb only grows with the move."""
    a = abs(pct)
    if pct < 0:
        verb = "CRASHES" if a >= 12 else "GETS HAMMERED" if a >= 5 else "SLIDES" if a >= 3 else "SLIPS"
        way = "DOWN"
    else:
        verb = "SOARS" if a >= 12 else "RIPS HIGHER" if a >= 5 else "JUMPS" if a >= 3 else "EDGES HIGHER"
        way = "UP"
    return f"{name.upper()} {verb} — {way} {a:.1f}% IN {window.upper()}"


def caption_html(headline: str, source: str) -> str:
    from html import escape

    return f"<b>{escape(headline)}</b>\n\n<i>Source: {escape(source)}</i>"


def x_text(headline: str, source: str) -> str:
    return f"{headline}\n\nSource: {source}"


# --- choosing the picture for an event --------------------------------------------------------------------------------

USED_DAYS = 14
# subject words -> what to photograph (searched in order; a photo counts only if its title names the subject)
SUBJECTS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(fed|federal reserve|powell|fomc|rate (?:cut|hike)s?)\b", re.I), "Marriner S. Eccles Federal Reserve Board Building", "eccles|federal reserve"),
    (re.compile(r"\b(treasury|ofac|fincen|sanction\w*)\b", re.I), "United States Treasury Building", "treasury"),
    (re.compile(r"\b(sec|securities and exchange commission|gensler|atkins)\b", re.I), "Securities and Exchange Commission headquarters", "securities|sec "),
    (re.compile(r"\b(cftc)\b", re.I), "Commodity Futures Trading Commission", "commodity futures"),
    (re.compile(r"\b(white house|trump|president)\b", re.I), "White House north facade", "white house"),
    (re.compile(r"\b(nyse|wall street|s&p|dow|nasdaq|stocks?|etfs?)\b", re.I), "New York Stock Exchange facade", "stock exchange|wall street"),
    (re.compile(r"\b(oil|opec|crude|brent|refiner\w*)\b", re.I), "oil refinery night", "refinery|oil"),
    (re.compile(r"\b(gold|bullion)\b", re.I), "gold bars", "gold"),
    (re.compile(r"\b(silver)\b", re.I), "silver bars", "silver"),
    (re.compile(r"\b(hack\w*|exploit\w*|drain\w*|breach\w*|stolen)\b", re.I), "data center server room", "server|data cent"),
    (re.compile(r"\b(mining|miners?|hashrate)\b", re.I), "cryptocurrency mining farm", "mining"),
    (re.compile(r"\b(china|beijing|chinese)\b", re.I), "Beijing skyline night", "beijing"),
    (re.compile(r"\b(court|judge|lawsuit|indict\w*|charged|guilty|sentenced)\b", re.I), "United States courthouse", "court"),
]
COMPANIES = re.compile(r"\b(nvidia|apple|tesla|microsoft|google|alphabet|amazon|meta|blackrock|coinbase|binance|tether|circle|microstrategy|strategy|"
                       r"jpmorgan|goldman sachs|robinhood|kraken|okx|bybit|mexc|bitget)\b", re.I)


def subject_queries(headline: str) -> list[tuple[str, str]]:
    """(search, word the photo's title must contain), most specific first: a named company's headquarters, then the subject."""
    out: list[tuple[str, str]] = []
    for name in dict.fromkeys(m.group(1).lower() for m in COMPANIES.finditer(headline)):
        out.append((f"{name} headquarters", name))
        out.append((f"{name} office building", name))
    out += [(q, must) for rx, q, must in SUBJECTS if rx.search(headline)]
    return out


def post_headline(ev: Any) -> str:
    """ONE short bold line: the event's own verified headline, written short ($16 billion -> $16B), no labels, no emoji."""
    f = ev.facts or {}
    if f.get("visual_headline"):
        return str(f["visual_headline"])
    h = re.sub(r"^[^\w$]+", "", ev.headline or ev.title or "").strip()
    h = re.sub(r"^(BREAKING|JUST IN|ALERT|UPDATE)\s*[:\-–—]\s*", "", h, flags=re.I)
    h = re.sub(r"\$\s?(\d[\d.,]*)\s*(billion|bn)\b", r"$\1B", h, flags=re.I)
    h = re.sub(r"\$\s?(\d[\d.,]*)\s*(million|mn)\b", r"$\1M", h, flags=re.I)
    return re.sub(r"\s+", " ", h).upper()[:120]


def source_name(ev: Any) -> str:
    names = [s.get("name") for s in (ev.sources or []) if s.get("name")]
    return names[0] if names else "Price Chat"


async def candles_for(coin: str, market: Any = None) -> list[tuple[int, float]]:
    import asyncio

    if market is None:
        from app.tradingos.market import MarketData

        market = MarketData()
    rows = await asyncio.to_thread(market.candles, coin, "15m", 97)
    return [(int(c.t) // 1000, float(c.c)) for c in rows]


async def for_event(ev: Any, *, used: set[str] | None = None, transport: Any = None, market: Any = None) -> tuple[bytes, dict[str, Any]]:
    """The picture for an event, and what it was (stored on the event, so X and Telegram carry the same one).
    Market moves: our chart. News: a real photo of the subject, not used in the last USED_DAYS. Nothing fitting: the branded card."""
    f = ev.facts or {}
    used = used or set()
    chosen = f.get("visual") or {}
    if f.get("points") or chosen.get("kind") == "chart" or ev.kind in ("recap", "market"):
        points = [tuple(p) for p in f.get("points") or []]
        coin = str(f.get("coin") or (f.get("tickers") or [""])[0] or "").lstrip("$").upper()
        if len(points) < 10 and coin:
            try:
                points = await candles_for(coin, market)
            except Exception as e:  # noqa: BLE001
                logger.info("newsdesk visual: no candles for %s (%s)", coin, type(e).__name__)
        pct = f.get("pct")
        if pct is None and len(points) >= 2:
            pct = (points[-1][1] - points[0][1]) / points[0][1] * 100
        if len(points) >= 10 and pct is not None:
            name = str(f.get("name") or coin)
            return chart(points, name=name, ticker=coin or name[:4], pct=float(pct), source=str(f.get("data_source") or "Hyperliquid")), {"kind": "chart", "coin": coin}
    async with httpx.AsyncClient(transport=transport) as client:
        if chosen.get("kind") == "photo" and chosen.get("url"):
            img = await fetch_image(client, chosen["url"])
            return photo(img, chosen["credit"], tone=chosen.get("tone", "neutral")), chosen
        tone = "risk" if ev.topic in ("hack", "scam", "failure", "controversy") or f.get("risk") else "neutral"
        for query, must in subject_queries(f"{ev.headline} {ev.title}"):
            try:
                found = await find_photos(client, query)
            except Exception as e:  # noqa: BLE001
                logger.info("newsdesk visual: photo search failed (%s)", type(e).__name__)
                break
            for p in found:
                if p.url in used or not re.search(must, p.title, re.I):
                    continue
                try:
                    img = await fetch_image(client, p.url)
                except Exception:  # noqa: BLE001
                    continue
                if not is_colour(img):
                    continue
                meta = {"kind": "photo", "url": p.url, "credit": p.credit, "page": p.page, "tone": tone, "title": p.title}
                return photo(img, p.credit, tone=tone), meta
    from app.newsdesk import card, events

    b = events.brief_of(ev, __import__("app.db.base", fromlist=["utcnow"]).utcnow())
    return card.render(ev.headline, b, [source_name(ev)], ev.released_at or ev.first_seen), {"kind": "card"}
