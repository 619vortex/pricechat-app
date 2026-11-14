"""The wire style (owner, 2026-11-14: "a cleaner, faster Watcher Guru news style"). Network.post_style "wire", opt-in: the default stays
"clean" (network.DEFAULT_STYLE) and Admin switches between Wire, Clean and Classic (network.STYLES) without a deploy.

Words: one short factual headline, usually one or two sentences, nothing else. A tag only when the story earns it (`tag_for`):
- BREAKING:  major and market-moving (editorial.breaking_worthy, or an exceptional measured move / liquidation flush: editorial.exceptional),
             strong evidence (official, two outlets, our prices), and it just happened: at most 60 minutes from first report to release.
- JUST IN:   fresh (the desk earned a label: BREAKING / JUST IN / ALERT) but smaller, at most 90 minutes from first report to release.
- UPDATE:    a development on a story already posted (an update event: Telegram replies to the original message).
- otherwise no tag at all. Data posts and market checks never carry one (no "MARKETS GONE MAD", no "ALERT").
A flag follows a tag only when the headline leads with the country (brief.flag). The sources and @thepricechat share one quiet last line.
Title Case outlet headlines are set in sentence case without losing a name or keeping an ordinary word's capital (`headline_case`), and a
qualifier the sources attach to a figure ("at least six") is never dropped (checks.restore_qualifiers; the final gate holds it too).

Wording (`checks.wording_problems`, on the copy and again on the final caption): no "massive", "huge", "explodes"... unless the sources
use the word and the sentence carries the figure; no superlative ("record", "biggest", "first-ever") the sources don't use; no "crashes",
"plunges", "soars" without a figure or the sources' own word; no figure stated as exact that the sources qualify
(`checks.qualifier_problems`). On top of every existing gate (figures, tickers, names, accusations, quotes, the chart), never instead of one.

Pictures (`picture`), only real ones and never forced:
- chart   real prices: a measured market move or liquidation flush (the desk's verified series), or a story that leads with an asset or a
          stock index and reports its move (a % move, a level broken, a record, liquidations) or one the Viral Engine measured reacting;
          the candles must show what the headline says (`chart_matches`), else no chart.
- data    a data post's comparison card (measured closes).
- photo   a licensed photo of the public figure the story is about (clean.person_subject's rules), or of the named site a major incident
          or conflict happened at (`site_subject`: "Nasser Hospital"), credited; never a generic scene, a skyline or a company logo.
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

    low = core.lower()
    return any(rx.fullmatch(core) for rx in editorial.COUNTRIES.values()) or low in clean.KNOWN_PEOPLE or low in ASSET_NAMES         or (low in clean.PROPER and low not in ("may", "march"))


# Ordinary words a Title Case Headline capitalises. One of these keeps its capital only where the reporting writes it capitalised on its
# own mid-sentence, or as part of the very name the headline uses ("Deutsche Bank", "Western Union"); never because the reporting
# capitalises it inside some name ("Bank says" after "Deutsche Bank's ... custody": 2026-11-14).
ORDINARY = set("""a an the and or but nor for of to in on at by from with as into onto over under after before amid about against ahead
between during since until via without within is are was were be been being has have had do does did will would can could may might must
shall should not no new top big more most less least all any some every each its it this that these those his her their our your out up down
off back says say said tells told reports report reported sees see seen warns warn urges urge plans plan wants want gets get makes make takes
take gives give opens open closes close launches launch goes go comes come hits hit falls fall rises rise jumps jump drops drop breaks break
tops top slips slip slides slide sinks sink surges surge soars soar plunges plunge climbs climb gains gain loses lose wins win cuts cut raises
raise holds hold keeps keep signs sign seeks seek files file sues sue bans ban halts halt blocks block backs back adds add buys buy sells sell
pays pay faces face becomes become remains remain stays stay turns turn moves move sets set leads lead shows show finds find calls call asks
ask kills kill killed strike strikes attack attacks army troops forces force police court judge ministry minister president prime chief
leader official officials government state states agency authority board council commission committee department office bank banks fund funds
group exchange exchanges market markets stock stocks shares bond bonds price prices rate rates deal deals talks plan war peace crisis probe
report tanker tankers ship ships oil gas crude output production custody clients client customers users option options settlement network
payments payment stablecoin stablecoins token tokens coin coins crypto digital asset assets service services platform company companies firm
firms investors traders analysts workers people city region border site plant hospital terminal port airport camp training women men
children readiness deal record high low week month year years day days time first last next former interim acting near pre-war post-war
u-turn heading access key nuclear watchdog pushes push sweep sweeps sweeping leveraged longs shorts liquidations theft plot directed prison
jailed fraudster merger focus put puts affair affairs scandal cell cells review reviews case cases claim claims charge charges trial
investigation inquiry ruling verdict sentence fine fines penalty lawsuit settlement bid offer stake sale sales purchase acquisition takeover
listing debut shares profit loss losses revenue earnings results forecast outlook guidance growth inflation jobs wages tariff tariffs trade
sanctions ceasefire truce invasion offensive missile missiles drone drones rocket rockets bomb blast explosion fire flood storm earthquake
hurricane outage hack hacker hackers exploit breach scam fraud theft heist withdrawals deposits reserves treasury fed bill law rule rules
policy vote election poll campaign summit meeting visit trip speech statement letter warning threat threats protest protests strike strikes
union deal agreement pact framework proposal approval license licence permit ban bans ruling ruled executive executives boss bosses founder
founders head heads staff employees team teams player players captain goalkeeper footballer club fans owner owners holders whales miners
validators developers wallet wallets account accounts address funds vault vaults bridge bridges chain chains protocol protocols app apps
blockchain mainnet testnet upgrade fork launch launches rollout feature features product products chip chips model models steady higher
lower sharply strong weak small early late still now soon today tonight tomorrow yesterday despite global local national federal central public
private major minor fresh latest final full half rare closed hot cold firm flat mixed broad deep wide quick fast slow safe risky cheap rich poor
free legal illegal unofficial possible likely expected sudden massive huge slight modest sharp again back away through across toward
towards beyond among around upon behind below above near past amid while when where which what who whom whose why how than then just even
also only very much many few such other another own same both either neither every selloff sell-off lows highs rally rallies slump
plunge crash rebound recovery""".split())
# words that end a name a Title Case Headline writes ("Deutsche Bank", "Anchorage Digital", "Gaza Sports Club"): after a word kept as a
# name, one of these is part of that name when the reporting gives no other spelling
NAME_TAILS = set("""bank group exchange capital digital markets network union fund funds house street club council ministry agency court
foundation labs lab holdings partners securities trust company corp corporation inc media news times post journal university institute
hospital airlines airways motors energy oil petroleum financial reserve authority commission""".split())
# always written with a capital in a Title Case Headline's sentence case (the reporting may write them lower case: "30,000 bitcoin")
ASSET_NAMES = {"bitcoin": "Bitcoin", "ether": "Ether", "ethereum": "Ethereum", "solana": "Solana", "hyperliquid": "Hyperliquid",
               "dogecoin": "Dogecoin", "cardano": "Cardano", "tether": "Tether", "ripple": "Ripple", "litecoin": "Litecoin"}


# how an ordinary English word ends (a name the reporting never writes keeps its capital; a word ending like these doesn't)
COMMON_SUFFIX = re.compile(r"(?:s|ed|ing|ly|tion|sion|ment|ness|ity|ive|ous|ful|less|able|ible|ate|ize|ise|al|er|est|ance|ence)$")


VERB_SUFFIX = re.compile(r"(?:s|ed|ing|ly)$")  # after a name, a word ending like a verb is the verb ("Nvidia Hires"), not more name


def _core(word: str) -> tuple[str, str, str, str]:
    m = re.match(r"^([^\w$]*)([\w$’'&.-]+?)([’']s)?([^\w]*)$", word)
    return (m.group(1), m.group(2), m.group(3) or "", m.group(4)) if m else ("", word, "", "")


def _name_like(core: str) -> bool:
    """Written the way only names are: all capitals (USDPT, U.S.), capitals inside a word (SpaceX, VisaNet, McDonald), a digit or & in it."""
    letters = re.sub(r"[^A-Za-z]", "", core)
    if len(letters) >= 2 and letters.isupper():
        return True
    if any(c.isdigit() for c in core) or "&" in core or "$" in core:
        return True
    return any(any(ch.isupper() for ch in seg[1:]) for seg in core.split("-"))


def _reading(reporting: str) -> tuple[dict[str, list[str]], set[str], set[tuple[str, str]]]:
    """How the reporting writes each word mid-sentence on its own (lower case -> the forms), which words it capitalises only inside a run of
    capitals (a name: Deutsche Bank), and those runs' neighbouring pairs."""
    standalone: dict[str, list[str]] = {}
    in_name: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", reporting or ""):
        toks = re.findall(r"[\w’'&.$-]+", sentence)
        cores = [_core(t)[1].rstrip(".") for t in toks]
        for i, c in enumerate(cores):
            if not c or i == 0:
                continue  # a sentence's first word is capitalised whatever it is
            cap = c[:1].isupper()
            prev_cap = cores[i - 1][:1].isupper() and (i > 1 or cores[i - 1].lower() not in checks.STOP)  # "Deutsche Bank and ...", not "The Bank"
            next_cap = i + 1 < len(cores) and cores[i + 1][:1].isupper()
            if cap and (prev_cap or next_cap):
                in_name.add(c.lower())
                if prev_cap:
                    pairs.add((cores[i - 1].lower(), c.lower()))
                continue
            standalone.setdefault(c.lower(), []).append(c)
    return standalone, in_name, pairs


