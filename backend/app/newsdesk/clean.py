"""The clean post style (owner, 2026-11-12, after a Watcher Guru screenshot: "stop making every post look like a corporate news poster").

The picture carries no headline, no logo, no kicker: the image speaks, the caption tells.

Pictures (`picture`), chosen per story, every pixel real or labelled:
- collage  a licensed photo of the story's subject (the person when a person is the subject, else the company or place) on the left and
           the named asset's real price candles on the right (Yahoo Finance, the last ~24 hours), like the reference. Only when the
           story names an asset we can chart and a photo of its subject exists.
- photo    the licensed photo alone, full width.
- chart    candles alone: a market move (the desk's own verified series, checked as before) or a story about an asset with no photo.
- data     a data post's comparison card (app/newsdesk/datacards), as before.
- ai       the GPU's illustration when nothing real fits, with a small "AI illustration" tag (never passed off as a photo).
Scandal, crime and allegation stories never show a person (app/newsdesk/photos.is_sensitive): the company or place instead. A licensed
photo keeps a small credit on the image (its licence requires attribution); a chart says where its prices come from, in small type.

Words (`telegram_caption`, `x_caption`): "BREAKING:", "JUST IN:", "ALERT:" or "UPDATE:" only when the desk's own rules earned that label
(app/newsdesk/brief), then the verified headline as a sentence with cashtags ($NVDA), and at most two more sentences with the figures and
what it means for markets. Clickable source names in one short line, then the community: @thepricechat. The words are the same verified
copy as before (network.write: every figure from a source, names from the sources); the cashtags are added after verification and
only for assets the sentence itself names.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timedelta, timezone
from html import escape as _html_escape
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

STYLE = "clean"
HANDLE = "@thepricechat"
HANDLE_URL = "https://t.me/thepricechat"
W, H = 1600, 900  # a single picture
CW, CH = 1600, 1000  # a collage: two 795 x 1000 panels, a 10 px seam
PANEL = (795, 1000)
GAP = 10
BG = (0, 0, 0)
UP, DOWN = (38, 166, 91), (234, 57, 67)
GRID, AXIS = (28, 28, 28), (150, 150, 150)
CHART_BARS = 96  # 15-minute candles: the last 24 hours
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
UA = {"User-Agent": "Mozilla/5.0 (PriceChatNewsDesk/1.0; +https://pricechat.live)"}
MAX_SENTENCES = 3
TG_LIMIT = 1024
X_LIMIT = 280

PREFIX = {"🚨 BREAKING": "BREAKING:", "🔴 ALERT": "ALERT:", "⚡ JUST IN": "JUST IN:", "🔄 UPDATE": "UPDATE:"}

# (the name as a sentence says it, the cashtag): only these, only when the sentence names them. "Strategy" alone is too common a word:
# it counts only next to Saylor or as MicroStrategy.
CASHTAGS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bMicroStrategy\b"), "$MSTR"), (re.compile(r"\bNvidia\b"), "$NVDA"), (re.compile(r"\bTesla\b"), "$TSLA"),
    (re.compile(r"\bApple\b"), "$AAPL"), (re.compile(r"\bCoinbase\b"), "$COIN"), (re.compile(r"\bMeta\b(?! Platforms)"), "$META"),
    (re.compile(r"\bAmazon\b"), "$AMZN"), (re.compile(r"\bMicrosoft\b"), "$MSFT"), (re.compile(r"\b(?:Alphabet|Google)\b"), "$GOOGL"),
    # "Bitcoin" inside a company's name (Mercado Bitcoin, Bitcoin Depot) is not the asset: 2026-11-12, an exchange hack story got a $BTC tag
    (re.compile(r"(?<!Mercado )(?<!Strike )\bBitcoin\b(?! (?:Depot|Suisse|Magazine|Core|Policy|Treasury Corp|Well|Group|Inc))(?!\.com)", re.I), "$BTC"),
    (re.compile(r"\b(?:Ethereum|Ether)\b(?! Foundation)"), "$ETH"), (re.compile(r"\bSolana\b(?! (?:Foundation|Labs))"), "$SOL"),
    (re.compile(r"\bXRP\b"), "$XRP"), (re.compile(r"\bHyperliquid\b"), "$HYPE"), (re.compile(r"\bBinance\b"), "$BNB"),
]
SAYLOR_STRATEGY = re.compile(r"\b(?:Saylor's |Michael Saylor's )?Strategy\b")
# the symbol a cashtag is charted from (Yahoo)
CHARTED = {"$MSTR": "MSTR", "$NVDA": "NVDA", "$TSLA": "TSLA", "$AAPL": "AAPL", "$COIN": "COIN", "$META": "META", "$AMZN": "AMZN", "$MSFT": "MSFT",
           "$GOOGL": "GOOGL", "$BTC": "BTC-USD", "$ETH": "ETH-USD", "$SOL": "SOL-USD", "$XRP": "XRP-USD", "$HYPE": "HYPE32196-USD", "$BNB": "BNB-USD"}
COMMODITY = [(re.compile(r"\b(oil|crude|brent|opec\+?)\b", re.I), "BZ=F", "Brent crude"), (re.compile(r"\bgold\b", re.I), "GC=F", "Gold"),
             (re.compile(r"\bsilver\b", re.I), "SI=F", "Silver"),
             # macro data and the Fed: the broad market, labelled as the S&P 500 (the caption never says the chart moved because of it)
             (re.compile(r"\b(inflation|cpi|consumer prices|jobs report|payrolls|unemployment rate|rate (?:cut|hike)s?|fomc|federal reserve|fed)\b", re.I),
              "^GSPC", "S&P 500")]
# people the news names by surname alone: the full name their photographs are filed under
KNOWN_PEOPLE = {"zelensky": "Volodymyr Zelenskyy", "zelenskyy": "Volodymyr Zelenskyy", "putin": "Vladimir Putin", "trump": "Donald Trump",
                "powell": "Jerome Powell", "saylor": "Michael Saylor", "musk": "Elon Musk", "huang": "Jensen Huang", "lagarde": "Christine Lagarde",
                "bessent": "Scott Bessent", "netanyahu": "Benjamin Netanyahu", "modi": "Narendra Modi", "xi": "Xi Jinping", "erdogan": "Recep Tayyip Erdoğan",
                "macron": "Emmanuel Macron", "starmer": "Keir Starmer", "lula": "Luiz Inácio Lula da Silva", "dimon": "Jamie Dimon", "fink": "Larry Fink",
                "armstrong": "Brian Armstrong", "buterin": "Vitalik Buterin", "zuckerberg": "Mark Zuckerberg", "altman": "Sam Altman"}


# --------------------------------------------------------------------------------------------------------------- words
def _esc(text: str, quote: bool = False) -> str:
    """Telegram HTML: text never escapes quotes (some clients show &#x27; as is); link URLs do."""
    return _html_escape(text, quote=quote)


def prefix_for(ev: Any) -> str:
    f = getattr(ev, "facts", None) or {}
    kicker = str(f.get("kicker") or "")
    if kicker == "MARKETS GONE MAD":
        return "MARKETS GONE MAD:"
    if kicker.startswith("UPDATE") or getattr(ev, "kind", "") == "update":
        return "UPDATE:"
    return PREFIX.get(str(getattr(ev, "label", "") or ""), "")


def cashtag(text: str) -> tuple[str, list[str]]:
    """The text with a cashtag after the first mention of each listed asset it names (XRP becomes $XRP), and the cashtags used."""
    used: list[str] = []
    out = text
    if "$MSTR" not in out and re.search(r"\bSaylor\b", out):
        m = SAYLOR_STRATEGY.search(out)
        if m:
            out = out[:m.end()] + " $MSTR" + out[m.end():]
            used.append("$MSTR")
    for rx, tag in CASHTAGS:
        if tag in out:
            if tag not in used:
                used.append(tag)
            continue
        # the first mention that isn't a possessive ("Binance's $6.3B hack" can't take a tag inside it); none: no tag
        m = next((x for x in rx.finditer(out) if tag == "$XRP" or not re.match(r"[’']s\b", out[x.end():])), None)
        if not m:
            continue
        if tag == "$XRP":
            out = out[:m.start()] + "$XRP" + out[m.end():]
        else:
            out = out[:m.end()] + f" {tag}" + out[m.end():]
        used.append(tag)
    return out, used


# always capitalised, whatever the reporting's sentences happen to contain (2026-11-12: "affair with chinese executive")
PROPER = {w.lower(): w for w in (
    "American Chinese Russian Ukrainian Iranian Israeli Saudi Japanese Korean European British French German Indian Brazilian Canadian Mexican "
    "Turkish Swiss Arab Emirati Qatari Taiwanese Dutch Italian Spanish Australian Palestinian Syrian Lebanese Venezuelan Argentine Polish "
    "Swedish Norwegian Danish Finnish Irish Scottish Belgian Austrian Greek Egyptian Nigerian African Asian Latin Western Eastern Gulf "
    "Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July August September October "
    "November December Christmas Congress Senate Fed Treasury").split()}


def _proper(text: str) -> str:
    return re.sub(r"\b[a-z][a-z]+\b", lambda m: PROPER.get(m.group(0), m.group(0)) if m.group(0) not in ("may", "march") else m.group(0), text)


def _sentence(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if t and t[-1] not in ".!?…\"'”":
        t += "."
    return t


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$%.]+", t.lower()) if len(w) > 2}


def sentences_of(c: Any, case_source: str = "") -> list[str]:
    """The headline as a sentence, then up to two more: the context's sentences that add something (not a re-telling of the headline),
    then what it means for markets. An outlet's Title Case Headline is set in sentence case, a word keeping its capital only where the
    reporting's own sentences (`case_source`: the articles' summaries and the context) capitalise it mid-sentence: names stay names."""
    from app.newsdesk.network import _sentences, sentence_case

    cased = sentence_case(c.headline, f"{c.context} {case_source}")
    head = _sentence(_proper(cased) if cased != c.headline else cased)
    out = [head]
    seen = _words(head)
    for s in [*_sentences(c.context), *([c.stakes] if c.stakes else [])]:
        s = _sentence(s)
        w = _words(s)
        if not w or len(w & seen) / len(w) > 0.6:
            continue
        out.append(s)
        seen |= w
        if len(out) >= MAX_SENTENCES:
            break
    return out


def _source_links(links: list[Any], outlets: list[str] | None) -> tuple[str, str]:
    """(HTML, plain): the sources in one short line of clickable names."""
    srcs = [x for x in links if getattr(x, "kind", "article") != "pricechat"][:3]
    if srcs:
        html = " · ".join(f'<a href="{_esc(x.url, quote=True)}">{_esc(x.name)}</a>' for x in srcs)
        return f"🔗 {html}", "Source: " + " · ".join(x.name for x in srcs)
    if outlets:
        return "🔗 " + _esc(" · ".join(outlets[:3])), "Source: " + " · ".join(outlets[:3])
    return "", ""


def telegram_caption(c: Any, links: list[Any], *, prefix: str = "", outlets: list[str] | None = None, case_source: str = "") -> tuple[str, str, str]:
    """(HTML, plain with the signature, plain without it: what the checks read). Picture first; then the story in 1-3 sentences, the
    sources on one line, and @thepricechat."""
    sents = sentences_of(c, case_source)
    while True:
        first, tags = cashtag(sents[0])
        rest = [cashtag(s)[0] for s in sents[1:]]
        lead = f"<b>{_esc(prefix)}</b> {_esc(first)}" if prefix else _esc(first)
        body_html = [lead + (" " + _esc(" ".join(rest)) if rest else "")]
        body_plain = [(f"{prefix} {first}" if prefix else first) + (" " + " ".join(rest) if rest else "")]
        src_html, src_plain = _source_links(links, outlets)
        if src_html:
            body_html.append(src_html)
            body_plain.append(src_plain)
        html = "\n\n".join([*body_html, f'<a href="{HANDLE_URL}">{HANDLE}</a>'])
        plain_body = "\n\n".join(body_plain)
        if len(html) <= TG_LIMIT or len(sents) == 1:
            return html, f"{plain_body}\n\n{HANDLE}", plain_body
        sents = sents[:-1]


def x_caption(c: Any, links: list[Any], *, prefix: str = "", with_links: bool = True, outlets: list[str] | None = None, case_source: str = "") -> str:
    """The same story for X: the hook (prefix + the headline with its cashtags), a second sentence when it fits, one source link."""
    from app.newsdesk.network import x_length

    sents = [cashtag(s)[0] for s in sentences_of(c, case_source)]
    head = f"{prefix} {sents[0]}" if prefix else sents[0]
    srcs = [x for x in links if getattr(x, "kind", "article") != "pricechat"]
    link = f"{srcs[0].name}: {srcs[0].url}" if (with_links and srcs) else (f"Via {' · '.join([x.name for x in srcs][:2] or (outlets or [])[:2])}" if (srcs or outlets) else "")
    for extra in (sents[1:2], []):
        text = "\n\n".join(p for p in [" ".join([head, *extra]), link] if p)
        if x_length(text) <= X_LIMIT:
            return text
    return head[:X_LIMIT]


# --------------------------------------------------------------------------------------------------------------- pictures
def _font(size: int, weight: str = "Medium") -> ImageFont.FreeTypeFont:
    from app.newsdesk.visual import _inter

    return _inter(size, weight)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def _small(img: Image.Image, text: str, at: str = "left", box: tuple[int, int, int, int] | None = None) -> None:
    """A credit or a data line in small type on a soft shadow: legible, never the subject."""
    if not text:
        return
    x0, y0, x1, y1 = box or (0, 0, img.width, img.height)
    d = ImageDraw.Draw(img)
    font = _font(17)
    tw = d.textlength(text, font=font)
    x = x0 + 14 if at == "left" else x1 - 14 - tw
    y = y1 - 30
    pad = Image.new("RGBA", (int(tw) + 16, 26), (0, 0, 0, 120))
    img.paste(pad, (int(x) - 8, y - 4), pad)
    d.text((x, y), text, font=font, fill=(225, 225, 225))


def photo_credit(meta: dict[str, Any]) -> str:
    """"Photo: Jane Doe / Wikimedia Commons, CC BY-SA 4.0": what the licence asks for, as briefly as it allows."""
    credit = str(meta.get("credit") or "").strip()
    lic = str(meta.get("licence") or "").strip()
    if lic and lic.lower() not in credit.lower():
        credit = f"{credit}, {lic}" if credit else lic
    return credit if credit.lower().startswith("photo") else f"Photo: {credit}" if credit else ""


def drawable(text: str) -> bool:
    """The bundled font (Inter) draws Latin, Greek and Cyrillic; anything else would come out as boxes."""
    return all(ord(ch) < 0x0530 or 0x1E00 <= ord(ch) < 0x2100 or ch in "–—’‘“”…·•€£¥" for ch in text)


def image_credit(meta: dict[str, Any]) -> str:
    """The credit drawn on the picture: the full one when the font can draw it, else where it's from and its licence (the full credit then
    goes in the caption, `caption_credit`)."""
    full = photo_credit(meta)
    if drawable(full):
        return full
    from app.newsdesk import photos

    p = meta.get("photo") or {}
    key = str(p.get("source") or p.get("provider") or "wikimedia").lower()
    where = photos.SOURCE_NAMES.get(key, key.title())
    lic = str(meta.get("licence") or "").strip()
    return f"Photo: {where}" + (f", {lic}" if lic else "")


def caption_credit(meta: dict[str, Any]) -> tuple[str, str]:
    """(HTML, plain): the full credit for the caption, only when the picture couldn't carry it."""
    full = photo_credit(meta)
    if not full or drawable(full):
        return "", ""
    page = str(meta.get("page") or "")
    html = f'📷 <a href="{_esc(page, quote=True)}">{_esc(full)}</a>' if page.startswith("https://") else f"📷 {_esc(full)}"
    return html, full


def with_credit(html: str, plain: str, meta: dict[str, Any]) -> tuple[str, str]:
    """The caption with the photo's full credit above the signature, when the picture couldn't carry it."""
    c_html, c_plain = caption_credit(meta)
    if not c_html:
        return html, plain
    sig = f'<a href="{HANDLE_URL}">{HANDLE}</a>'
    return html.replace(sig, f"{c_html}\n\n{sig}"), plain.replace(HANDLE, f"{c_plain}\n\n{HANDLE}")


def photo_picture(img: Image.Image, credit: str, size: tuple[int, int] = (W, H)) -> tuple[Image.Image, tuple[float, float, float, float]]:
    from app.newsdesk import photos

    out, box = photos.smart_crop(img, size)
    out = photos.mild_tone(out)
    return out, box


def candles_panel(bars: list[list[float]], size: tuple[int, int], label: str) -> Image.Image:
    """Real candles on black, the price axis on the right, the last price tagged; `label` (the asset, interval and source) in small type."""
    w, h = size
    img = Image.new("RGB", size, BG)
    d = ImageDraw.Draw(img)
    if len(bars) < 2:
        return img
    lo = min(b[3] for b in bars)
    hi = max(b[2] for b in bars)
    pad = (hi - lo) * 0.06 or hi * 0.01
    lo, hi = lo - pad, hi + pad
    axis_w = 112
    top, bottom = 40, h - 70
    x0, x1 = 16, w - axis_w

    def y(p: float) -> float:
        return bottom - (p - lo) / (hi - lo) * (bottom - top)

    font = _font(18)
    for i in range(5):
        p = lo + (hi - lo) * (i + 0.5) / 5
        d.line([(x0, y(p)), (x1, y(p))], fill=GRID)
        d.text((x1 + 10, y(p) - 10), _price(p), font=font, fill=AXIS)
    step = (x1 - x0) / len(bars)
    body = max(2.0, step * 0.62)
    for i, (_, o, hi_, lo_, c) in enumerate(bars):
        cx = x0 + step * (i + 0.5)
        col = UP if c >= o else DOWN
        d.line([(cx, y(hi_)), (cx, y(lo_))], fill=col, width=2)
        t, b = y(max(o, c)), y(min(o, c))
        d.rectangle([cx - body / 2, t, cx + body / 2, max(b, t + 2)], fill=col)
    last = bars[-1][4]
    col = UP if bars[-1][4] >= bars[-1][1] else DOWN
    d.rectangle([x1 + 2, y(last) - 14, w - 4, y(last) + 14], fill=col)
    d.text((x1 + 10, y(last) - 11), _price(last), font=font, fill=(255, 255, 255))
    _small(img, label, "left", (0, 0, w, h))
    return img


def _price(p: float) -> str:
    return f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}" if p >= 1 else f"{p:.4f}"


