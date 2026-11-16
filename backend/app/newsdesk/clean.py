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
MAX_SENTENCES = 2  # the headline and at most one supporting sentence (owner, 2026-11-16: concise in every style)
TG_LIMIT = 1024
# chart posts (owner, 2026-11-15, after a weekly ranking listed all 13 assets under its own chart): the chart carries the data, so the
# caption is one strong headline and at most one takeaway, never a list of figures, at most three assets, no cashtags on Telegram, and
# the sources with @thepricechat on one discreet last line. Data cards, market moves and recaps are always chart posts; a story is one
# when its picture is a chart or a collage with candles (telegram.compose_visual re-captions it once the picture is known).
CHART_KINDS = ("data", "market", "recap")
CHART_LAYOUTS = ("data", "chart", "collage")
CHART_SENTENCES = 2
MAX_ASSETS = 3
TG_CASHTAGS = 1  # a story on Telegram: one cashtag at most (X: one, checks.one_cashtag); a chart post: none
_FIGURE = re.compile(r"[+−-]?\$?\d[\d,.]*\s?(?:%|[KMBT]\b|bn\b)")
# our own measured move in brackets after an outlet's headline (app/newsdesk/headline: "(BTC -5.4% IN 24H)"): the chart beside it shows it
_PRICE_TAG = re.compile(r"\s*\((?:\$?[A-Z][A-Z0-9.=^-]{0,9}\s)?[+−-]?\d+(?:\.\d+)?%\s+in\s+\d+\s?[HMhm]\)", re.I)
# a feed's own sign-off copied into the summary (post 179: "Read the full story on CryptoSlate Understand the crypto stories that matter")
_BOILER = re.compile(r"\s*(?:Read the full story (?:on|at) .*$|Read more (?:on|at) .*$|Continue reading.*$|The post .{3,120} appeared first on .*$|"
                     r"Understand the crypto stories that matter.*$|Click here to .*$|Subscribe to .*$)", re.I | re.S)


def is_chart_post(ev: Any, layout: str = "") -> bool:
    return getattr(ev, "kind", "") in CHART_KINDS or layout in CHART_LAYOUTS


def _assets(text: str) -> list[str]:
    return [tag for _, tag in established(text)]


def unboiled(text: str) -> str:
    """A sentence without a feed's sign-off ("Read the full story on ...")."""
    return _BOILER.sub("", text or "").strip()


def chart_recaption(pkg: Any, ev: Any, rows: list[Any], *, x_links: bool = True) -> None:
    """A story whose picture turned out to carry its chart (a collage with candles, or candles alone): both captions follow the chart
    rules, since X posts the same picture. The words are the same verified copy; only fewer of them are kept."""
    from app.newsdesk import network

    said = " ".join([str(getattr(ev, "summary", "") or ""), *(str(getattr(r, "summary", "") or "") for r in rows or [])])
    pre, outs = prefix_for(ev), network.outlets_of(ev)
    pkg.telegram_html, pkg.telegram_plain, _ = telegram_caption(pkg.copy, pkg.links, prefix=pre, outlets=outs, case_source=said, chart=True)
    pkg.x_text = x_caption(pkg.copy, pkg.links, prefix=pre, with_links=x_links, outlets=outs, case_source=said, chart=True)


X_LIMIT = 280

JUST_IN_LABEL, BREAKING_LABEL = "⚡ JUST IN", "🚨 BREAKING"
PREFIX = {"🚨 BREAKING": "BREAKING:", "🔴 ALERT": "ALERT:", "⚡ JUST IN": "JUST IN:", "🔄 UPDATE": "UPDATE:"}

