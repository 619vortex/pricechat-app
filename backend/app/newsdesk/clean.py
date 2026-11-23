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
    # ... or a rally to a record (post 195, 2026-11-19: "Microsoft had rallied back to record highs" is about the shares)
    return re.compile(rf"\b(?:{ticker})\b|\b(?:{name})(?:[’']s)?\s+(?i:shares|stock)\b|\b(?i:shares\s+(?:of|in))\s+(?:{name})\b|\b(?:{name})\s+(?i:{_MOVE})|"
                      rf"\b(?:{name})\s+(?i:(?:had|has)\s+)?(?i:rall(?:ied|ies|y))\b[^.]{{0,30}}\b(?i:record|all-time)\s+(?i:highs?)\b")


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
                     chart: bool = False, data_note: str = "") -> tuple[str, str, str]:
    """(HTML, plain with the signature, plain without it: what the checks read). Picture first; then the story in 1-3 sentences (a chart
    post: the headline and at most one takeaway, each on its own line for a phone screen, no cashtags), each cashtag once and one at most,
    and the sources with @thepricechat on one last line. data_note: a measured market post's reference and source line ("Change from
    Thursday's close: Nasdaq 23,879.20 ... Market data: Yahoo Finance, 20:00 UTC, intraday (US market open)."), always printed, on its own
    line above the sources: the figures in the headline can be checked (owner, 2026-11-21: Telegram 203 printed percentages only)."""
    sents = sentences_of(c, case_source, chart=chart)
    if data_note:  # the note carries the measured figures: the story sentences never repeat them
        sents = sents[:1]
    while True:
        words = tagged(sents, 0 if chart else TG_CASHTAGS)
        first, rest = words[0], words[1:]
        lead = f"<b>{_esc(prefix)}</b> {_esc(first)}" if prefix else _esc(first)
        sep = "\n\n" if chart else " "  # a chart post: the takeaway on its own line, easy to read under the chart on a phone
        body_html = lead + (sep + _esc(sep.join(rest)) if rest else "") + (f"\n\n{_esc(data_note)}" if data_note else "")
        body_plain = (f"{prefix} {first}" if prefix else first) + (sep + sep.join(rest) if rest else "") + (f"\n\n{data_note}" if data_note else "")
        src_html, src_plain = _source_links(links, outlets)
        sig_html = f'<a href="{HANDLE_URL}">{HANDLE}</a>'
        footer_html = f"{src_html} · {sig_html}" if src_html else sig_html
        html = f"{body_html}\n\n{footer_html}"
        plain_body = f"{body_plain}\n\n{src_plain}" if src_plain else body_plain
        if len(html) <= TG_LIMIT or len(sents) == 1:
            return html, (f"{plain_body} · {HANDLE}" if src_plain else f"{plain_body}\n\n{HANDLE}"), plain_body
        sents = sents[:-1]


def x_caption(c: Any, links: list[Any], *, prefix: str = "", with_links: bool = True, outlets: list[str] | None = None, case_source: str = "",
              chart: bool = False, data_note: str = "") -> str:
    """The same story for X: the hook (prefix + the headline with its cashtag), a second sentence when it fits, one source link. One
    cashtag at most (X refuses API posts with more). data_note (see telegram_caption): the whole note when it fits, else its last sentence
    (the "Market data: ..." line, which never gives way)."""
    from app.newsdesk.network import _sentences, x_length

    sents = tagged(sentences_of(c, case_source, chart=chart), 1)
    head = f"{prefix} {sents[0]}" if prefix else sents[0]
    srcs = [x for x in links if getattr(x, "kind", "article") != "pricechat"]
    link = f"{srcs[0].name}: {srcs[0].url}" if (with_links and srcs) else (f"Via {' · '.join([x.name for x in srcs][:2] or (outlets or [])[:2])}" if (srcs or outlets) else "")
    if data_note:
        notes = [data_note] + ([_sentences(data_note)[-1]] if len(_sentences(data_note)) > 1 else [])
        for note in notes:
            text = "\n\n".join(p for p in [head, note, link] if p)
            if x_length(text) <= X_LIMIT:
                return text
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
    out = credit if credit.lower().startswith("photo") else f"Photo: {credit}" if credit else ""
    who = str(meta.get("who") or "").strip()  # who is pictured, where and when (photography.CAPTIONS)
    return f"{who} · {out}" if who and out else out


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


