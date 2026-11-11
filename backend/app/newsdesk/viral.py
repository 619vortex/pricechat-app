"""PRICE CHAT VIRAL ENGINE 2.0 (owner, 2026-11-11): every story is pitched five ways and the strongest *factual* pitch goes out.

The five hooks
  shock           lead with the most striking verified figure (the dollars lost, the size of the move, a record)
  money           who is exposed and how their prices actually moved since the first report (app/newsdesk/money, our own prices)
  controversy     a conflict the outlets report and attribute: someone accuses, denies, sues, defies (never invented outrage)
  winners_losers  the measured gainers and fallers among the exposed assets (both must exist)
  contradiction   a contrast the reporting itself states: "despite", "even as", "weeks after promising", a missed forecast

Each hook is built only from sentences in the reporting or figures we measured; a hook with nothing to stand on is not built at all. Each
draft then passes the same check every post passes (app/newsdesk/network.verify_copy: no figure or name the sources didn't give, no hype,
no advice) and the no-empty-post gate (substance). The survivors are scored for stopping power (hook_score: concreteness, tension, money,
brevity, front-loading, how strongly the facts support it, and what our audience engaged with lately) and the best one wins.

Controlled experiments: about one story in five (decided by the story's key, so it's stable and auditable) goes out with the runner-up
hook instead, when the runner-up is factual and within MARGIN points. That is how the learner (app/newsdesk/engagement.hook_adjustments)
gets comparisons rather than only its own favourites. The chosen hook, the alternatives and whether it was an experiment are stored in
facts["viral"], shown in Admin, and copied onto the X post so its metrics can be attributed.

The writers (the PC's local model, Claude) get the chosen hook as a directive plus the Price Chat voice (confident, sharp, dry wit allowed;
never about victims, deaths or war). Their draft is checked exactly like everything else; when it fails, the hook's own plain draft goes.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.newsdesk import brief, checks, money, network

HOOKS = ("shock", "money", "controversy", "winners_losers", "contradiction")
HOOK_NAMES = {"shock": "Shocking fact", "money": "Market consequence", "controversy": "Controversy", "winners_losers": "Winners vs losers",
              "contradiction": "Contradiction"}
EXPLORE_PCT = 20
MARGIN = 12  # the runner-up is tried only when it is this close to the best
FLOOR = 35  # no hook below this goes out as an experiment

CONTRAST = re.compile(r"\b(despite|even as|even though|whereas|contrary to|reversal|u-turn|backtrack\w*|reverses?|reversed|"
                      r"(?:days|weeks|months) after (?:saying|pledging|promising|vowing|denying|insisting|claiming|announcing)|"
                      r"after (?:saying|pledging|promising|vowing|denying|insisting)|instead of|while (?:\w+ ){0,3}(?:fell|rose|dropped|climbed))\b", re.I)
FAILED_CALL = re.compile(r"\b(missed (?:estimates|forecasts|expectations)|fell short|below (?:estimates|forecasts|expectations)|"
                         r"(?:higher|lower|hotter|cooler|weaker|stronger|faster|slower|bigger|smaller) than (?:expected|forecast|estimated|estimates)|"
                         r"beat (?:estimates|expectations|forecasts)|"
                         r"(?:had )?(?:predicted|forecast|promised|pledged|vowed)\b.*\b(?:but|instead|yet))\b", re.I)
CONFLICT = re.compile(r"\b(accus\w+|denie[sd]|deny|rejects?|rejected|defies|defied|clash\w*|feud\w*|row over|spat|condemn\w*|sues|sued|lawsuit|"
                      r"criticis\w+|criticiz\w+|slams?|blames?|blamed|hits back|pushes back|warns?|probe|investigat\w+)\b", re.I)
RECORD = re.compile(r"\b(record|all-time|biggest|largest|worst|best|first time|since (?:19|20)\d\d|in (?:a|over a) (?:decade|century)|"
                    r"highest|lowest)\b", re.I)
VAGUE = re.compile(r"\b(could|may|might|here'?s (?:what|why|how)|what (?:to know|we know)|everything you need|explained|explainer|"
                   r"things to know|analysis|opinion|watch|live updates?)\b", re.I)
EMPTY = re.compile(r"^(?:live|watch|video|podcast|opinion|analysis|explainer|newsletter|quiz|in pictures|in charts)\b|"
                   r"\b(live updates?|what we know|what to know|here'?s what|everything you need to know|key takeaways|morning briefing|"
                   r"evening briefing|week ahead|monitoring the situation|things to watch)\b", re.I)
HEDGE = re.compile(r"\b(alleg\w*|accus\w*|suspect\w*|claim\w*|reported(?:ly)?|according to|believed to|said to|denie[sd]|deny)\b", re.I)
STARTERS = {"The", "A", "An", "This", "That", "These", "Those", "Here", "Why", "How", "What", "When", "Live", "Watch", "Markets", "Stocks", "Investors",
            "Traders", "Analysts", "Experts", "Officials", "Prices", "Shares", "Crypto", "Global", "World"}
FIGURE = re.compile(r"[$€£]\s?\d|\d+(?:\.\d+)?\s?%|\b\d[\d,.]*\s?(?:billion|million|bn|trillion|k)\b|\b\d{2,}\b", re.I)

# What the writer is told for each hook. The facts rule still governs everything.
DIRECTIVES = {
    "shock": "Lead with the single most striking verified figure from the reporting, in the first five words of the headline.",
    "money": "Lead with the market consequence: name the exposed assets and their measured moves exactly as listed under MARKET REACTION.",
    "controversy": "Lead with the conflict the reporting describes, attributed to who said it. No outrage words; let the facts carry it.",
    "winners_losers": "Frame it as winners and losers, using only the measured moves listed under MARKET REACTION.",
    "contradiction": "Lead with the contradiction the reporting states (what was said or expected versus what happened).",
}
VOICE = ("Voice: Price Chat. Bloomberg's facts with Crypto Twitter's attitude: confident, sharp, plain words, short sentences; a dry, witty "
         "turn of phrase is welcome in the headline or the question, never sarcasm about people harmed, deaths, war, or victims of a hack or "
         "scam. Never mock a named person. Never exaggerate: the strongest true sentence beats a bigger false one. The headline names who or "
         "what the story is about.")
CAUSE = ("Market moves under MARKET REACTION are what our prices did since the first report, not proof the story caused them: put them only "
         "in stakes, starting \"Since the first report:\", never in the headline or as a cause (no \"as\", \"after\", \"on\" linking them).")
SOMBRE = re.compile(r"\b(killed|dead|deaths?|died|casualt\w+|injured|wounded|victims?|war|missile|strike|attack|shooting|hostages?|earthquake|"
                    r"flood\w*|famine|genocide)\b", re.I)


@dataclass
class Angle:
    hook: str
    copy: network.Copy
    evidence: str  # the sentence or measurement the hook stands on
    support: float  # 0-1: how strongly the facts carry it
    score: int = 0
    parts: dict[str, float] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def out(self) -> dict[str, Any]:
        return {"hook": self.hook, "name": HOOK_NAMES[self.hook], "headline": self.copy.headline, "context": self.copy.context, "stakes": self.copy.stakes,
                "evidence": self.evidence[:300], "support": round(self.support, 2), "score": self.score, "parts": self.parts, "problems": self.problems}


@dataclass
class Plan:
    chosen: Angle | None
    angles: list[Angle]
    exploring: bool = False
    reactions: list[money.Reaction] = field(default_factory=list)
    lead: str = "Since the first report"

    def out(self) -> dict[str, Any]:
        best = max((a for a in self.angles if a.ok), key=lambda a: a.score, default=None)
        return {"hook": self.chosen.hook if self.chosen else "", "score": self.chosen.score if self.chosen else 0,
                "experiment": {"exploring": self.exploring, "control": best.hook if best else "", "arm": self.chosen.hook if self.chosen else ""},
                "directive": DIRECTIVES.get(self.chosen.hook, "") if self.chosen else "", "copy": self.chosen.copy.out() if self.chosen else None,
                "angles": [a.out() for a in self.angles], "reactions": [r.out() for r in self.reactions],
                "measured": money.lines(self.reactions, self.lead)}


# --- building the five hooks --------------------------------------------------------------------------------------------------


def sentences(ev: Any, rows: list[Any] | None) -> list[str]:
    texts = [getattr(ev, "summary", "") or ""] + [f"{getattr(r, 'title', '')}. {getattr(r, 'summary', '') or ''}" for r in rows or []]
    if not rows:  # told runs headlines and summaries together without full stops: only when there are no rows to read properly
        texts.append(str((ev.facts or {}).get("told") or ""))
    seen: set[str] = set()
    out = []
    for t in texts:
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z$“\"'])", re.sub(r"\s+", " ", t).strip()):
            s = s.strip()
            k = s.lower()[:80]
            if 25 <= len(s) <= 400 and k not in seen:
                seen.add(k)
                out.append(s)
    return out


def _magnitude(s: str) -> float:
    usd = brief._amount_usd(s)
    pct = max((float(p) for p in brief.PERCENT.findall(s)), default=0.0)
    m = 0.0
    if usd >= 1e7:
        m = 0.5 + min(0.5, (len(str(int(usd))) - 8) * 0.15)  # $10M 0.5, $100M 0.65, $1B 0.8, $10B 0.95
    if pct >= 3:
        m = max(m, min(1.0, 0.4 + pct / 25))
    if RECORD.search(s):
        m = min(1.0, m + 0.3) if m else 0.45
    return m


def _fit(text: str, limit: int) -> str:
    return network.shorten(text, limit)


def build(ev: Any, rows: list[Any] | None, reactions: list[money.Reaction], lead: str = "Since the first report") -> list[Angle]:
    base = network.desk_copy(ev)
    head = base.headline
    sents = sentences(ev, rows)
    out: list[Angle] = []

    def angle(hook: str, evidence: str, support: float, *, context: str = "", stakes: str = "", headline: str = "") -> None:
        c = network.Copy(headline=headline or head, short=base.short, context=_fit(context, 200) if context else base.context, stakes=stakes,
                         question=base.question, writer="angle")
        out.append(Angle(hook, c, evidence, round(min(1.0, support), 2)))

    # shock: the biggest figure the reporting gives
    ranked = sorted(((s, _magnitude(s)) for s in [head, *sents]), key=lambda x: -x[1])
    if ranked and ranked[0][1] > 0:
        s, m = ranked[0]
        angle("shock", s, m, context=s if s != head else base.context)
    lines = money.lines(reactions, lead)
    if lines.get("reaction"):
        top = max(abs(r.pct) / max(money.noise(r.symbol), 0.01) for r in reactions)
        angle("money", lines["reaction"], 0.4 + min(0.6, top / 8), stakes=lines["reaction"])
    if lines.get("winners_losers"):
        both = min(max(abs(r.pct) for r in reactions if r.pct > 0), max(abs(r.pct) for r in reactions if r.pct < 0))
        angle("winners_losers", lines["winners_losers"], 0.5 + min(0.5, both / 6), stakes=lines["winners_losers"])
    alleged = bool((ev.facts or {}).get("alleged"))
    for s in [head, *sents]:
        if CONFLICT.search(s) and network.CAPWORD.search(s):
            outlets = sum(1 for r in rows or [] if CONFLICT.search(f"{getattr(r, 'title', '')} {getattr(r, 'summary', '')}"))
            angle("controversy", s, 0.5 + (0.2 if CONFLICT.search(head) else 0) + (0.2 if outlets >= 2 else 0),
                  context=s if s != head else base.context, stakes="These are allegations, not findings." if alleged else "")
            break
    for s in [head, *sents]:
        if CONTRAST.search(s) or FAILED_CALL.search(s):
            angle("contradiction", s, 0.6 + (0.2 if FIGURE.search(s) else 0) + (0.2 if s == head else 0), context=s if s != head else base.context)
            break
    return out


# --- scoring: what stops the scroll ---------------------------------------------------------------------------------------------


def hook_score(a: Angle, learned: dict[str, float] | None = None) -> tuple[int, dict[str, float]]:
    """0-100, every point explained. Rewards the things that make a reader stop *and* are true: a concrete figure, a named actor, real
    tension, a measured money consequence, a short headline that front-loads its point, and strong factual support. Penalises vagueness
    and length; hype never scores because verify_copy refuses it first."""
    c = a.copy
    head, body = c.headline, " ".join([c.headline, c.context, c.stakes])
    parts: dict[str, float] = {}
    parts["concrete"] = (12 if FIGURE.search(body) else 0) + (8 if network.CAPWORD.search(head) else 0) + (5 if re.search(r"\$[A-Z]{2,5}\b|[$€£]\s?\d", body) else 0)
    parts["tension"] = 10.0 if CONTRAST.search(body) or CONFLICT.search(body) or FAILED_CALL.search(body) else 0.0
    parts["money"] = 10.0 if a.hook in ("money", "winners_losers") and c.stakes else 0.0
    n = len(head)
    parts["brevity"] = 8.0 if n <= 70 else 4.0 if n <= 90 else -8.0
    parts["front"] = 6.0 if FIGURE.search(" ".join(head.split()[:5])) else 0.0
    parts["support"] = round(a.support * 25, 1)
    if VAGUE.search(head):
        parts["vague"] = -10.0
    if head.isupper():
        parts["shouting"] = -10.0
    if learned and learned.get(a.hook):
        parts["audience"] = round(max(-8.0, min(8.0, learned[a.hook])), 1)
    return max(0, min(100, math.floor(sum(parts.values()) + 20 + 0.5))), parts  # 20: the base any verified story starts from (halves round up)


def substance(c: network.Copy) -> str:
    """The no-empty-posts gate: why these words say nothing, or "". A post needs something specific: a figure, or someone named doing
    something, and it can't be a live blog, an explainer or a 'what we know'."""
    if EMPTY.search(c.headline):
        return f"no news in it: '{EMPTY.search(c.headline).group(0)}'"
    words = c.headline.split()
    if len(words) < 4:
        return "too thin: under four words"
    body = f"{c.headline} {c.context}"
    first = re.sub(r"[’']s$", "", words[0]).strip(":,")
    named = (network.CAPWORD.search(body) or any(w[:1].isupper() for w in words[1:])
             or (first[:1].isupper() and first not in STARTERS and first not in network.NAME_OK))  # "Iran opens a camp": Iran is named
    if not FIGURE.search(body) and not named:
        return "says nothing specific: no figure and no one named"
    return ""