def headline_case(head: str, reporting: str) -> str:
    """An outlet's Title Case Headline in sentence case. A word keeps its capital when it is written like a name (USDPT, SpaceX, U.S.), is a
    country, capital, leader or a word always capitalised (clean.PROPER), is part of a name the reporting writes the same way ("Deutsche
    Bank", "Anchorage Digital"), or the reporting writes it capitalised on its own mid-sentence ("Visa"). An ordinary word (ORDINARY) never
    keeps its capital just because the reporting capitalises it inside some name ("..., bank says"). Otherwise it takes the reporting's own
    spelling when there is one ("U-turn", "pre-war"), else lower case. A headline that isn't Title Case is unchanged."""
    words = head.split()
    if not title_cased(head):
        return head
    standalone, in_name, pairs = _reading(reporting)
    cores = [_core(w)[1].rstrip(".").lower() for w in words]
    out = [words[0]]
    for i, w in enumerate(words[1:], 1):
        pre, core, poss, post = _core(w)
        low = core.rstrip(".").lower()
        if not core[:1].isupper() or _name_like(core) or _protected(core.rstrip(".")):
            out.append(w)
            continue
        if (cores[i - 1], low) in pairs or (i + 1 < len(words) and (low, cores[i + 1]) in pairs):
            out.append(w)  # the name as the reporting writes it: "Deutsche Bank", "Western Union"
            continue
        seen = standalone.get(low)
        prev = _core(out[-1])[1].rstrip(".")
        prev_named = (out[-1][:1].isupper() and prev.lower() not in ORDINARY and prev.lower() not in checks.STOP and not _protected(prev)
                      and not out[-1].endswith((",", ":", ";")))  # the word before is kept as a name of its own (not "Powell", "Iran")
        if low in NAME_TAILS and prev_named and (not seen or low in in_name):
            out.append(w)  # the end of the name before it: "Deutsche Bank", "Gaza Sports Club"
            continue
        if seen:
            form = max(set(seen), key=lambda f: (seen.count(f), f[:1].islower()))
            tail = core[len(form):] if core.lower().startswith(form.lower()) else ""  # a trailing full stop ("U.S.")
            out.append(f"{pre}{form}{tail}{poss}{post}")  # the reporting's own spelling
            continue
        if low in in_name and low not in ORDINARY:
            out.append(w)  # only ever inside a name in the reporting, and no ordinary word: a name ("Abdel-Malek", "Nasser")
            continue
        if low not in in_name and low not in ORDINARY and low not in checks.STOP and len(low) > 3 \
                and (not COMMON_SUFFIX.search(low) or (prev_named and not VERB_SUFFIX.search(low))):
            out.append(w)  # the reporting never writes it: a word that reads as a name keeps its capital ("Wachtell", "Mahmoud Kamal")
            continue
        out.append(f"{pre}{core[:1].lower()}{core[1:]}{poss}{post}")
    return clean._proper(" ".join(out))


