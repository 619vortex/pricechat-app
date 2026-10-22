"""Hard gates for the Kiwi writer: pure functions, no model calls. Each returns (passed, reason).

A small local model follows a long rule list only part of the time and breaks rules silently, so every rule that code
can check lives here instead of in the prompt. The writer is soft; these are the hard gate (writer -> validators ->
critic -> renderer). Reasons are short: they double as kill reasons and as the fix sent back to the writer."""

import difflib
import re
from typing import Any, Iterable

BANNED_PHRASES = ("you might not know", "here's why", "everything you need to know", "let's dive in", "game-changer", "game changer",
                  "unpack", "delve", "the world of", "in today's")
HYPE_WORDS = ("massive", "huge", "insane", "crazy", "wild", "explosive", "skyrocket", "soaring", "plummeting")
REPORT_VERBS = ("hit", "hits", "sell", "sells", "sold", "announce", "announces", "announced", "report", "reports", "reported", "jump", "jumps",
                "jumped", "drop", "drops", "dropped", "close", "closes", "closed", "reach", "reaches", "reached", "rise", "rises", "rose",
                "fall", "falls", "fell", "post", "posts", "posted")
# a report verb is fine when the hook adds tension: a comparison, a turn, a second number, a stake
QUALIFIERS = re.compile(r"\b(than|vs\.?|versus|since|after|before|while|but|still|again|only|despite|from|then|yet|instead|until|"
                        r"x|times|more|less|half|double|twice|nobody|everyone|anyone|whoever|you)\b|\d(\.\d+)?x\b", re.I)
SHAPES = ("NUMBER SHOCK", "COMPARISON", "CONTRADICTION", "TIME JUMP", "STAKES")
FORMATS = ("PRICE SLAM", "THEN/NOW", "CHART DRAW", "RECEIPT", "STAKE LINE")
SCHEMA: dict[str, type | tuple[type, ...]] = {"hook": str, "shape": str, "on_screen_text": str, "visual_format": str, "first_3_seconds": str,
                                              "caption": str, "killed": list, "pick_reason": str}
MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
_NUM = re.compile(r"(?<![\w.])(?:US\$|HK\$|AED|Dh|[$€£¥])?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?(%|x\b|k\b|m\b|mn\b|b\b|bn\b|thousand|million|billion|trillion)?",
                  re.I)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9, "trillion": 1e12}

Result = tuple[bool, str]


def words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", (text or "").strip()) if re.search(r"[\w$€£%]", w)]


def numbers(text: str) -> list[float]:
    """Numbers as values: '$17,225' -> 17225, 'AED 3.4m' -> 3400000, '2.6x' -> 2.6, '+160%' -> 160."""
    out = []
    for m in _NUM.finditer(text or ""):
        whole, frac, unit = m.group(1).replace(",", ""), m.group(2), (m.group(3) or "").lower()
        out.append(float(f"{whole}.{frac}" if frac else whole) * _SCALE.get(unit, 1.0))
    return out


def amounts(text: str) -> list[float]:
    """Numbers that are amounts: with a currency, %, x or a unit, or a decimal, or a bare integer over 31 that isn't a year.
    Days of the month, grades ("PSA 10") and years are not amounts."""
    out = []
    for m in _NUM.finditer(text or ""):
        whole, frac, unit = m.group(1).replace(",", ""), m.group(2), (m.group(3) or "").lower()
        v = float(f"{whole}.{frac}" if frac else whole) * _SCALE.get(unit, 1.0)
        tagged = bool(unit) or bool(re.match(r"\s*(?:US\$|HK\$|AED|Dh|[$€£¥])", m.group(0), re.I)) or bool(frac)
        if tagged or not (v <= 31 or 1900 <= v <= 2100):
            out.append(v)
    return out


