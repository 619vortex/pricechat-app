"""What a post must pass to go out, whoever or whatever wrote it. Any failure stops it.

A URL makes a post cost $0.20 instead of $0.015 on X's pay-per-use pricing. Since 2026-11-11 the owner wants the original reporting linked
on every post: a link may appear only when it is one of the event's own verified source links (`links`); any other address still fails.
Numbers must be ones a source gave: a model can't invent a figure and have it published (digits inside an allowed link don't count).
"""

import re
from dataclasses import dataclass, field
from typing import Any

MAX_LEN = 280
STOP = set("the a an and or of to in on at for from by with as is are was were be been it its this that after before over under into out up down says say said new not no has have had will would can could may might than amid about who what when how why more most their his her they he she we you your our but if while".split())
NOT_NEWS = re.compile(r"\b(rumou?rs?|reportedly|allegedly|unconfirmed|sources? say|speculat\w+|price predictions?|prediction|opinion|analysis|editorial|sponsored|podcast|quiz|how to|explained|what to know|what we know|why you|should you|best \w+ to|top \d+|\d+ (ways|things|reasons|stocks to)|watch:|live:|live updates|newsletter)\b", re.I)
FORBIDDEN = re.compile(r"\b(guaranteed|risk[- ]free|buy now|sell now|you should buy|you should sell|to the moon|100x|1000x|get rich|can't lose|financial advice|insider tip)\b", re.I)
URLISH = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|net|org|io|co|ai|gov|uk|live|xyz|finance|news|eu|me|tv|app|dev)\b)", re.I)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
EXPLAINER = re.compile(r"^(what|what's|why|how|who|when|where|is|are|does|do|can|will|should)\b|\?\s*$", re.I)
URGENT = ("🚨", "BREAKING", "MARKET ALERT", "BIG MOVE")
# Too common to mean two stories are the same one.
COMMON_NAMES = {"bitcoin", "ethereum", "crypto", "crypto's", "solana", "stocks", "markets", "market", "wall", "street", "trump", "china", "europe", "america", "american", "federal", "reserve", "bank", "treasury", "government", "congress", "senate", "white", "house", "president", "ceo", "new", "york", "world", "global", "oil", "gold", "silver"}


@dataclass
class Result:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return next((c["detail"] for c in self.checks if not c["passed"]), "")


def numbers(text: str) -> set[str]:
    """Every number, comparable: 1,000.50 and 1000.5 are the same."""
    out = set()
    for raw in NUMBER.findall(text or ""):
        n = raw.replace(",", "")
        out.add(n.rstrip("0").rstrip(".") if "." in n else n)
    return out


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9$%]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in STOP}


def fingerprint(text: str) -> str:
    return " ".join(sorted(tokens(text)))


def names(title: str) -> set[str]:
    """The names a headline is about (Binance, Nvidia), for telling that two different headlines are one story."""
    words = re.findall(r"[A-Z][A-Za-z0-9']{3,}", title or "")
    return {w.lower() for w in words if w.lower() not in COMMON_NAMES and w.lower() not in STOP}


def is_explainer(title: str) -> bool:
    return bool(EXPLAINER.search((title or "").strip()))


def similarity(a: str, b: str) -> float:
    x, y = set(a.split()), set(b.split())
    return len(x & y) / len(x | y) if x and y else 0.0


EXCLUSIVE = re.compile(r"\b(exclusive|scoop|we can reveal|first reported by price chat|our reporting|price chat (has )?learned)\b", re.I)


