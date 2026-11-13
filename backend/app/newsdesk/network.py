"""Price Chat News: what every post says and where it links (owner, 2026-11-11: "a premium, fully automated global news network").

One package per released event, written once (when Telegram composes it) and reused by X, so both platforms carry the same story:

- the words: a short headline, one or two sentences of context with the story's own figures, what it means for markets when a source
  says so, and sometimes a question for the replies. Written by the model (Anthropic, settings.newsdesk_model) when a key exists, else by
  the desk from the outlets' own sentences. Either way `verify_copy` runs on exactly what is posted: every figure must be one a source
  gave, every name must be one a source used, no hype words, no investment advice, no claim of exclusive reporting.
- the links: 1-3 direct article links from distinct outlets (never a homepage or a section front; tracking parameters stripped), only from
  stories that are the same story (a newsletter round-up of three unrelated stories is never a source), plus a Price Chat article link when
  one exists (facts.article_url). Market moves link the data page they were measured on.
- the byline: PRICE CHAT NEWS, with an honest verification line: "Verified across 3 outlets", "Official statement", "Measured on our price
  data", or "Reported by CNBC" for a single attributed outlet. Nothing here ever says or implies the desk reported it first or alone.

X: the post is a standalone caption (headline, context, a question now and then, the article link). A link makes an X post cost $0.20
instead of $0.015 (pay-per-use pricing); the owner asked for links on every post (2026-11-11), so `x_links` is on by default and Admin can
turn it off (state.x_links = false) to go back to named sources only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.newsdesk import checks

logger = logging.getLogger(__name__)

BYLINE = "PRICE CHAT NEWS"
BRAND = "🥝 Price Chat News"
X_LIMIT = 280
X_URL_WEIGHT = 23  # X counts every link as 23 characters (t.co)
TG_CAPTION_LIMIT = 1024  # a photo caption on Telegram
MAX_LINKS = 3

# --- links -------------------------------------------------------------------------------------------------------------

TRACKING = re.compile(r"^(utm_\w+|at_\w+|maca|traffic_source|cmpid|ref|src|fbclid|gclid|mc_\w+|ncid|taid|cid|guccounter|smid|soc_\w+|ito|rss|"
                      r"partner|feed|output|outputtype|mod|sref|cmp|xtor|ns_\w+|ocid|sr_share|share|s|si|igshid)$", re.I)
ARTICLE_EXT = re.compile(r"\.(s?html?|php|aspx?)$", re.I)
DATA_HOSTS = ("finance.yahoo.com", "app.hyperliquid.xyz", "hyperliquid.xyz", "trade.xyz", "www.coinglass.com", "coinglass.com")
SECTION_WORDS = {"news", "markets", "market", "business", "world", "economy", "economics", "politics", "crypto", "tech", "technology", "latest",
                 "home", "index", "rss", "feed", "feeds", "live", "video", "videos", "topics", "topic", "section", "category", "tag", "tags"}


def clean_url(url: str) -> str | None:
    """https only, tracking parameters and fragments dropped."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return None
    if p.scheme != "https" or not p.netloc:
        return None
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)])
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, query, ""))


def is_article(url: str) -> bool:
    """A link to one story, not a homepage or a section front: /2026/11/10/business/nvidia-wachtell.html is, /markets/ is not."""
    p = urlsplit(url)
    segs = [s for s in p.path.split("/") if s]
    if not segs or re.search(r"\.(xml|rss|atom)$", p.path, re.I) or any(s.lower() in ("rss", "feed", "feeds", "xml", "atom") or s.lower().startswith(("rss-", "rss_"))
                                                                         for s in segs) or p.netloc.lower().startswith(("rss.", "feeds.")):
        return False  # a feed, not a story
    last = segs[-1]
    if ARTICLE_EXT.search(last) and len(segs) >= 1 and last.lower().rsplit(".", 1)[0] not in SECTION_WORDS:
        return True
    if any(s.count("-") >= 2 for s in segs):  # a slug: words-joined-by-dashes
        return True
    if any(re.fullmatch(r"\d{5,}", s) or re.fullmatch(r"[a-z0-9]{10,}", s) and re.search(r"\d", s) for s in segs):  # an id (DW a-79829019 style is a slug)
        return True
    if re.search(r"/20\d\d/\d{1,2}/", p.path) and len(segs) >= 4:
        return True
    return False