def _close(v: float, allowed: Iterable[float]) -> bool:
    """Equal, or the same number rounded (to 2 significant figures or within 1%): '$17K' for 17,225."""
    import math

    def sig(x: float) -> int:  # significant digits as written: 17225 -> 5, 17000 -> 2, 9.3 -> 2, 451 -> 3
        t = f"{abs(x):.10g}".replace(".", "").lstrip("0").rstrip("0") if not float(x).is_integer() else str(int(abs(x))).rstrip("0")
        return max(1, len(t))

    for a in allowed:
        if a == v:
            return True
        # a rounding is fine only toward fewer digits ("$17K" for 17,225), never a different, more precise number (451 for 450)
        if a and abs(v - a) / abs(a) <= 0.01 and sig(v) < sig(a):
            return True
        if a and v:
            for d in (1, 2):
                scale = 10 ** (math.floor(math.log10(abs(a))) - d + 1)
                if v in (math.floor(a / scale) * scale, math.ceil(a / scale) * scale, round(a / scale) * scale):
                    return True
    return False


# --- the gates ------------------------------------------------------------------------------------------------------------
def check_word_cap(text: str, max_words: int = 6) -> Result:
    n = len(words(text))
    return (n <= max_words, "" if n <= max_words else f"on-screen text is {n} words (max {max_words})")


def check_banned_phrases(text: str, phrases: Iterable[str] = BANNED_PHRASES) -> Result:
    low = (text or "").lower().replace("’", "'")
    hit = [p for p in phrases if p in low]
    return (not hit, "" if not hit else f"banned phrase: {hit[0]}")


def check_opener(text: str) -> Result:
    first = re.sub(r"^[^\w]+", "", (text or "").strip()).split(" ")[0] if text else ""
    bad = re.fullmatch(r"(this|it|it's|it’s|its)[,.:]?", first, re.I) is not None
    return (not bad, "" if not bad else f"opens with '{first}'")


def check_hype_words(text: str, wordlist: Iterable[str] = HYPE_WORDS) -> Result:
    hit = [w for w in wordlist if re.search(rf"\b{re.escape(w)}\w*\b", text or "", re.I)]
    return (not hit, "" if not hit else f"hype word: {hit[0]}")


def check_number_present(hook: str, source_facts: Iterable[str], derived: Iterable[str] = ()) -> Result:
    """The hook has a number, and every number in it comes from the facts or the fact card (rounding allowed)."""
    allowed = [n for t in list(source_facts) + list(derived) for n in numbers(t)]
    found = numbers(hook)
    if not found:
        return (False, "no number in the hook" if allowed else "the story has no number")
    bad = [n for n in found if not _close(n, allowed)]
    return (not bad, "" if not bad else f"number not in the facts: {bad[0]:g}")


def check_report_verb(hook: str) -> Result:
    """'Mewtwo sells for $17,225' reports; 'Mewtwo sells for 2.6x April's price' has a qualifier."""
    head = [w.lower().strip(",.:;!?") for w in words(hook)[:7]]  # the subject comes first: "Rocket's Mewtwo PSA 10 sells for..."
    verb = next((w for w in head if w in REPORT_VERBS), None)
    if not verb:
        return (True, "")
    # a second *amount* makes it a comparison; years, days of the month and grades ("PSA 10", "October 19") don't count
    qualified = bool(QUALIFIERS.search(hook)) or len(amounts(hook)) >= 2
    return (qualified, "" if qualified else f"reports ('{verb}') instead of provoking")


def check_format_repeat(visual_format: str, history: list[dict[str, Any]], last_n: int = 3) -> Result:
    """The format isn't one of the last `last_n` published formats (history newest first)."""
    recent = [h.get("visual_format") for h in history[:last_n] if h.get("visual_format")]
    ok = visual_format not in recent
    return (ok, "" if ok else f"{visual_format} used in the last {last_n}")


def check_schema(d: dict[str, Any]) -> Result:
    for k, t in SCHEMA.items():
        if k not in d:
            return (False, f"missing field: {k}")
        if not isinstance(d[k], t):
            return (False, f"{k} should be {t.__name__ if isinstance(t, type) else t}")
    if d["shape"] not in SHAPES:
        return (False, f"unknown shape: {d['shape']}")
    if d["visual_format"] not in FORMATS:
        return (False, f"unknown visual_format: {d['visual_format']}")
    if not all(isinstance(k, dict) and {"shape", "hook", "reason"} <= set(k) for k in d["killed"]):
        return (False, "killed entries need shape, hook, reason")
    return (True, "")