def single_photo(photo: Image.Image, credit: str, platform: str = "x") -> tuple[bytes, dict[str, Any]]:
    """The photograph alone, cropped (never stretched) to the platform's frame, with its licence credit small in a corner."""
    from app.newsdesk import photography

    img, box = photography.frame(photo, platform)
    _small(img, credit, "left")
    return _png(img), {"layout": "photo", "subject": list(box), "frame": list(img.size)}


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
# an act of violence in the headline: never a person's portrait for it (the war's diplomacy is fine: "Trump tells Xi ... end Ukraine war")
ACT_OF_VIOLENCE = re.compile(r"\b(strikes?|struck|attack\w*|kill\w*|dead|deaths?|dies|died|bomb\w*|shell(?:ed|ing)|missiles?|drones?|explosions?|"
                             r"blasts?|shooting|shot|gunm[ae]n|invad\w*|invasion|intercept\w*|wounded|injur\w*|casualt\w*|massacre\w*|"
                             r"assassinat\w*|hostages?|murder\w*|terror\w*|raids?)\b", re.I)
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


MAX_VISION_LOOKS = 4  # model looks per picture: a few candidates, never a crawl


def _expectation(b: Any, term: str, is_person: bool) -> str:
    if is_person:
        return f"{term}, the person the story is about: a clear, neutral photo of them"
    if b.cls in ("conflict", "disaster"):
        return "the event itself, photographed where and when it happened"
    return f"{term}: its recognisable branding (logo, signage, store, headquarters) or its own product"


async def find_photo(ev: Any, *, used: set[str], transport: Any, panel: bool, records: list[Any] | None = None,
                     settings: Any = None) -> tuple[Image.Image, dict[str, Any]] | None:
    """A licensed photo whose SUBJECT is the story's subject (app/newsdesk/editor): the person first (when a person is the subject and the
    story isn't sensitive), else the company. Every licence and relevance rule of app/newsdesk/photos applies, then the editor's: what
    the photo depicts (a sign, a headquarters, a product, the person; never a generic scene that merely carries the name), its pixels,
    one photo per story, and the vision check when the model is available. None: the post goes as text, never with a stand-in."""
    from app.newsdesk import editor, photography, photos, visual

    b = editor.brief(ev)
    sensitive = visual.is_sensitive(ev)
    plans: list[tuple[list[str], list[str]]] = []
    person = person_subject(ev)
    if person and b.cls not in ("conflict", "disaster"):
        plans.append(([person, f"{person} portrait"], [person]))  # its own search: the person is the subject (see the neutral filter below)
    # the company or institution the story is about, never a city or country stand-in (2026-11-16 audit: the Kremlin skyline on a
    # satellite story, the Knesset on a Gaza strike)
    queries = [(q, must) for q, must in visual.subject_queries(f"{ev.headline} {ev.title}") if must not in STAND_INS]
    if b.cls in ("conflict", "disaster"):
        queries = [(f"{p} {w}", p) for p in b.places[:2] for w in ("attack", "damage", str(b.year or ""))]  # the event, where it happened
    for term in dict.fromkeys(must for _, must in queries):
        plans.append(([q for q, must in queries if must == term], [term]))
    story = f"{getattr(ev, 'headline', '')}. {getattr(ev, 'title', '')}"
    looks = 0
    async with httpx.AsyncClient(transport=transport) as client:
        for qs, terms in plans:
            is_person = person is not None and terms == [person]
            try:
                found = await photos.discover(client, qs, terms, used=used, sensitive=sensitive and not is_person, panel=panel)
                if is_person and sensitive:  # a controversy: only a neutral photo of them (no arrest, court, police or protest scene)
                    found = [p for p in found if not NOT_NEUTRAL.search(f"{p.title} {p.description} {' '.join(p.tags)}")]
            except Exception as e:  # noqa: BLE001
                logger.info("clean: photo discovery failed (%s)", type(e).__name__)
                continue
            passed: list[tuple[float, Any, Any, Any]] = []
            found = [p for p in found if not photography.refused(str(terms[0]), p.title, p.description)]  # a metal is never a coin collection
            for p in found[:4]:
                fit = editor.depiction(p.title, p.tags, p.description, terms[0], b, person=is_person)
                if not fit.ok and fit.depiction != "weak":
                    logger.info("visual editor: skip %s for event %s: %s", p.title[:80], getattr(ev, "id", None), fit.why)
                    continue
                if (why := editor.reuse_problem(p.url, ev, records or [])):
                    logger.info("visual editor: skip %s: %s", p.title[:80], why)
                    continue
                img = await photos.fetch(client, p, min_width=photos.PANEL_MIN_WIDTH if panel else photos.MIN_WIDTH)
                if img is None:
                    continue
                if (why := editor.pixel_problem(img)):
                    logger.info("visual editor: skip %s: %s", p.title[:80], why)
                    continue
                passed.append((photography.aesthetic(img), p, img, fit))
            # owner, 2026-11-19: of the relevant, licensed photos, the most striking (dark surround, rich colour, contrast, detail)
            for score, p, img, fit in sorted(passed, key=lambda x: -x[0]):
                look = None
                if looks < MAX_VISION_LOOKS:
                    looks += 1
                    look = await editor.vision(img, story, _expectation(b, terms[0], is_person), settings=settings)
                ok, why = editor.vision_verdict(look, fit)
                if ok and look is None:
                    # owner, 2026-11-19: "Never trust a filename alone. If visual verification is unavailable, use only manually approved
                    # subject-specific images or publish text-only." A searched photo is never published on its record alone.
                    ok, why = False, "not visually verified (no vision check available): only the library's approved photos go without one"
                if not ok:
                    logger.info("visual editor: skip %s for event %s: %s", p.title[:80], getattr(ev, "id", None), why)
                    continue
                meta = p.to_meta() if hasattr(p, "to_meta") else {"url": p.url}
                verdict = {"ok": True, **fit.out(), "why": why, "vision": look, "subject": terms[0], "aesthetic": score}
                verdict["ok"] = True
                return img, {"url": p.url, "page": meta.get("page", ""), "title": meta.get("title", ""), "credit": p.credit,
                             "licence": meta.get("licence", "") or getattr(p, "licence", ""), "photo": meta, "person": terms[0] if is_person else "",
                             "editor": verdict}
    return None


