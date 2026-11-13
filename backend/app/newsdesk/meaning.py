"""Does the text MEAN what the sources say, not just reuse their words? (owner, 2026-11-13: "'Up to $1.4T' must never become '$1.4T'.
Preserve 'reportedly', 'allegedly', 'at least', 'could', 'may' and similar qualifiers. Headline verification checks meaning and factual
support, not just matching words.")

Each sentence of the text is matched to the ONE source sentence that carries it (most of its content words), and compared with it:
  figures      a qualifier on a figure in ANY source ("up to $1.4 trillion", "at least six", "less than $92", "about 300") must stay on that
               figure, in the same sense (an upper bound is not a lower bound). "About" may be added; nothing else may.
  hedges       the part of the carrying sentence the text restates: "allegedly / accused" must stay; "reportedly / according to /
               sources say / says" needs an attribution; "could / may / might / likely" needs a possibility; "plans / aims / expected to /
               lining up / in talks" needs a plan or possibility word; "at this point / for now / temporary" needs a time limit.
  negation     a "not / no / never / denies" in that part must stay, and the text can't add one that isn't there
  direction    "falls" can't come from a sentence that only says it rose, and the reverse
  superlatives "highest / record / biggest / first..." only when the sources use that word
  support      a sentence of the text no single source sentence carries (under SUPPORT_MIN of its content words) can't be compared, and is
               reported: it stitches or invents (hooks.merged does the same per report)
Deterministic, no model: it never rewrites, only rejects.
"""

from __future__ import annotations

import re

from app.newsdesk import checks

SUPPORT_MIN = 0.6
STRONG = 0.6  # a source sentence carrying this much of the text speaks for it too: its hedges count
QUAL = {
    "upper": r"up to|as much as|as many as|as high as|a maximum of|at most|no more than",
    "lower": r"at least|more than|over|above|upwards of|in excess of|a minimum of|exceeding|north of",
    "about": r"about|around|roughly|nearly|almost|approximately|an estimated|estimated|close to|just under|just over|some|~",
    "below": r"below|under|less than|fewer than|beneath",
}
QUAL_NAME = {"upper": "an upper bound ('up to')", "lower": "a lower bound ('at least', 'more than')", "about": "an approximation ('about')",
             "below": "a ceiling ('below', 'less than')"}
FIG = r"[$€£]?\s?(\d[\d,]*(?:\.\d+)?)\s?(%|percent|trillion|tn|billion|bn|million|mn|m|thousand|k)?\b"
UNIT = {"percent": "%", "tn": "trillion", "bn": "billion", "mn": "million", "m": "million", "k": "thousand"}
QUAL_FIG = {k: re.compile(rf"\b(?:{v})\s+(?:a\s+)?{FIG}", re.I) if k != "about" else re.compile(rf"(?:\b(?:{v})\s+|~)(?:a\s+)?{FIG}", re.I)
            for k, v in QUAL.items()}
ANY_FIG = re.compile(FIG, re.I)
PLUS_FIG = re.compile(rf"{FIG}\s?(?:\+|plus\b)", re.I)  # "70+ assets"

HEDGE = {
    "allege": r"alleg\w*|accus\w*|suspect\w*|purported\w*",
    "report": r"reportedly|reports?|reported|according to|sources? (?:say|said|familiar)|people familiar|said to|is said|are said",
    "says": r"says?|said|claims?|claimed|told|tells|warns?|warned|signals?|signalled|signaled|insists?|argues?|denies|denied|predicts?",
    "maybe": r"could|may|might|fears?|feared|worr(?:y|ies|ied)|concerns?|possibl\w*|potential\w*|likely|unlikely|risks?|threatens? to|would|considering|weighs|weighing|mulls?",
    "plan": r"plans?|planned|planning|aims?|aiming|seeks?|seeking|targets?|targeting|eyes|eyeing|expected to|expects?|set to|poised to|proposes?|"
            r"proposed|proposal|lining up|lines up|in talks|preparing|prepares|intends?|wants? to|hopes? to|will|upcoming|pending|bid to",
    "time": r"at this point|for now|so far|for the time being|temporar\w*|yet|until|interim|short-term",
}
# what the text must carry when the carrying part of the source has a hedge of that kind
NEEDS = {"allege": ("allege",), "report": ("report", "says", "allege", "maybe"), "says": ("says", "report", "allege", "maybe", "plan"),
         "maybe": ("maybe",), "plan": ("plan", "maybe"), "time": ("time",)}
