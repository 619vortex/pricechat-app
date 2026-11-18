"""The final gate for a trend tweet: the News Desk's own checks (app/newsdesk/checks: length, links, mentions, advice, rumour, attribution,
figures, tickers, and the claim gate for figures, named people, accusations and quotations, plus the wire rules for hype, superlatives and
dropped qualifiers), and the rules that are Trending's own:

- supported: at least one reliable source item (a news outlet) backs the topic; Reddit threads and X trends show interest, never facts.
- one_cashtag: at most one cashtag in the whole tweet (X answers 403 to an API post with more than one).
- buzz: "trending", "viral", "everyone's talking" only when a trend source actually shows it.
- controversy: "slams", "backlash", "feud"... only when the sources use the word themselves.
- bait: no hook/teaser lines and no engagement bait ("Worth a minute of your crypto scroll:", "Does this change your view?").
- question: at most one, only tied to a stated fact (shares the source's own distinctive words), never generic; none on a sensitive topic.
- sensitive (app/trending/sensitive): no emoji, question, exclamation, attitude or interest statistics; the attribution and the source's
  hedges ("alleged", "accused", "says") stay.

Facts come only from source_text. Whatever wrote the words, this runs last.
"""

import re
from typing import Any

from app.newsdesk import checks
from app.trending import sensitive

CASHTAG_ANY = re.compile(r"(?<![\w$])\$[A-Za-z]{1,6}\b(?![\d.,])")
BUZZ = re.compile(r"\b(trending|trends?\b|viral|everyone(?:'s| is)? (?:talking|asking)|the internet|whole timeline|blowing up|all over (?:x|twitter|reddit))", re.I)
DISCUSSION = re.compile(r"\b(reddit|redditors|r/\w+|threads?)\b", re.I)
CONTROVERSY = re.compile(r"\b(slams?|slammed|blasts?|blasted|feud\w*|outrage\w*|backlash|controvers\w+|furious|fury|war of words|rips?|ripped|"
                         r"scandal\w*|meltdown|humiliat\w+|destroy(?:s|ed)?)\b", re.I)
NOT_FOR_TRENDS = ("BREAKING", "JUST IN", "🚨")  # a trend tweet is never a verified breaking story
# the retired template lines and the canned questions (owner, 2026-11-14: robotic), and engagement bait in general
BAIT = re.compile(r"(worth a minute|on the [\w ]{0,20}radar|story of the moment|with market stakes|one for the [\w ]{1,15}(crowd|pickers)|"
                  r"worth (?:knowing|your attention)|big picture (?:story )?(?:of the day)?|today's big picture|storyline|scroll:|"
                  r"what happens next|does this change|who comes out ahead|who wins from this|who feels this first|overreaction or|game changer or|"
                  r"what's the market missing|how should markets read|which markets feel|thoughts\?|agree\?|your take|let us know|tell us|"
                  r"drop (?:a|your)|comment below|like if|retweet if|rt if|smash|follow for|don't miss|you won't believe|here's why|"
                  r"buckle up|here we go|stay tuned|watch this space|bullish or bearish|real deal\?)", re.I)
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
ATTITUDE = re.compile(r"\b(wow|whoa|yikes|oof|ouch|wild|brutal|chaos|chaotic|insane|crazy|drama|dramatic|not great|big if true|plot twist|"
                      r"stunning|shocking|jaw-dropping|unbelievable|incredible|bombshell|explosive|grim|ugly|nasty)\b", re.I)
INTEREST = re.compile(r"\b(searches|searched|search interest|google|reddit|on x)\b", re.I)
QUESTION_GENERIC = {"this", "that", "next", "happen", "happens", "mean", "means", "think", "view", "change", "market", "markets", "you", "anything"}


def question_problems(text: str, source_text: str) -> list[str]:
    """A question is allowed only rarely: one, specific (two of the source's own distinctive words), tied to the stated fact."""
    qs = re.findall(r"[^.!?\n]*\?", text or "")
    if not qs:
        return []
    if len(qs) > 1:
        return ["more than one question"]
    words = checks.tokens(qs[0]) - QUESTION_GENERIC
    shared = {w for w in words & checks.tokens(source_text or "") if w not in checks.COMMON_NAMES}
    return [] if len(shared) >= 2 else [f"a generic question not tied to a stated fact: {qs[0].strip()[:80]!r}"]


