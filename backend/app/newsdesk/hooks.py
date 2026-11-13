"""Five headline hooks per story, ranked for engagement AND factual accuracy, and the strongest true one goes (owner, 2026-11-13: "short,
provocative, specific; no corporate press-release headlines").

Where the five come from
  model   the writer (the GPU PC's local gpt-oss, or the Anthropic model when a key is set) is asked for five alternative headlines in the
          same JSON as its draft (network.MODEL_PROMPT "hooks"). Its draft headline is a candidate too.
  desk    without a model, five hooks are cut from the reporting itself: the outlet's own headline tightened, the most striking figure
          front-loaded, the actor put first with the attribution kept, the contradiction or the conflict the reporting states. Nothing
          is added: every word is the outlets'.

Accuracy (`fact_check`), checked against the source text of every outlet and our own measured prices. A hook is REJECTED when it
  - has a figure, or a name, no source gave
  - adds a loaded claim the sources don't make (a "record", a "crash", "fraud", "collapse"...)
  - states a cause the sources don't state ("sparks", "triggers", "because", or an "as"/"after" clause no source sentence supports)
  - drops "alleged"/"accused" the reporting uses, or puts an accusation next to an identifiable person without an official source or two
    outlets (scorecard.safety)
  - quotes words that are not in the sources, uses hype, a label, advice, all capitals, or runs past HEAD_MAX
Survivors get an accuracy score too (how much of the hook is the sources' own wording) and only hooks at ACCURACY_MIN or above can win.

Engagement (`engagement`): a specific figure, the figure in the first words, a named actor, tension (conflict or contrast), a strong verb,
brevity; minus press-release words ("announces", "launches", "partners with", "unveils"), vagueness and question headlines. What our audience
engaged with per hook strategy adds up to +-6 points (engagement.hook_points), once enough posts are measured.

The rank: 0.55 x engagement + 0.45 x accuracy, rejected hooks last. The result is stored on the event (facts["hooks"]) for Admin.

The debate question (`debate_question`): only when the story invites genuine disagreement (scorecard discussion >= QUESTION_MIN and it is
not about deaths or victims), framed around the decision or the conflict in the story, never asking whether a named person is guilty,
never with a name or figure the sources don't have, never with outrage words. A writer's question is used when it passes those rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.newsdesk import checks

HEAD_MAX = 100
ACCURACY_MIN = 70
QUESTION_MIN = 10  # scorecard discussion points a story needs before it gets a debate question
STRATEGIES = ("model", "writer", "outlet", "tight", "figure_first", "actor_first", "contradiction", "conflict", "detail")
_rx = lambda p: re.compile(p, re.I)  # noqa: E731

FIGURE = _rx(r"[$€£]\s?\d|\d+(?:\.\d+)?\s?%|\b\d[\d,.]*\s?(?:billion|million|trillion|bn|[mbk])\b|\b\d{2,}\b")
CORPORATE = _rx(r"\b(announces?|announced|launch(?:es|ed)?|unveil\w*|introduc\w+|partners? with|partnership|teams? up|is pleased|proud to|"
                r"strategic|solutions?|leverag\w*|innovative|ecosystem|synerg\w*|initiative|enhanc\w+|empower\w*|update on|statement on|"
                r"to explore|collaborat\w+|commit\w* to|reaffirm\w*|milestone|journey|cutting-edge|best-in-class|world-class)\b")
VAGUE = _rx(r"\b(here'?s (?:what|why|how)|what (?:to know|we know)|everything you need|explained|explainer|things to know|could|may|might|"
            r"leaves? questions|raises? questions|questions remain|faces? an? (?:uphill|tough))\b")
STRONG_VERB = _rx(r"\b(dumps?|dumped|slash\w*|bans?|banned|fires?|fired|sues|sued|quits?|buys?|bought|sells?|sold|freez\w*|froze|doubles?|doubled|"
                  r"halves|halved|jumps?|jumped|plunges?|plunged|soars?|soared|crash\w*|hits?|wipes?|wiped|seiz\w*|halts?|halted|blocks?|"
                  r"blocked|lifts?|lifted|charges?|charged|jailed|ousted|resigns?|resigned|steals?|stole|stolen|drains?|drained|tops?|topped|"
                  r"overtakes?|beats?|misses?|missed|surges?|surged|tumbles?|tumbled|mints?|launders?|wash\w*|admits?|admitted|defies|defied)\b")
TENSION = _rx(r"\b(but|yet|despite|even as|while|instead|versus|vs\.?|against|accus\w+|denie[sd]|sues|sued|probe\w*|investigat\w+|clash\w*|"
              r"defies|defied|backlash|row|feud|fight\w*|ban\w*|blocks?|blocked|fires?|fired|ousted|scandal)\b")
HYPE = _rx(r"\b(exclusive|scoop|you won'?t believe|shocking|insane|mind-?blowing|jaw-?dropping|bloodbath|to the moon|mooning|rekt|game[- ]changer|"
           r"must[- ]see|this is huge|massive news|destroy\w*|obliterat\w+|epic|unbelievable|breaking|just in)\b")
ADVICE = _rx(r"\b(buy now|sell now|you should|guaranteed|risk[- ]free|can'?t lose|get rich|100x|financial advice)\b")
LABEL = _rx(r"^(breaking|just in|alert|update|exclusive|watch|live)\s*[:\-–—|]")
# loaded words a hook may only use when the reporting does: the claim they carry must be the sources' claim
LOADED = ("record", "biggest", "largest", "worst", "first", "all-time", "historic", "unprecedented", "crash", "collapse", "plunge", "soar",
          "surge", "meltdown", "panic", "chaos", "fraud", "scam", "scandal", "ponzi", "criminal", "illegal", "lied", "lying", "stole", "theft",
          "arrest", "charged", "guilty", "bankrupt", "insolvent", "war", "crisis", "default", "bailout", "ban", "banned", "fired", "ousted",
          "resign", "hack", "exploit", "drained", "rug", "manipulat", "insider", "spy", "espionage", "affair", "probe", "investigat", "sued",
          "lawsuit", "sanction", "seize", "frozen", "halt", "delist", "wiped", "liquidat", "doubled", "tripled", "halved", "slashed", "dump",
          "defy", "defie", "slam", "blast", "rattl", "crush", "tank", "plummet", "skyrocket", "explod", "rocket", "turmoil", "shock")
SUFFIX = r"(?:s|es|ed|d|ing|ned|ning|ion|ions|ation|ive|er|ers|ly|ure|ures|ped|ping)?"
CAUSAL = _rx(r"\b(sparks?|sparked|triggers?|triggered|causes?|caused|drives? (?:\w+ )?(?:up|down|higher|lower)|drove (?:\w+ )?(?:up|down|higher|lower)|fuels?|fueled|fuelled|sends? (?:\w+ ){0,3}(?:higher|lower|soaring|tumbling|plunging|up|down)|sent (?:\w+ ){0,3}(?:higher|lower|soaring|tumbling|plunging)|forces?|forced|leads? to|"
             r"led to|because(?: of)?|due to|blamed on|thanks to|in response to|prompts?|prompted)\b")
LINK = _rx(r"\b(as|after|on|amid|following)\b")
HEDGE = _rx(r"\b(alleg\w*|accus\w*|suspect\w*|reported(?:ly)?|claims?|according to|says?|said|denie[sd]|deny)\b")
ACCUSED_WORD = _rx(r"\b(alleged(?:ly)?|accused(?: of)?|suspected|purported)\s+((?:[\w'’-]+\s+){0,2}[\w'’-]+)")
CAPWORD = re.compile(r"(?<!^)\b([A-Z][a-zA-Z0-9&'’.-]{2,})")
SOMBRE = _rx(r"\b(killed|kills?|dead|deaths?|died|dies|casualt\w+|injured|wounded|victims?|hostages?|massacre|genocide|famine|earthquake|"
             r"hospital|civilians?|children|suicide)\b")
OUTRAGE = _rx(r"\b(disgrace\w*|outrage\w*|shame\w*|evil|scum|idiot\w*|insane|stupid|corrupt\w*|criminal|traitor\w*|clown\w*|pathetic|ridiculous)\b")
GUILT = _rx(r"\b(guilty|a fraud|lying|lied|a scam|a criminal|crook|deserve|jail|prison|locked up)\b")
ATTRIBUTION_TAIL = re.compile(r"(?:,|\s[-–—])\s*((?:[A-Z][\w.’'&-]*\s?){1,6})\s+(says?|said|reports?|claims?|confirms?)\.?$")


@dataclass
class Hook:
    text: str
    strategy: str
    engagement: int = 0
    accuracy: int = 0
    parts: dict[str, float] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    rank: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems and self.accuracy >= ACCURACY_MIN

    @property
    def total(self) -> float:
        return round(0.55 * self.engagement + 0.45 * self.accuracy, 1) if self.ok else 0.0

    def out(self) -> dict[str, Any]:
        return {"text": self.text, "strategy": self.strategy, "rank": self.rank, "total": self.total, "engagement": self.engagement,
                "accuracy": self.accuracy, "parts": self.parts, "problems": self.problems, "ok": self.ok}


# --- the desk's own five (no model): cut from the reporting -----------------------------------------------------------------------


def _clean(text: str) -> str:
    from app.newsdesk import network

    t = re.sub(r"\s+", " ", text or "").strip().rstrip(".")
    t = LABEL.sub("", t).strip()
    t = re.sub(r"\s+[-–—|]\s+(?:Reuters|Bloomberg|CNBC|AP|AFP|BBC|FT|WSJ|CoinDesk|The Block|Decrypt|Cointelegraph)$", "", t)
    t = network.headline_case(t) if t.isupper() else t
    return network.shorten(t, HEAD_MAX)


def _tight(text: str) -> str:
    """The headline with its filler cut: the leading article, '... says report', 'here's why' tails."""
    t = _clean(text)
    t = re.sub(r"^(?:The|A|An)\s+", "", t)
    t = re.sub(r"\s*[:,]\s*(?:report|reports|sources say|source says)$", "", t, flags=re.I)
    t = re.sub(r"\s+(?:in|on) (?:a |the )?(?:statement|blog post|filing|press release)$", "", t, flags=re.I)
    t = re.sub(r"\bpercent\b", "%", t)
    t = re.sub(r"(\d)\s+%", r"\1%", t)
    return t[:1].upper() + t[1:] if t else t


