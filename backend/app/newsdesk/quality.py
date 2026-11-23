"""The editorial bar every X post clears, News Desk and Trending alike (owner, 2026-11-16, after an audit of the last posts: "irrelevant,
boring and sometimes incorrectly combined stories ... zero posts is better than garbage").

`weak_story(title)` says why a headline is not worth a post, or None. Each rule comes from a real post that should never have gone out:
- a round-up of several stories ("Nationwide ground stop lifted, Nvidia spy scandal, starship's longest test flight", News Desk post 118);
- a feature, diary, review or explainer, not an event ("'It is not unusual to go 48 hours with little sleep': what it's like to cover a
  Cop climate summit", trend post 152; "Ukraine, Russia race to build military satellite networks as war rages on", post 117);
- filler and speculation (app/newsdesk/wire.filler: previews, price targets, "could", "set to");
- a routine statement or talks with no outcome ("UN climate chief urges speed as COP31 confronts fossil fuel divide", trend post 152;
  "China, US discuss tariffs, farm trade ahead of Xi-Trump meeting", post 116): talk, unless something happened (a hard event, a figure,
  a decision) or someone announced what they will do.

`same_story(a, b)` is the story-identity rule: two headlines are one post only when they report the same specific event (the trending
clusterer's strict rule: same subject AND the same action or figure, nothing contradicting), never because they share a broad topic
(COP31, Trump, Bitcoin).

Ranking, scores, caps and every factual check stay where they are; this only says no."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

# three or more comma-separated items of two words or more: a newsletter's list of unrelated stories
_SEGMENT_WORDS = 2
# a trend piece or feature about a situation, not something that happened
FEATURE = re.compile(r"\b(race to (?:build|develop|create|win|dominate|secure|replace)|as (?:the )?war rages(?: on)?|the rise of|the future of|"
                     r"the new (?:era|age) of|is reshaping|are reshaping|is changing|are changing|a look at|in pictures|photos:|watch:|"
                     r"the inside story|explainer|analysis:|opinion:|review:|letters?:|obituary|"
                     # "SpaceX-Tesla merger talks put 30,000 bitcoin back in focus" (Telegram 172): attention, not an event
                     r"(?:back )?in(?:to)? (?:the )?(?:focus|spotlight)|under the microscope)\b", re.I)
# a live page's rolling headline ("Middle East latest: ...", Telegram 161): not one story
LIVE_PAGE = re.compile(r"^[\w .'’-]{2,40}\b(?:latest|live)\s*[:\-–—]", re.I)
# talk: someone urging, discussing, hoping; a statement, not an event
TALK = re.compile(r"\b(urges?|urged|calls? (?:for|on)|called (?:for|on)|vows?|vowed|hopes?|hoped|pledges?|pledged|appeals?|discuss(?:es|ed)?|"
                  r"push(?:es)? for|pushed for|press(?:es)? for|pressed for|"  # "U.N. watchdog agency pushes for access to key Iran nuclear site" (174)
                  r"holds? talks|held talks|in talks|to meet|will meet|meets? ahead|ahead of|says?|said|warns?|warned|insists?|insisted|"
                  r"stresses|stressed|reiterates?|reiterated|signals?|signalled|signaled|weighs?|confronts?|faces?|grapples?)\b", re.I)
# "..., Megaspeed says" / "..., officials said": attribution at the end, not the story's verb
TRAILING_ATTRIBUTION = re.compile(r",\s*[^,]{1,50}?\s(?:says?|said|reports?|reported|told \w+|according to [^,]+)\.?\s*$", re.I)
# an announced action ("says it will impose", "vows to retaliate with strikes") or an outcome: something happened or will happen
OUTCOME = re.compile(r"\b(will|to (?:impose|lift|end|cut|raise|ban|sign|send|deploy|launch|withdraw|resume|halt|close|open)|agreed?|agrees|"
                     r"deal (?:reached|signed|struck)|reach(?:es|ed)? (?:a )?deal|signs?|signed|imposes?|imposed|lifts?|lifted|ends?|ended|"
                     r"approv(?:es|ed)|rejects?|rejected|bans?|banned|cuts?|hikes?|hiked|raises?|raised|orders?|ordered|confirms?|confirmed|"
                     # a market moved: "SOL Hits Lowest Since June Ahead of London Breakpoint" is a move, not a preview (production 11-16)
                     r"hits?|lowest|highest|record|falls?|fell|rises?|rose|drops?|dropped|jumps?|jumped|surges?|surged|plunges?|plunged|"
                     r"slides?|slid|slumps?|slumped|tumbles?|tumbled|soars?|soared|rall(?:y|ies|ied)|sinks?|sank)\b", re.I)
# what someone says happened is news when it names an event: "Estonia Says Russia Behind Failed Arson Attempt on Drone-Maker"
EVENT_SAID = re.compile(r"\b(attacks?|attacked|arson|sabotage|strikes?|struck|hack(?:ed|s)?|explosions?|blasts?|fires?|killed|kills|dead|deaths?|"
                        r"arrest(?:ed|s)?|raids?|raided|seiz(?:ed|es|ure)|defaults?|defaulted|bankrupt\w*|collaps\w+|outages?|breach(?:ed|es)?|"
                        r"shot down|downed|invad\w+|behind)\b", re.I)


# production 2026-11-22: "Two injured, one critically, in Iranian attack on industrial plant in Bahrain" was refused as a round-up: a clause
# that opens with a preposition, or a two-word aside ("one critically"), belongs to the same sentence
CONTINUATION = re.compile(r"^(?:[\"'‘“]?\w+ing\b|as\b|after\b|while\b|amid\b|says?\b|said\b|according\b|in\b|on\b|at\b|near\b|from\b|with\b|"
                          r"during\b|into\b|off\b|over\b|one\b|both\b|all\b|including\b)", re.I)


def is_roundup(title: str) -> bool:
    from app.newsdesk import network

    t = re.sub(r"\s+", " ", (title or "").strip())
    if network.is_roundup(t) or LIVE_PAGE.match(t):
        return True
    parts = [p.strip() for p in re.split(r"[,;]", t) if p.strip()]
    # a continuation clause ("..., Saying 'It's Life'", "..., as shares fall") makes it one sentence, not a list (production 2026-11-17:
    # "Trump Downplays Microsoft, JPMorgan Outages, Saying 'It's Life'" was refused as a round-up)
    if any(CONTINUATION.match(p) for p in parts[1:]):
        return False
    return len(parts) >= 3 and all(len(p.split()) >= _SEGMENT_WORDS for p in parts)


def feature(title: str) -> str | None:
    from app.newsdesk import checks
    from app.trending import sources

    t = title or ""
    m = FEATURE.search(t) or sources.NOT_A_TOPIC.search(t) or checks.NOT_NEWS.search(t)
    if m:
        return f"a feature or explainer, not an event ({m.group(0).strip().lower()})"
    if checks.is_explainer(t):
        return "an explainer, not an event"
    return None


def talk_only(title: str) -> str | None:
    """A statement or talks with nothing that happened: no hard event, no figure, no decision, no announced action."""
    from app.newsdesk import editorial, wire

    t = TRAILING_ATTRIBUTION.sub("", (title or "").strip())
    m = TALK.search(t)
    if m is None:
        return None
    if editorial.hard_event(title or "") or wire.FIGURE.search(t) or OUTCOME.search(t):
        return None
    if m.group(0).lower() in ("say", "says", "said") and EVENT_SAID.search(t[m.end():]):
        return None  # a reported event ("..., says Russia behind failed arson attempt"), attributed
    # a decision verb other than the talk itself ("Fed holds rates", "SEC sues Binance") makes it news
    rest = t[:m.start()] + t[m.end():]
    decided = [d.group(0).lower() for d in wire.DECISION.finditer(rest)]
    if any(d not in ("says", "said", "tells", "told", "warns", "warned", "reports", "reported") for d in decided):
        return None
    return f"a routine statement or talks with no outcome ({m.group(0).lower()})"


def weak_story(title: str, summary: str = "") -> str | None:
    """Why this headline isn't worth a post, or None."""
    from app.newsdesk import wire

    t = (title or "").strip()
    if not t:
        return "no headline"
    if is_roundup(t):
        return "a round-up of several stories, not one story"
    why = feature(t)
    if why:
        return why
    why = wire.filler(SimpleNamespace(kind="news", title=t))
    if why:
        return why
    return talk_only(t)