# ------------------------------------------------------------------------------------------------- templates (app/newsdesk/templates)
NAMES = {"$BTC": "Bitcoin", "$ETH": "Ether", "$SOL": "Solana", "$XRP": "XRP", "$NVDA": "Nvidia", "$TSLA": "Tesla", "$AAPL": "Apple",
         "$COIN": "Coinbase", "$META": "Meta", "$AMZN": "Amazon", "$MSFT": "Microsoft", "$GOOGL": "Alphabet", "$MSTR": "Strategy",
         "$HYPE": "Hyperliquid", "$BNB": "BNB"}
FALLS = re.compile(r"\b(fall(?:s|en)?|fell|drop(?:s|ped)?|slid(?:e|es)?|sink(?:s)?|sank|plung(?:e|es|ed)|crash(?:es|ed)?|tumbl(?:e|es|ed)|"
                   r"slump(?:s|ed)?|below|lose[s]?|lost|selloff|sell-off|down)\b", re.I)
RISES = re.compile(r"\b(rise[sn]?|rose|rall(?:y|ies|ied)|jump(?:s|ed)?|surg(?:e|es|ed)|soar(?:s|ed)?|climb(?:s|ed)?|gain(?:s|ed)?|"
                   r"record highs?|all-time highs?|above|up)\b", re.I)
SERVICES = re.compile(r"\b(Outlook|Teams|Azure|Microsoft 365|Office 365|Xbox Live|iCloud|Apple Pay|App Store|Gmail|YouTube|Google Cloud|"
                      r"AWS|ChatGPT|WhatsApp|Instagram|Facebook|Slack|GitHub|Zoom|PayPal|Venmo|Zelle|Cash App)\b")


