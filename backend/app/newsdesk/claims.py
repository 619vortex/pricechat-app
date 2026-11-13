"""The claim checker (owner, 2026-11-13, after "...$1.2B of North Korea's stolen Binance $BNB crypto"): factual accuracy comes BEFORE
viral optimisation. Every generated word that goes out (a hook, a caption, a debate question) is compared with the source texts, and a
text that introduces anything the sources don't establish is REJECTED, never silently "fixed".

Deterministic, no model involved. A text fails when it has
  - a number or amount no source gave
  - a ticker the sources don't establish (`ticker_problems`):
      coins       $BTC, $ETH, $SOL...: the coin itself must be named as the asset (Bitcoin, Ether), not inside a company name
                  ("Mercado Bitcoin", "Ethereum Foundation")
      stocks      $COIN, $NVDA, $TSLA...: the sources must talk about the company's SHARES or STOCK (or use the ticker): an outage, a hack
                  or a lawsuit at Coinbase is not a $COIN story
      tokens      $BNB, $HYPE, $OKB...: an exchange's name is not its token. Only when the sources name the token itself ("BNB fell 4%")
      any other   only when the sources print it
  - a named person the sources don't name
  - an accusation, a legal action or a crime word (stole, charged, seized, sued, arrested, indicted, fraud, laundering, hacked...) the
    sources don't use
  - a cause the sources don't state (sparks, triggers, because, due to, sends...), or a support / completion word in a form they don't
    use (backs, underpins, secured, completed: "lining up $26B" is not "secured $26B")
  - a quote that isn't verbatim in the sources
  - legal actions and accusations without a reliable source (`reliable_problems`): an official source (court, DOJ, SEC, a regulator),
    two independent outlets, or one major outlet; an accusation about an identifiable person also needs attribution and an official
    source or two outlets (scorecard.safety)

`check` returns the problems; an empty list means publishable on accuracy. network.package runs it on the final Telegram and X texts
(after cashtags and every other step), and a failure there is a hard gate: the event is not posted and the reason is recorded on the event
(facts["accuracy"]) for Admin, whatever its virality or editorial score.
"""

from __future__ import annotations

import re
from typing import Any

from app.newsdesk import checks

_rx = lambda p: re.compile(p, re.I)  # noqa: E731

# the asset itself, by name (a coin is its own asset)
COINS: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"), "ETH": ("ether", "ethereum", "eth"), "SOL": ("solana", "sol"), "XRP": ("xrp",), "DOGE": ("dogecoin", "doge"),
    "ADA": ("cardano", "ada"), "AVAX": ("avalanche", "avax"), "DOT": ("polkadot", "dot"), "LTC": ("litecoin", "ltc"), "TRX": ("tron", "trx"),
    "NEAR": ("near",), "LINK": ("chainlink", "link"), "TON": ("toncoin", "ton"), "USDT": ("tether", "usdt"), "USDC": ("usdc",),
}
# a company's shares: only when the sources discuss the stock
STOCKS: dict[str, tuple[str, ...]] = {
    "COIN": ("coinbase",), "NVDA": ("nvidia",), "TSLA": ("tesla",), "AAPL": ("apple",), "MSFT": ("microsoft",), "AMZN": ("amazon",),
    "META": ("meta",), "GOOGL": ("alphabet", "google"), "GOOG": ("alphabet", "google"), "MSTR": ("microstrategy", "strategy"), "HOOD": ("robinhood",),
    "CRCL": ("circle",), "GLXY": ("galaxy",), "MARA": ("marathon",), "RIOT": ("riot",), "PYPL": ("paypal",), "AMD": ("amd",), "INTC": ("intel",),
}
STOCK_WORDS = _rx(r"\b(shares?|stocks?|stock price|equity|market (?:value|cap\w*)|valuation|nasdaq-listed|nyse-listed|traded|trading at|"
                  r"premarket|pre-market|after-hours|earnings|investors? (?:sold|bought|dumped)|sell-?off in)\b")
# an exchange's or a protocol's token: the name of the company never establishes it
TOKENS: dict[str, tuple[str, ...]] = {"BNB": ("binance",), "HYPE": ("hyperliquid",), "OKB": ("okx",), "CRO": ("crypto.com",), "BGB": ("bitget",),
                                     "KCS": ("kucoin",), "HT": ("huobi", "htx"), "LEO": ("bitfinex",), "GT": ("gate.io",), "MX": ("mexc",)}