def is_data_page(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return host in DATA_HOSTS and len([s for s in urlsplit(url).path.split("/") if s]) >= 1


PAYWALLED = {"new york times", "wall street journal", "financial times", "bloomberg", "barron's", "the information", "the economist", "the athletic",
             "washington post", "the times", "nikkei", "the telegraph"}


@dataclass
class Link:
    name: str
    url: str
    kind: str = "article"  # article | data | pricechat

    def out(self) -> dict[str, str]:
        return asdict(self)


def related(lead_title: str, other_title: str) -> bool:
    """Two headlines are the same story: they share a distinctive name (Nvidia, Wachtell) or most of their words."""
    if checks.names(lead_title) & checks.names(other_title):
        return True
    return checks.similarity(checks.fingerprint(lead_title), checks.fingerprint(other_title)) >= 0.34


def links_for(ev: Any, rows: list[Any] | None = None, *, limit: int = MAX_LINKS) -> list[Link]:
    """1-3 direct links from distinct outlets, the lead story's first. rows: the stories behind the event (NdStory), when at hand; they
    carry every outlet's own link and title, so an outlet that isn't the same story is left out."""
    f = ev.facts or {}
    lead_title = getattr(ev, "title", "") or ""
    cands: list[tuple[str, str, str]] = []  # (name, url, title)
    for r in rows or []:
        cands.append((str(getattr(r, "source", "") or ""), str(getattr(r, "link", "") or ""), str(getattr(r, "title", "") or "")))
    for s in ([] if rows else ev.sources or []):  # the stories themselves, when at hand, carry each outlet's own title to compare
        cands.append((str(s.get("name") or ""), str(s.get("url") or ""), str(s.get("title") or lead_title)))
    lead = cands[:1] if rows else []
    cands = lead + sorted(cands[len(lead):], key=lambda c: c[0].lower() in PAYWALLED)  # free to read first, after the lead story
    out: list[Link] = []
    seen_names: set[str] = set()
    seen_urls: set[str] = set()
    market = getattr(ev, "kind", "") in ("market", "recap") or bool(f.get("symbol"))
    for name, raw, title in cands:
        url = clean_url(raw)
        if not name or not url or name.lower() in seen_names or url in seen_urls:
            continue
        if is_article(url):
            kind = "article"
        elif market and is_data_page(url):
            kind = "data"
        else:
            continue  # a homepage, a section front, a feed: not where the reader can check the story
        if kind == "article" and title and lead_title and title != lead_title and not related(lead_title, title):
            continue  # never link a different story as a source
        seen_names.add(name.lower())
        seen_urls.add(url)
        out.append(Link(name, url, kind))
        if len(out) >= limit:
            break
    own = clean_url(str(f.get("article_url") or ""))
    if own:
        out.append(Link("Price Chat", own, "pricechat"))
    return out


# --- verification line -------------------------------------------------------------------------------------------------


def outlets_of(ev: Any) -> list[str]:
    f = ev.facts or {}
    names = [str(n) for n in (f.get("outlets") or []) if n]
    names += [str(s.get("name")) for s in ev.sources or [] if s.get("name")]
    return list(dict.fromkeys(n for n in names if n and n.lower() not in ("price chat", "public exchange feeds")))


def verification(ev: Any) -> str:
    """What the desk did to stand behind the story, in words that never claim it was ours first."""
    evidence = getattr(ev, "evidence", "") or ""
    outs = outlets_of(ev)
    kind = getattr(ev, "kind", "")
    if kind in ("market", "recap") or evidence == "price data":
        src = str((ev.facts or {}).get("data_source") or (outs[0] if outs else "exchange data"))
        return f"Measured on our price data ({src})"
    if evidence == "official":
        return "From an official statement"
    if len(outs) >= 2:
        return f"Verified across {len(outs)} outlets"
    if outs:
        return f"Reported by {outs[0]}"
    return "Price Chat News"


def is_original(ev: Any) -> bool:
    """A story the desk verified itself (two or more independent outlets, an official source, or our own price data): it leads under the
    PRICE CHAT NEWS byline. A single attributed outlet's story stays credited to that outlet in the byline."""
    return (getattr(ev, "evidence", "") in ("official", "two outlets", "price data") or getattr(ev, "kind", "") in ("market", "recap")) and len(outlets_of(ev)) != 1 \
        or getattr(ev, "evidence", "") == "official"


# --- round-ups: several unrelated stories under one title ----------------------------------------------------------------

ROUNDUP = re.compile(r"\b(briefing|morning (minute|bid|report)|evening (briefing|report)|five things|\d+ things|markets? wrap|week ahead|the week in|"
                     r"rundown|round-?up|recap|newsletter|daily debrief|what we're watching|what's moving|stocks to watch|in brief|live updates?|"
                     r"the big take|need to know)\b", re.I)
LISTY = re.compile(r"^[^,;:]{2,40},\s*[^,;:]{2,40}(?:,\s*[^,;:]{2,40})*\s*(?:&|\band\b|\+)\s*[^,;:]{2,40}$", re.I)
VERB = re.compile(r"\b(is|are|was|were|has|have|had|will|would|says?|said|to|hits?|falls?|rises?|jumps?|drops?|sinks?|surges?|plunges?|soars?|slides?|"
                  r"cuts?|raises?|hikes?|holds?|opens?|closes?|launch(?:es)?|files?|sues?|wins?|loses?|warns?|seeks?|plans?|agrees?|signs?|"
                  r"hires?|fires?|resigns?|buys?|sells?|bans?|blocks?|names?|sets?|gets?|makes?|takes?|leaves?|faces?|begins?|ends?|starts?|"
                  r"reports?|shows?|calls?|tells?|sees?|eyes?|weighs?|mulls?|tops?|beats?|misses?|halts?|resumes?|freezes?|backs?|rejects?)\b", re.I)


def is_roundup(title: str) -> bool:
    """A newsletter's round-up of unrelated stories ("Hot Inflation, AI Selloff & Anthropic IPO"): one post must be one story."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    if ROUNDUP.search(t):
        return True
    return bool(LISTY.match(t)) and not VERB.search(t)


# --- the words -------------------------------------------------------------------------------------------------------------


@dataclass
class Copy:
    headline: str  # sentence case, short and specific (the post's first line)
    short: str  # the picture's headline, at most SHORT characters
    context: str = ""  # one or two sentences: what happened, who, the figure
    stakes: str = ""  # what it means for markets, only when a source or our data says so
    question: str = ""  # a question for the replies, now and then
    writer: str = "desk"  # model | desk
    rejected: list[str] = field(default_factory=list)  # why a model draft was refused (Admin)
    hooks: list[str] = field(default_factory=list)  # the writer's five alternative headlines (app/newsdesk/hooks ranks and fact-checks them)

    def out(self) -> dict[str, Any]:
        return asdict(self)


SHORT = 64
HEADLINE_MAX = 96
HYPE = re.compile(r"\b(exclusive|scoop|we can reveal|our sources|sources tell|we've learned|we have learned|first reported by|you won'?t believe|"
                  r"shocking|insane|mind-?blowing|jaw-?dropping|destroy(?:s|ed)?|obliterat\w+|bloodbath|to the moon|moon(?:ing)?|rekt|"
                  r"game[- ]changer|must[- ]see|this is huge|guaranteed|massive news)\b", re.I)
LABELS = re.compile(r"^(BREAKING|JUST IN|ALERT|UPDATE|EXCLUSIVE|WATCH|LIVE)\s*[:\-–—|]\s*", re.I)
CAPWORD = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z0-9&'’.-]{2,})")
NAME_OK = {"Price", "Chat", "News", "Why", "What", "Who", "How", "Will", "Can", "Is", "Are", "Does", "Do", "Should", "The", "This", "That",
           "Markets", "Traders", "Investors", "Wall", "Street", "Bitcoin", "Ethereum", "Crypto", "Oil", "Gold", "Silver", "Fed", "US", "U.S.",
           "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December", "Hyperliquid", "Yahoo", "Finance", "And", "But", "Or", "If", "Its",
           "AI", "CEO", "IPO", "ETF", "UTC", "GDP", "CPI", "S&P", "Nasdaq", "Dow", "Brent", "WTI", "In", "On", "At", "For", "As", "After",
           "U.S", "UK", "U.K", "EU", "UN", "President", "Prime", "Minister", "Chairman", "Chair", "Governor", "Secretary", "Senator", "Chief", "Executive"}


def _sentences(text: str) -> list[str]:
    from app.newsdesk.telegram import sentences

    return sentences(text or "")


def headline_case(title: str) -> str:
    """Sentence case from a shouted or title-cased headline, keeping names, tickers and figures as written in `title` when it isn't
    all capitals."""
    from app.newsdesk import visual

    t = re.sub(r"^[^\w$]+", "", title or "").strip()
    t = LABELS.sub("", t)
    if not t:
        return ""
    if not t.isupper():
        return t[:1].upper() + t[1:]
    words = t.split()
    out = []
    for i, w in enumerate(words):
        core = re.sub(r"[^\w$.&'’-]", "", w)
        if core.upper() in visual.ACRONYMS or re.search(r"[\d&]", core) or (len(core) <= 3 and core.isalpha() and core.lower() not in visual.COMMON):
            out.append(w)
        else:
            low = w.lower()
            out.append(low[:1].upper() + low[1:] if i == 0 else low)
    return " ".join(out)


def sentence_case(head: str, source_text: str) -> str:
    """A writer's Title Case Headline In Sentence Case: a word keeps its capital only where the reporting capitalises it mid-sentence
    (a name), or it is an acronym or ticker (NEAR, USDC, CEO). Already sentence case: unchanged."""
    words = head.split()
    long = [w for w in words[1:] if len(re.sub(r"\W", "", w)) > 3]
    if not long or sum(1 for w in long if w[:1].isupper()) / len(long) < 0.6:
        return head
    out = [words[0]]
    for w in words[1:]:
        core = re.sub(r"[’']s$", "", re.sub(r"[^\w’'&.-]", "", w))
        named = core and (re.search(rf"(?<![.!?]\s)(?<!^)\b{re.escape(core)}\b", source_text or "")
                          or (re.search(rf"\b{re.escape(core)}\b", source_text or "") and not re.search(rf"\b{re.escape(core.lower())}\b", source_text or "")))
        keep = not w[:1].isupper() or core.isupper() or re.search(r"[\d&]", core) or named
        out.append(w if keep else w[:1].lower() + w[1:])
    return " ".join(out)


def shorten(text: str, limit: int) -> str:
    """Short forms ($16 billion -> $16B), then trailing clauses dropped (as visual.trim), never a word added."""
    from app.newsdesk import visual

    t = re.sub(r"\$\s?(\d[\d.,]*)\s*(billion|bn)\b", r"$\1B", text or "", flags=re.I)
    t = re.sub(r"\$\s?(\d[\d.,]*)\s*(million|mn)\b", r"$\1M", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+[-–—:|]\s+(here'?s|here is|what|how|why|everything|all you)\b.*$", "", t, flags=re.I)  # an explainer's tail is not the news
    if len(t) <= limit:
        return t
    upper = t.upper()
    cut = visual.trim(upper, limit)
    return t[:len(cut)].rstrip(" ,:;-") if len(cut) < len(t) else t


QUESTIONS: dict[str, tuple[str, ...]] = {
    "incident": ("Would this change where you keep your coins?", "Is self-custody the real lesson here?"),
    "hack": ("Would this change where you keep your coins?", "Is self-custody the real lesson here?"),
    "geopolitics": ("Are markets pricing this in yet?", "Who gains if this escalates?"),
    "macro": ("Is this priced in?", "What moves next: rates, stocks or crypto?"),
    "rates": ("Is this priced in?",),
    "corporate": ("Does this change your view of the stock?", "Overreaction or not enough?"),
    "controversy": ("Does this change your view of the company?",),
    "regulation": ("Good for the market or not?", "Who wins from this rule?"),
    "market": ("Dip or the start of something bigger?", "Who's buying this move?"),
    "flows": ("Smart money or late money?",),
}


def question_for(ev: Any, desk: str) -> str:
    """About every other post, a question that invites replies. Questions claim nothing: never a prediction dressed as fact."""
    bank = QUESTIONS.get(desk) or QUESTIONS.get(getattr(ev, "topic", "") or "") or ()
    if not bank:
        return ""
    h = int(hashlib.sha1(str(getattr(ev, "key", "") or getattr(ev, "id", "")).encode()).hexdigest(), 16)
    if h % 2:
        return ""
    return bank[(h >> 4) % len(bank)]


def desk_of(ev: Any) -> str:
    """The story's type (incident, market, geopolitics, regulation, corporate, flows, macro, news), as the picture planner sees it."""
    from app.newsdesk import visual

    try:
        return visual.story_type(ev)
    except Exception:  # noqa: BLE001 - a story the planner can't place still gets words
        return getattr(ev, "topic", "") or "news"


def _desk_context(ev: Any, head: str) -> str:
    f = ev.facts or {}
    kept: list[str] = []
    seen = [checks.fingerprint(head), checks.fingerprint(getattr(ev, "title", "") or "")]
    for line in _sentences(getattr(ev, "summary", "") or ""):
        mark = checks.fingerprint(line)
        if len(line) < 25 or any(checks.similarity(mark, s) >= 0.5 for s in seen):
            continue
        if len(" ".join([*kept, line])) > 240:
            break
        kept.append(line)
        seen.append(mark)
        if len(kept) >= 2:
            break
    if not kept and f.get("price_check") and getattr(ev, "kind", "") == "news":
        kept.append(f"Our prices: {f['price_check']}.")
    return " ".join(kept)


def _desk_stakes(ev: Any) -> str:
    """Market consequences only from our own measured data: the story's verified move, never a guess."""
    f = ev.facts or {}
    tag = str(f.get("price_tag") or "").strip()
    if tag:
        return tag
    return ""


def desk_copy(ev: Any) -> Copy:
    from app.newsdesk import visual

    raw = getattr(ev, "headline", "") or getattr(ev, "title", "")
    # a shouted headline takes the outlet's own capitals back (Wachtell, C.E.O.): never lower-case a name the desk can't check
    head = visual.display_headline(ev) if raw.isupper() and getattr(ev, "title", "") else headline_case(raw)
    if getattr(ev, "kind", "") in ("market", "data"):  # our own titles ("S&P 500 Falls 3.8% in 24 Hours"); an outlet's keeps its names as written
        head = sentence_case(head, getattr(ev, "summary", "") or "")
    head = shorten(head, HEADLINE_MAX)
    short = shorten(head, SHORT)
    return Copy(headline=head, short=short, context=_desk_context(ev, head), stakes=_desk_stakes(ev), question=question_for(ev, desk_of(ev)))


def source_text_of(ev: Any, rows: list[Any] | None = None) -> str:
    f = ev.facts or {}
    parts = [str(f.get("told") or ""), getattr(ev, "title", "") or "", getattr(ev, "headline", "") or "", getattr(ev, "summary", "") or "",
             str(f.get("price_tag") or ""), str(f.get("price_check") or ""), str(f.get("stat") or ""), "24 hours"]
    parts += [str(x) for x in ((f.get("viral") or {}).get("measured") or {}).values()]  # our measured market reaction (app/newsdesk/money)
    parts.append(str((f.get("data") or {}).get("told") or ""))  # a data post's figures, all measured (app/newsdesk/datapost)
    parts.append(str(f.get("kicker") or ""))  # "UPDATE · first reported 10 Nov": the date of our own first post
    for r in rows or []:
        parts.append(f"{getattr(r, 'title', '')} {getattr(r, 'summary', '') or ''}")
    return " ".join(parts)


def verify_copy(c: Copy, source_text: str) -> list[str]:
    """Why these words can't go out (empty: they can). The same rules for the model and the desk."""
    problems = []
    body = " ".join([c.headline, c.short, c.context, c.stakes, c.question])
    extra = checks.numbers(body) - checks.numbers(source_text)
    if extra:
        problems.append(f"figures no source gave: {', '.join(sorted(extra))}")
    low = source_text.lower()
    def bare(w: str) -> str:  # "Huang's" is Huang: a possessive with either apostrophe is the same name
        return re.sub(r"[’']s$", "", w).rstrip(".’'")

    unknown = sorted({bare(m.group(1)) for m in CAPWORD.finditer(" ".join([c.context, c.stakes]))
                      if bare(m.group(1)) not in NAME_OK and bare(m.group(1)).lower() not in low})
    if unknown:
        problems.append(f"names no source used: {', '.join(unknown[:5])}")
    if HYPE.search(body):
        problems.append(f"hype or a claim of exclusivity: {HYPE.search(body).group(0)}")
    if checks.FORBIDDEN.search(body):
        problems.append("investment advice or a promise")
    if checks.URLISH.search(body):
        problems.append("a web address in the words")
    if not c.headline or len(c.headline) > HEADLINE_MAX + 10:
        problems.append(f"headline length {len(c.headline)}")
    if len(c.short) > SHORT + 6:
        problems.append(f"picture headline over {SHORT} characters")
    if len(c.context) > 300:
        problems.append("context over 300 characters")
    if c.question and (not c.question.endswith("?") or len(c.question) > 100):
        problems.append("the question isn't a short question")
    return problems


MODEL_PROMPT = (
    "You are the copy editor of Price Chat News, a markets news service on X and Telegram. From the reporting below, write JSON with keys:\n"
    "headline: under 80 characters, sentence case, specific and punchy, leads with the consequence or the contradiction when the reporting "
    "states one; no emoji, no label words (BREAKING, JUST IN), no clickbait.\n"
    "short: the same story in at most 60 characters, for the picture.\n"
    "context: one or two sentences (max 230 characters) with the most telling figures and who is involved.\n"
    "stakes: one sentence (max 140 characters) on market consequences, winners or losers, ONLY if the reporting states them; else \"\".\n"
    "question: a short neutral question that invites debate (max 80 characters), or \"\".\n"
    "hooks: a list of five alternative headlines (each under 90 characters) for the same story: short, provocative, specific, each led by "
    "a different angle (the figure, the actor, the conflict, the contradiction, the consequence); every word must be supported by the "
    "reporting: no new name, number, ticker, accusation, legal action, cause or quote.\n"
    "Rules: use only facts, names and figures that appear in the reporting, exactly as given. Never invent a figure, a cause, a reaction or a "
    "quote. Never say exclusive, first or that Price Chat reported it; the outlets reported it. No advice to buy or sell. No outrage words. "
    "Reply with the JSON only.")


async def model_copy(settings: Any, ev: Any, rows: list[Any], transport: Any = None) -> Copy | None:
    from app.newsdesk import writer
    from app.security import credentials

    key = credentials.resolve("anthropic_api_key", settings)
    model = getattr(settings, "newsdesk_model", "")
    if not key or not model:
        return None
    told = reporting(ev, rows)
    from app.newsdesk import viral

    body = {"model": model, "max_tokens": 500, "system": viral.prompt(ev), "messages": [{"role": "user", "content": told}]}
    try:
        async with httpx.AsyncClient(timeout=30, transport=transport) as client:
            r = await client.post(writer.ANTHROPIC_URL, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body)
        if r.status_code != 200:
            logger.info("newsdesk network: writer HTTP %s", r.status_code)
            return None
        text = "".join(b.get("text", "") for b in r.json().get("content") or [] if isinstance(b, dict) and b.get("type") == "text").strip()
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text))
        c = Copy(headline=str(data.get("headline") or "").strip(), short=str(data.get("short") or "").strip(), context=str(data.get("context") or "").strip(),
                 stakes=str(data.get("stakes") or "").strip(), question=str(data.get("question") or "").strip(), writer="model",
                 hooks=[str(h).strip() for h in (data.get("hooks") or []) if isinstance(h, str) and str(h).strip()][:5])
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as e:
        logger.info("newsdesk network: writer %s", type(e).__name__)
        return None
    c.short = c.short or shorten(c.headline, SHORT)
    return c


