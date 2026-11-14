"""The final gate for a trend tweet: the News Desk's own checks (app/newsdesk/checks: length, links, mentions, advice, rumour, attribution,
figures, tickers, and the claim gate for figures, named people, accusations and quotations, plus the wire rules for hype, superlatives and
dropped qualifiers), and the rules that are Trending's own:

- supported: at least one reliable source item (a news outlet) backs the topic; Reddit threads and X trends show interest, never facts.
- one_cashtag: at most one cashtag in the whole tweet (X answers 403 to an API post with more than one).
- buzz: "trending", "viral", "everyone's talking" only when a trend source actually shows it.
- controversy: "slams", "backlash", "feud"... only when the sources use the word themselves.

Personality lives in the framing and the questions; facts come only from source_text. Whatever wrote the words, this runs last.
"""

import re
from typing import Any

from app.newsdesk import checks

CASHTAG_ANY = re.compile(r"(?<![\w$])\$[A-Za-z]{1,6}\b(?![\d.,])")
BUZZ = re.compile(r"\b(trending|trends?\b|viral|everyone(?:'s| is)? (?:talking|asking)|the internet|whole timeline|blowing up|all over (?:x|twitter|reddit))", re.I)
DISCUSSION = re.compile(r"\b(reddit|redditors|r/\w+|threads?)\b", re.I)
CONTROVERSY = re.compile(r"\b(slams?|slammed|blasts?|blasted|feud\w*|outrage\w*|backlash|controvers\w+|furious|fury|war of words|rips?|ripped|"
                         r"scandal\w*|meltdown|humiliat\w+|destroy(?:s|ed)?)\b", re.I)
NOT_FOR_TRENDS = ("BREAKING", "JUST IN", "🚨")  # a trend tweet is never a verified breaking story


def cashtags(text: str) -> list[str]:
    return [m.group(0) for m in CASHTAG_ANY.finditer(text or "")]


def gate(text: str, *, source_text: str, source_names: list[str], reliable_count: int, buzz_ok: bool = False, discussion_ok: bool = False) -> dict[str, Any]:
    """{"ok", "checks": [{check, passed, detail}], "reason"}: reason is the first failure, "" when it may go out."""
    out: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        out.append({"check": name, "passed": bool(passed), "detail": detail})

    t = (text or "").strip()
    add("supported", reliable_count >= 1, "No reliable source backs this topic yet (only social signals): held until an outlet reports it")
    base = checks.check_post(t, source_text=source_text, source_names=source_names, breaking=False, links=())
    out += base.checks
    tags = cashtags(t)
    add("one_cashtag", len(tags) <= 1, f"{len(tags)} cashtags ({', '.join(tags)}): X refuses API posts with more than one")
    add("not_breaking", not any(w in t for w in NOT_FOR_TRENDS), "A trend tweet can't say BREAKING / JUST IN")
    buzz = [m.group(0) for m in BUZZ.finditer(t)]
    add("buzz", not buzz or buzz_ok, f"It says {buzz[0] if buzz else ''!r} but no trend source shows it")
    talk = [m.group(0) for m in DISCUSSION.finditer(t)]
    add("discussion", not talk or discussion_ok, f"It mentions {talk[0] if talk else ''!r} but no Reddit discussion backs it")
    fights = [m.group(0) for m in CONTROVERSY.finditer(t) if not re.search(checks._word_rx(m.group(0)), source_text or "", re.I)]
    add("controversy", not fights, f"Controversy no source reports: {', '.join(dict.fromkeys(fights))}")
    for p in checks.final_gate(t, source_text, wire=True):
        add("final_gate", False, p)
    ok = all(c["passed"] for c in out)
    return {"ok": ok, "checks": out, "reason": next((c["detail"] for c in out if not c["passed"]), "")}