def _norm(t: str, mask_numbers: bool) -> str:
    t = re.sub(r"[^\w$%\s]", " ", (t or "").lower())
    if mask_numbers:
        t = re.sub(r"\$?\d[\d,.]*\s?(%|x|k|m|bn|billion|million)?", "#", t)
    return re.sub(r"\s+", " ", t).strip()


def check_duplicate(hook: str, history: list[dict[str, Any]], last_n: int = 50) -> Result:
    """Fuzzy match against recent hooks: the same words, or the same formula with new numbers ('A piece of cardboard...')."""
    for h in history[:last_n]:
        old = h.get("hook") or ""
        if not old:
            continue
        raw = difflib.SequenceMatcher(None, _norm(hook, False), _norm(old, False)).ratio()
        shape = difflib.SequenceMatcher(None, _norm(hook, True), _norm(old, True)).ratio()
        if raw >= 0.85 or shape >= 0.9:
            return (False, f"too close to “{old[:50]}”")
    return (True, "")


def check_shape_signal(shape: str, hook: str) -> Result:
    """The part of each shape's test that code can see (the rest is the critic's job)."""
    nums = numbers(hook)
    if shape == "NUMBER SHOCK":  # "$17,225 ..." or "AED 917 billion ..." / "EUR 1,225,000 ..."
        ok = bool(re.match(r"^\W*(?:(?:US|HK)?\$|€|£|AED|EUR|USD|GBP|Dh)?\s?\d", hook.strip(), re.I))
        return (ok, "" if ok else "number shock must start with the number")
    if shape == "COMPARISON":
        return (len(nums) >= 2 or bool(QUALIFIERS.search(hook) and nums), "" if len(nums) >= 2 or (QUALIFIERS.search(hook) and nums) else "comparison needs two numbers")
    if shape == "TIME JUMP":
        ok = bool(re.search(rf"\b{MONTH}\b|\b(19|20)\d\d\b|\b(ago|since|back in|last year)\b", hook, re.I))
        return (ok, "" if ok else "time jump needs a past date")
    return (True, "")


PERIOD = re.compile(rf"\b{MONTH}\b|\b(19|20)\d\d\b|\b(Q[1-4]|H[12]|FY\s?\d{{2,4}})\b", re.I)


def _period(text: str) -> set[str]:
    """The period a sentence talks about: its months, years and quarters ('October 6, 2026' -> {'oct', '2026'})."""
    out = set()
    for m in PERIOD.finditer(text or ""):
        t = m.group(0).lower()
        out.add(t[:3] if t[:3].isalpha() and len(t) > 3 else t.replace(" ", ""))
    return out


def check_hook_substance(hook: str, min_words: int = 4) -> Result:
    """A number on its own ('$96.2 billion.') is a label, not a hook: it needs a subject or a turn."""
    n = len(words(hook))
    return (n >= min_words, "" if n >= min_words else f"hook is {n} words: a bare number, add who or what")


def check_period_mix(hook: str, shape: str, facts: list[str], derived: list[str] = ()) -> Result:
    """Two numbers from different periods set side by side ('Revenue up 106%. Shares down 9.26%': a July quarter vs October)
    is a false contradiction. A comparison may span periods only when the hook names both."""
    found = amounts(hook)
    if len(found) < 2:
        return (True, "")
    sources = [f for f in list(facts) + list(derived) if not f.startswith("HEADLINE:")]  # a headline frames; facts carry the dates
    homes = [[f for f in sources if _close(a, numbers(f))] for a in found]
    if any(not h for h in homes):
        return (True, "")  # the number check reports unknown numbers
    if any(all(_close(a, numbers(f)) for a in found) for f in sources):
        return (True, "")  # one fact holds them all: same moment
    # each number's home is the fact that states it exactly ("9.3%" is the derived Oct 6 line, not a fact that rounds to it)
    homes = [[f for f in h if any(abs(a - n) < 1e-9 for n in numbers(f))] or h for a, h in zip(found, homes)]
    periods_by_home = [[_period(f) for f in h if _period(f)] for h in homes]
    if any(not p for p in periods_by_home):
        return (True, "")  # an undated fact: can't tell

    def apart(a: set[str], b: set[str]) -> bool:  # months/quarters when both have them (2026 alone is too coarse), else years
        fine_a, fine_b = {t for t in a if not t.isdigit()}, {t for t in b if not t.isdigit()}
        if fine_a and fine_b:
            return not (fine_a & fine_b)
        return not ({t for t in a if t.isdigit()} & {t for t in b if t.isdigit()})

    # two numbers belong together if any of their homes share a period
    mixed = any(all(apart(pa, pb) for pa in ha for pb in hb) for i, ha in enumerate(periods_by_home) for hb in periods_by_home[i + 1:])
    if not mixed:
        return (True, "")
    if shape in ("COMPARISON", "TIME JUMP") and len(_period(hook)) >= 2:
        return (True, "")  # then/now, with both periods said out loud
    return (False, "mixes two periods as one moment")