def reporting(ev: Any, rows: list[Any] | None) -> str:
    """The reporting a model writes from: each outlet's headline and summary, and our price data."""
    told = "\n\n".join(f"{getattr(x, 'source', '')}: {getattr(x, 'title', '')}\n{(getattr(x, 'summary', '') or '')[:700]}" for x in (rows or [])[:3]) \
        or f"{getattr(ev, 'title', '')}\n{getattr(ev, 'summary', '')}"
    f = ev.facts or {}
    if f.get("price_tag") or f.get("price_check"):
        told += f"\n\nOur price data: {f.get('price_tag') or ''} {f.get('price_check') or ''}"
    from app.newsdesk import viral

    return told + viral.reporting_extra(ev)


def copy_request(ev: Any, rows: list[Any]) -> dict[str, str]:
    """What the GPU PC's local model is asked (app/newsdesk/render_queue sends it with the picture job)."""
    from app.newsdesk import viral

    return {"system": viral.prompt(ev), "user": reporting(ev, rows)}


def local_copy(ev: Any) -> Copy | None:
    got = (ev.facts or {}).get("local_copy")
    if not isinstance(got, dict) or not got.get("headline"):
        return None
    c = Copy(headline=str(got["headline"]).strip(), short=str(got.get("short") or "").strip(), context=str(got.get("context") or "").strip(),
             stakes=str(got.get("stakes") or "").strip(), question=str(got.get("question") or "").strip(), writer="local",
             hooks=[str(h).strip() for h in (got.get("hooks") or []) if isinstance(h, str) and str(h).strip()][:5])
    c.short = c.short or shorten(c.headline, SHORT)
    return c