def _actor_first(title: str) -> str:
    """'Israeli strike kills six, Gaza civil defence agency says' -> 'Gaza civil defence agency: Israeli strike kills six'. The attribution is
    kept, moved to the front; nothing else changes."""
    t = _clean(title)
    m = ATTRIBUTION_TAIL.search(t)
    if not m:
        return ""
    who, claim = m.group(1).strip(), t[:m.start()].strip(" ,")
    return f"{who}: {claim[:1].upper() + claim[1:]}" if claim and who else ""


def _sentence_hook(s: str) -> str:
    from app.newsdesk import network

    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"^(?:On \w+day|Earlier|Meanwhile|However|In addition|Separately),?\s+", "", s)
    s = re.sub(r",\s*(?:according to|the company said|it said|he said|she said|they said)[^,.]*", "", s)
    s = re.sub(r"\s*\((?:[A-Z][\w.]*\s?){1,3}\)\.?\s*$", "", s)  # "(Bloomberg)"
    s = re.sub(r"\.{2,}", ".", s).rstrip(".")
    if len(s) > HEAD_MAX:
        cut = re.split(r",\s|;\s|\s[-–—]\s", s)[0]
        s = cut if 30 <= len(cut) <= HEAD_MAX else network.shorten(s, HEAD_MAX)
    return s[:1].upper() + s[1:] if s else s