MONEY_QUESTION = re.compile(r"\b(buy|buying|bought|sell|selling|sold|invest\w*|hold|holding|fold|bag|bagholder|top|bottom|dip|worth it|"
                            r"go(es)? up|go(es)? down|price will|profit|returns?)\b", re.I)


def check_caption_question(caption: str) -> Result:
    """The comment question asks about the viewer's taste or experience, never about buying, selling or where a price goes."""
    for q in re.findall(r"[^.!?]*\?", caption or ""):
        m = MONEY_QUESTION.search(q)
        if m:
            return (False, f"the question is about money ('{m.group(0)}'): ask about taste or experience")
    return (True, "")


LOSS_WORDS = re.compile(r"\b(bag|bagholders?|peak|the top|too late|last ones? in|crash\w*|bubble|underwater|under water|losing|lost money|"
                        r"wrong side|in the red|wiped out)\b", re.I)
DECLINE = re.compile(r"(^|\s)-\d|\b(down|below|fell|falls|dropped|drops|declined?|lower|lost|loss|off its|from the [\w ,]+ high)\b", re.I)


def check_unsupported_loss(text: str, facts: Iterable[str], derived: Iterable[str] = ()) -> Result:
    """Loss language ('held the bag', 'wrong side of the peak') only when the research shows a decline."""
    m = LOSS_WORDS.search(text or "")
    if not m:
        return (True, "")
    if any(DECLINE.search(f) for f in list(facts) + list(derived) if not f.startswith("HEADLINE:")):
        return (True, "")
    return (False, f"claims a loss ('{m.group(0)}') the numbers don't show")


def gate(cand: dict[str, Any], facts: list[str], derived: list[str], history: list[dict[str, Any]]) -> list[tuple[str, bool, str]]:
    """Every hard check for one writer candidate: [(check, passed, reason)]."""
    hook, screen, cap = str(cand.get("hook") or ""), str(cand.get("on_screen_text") or ""), str(cand.get("caption") or "")
    all_text = " ".join([hook, screen, cap])
    out = [("word_cap", *check_word_cap(screen)),
           ("hook_length", len(words(hook)) <= 12, "" if len(words(hook)) <= 12 else f"hook is {len(words(hook))} words (max 12)"),
           ("banned_phrases", *check_banned_phrases(all_text)),
           ("opener", *check_opener(hook)),
           ("opener_screen", *check_opener(screen)),
           ("hype_words", *check_hype_words(all_text)),
           ("number_present", *check_number_present(hook, facts, derived)),
           ("caption_numbers", *_all_numbers(cap + " " + screen, facts, derived)),
           ("report_verb", *check_report_verb(hook)),
           ("duplicate", *check_duplicate(hook, history)),
           ("shape_signal", *check_shape_signal(str(cand.get("shape") or ""), hook)),
           ("substance", *check_hook_substance(hook)),
           ("period_mix", *check_period_mix(hook, str(cand.get("shape") or ""), facts, derived)),
           ("caption_question", *check_caption_question(cap)),
           ("unsupported_loss", *check_unsupported_loss(all_text, facts, derived))]
    return out


def _all_numbers(text: str, facts: list[str], derived: list[str]) -> Result:
    allowed = [n for t in facts + derived for n in numbers(t)]
    bad = [n for n in numbers(text) if not _close(n, allowed) and not (n <= 10 and float(n).is_integer())]
    return (not bad, "" if not bad else f"number not in the facts: {bad[0]:g}")