def title_cased(text: str) -> bool:
    words = (text or "").split()
    long = [w for w in words[1:] if len(re.sub(r"\W", "", w)) > 3]
    return bool(long) and sum(1 for w in long if w[:1].isupper()) / len(long) >= 0.6


def case_source(ev: Any, rows: list[Any] | None) -> str:
    """What the casing reads names from: the articles' summaries, and the outlets' own headlines that are already in sentence case (The
    Block's "Deutsche Bank goes live with digital asset custody service" says "custody" is an ordinary word)."""
    parts = [str(getattr(ev, "summary", "") or ""), *(str(getattr(r, "summary", "") or "") for r in rows or [])]
    parts += [f"{t}." for t in (str(getattr(r, "title", "") or "") for r in rows or []) if t and not title_cased(t)]
    return " ".join(p for p in parts if p)


def story_sentences(c: Any, case_source: str = "", source_text: str = "") -> list[str]:
    """The headline as a sentence (shortened past HEAD_MAX by dropping a trailing clause), then at most one more: the first of the copy's
    sentences that adds a figure the headline lacks, or any short one when the headline is very short. Usually one or two sentences. A
    qualifier the sources attach to a figure ("at least six") goes back in front of it (checks.restore_qualifiers): only their words."""
    from app.newsdesk import network

    cased = headline_case(network.shorten(c.headline, HEAD_MAX), f"{c.context or ''} {case_source}")
    short = network.Copy(**{**c.out(), "headline": cased})
    sents = clean.sentences_of(short, "")
    sents[0] = clean._sentence(cased)
    if source_text:
        sents = [checks.restore_qualifiers(s, source_text) for s in sents]
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