# Cashtags, owner 2026-11-13 after "$1.2B of North Korea's stolen Binance $BNB crypto": a ticker only where the sentence itself establishes
# the asset. Two kinds:
# * ASSETS: the name IS the asset (Bitcoin, Ether, Solana, XRP): the name is enough.
# * LISTED: a company or exchange. Its name never implies its token or stock (a Binance hack is not a BNB story, a Coinbase outage is not a
#   COIN story). The tag goes on only when the sentence names the ticker itself (BNB, HYPE, COIN) or the company's shares/stock, or a
#   stock-style move ("Nvidia falls 4%"). "Strategy" counts only as MicroStrategy or next to Saylor, and then under the same rule.
ASSETS: list[tuple[re.Pattern[str], str]] = [
    # "Bitcoin" inside a company's name (Mercado Bitcoin, Bitcoin Depot) is not the asset: 2026-11-12, an exchange hack story got a $BTC tag
    (re.compile(r"(?<!Mercado )(?<!Strike )\bBitcoin\b(?! (?:Depot|Suisse|Magazine|Core|Policy|Treasury Corp|Well|Group|Inc))(?!\.com)", re.I), "$BTC"),
    (re.compile(r"\b(?:Ethereum|Ether)\b(?! Foundation)"), "$ETH"), (re.compile(r"\bSolana\b(?! (?:Foundation|Labs))"), "$SOL"),
    (re.compile(r"\bXRP\b"), "$XRP"),
]
_MOVE = r"(?:falls?|rises?|jumps?|drops?|slides?|soars?|surges?|plunges?|tumbles?|gains?|rall(?:y|ies)|sinks?|climbs?|slumps?)\s+(?:about\s+|nearly\s+|over\s+)?\d+(?:\.\d+)?\s?%"


def _stock(name: str, ticker: str) -> re.Pattern[str]:
    """Evidence that a sentence is about a listed company's shares: its ticker, '<name> shares/stock', 'shares of <name>', or '<name> falls 4%'."""
    return re.compile(rf"\b(?:{ticker})\b|\b(?:{name})(?:[’']s)?\s+(?i:shares|stock)\b|\b(?i:shares\s+(?:of|in))\s+(?:{name})\b|\b(?:{name})\s+(?i:{_MOVE})")


LISTED: list[tuple[re.Pattern[str], str, re.Pattern[str]]] = [  # (the name, the cashtag, what must also be in the sentence)
    (re.compile(r"\bMicroStrategy\b"), "$MSTR", _stock("MicroStrategy|Strategy", "MSTR")),
    (re.compile(r"\bNvidia\b"), "$NVDA", _stock("Nvidia", "NVDA")), (re.compile(r"\bTesla\b"), "$TSLA", _stock("Tesla", "TSLA")),
    (re.compile(r"\bApple\b"), "$AAPL", _stock("Apple", "AAPL")), (re.compile(r"\bCoinbase\b"), "$COIN", _stock("Coinbase", "COIN")),
    (re.compile(r"\bMeta\b(?! Platforms)"), "$META", _stock("Meta", "META")), (re.compile(r"\bAmazon\b"), "$AMZN", _stock("Amazon", "AMZN")),
    (re.compile(r"\bMicrosoft\b"), "$MSFT", _stock("Microsoft", "MSFT")),
    (re.compile(r"\b(?:Alphabet|Google)\b"), "$GOOGL", _stock("Alphabet|Google", "GOOGL|GOOG")),
    # exchanges / protocols with a token: only the token's own symbol establishes it
    (re.compile(r"\bHyperliquid\b"), "$HYPE", re.compile(r"\bHYPE\b")), (re.compile(r"\bBinance\b"), "$BNB", re.compile(r"\bBNB\b")),
]
# every (name, cashtag) the desk knows, for code that only needs the table (never for deciding a tag: see established())
CASHTAGS: list[tuple[re.Pattern[str], str]] = [*ASSETS, *[(rx, tag) for rx, tag, _ in LISTED]]
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
    label = str(getattr(ev, "label", "") or "")
    if label in (JUST_IN_LABEL, BREAKING_LABEL):  # the desk's own label, still true when it goes out: never "JUST IN" on a story hours old
        from app.newsdesk import wire

        age = wire.age_at_release(ev)
        limit = wire.BREAKING_MINUTES if label == BREAKING_LABEL else wire.JUST_IN_MINUTES
        if age is not None and age > limit:
            return ""
    return PREFIX.get(label, "")


def established(text: str) -> list[tuple[re.Pattern[str], str]]:
    """The (name, cashtag) pairs this text establishes as an asset it is about (see ASSETS / LISTED)."""
    out = [(rx, tag) for rx, tag in ASSETS if rx.search(text)]
    for rx, tag, evidence in LISTED:
        if evidence.search(text) and (rx.search(text) or re.search(rf"\b{re.escape(tag[1:])}\b", text)):
            out.append((rx, tag))
    if re.search(r"\bSaylor\b", text) and SAYLOR_STRATEGY.search(text) and _stock("MicroStrategy|Strategy", "MSTR").search(text):
        out.append((SAYLOR_STRATEGY, "$MSTR"))
    return out


