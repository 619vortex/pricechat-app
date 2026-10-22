"""Script checks, run by the backend on every script a worker writes (the worker retries with this feedback).

- No invented numbers: every number spoken or written must come from the research brief (prices, %, sales, years).
- No unsupported financial claims (FORBIDDEN_CLAIMS) and no banned topics (strategy + the brand's own "avoid" list).
- Sources: the brief's sources are cited (when `require_sources`).
- Length: the voiceover fits the configured duration.
"""

import re
from typing import Any

from app.brand_manager.defaults import FORBIDDEN_CLAIMS

WORDS_PER_SECOND = 2.6
# a currency sign or code may be glued to the number ("Dh100 billion", "AED917bn", "US$1.7 million")
_NUM = re.compile(r"(?<![\w.])(?:US\$|HK\$|AED|Dh|[$€£¥])?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?(%|k\b|m\b|mn\b|b\b|bn\b|thousand|million|billion|trillion)?", re.I)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9, "trillion": 1e12}


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
                                     "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_BIG = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000, "trillion": 1_000_000_000_000}
_WORDNUM = re.compile(r"\b(?:(?:" + "|".join(list(_UNITS) + list(_TENS) + list(_BIG) + ["point"]) + r")\b(?:[\s-]+(?:and[\s-]+)?)?)+", re.I)


def _spelled(m: re.Match[str]) -> str:
    """'fourteen billion' -> '14000000000', 'one hundred twenty' -> '120'; written-out numbers are checked like digits."""
    words = [w for w in re.findall(r"[a-z]+", m.group(0).lower()) if w != "and"]
    if not any(w in _UNITS or w in _TENS for w in words):
        return m.group(0)  # a lone "million" belongs to digits before it ("1.5 million"): the digit pattern reads it
    trail = " " if m.group(0)[-1:].isspace() else ""
    if "point" in words:  # "three hundred eighty six point three million" -> 386.3 million
        k = words.index("point")
        whole = _value(words[:k]) if words[:k] else 0
        rest = words[k + 1:]
        digits = ""
        while rest and rest[0] in _UNITS and _UNITS[rest[0]] < 10:
            digits += str(_UNITS[rest.pop(0)])
        scale = _BIG.get(rest[0], 1) if rest and rest[0] in _BIG and rest[0] != "hundred" else 1
        value = (whole + float("0." + (digits or "0"))) * scale
        return f"{value:.6f}".rstrip("0").rstrip(".") + trail
    return f"{_value(words)}{trail}"


def _value(words: list[str]) -> int:
    total = current = 0
    for w in words:
        if w in _UNITS:
            current += _UNITS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        elif w in _BIG:
            total += max(current, 1) * _BIG[w]
            current = 0
    return total + current


def numbers(text: str) -> list[float]:
    text = _WORDNUM.sub(_spelled, text or "")
    out = []
    for m in _NUM.finditer(text or ""):
        whole, frac, unit = m.group(1).replace(",", ""), m.group(2), (m.group(3) or "").lower()
        v = float(f"{whole}.{frac}" if frac else whole)
        out.append(v * _SCALE.get(unit, 1.0))
    return out


def _allowed(brief: dict[str, Any]) -> list[float]:
    texts: list[str] = [str(brief.get("headline") or "")]
    for f in brief.get("facts") or []:
        texts.append(str(f.get("text") if isinstance(f, dict) else f))
    for k, v in (brief.get("numbers") or {}).items():
        texts.append(f"{k} {v}")
    texts += _derived(brief)  # the fact card's comparisons, recomputed here from the brief (never taken from the worker)
    vals = [n for t in texts for n in numbers(t)]
    vals += [float(v) for v in (brief.get("numbers") or {}).values() if isinstance(v, (int, float))]
    return vals


def _derived(brief: dict[str, Any]) -> list[str]:
    from app.brand_manager import factcard

    try:
        return factcard.derive(brief)["derived"]
    except Exception:  # noqa: BLE001 - a malformed brief simply has no derived facts
        return []


# Claims that sound like facts: allowed only when the research itself says them ("a record sale", "the rarest card").
SUPERLATIVES = ("record", "records", "rare", "rarest", "rarity", "only a few", "one of only", "one of a few", "handful", "most expensive",
                "most valuable", "highest", "biggest", "largest", "all-time", "all time", "of all time", "ever sold", "ever recorded",
                "tiny number", "very few", "few cards", "scarce", "historic", "unprecedented", "never before", "first time", "skyrocket", "skyrocketed", "soared", "plummeted", "crashed", "outlook")


