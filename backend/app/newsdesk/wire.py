"""The wire style (owner, 2026-11-14: "a cleaner, faster Watcher Guru news style"). Network.post_style "wire", the default; Admin switches
back to "clean" or "classic" (network.STYLES) without a deploy.

Words: one short factual headline, usually one or two sentences, nothing else. A tag only when the story earns it (`tag_for`):
- BREAKING:  major and market-moving (editorial.breaking_worthy, or an exceptional measured move / liquidation flush: editorial.exceptional),
             strong evidence (official, two outlets, our prices), and it just happened: at most 60 minutes from first report to release.
- JUST IN:   fresh (the desk earned a label: BREAKING / JUST IN / ALERT) but smaller, at most 90 minutes from first report to release.
- UPDATE:    a development on a story already posted (an update event: Telegram replies to the original message).
- otherwise no tag at all. Data posts and market checks never carry one (no "MARKETS GONE MAD", no "ALERT").
A flag follows a tag only when the headline leads with the country (brief.flag). The sources and @thepricechat share one quiet last line.

Wording (`checks.wording_problems`, on the copy and again on the final caption): no "massive", "huge", "explodes"... unless the sources
use the word and the sentence carries the figure; no superlative ("record", "biggest", "first-ever") the sources don't use; no "crashes",
"plunges", "soars" without a figure or the sources' own word. On top of every existing gate (figures, tickers, names, accusations, quotes,
the chart), never instead of one.

Pictures (`picture`), only real ones and never forced:
- chart   real prices: a measured market move or liquidation flush (the desk's verified series), or a story that leads with an asset and
          reports its move (a % move, a level broken, a record, liquidations) or one the Viral Engine measured reacting.
- data    a data post's comparison card (measured closes).
- photo   a licensed photo of the public figure the story is about (clean.person_subject's rules), never a generic scene or a company logo.
- text    everything else: a text-only post. No AI illustration in this style.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html import escape as _html_escape
from typing import Any

from app.newsdesk import brief, checks, clean

logger = logging.getLogger(__name__)

STYLE = "wire"
HANDLE, HANDLE_URL = clean.HANDLE, clean.HANDLE_URL
BREAKING, JUST_IN, UPDATE = "BREAKING:", "JUST IN:", "UPDATE:"
BREAKING_MINUTES = 60  # first report to release: BREAKING means it just happened
JUST_IN_MINUTES = 90
HEAD_MAX = 120  # characters: a longer headline loses its trailing clause (network.shorten: nothing is added)
SECOND_MAX = 200  # the second sentence, when there is one
STRONG = ("official", "two outlets", "price data", "exchange data")


def _esc(text: str, quote: bool = False) -> str:
    return _html_escape(text, quote=quote)


# --------------------------------------------------------------------------------------------------------------- tags
def _utc(at: Any) -> datetime | None:
    if not isinstance(at, datetime):
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def age_at_release(ev: Any) -> float | None:
    """Minutes from the story's first report (an outlet's publication, else when the desk first saw it) to its release."""
    f = getattr(ev, "facts", None) or {}
    released = _utc(getattr(ev, "released_at", None))
    first = None
    if f.get("published_at"):
        try:
            first = _utc(datetime.fromisoformat(str(f["published_at"])))
        except ValueError:
            first = None
    first = first or _utc(getattr(ev, "first_seen", None))
    if released is None or first is None:
        return None
    return max(0.0, (released - first).total_seconds() / 60)


def market_moving(ev: Any) -> bool:
    """Major and market-moving: an exceptional measured move or liquidation flush, or news the desk would break between slots."""
    from app.newsdesk import editorial

    kind = getattr(ev, "kind", "")
    if kind == "market":
        return editorial.exceptional(ev)[0]
    if kind in ("news", "update"):
        return editorial.breaking_worthy(getattr(ev, "title", "") or "", getattr(ev, "topic", "") or "", (getattr(ev, "facts", None) or {}).get("importance"))[0]
    return False


def tag_for(ev: Any) -> str:
    """BREAKING / JUST IN / UPDATE, or "" (see the module's rules)."""
    f = getattr(ev, "facts", None) or {}
    kind = getattr(ev, "kind", "")
    if kind in ("data", "recap"):
        return ""
    if kind == "update" or str(f.get("kicker") or "").startswith("UPDATE"):
        return UPDATE
    label = str(getattr(ev, "label", "") or "")
    age = age_at_release(ev)
    if age is None:
        return ""
    earned = label in (brief.BREAKING, brief.JUST_IN, brief.ALERT) or getattr(ev, "slot", "") == "breaking"
    if not earned:
        return ""
    if (label == brief.BREAKING or getattr(ev, "slot", "") == "breaking") and age <= BREAKING_MINUTES \
            and (getattr(ev, "evidence", "") or "") in STRONG and market_moving(ev):
        return BREAKING
    if age <= JUST_IN_MINUTES:
        return JUST_IN
    return ""


def flag_for(ev: Any, tag: str) -> str:
    """A flag after a BREAKING / JUST IN tag only when the headline leads with exactly one country (brief.FLAGS): "China, US discuss tariffs"
    gets none."""
    if tag not in (BREAKING, JUST_IN):
        return ""
    lead = " ".join((getattr(ev, "headline", "") or getattr(ev, "title", "") or "").split()[:4])
    flags = {f for pattern, f in brief.FLAGS if pattern.search(lead)}
    return flags.pop() if len(flags) == 1 else ""


# --------------------------------------------------------------------------------------------------------------- words
def _protected(core: str) -> bool:
    """A name the reporting may never write mid-sentence but that is always a name: a country, capital or leader the desk knows."""
    from app.newsdesk import editorial

    return any(rx.fullmatch(core) for rx in editorial.COUNTRIES.values()) or core.lower() in clean.KNOWN_PEOPLE


def headline_case(head: str, reporting: str) -> str:
    """An outlet's Title Case Headline in sentence case: the same rule as the clean style (network.sentence_case: a word keeps its capital
    where the reporting capitalises it mid-sentence), with two repairs. A word it would lower-case takes the reporting's own spelling when the
    reporting uses it ("U-turn", "pre-war", never "u-Turn"), and a country, capital or known leader keeps its capital even when the reporting
    never names it again ("Riyadh"). Already sentence case: unchanged."""
    from app.newsdesk import network

    cased = network.sentence_case(head, reporting)
    if cased == head:
        return head
    forms: dict[str, list[str]] = {}
    for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b[\w’'-]+", reporting or ""):
        forms.setdefault(m.group(0).lower(), []).append(m.group(0))
    out = []
    for o, c in zip(head.split(), cased.split()):
        if o == c:
            out.append(o)
            continue
        m = re.match(r"^([^\w]*)([\w’'-]+?)([’']s)?([^\w]*)$", o)
        if not m:
            out.append(c)
            continue
        core = m.group(2)
        if _protected(core):
            out.append(o)
            continue
        seen = forms.get(core.lower())
        form = max(set(seen), key=seen.count) if seen else core.lower()
        out.append(f"{m.group(1)}{form}{m.group(3) or ''}{m.group(4)}")
    return clean._proper(" ".join(out))


def story_sentences(c: Any, case_source: str = "") -> list[str]:
    """The headline as a sentence (shortened past HEAD_MAX by dropping a trailing clause), then at most one more: the first of the copy's
    sentences that adds a figure the headline lacks, or any short one when the headline is very short. Usually one or two sentences."""
    from app.newsdesk import network

    cased = headline_case(network.shorten(c.headline, HEAD_MAX), case_source)
    short = network.Copy(**{**c.out(), "headline": cased})
    sents = clean.sentences_of(short, "")
    sents[0] = clean._sentence(cased)
    head, rest = sents[0], sents[1:]
    have = checks.numbers(head)
    for s in rest:
        if len(s) > SECOND_MAX:
            continue
        if checks.numbers(s) - have or len(head.split()) < 8:
            return [head, s]
    return [head]


def _sources(links: list[Any], outlets: list[str] | None) -> tuple[list[tuple[str, str]], list[str]]:
    srcs = [x for x in links if getattr(x, "kind", "article") != "pricechat"][:3]
    if srcs:
        return [(x.name, x.url) for x in srcs], [x.name for x in srcs]
    return [], list(outlets or [])[:3]


def footer(links: list[Any], outlets: list[str] | None) -> tuple[str, str, str]:
    """(HTML, plain with the signature, plain without it): the sources and @thepricechat on one quiet last line."""
    linked, names = _sources(links, outlets)
    sig = f'<a href="{HANDLE_URL}">{HANDLE}</a>'
    if linked:
        html = "🔗 " + " · ".join(f'<a href="{_esc(u, quote=True)}">{_esc(n)}</a>' for n, u in linked) + f" · {sig}"
    elif names:
        html = "🔗 " + _esc(" · ".join(names)) + f" · {sig}"
    else:
        return sig, HANDLE, ""
    plain = "Source: " + " · ".join(names)
    return html, f"{plain} · {HANDLE}", plain


def lead(c: Any, *, tag: str = "", flag: str = "", case_source: str = "") -> tuple[str, str]:
    """(HTML, plain): the tag, the flag, the story in one or two sentences with the cashtags the sentences establish."""
    story = clean.cashtag(" ".join(story_sentences(c, case_source)))[0]  # one tag per asset per post, at its first mention
    pre = " ".join(x for x in (tag, flag) if x)
    plain = " ".join(x for x in (pre, story) if x)
    html = " ".join(x for x in ((f"<b>{_esc(tag)}</b>" if tag else ""), flag, _esc(story)) if x)
    return html, plain


def telegram_caption(c: Any, links: list[Any], *, tag: str = "", flag: str = "", outlets: list[str] | None = None, case_source: str = "") -> tuple[str, str, str]:
    """(HTML, plain as sent, plain the checks read). Tag + story, a blank line, then the sources and @thepricechat."""
    body_html, body_plain = lead(c, tag=tag, flag=flag, case_source=case_source)
    f_html, f_plain, f_checked = footer(links, outlets)
    return f"{body_html}\n\n{f_html}", f"{body_plain}\n\n{f_plain}", (f"{body_plain}\n\n{f_checked}" if f_checked else body_plain)


def x_caption(c: Any, links: list[Any], *, tag: str = "", flag: str = "", with_links: bool = True, outlets: list[str] | None = None,
              case_source: str = "") -> str:
    """The same words on X: tag + story, then one source link (or the sources named). The second sentence goes first if it doesn't fit."""
    from app.newsdesk import network

    raw = story_sentences(c, case_source)
    pre = " ".join(x for x in (tag, flag) if x)
    srcs = [x for x in links if getattr(x, "kind", "article") != "pricechat"]
    if with_links and srcs:
        tail = f"{srcs[0].name}: {srcs[0].url}"
    else:
        named = [x.name for x in srcs][:2] or list(outlets or [])[:2]
        tail = f"Via {' · '.join(named)}" if named else ""
    for n in (len(raw), 1):
        story = clean.cashtag(" ".join(raw[:n]))[0]  # one tag per asset, at its first mention
        text = "\n\n".join(p for p in (" ".join(x for x in (pre, story) if x), tail) if p)
        if network.x_length(text) <= network.X_LIMIT:
            return text
    return " ".join(x for x in (pre, clean.cashtag(raw[0])[0]) if x)[:network.X_LIMIT]


def fallback_caption(ev: Any, source: str) -> tuple[str, str]:
    """(HTML, plain) when the package failed its checks: the outlet's own headline in sentence case and its source, nothing else."""
    from app.newsdesk import network

    head = clean._sentence(network.shorten(network.headline_case(getattr(ev, "headline", "") or getattr(ev, "title", "") or ""), HEAD_MAX))
    tag = tag_for(ev)
    pre = " ".join(x for x in (tag, flag_for(ev, tag)) if x)
    plain = " ".join(x for x in (pre, head) if x)
    html = " ".join(x for x in ((f"<b>{_esc(tag)}</b>" if tag else ""), flag_for(ev, tag), _esc(head)) if x)
    return (f"{html}\n\n🔗 {_esc(source)} · <a href=\"{HANDLE_URL}\">{HANDLE}</a>", f"{plain}\n\nSource: {source}")


def with_credit(html: str, plain: str, meta: dict[str, Any]) -> tuple[str, str]:
    """A photographer's credit the picture couldn't draw goes on its own line just above the footer."""
    c_html, c_plain = clean.caption_credit(meta)
    if not c_html:
        return html, plain
    h_body, _, h_foot = html.rpartition("\n\n")
    p_body, _, p_foot = plain.rpartition("\n\n")
    return f"{h_body}\n\n{c_html}\n\n{h_foot}", f"{p_body}\n\n{c_plain}\n\n{p_foot}"


# --------------------------------------------------------------------------------------------------------------- ranking
# Ranking only (never a bar): a story that makes people react, something happened with consequences
SURPRISE = re.compile(r"\b(hack(?:ed|s)?|exploit(?:ed)?|halt(?:s|ed)?|suspend(?:s|ed)?|freez(?:e|es)|froze|seiz(?:e|es|ed|ure)|arrest(?:s|ed)?|charged|indict\w*|"
                      r"bankrupt\w*|insolven\w*|defaults?|collaps\w*|resign\w*|steps? down|ousted|fired|bans?|banned|emergency|unexpected(?:ly)?|surprise\w*|"
                      r"record|all-time high|first time|liquidat\w+|depeg\w*|delist\w*|sanction\w*|ceasefire|invasion|approv(?:es|ed|al)|rejects?|rejected|"
                      r"lawsuit|sues|sued|guilty|convicted|shut(?:s)? down)\b", re.I)
FIGURE = re.compile(r"[$€£]\s?\d|\d+(?:\.\d+)?\s?%|\b\d[\d,.]*\s?(?:billion|million|bn|[BM])\b")
SURPRISE_MAX = 6.0
# Downranked against the bar too (like routine controversy): filler, previews, opinion, speculation, partnerships and how-tos
FILLER = re.compile(r"\b(price predictions?|price analysis|forecasts?|outlook|preview|what to (?:know|watch|expect)|here'?s (?:why|what|how)|explain\w*|"
                    r"analysts? (?:say|says|expect|see|think)|opinion|column|interview|podcast|webinar|guide|how to|tips|partners? with|partnership|"
                    r"teams? up|integrat(?:es|ion)|could|might|poised to|set to|eyes|mulls|weighs)\b", re.I)
FILLER_PENALTY = 8.0


def surprise_bonus(ev: Any) -> float:
    title = getattr(ev, "title", "") or ""
    hits = {m.group(0).lower() for m in SURPRISE.finditer(title)}
    return min(SURPRISE_MAX, (5.0 if hits else 0.0) + (2.0 if FIGURE.search(title) else 0.0))


def filler(ev: Any) -> str | None:
    """Why this headline reads as filler, or None. Only news: our own measured moves and data posts say what they measured."""
    if getattr(ev, "kind", "") not in ("news", "update"):
        return None
    m = FILLER.search(getattr(ev, "title", "") or "")
    return f"filler or speculation ({m.group(0).lower()})" if m else None


# --------------------------------------------------------------------------------------------------------------- pictures
MOVE = re.compile(r"\d+(?:\.\d+)?\s?%|\bliquidat\w*|\b(?:breaks?|broke|falls?|fell|drops?|dropped|slides?|slid|sinks?|sank|tops?|topped|crosses|crossed|"
                  r"climbs?|climbed|rises?|rose|jumps?|jumped|surges?|surged|plunges?|plunged|tumbles?|tumbled)\s+(?:below|above|under|past|through|to|beyond)\s+\$?\d|"
                  r"\b(?:record|all-time)\s+(?:high|low)s?\b", re.I)


# a commodity's own price moving: "Oil prices jump 5%", "Gold hits a record high", never "Saudi crude output jumps 26%"
COMMODITY_MOVE = re.compile(r"\b(?:oil|crude|brent|wti|gold|silver)(?:\s+(?:prices?|futures))?\s+(?:hits?|tops?|breaks?|"
                            r"(?:falls?|fell|drops?|dropped|slides?|slid|sinks?|sank|climbs?|climbed|rises?|rose|jumps?|jumped|surges?|surged|"
                            r"plunges?|plunged|tumbles?|tumbled|gains?|gained|rall(?:y|ies|ied)|soars?|soared))\b", re.I)


def price_story(ev: Any) -> tuple[str, str, str] | None:
    """(Yahoo symbol, name, label) when the story is about this asset's price: the headline leads with the asset (its first four words
    establish it) and reports a move (a commodity: its own price moving, COMMODITY_MOVE), or the Viral Engine measured that asset reacting.
    Macro stories (the S&P 500 stand-in) only on a measured reaction. Else None: a chart beside a story implies the story is about that price."""
    head = f"{getattr(ev, 'headline', '') or ''}"
    title = f"{getattr(ev, 'title', '') or ''}"
    for text in (head, title):
        subject = clean.chart_subject(text)
        if subject is None:
            continue
        leading = clean.chart_subject(" ".join(text.split()[:4]))
        commodity = subject[0] in ("BZ=F", "GC=F", "SI=F")
        moved = COMMODITY_MOVE.search(text) if commodity else MOVE.search(text)
        if subject[0] != "^GSPC" and leading is not None and leading[0] == subject[0] and moved:
            return subject
        measured = ((getattr(ev, "facts", None) or {}).get("viral") or {}).get("reactions") or []
        if any(isinstance(r, dict) and str(r.get("symbol")) == subject[0] for r in measured):
            return subject
    return None


def text_only() -> tuple[bytes, dict[str, Any]]:
    return b"", {"style": STYLE, "kind": "text", "layout": "text"}


async def _person_photo(ev: Any, used: set[str], transport: Any) -> tuple[Any, dict[str, Any]] | None:
    """A licensed photo of the public figure the story is about (clean.person_subject: never a private person, never on violence; a
    controversy only in a neutral photo). Only the person: no company, place or scene stand-in."""
    import httpx

    from app.newsdesk import photos, visual

    person = clean.person_subject(ev)
    if not person:
        return None
    # the story is about them: they lead the headline ("Powell says...", "Musk's xAI..."), not a meeting they are mentioned in
    lead = {re.sub(r"[’']s$", "", w).strip(",.:;").lower() for w in (getattr(ev, "headline", "") or getattr(ev, "title", "") or "").split()[:4]}
    names = {person.split()[-1].lower(), *(k for k, v in clean.KNOWN_PEOPLE.items() if v == person)}
    if not lead & names:
        return None
    sensitive = visual.is_sensitive(ev)
    async with httpx.AsyncClient(transport=transport) as client:
        try:
            found = await photos.discover(client, [person, f"{person} portrait"], [person], used=used, sensitive=False, panel=False)
        except Exception as e:  # noqa: BLE001
            logger.info("wire: photo discovery failed (%s)", type(e).__name__)
            return None
        if sensitive:
            found = [p for p in found if not clean.NOT_NEUTRAL.search(f"{p.title} {p.description} {' '.join(p.tags)}")]
        for p in found[:3]:
            img = await photos.fetch(client, p, min_width=photos.MIN_WIDTH)
            if img is None:
                continue
            meta = p.to_meta() if hasattr(p, "to_meta") else {"url": p.url}
            return img, {"url": p.url, "page": meta.get("page", ""), "title": meta.get("title", ""), "credit": p.credit,
                         "licence": meta.get("licence", "") or getattr(p, "licence", ""), "photo": meta, "person": person}
    return None


async def picture(ev: Any, *, used: set[str] | None = None, transport: Any = None, market: Any = None, now: datetime | None = None,
                  db: Any = None) -> tuple[bytes, dict[str, Any]]:
    """The wire picture, or none: (b"", {"kind": "text"}) is a text-only post. Stored on the event, so X does exactly the same."""
    from app.newsdesk import visual

    now = now or datetime.now(timezone.utc)
    f = getattr(ev, "facts", None) or {}
    used = used or set()
    base = {"style": STYLE}
    kind = getattr(ev, "kind", "")
    if kind == "data" and f.get("data"):
        from app.newsdesk import datacards

        img, meta = datacards.data_card(f["data"], when=getattr(ev, "released_at", None) or now, mad=False)
        return img, {**meta, **base, "kind": "data", "layout": "data"}
    if kind in ("market", "recap"):  # a measured move or liquidation flush: the desk's own checked series
        chosen = visual.plan(ev, [])
        checked = await visual._chart_for(ev, chosen, market, now)  # raises NumbersRefused when the numbers don't check out
        if checked is None:
            return text_only()
        pts = [tuple(p) for p in f.get("points") or []]
        if len(pts) < 10:
            coin = str(f.get("coin") or (f.get("tickers") or [""])[0] or "").lstrip("$").upper()
            pts = await visual.candles_for(coin, market) if coin else []
        bars = clean.line_bars(pts)[-clean.CHART_BARS:]
        if len(bars) < 2:
            return text_only()
        label = f"{f.get('name') or f.get('coin') or ''} · {str(f.get('window') or '24h')} · {f.get('data_source') or 'Hyperliquid'}"
        img, meta = clean.single_chart(bars, label)
        return img, {**base, **meta, "number_check": checked[1].get("number_check"), "kind": "chart", "bars": bars, "label": label}
    subject = price_story(ev)
    if subject is not None:
        import httpx

        try:
            async with httpx.AsyncClient(transport=transport) as client:
                bars = await clean.fetch_bars(client, subject[0], now)
        except Exception as e:  # noqa: BLE001 - no chart, then
            logger.info("wire: no candles for %s (%s)", subject[0], type(e).__name__)
            bars = []
        if len(bars) >= 20:
            label = f"{subject[2]} · 15m · Yahoo Finance"
            img, meta = clean.single_chart(bars, label)
            return img, {**base, **meta, "kind": "chart", "bars": bars, "label": label, "symbol": subject[0]}
    got = await _person_photo(ev, used, transport)
    if got is not None:
        photo, pmeta = got
        credit = clean.image_credit(pmeta)
        img, meta = clean.single_photo(photo, credit)
        return img, {**base, **pmeta, **meta, "kind": "photo", "credit_line": credit}
    return text_only()


async def for_event(ev: Any, stored: dict[str, Any], **kw: Any) -> tuple[bytes, dict[str, Any]]:
    """The same wire picture again (X follows Telegram), or a new one."""
    if stored.get("style") == STYLE:
        layout = stored.get("layout")
        if layout == "text":
            return b"", stored
        if layout in ("photo", "chart"):
            try:
                again = await clean.redraw(ev, stored, transport=kw.get("transport"), db=kw.get("db"), now=kw.get("now"))
            except Exception as e:  # noqa: BLE001 - drawn afresh below
                logger.info("wire: couldn't redraw %s (%s)", getattr(ev, "id", None), type(e).__name__)
                again = None
            if again is not None:
                return again
    return await picture(ev, used=kw.get("used"), transport=kw.get("transport"), market=kw.get("market"), now=kw.get("now"), db=kw.get("db"))