def collage(photo: Image.Image, credit: str, bars: list[list[float]], label: str) -> tuple[bytes, dict[str, Any]]:
    left, box = photo_picture(photo, credit, PANEL)
    _small(left, credit, "left")
    right = candles_panel(bars, PANEL, label)
    canvas = Image.new("RGB", (CW, CH), BG)
    canvas.paste(left, (0, 0))
    canvas.paste(right, (PANEL[0] + GAP, 0))
    return _png(canvas), {"layout": "collage", "subject": list(box)}


def single_photo(photo: Image.Image, credit: str) -> tuple[bytes, dict[str, Any]]:
    img, box = photo_picture(photo, credit)
    _small(img, credit, "left")
    return _png(img), {"layout": "photo", "subject": list(box)}


def single_chart(bars: list[list[float]], label: str) -> tuple[bytes, dict[str, Any]]:
    return _png(candles_panel(bars, (W, H), label)), {"layout": "chart"}


def ai_picture(background: Image.Image, model: str) -> tuple[bytes, dict[str, Any]]:
    img, _ = photo_picture(background, "")
    _small(img, f"AI illustration · {model}", "right")
    return _png(img), {"layout": "ai"}


def line_bars(points: list[tuple[int, float]]) -> list[list[float]]:
    """A verified close series (a market move) as candles: each candle opens at the previous close."""
    out = []
    for i in range(1, len(points)):
        o, c = float(points[i - 1][1]), float(points[i][1])
        out.append([float(points[i][0]), o, max(o, c), min(o, c), c])
    return out