HEDGE_RX = {k: re.compile(rf"\b(?:{v})\b", re.I) for k, v in HEDGE.items()}
NEG = re.compile(r"\b(?:not|no|never|none|neither|nor|without|denies|denied|rejects?|rejected|refuses?|refused|fails?|failed|halts?|halted|"
                 r"won't|can't|cannot|isn't|aren't|wasn't|weren't|doesn't|don't|didn't|hasn't|haven't)\b|n't\b", re.I)
UP = re.compile(r"\b(?:rises?|rose|risen|jumps?|jumped|surges?|surged|soars?|soared|climbs?|climbed|gains?|gained|rall(?:y|ies|ied)|higher|"
                r"highest|doubles?|doubled|spikes?|spiked|rebounds?|rebounded|up)\b", re.I)
DOWN = re.compile(r"\b(?:falls?|fell|fallen|drops?|dropped|slides?|slid|plunges?|plunged|tumbles?|tumbled|sinks?|sank|slumps?|slumped|"
                  r"lower|lowest|declines?|declined|dips?|dipped|crash\w*|halves|halved|down|sheds?|loses?|lost)\b", re.I)
SUPERLATIVE = re.compile(r"\b(?:highest|lowest|record|biggest|largest|smallest|first|last|most|least|worst|best|all-time|unprecedented|ever|"
                         r"only|peaks?|highs|lows|tops|topped|surpass\w*|exceeds?|exceeded|outstrips?|eclipses?|eclipsed)\b", re.I)


def _norm(n: str) -> str:
    n = n.replace(",", "")
    return n.rstrip("0").rstrip(".") if "." in n else n


def _unit(u: str | None) -> str:
    u = (u or "").lower()
    return UNIT.get(u, u)


WORDNUM = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                                            "sixteen seventeen eighteen nineteen twenty".split())}
WORDNUM.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
                "hundred": "100", "a hundred": "100", "dozen": "12", "a dozen": "12"})
WORDNUM_RX = re.compile(r"\b(" + "|".join(sorted(map(re.escape, WORDNUM), key=len, reverse=True)) + r")\b", re.I)


def _digits(text: str) -> str:
    """'at least six' and 'at least 6' are the same figure."""
    return WORDNUM_RX.sub(lambda m: WORDNUM[m.group(1).lower()], text or "")


def _qualified(text: str) -> dict[tuple[str, str], set[str]]:
    """(figure, unit) -> the qualifier kinds in front of it in the text."""
    out: dict[tuple[str, str], set[str]] = {}
    for kind, rx in QUAL_FIG.items():
        for m in rx.finditer(text or ""):
            out.setdefault((_norm(m.group(1)), _unit(m.group(2))), set()).add(kind)
    for m in PLUS_FIG.finditer(text or ""):
        out.setdefault((_norm(m.group(1)), _unit(m.group(2))), set()).add("lower")
    return out


def figure_problems(text: str, source_text: str) -> list[str]:
    """A qualifier the sources put on a figure that the text drops or changes ("up to $1.4 trillion" -> "$1.4 trillion")."""
    text, source_text = _digits(text), _digits(source_text)
    src_q = _qualified(source_text)
    txt_q = _qualified(text)
    out = []
    for m in ANY_FIG.finditer(text or ""):
        n, u = _norm(m.group(1)), _unit(m.group(2))
        need = src_q.get((n, u)) or (src_q.get((n, "")) if not u else None) or set()
        if not need:
            continue
        have = txt_q.get((n, u), set())
        if not have & need:
            shown = m.group(0).strip()
            kinds = " or ".join(QUAL_NAME[k] for k in sorted(need))
            out.append(f"drops the qualifier on {shown}: the sources give {kinds}" if not have - {"about"} else
                       f"changes the qualifier on {shown}: the sources give {kinds}")
    # a qualifier the text adds to a figure the sources state plainly (other than "about"), e.g. "at least $5M" from "$5M"
    for (n, u), kinds in txt_q.items():
        added = kinds - {"about"} - src_q.get((n, u), set())
        if added and (n, u) not in src_q and n in checks.numbers(source_text):
            out.append(f"adds {QUAL_NAME[sorted(added)[0]]} to {n} {u}".rstrip() + " that no source gives")
    return list(dict.fromkeys(out))


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9$%][a-z0-9$%'’.-]*", (s or "").lower())


def _stem(w: str) -> str:
    w = w.strip(".'’").replace("’", "'")
    w = re.sub(r"'s$", "", w)
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _content(s: str) -> set[str]:
    return {_stem(w) for w in _words(s) if len(w) > 2 and w not in checks.STOP}


def split(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“$0-9])|\n+|\s+[|•·]\s+", text or "")
    return [p.strip() for p in parts if len(_content(p)) >= 2]