def lead(c: Any, *, tag: str = "", flag: str = "", case_source: str = "", source_text: str = "") -> tuple[str, str]:
    """(HTML, plain): the tag, the flag, the story in one or two sentences with the cashtags the sentences establish."""
    story = clean.cashtag(" ".join(story_sentences(c, case_source, source_text)))[0]  # one tag per asset per post, at its first mention
    pre = " ".join(x for x in (tag, flag) if x)
    plain = " ".join(x for x in (pre, story) if x)
    html = " ".join(x for x in ((f"<b>{_esc(tag)}</b>" if tag else ""), flag, _esc(story)) if x)
    return html, plain


def telegram_caption(c: Any, links: list[Any], *, tag: str = "", flag: str = "", outlets: list[str] | None = None, case_source: str = "",
                     source_text: str = "") -> tuple[str, str, str]:
    """(HTML, plain as sent, plain the checks read). Tag + story, a blank line, then the sources and @thepricechat."""
    body_html, body_plain = lead(c, tag=tag, flag=flag, case_source=case_source, source_text=source_text)
    f_html, f_plain, f_checked = footer(links, outlets)
    return f"{body_html}\n\n{f_html}", f"{body_plain}\n\n{f_plain}", (f"{body_plain}\n\n{f_checked}" if f_checked else body_plain)


def x_caption(c: Any, links: list[Any], *, tag: str = "", flag: str = "", with_links: bool = True, outlets: list[str] | None = None,
              case_source: str = "", source_text: str = "") -> str:
    """The same words on X: tag + story, then one source link (or the sources named). The second sentence goes first if it doesn't fit."""
    from app.newsdesk import network

    raw = story_sentences(c, case_source, source_text)
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


def fallback_caption(ev: Any, source: str, source_text: str = "") -> tuple[str, str]:
    """(HTML, plain) when the package failed its checks: the outlet's own headline in sentence case and its source, nothing else."""
    from app.newsdesk import network

    raw = network.headline_case(getattr(ev, "headline", "") or getattr(ev, "title", "") or "")
    head = clean._sentence(headline_case(network.shorten(raw, HEAD_MAX), getattr(ev, "summary", "") or ""))
    if source_text:
        head = checks.restore_qualifiers(head, source_text)
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
                      r"lawsuit|sues|sued|guilty|convicted|jailed|sentenced|shut(?:s)? down|probe|investigat\w+|fined|missiles?|strikes? on|explosion|"
                      r"outage|tariffs?|u-turn|reverses?|reversal|plunges?|plunged|tumbles?|soars?|soared|sinks?|slumps?)\b", re.I)
FIGURE = re.compile(r"[$€£]\s?\d|\d+(?:\.\d+)?\s?%|\b\d[\d,.]*\s?(?:billion|million|bn|[BM])\b|\b\d+(?:\.\d+)?\s?(?:basis points|bps|bp)\b")
COUNT = re.compile(r"\b(?!(?:19|20)\d\d\b)\d[\d,.]*\b")  # a count or an amount in the headline, not a year
SURPRISE_MAX = 6.0
FILLER_PENALTY = 8.0
# Filler whatever else the headline says: predictions and price targets, previews, explainers, listicles, opinion, sponsored formats
ALWAYS_FILLER = re.compile(r"\b(price predictions?|price analysis|technical analysis|price forecast|forecasts? for|outlook for|preview|week ahead|"
                           r"what to (?:know|watch|expect)|things to (?:know|watch)|here'?s (?:why|what|how)|explain(?:ed|er|s)?|opinion|column|"
                           r"interview|podcast|webinar|guide|how to|tips|best (?:crypto|coins?|stocks?|altcoins?)|top \d+|"
                           r"\d+ (?:altcoins?|coins?|cryptos?|stocks?|tokens?|things|ways|reasons)\b)", re.I)
# a price target someone guesses at ("could hit $150,000", "set to explode"): never news, whatever figure it names
PRICE_TARGET = re.compile(r"\b(?:could|might|may|set to|poised to|expected to|likely to|on track to|eyes?|eyeing|targets?)\s+"
                          r"(?:hit|reach|test|top|surge|soar|rally|climb|rise|fall|drop|crash|plunge|double|triple|explode|moon|rocket|break(?:out)?)\b", re.I)
# speculation: filler only when it is the headline's main claim and nothing concrete comes with it (owner, 2026-11-14: "Fed set to cut rates
# by 25 basis points" and "Binance could face a $4 billion fine as DOJ files charges" are news)
SPECULATIVE = re.compile(r"\b(could|might|may|poised to|set to|expected to|likely to|eyes|mulls|weighs|considers|considering|plans to|aims to|"
                         r"analysts? (?:say|says|expect|see|think)|experts? (?:say|warn)|partners? with|partnership|teams? up|integrat(?:es|ion))\b", re.I)