COMPANY_WRAP = _rx(r"\b(?:mercado|strike|bitcoin depot|ethereum foundation|solana (?:foundation|labs)|bitcoin (?:suisse|magazine|core|policy|well|group))\b")
CASHTAG = re.compile(r"(?<![\w$])\$([A-Z]{2,6})\b(?![\d.,])")
LEGAL = ("stole", "stolen", "steal", "theft", "thief", "thieves", "charged", "charges", "seized", "seize", "seizure", "seizes", "sued", "sues",
         "suing", "lawsuit", "arrested", "arrest", "indicted", "indictment", "convicted", "guilty", "sentenced", "jailed", "fraud", "laundered",
         "laundering", "launder", "hacked", "hack", "hacker", "exploit", "exploited", "drained", "accused", "alleged", "fined", "banned",
         "sanctioned", "subpoena", "probe", "investigation", "investigating", "raided", "raid", "frozen", "froze", "confiscated", "forfeiture",
         "pleaded", "plead", "scam", "scammed", "ponzi", "embezzled", "bribery", "manipulation", "insider")
LEGAL_ACTION = _rx(r"\b(charged|charges|seized|seizes|seizure|sued|sues|suing|lawsuit|arrested|arrest|indicted|indictment|convicted|guilty|"
                   r"sentenced|jailed|fined|banned|sanctioned|subpoena\w*|raided|confiscat\w+|forfeit\w*|pleaded|accused|alleg\w+|fraud\w*|"
                   r"launder\w*|stole|stolen|theft|embezzl\w*|brib\w*)\b")
CAUSAL = _rx(r"\b(sparks?|sparked|triggers?|triggered|causes?|caused|drives? (?:\w+ )?(?:up|down|higher|lower)|drove (?:\w+ )?(?:up|down|higher|lower)|fuels?|fueled|fuelled|sends? (?:\w+ ){0,3}(?:higher|lower|soaring|tumbling|plunging|up|down)|sent (?:\w+ ){0,3}(?:higher|lower|soaring|tumbling|plunging)|forces?|forced|"
             r"leads? to|led to|because(?: of)?|due to|blamed on|thanks to|in response to|prompts?|prompted|on the back of)\b")
# one fact holding up another, or a plan turned into a done deal (owner review 2026-11-13: "lining up $26 billion ... alongside the
# listing" became "$26 billion private placement backs Anthropic's IPO range"): only in the form the sources use
SUPPORT = _rx(r"\b(backs|backed|backing|underpins?|underpinned|props? up|propped up|bankrolls?|bankrolled|powers|powered|"
              r"secures?|secured|clinch(?:es|ed)|completes|completed|finali[sz]es|finali[sz]ed|seals?|sealed)\b")
# major outlets whose own reporting carries a legal action or an accusation on its own
RELIABLE = ("reuters", "bloomberg", "associated press", "ap news", "ap", "afp", "financial times", "ft", "wall street journal", "wsj",
            "new york times", "nyt", "bbc", "cnbc", "the guardian", "guardian", "washington post", "the economist", "axios", "politico",
            "nikkei", "the block", "coindesk", "al jazeera", "dw", "france 24", "sky news", "marketwatch", "barron's", "fortune")
OFFICIAL = _rx(r"\b(doj|department of justice|justice department|sec|cftc|fbi|treasury|ofac|court|prosecutors?|attorney general|regulator|"
               r"police|finra|fca|esma|ministry|central bank|federal reserve)\b")


def _has_word(text: str, word: str) -> bool:
    return bool(re.search(rf"(?<![\w$]){re.escape(word)}(?![\w])", text, re.I))


def _coin_named(src: str, names: tuple[str, ...]) -> bool:
    for n in names:
        for m in re.finditer(rf"(?<![\w$]){re.escape(n)}(?![\w])", src, re.I):
            before = src[max(0, m.start() - 20):m.start()].lower()
            after = src[m.end():m.end() + 20].lower()
            if COMPANY_WRAP.search(before + n + after):
                continue
            if len(n) <= 4 and not src[m.start():m.end()].isupper():  # "sol", "eth", "dot", "link", "ton" as plain words are not the coin
                continue
            return True
    return False


def ticker_problems(text: str, source_text: str) -> list[str]:
    """Every cashtag in `text` must be established by the SOURCE text (clean.established: an asset named as the asset, or a listed
    company's/exchange's ticker only where its shares, stock or the ticker itself are named), or printed in it as a cashtag."""
    from app.newsdesk import clean

    src = source_text or ""
    ok = {tag[1:] for _, tag in clean.established(src)}
    out = []
    for m in CASHTAG.finditer(text or ""):
        t = m.group(1)
        if t in ok or f"${t}" in src or (t in COINS and re.search(rf"(?<![\w$]){t}(?![\w])", src)):  # a coin's own symbol in the sources
            continue
        if t in TOKENS:
            out.append(f"${t}: the sources name {TOKENS[t][0].title()}, not its {t} token (a company's name is not its token)")
        elif t in STOCKS:
            out.append(f"${t}: the sources name {STOCKS[t][0].title()} but never its shares or stock")
        elif t in COINS:
            out.append(f"${t}: the sources never name the {t} asset itself")
        else:
            out.append(f"${t}: a ticker the sources don't establish")
    return list(dict.fromkeys(out))