def carrier(sentence: str, sources: list[str]) -> tuple[str, float]:
    """The source sentence that carries most of `sentence`'s content words, and that share."""
    want = _content(sentence)
    if not want:
        return "", 1.0
    best, share = "", 0.0
    for s in sources:
        got = len(want & _content(s)) / len(want)
        if got > share or (got == share and best and len(s) < len(best)):
            best, share = s, got
    return best, share


def _window(src_sentence: str, sentence: str) -> str:
    """The part of the carrying sentence the text restates: from the first to the last shared word, three words either side."""
    want = _content(sentence)
    words = src_sentence.split()
    hits = [i for i, w in enumerate(words) if _stem(re.sub(r"^[^\w$%]+|[^\w%]+$", "", w.lower())) in want]
    if not hits:
        return ""
    return " ".join(words[max(0, hits[0] - 3): hits[-1] + 4])


def _speaker_named(span: str, m: re.Match[str], text: str) -> bool:
    """The one who says it (a capitalised name just before the verb, or just after a trailing ", Elliptic says") is named in the text."""
    # the words right before the verb, back to the last comma or clause break ("Saudi Arabia told OPEC"; "..., Elliptic says"): never a
    # name further back ("from the Binance hack, Elliptic says" is Elliptic's claim, not Binance's)
    head = re.split(r"[,;:–—]|\b(?:that|which|who|as|after|while)\b", span[: m.start()])[-1]
    names = re.findall(r"[A-Z][\w’'-]+", head)[-3:]
    low = text.lower()
    return any(len(w) > 2 and re.sub(r"[’']s$", "", w).lower() in low for w in names)


def sentence_problems(sentence: str, sources: list[str], source_text: str, attribution: bool = True) -> list[str]:
    s = sentence.strip()
    carry, share = carrier(s, sources)
    if share < SUPPORT_MIN:
        return [f"no single source sentence carries \"{s[:60]}\" ({share:.0%} of its words at most)"]
    win = _window(carry, s)
    out = []
    have = {k for k, rx in HEDGE_RX.items() if rx.search(s)}
    # the hedges of every sentence that clearly carries the claim, not only the closest: an outlet's headline often drops the "Elliptic
    # says" its own story keeps (2026-11-12 audit, the North Korea memecoin post)
    spans = [win] + [_window(x, s) for x in sources if x != carry and carrier(s, [x])[1] >= STRONG]
    # attribution ("says", "reportedly") is the outlet's choice of voice: when one carrying sentence states the claim in its own voice, we
    # may too. Qualifiers that change the claim (alleged, could, plans, for now) bind whatever any other outlet wrote.
    flat = {k for k in ("says", "report") if any(not HEDGE_RX[k].search(x) and not HEDGE_RX["report"].search(x) for x in spans)}
    for span in spans:
        for kind, rx in HEDGE_RX.items():
            m = rx.search(span)
            if not m or have & set(NEEDS[kind]) or kind in flat or (not attribution and kind in ("says", "report")):
                continue
            if kind in ("says", "report") and _speaker_named(span, m, s):
                continue  # "Saudi output jumps 26%" from "Saudi Arabia told OPEC...": the claimant is the subject
            out.append(f"drops the qualifier '{m.group(0).lower()}' from the source: \"{span[:90]}\"")
    if NEG.search(win) and not NEG.search(s):
        out.append(f"drops a negation ('{NEG.search(win).group(0).lower()}') from the source: \"{win[:90]}\"")
    if NEG.search(s) and not NEG.search(win) and not any(NEG.search(x) and carrier(s, [x])[1] >= SUPPORT_MIN for x in sources):
        out.append(f"adds a negation ('{NEG.search(s).group(0).lower()}') no source sentence has")
    for mine, other, name in ((UP, DOWN, "a rise"), (DOWN, UP, "a fall")):
        if mine.search(s) and not mine.search(win) and other.search(win) and not any(
                mine.search(x) and carrier(s, [x])[1] >= SUPPORT_MIN for x in sources):
            out.append(f"says {name} ('{mine.search(s).group(0).lower()}') where the source says '{other.search(win).group(0).lower()}'")
    low = (source_text or "").lower()
    for m in SUPERLATIVE.finditer(s):
        w = m.group(0).lower()
        if not re.search(rf"\b{re.escape(w)}\b", low):
            out.append(f"a superlative or comparison the sources don't use: '{w}'")
    return out


