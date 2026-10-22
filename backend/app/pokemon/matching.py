"""Card identity matching: a title (or CSV fields) -> one catalog card + grade, with a confidence and the reasons.

Safety first. A match is automatic only when the title states everything that tells this card apart from its
neighbours; anything uncertain goes to REVIEW NEEDED, never into the sales history.

  Hard conflicts reject a candidate: a different edition, language, card number, set or year; a "Dark" / "Shining"
  name on a plain card (or the reverse); a grader we don't track (SGC ...).
  Blockers reject the whole title: reprints and look-alikes (Celebrations "Classic Collection", Base Set 2, Evolutions,
  Legendary Collection), proxies, customs, lots, jumbo / metal cards.
  Missing facts cap the confidence: no edition when 1st Edition / Shadowless / Unlimited siblings exist; a Japanese card
  whose title doesn't say Japanese; a variant (No Rarity ...) the title doesn't confirm.

AUTO_MATCH: confidence >= 0.85, at least 0.2 ahead of the next card, and (for sales / listings) a grade.
"""

import re
from dataclasses import dataclass, field
from typing import Iterable

from app.models.pokemon import PokemonCard
from app.pokemon.grades import Grade, GradeError, normalise

AUTO_MATCH = 0.85
MIN_MARGIN = 0.2
ALGORITHM = "match-v1"

# phrases that mean "not the original card" (reprints, look-alikes, fakes) or "not a single card"
BLOCKERS = ("celebrations", "classic collection", "25th anniversary", "base set 2", "evolutions", "legendary collection",
            "reprint", "proxy", "custom", "fan art", "orica", "replica", "unofficial", "metal card", "gold metal", "jumbo",
            "oversized", "oversize", "lot of", "card lot", "bundle", "sticker", "digital", "coin", "pin badge", "plush",
            "figure", "sleeve", "binder", "case only", "slab only", "empty slab", "mcdonald", "topps", "fake")
OTHER_GRADERS = ("sgc", "ace grading", "tag grading", "ags", "hga", "ksa", "mnt grading")
LANGUAGES = {"japanese": "Japanese", "japan": "Japanese", "jpn": "Japanese", "jp": "Japanese", "korean": "Korean", "chinese": "Chinese",
             "german": "German", "french": "French", "italian": "Italian", "spanish": "Spanish", "portuguese": "Portuguese",
             "dutch": "Dutch", "nederlands": "Dutch", "polish": "Polish", "russian": "Russian", "thai": "Thai", "indonesian": "Indonesian",
             "english": "English", "eng": "English"}
NAME_MODIFIERS = ("dark", "shining", "light", "crystal", "delta")
# extra ways people write the catalog's set names
SET_ALIASES: dict[str, tuple[str, ...]] = {
    "base set": ("base set", "base"),
    "team rocket": ("team rocket",),
    "neo genesis": ("neo genesis",),
    "neo destiny": ("neo destiny",),
    "skyridge": ("skyridge",),
    "ex deoxys": ("ex deoxys", "deoxys"),
    "ex dragon frontiers": ("dragon frontiers",),
    "pop series 5": ("pop series 5", "pop 5"),
    "corocoro comic promo": ("corocoro",),
    "japanese base set (expansion pack)": ("base set", "expansion pack", "base"),
    "japanese neo genesis (gold, silver, to a new world...)": ("neo genesis", "neo 1", "gold silver"),
}
VARIANT_WORDS: dict[str, tuple[str, ...]] = {
    "gold star": ("gold star", "star", "★"),
    "no rarity": ("no rarity", "no symbol", "no rarity symbol"),
    "crystal": ("crystal",),
    "illustrator": ("illustrator",),
}
_GRADE_RE = re.compile(r"\b(psa|bgs|beckett|cgc)\s*(?:gem\s*(?:mint|mt)\s*|mint\s*|nm[\s-]*mt\s*|pristine\s*|black\s*label\s*)?(10|[1-9](?:\.5)?)\b")
_RAW_RE = re.compile(r"\b(ungraded|raw)\b")
_RAW_COND = (("near mint", "NM"), ("nm", "NM"), ("lightly played", "LP"), ("lp", "LP"), ("moderately played", "MP"),
             ("heavily played", "HP"), ("damaged", "DMG"))
_NUM_RE = re.compile(r"(?<![\d/])#?\s?(\d{1,3})\s?/\s?(\d{2,3})(?![\d/])")
_HASH_NUM_RE = re.compile(r"#\s?(\d{1,3})(?![\d/])")  # "#100" with no set size: the card number alone
# a reverse-holo print is a different collectible, and the catalog doesn't track it: never credit its sale to the holo
REVERSE = ("reverse holo", "rev holo", "reverse foil", "reverse holofoil")
# distinct printings the catalogue does not track as their own card: a title naming one is NOT the ordinary card
# (e.g. Base Set 1st Edition "Red Cheeks" Pikachu sells for several times the yellow-cheeks card). Matched only when a
# catalog card carries that variant.
UNTRACKED_VARIANTS = ("red cheeks", "red cheek")
_YEAR_RE = re.compile(r"\b(199[5-9]|20[0-3]\d)\b")