def measured_only(ev: Any, source_text: str) -> set[str]:
    """Figures we measured that no outlet reported: they may only appear in the 'Since the first report' stakes line."""
    m = " ".join(str(x) for x in (stored(ev).get("measured") or {}).values())
    return checks.numbers(m) - checks.numbers(source_text) if m else set()


def draft_problems(ev: Any, c: network.Copy, rows: list[Any] | None) -> list[str]:
    """The Viral Engine's own refusals, on top of network.verify_copy: an empty post, a headline that no longer says who, and a measured
    move presented as the story's consequence."""
    out = []
    empty = substance(c)
    if empty:
        out.append(empty)
    names = {m.group(1).rstrip(".’'") for m in network.CAPWORD.finditer(getattr(ev, "title", "") or "")} - network.NAME_OK
    first = ((getattr(ev, "title", "") or "").split() or [""])[0]
    bare = re.sub(r"[’']s$", "", first).strip(":,")
    later = f"{getattr(ev, 'summary', '') or ''} {(ev.facts or {}).get('told') or ''}"
    # the title's first word is a name when it is possessive ("Binance's") or capitalised again mid-sentence in the reporting ("... said Binance")
    if len(bare) > 2 and bare[:1].isupper() and bare not in network.NAME_OK and (bare != first or re.search(rf"(?<![.!?]\s)(?<!^)\b{re.escape(bare)}\b", later)):
        names.add(bare)
    if names and not any(n.lower() in c.headline.lower() for n in names):
        out.append(f"the headline doesn't say who ({', '.join(sorted(names)[:3])})")
    src = " ".join(f"{getattr(r, 'title', '')} {getattr(r, 'summary', '')}" for r in rows or []) + f" {getattr(ev, 'title', '')} {getattr(ev, 'summary', '')} {(ev.facts or {}).get('told') or ''}"
    said = f"{c.headline} {c.context}"
    hedged = {w.lower() for m in re.finditer(r"\b(?:alleged(?:ly)?|accused(?: of)?|suspected|purported)\s+((?:[\w'’-]+\s+){0,2}[\w'’-]+)", src, re.I)
              for w in m.group(1).split() if len(w) > 3}
    own = c.writer in ("angle", "desk")  # the outlets' own sentences: what they assert, they assert (NYT: "Affair With Chinese Executive")
    if hedged and not own and not HEDGE.search(said) and any(re.search(rf"\b{re.escape(w)}\b", said, re.I) for w in hedged):  # "an alleged spy" must not become "a spy"
        out.append("the reporting says alleged or accused; the draft states it as fact")
    own = measured_only(ev, src)
    used = checks.numbers(f"{c.headline} {c.context}") & own
    if used:
        out.append(f"a measured move ({', '.join(sorted(used))}) told as the story's consequence: only in 'Since the first report'")
    return out


