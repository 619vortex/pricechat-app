"""Hashtags for a video's TikTok caption, built by the backend (the worker's suggestions are only one ingredient).

A good TikTok set mixes 1 brand tag, 1-2 tags about this exact story (the coin, the card, the grade) and 1-2 broader
niche tags, 3-5 in total. So, in order: the brand's "always" tags → tags from the brief (coin / card / PSA grade) → the
script writer's specific tags → the category's tags, each cleaned (lowercase letters, digits and _), deduplicated, and
nothing hype-y or banned (#100x, #tothemoon, #presale...)."""

import re
from typing import Any

from app.brand_manager.defaults import FORBIDDEN_CLAIMS

# never as a tag, whatever the writer suggests
BLOCKED = {"fyp", "foryou", "foryoupage", "viral", "xyzbca", "tothemoon", "moon", "100x", "1000x", "getrich", "pump", "pumpit",
           "presale", "airdrop", "financialadvice", "nfa", "buynow", "gamble", "gambling", "giveaway"}
_STOP = {"the", "and", "for", "ex", "gx", "vmax", "vstar", "v", "of", "a"}


def clean(tag: Any) -> str | None:
    t = re.sub(r"[^0-9a-z_]", "", str(tag or "").lower().replace("é", "e"))
    if not 2 <= len(t) <= 30 or t.isdigit() or t in BLOCKED:
        return None
    return "#" + t


def _banned(tag: str, banned: list[str]) -> bool:
    body = tag[1:]
    return any(b and re.sub(r"[^0-9a-z]", "", b.lower()) in body for b in list(FORBIDDEN_CLAIMS) + banned if len(re.sub(r"[^0-9a-z]", "", b.lower())) >= 4)


def topical(category: str, brief: dict[str, Any]) -> list[str]:
    """The tags only this story gets: #bitcoin #btc, #gengar #psa10."""
    out: list[str] = []
    coin = brief.get("coin") or {}
    for v in (coin.get("name"), coin.get("symbol")):
        if v:
            out.append(str(v))
    card = brief.get("card") or {}
    if card.get("name"):
        words = [w for w in re.findall(r"[A-Za-zé]+", str(card["name"])) if w.lower() not in _STOP]
        if words:
            out.append(words[0])  # "Charizard ex" -> #charizard
    blob = " ".join(str(f.get("text") if isinstance(f, dict) else f) for f in brief.get("facts") or []) + " " + str(brief.get("headline") or "")
    m = re.search(r"\b(PSA|BGS|CGC)\s?(10|9\.5|9)\b", blob)
    if m and category.startswith("pokemon"):
        out.append(m.group(1) + m.group(2).replace(".", ""))
    for t in ((brief.get("trend") or {}).get("terms") or [])[:2]:  # a live trend word: one word or a ticker makes a tag people search
        label = str(t.get("label") or "").split(" (")[0]
        if label and " " not in label:
            out.append(label)
    if category == "crypto_news":
        for word in ("Bitcoin", "Ethereum", "Solana", "XRP", "ETF", "SEC", "Binance", "stablecoin"):
            if re.search(rf"\b{word}s?\b", str(brief.get("headline") or ""), re.I):
                out.append(word)
    return out


def build(suggested: list[Any] | None, strategy: dict[str, Any], category: str, brief: dict[str, Any]) -> list[str]:
    conf = strategy.get("hashtags") or {}
    limit = max(0, min(10, int(conf.get("max", 5))))
    banned = [str(b) for b in strategy.get("banned_topics") or []]
    out: list[str] = []

    def add(items: list[Any], room: int | None = None) -> None:
        n = 0
        for raw in items:
            t = clean(raw)
            if t and t not in out and not _banned(t, banned) and len(out) < limit and (room is None or n < room):
                out.append(t)
                n += 1

    add(list(conf.get("always") or []))
    add(topical(category, brief), 2)
    add(list(suggested or []), 1)
    add(list((conf.get("per_category") or {}).get(category) or []))
    add(list(suggested or []))
    return out