DECISION = re.compile(r"\b(approv(?:es|ed)|rejects?|rejected|bans?|banned|halts?|halted|cuts?|raised?|raises|hikes?|hiked|files?|filed|sues|sued|"
                      r"charged|arrested|signs?|signed|launch(?:es|ed)|acquir(?:es|ed)|buys?|bought|sells?|sold|hacked|seized|sentenced|jailed|"
                      r"resigns?|resigned|fired|delists?|suspends?|suspended|confirms?|confirmed|announces?|announced|orders?|ordered|kills?|killed|"
                      r"strikes?|struck|passes|passed|votes?|voted|wins?|won|loses|lost|reports?|reported|falls?|fell|rises?|rose|jumps?|jumped|"
                      r"drops?|dropped|surges?|surged|breaks?|broke|says|said|tells|told|warns|warned)\b", re.I)
SUBORDINATE = re.compile(r"\s(?:as|after|amid|while|because|since|though|although|when)\s|[,;:–—]\s|\s-\s", re.I)
EARNINGS = re.compile(r"\b(earnings|(?:quarterly|annual|full-year|(?:first|second|third|fourth)-quarter|q[1-4]) (?:results|revenue|profit|sales)|"
                      r"revenue|profits?|q[1-4] results)\b", re.I)
EARNINGS_SURPRISE = re.compile(r"\b(beats?|miss(?:es|ed)?|warns?|guidance|cuts? (?:forecast|outlook)|raises? (?:forecast|outlook)|surges?|soars?|"
                               r"plunges?|tumbles?|slumps?|record|unexpected\w*|layoffs?|loss)\b|\d+(?:\.\d+)?\s?%", re.I)


def surprise_bonus(ev: Any) -> float:
    title = getattr(ev, "title", "") or ""
    hits = {m.group(0).lower() for m in SURPRISE.finditer(title)}
    return min(SURPRISE_MAX, (5.0 if hits else 0.0) + (2.0 if FIGURE.search(title) else 0.0))


def _main_clause(title: str) -> str:
    m = SUBORDINATE.search(title or "")
    return (title or "")[:m.start()] if m else (title or "")


def filler(ev: Any) -> str | None:
    """Why this headline reads as filler, or None. Only news: our own measured moves and data posts say what they measured.
    - always: price predictions and targets, previews, explainers, listicles, opinion, interviews, guides;
    - routine earnings: results with no surprise (no beat or miss, no guidance, no big move, no figure);
    - speculation ("could", "set to", "analysts say", a partnership): only when it is in the main clause and the headline has no figure and
      no concrete decision or event (DECISION, SURPRISE) of its own."""
    if getattr(ev, "kind", "") not in ("news", "update"):
        return None
    title = getattr(ev, "title", "") or ""
    m = ALWAYS_FILLER.search(title) or PRICE_TARGET.search(title)
    if m:
        return f"filler or speculation ({m.group(0).lower()})"
    if EARNINGS.search(title) and not EARNINGS_SURPRISE.search(title) and not SURPRISE.search(title):
        return f"routine earnings ({EARNINGS.search(title).group(0).lower()})"
    main = _main_clause(title)
    s = SPECULATIVE.search(main)
    if s is None:
        return None
    rest = main[:s.start()] + re.sub(r"^\s*\w+", "", main[s.end():])  # the verb right after "could" / "set to" is the speculation itself
    if FIGURE.search(title) or COUNT.search(title) or DECISION.search(rest) or SURPRISE.search(rest):
        return None  # speculation with a concrete fact, figure or decision of its own: news
    return f"filler or speculation ({s.group(0).lower()})"


# --------------------------------------------------------------------------------------------------------------- pictures
MOVE = re.compile(r"\d+(?:\.\d+)?\s?%|\bliquidat\w*|\b(?:breaks?|broke|falls?|fell|drops?|dropped|slides?|slid|sinks?|sank|tops?|topped|crosses|crossed|"
                  r"climbs?|climbed|rises?|rose|jumps?|jumped|surges?|surged|plunges?|plunged|tumbles?|tumbled)\s+(?:below|above|under|past|through|to|beyond)\s+\$?\d|"
                  r"\b(?:record|all-time)\s+(?:high|low)s?\b", re.I)