def sensitive_problems(text: str, source_text: str, sens: dict[str, Any] | None) -> list[str]:
    """For a sensitive topic: neutral and attributed, the source's hedges kept, no interest statistics."""
    if not sens:
        return []
    t = text or ""
    out = []
    if EMOJI.search(t):
        out.append("an emoji")
    if "?" in t:
        out.append("a question")
    if "!" in t:
        out.append("an exclamation mark")
    if m := ATTITUDE.search(t) or checks.INTENSE.search(t):
        out.append(f"attitude ({m.group(0)})")
    if m := INTEREST.search(t):
        out.append(f"interest statistics ({m.group(0)})")
    if not sensitive.ATTRIBUTION.search(t):
        out.append("no attribution (\"X reports\", \"according to\")")
    hedges = {h.lower()[:5] for h in sensitive.HEDGES.findall(source_text or "")}
    if hedges and sens["kind"] in ("allegation", "legal", "crime", "sexual", "terrorism") and not any(h.lower()[:5] in hedges for h in sensitive.HEDGES.findall(t)):
        out.append("the source's hedge words (alleged / accused / says) were dropped")
    return [f"Sensitive topic ({sens['label']}): {', '.join(out)}"] if out else []


def cashtags(text: str) -> list[str]:
    return [m.group(0) for m in CASHTAG_ANY.finditer(text or "")]


def gate(text: str, *, source_text: str, source_names: list[str], reliable_count: int, buzz_ok: bool = False, discussion_ok: bool = False,
         breaking_ok: bool = False) -> dict[str, Any]:
    """{"ok", "checks": [{check, passed, detail}], "reason"}: reason is the first failure, "" when it may go out. breaking_ok: a verified
    fast-lane story (app/terminal/intel.breaking_allowed) may say BREAKING / JUST IN; every other check is the same."""
    out: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        out.append({"check": name, "passed": bool(passed), "detail": detail})

    t = (text or "").strip()
    add("supported", reliable_count >= 1, "No reliable source backs this topic yet (only social signals): held until an outlet reports it")
    base = checks.check_post(t, source_text=source_text, source_names=source_names, breaking=breaking_ok, links=())
    out += base.checks
    tags = cashtags(t)
    add("one_cashtag", len(tags) <= 1, f"{len(tags)} cashtags ({', '.join(tags)}): X refuses API posts with more than one")
    add("not_breaking", breaking_ok or not any(w in t for w in NOT_FOR_TRENDS), "A trend tweet can't say BREAKING / JUST IN (only a verified breaking story)")
    buzz = [m.group(0) for m in BUZZ.finditer(t)]
    add("buzz", not buzz or buzz_ok, f"It says {buzz[0] if buzz else ''!r} but no trend source shows it")
    talk = [m.group(0) for m in DISCUSSION.finditer(t)]
    add("discussion", not talk or discussion_ok, f"It mentions {talk[0] if talk else ''!r} but no Reddit discussion backs it")
    fights = [m.group(0) for m in CONTROVERSY.finditer(t) if not re.search(checks._word_rx(m.group(0)), source_text or "", re.I)]
    add("controversy", not fights, f"Controversy no source reports: {', '.join(dict.fromkeys(fights))}")
    bait = [m.group(0) for m in BAIT.finditer(t)]
    add("bait", not bait, f"A template line or engagement bait: {bait[0] if bait else ''!r}")
    for p in question_problems(t, source_text):
        add("question", False, f"Question: {p}")
    sens = sensitive.classify(f"{source_text} {t}")
    for p in sensitive_problems(t, source_text, sens):
        add("sensitive", False, p)
    for p in checks.final_gate(t, source_text, wire=True):
        add("final_gate", False, p)
    ok = all(c["passed"] for c in out)
    return {"ok": ok, "checks": out, "reason": next((c["detail"] for c in out if not c["passed"]), "")}


# --- the editorial bar and the story's identity (owner, 2026-11-16) --------------------------------------------------------------------
# Run when the tweet is written and again immediately before it is sent. Trend post 152 told a Guardian journalist's diary and an
# unrelated Reuters COP31 report as one story: a tweet now tells exactly one article's story, and that story must be worth a post.
CASHTAG_WORDS = re.compile(r"\(\$[A-Za-z]{1,6}\)|(?<![\w$])\$[A-Za-z]{1,6}\b")
WORLD_MAJOR = 2.0  # a "world" story is in scope only as a major disaster or emergency (earthquake, hurricane, outbreak), not climate talks


def story_problems(text: str, *, lead_title: str, lead_summary: str, measured: str, outlet: str, category: str) -> list[str]:
    """Why this tweet must not go out, or []: the lead story isn't worth a post (app/newsdesk/quality), it is off this account's beat, or the
    tweet says something that isn't in the lead story (another article's words: two stories told as one)."""
    from app.newsdesk import quality
    from app.trending import rank

    out = []
    why = quality.weak_story(lead_title)
    if why:
        out.append(f"not worth a post: {why}")
    if category == "world" and rank._strength(lead_title, rank.WORLD) < WORLD_MAJOR:
        out.append("off this account's beat (crypto, markets, technology, geopolitics, the economy): a world story that is no major emergency")
    allowed = checks.tokens(" ".join([lead_title, lead_summary, measured, outlet, "via reports"]))
    extra = checks.tokens(CASHTAG_WORDS.sub(" ", text or "")) - allowed
    if extra:
        out.append(f"words that aren't in the lead story ({', '.join(sorted(extra)[:5])}): one tweet tells one article's story")
    return out