# --------------------------------------------------------------------------------------------------------------- data
async def fetch_bars(client: httpx.AsyncClient, symbol: str, end: datetime | None = None) -> list[list[float]]:
    """The last CHART_BARS 15-minute candles [t, open, high, low, close] up to `end` from Yahoo Finance (incomplete bars dropped)."""
    cut = end or datetime.now(timezone.utc)
    window = {"period1": int((cut - timedelta(days=3)).timestamp()), "period2": int(cut.timestamp()), "interval": "15m", "includePrePost": "true"}
    r = await client.get(YAHOO.format(symbol=symbol), params=window, headers=UA, timeout=15)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return []
    ts = res[0].get("timestamp") or []
    q = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
    bars = [[float(t), o, h, lo, c] for t, o, h, lo, c in zip(ts, q.get("open", []), q.get("high", []), q.get("low", []), q.get("close", []))
            if None not in (o, h, lo, c) and t + 900 <= cut.timestamp()]  # only candles closed by then
    return bars[-CHART_BARS:]


# a story about a price: a chart beside it shows what the story says. Any other story gets a chart only when the Viral Engine measured that
# asset reacting beyond its noise since the story broke (app/newsdesk/money): a chart next to a story implies the story moved it, and the
# desk never implies a cause nobody showed (2026-11-12: a Samsung wallet story beside a falling $SOL chart)
PRICE_TALK = re.compile(r"\b(rise[sn]?|rose|rall(?:y|ies|ied)|jump(?:s|ed)?|surg(?:e|es|ed)|soar(?:s|ed)?|climb(?:s|ed)?|gain(?:s|ed)?|fall(?:s|en)?|fell|"
                        r"drop(?:s|ped)?|slid(?:e|es)?|sink(?:s)?|sank|plung(?:e|es|ed)|crash(?:es|ed)?|tumbl(?:e|es|ed)|slump(?:s|ed)?|"
                        r"record high|all-time high|selloff|sell-off|shares|stock|price[sd]?)\b|\d+(?:\.\d+)?\s?%", re.I)