# a commodity's own price moving: "Oil prices jump 5%", "Gold hits a record high", never "Saudi crude output jumps 26%"
COMMODITY_MOVE = re.compile(r"\b(?:oil|crude|brent|wti|gold|silver)(?:\s+(?:prices?|futures))?\s+(?:hits?|tops?|breaks?|"
                            r"(?:falls?|fell|drops?|dropped|slides?|slid|sinks?|sank|climbs?|climbed|rises?|rose|jumps?|jumped|surges?|surged|"
                            r"plunges?|plunged|tumbles?|tumbled|gains?|gained|rall(?:y|ies|ied)|soars?|soared))\b", re.I)
# a stock index's own move, named in the first words: "S&P 500 falls 3%", "Nasdaq jumps 2.4%" (never a macro story's stand-in)
INDEXES = [(re.compile(r"\b(?:s&p 500|s&p)\b", re.I), "^GSPC", "S&P 500"), (re.compile(r"\bnasdaq(?: composite)?\b(?![- ]listed)", re.I), "^IXIC", "Nasdaq"),
           (re.compile(r"\bdow(?: jones)?\b", re.I), "^DJI", "Dow Jones")]
INDEX_MOVE = re.compile(r"\b(?:falls?|fell|drops?|dropped|slides?|slid|sinks?|sank|climbs?|climbed|rises?|rose|jumps?|jumped|surges?|surged|plunges?|"
                        r"plunged|tumbles?|tumbled|gains?|gained|rall(?:y|ies|ied)|soars?|soared|loses?|lost|sheds?)\b[^.]{0,30}?\d+(?:\.\d+)?\s?%", re.I)
UP = re.compile(r"\b(climbs?|climbed|rises?|rose|jumps?|jumped|surges?|surged|gains?|gained|rall(?:y|ies|ied)|soars?|soared|tops?|topped|"
                r"crosses|crossed|above|record high|all-time high|higher)\b", re.I)
DOWN = re.compile(r"\b(falls?|fell|drops?|dropped|slides?|slid|sinks?|sank|plunges?|plunged|tumbles?|tumbled|loses?|lost|sheds?|below|under|"
                  r"record low|lower|crash(?:es|ed)?)\b", re.I)
PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
LEVEL = re.compile(r"\b(below|under|above|past|through|beyond|tops?|topped|crosses|crossed)\s+\$?(\d[\d,]*(?:\.\d+)?)\s?([kK])?\b", re.I)


def price_story(ev: Any) -> tuple[str, str, str] | None:
    """(Yahoo symbol, name, label) when the story is about this asset's price: the headline leads with the asset (its first four words
    establish it) and reports a move (a commodity: its own price moving, COMMODITY_MOVE; an index: its own % move, INDEX_MOVE), or the
    Viral Engine measured that asset reacting. Macro stories (the S&P 500 stand-in) only on a measured reaction. Else None: a chart beside a
    story implies the story is about that price."""
    head = f"{getattr(ev, 'headline', '') or ''}"
    title = f"{getattr(ev, 'title', '') or ''}"
    for text in (head, title):
        first = " ".join(text.split()[:4])
        for rx, sym, name in INDEXES:
            if rx.search(first) and INDEX_MOVE.search(text):
                return sym, name, name
        subject = clean.chart_subject(text)
        if subject is None:
            continue
        leading = clean.chart_subject(first)
        commodity = subject[0] in ("BZ=F", "GC=F", "SI=F")
        moved = COMMODITY_MOVE.search(text) if commodity else MOVE.search(text)
        if subject[0] != "^GSPC" and leading is not None and leading[0] == subject[0] and moved:
            return subject
        measured = ((getattr(ev, "facts", None) or {}).get("viral") or {}).get("reactions") or []
        if any(isinstance(r, dict) and str(r.get("symbol")) == subject[0] for r in measured):
            return subject
    return None


def chart_matches(text: str, bars: list[list[float]]) -> str:
    """Why these candles don't show what the headline says ("" when they do), so a chart never contradicts its story: the direction of a
    stated move, at least half of a stated percent over the chart's day, a level broken (the candles reach it and close on the stated side),
    a record high or low (the close near the day's top or bottom). A headline that states none of these: the candles just have to move."""
    if len(bars) < 2:
        return "no candles"
    first, last = float(bars[0][1]), float(bars[-1][4])
    hi, lo = max(float(b[2]) for b in bars), min(float(b[3]) for b in bars)
    change = (last - first) / first * 100 if first else 0.0
    up, down = bool(UP.search(text or "")), bool(DOWN.search(text or ""))
    stated = False
    lv = LEVEL.search(text or "")
    if lv:
        stated = True
        level = float(lv.group(2).replace(",", "")) * (1000 if lv.group(3) else 1)
        if lv.group(1).lower() in ("below", "under"):
            if lo > level or last > level * 1.02:
                return f"the candles don't show a break below {level:,.0f} (low {lo:,.2f}, last {last:,.2f})"
        elif hi < level or last < level * 0.98:
            return f"the candles don't show a move above {level:,.0f} (high {hi:,.2f}, last {last:,.2f})"
    pm = PCT.search(text or "")
    if pm and (up or down) and not (up and down and not lv):
        stated = True
        want = float(pm.group(1))
        if (down and not up and change > 0) or (up and not down and change < 0):
            return f"the headline's move is {'down' if down else 'up'}, the candles {change:+.2f}%"
        if abs(change) < want * 0.5:
            return f"the headline says {want:g}%, the candles {change:+.2f}% over the chart's day"
    if re.search(r"\b(record|all-time) high\b", text or "", re.I):
        stated = True
        if last < hi * 0.97:
            return "a record high, but the candles close well under the day's high"
    if re.search(r"\b(record|all-time) low\b", text or "", re.I):
        stated = True
        if last > lo * 1.03:
            return "a record low, but the candles close well over the day's low"
    if not stated and abs(change) < 0.3 and (hi - lo) / max(lo, 1e-9) * 100 < 0.6:
        return "the candles barely move"
    return ""