def cashtag(text: str) -> tuple[str, list[str]]:
    """The text with a cashtag after the first mention of each asset it establishes (XRP becomes $XRP), and the cashtags used. A company's or
    exchange's name alone never brings its ticker: "stolen Binance crypto" stays as written (2026-11-13: it became "Binance $BNB crypto")."""
    used: list[str] = []
    out = text
    for rx, tag in established(text):
        if tag in out:
            if tag not in used:
                used.append(tag)
            continue
        bare = re.search(rf"(?<!\$)\b{re.escape(tag[1:])}\b", out)
        if bare:  # the symbol itself is in the text ("BNB falls", "Hyperliquid's HYPE token"): the symbol becomes the tag
            out = out[:bare.start()] + tag + out[bare.end():]
            used.append(tag)
            continue
        # the first mention that isn't a possessive ("Binance's $6.3B hack" can't take a tag inside it); none: no tag
        m = next((x for x in rx.finditer(out) if not re.match(r"[’']s\b", out[x.end():])), None)
        if not m:
            continue
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


def sentences_of(c: Any, case_source: str = "", *, chart: bool = False) -> list[str]:
    """The headline as a sentence, then up to two more: the context's sentences that add something (not a re-telling of the headline),
    then what it means for markets. An outlet's Title Case Headline is set in sentence case, a word keeping its capital only where the
    reporting's own sentences (`case_source`: the articles' summaries and the context) capitalise it mid-sentence: names stay names.
    A feed's sign-off ("Read the full story on ...") never comes along. chart: a chart post's caption (see CHART_KINDS): the headline
    without the bracketed move the chart shows, and at most one takeaway that is no list of figures and names no more than MAX_ASSETS
    assets in all."""
    from app.newsdesk.network import _sentences, sentence_case

    headline = _PRICE_TAG.sub("", c.headline) if chart else c.headline
    cased = sentence_case(headline, f"{c.context} {case_source}")
    head = _sentence(_proper(cased) if cased != headline else cased)
    out = [head]
    seen = _words(head)
    assets = set(_assets(head))
    for s in [*_sentences(unboiled(c.context)), *([c.stakes] if c.stakes else [])]:
        s = unboiled(s)
        if not s:
            continue
        s = _sentence(s)
        w = _words(s)
        if not w or len(w & seen) / len(w) > 0.6:
            continue
        if chart and (len(_FIGURE.findall(s)) >= 3 or len(assets | set(_assets(s))) > MAX_ASSETS):
            continue  # a list of figures, or more assets than a chart caption names: the chart has them
        out.append(s)
        seen |= w
        assets |= set(_assets(s))
        if len(out) >= (CHART_SENTENCES if chart else MAX_SENTENCES):
            break
    return out


def tagged(sents: list[str], limit: int) -> list[str]:
    """The sentences with their cashtags (cashtag()), each tag once in the whole caption and at most `limit` of them."""
    from app.newsdesk import checks

    joined = "\u0000".join(cashtag(s)[0] for s in sents)
    return checks.limit_cashtags(joined, limit).split("\u0000")


def _source_links(links: list[Any], outlets: list[str] | None) -> tuple[str, str]:
    """(HTML, plain): the sources in one short line of clickable names."""
    srcs = [x for x in links if getattr(x, "kind", "article") != "pricechat"][:3]
    if srcs:
        html = " · ".join(f'<a href="{_esc(x.url, quote=True)}">{_esc(x.name)}</a>' for x in srcs)
        return f"🔗 {html}", "Source: " + " · ".join(x.name for x in srcs)
    if outlets:
        return "🔗 " + _esc(" · ".join(outlets[:3])), "Source: " + " · ".join(outlets[:3])
    return "", ""