def charted_story(ev: Any, symbol: str) -> bool:
    if symbol != "^GSPC" and PRICE_TALK.search(f"{getattr(ev, 'headline', '')} {getattr(ev, 'title', '')}"):
        return True  # (a macro story's "prices rose 0.4%" is consumer prices: the S&P 500 beside it needs a measured reaction)
    measured = ((getattr(ev, "facts", None) or {}).get("viral") or {}).get("reactions") or []
    return any(str(r.get("symbol")) == symbol for r in measured if isinstance(r, dict))


def chart_subject(text: str) -> tuple[str, str, str] | None:
    """(Yahoo symbol, cashtag or name, how the chart is labelled) for the asset a headline names, or None."""
    _, tags = cashtag(text)
    named = [tag for rx, tag in CASHTAGS if rx.search(text)]  # a possessive mention ("Binance's hack") still names the asset
    for tag in dict.fromkeys([*tags, *named]):
        if tag in CHARTED:
            return CHARTED[tag], tag, tag
    for rx, sym, name in COMMODITY:
        if rx.search(text):
            return sym, name, name
    return None


def person_subject(ev: Any) -> str | None:
    """The person the story is about, when it is about one (and isn't a scandal, crime or allegation story)."""
    from app.newsdesk import photos

    text = f"{getattr(ev, 'headline', '')} {getattr(ev, 'title', '')}"
    if photos.is_sensitive(text):
        return None
    names = photos.people_named(text)
    if names:
        return names[0]
    for word in re.findall(r"\b[A-Z][a-z]+\b", text):
        if word.lower() in KNOWN_PEOPLE:
            return KNOWN_PEOPLE[word.lower()]
    return None