def explore(key: str) -> bool:
    return int(hashlib.sha1(f"viral:{key}".encode()).hexdigest(), 16) % 100 < EXPLORE_PCT


def plan(ev: Any, rows: list[Any] | None, quotes: dict[str, Any] | None, now: datetime, learned: dict[str, float] | None = None,
         since: datetime | None = None, experiments: bool = True) -> Plan:
    """The five hooks for a story, checked and scored, and the one that goes."""
    text = f"{getattr(ev, 'title', '')} {getattr(ev, 'headline', '')}"  # the headline's assets, not every word in the summary
    reactions = money.reaction(text, quotes or {}, since, now) if quotes else []
    lead = "Over the same 24 hours" if getattr(ev, "kind", "") == "market" else "Since the first report"
    angles = build(ev, rows, reactions, lead)
    src = network.source_text_of(ev, rows) + " " + " ".join(money.lines(reactions, lead).values())
    for a in angles:
        a.problems = network.verify_copy(a.copy, src)
        empty = substance(a.copy)
        if empty:
            a.problems.append(empty)
        a.score, a.parts = hook_score(a, learned)
    ok = sorted((a for a in angles if a.ok), key=lambda a: (-a.score, HOOKS.index(a.hook)))
    if not ok:
        return Plan(None, angles, reactions=reactions, lead=lead)
    chosen, exploring = ok[0], False
    if experiments and len(ok) > 1 and explore(str(getattr(ev, "key", "") or getattr(ev, "id", ""))) and ok[1].score >= max(FLOOR, ok[0].score - MARGIN):
        chosen, exploring = ok[1], True
    return Plan(chosen, angles, exploring, reactions, lead)