async def write(settings: Any, ev: Any, rows: list[Any] | None = None, transport: Any = None) -> Copy:
    """The words (`draft`), then the media engine's pass (`polish`): the strongest verified hook as the headline, a debate question only
    when the story invites one, one punchy sentence of context."""
    c = await draft(settings, ev, rows, transport)
    try:
        return polish(ev, c, rows)
    except Exception as e:  # noqa: BLE001 - the checked draft stands
        logger.info("newsdesk network: no polish for event %s (%s)", getattr(ev, "id", None), type(e).__name__)
        return c


CONTEXT_MAX = 160  # owner, 2026-11-13: short, punchy Telegram captions: one sentence of context
WRITER_MARGIN = 8.0  # a hook replaces a checked writer's headline only when it ranks this many points higher


def polish(ev: Any, c: Copy, rows: list[Any] | None) -> Copy:
    """Only for stories the scorecard saw (facts["editorial"]). The five hooks (the writer's, else the desk's) are ranked for engagement and
    accuracy (app/newsdesk/hooks); the winner replaces the headline only if the whole copy still passes every check. Hooks that fail are
    recorded with why, never repaired. The debate question replaces the writer's unless the writer's passes the question rules."""
    from app.newsdesk import hooks, viral

    f = getattr(ev, "facts", None) or {}
    card = f.get("editorial")
    if not card or getattr(ev, "kind", "") not in ("news", "update"):
        return c
    src = source_text_of(ev, rows)
    plan = hooks.plan(ev, rows, model_hooks=c.hooks or None, writer_headline=c.headline)
    best = plan.get("chosen") or ""
    picked = str((f.get("hooks") or {}).get("admin_choice") or "")
    if picked:  # the owner's pick from Admin: it goes through every check below and every gate after, like any other headline
        plan["admin_choice"] = picked
        best = picked
    mine = None if picked else next((h for h in plan.get("hooks") or [] if h.get("text") == c.headline), None)
    top = next((h for h in plan.get("hooks") or [] if h.get("ok")), None)
    if not picked and mine and mine.get("ok") and top and float(mine["total"]) >= float(top["total"]) - WRITER_MARGIN:
        best = c.headline  # the checked draft's own headline stands unless a hook is clearly stronger
        plan["chosen"], plan["chosen_strategy"] = best, mine.get("strategy") or "writer"
        plan["why"] = f"the writer's headline (total {mine['total']}) is within {WRITER_MARGIN:g} of the best hook ({top['total']})"
    out = Copy(**{**asdict(c)})
    if best and best != c.headline:
        trial = Copy(**{**asdict(c), "headline": best, "short": shorten(best, SHORT)})
        problems = verify_copy(trial, src) + viral.draft_problems(ev, trial, rows)
        if not problems:
            out = trial
        else:
            plan["kept_writer"] = f"the winning hook failed the copy checks: {problems[0]}"
    q, why = hooks.debate_question(ev, card, writer_question=c.question, source_text=src)
    out.question = q
    plan["question"], plan["question_why"] = q, why
    if len(out.context) > CONTEXT_MAX:
        first = (_sentences(out.context) or [out.context])[0]
        out.context = first if len(first) <= CONTEXT_MAX + 60 else ""
    ev.facts = {**f, "hooks": plan}
    return out