# a market move's subject, for the photo beside its chart: (words in the headline, search, word the photo's title must contain)
MARKET_PHOTOS = [(re.compile(r"\b(s&p|dow|nasdaq|wall street|stocks?)\b", re.I), "New York Stock Exchange facade", "stock exchange"),
                 (re.compile(r"\b(oil|crude|brent|wti)\b", re.I), "oil tanker at sea", "tanker"),
                 (re.compile(r"\bgold\b", re.I), "gold bullion bars", "gold")]


async def find_photo(ev: Any, *, used: set[str], transport: Any, panel: bool) -> tuple[Image.Image, dict[str, Any]] | None:
    """A licensed photo of the story's subject: the person first (when a person is the subject and the story isn't sensitive), else the
    company or place (app/newsdesk/visual.subject_queries). Every licence and relevance rule of app/newsdesk/photos applies."""
    from app.newsdesk import photos, visual

    sensitive = visual.is_sensitive(ev)
    plans: list[tuple[list[str], list[str]]] = []
    person = None if sensitive else person_subject(ev)
    if person:
        plans.append(([person, f"{person} portrait"], [person]))
    queries = visual.subject_queries(f"{ev.headline} {ev.title}")
    if getattr(ev, "kind", "") in ("market", "recap"):
        queries += [(q, must) for rx, q, must in MARKET_PHOTOS if rx.search(f"{ev.headline} {ev.title}")]
    for term in dict.fromkeys(must for _, must in queries):
        plans.append(([q for q, must in queries if must == term], [term]))
    async with httpx.AsyncClient(transport=transport) as client:
        for qs, terms in plans:
            try:
                found = await photos.discover(client, qs, terms, used=used, sensitive=sensitive, panel=panel)
            except Exception as e:  # noqa: BLE001
                logger.info("clean: photo discovery failed (%s)", type(e).__name__)
                continue
            for p in found[:3]:
                img = await photos.fetch(client, p, min_width=photos.PANEL_MIN_WIDTH if panel else photos.MIN_WIDTH)
                if img is None:
                    continue
                meta = p.to_meta() if hasattr(p, "to_meta") else {"url": p.url}
                return img, {"url": p.url, "page": meta.get("page", ""), "title": meta.get("title", ""), "credit": p.credit,
                             "licence": meta.get("licence", "") or getattr(p, "licence", ""), "photo": meta, "person": terms[0] if terms == [person] else ""}
    return None