def desk_hooks(ev: Any, rows: list[Any] | None = None) -> list[Hook]:
    """Up to five hooks from the reporting alone (outlet headline, tightened, figure first, actor first, contradiction/conflict/detail)."""
    from app.newsdesk import viral

    title = getattr(ev, "title", "") or getattr(ev, "headline", "") or ""
    out: list[Hook] = []
    seen: set[str] = set()

    def add(text: str, strategy: str) -> None:
        text = (text or "").strip()
        key = checks.fingerprint(text)
        if len(text) < 20 or not key or any(checks.similarity(key, k) >= 0.8 for k in seen):
            return
        seen.add(key)
        out.append(Hook(text, strategy))

    add(_clean(title), "outlet")
    add(_tight(title), "tight")
    add(_actor_first(title), "actor_first")
    sents = viral.sentences(ev, rows)
    figures = sorted(((s, viral._magnitude(s)) for s in sents if FIGURE.search(s)), key=lambda x: -x[1])
    if figures and figures[0][1] > 0:
        add(_sentence_hook(figures[0][0]), "figure_first")
    for s in sents:
        if viral.CONTRAST.search(s) or viral.FAILED_CALL.search(s):
            add(_sentence_hook(s), "contradiction")
            break
    for s in sents:
        if viral.CONFLICT.search(s) and CAPWORD.search(s):
            add(_sentence_hook(s), "conflict")
            break
    for s in sents:
        if len(out) >= 5:
            break
        if CAPWORD.search(s) and (FIGURE.search(s) or STRONG_VERB.search(s)):
            add(_sentence_hook(s), "detail")
    return out[:5]