async def draft(settings: Any, ev: Any, rows: list[Any] | None = None, transport: Any = None) -> Copy:
    """The words, from the first writer whose draft passes every check: the GPU PC's local model (written with the picture, no API cost,
    fact-checked there by a second pass), then the Anthropic model when a key exists, then the desk's own from the outlets' sentences
    (checked too: a part that fails is dropped)."""
    from app.newsdesk import viral

    src = source_text_of(ev, rows)
    rejected: list[str] = []

    def check(c: Copy) -> list[str]:
        return verify_copy(c, src) + viral.draft_problems(ev, c, rows)

    named = " ".join([getattr(ev, "summary", "") or "", *[getattr(r, "summary", "") or "" for r in rows or []]])  # sentence-case text: where names show
    drafted = local_copy(ev)
    if drafted is not None:
        drafted.headline = sentence_case(drafted.headline, named)
        problems = check(drafted)
        if not problems:
            return drafted
        rejected = [f"local: {p}" for p in problems]
        logger.info("newsdesk network: local copy refused for event %s: %s", getattr(ev, "id", None), "; ".join(problems))
    if settings is not None and getattr(ev, "kind", "") not in ("recap",):
        drafted = await model_copy(settings, ev, rows or [], transport)
        if drafted is not None:
            drafted.headline = sentence_case(drafted.headline, named)
            problems = check(drafted)
            if not problems:
                return drafted
            rejected += problems
            logger.info("newsdesk network: model copy refused for event %s: %s", getattr(ev, "id", None), "; ".join(problems))
    drafted = viral.angle_copy(ev)
    if drafted is not None:
        problems = check(drafted)
        if not problems:
            drafted.rejected = rejected
            return drafted
        rejected += [f"angle: {p}" for p in problems]
    c = desk_copy(ev)
    c.rejected = rejected
    for part in ("stakes", "context"):
        if verify_copy(c, src):
            setattr(c, part, "")
    return c