def same_story(a: str, b: str) -> bool:
    """One specific event, not one broad topic (see the module's rules)."""
    from app.trending import rank

    return rank.related(a or "", b or "")


def event_problems(ev: Any, rows: list[Any] | None = None) -> list[str]:
    """The final editorial check on a News Desk event, right before it goes out: still worth a post, and every outlet it links or cites
    reports this same event. Our own measured moves and data posts say what they measured and are checked elsewhere."""
    if getattr(ev, "kind", "") not in ("news", "update"):
        return []
    title = getattr(ev, "title", "") or ""
    problems = []
    from app.newsdesk import importance

    # owner, 2026-11-23: the same test as at release (importance.judge on the story's content, without the time fade), never a second,
    # different one: what the editor calls noise is stopped here too (a headline rewritten, or released before a rule changed)
    d = importance.judge_event(ev, age_min=None) if hasattr(ev, "evidence") else importance.judge(title, getattr(ev, "summary", "") or "")
    if d.tier == importance.NOISE:
        problems.append(f"not worth a post: {d.reason}")
    lead = title
    for r in rows or []:
        other = str(getattr(r, "title", "") or "")
        if other and other != lead and not same_story(lead, other) and not checks_same(lead, other):
            problems.append(f"a different story among its sources: {other[:90]!r}")
            break
    return problems