# --- accuracy ----------------------------------------------------------------------------------------------------------------------


def _bare(w: str) -> str:
    return re.sub(r"[’']s$", "", w).rstrip(".’'").strip(":,;")


def _supported_clause(hook: str, sentences: list[str]) -> bool:
    """An 'X as/after Y' hook: some single source sentence must carry most of both sides."""
    m = LINK.search(hook)
    if not m:
        return True
    a, b = checks.tokens(hook[:m.start()]), checks.tokens(hook[m.end():])
    if not a or not b:
        return True
    for s in sentences:
        t = checks.tokens(s)
        if len(a & t) / len(a) >= 0.5 and len(b & t) / len(b) >= 0.5:
            return True
    return False


def fact_check(text: str, source_text: str, *, evidence: str = "", outlets: int = 1, sentences: list[str] | None = None,
               extractive: bool = False, who: set[str] | None = None) -> tuple[list[str], int]:
    """(problems, accuracy 0-100). Any problem rejects the hook. extractive: the hook is an outlet's own sentence (what it asserts, it
    asserts: the alleged-or-accused rule binds writers). who: the names the story is about; a hook must name at least one."""
    from app.newsdesk import network, scorecard

    problems: list[str] = []
    src = source_text or ""
    low = src.lower()
    h = (text or "").strip()
    if not h:
        return ["empty"], 0
    if len(h) > HEAD_MAX:
        problems.append(f"too long: {len(h)} characters")
    extra = checks.numbers(h) - checks.numbers(src)
    if extra:
        problems.append(f"a figure no source gave: {', '.join(sorted(extra))}")
    unknown = sorted({_bare(m.group(1)) for m in CAPWORD.finditer(h)} - network.NAME_OK - {""})
    unknown = [w for w in unknown if w.lower() not in low and not w.isupper()]
    if unknown:
        problems.append(f"a name no source used: {', '.join(unknown[:4])}")
    hl = h.lower()
    added = [w for w in LOADED if re.search(rf"\b{w}{SUFFIX}\b", hl) and not re.search(rf"\b{w}", low)]
    if added:
        problems.append(f"adds a claim the sources don't make: '{added[0]}'")
    causal = CAUSAL.search(h)
    if causal and not re.search(rf"\b{re.escape(causal.group(0).lower())}", low):
        problems.append(f"states a cause the sources don't: '{causal.group(0).lower()}'")
    sents = sentences if sentences is not None else re.split(r"(?<=[.!?])\s+", src)
    if not causal and LINK.search(h) and not _supported_clause(h, sents + [src[:300]]):
        problems.append(f"links two facts with '{LINK.search(h).group(0)}' that no source sentence links")
    hedged = {w.lower() for m in ACCUSED_WORD.finditer(src) for w in m.group(2).split() if len(w) > 3}
    if who and not any(re.search(rf"\b{re.escape(w)}", h, re.I) for w in who):
        problems.append(f"doesn't say who the story is about ({', '.join(sorted(who)[:3])})")
    if hedged and not extractive and not HEDGE.search(h) and any(re.search(rf"\b{re.escape(w)}\b", h, re.I) for w in hedged):
        problems.append("the reporting says alleged or accused; the hook states it as fact")
    problems += scorecard.safety(h, evidence, outlets)
    from app.newsdesk import claims

    problems += claims.ticker_problems(h, src) + claims.people_problems(h, src) + claims.legal_problems(h, src)
    problems = list(dict.fromkeys(problems))
    for q in re.findall(r"[\"“]([^\"”]{6,})[\"”]|(?<!\w)['‘]([^'’]{6,})['’](?!\w)", h):
        q = q[0] or q[1]
        if q.lower() not in low:
            problems.append(f"a quote not in the sources: '{q[:40]}'")
    if HYPE.search(h):
        problems.append(f"hype or a label: '{HYPE.search(h).group(0).lower()}'")
    if ADVICE.search(h):
        problems.append("advice or a promise")
    if LABEL.search(h):
        problems.append("a label word")
    if h.isupper():
        problems.append("all capitals")
    if checks.URLISH.search(h):
        problems.append("a web address")
    # closeness: how much of the hook is the sources' own wording
    words = checks.tokens(h)
    src_words = checks.tokens(src)
    share = len(words & src_words) / len(words) if words else 0.0
    accuracy = int(round(100 * min(1.0, share / 0.85))) - 25 * len(problems)
    return problems, max(0, min(100, accuracy))