# --- the captions -----------------------------------------------------------------------------------------------------------

EMOJI_RX = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def x_length(text: str) -> int:
    """X's weighted length: a link is 23, an emoji 2, everything else 1 (close enough for Latin text)."""
    n = 0
    for part in re.split(r"(https?://\S+)", text):
        if part.startswith(("http://", "https://")):
            n += X_URL_WEIGHT
        else:
            n += len(part) + len(EMOJI_RX.findall(part))
    return n


def x_caption(c: Copy, links: list[Link], *, with_links: bool = True, breaking: bool = False, outlets: list[str] | None = None, kicker: str = "") -> str:
    """A standalone X post: the headline, the context, a question now and then, the article. Parts are dropped from the least needed until
    it fits: the second link, the question, the stakes, the second sentence of context."""
    head = f"BREAKING: {c.headline}" if breaking and not c.headline.upper().startswith("BREAKING") else c.headline
    if kicker:
        head = f"{kicker}\n\n{head}"
    ctx = _sentences(c.context)
    use = [x for x in links if x.kind != "pricechat"][:2] if with_links else []
    own = next((x for x in links if x.kind == "pricechat"), None) if with_links else None
    if own is not None:
        use = [own, *use[:1]]
    names = [x.name for x in links if x.kind != "pricechat"] or list(outlets or [])  # no direct article: the outlets still credited by name
    plans = [(ctx, c.stakes, c.question, use), (ctx, c.stakes, c.question, use[:1]), (ctx, "", c.question, use[:1]), (ctx, "", "", use[:1]),
             (ctx[:1], "", "", use[:1]), ([], "", "", use[:1]), ([], "", "", [])]
    for sents, stakes, question, ls in plans:
        blocks = [head]
        if sents:
            blocks.append(" ".join(sents))
        if stakes:
            blocks.append(stakes)
        if question:
            blocks.append(question)
        if ls:
            blocks.append("\n".join(f"{x.name}: {x.url}" if x.kind != "pricechat" else f"Full story: {x.url}" for x in ls))
        elif names:
            blocks.append(f"Via {' · '.join(names[:2])}")
        text = "\n\n".join(blocks)
        if x_length(text) <= X_LIMIT:
            return text
        if len(sents) == 1 and not stakes and not question and ls:  # one long sentence: shortened to fit rather than dropped
            room = X_LIMIT - x_length("\n\n".join([head, *blocks[2:]])) - 2
            if room >= 60:
                blocks[1] = shorten(sents[0], room)
                text = "\n\n".join(blocks)
                if x_length(text) <= X_LIMIT:
                    return text
    return head[:X_LIMIT]