def _research_text(brief: dict[str, Any]) -> str:
    parts = [str(brief.get("headline") or ""), str(brief.get("note") or "")]
    parts += [str(f.get("text") if isinstance(f, dict) else f) for f in brief.get("facts") or []]
    parts += _derived(brief)  # "the highest of 22 recorded sales" is computed from the research, so it may be said
    return " ".join(parts).lower()


def _sig(a: float, digits: int) -> list[float]:
    """a rounded down / up to `digits` significant figures ("over 14 billion" for 14.37 billion)."""
    import math

    if a == 0:
        return [0.0]
    scale = 10 ** (math.floor(math.log10(abs(a))) - digits + 1)
    return [math.floor(a / scale) * scale, math.ceil(a / scale) * scale]


def _ok_number(v: float, allowed: list[float]) -> bool:
    if v <= 10 and float(v).is_integer():  # "top 5", "3 things", "1st"
        return True
    for a in allowed:
        if any(abs(v - r) <= abs(r) * 1e-9 for d in (1, 2) for r in _sig(a, d)):
            return True
        if a == 0:
            if v == 0:
                return True
            continue
        if abs(v - a) / abs(a) <= 0.01:  # rounding ("$67,400" for 67,412.33; "$67K")
            return True
        if abs(a) >= 1000 and abs(v - a / 1000) / abs(a / 1000) <= 0.01:  # "67 thousand" written without unit
            return True
    return False


def check(script: dict[str, Any], brief: dict[str, Any], strategy: dict[str, Any], avoid: list[str] | None = None) -> list[str]:
    problems: list[str] = []
    voice = str(script.get("voiceover") or "")
    title = str(script.get("title") or "")
    desc = str(script.get("description") or "")
    if len(voice.split()) < 15:
        problems.append("The voiceover is missing or too short.")
    if not title.strip():
        problems.append("The title is missing.")
    spoken = " ".join([voice, title, desc, " ".join(str(s.get("text", "")) for s in script.get("storyboard") or [] if isinstance(s, dict))])
    allowed = _allowed(brief)
    bad = sorted({f"{n:.0f}" if float(n).is_integer() else f"{n:g}" for n in numbers(spoken) if not _ok_number(n, allowed)})
    if bad:
        problems.append(f"These numbers aren't in the research and may be invented: {', '.join(bad[:8])}. Use only the brief's numbers.")
    low = spoken.lower()
    claims = [c for c in FORBIDDEN_CLAIMS if c in low]
    if claims:
        problems.append(f"Unsupported financial claims: {', '.join(claims)}. Report facts; no advice or predictions.")
    research = _research_text(brief)
    superlatives = sorted({w for w in SUPERLATIVES if re.search(rf"\b{w}\b", low) and not re.search(rf"\b{w}\b", research)})
    if superlatives:
        problems.append(f"These claims aren't in the research: {', '.join(superlatives)}. Don't call it rare, a record, the most/biggest or "
                        "one of a few unless the brief says so.")
    banned = [t for t in list(strategy.get("banned_topics") or []) + list(avoid or []) if t and t.lower() in low]
    if banned:
        problems.append(f"Banned topics mentioned: {', '.join(banned)}.")
    if strategy.get("require_sources") and brief.get("sources") and not script.get("sources"):
        problems.append("The script must list its sources (from the brief).")
    secs = len(voice.split()) / WORDS_PER_SECOND
    lo, hi = float(strategy.get("min_seconds", 20)), float(strategy.get("max_seconds", 40))
    if secs < lo - 4:
        problems.append(f"Too short: about {secs:.0f}s of narration; aim for {lo:.0f}-{hi:.0f}s.")
    if secs > hi + 6:
        problems.append(f"Too long: about {secs:.0f}s of narration; aim for {lo:.0f}-{hi:.0f}s.")
    tags = script.get("hashtags") or []
    if len(tags) > int((strategy.get("hashtags") or {}).get("max", 5)) + 2:
        problems.append("Too many hashtags.")
    return problems


def finance_disclaimer(category: str) -> str:
    return "Not financial advice." if category.startswith("crypto") or category == "facts" else ""