# --- engagement --------------------------------------------------------------------------------------------------------------------


def engagement(text: str, strategy: str = "", learned: dict[str, float] | None = None) -> tuple[int, dict[str, float]]:
    h = text or ""
    parts: dict[str, float] = {}
    if FIGURE.search(h):
        parts["figure"] = 15.0
        if FIGURE.search(" ".join(h.split()[:5])):
            parts["figure_first"] = 8.0
    if CAPWORD.search(h) or (h.split() and h.split()[0][:1].isupper() and h.split()[0].lower() not in ("the", "a", "an", "this", "why", "how", "what")):
        parts["named"] = 12.0
    if TENSION.search(h):
        parts["tension"] = 10.0
    if STRONG_VERB.search(h):
        parts["verb"] = 8.0
    n = len(h)
    parts["brevity"] = 12.0 if n <= 60 else 8.0 if n <= 80 else 0.0 if n <= HEAD_MAX else -10.0
    if CORPORATE.search(h):
        parts["press_release"] = -15.0
    if VAGUE.search(h):
        parts["vague"] = -10.0
    if h.rstrip().endswith("?"):
        parts["question_headline"] = -6.0
    if learned and learned.get(strategy):
        parts["audience"] = round(max(-6.0, min(6.0, float(learned[strategy]))), 1)
    return int(max(0, min(100, round(30 + sum(parts.values()))))), parts


# --- ranking -----------------------------------------------------------------------------------------------------------------------


MERGE_MIN = 0.6  # the share of a hook's words one single report must carry: no stitching two reports into one claim


def merged(text: str, reports: list[str]) -> str:
    """Why the hook stitches facts from different reports together, or "". Each report (an outlet's headline and summary) is read on
    its own: 2026-11-12, "Israeli strike kills Palestinian footballer, hospital staff" joined a BBC hospital report to an unrelated Al
    Jazeera story that had been grouped with it."""
    words = checks.tokens(text)
    if not words or not reports:
        return ""
    best = max(len(words & checks.tokens(r)) / len(words) for r in reports)
    return "" if best >= MERGE_MIN else f"no single report carries it ({best:.0%} at most): it merges facts from different reports"