def check_post(text: str, *, source_text: str, source_names: list[str], breaking: bool = False, as_edited: bool = False, max_len: int = MAX_LEN,
               links: list[str] | tuple[str, ...] = ()) -> Result:
    """source_text: everything the sources said, plus the verified figures. as_edited: a person changed it, and may add figures they checked.
    links: the event's own verified source links; only these may appear in the text."""
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail or name})

    t = (text or "").strip()
    bare = t
    for url in sorted(links, key=len, reverse=True):
        bare = bare.replace(url, "")
    weighted = len(bare) + 23 * sum(t.count(u) for u in links) if links else len(t)  # X counts a link as 23 characters
    add("length", 0 < weighted <= max_len, f"{weighted} characters (limit {max_len})")
    add("no_link", not URLISH.search(bare), "It contains a link that isn't one of the story's own sources" if links else
        "It contains a link or web address, which costs $0.20 instead of $0.015 on X")
    add("not_exclusive", not EXCLUSIVE.search(bare), "It claims exclusive or original reporting: the outlets reported it")
    add("no_mentions", "@" not in t, "It mentions an account")
    add("hashtags", t.count("#") <= 1, "Too many hashtags")
    add("claims", not FORBIDDEN.search(t), "It makes a promise or gives investment advice")
    add("not_rumour", not NOT_NEWS.search(t) or as_edited, "It reads as rumour or opinion, not reported fact")
    add("attribution", not source_names or any(n.lower() in t.lower() for n in source_names), f"It doesn't name its source ({', '.join(source_names)})")
    add("urgency", breaking or not any(w in t for w in URGENT[:2]), "It says BREAKING, but isn't a verified breaking story")
    if not as_edited:
        extra = numbers(bare) - numbers(source_text)
        add("figures", not extra, f"Figures no source gave: {', '.join(sorted(extra))}")
    unsupported = unsupported_tickers(t, source_text)
    add("tickers", not unsupported, f"Tickers the sources don't establish: {', '.join(unsupported)} (a company's name never brings its token or stock)")
    return Result(all(c["passed"] for c in checks), checks)


CASHTAG = re.compile(r"(?<![\w$])\$([A-Z]{2,6})\b")


def unsupported_tickers(text: str, source_text: str) -> list[str]:
    """Cashtags in the text that the sources don't establish (owner, 2026-11-13, after "stolen Binance $BNB crypto"): a tag passes only when
    the source text names the symbol itself or clean.established() finds the asset there (Bitcoin; Nvidia's shares...)."""
    from app.newsdesk import clean

    tags = list(dict.fromkeys(f"${m.group(1)}" for m in CASHTAG.finditer(text or "")))
    if not tags:
        return []
    src = source_text or ""
    ok = {tag for _, tag in clean.established(src)}
    return [tag for tag in tags if tag not in ok and not re.search(rf"(?<![\w$])\$?{tag[1:]}\b", src)]


# X refuses (403 Forbidden) a post made through the API with more than one cashtag (owner, 2026-11-14). X keeps the first cashtag; the
# others go back to plain words. This only takes "$" signs away, so it can't add a claim, and the final gate still holds what is sent.
X_MAX_CASHTAGS = 1
_TAG_WITH_SPACE = re.compile(r"( ?)(?<![\w$])\$([A-Z]{2,6})\b")


def cashtag_count(text: str) -> int:
    """How many "$" cashtags the text has (each mention counts: X's limit is on cashtags in a post)."""
    return len(CASHTAG.findall(text or ""))


def _names_tag(before: str, tag: str) -> bool:
    """Whether the words just before a cashtag are the asset's name or symbol, so the tag was added after them and can simply go."""
    from app.newsdesk import clean

    tail = before[-60:]
    if re.search(rf"\b{re.escape(tag)}$", tail):
        return True
    for rx, t in clean.established(tail):
        if t == f"${tag}" and any(m.end() == len(tail) for m in rx.finditer(tail)):
            return True
    return False


def one_cashtag(text: str) -> str:
    """The text with only its first cashtag. A later tag right after its name ("Ether $ETH", as clean.cashtag writes it) is dropped, so
    the sentence reads as the reporting wrote it; a tag standing for the name ("$ETH rose", "$SOL -4%") becomes the bare symbol."""
    kept = False

    def repl(m: re.Match[str]) -> str:
        nonlocal kept
        space, tag = m.group(1), m.group(2)
        if not kept:
            kept = True
            return m.group(0)
        if space and _names_tag(m.string[:m.start()], tag):
            return ""
        return space + tag

    return _TAG_WITH_SPACE.sub(repl, text or "")


