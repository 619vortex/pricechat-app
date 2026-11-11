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
    return Result(all(c["passed"] for c in checks), checks)