def rank(hooks: list[Hook], source_text: str, *, evidence: str = "", outlets: int = 1, sentences: list[str] | None = None,
         learned: dict[str, float] | None = None, who: set[str] | None = None, reports: list[str] | None = None) -> list[Hook]:
    for h in hooks:
        h.problems, h.accuracy = fact_check(h.text, source_text, evidence=evidence, outlets=outlets, sentences=sentences,
                                            extractive=h.strategy not in ("model", "writer"), who=who)
        if h.strategy in ("model", "writer") and reports:
            why = merged(h.text, reports)
            if why:
                h.problems.append(why)
        if not h.problems and h.accuracy < ACCURACY_MIN:
            h.problems.append(f"too little of it is the sources' own wording (accuracy {h.accuracy}, needs {ACCURACY_MIN})")
        h.engagement, h.parts = engagement(h.text, h.strategy, learned)
    ordered = sorted(hooks, key=lambda h: (not h.ok, -h.total, -h.accuracy, STRATEGIES.index(h.strategy) if h.strategy in STRATEGIES else 99))
    for i, h in enumerate(ordered, 1):
        h.rank = i
    return ordered


def plan(ev: Any, rows: list[Any] | None = None, model_hooks: list[str] | None = None, writer_headline: str = "",
         learned: dict[str, float] | None = None) -> dict[str, Any]:
    """The five hooks (the model's when it gave them, else the desk's), ranked; the winner and why. Stored as facts["hooks"]."""
    from app.newsdesk import network, viral

    cands: list[Hook] = []
    if model_hooks:
        cands += [Hook(str(t).strip(), "model") for t in model_hooks[:5] if str(t).strip()]
    if len(cands) < 5:
        have = {checks.fingerprint(h.text) for h in cands}
        cands += [h for h in desk_hooks(ev, rows) if checks.fingerprint(h.text) not in have][: 5 - len(cands)]
    if writer_headline and all(checks.similarity(checks.fingerprint(writer_headline), checks.fingerprint(h.text)) < 0.8 for h in cands):
        cands.append(Hook(writer_headline, "writer"))
    src = network.source_text_of(ev, rows)
    outlets = len({str(s.get("name") or "").lower() for s in (getattr(ev, "sources", None) or [])} - {""}) or len((ev.facts or {}).get("outlets") or []) or 1
    reports = [f"{getattr(r, 'title', '')}. {getattr(r, 'summary', '') or ''}" for r in rows or []]
    # the event's own summary is already a merge of its reports: only the lead headline (and our measured tag) counts as a report of its own
    reports.append(f"{getattr(ev, 'title', '')}. {(ev.facts or {}).get('price_tag') or ''}" if rows else f"{getattr(ev, 'title', '')}. {getattr(ev, 'summary', '') or ''}")
    ranked = rank(cands, src, evidence=getattr(ev, "evidence", "") or "", outlets=outlets, sentences=viral.sentences(ev, rows), learned=learned,
                  who=subjects(getattr(ev, "title", "") or ""), reports=reports)
    best = next((h for h in ranked if h.ok), None)
    return {"source": "model" if model_hooks else "desk", "hooks": [h.out() for h in ranked], "chosen": best.text if best else "",
            "chosen_strategy": best.strategy if best else "", "why": (f"rank 1 of {len(ranked)}: engagement {best.engagement}, accuracy {best.accuracy}"
                                                                     if best else "no hook passed the fact check: the outlet's headline stays")}


# --- the debate question -----------------------------------------------------------------------------------------------------------

QUESTIONS = {
    "government": ("Right call by {who}, or a mistake?", "Who wins from {who}'s decision?"),
    "scandal": ("Does this change how you see {who}?", "Should this change how investors treat {who}?"),
    "billionaire": ("Smart move by {who}, or a costly one?", "Is {who} right on this one?"),
    "exchange": ("Would this change whether you keep funds on {who}?", "Is self-custody the real lesson here?"),
    "crypto_major": ("Bullish or bearish for crypto?", "Priced in already, or not yet?"),
    "market_move": ("Overreaction, or the start of something bigger?", "Who is on the wrong side of this move?"),
    "macro": ("Priced in, or are markets underestimating this?", "Who wins and who loses from this?"),
    "geopolitics": ("Are markets underpricing this?", "Who gains if this escalates?"),
    "unusual": ("Genius or madness?", "Would you have seen this coming?"),
}