def final_gate(text: str, source_text: str, chart_symbol: str = "", *, wire: bool = False) -> list[str]:
    """The last check on what actually goes out, after every formatting step (cashtags, credits, labels): the problems, or [] to send.
    A hard gate, whatever wrote the words (the desk's writer, a person, the viral hooks) and whatever the story's score.
    wire: the wire style (app/newsdesk/wire) also holds hype and superlatives the sources don't support (wording_problems), on top."""
    from app.newsdesk import clean

    problems = []
    bad = unsupported_tickers(text, source_text)
    if bad:
        problems.append(f"tickers the sources don't establish: {', '.join(bad)}")
    tag = next((t for t, sym in clean.CHARTED.items() if sym == chart_symbol), "") if chart_symbol else ""
    if tag and tag not in {t for _, t in clean.established(source_text or "")} and not re.search(rf"\b{tag[1:]}\b", source_text or ""):
        problems.append(f"a {tag} price chart the sources don't establish")
    problems += claim_problems(story_of(text), source_text)
    if wire:
        problems += wording_problems(story_of(text), source_text)
        problems += qualifier_problems(story_of(text), source_text)  # "six" where the sources say "at least six"
    return problems


# The wire style's wording rules (owner, 2026-11-14: no "massive", "huge", "explodes" unless the figures support it; no unsupported
# superlatives). Deterministic: a word passes only on the sources' own use of it (and, for intensity words, a figure in the same sentence).
INTENSE = re.compile(r"\b(massive(?:ly)?|huge(?:ly)?|enormous(?:ly)?|gigantic|colossal|monster|mega|stunning(?:ly)?|shock(?:ing|ingly)?|explod(?:e|es|ed|ing)|"
                     r"explosive|skyrocket(?:s|ed|ing)?|rocket(?:s|ed|ing)|moon(?:s|ed|ing)|bloodbath|carnage|meltdown|frenzy|mania|insane(?:ly)?|crazy|"
                     r"wild(?:ly)?|epic|nuked?)\b", re.I)
SUPERLATIVE = re.compile(r"\b(record(?:-breaking|-high|-low)?|all-time|biggest|largest|highest|lowest|worst|best|fastest|steepest|sharpest|deepest|"
                         r"strongest|weakest|first[- ]ever|ever|unprecedented|historic|never before)\b", re.I)
MOVE_WORDS = re.compile(r"\b(crash(?:es|ed|ing)?|plung(?:e|es|ed|ing)|plummet(?:s|ed|ing)?|soar(?:s|ed|ing)?|surg(?:e|es|ed|ing)|spik(?:e|es|ed)|"
                        r"tank(?:s|ed|ing)?|collaps(?:e|es|ed|ing)|tumbl(?:e|es|ed|ing)|slump(?:s|ed|ing)?)\b", re.I)