def render(name: str, args: dict[str, Any], fmt: str) -> tuple[bytes, dict[str, Any]]:
    """A template from its stored arguments (X redraws what Telegram showed, in its own format)."""
    from app.newsdesk import templates

    a = dict(args)
    when = a.pop("when", None)
    when = datetime.fromisoformat(when) if isinstance(when, str) and when else None
    if name == "move":
        ctx = [tuple(p) for p in a.get("context_points") or []] or None
        return templates.move_card(fmt, when=when, **{**a, "points": [tuple(p) for p in a["points"]], "context_points": ctx})
    if name == "anomaly":
        return templates.anomaly_card(fmt, when=when, **{**a, "closes": [tuple(p) for p in a["closes"]]})
    if name == "battle":
        return templates.battle_card(fmt, when=when, names=tuple(a["names"]), pcts=tuple(a["pcts"]), period=a["period"],
                                     series=[[tuple(p) for p in s] for s in a["series"]])
    if name == "then_now":
        return templates.then_now_card(fmt, when=when, **{**a, "series": [tuple(p) for p in a["series"]]})
    if name == "ranking":
        return templates.ranking_card(fmt, when=when, title=a["title"], rows=[tuple(r) for r in a["rows"]], kicker=a.get("kicker", "THE WEEK"))
    if name == "outage":
        stock = tuple(a["stock"]) if a.get("stock") else None
        return templates.outage_card(fmt, when=when, **{**a, "stock": stock})
    raise ValueError(f"unknown template {name!r}")


def _iso(when: Any) -> str:
    return when.isoformat() if isinstance(when, datetime) else str(when or "")


def data_template(post: dict[str, Any], when: datetime | None, mad: bool) -> tuple[str, dict[str, Any]] | None:
    """(template, arguments) for a Viral Engine data post, every figure from the post's own verified series; None when it has none."""
    from app.newsdesk import datapost, templates

    kind = post.get("kind", "")
    head = str(post.get("headline") or "")
    series = post.get("series") or {}
    syms = list(post.get("symbols") or series.keys())
    name_of = lambda s: datapost.NAME.get(s, s)  # noqa: E731
    cap_ = lambda s: s[:1].upper() + s[1:]  # noqa: E731
    if post.get("bars"):
        return "ranking", {"title": head, "rows": [[str(n), float(v)] for n, v in post["bars"]], "when": _iso(when),
                           "kicker": "MARKETS GONE MAD" if mad else "THE WEEK" if kind == "ranking" else "SECTOR ROTATION"}
    if kind == "anomaly" and syms and series.get(syms[0]):
        m = re.search(r"(since \w+ \d{4}|in at least two years)", head)
        pcts = templates.pcts_in(head)
        return "anomaly", {"name": cap_(name_of(syms[0])), "symbol": syms[0], "closes": series[syms[0]], "headline_pct": pcts[-1] if pcts else None,
                           "since": m.group(1) if m else "", "when": _iso(when), "live": True}
    if kind == "battle" and len(syms) == 2 and all(series.get(s) for s in syms):
        pcts = templates.pcts_in(head)
        if len(pcts) < 2:
            return None
        m = re.search(r"over the (past [\w ]+?):", head)
        return "battle", {"names": [cap_(name_of(s)) for s in syms], "pcts": pcts[:2], "series": [series[s] for s in syms],
                          "period": m.group(1) if m else "period", "when": _iso(when)}
    if kind == "then_now" and syms and series.get(syms[0]):
        nums = [float(x.replace(",", "")) for x in re.findall(r"\$?(\d[\d,]*(?:\.\d+)?)(?!\s?%)", head.split(":", 1)[-1])]
        if len(nums) < 2:
            return None
        return "then_now", {"name": cap_(name_of(syms[0])), "symbol": syms[0], "then": nums[0], "now_price": nums[-1], "series": series[syms[0]],
                            "when": _iso(when)}
    return None


def _story_move(ev: Any, subject: tuple[str, str, str], bars: list[list[float]]) -> dict[str, Any] | None:
    """The move card's arguments for a story about a price, from Yahoo's last 24 hours of 15-minute candles, or None when the candles
    disagree with what the story says (direction, or its percentage by more than visual.CROSS_SOURCE_PP)."""
    from app.newsdesk import visual

    if len(bars) < 20:
        return None
    pts = [[bars[0][0] - 900, bars[0][1]], *[[b[0], b[4]] for b in bars]]  # the first candle's open, then each close
    pct = (pts[-1][1] / pts[0][1] - 1) * 100
    head = f"{getattr(ev, 'headline', '')} {getattr(ev, 'title', '')}"
    falls, rises = bool(FALLS.search(head)), bool(RISES.search(head))
    if falls != rises and (pct < 0) != falls:
        logger.info("visual editor: event %s: the story says %s, 24h candles say %+.2f%%: no chart", getattr(ev, "id", None), "down" if falls else "up", pct)
        return None
    m = visual.HEADLINE_PCT.search(head)
    if m and visual._headline_window(head) in (None, 86400) and abs(float(m.group(1)) - abs(pct)) > visual.CROSS_SOURCE_PP:
        logger.info("visual editor: event %s: the story says %s%%, 24h candles say %+.2f%%: no chart", getattr(ev, "id", None), m.group(1), pct)
        return None
    tag = subject[1]
    return {"name": NAMES.get(tag, tag.lstrip("$")), "ticker": tag.lstrip("$") if tag.startswith("$") else subject[0], "pct": round(pct, 4),
            "points": pts, "window": "Last 24 hours", "source": "Yahoo Finance · 15-minute closes", "when": _iso(datetime.fromtimestamp(bars[-1][0] + 900, timezone.utc))}