def question_problems(q: str, source_text: str) -> list[str]:
    from app.newsdesk import network

    out = []
    q = (q or "").strip()
    if not q.endswith("?") or len(q) > 90:
        out.append("not a short question")
    if re.match(r"^(?:why|how|what|when|where)\b", q, re.I) and not re.match(r"^(?:what moves next|who wins)", q, re.I):
        out.append("a factual question, not an opinion people can disagree on")
    if OUTRAGE.search(q):
        out.append(f"outrage word: '{OUTRAGE.search(q).group(0).lower()}'")
    if GUILT.search(q):
        out.append("asks about someone's guilt")
    extra = checks.numbers(q) - checks.numbers(source_text)
    if extra:
        out.append("a figure no source gave")
    names = {_bare(m.group(1)) for m in CAPWORD.finditer(q)} - network.NAME_OK
    if any(n.lower() not in (source_text or "").lower() for n in names if n):
        out.append("a name no source used")
    if HYPE.search(q):
        out.append("hype")
    from app.newsdesk import claims

    out += claims.ticker_problems(q, source_text) + claims.people_problems(q, source_text) + claims.legal_problems(q, source_text)
    return out


DEMONYM = re.compile(r"^(?:north|south|east|west|new|[A-Za-z]+(?:ian|ean|ese|ish|ic|i))$", re.I)  # Russian, Chinese, Israeli, Saudi: not an actor


def subjects(title: str) -> set[str]:
    """The names a story is about (as editorial.names_in, without demonyms and directions)."""
    from app.newsdesk import editorial

    return {n for n in editorial.names_in(title or "") if not DEMONYM.match(n) and len(n) > 2}


def _who(ev: Any, beat: str = "") -> str:
    """The actor a question can name: the decision maker, the exchange, the billionaire, the company; never a demonym."""
    from app.newsdesk import scorecard

    title = getattr(ev, "title", "") or ""
    order = {"government": (r"\b(trump|biden|white house|congress|senate|the sec|sec|cftc|the fed|fed|treasury|ecb|eu|european commission|"
                            r"parliament|government|supreme court|opec)\b",),
             "exchange": (scorecard.CRYPTO_EXCHANGE.pattern, scorecard.EXCHANGE.pattern),
             "billionaire": (scorecard.BILLIONAIRE.pattern,),
             "scandal": (scorecard.MEGA.pattern, scorecard.CRYPTO_EXCHANGE.pattern, scorecard.BILLIONAIRE.pattern)}.get(beat, ())
    for pat in order:
        m = re.search(pat, title, re.I)
        if m and m.group(0).lower() not in ("exchange", "government"):
            w = m.group(0)
            return w if not w.islower() else w.upper() if len(w) <= 4 else w.title()
    for m in CAPWORD.finditer(" " + title):
        w = _bare(m.group(1))
        if w.lower() in subjects(title):
            return w
    return ""


def debate_question(ev: Any, card: dict[str, Any] | None, writer_question: str = "", source_text: str = "") -> tuple[str, str]:
    """(question, why). "" when the story doesn't invite genuine disagreement or would turn harm into a debate."""
    card = card or {}
    title = getattr(ev, "title", "") or ""
    if SOMBRE.search(f"{title} {getattr(ev, 'summary', '') or ''}"):
        return "", "no question: the story is about harm to people"
    disc = float((card.get("dims") or {}).get("discussion") or 0)
    if writer_question and disc >= QUESTION_MIN / 2 and not question_problems(writer_question, source_text):
        return writer_question.strip(), "the writer's question passed the rules"  # the writer judged it worth asking; half the bar
    if disc < QUESTION_MIN:
        return "", f"no question: discussion {disc:g} under {QUESTION_MIN}"
    beat = str(card.get("beat") or "")
    bank = QUESTIONS.get(beat) or ()
    who = _who(ev, beat)
    for q in bank:
        if "{who}" in q and not who:
            continue
        text = q.format(who=who)
        if not question_problems(text, source_text or title):
            return text, f"{beat} question about {who or 'the story'} (discussion {disc:g})"
    return "", "no question fits this story"


__all__ = ["ACCURACY_MIN", "Hook", "debate_question", "desk_hooks", "engagement", "fact_check", "plan", "question_problems", "rank"]