def telegram_caption(c: Any, links: list[Any], *, prefix: str = "", outlets: list[str] | None = None, case_source: str = "",
                     chart: bool = False) -> tuple[str, str, str]:
    """(HTML, plain with the signature, plain without it: what the checks read). Picture first; then the story in 1-3 sentences (a chart
    post: the headline and at most one takeaway, each on its own line for a phone screen, no cashtags), each cashtag once and one at most,
    and the sources with @thepricechat on one last line."""
    sents = sentences_of(c, case_source, chart=chart)
    while True:
        words = tagged(sents, 0 if chart else TG_CASHTAGS)
        first, rest = words[0], words[1:]
        lead = f"<b>{_esc(prefix)}</b> {_esc(first)}" if prefix else _esc(first)
        sep = "\n\n" if chart else " "  # a chart post: the takeaway on its own line, easy to read under the chart on a phone
        body_html = lead + (sep + _esc(sep.join(rest)) if rest else "")
        body_plain = (f"{prefix} {first}" if prefix else first) + (sep + sep.join(rest) if rest else "")
        src_html, src_plain = _source_links(links, outlets)
        sig_html = f'<a href="{HANDLE_URL}">{HANDLE}</a>'
        footer_html = f"{src_html} · {sig_html}" if src_html else sig_html
        html = f"{body_html}\n\n{footer_html}"
        plain_body = f"{body_plain}\n\n{src_plain}" if src_plain else body_plain
        if len(html) <= TG_LIMIT or len(sents) == 1:
            return html, (f"{plain_body} · {HANDLE}" if src_plain else f"{plain_body}\n\n{HANDLE}"), plain_body
        sents = sents[:-1]


def x_caption(c: Any, links: list[Any], *, prefix: str = "", with_links: bool = True, outlets: list[str] | None = None, case_source: str = "",
              chart: bool = False) -> str:
    """The same story for X: the hook (prefix + the headline with its cashtag), a second sentence when it fits, one source link. One
    cashtag at most (X refuses API posts with more)."""
    from app.newsdesk.network import x_length

    sents = tagged(sentences_of(c, case_source, chart=chart), 1)
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
    """The caption with the photo's full credit (its licence requires it), on its own line before the last line, when the picture couldn't
    carry it. Otherwise the credit is on the picture and the caption has none."""
    c_html, c_plain = caption_credit(meta)
    if not c_html:
        return html, plain
    body, _, footer = html.rpartition("\n\n")
    pbody, _, pfooter = plain.rpartition("\n\n")
    return f"{body}\n\n{c_html}\n\n{footer}", f"{pbody}\n\n{c_plain}\n\n{pfooter}"


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
    # only an asset the text establishes (a possessive counts: "Nvidia's shares fall"); never a company's token or stock from its name alone
    for tag in dict.fromkeys(tag for _, tag in established(text)):
        if tag in CHARTED:
            return CHARTED[tag], tag, tag
    for rx, sym, name in COMMODITY:
        if rx.search(text):
            return sym, name, name
    return None


# owner, 2026-11-12: "allow authentic, licensed photos of public figures in controversy stories when directly relevant, with neutral presentation
# and no implication of guilt". A controversy story may show the public figure it is about; never a private person, never on a story of
# violence or death, and never a photo of an arrest, a courtroom, a police scene or a prison (that photo would imply the guilt).
VIOLENT = re.compile(r"\b(death|deaths|dead|dies|died|killed|kills?|murder\w*|suicide|wars?|casualt(?:y|ies)|assault\w*|rape|sexual|shooting|"
                     r"terror\w*|kidnap\w*|attack\w*|bomb\w*|strikes?)\b", re.I)
ROLE = re.compile(r"\b(CEO|chief executive|founder|co-?founder|chair(?:man|woman)?|president|prime minister|minister|senator|governor|"
                  r"secretary|chancellor|king|queen|prince|mayor|director|billionaire)\b", re.I)
NOT_NEUTRAL = re.compile(r"\b(arrest\w*|court\w*|trial|mug ?shot|police|prison|jail\w*|handcuff\w*|booking|custody|protest\w*|perp walk|"
                         r"indict\w*|testif\w*|hearing|deposition|crying|angry|shouting)\b", re.I)


def _named_person(text: str) -> str | None:
    from app.newsdesk import photos

    names = [re.sub(r"[’']s$", "", n) for n in photos.people_named(text)]  # "Jensen Huang's affair": the person, not the possessive
    if names:
        return names[0]
    for word in re.findall(r"\b[A-Z][a-z]+\b", text):
        if word.lower() in KNOWN_PEOPLE:
            return KNOWN_PEOPLE[word.lower()]
    return None