def _word_rx(word: str) -> str:
    """The word's family as a pattern: 'plunges' matches plunge/plunged/plunging; a word with no suffix matches only itself ('ever' is not
    'every')."""
    w = re.escape(word.lower()).replace(r"\ ", "[- ]").replace("\\-", "[- ]")
    for suffix in ("ingly", "ing", "ed", "es", "ly", "s", "e"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return rf"\b{w[: -len(suffix)]}\w*"
    return rf"\b{w}\b"


def _sources_use(word: str, source_text: str) -> bool:
    return bool(re.search(_word_rx(word), source_text or "", re.I))


def wording_problems(story: str, source_text: str) -> list[str]:
    """Hype, superlatives and loaded move words the sources don't support. Per sentence: an intensity word needs the sources' own word AND
    a figure in the sentence; a superlative needs the sources' own word; a crash/plunge/soar word needs a figure in the sentence or the
    sources' own word."""
    from app.newsdesk.telegram import sentences as _sents

    out: list[str] = []
    parts = [s for p in re.split(r"\n\s*\n", story or "") for s in (_sents(p) or [p])]
    for s in parts:
        figure = bool(re.search(r"\d", s))
        for m in INTENSE.finditer(s):
            if not (_sources_use(m.group(0), source_text) and figure):
                out.append(f"hype the figures don't support: {m.group(0)}")
        for m in SUPERLATIVE.finditer(s):
            if not _sources_use(m.group(0), source_text):
                out.append(f"a superlative no source uses: {m.group(0)}")
        for m in MOVE_WORDS.finditer(s):
            if not figure and not _sources_use(m.group(0), source_text):
                out.append(f"a loaded move word without a figure: {m.group(0)}")
    return list(dict.fromkeys(out))


# --- factual qualifiers (owner, 2026-11-14: "at least six" must never go out as "six") ------------------------------------------------
# A figure the sources give only with a qualifier ("at least six", "up to $3 billion", "an estimated 40%") is not an exact figure. Wire copy
# that drops the qualifier gets it back (restore_qualifiers); the wire final gate holds whatever is still stated as exact (qualifier_problems).
QUALIFIERS = ("at least", "up to", "as many as", "as much as", "as few as", "more than", "less than", "fewer than", "no fewer than", "at most",
              "nearly", "almost", "about", "around", "roughly", "approximately", "close to", "an estimated", "estimated", "reportedly", "allegedly",
              "upwards of", "just over", "just under", "some")
_Q_ALT = "|".join(sorted((re.escape(q) for q in QUALIFIERS), key=len, reverse=True))
NUMBER_WORDS = {"two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
                "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
                "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
                "eighty": "80", "ninety": "90", "hundreds": "100s", "thousands": "1000s", "dozens": "12s"}
_FIGURE = re.compile(r"(?<![\w.,$€£])(?:[$€£]\s?)?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|percent\b|[BMK]\b|bn\b|billion\b|million\b|trillion\b))?"
                     r"|\b(?:" + "|".join(NUMBER_WORDS) + r")\b", re.I)
_QUAL_BEFORE = re.compile(rf"(?:(\w+)\W+)?\b({_Q_ALT})\s+$", re.I)  # "... reported. At least six ...": a sentence may start with one
# "talks about 30,000 bitcoin", "worries around $2 billion": there "about" / "around" is a preposition, not a qualifier
_PREPOSITIONAL = {"about", "around", "some"}
_TALK = {"talk", "talks", "talked", "worry", "worries", "worried", "concern", "concerns", "concerned", "question", "questions", "know", "knew",
         "think", "thinks", "care", "cares", "news", "report", "reports", "said", "says", "say", "asked", "asks", "story", "stories", "came",
         "comes", "bring", "brings", "brought", "built", "centred", "centered", "revolves", "focus", "focused"}
# a word between a qualifier and its figure that keeps them together: "reportedly killed six", "at least another six"
_BRIDGE = re.compile(r"\b(killed|kills|kill|injured|wounded|dead|died|another|a further|an additional)\s+$", re.I)


def _figure_key(raw: str) -> str:
    """'six' and '6' are one figure; '$2.7 million' and '$2.7M' too."""
    s = raw.lower().strip()
    if s in NUMBER_WORDS:
        return NUMBER_WORDS[s]
    m = re.match(r"([$€£]?)\s?(\d[\d,]*(?:\.\d+)?)\s?(%|percent|bn|billion|million|trillion|[bmk])?", s)
    if not m:
        return s
    n = m.group(2).replace(",", "")
    unit = {"percent": "%", "bn": "b", "billion": "b", "million": "m", "trillion": "t"}.get(m.group(3) or "", m.group(3) or "")
    if unit == "k":  # "$70K" is "$70,000"
        n, unit = f"{float(n) * 1000:.6f}", ""
    n = n.rstrip("0").rstrip(".") if "." in n else n
    return f"{m.group(1)}{n}{unit}"


def _noun_after(text: str, end: int) -> str | None:
    """The word a figure counts ("six Palestinians" -> palestinian); "" when a small word follows ("$1 billion of ..."); None when nothing
    does (punctuation, the end: "at least six, Gaza ...")."""
    m = re.match(r"\s+([A-Za-z]+)", text[end:])
    if not m:
        return None
    w = m.group(1).lower()
    if w in STOP or len(w) < 3:
        return ""
    return w[:-1] if w.endswith("s") and len(w) > 4 else w


def _qualifier(before: str) -> str:
    q = _QUAL_BEFORE.search(before)
    if q is None:
        return ""
    word = q.group(2).lower()
    if word in _PREPOSITIONAL and (q.group(1) or "").lower() in _TALK:
        return ""
    return word


def _figures(text: str) -> list[tuple[str, str, str | None, int, int]]:
    """(key, qualifier or "", counted noun, start, end) for every figure in the text."""
    out = []
    t = text or ""
    for m in _FIGURE.finditer(t):
        before = t[:m.start()]
        q = _qualifier(before)
        if not q:  # "reportedly killed six": a qualifier one bridging word back
            b = _BRIDGE.search(before)
            if b is not None:
                q = _qualifier(before[:b.start()])
        out.append((_figure_key(m.group(0)), q, _noun_after(t, m.end()), m.start(), m.end()))
    return out


def _source_qualifier(key: str, noun: str | None, source_figures: list[tuple[str, str, str | None, int, int]]) -> str:
    """The qualifier the sources attach to this figure counting the same thing (the same noun after it, or nothing after one of them), or ""."""
    for k, q, n, _, _ in source_figures:
        if k == key and q and (noun is None or n is None or n == noun):
            return q
    return ""


# hedges on the claim rather than on the figure ("reportedly killed 20"): never moved in front of a figure, only held (qualifier_problems)
HEDGES = {"reportedly", "allegedly"}


def qualifier_problems(story: str, source_text: str) -> list[str]:
    """Figures the story states as exact where the sources qualify them ("six" when the sources say "at least six")."""
    src = _figures(source_text or "")
    out = []
    for key, q, noun, s, e in _figures(story or ""):
        if q:
            continue
        theirs = _source_qualifier(key, noun, src)
        if theirs and theirs in HEDGES and re.search(r"\b(reportedly|allegedly|said|says|according to|claims?|claimed)\b", story, re.I):
            continue  # the post attributes or hedges the claim itself
        if theirs:
            out.append(f"a figure stated as exact that the sources qualify: {story[s:e]} (the sources say {theirs} {story[s:e]})")
    return list(dict.fromkeys(out))


def restore_qualifiers(text: str, source_text: str) -> str:
    """The same text with each qualifier the sources attach to a figure put back in front of it. Only the sources' own words are added."""
    src = _figures(source_text or "")
    out, last = [], 0
    t = text or ""
    for key, q, noun, s, e in _figures(t):
        if q:
            continue
        theirs = _source_qualifier(key, noun, src)
        if not theirs or theirs in HEDGES:
            continue
        out.append(t[last:s])
        out.append(f"{theirs} ")
        last = s
    out.append(t[last:])
    return "".join(out)


# --- same event (owner, 2026-11-14: post 166 linked an unrelated Al Jazeera story because both said "Israeli") -------------------------
# Countries, capitals, regions and their adjectives say where, not what happened: two stories never become one on these alone.
PLACES = set("""afghanistan africa albania algeria america argentina armenia asia australia austria azerbaijan bahrain bangladesh beijing beirut
belarus belgium berlin brazil britain brussels bulgaria cairo california canada caribbean chile china colombia crimea cuba cyprus damascus delhi
denmark donbas doha dubai egypt england europe finland france gaza geneva georgia germany greece haiti hungary india indonesia iran iraq
ireland israel istanbul italy japan jerusalem jordan kabul kashmir kazakhstan kenya korea kuwait kyiv lebanon libya london madrid malaysia
mexico moscow netherlands nigeria norway oman pakistan palestine paris peru philippines poland portugal pyongyang qatar rafah riyadh romania
russia scotland seoul serbia singapore somalia spain sudan sweden switzerland syria taiwan tehran texas thailand tokyo turkey ukraine venezuela
vietnam wales washington yemen""".split())
DEMONYMS = set("""afghan african albanian algerian american americans arab argentine armenian asian australian austrian azerbaijani bahraini
belarusian belgian brazilian british bulgarian canadian chilean chinese colombian cuban cypriot danish dutch egyptian emirati english european
filipino finnish french georgian german greek indian indonesian iranian iranians iraqi irish israeli israelis italian japanese jordanian kazakh
kenyan korean kuwaiti lebanese libyan malaysian mexican nigerian norwegian omani pakistani palestinian palestinians peruvian polish portuguese
qatari romanian russian russians saudi scottish serbian somali spanish sudanese swedish swiss syrian taiwanese thai turkish ukrainian ukrainians
venezuelan vietnamese welsh yemeni western eastern northern southern""".split())
# capitalised in a Title Case Headline, but ordinary words: never evidence that two stories are one
ORDINARY = set("""about after again against ahead amid army attack attacks back backs bank banks bans become becomes bill bills board breaks
brings buys calls chief city civil clients cuts deal deals debuts defence defense digital ends exchange faces falls files fire first force
forces gets gives goes group grows health hits holds hospital jumps kills killed launches leader leaders live makes military minister ministry
more most network news nuclear official officials opens order over plans plunges police president prime probe raises record report reports
rises says sees seeks sells settle shares signs site soars state states strike strikes takes talks troops urges warns week wins world year
years below above under sets new lows highs low high selloff sell-off slides tumbles drops sinks dips rally rallies amid""".split())


def distinctive_names(title: str) -> set[str]:
    """The names that say who or what a headline is about (Bybaz, Nasser, Aramco, Abdel-Malek): no places, no adjectives of nationality, no
    ordinary words a Title Case Headline capitalises, none of the desk's common names."""
    out = set()
    for n in names(title):
        n = re.sub(r"'s$", "", n)
        if n in COMMON_NAMES or n in PLACES or n in DEMONYMS or n in ORDINARY or n in STOP or len(n) < 4:
            continue
        out.add(n)
    return out


def places_in(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (title or "").lower()) if w in PLACES}