PEAK_TALK = re.compile(r"\b(record highs?|all-time highs?|record close|peak(?:ed)?|rallied back)\b", re.I)
PEAK_MIN_DROP = 2.0  # percent below the peak before "since its peak" is a story the chart can tell


async def _peak_move(ev: Any, subject: tuple[str, str, str], transport: Any, now: datetime) -> dict[str, Any] | None:
    """A stock told around its record ("had rallied back to record highs. Then came ..."): the real daily closes from 20 sessions before
    its highest close of the last three months to the latest close, and the fall from that peak, measured. None unless the data shows a
    real fall of at least PEAK_MIN_DROP from a peak inside the window (never a claim the closes don't make)."""
    from app.newsdesk import datapost

    if not PEAK_TALK.search(f"{getattr(ev, 'headline', '')} {getattr(ev, 'title', '')}"):
        return None
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            daily = await datapost.fetch_daily(client, subject[0], "6mo")
    except Exception as e:  # noqa: BLE001
        logger.info("clean: no daily closes for %s (%s)", subject[0], type(e).__name__)
        return None
    daily = [(t, c) for t, c in daily if t <= now][-66:]  # about three months of sessions
    if len(daily) < 30:
        return None
    i = max(range(len(daily)), key=lambda k: daily[k][1])
    peak_t, peak = daily[i]
    last = daily[-1][1]
    drop = (last / peak - 1) * 100
    if i >= len(daily) - 1 or -drop < PEAK_MIN_DROP:
        return None
    pts = [[t.timestamp(), c] for t, c in daily[max(0, i - 20):]]
    pts_from_peak = [p for p in pts if p[0] >= peak_t.timestamp()]
    tag = subject[1]
    return {"name": NAMES.get(tag, tag.lstrip("$")), "ticker": tag.lstrip("$"), "pct": round(drop, 4), "points": pts_from_peak,
            "context_points": pts, "window": f"Since its {peak_t.day} {peak_t:%b} peak close", "source": "Yahoo Finance · daily closes",
            "when": _iso(now)}


def _outage(ev: Any, b: Any, stock: tuple[str, float, str] | None) -> dict[str, Any]:
    from app.newsdesk import visual

    inc = b.incident
    head = f"{getattr(ev, 'headline', '')} {getattr(ev, 'title', '')}"
    status = "cyberattack" if inc.kind == "cyber" else "recovering" if inc.resolved else "outage" if "outage" in (inc.disruption or "") or "down" in (inc.disruption or "") else "disruption"
    when = getattr(ev, "released_at", None) or getattr(ev, "first_seen", None)
    return {"company": b.subject or inc.label.title(), "services": list(dict.fromkeys(m.group(0) for m in SERVICES.finditer(head)))[:3], "status": status,
            "sector": inc.label, "source": visual.source_name(ev), "when": _iso(when), "stock": list(stock) if stock else None}


def _template(name: str, args: dict[str, Any], fmt: str, base: dict[str, Any], **extra: Any) -> tuple[bytes, dict[str, Any]] | None:
    from app.newsdesk import templates

    try:
        data, meta = render(name, args, fmt)
    except templates.TemplateRefused as e:
        logger.info("visual editor: no %s graphic: %s", name, e)
        return None
    return data, {**base, **meta, "kind": "template", "layout": name, "template_args": args, **extra}


def text_only(base: dict[str, Any], why: str = "") -> tuple[bytes, dict[str, Any]]:
    if why:
        logger.info("visual editor: text only: %s", why)
    return b"", {**base, "kind": "text", "layout": "text"}


