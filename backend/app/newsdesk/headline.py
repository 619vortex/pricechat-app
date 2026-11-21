"""Headlines that say what happened, first and plainly, without adding anything.

A news headline is the outlet's own words, cleaned (no banner words, no outlet name, no trailing full stop) and set in capitals. Two things may
change, and only these:
- a figure our own prices measured for the asset the headline leads with is added in brackets, when the headline gives none
  ("BITCOIN SLIDES AS ETF OUTFLOWS MOUNT (BTC -6.2% IN 24H)"). The figure is a fact we hold, and the checks see where it came from.
- a headline too long for its place loses a trailing "as ...", "amid ..." or "after ..." clause: the context goes, the event stays.
Verbs are never strengthened: "falls" stays "falls". A market post's verb is earned by the measured move (see `move_verb`).
"""

import re

from app.newsdesk import brief, moves, writer

TRAIL = re.compile(r"\s+(?:,\s*)?\b(AS|AMID|AFTER|WHILE|FOLLOWING|AHEAD OF|DESPITE|WITH)\b\s+.*$")
SHORT_WINDOW = {"15m": "15M", "1h": "1H", "4h": "4H", "24h": "24H"}


def move_verb(change: float, ratio: float) -> str:
    up = change > 0
    if ratio >= 3:
        return "SKYROCKETS" if up else "CRASHES"
    if ratio >= 2:
        return "SOARS" if up else "PLUNGES"
    if ratio >= 1.5:
        return "JUMPS" if up else "TUMBLES"
    return "RISES" if up else "FALLS"


def price_tag(key: str, window: str, change: float) -> str:
    return f"{key} {'+' if change > 0 else '-'}{abs(change):.1f}% IN {SHORT_WINDOW.get(window, window.upper())}"


def shorten(headline: str, limit: int) -> str:
    """Drop a trailing clause only if what's left still has a subject and a verb (three words or more)."""
    if len(headline) <= limit:
        return headline
    cut = TRAIL.sub("", headline).rstrip(" ,:;-")
    return cut if len(cut.split()) >= 3 and len(cut) < len(headline) else headline


def news(title: str, *, tag: str = "", limit: int = 140) -> str:
    h = brief.clean_headline(title)
    if tag and not brief.PERCENT.search(h):
        candidate = f"{shorten(h, limit - len(tag) - 3)} ({tag})"
        if len(candidate) <= limit:
            return candidate
    return shorten(h, limit)


def _money(item: dict, price: float) -> str:
    return writer.plain(price) if item.get("group") == "index" else writer.usd(price)


def _price(item: dict, price: float) -> str:
    """A price as the market quotes it: an index level to the cent (22,810.57), a dollar price as money."""
    return writer.level(price) if item.get("group") == "index" else writer.usd(price)


def market(m: moves.Move) -> str:
    name = str(m.item.get("name", m.symbol)).upper()
    if m.kind == "wide":
        verb = move_verb(m.change, m.ratio)
        if name.endswith("STOCKS"):  # they plunge; the market plunges
            verb = verb[:-2] if verb.endswith("ES") and verb[:-2].endswith(("SH", "CH")) else verb[:-1]
        if m.figures:  # each member's level beside its change (owner, 2026-11-21: Telegram 203 printed percentages only)
            figures = ", ".join(f"{str(f['name']).upper()} {'+' if f['change'] > 0 else '-'}{abs(f['change']):.1f}% TO {_price(m.item, float(f['price']))}"
                                for f in m.figures)
        else:
            figures = ", ".join(f"{n.upper()} {'+' if c > 0 else '-'}{abs(c):.1f}%" for n, c in m.members)
        return f"{name} {verb}: {figures} {moves.WINDOW_WORDS[m.window].upper()}"
    if m.kind == "move":
        return f"{name} {move_verb(m.change, m.ratio)} {abs(m.change):.1f}% {moves.WINDOW_WORDS[m.window].upper()} TO {_price(m.item, m.price)}"
    if m.kind in ("high", "low"):
        return f"{name} HITS A NEW 52-WEEK {'HIGH' if m.kind == 'high' else 'LOW'} AT {_money(m.item, m.price)}"
    return f"{name} {'BREAKS ABOVE' if m.change > 0 else 'FALLS BELOW'} {_money(m.item, m.level)}, NOW {_money(m.item, m.price)}"