def people_problems(text: str, source_text: str) -> list[str]:
    from app.newsdesk import scorecard

    low = (source_text or "").lower()
    out = []
    for p in scorecard.people_in(text or ""):
        if p == "a named role holder":
            continue
        last = p.split()[-1].lower()
        if last not in low:
            out.append(f"a person the sources don't name: {p}")
    return out


def legal_problems(text: str, source_text: str) -> list[str]:
    low = (source_text or "").lower()
    out = []
    for w in LEGAL:
        if re.search(rf"\b{w}\b", text or "", re.I) and not re.search(rf"\b{w[:5]}", low):
            out.append(f"'{w}': an accusation or legal action the sources don't state")
    return out


def reliable_problems(text: str, *, evidence: str = "", outlets: list[str] | None = None, source_text: str = "", attributed: bool = True) -> list[str]:
    """A legal action or accusation needs an official source, two independent outlets or one major outlet, and attribution."""
    from app.newsdesk import scorecard

    hit = LEGAL_ACTION.search(text or "")
    if not hit:
        return []
    names = [o.lower().strip() for o in outlets or [] if o]
    reliable = evidence == "official" or (evidence == "two outlets" and len(set(names)) >= 2) or any(n in RELIABLE for n in names) \
        or bool(OFFICIAL.search(source_text or "") and evidence in ("official", "two outlets"))
    out = []
    if not reliable:
        out.append(f"a legal action or accusation ('{hit.group(0).lower()}') from {', '.join(outlets or []) or 'an unnamed source'} alone: needs a "
                   f"court, regulator or DOJ source, two independent outlets, or a major outlet")
    if not attributed:
        out.append(f"a legal action or accusation ('{hit.group(0).lower()}') without attribution: name who says so")
    if not reliable:  # an accusation about an identifiable person: the stricter rule (official or two outlets) says why
        out += scorecard.safety(text or "", evidence, len(set(names)) or 1)
    return out


def link_problems(text: str, source_text: str) -> list[str]:
    """Every cause word (CAUSAL) and support / completion word (SUPPORT) in the text that the sources don't use in that form."""
    low = (source_text or "").lower()
    out = []
    for rx, what in ((CAUSAL, "a cause the sources don't state"), (SUPPORT, "a link or outcome the sources don't state")):
        for m in rx.finditer(text or ""):
            w = re.sub(r"\s+", " ", m.group(0).lower())
            if not re.search(rf"\b{re.escape(w)}\b", low):
                out.append(f"{what}: '{w}'")
    return list(dict.fromkeys(out))


def check(text: str, source_text: str, *, evidence: str = "", outlets: list[str] | None = None, attributed: bool = True,
          numbers: bool = True) -> list[str]:
    """Everything `text` claims that `source_text` doesn't establish ([] = publishable on accuracy)."""
    src = source_text or ""
    t = text or ""
    probs: list[str] = []
    if numbers:
        extra = checks.numbers(CASHTAG.sub(" ", t)) - checks.numbers(src)
        if extra:
            probs.append(f"a number no source gave: {', '.join(sorted(extra)[:4])}")
    probs += ticker_problems(t, src)
    probs += people_problems(t, src)
    probs += legal_problems(t, src)
    probs += link_problems(t, src)
    for q in re.findall(r"[\"“]([^\"”]{6,})[\"”]", t):
        if q.lower() not in src.lower():
            probs.append(f"a quote not in the sources: '{q[:40]}'")
    probs += reliable_problems(t, evidence=evidence, outlets=outlets, source_text=src, attributed=attributed)
    return list(dict.fromkeys(probs))


def for_event(ev: Any, text: str, source_text: str, outlets: list[str] | None = None, numbers: bool = True) -> list[str]:
    names = outlets if outlets is not None else [str(s.get("name") or "") for s in (getattr(ev, "sources", None) or [])]
    attributed = any(n and n.lower() in (text or "").lower() for n in names) or bool(re.search(r"\b(says?|said|according to|reports?|reported)\b", text or "", re.I))
    return check(text, source_text, evidence=getattr(ev, "evidence", "") or "", outlets=names, attributed=attributed, numbers=numbers)


__all__ = ["check", "for_event", "legal_problems", "link_problems","people_problems", "reliable_problems", "ticker_problems"]