def text_only() -> tuple[bytes, dict[str, Any]]:
    return b"", {"style": STYLE, "kind": "text", "layout": "text"}


# a file that is a social-media post, a screenshot, a poster or a quote card is not a photograph of the person (2026-11-14: Commons
# "Donald Trump Facebook Twitter post ..." is a tweet laid over a rally photo: words the post would appear to show)
NOT_PLAIN = re.compile(r"\b(facebook|twitter|tweet\w*|instagram|tiktok|x\.com|social media|post|posts|screenshot\w*|screen ?grab|meme|quote|quotes|"
                       r"poster|banner|graphic|infographic|cartoon|caricature|collage|montage|edit(?:ed)?|composite|thumbnail|logo|text)\b", re.I)


def plain_photo(p: Any) -> bool:
    return not NOT_PLAIN.search(f"{getattr(p, 'title', '')} {getattr(p, 'description', '')}".replace("_", " "))


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
        found = [p for p in found if plain_photo(p)]
        for p in found[:3]:
            img = await photos.fetch(client, p, min_width=photos.MIN_WIDTH)
            if img is None:
                continue
            meta = p.to_meta() if hasattr(p, "to_meta") else {"url": p.url}
            return img, {"url": p.url, "page": meta.get("page", ""), "title": meta.get("title", ""), "credit": p.credit,
                         "licence": meta.get("licence", "") or getattr(p, "licence", ""), "photo": meta, "person": person}
    return None


# a major incident or conflict at a named site: "Israeli strike on Nasser Hospital ...", "Missile strike hits ... Ras Tanura oil terminal".
# The site's own licensed photograph (photos.py: licence verified, the name in the photo's title, no people on a sensitive story) tells the
# reader where it happened; a generic scene, a skyline or a stand-in never does, so there is none.
SITE_WORDS = ("oil export terminal", "oil terminal", "export terminal", "hospital", "terminal", "refinery", "port", "airport", "plant", "power station", "dam", "bridge", "reactor", "embassy", "air base",
              "naval base", "base", "pipeline", "mosque", "cathedral", "church", "stadium", "oil field", "gas field", "facility", "complex", "strait",
              "canal", "prison", "university", "market")
SITE = re.compile(r"\b((?:[A-Z][\w'’-]+\s+){0,2}[A-Z][\w'’-]+)\s+(?i:(" + "|".join(SITE_WORDS) + r"))\b")
SITE_AT = re.compile(r"(?i:\b(" + "|".join(SITE_WORDS) + r"))\s+(?:at|in|of|near)\s+((?:[A-Z][\w'’-]+\s+){0,2}[A-Z][\w'’-]+)")
MAJOR_SCENE = re.compile(r"\b(strikes?|struck|missiles?|drones?|attack(?:s|ed)?|explosions?|blasts?|fires?|earthquakes?|floods?|hurricanes?|"
                         r"typhoons?|collaps(?:e|es|ed)|bomb\w*|shell(?:ing|ed)|raids?|seiz(?:e|es|ed|ure)|sinks?|sank|collision|derail\w*)\b", re.I)


def site_subject(ev: Any) -> str | None:
    """The named site a major incident or conflict story happened at ("Nasser Hospital", "Ras Tanura terminal"), or None: the story must be
    about a physical event (MAJOR_SCENE) and the site must have a name of its own, not a country or a nationality ("Gaza hospital": no)."""
    text = " ".join(x for x in (getattr(ev, "headline", "") or "", getattr(ev, "title", "") or "", str((getattr(ev, "facts", None) or {}).get("told") or ""),
                                getattr(ev, "summary", "") or "") if x)
    if not MAJOR_SCENE.search(text):
        return None
    generic = checks.PLACES | checks.DEMONYMS | checks.ORDINARY | ORDINARY | checks.STOP | checks.COMMON_NAMES
    found = [(m.group(1), m.group(2)) for m in SITE.finditer(text)] + [(m.group(2), m.group(1)) for m in SITE_AT.finditer(text)]
    for run, word in found:
        named = [w for w in run.split() if w[:1].isupper() and re.sub(r"[’']s$", "", w).lower() not in generic]
        if named:
            return f"{' '.join(named)} {word.lower()}"
    return None