def telegram_caption(c: Copy, links: list[Link], line: str, *, breaking: bool = False, outlets: list[str] | None = None, kicker: str = "") -> tuple[str, str]:
    """(HTML, plain) for a Telegram photo caption: bold headline, context, why it matters, a question, the byline and clickable sources."""
    from html import escape as _escape

    def escape(t: str, quote: bool = False) -> str:  # text needs no quote escaping (Telegram shows &#x27; literally in some clients)
        return _escape(t, quote=quote)

    head = f"🔴 BREAKING: {c.headline}" if breaking else c.headline
    html_parts = [f"<b>{escape(head)}</b>"]
    plain_parts = [head]
    if kicker:
        mark = "🤯 " if kicker == "MARKETS GONE MAD" else "🔄 " if kicker.startswith("UPDATE") else ""
        html_parts.insert(0, f"<b>{mark}{escape(kicker)}</b>")
        plain_parts.insert(0, kicker)
    if c.context:
        html_parts.append(escape(c.context))
        plain_parts.append(c.context)
    if c.stakes:
        html_parts.append(f"📊 <b>Why it matters:</b> {escape(c.stakes)}")
        plain_parts.append(f"Why it matters: {c.stakes}")
    if c.question:
        html_parts.append(f"💬 {escape(c.question)}")
        plain_parts.append(c.question)
    sources = [x for x in links if x.kind != "pricechat"]
    own = next((x for x in links if x.kind == "pricechat"), None)
    if sources:
        word = "Data" if all(x.kind == "data" for x in sources) else "Sources" if len(sources) > 1 else "Source"
        html_parts.append(f"🔗 {word}: " + " · ".join(f'<a href="{escape(x.url, quote=True)}">{escape(x.name)}</a>' for x in sources))
        plain_parts.append(f"{word}: " + " · ".join(x.name for x in sources))
    elif outlets:  # no direct article link to give: the outlets are named, never left out
        word = "Sources" if len(outlets) > 1 else "Source"
        html_parts.append(f"🔗 {word}: " + escape(" · ".join(outlets[:3])))
        plain_parts.append(f"{word}: " + " · ".join(outlets[:3]))
    if own is not None:
        html_parts.append(f'📰 <a href="{escape(own.url, quote=True)}">Read on Price Chat</a>')
        plain_parts.append("Read on Price Chat")
    html_parts.append(f"<i>{escape(BRAND)} · {escape(line)}</i>")
    plain_parts.append(f"{BRAND} · {line}")
    html, plain = "\n\n".join(html_parts), "\n\n".join(plain_parts)
    while len(html) > TG_CAPTION_LIMIT and c.context:  # a photo caption holds 1024 characters: the context gives way first
        c = Copy(**{**c.out(), "context": " ".join(_sentences(c.context)[:-1])})
        return telegram_caption(c, links, line, breaking=breaking, outlets=outlets, kicker=kicker)
    return html, plain


@dataclass
class Package:
    copy: Copy
    links: list[Link]
    line: str  # the verification line
    original: bool
    x_text: str
    telegram_html: str
    telegram_plain: str
    checks: list[dict[str, Any]]
    ok: bool
    empty: str = ""  # why the words say nothing (app/newsdesk/viral.substance): such a story isn't posted at all
    accuracy: list[str] = field(default_factory=list)  # app/newsdesk/claims on the final texts: any problem = not publishable (hard gate)

    def out(self) -> dict[str, Any]:
        return {"copy": self.copy.out(), "links": [x.out() for x in self.links], "line": self.line, "original": self.original, "x_text": self.x_text,
                "telegram_html": self.telegram_html, "telegram_plain": self.telegram_plain, "checks": self.checks, "ok": self.ok, "empty": self.empty,
                "accuracy": self.accuracy}


def x_links_on(state: dict[str, Any] | None) -> bool:
    return (state or {}).get("x_links") is not False


STYLES = ("clean", "classic")
DEFAULT_STYLE = "clean"  # tests of the classic layout set "classic"


def post_style(state: dict[str, Any] | None) -> str:
    """clean (owner, 2026-11-12: the picture speaks, a short caption, @thepricechat) unless Admin set the older classic layout back."""
    s = str((state or {}).get("post_style") or DEFAULT_STYLE)
    return s if s in STYLES else DEFAULT_STYLE