def stored(ev: Any) -> dict[str, Any]:
    return dict((ev.facts or {}).get("viral") or {})


def angle_copy(ev: Any) -> network.Copy | None:
    """The chosen hook's own plain draft, for when no writer's draft passes."""
    c = stored(ev).get("copy")
    if not isinstance(c, dict) or not c.get("headline"):
        return None
    return network.Copy(**{k: c.get(k, "") for k in ("headline", "short", "context", "stakes", "question")}, writer="angle")


def prompt(ev: Any) -> str:
    """The writer's instructions: the network's rules, the chosen hook and the voice (no wit on sombre stories)."""
    v = stored(ev)
    hook = v.get("hook") or ""
    sombre = bool(SOMBRE.search(f"{getattr(ev, 'title', '')} {getattr(ev, 'summary', '')}"))
    extra = []
    if hook in DIRECTIVES:
        extra.append(f"Angle: {HOOK_NAMES[hook]}. {DIRECTIVES[hook]}")
    extra.append("Voice: sober and precise; this story involves harm to people, so no wit at all." if sombre else VOICE)
    if v.get("measured"):
        extra.append(CAUSE)
    return network.MODEL_PROMPT + "\n" + "\n".join(extra)


def reporting_extra(ev: Any) -> str:
    m = stored(ev).get("measured") or {}
    return f"\n\nMARKET REACTION (measured on our prices): {m['reaction']}" + (f" {m['winners_losers']}" if m.get("winners_losers") else "") if m.get("reaction") else ""