async def picture(ev: Any, *, used: set[str] | None = None, transport: Any = None, market: Any = None, now: datetime | None = None,
                  db: Any = None, platform: str = "telegram", records: list[Any] | None = None) -> tuple[bytes, dict[str, Any]]:
    """The clean picture for an event and what it was made of (stored on the event: X redraws the same photograph in its own frame).

    Owner, 2026-11-19 (second brief): beautiful, relevant, real photographs by default; the caption carries the news and the numbers.
    1. A comparison (one asset against another, the week's ranking): its chart, because the story IS the comparison.
    2. A market move or a single-asset data post: its numbers are still verified (a refused figure stops the post), then the subject's
       photograph (app/newsdesk/photography): gold shows gold, Bitcoin shows Bitcoin.
    3. A war or a disaster: a dated photograph of the event itself, or text. Never a stand-in.
    4. A person the story is about, then the subject the headline names first: the editor's library photograph, else the most striking
       licensed photo whose record shows the subject (app/newsdesk/editor).
    5. A live outage with no photograph: the outage card. Otherwise text only."""
    from app.newsdesk import editor, photography, visual

    now = now or datetime.now(timezone.utc)
    fmt = "x" if platform == "x" else "tg"
    f = ev.facts or {}
    used = used or set()
    base = {"style": STYLE}
    b = editor.brief(ev)
    kind = getattr(ev, "kind", "")
    subject = None
    if kind == "data" and f.get("data"):
        post = f["data"]
        if post.get("bars") or post.get("kind") in ("battle", "ranking", "rotation"):  # a comparison: the chart is the story
            spec = data_template(post, getattr(ev, "released_at", None) or now, bool(f.get("kicker")))
            got = _template(spec[0], spec[1], fmt, base) if spec else None
            return got or text_only(base, "the comparison's figures did not check out")
        syms = list(post.get("symbols") or (post.get("series") or {}).keys())
        subject = photography.subject_for_symbol(syms[0] if syms else "", str(post.get("headline") or ""))
    elif kind == "market" and visual.yahoo_equity(ev):
        # a US stock or index move: its figures against Yahoo's own quote (there are no Hyperliquid candles for the Nasdaq), then the
        # subject's photograph (Wall Street for the indices). Before 2026-11-21 every such post went out as text: Telegram 199 and 203.
        check = await _quote_check(ev, transport, now, db)
        if check is None:
            return text_only(base, "no Yahoo quote to check the figures against")
        ev.facts = {**(ev.facts or {}), "number_check": check.out()}
        if not check.ok:
            logger.warning("visual editor: event %s refused, its figures don't match Yahoo: %s", getattr(ev, "id", None), "; ".join(check.reasons))
            from app.newsdesk import safety

            safety.note("figures_mismatch", str((ev.facts or {}).get("symbol") or ""), "; ".join(check.reasons)[:300])
            raise visual.NumbersRefused("; ".join(check.reasons))  # a market post IS its numbers: unverified, it doesn't go
        f = ev.facts
        subject = photography.subject_for_symbol(str(f.get("symbol") or ""), str(ev.headline or ""))
    elif kind in ("market", "recap"):
        chosen = visual.plan(ev, [])
        checked = await visual._chart_for(ev, chosen, market, now)  # raises NumbersRefused when the numbers don't check out
        if checked is None:
            return text_only(base, "no verified series")
        subject = photography.subject_for_symbol(str(f.get("coin") or (f.get("tickers") or [""])[0] or ""), str(f.get("name") or ev.headline))
    if subject is not None:
        got = await photography.library_photo(subject, transport=transport, records=records)
        if got is not None:
            return _photo_out(got, base, platform)
        return text_only(base, f"no {subject.name} photograph available")
    if kind in ("data", "market", "recap"):
        return text_only(base, "no photograph of this asset")
    text = f"{ev.headline} {ev.title}"
    # the people a story is about (owner, 2026-11-19: "Trump/Xi/Zelensky story -> relevant real photograph of the people"), from the
    # editor's people library: before a company or market subject only when named first, and never for an act of violence (a war's diplomacy
    # is about the leaders; a strike is about the strike: its own photograph or text)
    who = photography.people_of(text)
    lib_at = photography.subject_of(text)
    lib_pos = lib_at.rx().search(text).start() if lib_at is not None else len(text)  # type: ignore[union-attr]
    if who is not None and who[1] <= lib_pos and not ACT_OF_VIOLENCE.search(text):
        got = await photography.library_photo(who[0], transport=transport, records=records)
        if got is not None:
            return _photo_out(got, base, platform)
    if b.cls in ("conflict", "disaster"):  # the event itself, dated, or nothing
        got = await find_photo(ev, used=used, transport=transport, panel=False, records=records)
        return _photo_out(got, base, platform) if got is not None else text_only(base, f"no dated photograph of this {b.cls}")
    text = f"{ev.headline} {ev.title}"
    person = person_subject(ev)
    lib = photography.subject_of(text)
    if person and not (lib and lib.rx().search(text).start() < text.find(person.split()[-1])):
        got = await find_photo(ev, used=used, transport=transport, panel=False, records=records)  # the person first in its plans
        if got is not None:
            return _photo_out(got, base, platform)
    if lib is not None:
        got = await photography.library_photo(lib, transport=transport, records=records)
        if got is not None:
            return _photo_out(got, base, platform)
    got = await find_photo(ev, used=used, transport=transport, panel=False, records=records)
    if got is not None:
        return _photo_out(got, base, platform)
    if b.cls in ("outage", "cyber") and b.incident is not None:  # no photograph of the service: its name and status, nothing invented
        got = _template("outage", _outage(ev, b, None), fmt, base)
        if got:
            return got
    return text_only(base, f"no photograph shows this {b.cls} story's subject")