async def package(ev: Any, *, settings: Any = None, rows: list[Any] | None = None, transport: Any = None, x_links: bool = True,
                  style: str = "classic") -> Package:
    """Everything both platforms post for an event, checked. Stored on the event (facts.package) so X uses exactly what Telegram used."""
    from app.newsdesk import brief

    c = await write(settings, ev, rows, transport)
    links = links_for(ev, rows)
    line = verification(ev)
    breaking = getattr(ev, "label", "") == brief.BREAKING
    outs = outlets_of(ev)
    kicker = str((ev.facts or {}).get("kicker") or "")
    if style == "clean":  # app/newsdesk/clean: the label earned, the story in 1-3 sentences with cashtags, the sources, @thepricechat
        from app.newsdesk import clean

        pre = clean.prefix_for(ev)
        said = " ".join([str(getattr(ev, "summary", "") or ""), *(str(getattr(r, "summary", "") or "") for r in rows or [])])
        x = clean.x_caption(c, links, prefix=pre, with_links=x_links, outlets=outs, case_source=said)
        html, plain, checked = clean.telegram_caption(c, links, prefix=pre, outlets=outs, case_source=said)
    else:
        x = x_caption(c, links, with_links=x_links, breaking=breaking, outlets=outs, kicker=kicker)
        html, plain = telegram_caption(c, links, line, breaking=breaking, outlets=outs, kicker=kicker)
        checked = plain
    src = f"{source_text_of(ev, rows)} {line}"
    allowed = [x_.url for x_ in links]
    names = [x_.name for x_ in links if x_.kind != "pricechat"] or outlets_of(ev)[:1]
    rx = checks.check_post(x, source_text=src, source_names=names, breaking=breaking, links=allowed)
    rt = checks.check_post(checked, source_text=src, source_names=names, breaking=breaking, links=allowed, max_len=TG_CAPTION_LIMIT)  # the story, not the signature
    all_checks = [{**c_, "platform": "x"} for c_ in rx.checks] + [{**c_, "platform": "telegram"} for c_ in rt.checks]
    from app.newsdesk import viral

    empty = viral.substance(c) if getattr(ev, "kind", "") in ("news", "update") else ""
    if empty:
        all_checks.append({"check": "substance", "passed": False, "detail": empty, "platform": "both"})
    accuracy = final_accuracy(ev, x, checked, src, names)
    for prob in accuracy:
        all_checks.append({"check": "accuracy", "passed": False, "detail": prob, "platform": "both"})
    return Package(c, links, line, is_original(ev), x, html, plain, all_checks, rx.ok and rt.ok and not empty and not accuracy, empty, accuracy)


URL = re.compile(r"https?://\S+|\b[\w.-]+\.(?:com|org|net|io|co|uk|xyz)/\S*")


def final_accuracy(ev: Any, x_text: str, tg_text: str, source_text: str, outlets: list[str], numbers: bool = True) -> list[str]:
    """The claim checker (app/newsdesk/claims) on the exact texts both platforms would post, after cashtags and every other step. Market
    moves, recaps and data posts are built from our own measured numbers and keep their own checks."""
    if getattr(ev, "kind", "") not in ("news", "update"):
        return []
    from app.newsdesk import claims

    out: list[str] = []
    for platform, text in (("X", x_text), ("Telegram", tg_text)):
        bare = URL.sub(" ", text or "").replace("@thepricechat", " ")
        out += [f"{platform}: {p}" for p in claims.for_event(ev, bare, source_text, [*outlets, *[str(s.get("name") or "") for s in getattr(ev, "sources", None) or []]],
                                                             numbers=numbers)]
    return list(dict.fromkeys(out))


def stored(ev: Any) -> dict[str, Any] | None:
    p = (ev.facts or {}).get("package")
    return p if isinstance(p, dict) and p.get("ok") else None


__all__ = ["BYLINE", "Copy", "Link", "Package", "clean_url", "is_article", "is_roundup", "links_for", "package", "related", "telegram_caption",
           "verification", "verify_copy", "write", "x_caption", "x_length"]


# --- Admin: the network's switches (monitoring, settings, emergency pause) ----------------------------------------------------

EMERGENCY = "Emergency pause (Admin)"
FAR = 3650  # days: an emergency pause holds until the owner resumes


def state(db: Any) -> dict[str, Any]:
    from app.newsdesk import engine
    from app.newsdesk import telegram as lane

    x, tg = engine.settings_row(db), lane.settings_row(db)
    return {"x_links": x_links_on(x.state), "x_cost_per_post_usd": 0.20 if x_links_on(x.state) else 0.015,
            "emergency": (x.pause_reason or "") == EMERGENCY or (tg.state or {}).get("problem") == EMERGENCY,
            "x_paused": x.pause_reason or "", "telegram_paused": (tg.state or {}).get("problem") or "",
            "data_posts": (x.state or {}).get("data_posts", True) is not False, "experiments": (x.state or {}).get("experiments", True) is not False,
            "post_style": post_style(x.state)}


def configure(db: Any, *, x_links: bool | None = None, emergency: bool | None = None, data_posts: bool | None = None, experiments: bool | None = None,
              post_style: str | None = None, now: Any = None) -> dict[str, Any]:
    """x_links: source links in X posts (on by default; off = named sources only, $0.015 a post). emergency: stop both platforms at once
    until resumed (the desk keeps choosing stories, nothing is posted); off lifts only an emergency pause, never another lane's own pause."""
    from datetime import timedelta

    from app.db.base import utcnow
    from app.newsdesk import engine
    from app.newsdesk import telegram as lane

    now = now or utcnow()
    x, tg = engine.settings_row(db), lane.settings_row(db)
    if x_links is not None:
        x.state = {**(x.state or {}), "x_links": bool(x_links)}
    if data_posts is not None:  # Viral Engine 2.0: data posts and MARKETS GONE MAD
        x.state = {**(x.state or {}), "data_posts": bool(data_posts)}
    if experiments is not None:  # Viral Engine 2.0: runner-up hook experiments
        x.state = {**(x.state or {}), "experiments": bool(experiments)}
    if post_style is not None and post_style in STYLES:  # clean (default) or the classic layout
        x.state = {**(x.state or {}), "post_style": post_style}
    if emergency is True:
        x.paused_until, x.pause_reason = now + timedelta(days=FAR), EMERGENCY
        tg.state = {**(tg.state or {}), "paused_until": (now + timedelta(days=FAR)).isoformat(), "problem": EMERGENCY}
    elif emergency is False:
        if x.pause_reason == EMERGENCY:
            x.paused_until, x.pause_reason = None, None
        if (tg.state or {}).get("problem") == EMERGENCY:
            tg.state = {k: v for k, v in (tg.state or {}).items() if k not in ("paused_until", "problem")}
    db.commit()
    return state(db)