def split_off_story(ev: Any, rows: list[Any]) -> tuple[list[Any], list[Any]]:
    """(dropped, kept): the event's source rows that tell a DIFFERENT story are left out before the caption is written, instead of the
    whole post being refused at the last check. Production 2026-11-17/18: four of five breaking releases (#649 FAA routers, #658, #662
    Kuwait/Qeshm strikes, #667) were skipped on both X and Telegram because one corroborating row was another story ("a different story
    among its sources: 'Iran attacks US air base in Bahrain'"). The lead (the event's own headline) always stays."""
    lead = getattr(ev, "title", "") or ""
    kept, dropped = [], []
    for r in rows:
        other = str(getattr(r, "title", "") or "")
        if not other or other == lead or same_story(lead, other) or checks_same(lead, other):
            kept.append(r)
        else:
            dropped.append(r)
    if not kept and rows:  # nothing tells the event's own headline: keep what we have and let the final check decide
        return [], rows
    return dropped, kept


REPEAT_DAYS = 7


def already_posted(db: Any, ev: Any, now: Any) -> str | None:
    """The same story already went out in the last week, by what the channel actually delivered (Telegram) or X actually published, not by
    the events' own bookkeeping: Telegram posts 170 and 171 (2026-11-13 00:03 and 04:01) were the same "North Korean hackers are creating
    memecoins to wash loot from $6.3B Binance hack" (first posted 11-09, post 151), and 168 repeated 158's Nvidia story two days later.
    Updates are developments of a story and have their own rules."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.models.newsdesk import NdPost, NdTelegramPost

    if getattr(ev, "kind", "") != "news":
        return None
    title = (getattr(ev, "title", "") or "").strip()
    if not title:
        return None
    since = now - timedelta(days=REPEAT_DAYS)
    own = f"event:{getattr(ev, 'id', None)}"
    told = [(r.title, r.created_at) for r in db.scalars(select(NdTelegramPost).where(NdTelegramPost.status.in_(("delivered", "uncertain")),
                                                                                     NdTelegramPost.created_at >= since, NdTelegramPost.story_link != own))]
    told += [(str((p.facts or {}).get("title") or ""), p.created_at) for p in db.scalars(select(NdPost).where(
        NdPost.status.in_(("published", "uncertain")), NdPost.created_at >= since, NdPost.key != own))]
    for other, at in told:
        if other and (other.strip().lower() == title.lower() or same_story(title, other)):
            return f"already posted on {at:%d %b %H:%M}: {other[:90]!r}"
    return None


def checks_same(a: str, b: str) -> bool:
    from app.newsdesk import network

    return network.related(a, b)


# --- one incident, told in other words (owner, 2026-11-23: "eliminate the duplicate pairs without suppressing genuinely new developments") ----
# The 7-day backtest of the new pipeline published the FBI crime-database breach four times ("FBI crime database hacked: CNN", "USDA, FBI
# breached by hackers ...", "FBI national crime database suffers suspected breach", "FBI investigates apparent hack of critical crime
# database"), the SEC's $37M crypto-fraud case twice, the Anthropic IPO six times and the Bahrain plant attack twice: same_story (wording) and
# the event-family rule (newsworthy.repeat_of) both missed them. Two headlines are one incident when they name the same specific actor or
# thing (never a country, a capital or a leader) AND the same kind of thing happening to it, or state the same sum, or, in a conflict, the
# same sides and the same struck object within a day.
INCIDENT_KIND = {  # word -> the kind of incident it names
    **{w: "listing" for w in ("ipo", "ipos", "listing", "debut", "debuts", "trading debut", "public trading", "priced", "prices shares", "valuing")},
    **{w: "breach" for w in ("hack", "hacked", "hacks", "hackers", "breach", "breached", "exploit", "exploited", "heist", "theft", "stolen", "drained",
                              "compromise", "compromised", "attack on its systems")},
    **{w: "fraud" for w in ("fraud", "scam", "fake", "charges", "charged", "sues", "sued", "settlement")},
    **{w: "outage" for w in ("outage", "outages", "ground stop", "went down", "goes down")},
    **{w: "bridge" for w in ("bridge",)}, **{w: "refinery" for w in ("refinery", "refineries")},
    **{w: "plant" for w in ("plant", "facility", "factory")}, **{w: "port" for w in ("port", "naval base", "harbour", "harbor")},
    **{w: "ship" for w in ("ship", "ships", "tanker", "tankers", "warship", "warships", "vessel", "vessels")},
    **{w: "database" for w in ("database",)}, **{w: "pipeline" for w in ("pipeline", "pipelines")},
    **{w: "airport" for w in ("airport", "airbase", "air base")}, **{w: "base" for w in ("base", "military site", "missile site", "launch site")},
}
_KIND_RX = re.compile(r"\b(" + "|".join(sorted((re.escape(w) for w in INCIDENT_KIND), key=len, reverse=True)) + r")\b", re.I)
INSTITUTION = re.compile(r"\b(FBI|SEC|DOJ|CFTC|FTC|IRS|NSA|CIA|USDA|FAA|IMF|ECB|OPEC|NATO|IAEA|UN)\b")
ALIAS = {"KERCH": "CRIMEA", "CRIMEAN": "CRIMEA"}  # one bridge, two names
SOURCES = {"CNN", "BBC", "REUTERS", "AP", "AFP", "BLOOMBERG", "CNBC", "WSJ", "FT"}  # "...: CNN" names who reported it, not who it is about
CONFLICT_OBJECTS = {"bridge", "refinery", "plant", "port", "ship", "pipeline", "airport", "base"}
INCIDENT_WINDOW_H = 72.0  # a hack, a fraud case, a listing, an outage: one story for three days
CONFLICT_WINDOW_H = 24.0  # a strike on the same bridge two days later is a new strike


def incident_kinds(title: str) -> set[str]:
    return {INCIDENT_KIND[m.group(1).lower()] for m in _KIND_RX.finditer(title or "") if m.group(1).lower() in INCIDENT_KIND}


def same_incident(a: str, b: str, hours_apart: float) -> bool:
    """Whether two headlines report one incident (see the rules above)."""
    from app.newsdesk import newsworthy

    ea, eb = newsworthy.event_of(a or ""), newsworthy.event_of(b or "")

    def own(e: Any, title: str) -> set[str]:
        names = {ALIAS.get(x, x) for x in e.anchors if not x.startswith(("$", "~")) and not newsworthy.NATIONAL.match(x) and x not in SOURCES}
        return names | {m.group(1).upper() for m in INSTITUTION.finditer(title or "")}  # the FBI, the SEC: actors here, even if not "anchors"

    shared = own(ea, a) & own(eb, b)
    ka, kb = incident_kinds(a), incident_kinds(b)
    conflict = ea.kin() == "conflict" or eb.kin() == "conflict"
    window = CONFLICT_WINDOW_H if conflict else INCIDENT_WINDOW_H
    if hours_apart > window:
        return False
    institutional = bool(shared) and all(INSTITUTION.fullmatch(x) for x in shared)
    if shared and (ka & kb) and not (institutional and not (ka & kb) - {"fraud"}):
        return True  # an agency bringing two different cases is not one incident: it needs the same sum (below) or the same object
    if shared and conflict and ea.kin() == eb.kin() == "conflict":
        return True  # the same named place or unit attacked ("Qeshm") within a day
    if ea.money and eb.money and abs(ea.money - eb.money) / max(ea.money, eb.money) <= 0.05 and (shared or ka & kb):
        return True  # the same sum: "$37M fraud" and "More Than $37 Million"
    if not (conflict and ea.states & eb.states and (ka & kb & CONFLICT_OBJECTS)):
        return False
    pa, pb = _places(ea), _places(eb)
    return not (pa and pb and not (pa & pb))  # "Kyiv bridge" and "Crimean bridge" are two bridges


COUNTRY_WORD = re.compile(r"^(?:RUSSIA\w*|UKRAIN\w*|IRAN\w*|ISRAEL\w*|US|USA|AMERICA\w*|CHINA|CHINESE|SAUDI\w*|BAHRAIN\w*|KUWAIT\w*|QATAR\w*|"
                          r"YEMEN\w*|HOUTHIS?|LEBAN\w*|SYRIA\w*|IRAQ\w*|EU|EUROPE\w*|NATO|ZELENSK\w*|PUTIN|TRUMP|NETANYAHU|XI|KHAMENEI|KREMLIN)$")


def _places(e: Any) -> set[str]:
    """The specific places and things a conflict headline names: cities (Kyiv), regions (Crimea), units; never a country or its leader."""
    return {ALIAS.get(x, x) for x in e.anchors if not x.startswith(("$", "~")) and not COUNTRY_WORD.match(x)}


def _named_as_party(title: str, rx: "re.Pattern[str]") -> bool:
    """Whether a country appears as an actor or a target, not only as an owner ("Russia's bridge")."""
    return any(not re.match(r"\s*['’]s\b", title[m.end():]) for m in rx.finditer(title))


def incident_development(new_title: str, new_summary: str, before: list[str]) -> str:
    """What is materially new against the earlier headlines of one incident ("" = nothing): newsworthy.development's rules (more deaths, a
    new country drawn in, a move or sum at least 1.5 to 2 times the size, a hard outcome), plus the first market move of a listing or a
    company (a debut that jumps 35% after a headline about the pricing)."""
    from app.newsdesk import newsworthy

    new = newsworthy.event_of(new_title or "", new_summary or "")
    old = [newsworthy.event_of(t) for t in before]
    dev = newsworthy.development(new, old, f"{new_title} {new_summary or ''}")
    if dev.endswith("drawn into the conflict"):
        # a country named only as an owner ("Russia's Prized Kerch Bridge Struck") is not a new party to the conflict
        fresh = [x.strip() for x in dev[: -len(" drawn into the conflict")].split(",")]
        party = [st for st in fresh if st in newsworthy.STATES and _named_as_party(new_title or "", newsworthy.STATES[st])]
        dev = f"{', '.join(party)} drawn into the conflict" if party else ""
    if dev:
        return dev
    if new.pct >= 10 and all(not o.pct for o in old):
        return f"the first market move: {new.pct:g}%"
    return ""


__all__ = ["event_problems", "feature", "incident_development", "incident_kinds", "is_roundup", "same_incident", "same_story", "talk_only", "weak_story"]