LEVEL_ASSETS = {"bitcoin", "btc", "ether", "ethereum", "eth", "solana", "xrp", "gold", "silver", "oil", "brent", "crude", "nasdaq", "dow"}
_LEVEL = re.compile(r"\b(below|under|beneath|above|over|past|tops?|topped|crosses|crossed|to)\s+(\$\s?\d[\d,]*(?:\.\d+)?\s?[kK]?)", re.I)
_LEVEL_SIDE = {"below": "down", "under": "down", "beneath": "down", "to": "to"}


def price_levels(title: str) -> set[tuple[str, str]]:
    """(side, level) for each price level a headline says an asset went through: ("down", "$69000")."""
    return {(_LEVEL_SIDE.get(m.group(1).lower(), "up"), _figure_key(m.group(2).replace(" ", ""))) for m in _LEVEL.finditer(title or "")
            if m.group(1).lower() != "to"}


def same_event(a: str, b: str) -> bool:
    """Two headlines tell the same event: the same distinctive subject (two names, or one and a fair share of words) or most of their
    words, and nothing that says otherwise (different subjects, different places, different figures). Generic overlap ("Israeli", "Gaza",
    "kills") is never enough."""
    if not a or not b:
        return False
    if a.strip().lower() == b.strip().lower():
        return True
    pa, pb = places_in(a), places_in(b)
    if pa and pb and not pa & pb:
        return False  # a strike in Gaza and a strike in Lebanon
    la, lb = price_levels(a), price_levels(b)
    if la & lb and {w for w in tokens(a) & tokens(b) if w in LEVEL_ASSETS}:
        return True  # one asset through one price level, the same way: "Bitcoin breaks below $69,000" / "Bitcoin tumbles below $69,000"
    if la and lb and not la & lb:
        return False  # below $69,000 and above $69,000; $70,000 and $4,000
    na, nb = distinctive_names(a), distinctive_names(b)
    if na and nb and not na & nb:
        return False  # Bybaz hacked and Qorvex hacked; a footballer killed and a hospital struck
    near = similarity(fingerprint(a), fingerprint(b))
    shared = na & nb
    # beyond the subject's own name, something the two say in common: "Deutsche Bank ... custody" twice, not "Deutsche Bank" and a fine
    said = {t for t in tokens(a) & tokens(b) if re.sub(r"'s$", "", t) not in shared and t not in COMMON_NAMES and t not in ORDINARY}
    fa, fb = {k for k, *_ in _figures(a)}, {k for k, *_ in _figures(b)}
    money = {k for k in fa & fb if k[:1] in "$€£" or k.endswith(("%", "b", "m"))}  # the same amount: "$1.2B" and "$1.2 billion"
    if len(shared) >= 2 or (shared and ((near >= 0.15 and said) or money)):
        return True
    if near >= 0.34:
        if near < 0.5 and fa and fb and not fa & fb:
            return False  # "kills six" and "kills 20" with little else in common: not shown to be one event
        return True
    return False