def overview(db: Any, now: datetime, limit: int = 12) -> dict[str, Any]:
    from datetime import timedelta

    from sqlalchemy import select

    from app.models.newsdesk import NdEvent
    from app.newsdesk import datapost, engagement

    evs = db.scalars(select(NdEvent).where(NdEvent.created_at >= now - timedelta(hours=48)).order_by(NdEvent.id.desc()))
    stories = []
    for ev in evs:
        v = (ev.facts or {}).get("viral")
        if not v:
            continue
        stories.append({"event": ev.id, "title": ev.title, "status": ev.status, "score": float(ev.virality or 0), "hook": v.get("hook"),
                        "exploring": bool((v.get("experiment") or {}).get("exploring")), "angles": v.get("angles") or [], "reactions": v.get("reactions") or []})
        if len(stories) >= limit:
            break
    data = {k.split(":")[0]: rows for k, (_, rows) in datapost._cache.items()}
    posts = datapost.candidates(data, now) if data else []
    mgm = datapost.markets_gone_mad(posts)
    released = [{"event": e.id, "title": e.title, "kicker": (e.facts or {}).get("kicker") or "", "status": e.status}
                for e in db.scalars(select(NdEvent).where(NdEvent.kind == "data", NdEvent.created_at >= now - timedelta(days=7)).order_by(NdEvent.id.desc()))][:10]
    return {"at": now.isoformat(), "hooks": {h: {"name": HOOK_NAMES[h], "directive": DIRECTIVES[h]} for h in HOOKS}, "explore_pct": EXPLORE_PCT, "margin": MARGIN,
            "stories": stories, "engagement": engagement.report(db, now),
            "data": {"candidates": [{"kind": p.kind, "headline": p.headline, "interest": p.interest, "why": p.why} for p in posts[:8]],
                     "bar": datapost.DATA_BAR, "mgm_bar": datapost.MGM_BAR, "mgm_today": mgm.headline if mgm else "", "recent": released}}


__all__ = ["Angle", "DIRECTIVES", "HOOKS", "Plan", "angle_copy", "build", "hook_score", "overview", "plan", "prompt", "substance"]