def norm(text: str) -> str:
    t = (text or "").lower().replace("★", " ★ ").replace("é", "e").replace("&", " and ")
    t = re.sub(r"[^\w★/.#\s-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(phrase)}(?![\w])", text) is not None


@dataclass
class Parsed:
    text: str
    year: int | None = None
    number: str | None = None  # "4/102"
    number_main: str | None = None  # "4"
    edition: str | None = None
    language: str | None = None
    grade: Grade | None = None
    grade_problem: str | None = None
    blockers: list[str] = field(default_factory=list)


def parse(title: str) -> Parsed:
    t = norm(title)
    p = Parsed(text=t)
    p.blockers = [b for b in BLOCKERS if has(t, b)]
    y = _YEAR_RE.search(t)
    p.year = int(y.group(1)) if y else None
    n = _NUM_RE.search(t)
    if n:
        p.number_main, p.number = str(int(n.group(1))), f"{int(n.group(1))}/{int(n.group(2))}"
    else:
        h = _HASH_NUM_RE.search(t)
        if h:
            p.number_main = p.number = str(int(h.group(1)))
    if has(t, "1st edition") or has(t, "1st ed") or has(t, "first edition") or has(t, "1st"):
        p.edition = "1st Edition"
    elif has(t, "shadowless"):
        p.edition = "Shadowless"
    elif has(t, "unlimited"):
        p.edition = "Unlimited"
    for word, lang in LANGUAGES.items():
        if has(t, word):
            p.language = lang
            break
    other = next((g for g in OTHER_GRADERS if has(t, g)), None)
    g = _GRADE_RE.search(t)
    if other and not g:
        p.grade_problem = f"graded by {other.upper()}, which Price Chat doesn't track"
    elif g:
        try:
            p.grade = normalise(g.group(1), g.group(2))
        except GradeError as exc:
            p.grade_problem = str(exc)
    elif _RAW_RE.search(t):
        cond = next((c for phrase, c in _RAW_COND if has(t, phrase)), None)
        if cond:
            p.grade = Grade("RAW", cond)
        else:
            p.grade_problem = "raw card without a stated condition"
    return p


def from_fields(card: str = "", set_name: str = "", year: object = None, number: str = "", language: str = "", edition: str = "",
                variant: str = "") -> str:
    """CSV columns -> one title-like string, so structured rows and titles go through the same matcher."""
    parts = [str(year or ""), set_name, card, number, language, edition, variant]
    return " ".join(p for p in (str(x).strip() for x in parts) if p and p != "—")


@dataclass
class Match:
    card_id: int | None
    confidence: float
    status: str  # matched | review | rejected
    reasons: list[str]
    candidates: list[tuple[int, float]]
    grade: Grade | None
    grade_problem: str | None = None


def _required_words(c: PokemonCard) -> list[str]:
    return [w for w in norm(c.name.replace("★", "")).split() if w]


def _set_hit(c: PokemonCard, text: str) -> bool:
    names = SET_ALIASES.get(c.set_name.lower(), ()) + (norm(c.set_name),)
    return any(has(text, a) for a in names)


def _siblings(c: PokemonCard, cards: list[PokemonCard]) -> list[PokemonCard]:
    """Cards that differ from `c` only by edition (1st Edition / Shadowless / Unlimited)."""
    return [o for o in cards if o.id != c.id and o.set_name == c.set_name and o.card_number == c.card_number and o.name == c.name
            and o.language == c.language and o.variant == c.variant]