async def picture(ev: Any, *, used: set[str] | None = None, transport: Any = None, market: Any = None, now: datetime | None = None,
                  db: Any = None) -> tuple[bytes, dict[str, Any]]:
    """The clean picture for an event and what it was made of (stored on the event: X redraws exactly the same from it)."""
    from app.newsdesk import visual

    now = now or datetime.now(timezone.utc)
    f = ev.facts or {}
    used = used or set()
    base = {"style": STYLE}
    if getattr(ev, "kind", "") == "data" and f.get("data"):  # a comparison graphic: the data card
        from app.newsdesk import datacards

        when = getattr(ev, "released_at", None) or now
        img, meta = datacards.data_card(f["data"], when=when, mad=bool(f.get("kicker")))
        return img, {**meta, **base, "kind": "data", "layout": "data"}
    if getattr(ev, "kind", "") in ("market", "recap"):  # a measured move: the desk's own checked series, drawn clean
        chosen = visual.plan(ev, [])
        checked = await visual._chart_for(ev, chosen, market, now)  # raises NumbersRefused when the numbers don't check out
        if checked is not None:
            pts = [tuple(p) for p in (ev.facts or {}).get("points") or []]
            if len(pts) < 10:
                coin = str(f.get("coin") or (f.get("tickers") or [""])[0] or "").lstrip("$").upper()
                pts = await visual.candles_for(coin, market) if coin else []
            bars = line_bars(pts)[-CHART_BARS:]
            label = f"{f.get('name') or f.get('coin') or ''} · {str(f.get('window') or '24h')} · {f.get('data_source') or 'Hyperliquid'}"
            check = {"number_check": checked[1].get("number_check")}
            got = await find_photo(ev, used=used, transport=transport, panel=True)  # the market's own photo beside its verified chart
            if got is not None:
                photo, pmeta = got
                credit = image_credit(pmeta)
                data, meta = collage(photo, credit, bars, label)
                return data, {**base, **pmeta, **meta, **check, "kind": "photo", "bars": bars, "label": label, "credit_line": credit}
            img, meta = single_chart(bars, label)
            return img, {**base, **meta, **check, "kind": "chart", "bars": bars, "label": label}
    text = f"{ev.headline} {ev.title}"
    subject = chart_subject(text)
    if subject is not None and not charted_story(ev, subject[0]):
        subject = None  # not a story about this asset's price, and no measured reaction: no chart beside it
    bars: list[list[float]] = []
    label = ""
    if subject is not None:
        try:
            async with httpx.AsyncClient(transport=transport) as client:
                bars = await fetch_bars(client, subject[0], now)
        except Exception as e:  # noqa: BLE001 - the photo alone
            logger.info("clean: no candles for %s (%s)", subject[0], type(e).__name__)
            bars = []
        label = f"{subject[2]} · 15m · Yahoo Finance"
    got = await find_photo(ev, used=used, transport=transport, panel=len(bars) >= 20)
    if got is not None:
        img, pmeta = got
        credit = image_credit(pmeta)
        if len(bars) >= 20:
            data, meta = collage(img, credit, bars, label)
            return data, {**base, **pmeta, **meta, "kind": "photo", "bars": bars, "label": label, "symbol": subject[0] if subject else "", "credit_line": credit}
        data, meta = single_photo(img, credit)
        return data, {**base, **pmeta, **meta, "kind": "photo", "credit_line": credit}
    if len(bars) >= 20:
        data, meta = single_chart(bars, label)
        return data, {**base, **meta, "kind": "chart", "bars": bars, "label": label, "symbol": subject[0] if subject else ""}
    if db is not None and getattr(ev, "id", None):
        from app.newsdesk import render_queue

        try:
            bg = render_queue.background_for(db, ev.id)
        except Exception:  # noqa: BLE001
            bg = None
        if bg is not None:
            model = str(bg[1].get("model") or "")
            short = "Qwen-Image" if "qwen" in model.lower() else "FLUX.2 klein" if "klein" in model.lower() else "AI model"
            data, meta = ai_picture(bg[0], short)
            return data, {**base, **meta, "kind": "cinematic", "ai_illustration": True, "model": model, "render_job": bg[1].get("job")}
    # nothing real and no illustration: the desk's own plain card (headline only) rather than no post
    data, meta = await visual.for_event(ev, used=used, transport=transport, market=market, now=now, db=db, style="classic")
    return data, {**meta, "style": "classic"}