# qualifiers that bind a term wherever a source puts them on it ("Trump's temporary waiver of some sanctions" vs "Trump lifts sanctions",
# audit 2026-11-12; "an alleged Chinese spy" vs "a Chinese spy"), even when that sentence doesn't carry the rest of the text
BINDING = {
    "allege": re.compile(r"\b(?:alleg\w*|accus\w*|suspected|purported\w*|unconfirmed|unverified)\b", re.I),
    "time": re.compile(r"\b(?:temporar\w*|interim|for now|at this point|for the time being)\b", re.I),
    "maybe": re.compile(r"\b(?:could|may|might|possibly|potentially)\b"),  # case-sensitive: "May" is a month
}
NEAR = 4  # a qualifier binds the words after it ("alleged Chinese spy", "temporary waiver of some sanctions"), not the ones before
COMMON = {"price", "prices", "market", "markets", "crypto", "bitcoin", "stocks", "shares", "trading", "according", "report", "reports",
          "president", "company", "companies", "people", "billion", "million", "trillion", "percent", "years", "week", "month", "today",
          "chinese", "russian", "american", "israeli", "iranian", "ukrainian", "british", "european", "korean", "japanese", "indian",
          "saudi", "german", "french", "turkish", "palestinian", "lebanese", "syrian", "emirati", "qatari", "polish", "brazilian"}


def binding_problems(sentence: str, sources: list[str]) -> list[str]:
    have = {k for k, rx in HEDGE_RX.items() if rx.search(sentence)}
    keys = {_stem(w) for w in _words(sentence) if len(w) >= 3 and w not in checks.STOP and w not in COMMON}  # "spy", "ceo" count
    out = []
    for x in sources:
        words = x.split()
        for kind, rx in BINDING.items():
            if have & set(NEEDS[kind]):
                continue
            for m in rx.finditer(x):
                at = len(x[: m.start()].split())
                span = words[at: at + NEAR + 1]
                near = {_stem(re.sub(r"^[^\w$%]+|[^\w%]+$", "", w.lower())) for w in span}
                hit = sorted(keys & near)
                if hit:
                    out.append(f"the sources qualify '{hit[0]}' with '{m.group(0).lower()}': \"{' '.join(span)[:90]}\"")
                    break
    return out


def problems(text: str, source_text: str, sentences: list[str] | None = None, *, strict: bool = True) -> list[str]:
    """Everything in `text` whose meaning the sources don't support ([] = it says what they say). strict (headlines and hooks): every
    sentence must be carried by one source sentence. A caption's context lines may join reports (hooks.merged and the claim checks
    cover that), so with strict=False an uncarried sentence is skipped, only the sentences that are carried get compared, and attribution
    ("says", "reportedly") is the writer's to phrase (claims.reliable_problems and the alleged rule bind accusations); every qualifier
    that changes the claim (up to, alleged, could, plans, for now) still binds.
    Questions make no claim and are skipped."""
    sources = [x for x in (sentences if sentences else split(source_text)) if x.strip()]
    if not sources:
        sources = [source_text or ""]
    out = figure_problems(text, source_text)
    for s in split(text) or ([text] if (text or "").strip() else []):
        if s.rstrip().endswith("?"):
            continue
        found = sentence_problems(s, sources, source_text, attribution=strict) + binding_problems(s, sources)
        out += found if strict else [p for p in found if not p.startswith("no single source sentence")]
    return list(dict.fromkeys(out))


__all__ = ["binding_problems", "carrier", "figure_problems", "problems", "sentence_problems", "split"]