def score(c: PokemonCard, p: Parsed, cards: list[PokemonCard]) -> tuple[float, list[str]]:
    """0 = impossible; otherwise a confidence with the reasons that built it."""
    t = p.text
    why: list[str] = []
    words = _required_words(c)
    if not all(has(t, w) for w in words):
        return 0.0, [f"name '{c.name}' not in the title"]
    for mod in NAME_MODIFIERS:
        if has(t, mod) and mod not in words:
            return 0.0, [f"'{mod.title()}' card, not {c.name}"]
    s, cap = 0.3, 1.0
    why.append(f"name {c.name}")
    other_sets = [o for o in cards if o.set_name != c.set_name and _set_hit(o, t) and not _set_hit(c, t)]
    if _set_hit(c, t):
        s += 0.25
        why.append(f"set {c.set_name}")
    elif other_sets:
        return 0.0, [f"set is {other_sets[0].set_name}, not {c.set_name}"]
    if p.number_main:
        if not c.card_number:
            return 0.0, [f"number {p.number} but this card has none"]
        # printed numbers aren't always "4/102": Japanese vintage prints "No.006" (a Pokédex number), promos "SWSH050"...
        mine = [re.sub(r"\D", "", x) for x in c.card_number.split("/")]
        if not mine[0]:
            return 0.0, [f"number {p.number}, but this card prints {c.card_number}"]
        if p.number_main != str(int(mine[0])) or (p.number and "/" in p.number and len(mine) == 2 and mine[1] and p.number.split("/")[1] != str(int(mine[1]))):
            return 0.0, [f"number {p.number}, not {c.card_number}"]
        s += 0.25
        why.append(f"number {c.card_number}")
    if p.year:
        if abs(p.year - c.year) > 1:
            return 0.0, [f"year {p.year}, not {c.year}"]
        s += 0.1
        why.append(f"year {p.year}")
    if p.language:
        if p.language != c.language:
            return 0.0, [f"{p.language}, not {c.language}"]
        s += 0.1
        why.append(p.language)
    elif c.language != "English":
        cap = min(cap, 0.6)
        why.append(f"title doesn't say {c.language}")
    sib = _siblings(c, cards)
    if p.edition:
        if c.edition not in ("—", p.edition):
            return 0.0, [f"{p.edition}, not {c.edition}"]
        if c.edition == p.edition:
            s += 0.2
            why.append(p.edition)
    elif sib:
        cap = min(cap, 0.6)
        why.append(f"edition not stated ({', '.join(sorted({c.edition, *(o.edition for o in sib)}))} exist)")
    if c.variant:
        vw = VARIANT_WORDS.get(c.variant.lower(), (c.variant.lower(),))
        if any(has(t, w) for w in vw):
            s += 0.2
            why.append(c.variant)
        elif c.variant.lower() in ("no rarity",):
            cap = min(cap, 0.6)
            why.append(f"'{c.variant}' not confirmed")
    return round(min(s, cap, 1.0), 3), why


def match(title: str, cards: Iterable[PokemonCard], need_grade: bool = True) -> Match:
    p = parse(title)
    cards = list(cards)
    # a reprint set's name (Legendary Collection, Base Set 2, Evolutions...) blocks a match to the ORIGINAL card, but is just the
    # set when the candidate card is from that set
    p.blockers = [b for b in p.blockers if not any(b in (c.set_name or "").lower() for c in cards)]
    if p.blockers:
        return Match(None, 0.0, "rejected", [f"not an original single card: {', '.join(p.blockers)}"], [], p.grade, p.grade_problem)
    rev = next((r for r in REVERSE + ("reverse foil",) if has(p.text, r)), None)
    reverse_cards = [c for c in cards if "reverse" in (c.variant or "").lower()]
    if rev:
        # vintage reverse holos (e.g. Legendary Collection "Reverse Foil") are their own catalog printing; others aren't tracked
        if not reverse_cards:
            return Match(None, 0.0, "rejected", [f"{rev}: a reverse-holo print, which the catalog doesn't track"], [], p.grade, p.grade_problem)
        cards = reverse_cards
    else:
        cards = [c for c in cards if c not in reverse_cards]  # a title that doesn't say reverse is never the reverse print
    odd = next((v for v in UNTRACKED_VARIANTS if has(p.text, v)), None)
    if odd:
        cards = [c for c in cards if odd.split()[0] in (c.variant or "").lower() or odd.split()[0] in c.name.lower()]
        if not cards:
            return Match(None, 0.0, "rejected", [f"{odd}: a distinct printing the catalog doesn't track"], [], p.grade, p.grade_problem)
    scored = sorted(((c.id, *score(c, p, cards)) for c in cards), key=lambda x: -x[1])
    live = [(cid, sc, why) for cid, sc, why in scored if sc > 0]
    if not live:
        # say why the cards that share the name don't fit (e.g. "Japanese, not English"), not just "no match"
        near = list(dict.fromkeys(why[0] for _, sc, why in scored if sc == 0 and why and "not in the title" not in why[0]))
        return Match(None, 0.0, "rejected", ["no catalog card fits", *near[:3]], [], p.grade, p.grade_problem)
    best_id, best, why = live[0]
    second = live[1][1] if len(live) > 1 else 0.0
    reasons = list(why)
    status = "matched"
    if best < AUTO_MATCH:
        status = "review"
        reasons.append(f"confidence {best:.2f} is below {AUTO_MATCH}")
    if best - second < MIN_MARGIN and len(live) > 1:
        status = "review"
        reasons.append("another card fits almost as well")
    if need_grade and p.grade is None:
        status = "review"
        reasons.append(p.grade_problem or "no grade in the title")
    return Match(best_id, best, status, reasons, [(cid, sc) for cid, sc, _ in live[:5]], p.grade, p.grade_problem)