async def download(url: str, transport: Any = None) -> Image.Image:
    """The licensed photo again, as stored (X redraws Telegram's picture)."""
    from app.newsdesk import photos

    async with httpx.AsyncClient(transport=transport) as client:
        r = await client.get(url, headers=photos.UA, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")


async def redraw(ev: Any, meta: dict[str, Any], *, transport: Any = None, db: Any = None, now: datetime | None = None) -> tuple[bytes, dict[str, Any]] | None:
    """The same clean picture again from what was stored (X posts what Telegram showed): the same photo, the same candles."""
    layout = meta.get("layout")
    if layout in ("collage", "photo") and meta.get("url"):
        img = await download(meta["url"], transport)
        credit = str(meta.get("credit_line") or image_credit(meta))
        if layout == "collage":
            return collage(img, credit, meta.get("bars") or [], str(meta.get("label") or ""))[0], meta
        return single_photo(img, credit)[0], meta
    if layout == "chart" and meta.get("bars"):
        return single_chart(meta["bars"], str(meta.get("label") or ""))[0], meta
    if layout == "ai" and db is not None and getattr(ev, "id", None):
        from app.newsdesk import render_queue

        bg = render_queue.background_for(db, ev.id)
        if bg is not None:
            return ai_picture(bg[0], "Qwen-Image" if "qwen" in str(meta.get("model", "")).lower() else "FLUX.2 klein")[0], meta
    if layout == "data":
        return None  # the data card is drawn by viral_visual from the event itself
    return None