def site_photo_fits(p: Any, name: str, word: str, where: set[str]) -> bool:
    """The photo shows this site and no other of the same name: its title or description names the site itself ("Nasser Hospital"), not a
    longer name ending in it ("Gamal Abdel Nasser Hospital" is in Egypt), and names a place the story names (Gaza). A story that names no
    place can't be checked, so it gets no site photo."""
    title = re.sub(r"^file:|\.(?:jpe?g|png|gif|tiff?|webp)$", "", str(getattr(p, "title", "") or ""), flags=re.I)
    fields = [title, str(getattr(p, "description", "") or ""), *(str(t) for t in getattr(p, "tags", []) or [])]
    # each field, phrase by phrase: the word before the site's name counts only inside the same phrase
    phrases = [x for f in fields for x in re.split(r"[.,;:()|/]+", f.replace("_", " ").lower()) if x.strip()]
    low = " | ".join(phrases)
    hits = [h for x in phrases for h in re.finditer(rf"(?:([a-z]+)[\s-]+)?\b{re.escape(name.lower())}\s+{re.escape(word.lower())}\b", x)]
    if not hits:
        return False
    generic = checks.STOP | ORDINARY | checks.ORDINARY | {"file", "the", "of", "at", "in"}
    if any(h.group(1) and h.group(1) not in generic and h.group(1) not in checks.PLACES for h in hits):
        return False  # part of another, longer name
    return bool(where) and any(re.search(rf"\b{re.escape(w)}\b", low) for w in where)


async def _site_photo(ev: Any, used: set[str], transport: Any) -> tuple[Any, dict[str, Any]] | None:
    """A licensed photograph of the named site a major incident happened at (site_subject), credited. Never people (sensitive=True)."""
    import httpx

    from app.newsdesk import photos

    site = site_subject(ev)
    if not site:
        return None
    word = next(w for w in SITE_WORDS if site.endswith(f" {w}"))
    name = site[: -len(word) - 1]
    story = " ".join(str(x or "") for x in (getattr(ev, "headline", ""), getattr(ev, "title", ""), getattr(ev, "summary", "")))
    where = checks.places_in(story)
    async with httpx.AsyncClient(transport=transport) as client:
        try:
            found = await photos.discover(client, [site, name], [name], used=used, sensitive=True, panel=False)
        except Exception as e:  # noqa: BLE001
            logger.info("wire: site photo discovery failed (%s)", type(e).__name__)
            return None
        found = [p for p in found if site_photo_fits(p, name, word, where) and plain_photo(p)]
        for p in found[:3]:
            img = await photos.fetch(client, p, min_width=photos.MIN_WIDTH)
            if img is None:
                continue
            meta = p.to_meta() if hasattr(p, "to_meta") else {"url": p.url}
            return img, {"url": p.url, "page": meta.get("page", ""), "title": meta.get("title", ""), "credit": p.credit,
                         "licence": meta.get("licence", "") or getattr(p, "licence", ""), "photo": meta, "site": site}
    return None


async def picture(ev: Any, *, used: set[str] | None = None, transport: Any = None, market: Any = None, now: datetime | None = None,
                  db: Any = None) -> tuple[bytes, dict[str, Any]]:
    """The wire picture, or none: (b"", {"kind": "text"}) is a text-only post. Stored on the event, so X does exactly the same.
    In order: a data card; a measured move's verified chart; the real chart of a price story whose candles show what it says
    (chart_matches); the public figure who leads the story; the named site of a major incident; else text only."""
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
        said = getattr(ev, "headline", "") or getattr(ev, "title", "") or ""
        why = chart_matches(said, bars) if len(bars) >= 20 else "fewer than 20 candles"
        if not why:
            label = f"{subject[2]} · 15m · Yahoo Finance"
            img, meta = clean.single_chart(bars, label)
            return img, {**base, **meta, "kind": "chart", "bars": bars, "label": label, "symbol": subject[0],
                         "chart_check": f"{len(bars)} candles, {(bars[-1][4] - bars[0][1]) / bars[0][1] * 100:+.2f}%"}
        logger.info("wire: no chart for %s: %s", getattr(ev, "id", None), why)
    got = await _person_photo(ev, used, transport)
    if got is None:
        got = await _site_photo(ev, used, transport)
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