def public_figure(name: str, text: str) -> bool:
    """A person the public knows by role: a name we know, or one the story gives a public role ("CEO Jensen Huang")."""
    if name in KNOWN_PEOPLE.values():
        return True
    last = name.split()[-1]
    return bool(re.search(rf"{ROLE.pattern}\W+(?:\w+\W+){{0,3}}?{re.escape(name)}|{re.escape(last)}\W+(?:\w+\W+){{0,2}}?{ROLE.pattern}", text, re.I))


def person_subject(ev: Any) -> str | None:
    """The person the story is about, when it is about one. In a controversy story only a public figure it names, and never in a story of
    violence or death."""
    from app.newsdesk import photos

    text = f"{getattr(ev, 'headline', '')} {getattr(ev, 'title', '')}"
    name = _named_person(text)
    if not name:
        return None
    if photos.is_sensitive(text) and (VIOLENT.search(text) or not public_figure(name, text)):
        return None
    return name


# a market move's subject, for the photo beside its chart: (words in the headline, search, word the photo's title must contain)
MARKET_PHOTOS = [(re.compile(r"\b(s&p|dow|nasdaq|wall street|stocks?)\b", re.I), "New York Stock Exchange facade", "stock exchange"),
                 (re.compile(r"\b(oil|crude|brent|wti)\b", re.I), "oil tanker at sea", "tanker"),
                 (re.compile(r"\bgold\b", re.I), "gold bullion bars", "gold")]


STAND_INS = {"tehran", "knesset", "kremlin", "kyiv|maidan", "riyadh", "doha", "dubai", "beijing", "tokyo", "white house", "capitol", "pentagon", "court"}


async def find_photo(ev: Any, *, used: set[str], transport: Any, panel: bool) -> tuple[Image.Image, dict[str, Any]] | None:
    """A licensed photo of the story's subject: the person first (when a person is the subject and the story isn't sensitive), else the
    company or place (app/newsdesk/visual.subject_queries). Every licence and relevance rule of app/newsdesk/photos applies."""
    from app.newsdesk import photos, visual

    sensitive = visual.is_sensitive(ev)
    plans: list[tuple[list[str], list[str]]] = []
    person = person_subject(ev)
    if person:
        plans.append(([person, f"{person} portrait"], [person]))  # its own search: the person is the subject (see the neutral filter below)
    # the company or institution the story is about, never a city or country stand-in (2026-11-16 audit: the Kremlin skyline on a
    # satellite story, the Knesset on a Gaza strike); a major incident's named site comes from wire's rule below
    queries = [(q, must) for q, must in visual.subject_queries(f"{ev.headline} {ev.title}") if must not in STAND_INS]
    if getattr(ev, "kind", "") in ("market", "recap"):
        queries += [(q, must) for rx, q, must in MARKET_PHOTOS if rx.search(f"{ev.headline} {ev.title}")]
    for term in dict.fromkeys(must for _, must in queries):
        plans.append(([q for q, must in queries if must == term], [term]))
    async with httpx.AsyncClient(transport=transport) as client:
        for qs, terms in plans:
            try:
                is_person = person is not None and terms == [person]
                found = await photos.discover(client, qs, terms, used=used, sensitive=sensitive and not is_person, panel=panel)
                if is_person and sensitive:  # a controversy: only a neutral photo of them (no arrest, court, police or protest scene)
                    found = [p for p in found if not NOT_NEUTRAL.search(f"{p.title} {p.description} {' '.join(p.tags)}")]
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
    if getattr(ev, "kind", "") in ("news", "update"):  # the named site of a major incident (wire's rule), credited
        from app.newsdesk import wire

        site = await wire._site_photo(ev, used, transport)
        if site is not None:
            img, pmeta = site
            credit = image_credit(pmeta)
            data, meta = single_photo(img, credit)
            return data, {**base, **pmeta, **meta, "kind": "photo", "credit_line": credit}
    # nothing real: a text post (owner, 2026-11-16: no headline-only card, no illustration standing in for a photo)
    return b"", {**base, "kind": "text", "layout": "text"}


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
    if layout == "text":
        return b"", meta  # a text post on Telegram stays a text post on X
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