# what a caption carries besides the story: the sources line, the photo credit, the signature (their names and numbers aren't claims)
NOT_STORY = re.compile(r"^(?:🔗|🥝|Price Chat News|Source:|Sources:|Via |Photo|Image|Illustration|AI illustration|Credit|Chart:|Data:|@thepricechat)", re.I)
# accusations and legal actions: the caption may use one only when the sources do (same stem)
ACCUSE = {
    "steal": r"\b(?:stole|stolen|steal\w*|theft|thie(?:f|ves))\b", "hack": r"\bhack(?:s|ed|er|ers|ing)?\b", "launder": r"\blaunder\w*\b",
    "fraud": r"\bfraud\w*\b", "scam": r"\bscam\w*\b", "charge": r"\b(?:charged|charges|indict\w*)\b", "sue": r"\b(?:sue[sd]?|suing|lawsuit)\b",
    "arrest": r"\barrest\w*\b", "seize": r"\b(?:seiz\w+|confiscat\w+|forfeit\w*)\b", "convict": r"\b(?:convict\w*|guilty|sentenced)\b",
    "embezzle": r"\bembezzl\w*\b", "bribe": r"\bbrib\w+\b", "sanction": r"\bsanction\w*\b", "manipulate": r"\bmanipulat\w+\b",
    "ponzi": r"\bponzi\b", "insider": r"\binsider trading\b", "exploit": r"\bexploit\w*\b", "bankrupt": r"\bbankrupt\w*\b",
}
QUOTED = re.compile(r"[\"“]([^\"”]{12,})[\"”]")