async def _quote_check(ev: Any, transport: Any, now: datetime | None, db: Any) -> Any:
    """visual.verify_quote_move against the quotes the live re-read just used (app/newsdesk/livedata keeps them 90 s), else read now.
    None when no quote at all could be read (the post's text was already re-read before sending; it goes without a picture)."""
    from app.newsdesk import livedata, sources, visual

    f = ev.facts or {}
    watchlist: list[dict[str, Any]] = sources.DEFAULT_WATCHLIST
    if db is not None:
        try:
            from app.newsdesk import engine

            watchlist = engine.settings_row(db).watchlist or sources.DEFAULT_WATCHLIST
        except Exception:  # noqa: BLE001 - the default list has every index
            watchlist = sources.DEFAULT_WATCHLIST
    try:
        quotes = await livedata._fetch(livedata._item_for(str(f.get("symbol") or ""), watchlist), transport)
    except Exception as e:  # noqa: BLE001
        logger.info("visual editor: no Yahoo quote for event %s (%s)", getattr(ev, "id", None), type(e).__name__)
        return None
    if not quotes:
        return None
    return visual.verify_quote_move(ev, quotes, now)


def _photo_out(got: tuple[Image.Image, dict[str, Any]], base: dict[str, Any], platform: str) -> tuple[bytes, dict[str, Any]]:
    img, pmeta = got
    credit = image_credit(pmeta)
    data, meta = single_photo(img, credit, platform)
    return data, {**base, **pmeta, **meta, "kind": "photo", "credit_line": credit}


async def download(url: str, transport: Any = None) -> Image.Image:
    """The licensed photo again, as stored (X redraws Telegram's picture)."""
    from app.newsdesk import photos

    async with httpx.AsyncClient(transport=transport) as client:
        r = await client.get(url, headers=photos.UA, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")


async def redraw(ev: Any, meta: dict[str, Any], *, transport: Any = None, db: Any = None, now: datetime | None = None,
                 platform: str = "telegram") -> tuple[bytes, dict[str, Any]] | None:
    """The same clean picture again from what was stored (X posts what Telegram showed): the same photo, the same template with the same
    verified figures (in X's 16:9 format when it is for X)."""
    layout = meta.get("layout")
    if layout in ("text", "link_preview"):
        return b"", meta  # a text post on Telegram stays one on X; the article's preview is Telegram's own and is never redrawn or re-uploaded
    if meta.get("template") and meta.get("template_args"):
        fmt = "x" if platform == "x" else "tg"
        data, tmeta = render(str(meta["template"]), meta["template_args"], fmt)
        return data, {**meta, **tmeta}
    if layout in ("collage", "photo") and meta.get("url"):
        img = await download(meta["url"], transport)
        credit = str(meta.get("credit_line") or image_credit(meta))
        if layout == "collage":
            return collage(img, credit, meta.get("bars") or [], str(meta.get("label") or ""))[0], meta
        return single_photo(img, credit, platform)[0], meta
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