def story_of(caption: str) -> str:
    """The claims part of a final caption: every paragraph except the sources line, credits and signature."""
    return "\n\n".join(p for p in re.split(r"\n\s*\n", caption or "") if p.strip() and not NOT_STORY.match(p.strip()))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[’‘]", "'", s or "")).strip().lower()


def claim_problems(story: str, source_text: str) -> list[str]:
    """Claims in the story the sources don't support (owner, 2026-11-13): figures, named people, accusations / legal actions, quotations.
    Deterministic and conservative: a word the sources use passes; anything new is a problem, never silently rewritten."""
    from app.newsdesk import photos

    src = source_text or ""
    low = _norm(src)
    out = []
    extra = numbers(story) - numbers(src)
    if extra:
        out.append(f"figures no source gave: {', '.join(sorted(extra))}")
    people = [p for p in dict.fromkeys(photos.people_named(story)) if p.split()[-1].lower().strip("'s") not in low]
    if people:
        out.append(f"people no source names: {', '.join(people)}")
    accused = [k for k, rx in ACCUSE.items() if re.search(rx, story, re.I) and not re.search(rx, src, re.I)]
    if accused:
        out.append(f"accusations or legal actions no source makes: {', '.join(accused)}")
    quotes = [q for q in QUOTED.findall(story) if _norm(q) not in low]
    if quotes:
        out.append(f"quotations not in the sources: {'; '.join(q[:60] for q in quotes)}")
    return out
