"""Trend scan: which words finance and crypto headlines suddenly use, so Content Studio can ride them.

How a trend is found (nothing is estimated or generated; every number is a count of real, linked headlines):
- Every 30 minutes the scan reads the headlines of the last 24 hours from the outlets in `defaults.TREND_FEEDS` (crypto:
  CoinDesk, Cointelegraph, Decrypt, The Block; finance: CNBC, MarketWatch, Financial Times, BBC Business) and CoinGecko's
  most-searched coins (a real search trend).
- Each headline gives its words and two-word phrases ("rate cut", "GENIUS Act", "$SOL"), minus filler words. A term
  counts once per headline; it becomes a trend candidate when at least 2 headlines from at least 2 different outlets use
  it (one outlet's recurring column is not a trend).
- Each day keeps its own count (bm_trend_days), so today's count is compared with the previous 7 days: NEW (not seen
  before), RISING (at least twice its daily average), STEADY, or COOLING.

How Content Studio uses it:
- `annotate`: a discovered topic that mentions a live trend gets a ranking boost and `brief.trend` (the words, their counts).
- `topics`: the strongest new / rising headline trends become "finance_trends" stories whose facts are the attributed
  headlines and the scan's own counts.
- The writer is told which trend words its story contains (queue payload `trends`), the hashtags may use them, and the
  analyzer measures whether trend-riding videos get more views (`learn`), which scales the boost (0.8-1.4)."""

import asyncio
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.brand_manager.defaults import TREND_FEEDS
from app.models.brand_manager import BmTrendDay

logger = logging.getLogger(__name__)

WINDOW_HOURS = 24
BASELINE_DAYS = 7
KEEP_DAYS = 35
MIN_DOCS, MIN_OUTLETS = 2, 2  # a trend: at least 2 headlines from at least 2 outlets
STORY_MIN_DOCS = 3  # a trend story needs a little more evidence than a boost
MAX_TERMS = 200  # rows kept per day

STOP = set("""a an the and or but if then so of to in on at by for from with into onto over under about after before amid as is are was were be
been being it its it's this that these those there here what why how who whom which when where while than too very can could would should
will may might must do does did done has have had having not no yes all any each more most some such only own same just also still back
up down out off again once your you we our us they them their he she his her i me my mine new says say said told tell get gets got
year years week weeks day days today tonight yesterday tomorrow month months time times now next last first second third one two three
four five six seven eight nine ten per vs via amid against near toward towards despite because since until within without""".split())
# nouns every finance headline uses (and evergreen subjects): never a trend alone, fine in a phrase ("Bitcoin ETF", "rate cut")
COMMON = set("""stock stocks market markets price prices report reports news company companies firm firms business deal deals investor
investors analyst analysts trader traders share shares percent million billion trillion people world global data economy economic
finance financial money crypto cryptocurrency coin coins token tokens fund funds platform platforms payment payments retail tech
regulator regulators transfer transfers bank banks user users group groups chief ceo growth record quarter results earnings rate rates
sales demand supply value asset assets bet bets rule rules plan plans access loss losses gain gains bitcoin btc ether ethereum
eth blockchain usa desk claim claims""".split())
# headline verbs, vague words and outlet / calendar names: never part of a trend term
FILLER = set("""update live latest big bigger biggest top high higher low lower rise rises rising fall falls falling drop drops jump
jumps slip slips climb climbs move moves moving make makes made trade trades trading expert experts chart watch here's what's it's
video podcast opinion analysis exclusive breaking approve approves approved approval launch launches launched open opens opened ruling
seek seeks hit hits set sets call calls add adds backs join joins expand expands extend extends start starts end ends win wins lose
loses push pushes warn warns named name names file files filed face faces turn turns boost boosts lift lifts sees see
show shows head amid send sends take takes bring brings explore explores hope hopes grow grows draw draws surge surges soar soars
sink sinks slide slides plunge plunges rally rallies tumble tumbles eye eyes weigh weighs
cnbc ft bbc marketwatch coindesk cointelegraph decrypt block monday tuesday wednesday thursday friday saturday sunday january february
march april may june july august september october november december""".split())
GENERIC = COMMON | FILLER
CLAUSE = re.compile(r"[:;,|?!()\[\]—–“”‘\"]|\s-\s|'(?=\s)|(?<=\s)'")  # phrases never cross these
TOKEN = re.compile(r"\$[A-Za-z]{2,10}\b|[A-Za-z][A-Za-z0-9&.\-]*[A-Za-z0-9]|[A-Za-z]")
STATUS_ORDER = {"new": 0, "rising": 1, "searched": 2, "measuring": 3, "steady": 4, "cooling": 5}
LIVE = {"new", "rising", "searched", "measuring"}  # the statuses that boost topics and reach the writer (measuring: no baseline yet)


def _norm(tok: str) -> str:
    t = tok.strip(".-&").replace("’", "'")
    t = re.sub(r"'s$", "", t)
    return t.lower()


def _usable(key: str, raw: str, min_len: int = 4) -> bool:
    if key.startswith("$"):
        return len(key) >= 3
    if key in STOP or key.isdigit() or len(key) < 2:
        return False
    return len(key) >= min_len or (raw.isupper() and len(raw) >= 2)  # short words only as acronyms: ETF, SEC, CPI, AI


def terms_of(title: str) -> dict[str, str]:
    """{normalized term: as written} for one headline: usable single words (not common, not filler) and two-word phrases
    within one clause (no filler word, at most one common noun)."""
    out: dict[str, str] = {}
    for clause in CLAUSE.split(title.replace("’", "'")):
        toks = [(t.strip(".-&"), _norm(t)) for t in TOKEN.findall(clause)]
        for i, (raw, key) in enumerate(toks):
            if _usable(key, raw) and key not in GENERIC:
                out.setdefault(key, raw)
            if i + 1 < len(toks):
                raw2, key2 = toks[i + 1]
                if (_usable(key, raw, 3) and _usable(key2, raw2, 3) and key not in FILLER and key2 not in FILLER
                        and not (key in COMMON and key2 in COMMON)):
                    out.setdefault(f"{key} {key2}", f"{raw} {raw2}")
    return out


def count(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Headline items -> trend candidates: [{term, label, docs, sources, examples}], most-used first."""
    docs: dict[str, set[str]] = defaultdict(set)
    outlets: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_titles: set[str] = set()
    for it in items:
        title = str(it.get("title") or "").strip()
        if not title or title.lower() in seen_titles:  # the same headline syndicated twice counts once
            continue
        seen_titles.add(title.lower())
        for key, raw in terms_of(title).items():
            docs[key].add(title.lower())
            outlets[key].add(it["source"])
            labels[key][raw] += 1
            if len(examples[key]) < 5:
                examples[key].append({"title": title[:300], "source": it["source"], "url": it.get("link") or "",
                                      "at": it["at"].isoformat() if it.get("at") else None})
    rows = [{"term": k, "label": _label(labels[k]), "docs": len(docs[k]), "sources": sorted(outlets[k]), "examples": examples[k]}
            for k in docs if len(docs[k]) >= MIN_DOCS and len(outlets[k]) >= MIN_OUTLETS]
    # a word that only appears inside one phrase is that phrase ("jobless" -> "jobless claims")
    by_term = {r["term"]: r for r in rows}
    for r in rows:
        if " " in r["term"]:
            for part in r["term"].split(" "):
                single = by_term.get(part)
                if single and r["docs"] >= 0.75 * single["docs"]:
                    single["subsumed"] = True
    rows = [r for r in rows if not r.get("subsumed")]
    rows.sort(key=lambda r: (-r["docs"], -len(r["sources"]), r["term"]))
    return rows[:MAX_TERMS]


def _label(c: Counter) -> str:
    """The way most headlines write it, capitalized like a label: 'rate cut' -> 'Rate cut', keeps 'GENIUS Act', '$SOL'."""
    raw = c.most_common(1)[0][0]
    return raw if any(ch.isupper() for ch in raw) else raw[:1].upper() + raw[1:]


# --- the scan (job: brand.trends) ----------------------------------------------------------------------------------------


async def _feed(client: httpx.AsyncClient, name: str, url: str) -> tuple[str, list[dict[str, Any]] | None]:
    from app.brand_manager.discovery import UA, parse_feed

    try:
        r = await client.get(url, headers=UA)
        return name, parse_feed(r.text, name) if r.status_code == 200 else None
    except httpx.HTTPError:
        return name, None


async def _searches(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    from app.brand_manager.discovery import COINGECKO, UA

    try:
        r = await client.get(f"{COINGECKO}/search/trending", headers=UA)
        body = r.json() if r.status_code == 200 else {}
        coins = [c.get("item") or {} for c in (body.get("coins") or []) if isinstance(c, dict)] if isinstance(body, dict) else []
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for i, c in enumerate(coins[:7], start=1):
        name, sym = str(c.get("name") or "").strip(), str(c.get("symbol") or "").upper().strip()
        if name and name.lower() not in GENERIC:  # Bitcoin is always searched: not a trend
            out.append({"term": name.lower()[:80], "label": f"{name} ({sym})"[:80] if sym and sym != name.upper() else name[:80], "kind": "search",
                        "docs": 0, "sources": ["CoinGecko searches"],
                        "examples": [{"title": f"#{i} most-searched coin on CoinGecko in the last 24 hours", "source": "CoinGecko",
                                      "url": f"https://www.coingecko.com/en/coins/{c.get('id')}", "rank": i, "symbol": sym}]})
    return out


async def refresh(db: Session, now: datetime, transport: Any = None) -> dict[str, Any]:
    """Read the outlets, count today's trend words and save them as today's row (the day's latest scan wins)."""
    async with httpx.AsyncClient(timeout=20.0, transport=transport, follow_redirects=True) as client:
        results = await asyncio.gather(*(_feed(client, n, u) for n, u in TREND_FEEDS))
        searches = await _searches(client)
    since = now - timedelta(hours=WINDOW_HOURS)
    items = [it for _, got in results for it in got or [] if it.get("at") and since <= it["at"] <= now + timedelta(minutes=10)]
    failed = [n for n, got in results if got is None]
    rows = count(items) + searches
    day = now.date().isoformat()
    if not items and not searches:  # every source down: keep today's last good scan
        return {"headlines": 0, "terms": 0, "failed": failed, "kept_previous": True}
    db.execute(delete(BmTrendDay).where(BmTrendDay.day == day))
    seen: set[str] = set()
    for r in rows:
        if r["term"][:80] in seen:  # a trending coin that's also in the headlines: the headline row (counted first) wins
            continue
        seen.add(r["term"][:80])
        db.add(BmTrendDay(day=day, term=r["term"][:80], label=r["label"][:80], kind=r.get("kind", "headline"), docs=r["docs"],
                          sources=r["sources"], examples=r["examples"]))
    db.execute(delete(BmTrendDay).where(BmTrendDay.day < (now - timedelta(days=KEEP_DAYS)).date().isoformat()))
    db.commit()
    return {"headlines": len(items), "outlets": len(TREND_FEEDS) - len(failed), "terms": len(rows), "failed": failed}


# --- reading the trends --------------------------------------------------------------------------------------------------


def current(db: Session, now: datetime, limit: int = 30) -> list[dict[str, Any]]:
    """Today's trends with their 7-day comparison, strongest first. Cooling terms are listed last (never boosted)."""
    today = now.date()
    first = (today - timedelta(days=BASELINE_DAYS)).isoformat()
    rows = list(db.scalars(select(BmTrendDay).where(BmTrendDay.day >= first)))
    todays = [r for r in rows if r.day == today.isoformat()]
    if not todays:  # the scan hasn't run today yet: yesterday's scan is the latest picture
        last = max((r.day for r in rows), default=None)
        todays = [r for r in rows if r.day == last] if last else []
        today = datetime.fromisoformat(last).date() if last else today
    before = [r for r in rows if r.day < today.isoformat()]
    days_scanned = len({r.day for r in before})
    hist: dict[str, dict[str, int]] = defaultdict(dict)
    for r in before:
        hist[r.term][r.day] = r.docs
    out = []
    for r in todays:
        past = hist.get(r.term, {})
        series = [past.get((today - timedelta(days=d)).isoformat(), 0) for d in range(BASELINE_DAYS, 0, -1)] + [r.docs]
        if r.kind == "search":
            rank = int((r.examples or [{}])[0].get("rank") or 7)
            status, baseline, momentum, strength = "searched", None, None, round(4.0 + (8 - rank) * 0.5, 2)
        else:
            baseline = sum(past.values()) / days_scanned if days_scanned else None
            momentum = (r.docs + 1) / (baseline + 1) if baseline is not None else None
            if baseline is None:
                status = "measuring"
            elif baseline == 0:
                status = "new"
            elif momentum >= 2:
                status = "rising"
            elif momentum >= 0.8:
                status = "steady"
            else:
                status = "cooling"
            lift = math.sqrt(min(momentum, 5.0)) if momentum is not None else 1.0
            strength = round(r.docs * lift * (1 + 0.15 * (len(r.sources or []) - 1)), 2)
        out.append({"term": r.term, "label": r.label, "kind": r.kind, "status": status, "docs": r.docs, "outlets": list(r.sources or []),
                    "baseline": round(baseline, 2) if baseline is not None else None, "momentum": round(momentum, 2) if momentum is not None else None,
                    "strength": strength, "series": series, "examples": list(r.examples or []), "day": today.isoformat()})
    out.sort(key=lambda t: (t["status"] == "cooling", -t["strength"], STATUS_ORDER.get(t["status"], 9)))
    return out[:limit]


def _pattern(t: dict[str, Any]) -> re.Pattern[str]:
    body = r"\W+".join(re.escape(w) for w in t["term"].split(" "))
    if t["kind"] == "search":  # the coin's name in any case, or its ticker as written (with or without $)
        sym = str((t.get("examples") or [{}])[0].get("symbol") or "")
        ticker = rf"|(?<!\w)\$?{re.escape(sym)}(?!\w)" if len(sym) >= 3 else ""
        return re.compile(rf"(?i:(?<!\w){body}(?!\w)){ticker}")
    if t["label"].isupper() and len(t["label"]) <= 5:  # acronyms are matched as written: "SEC" is not "sec"
        return re.compile(rf"(?<![\w$]){re.escape(t['label'])}(?!\w)")
    return re.compile(rf"(?<!\w){body}(?!\w)", re.I)


def match(text: str, trends: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The live trends (new, rising, searched, or still measuring) this text mentions, strongest first."""
    return [t for t in trends if t["status"] in LIVE and _pattern(t).search(text or "")]


def _text_of(topic: Any) -> str:
    b = topic.brief or {}
    facts = " ".join(str(f.get("text") if isinstance(f, dict) else f) for f in b.get("facts") or [])
    coin = b.get("coin") or {}
    return " ".join([topic.topic, str(b.get("headline") or ""), facts, str(coin.get("name") or ""), str(coin.get("symbol") or "")])


def brief_terms(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"term": t["term"], "label": t["label"], "status": t["status"], "kind": t["kind"], "headlines_24h": t["docs"],
             "outlets": len(t["outlets"])} for t in found[:3]]


def annotate(topics: list[Any], trends: list[dict[str, Any]], learned: float | None = None) -> None:
    """Boost the topics that ride a live trend and record which trend words they contain (brief.trend)."""
    mult = max(0.8, min(1.4, float(learned))) if learned else 1.0
    for t in topics:
        if t.category == "finance_trends":
            continue  # it IS the trend
        found = match(_text_of(t), trends)
        if not found:
            continue
        boost = 1 + min(0.4, 0.04 * found[0]["strength"]) * mult
        t.score = round(t.score * boost, 4)
        t.brief["trend"] = {"terms": brief_terms(found), "boost": round(boost, 3)}


def story(t: dict[str, Any], now: datetime) -> Any | None:
    """One headline trend as a finance_trends topic: the attributed headlines + the scan's counts are the only facts."""
    from app.brand_manager.discovery import Topic, _key

    if t["kind"] != "headline" or t["docs"] < STORY_MIN_DOCS or len(t["outlets"]) < MIN_OUTLETS:
        return None
    label, n, k = t["label"], t["docs"], len(t["outlets"])
    facts = [{"text": f"“{label}” appeared in {n} headlines from {k} outlets in the last 24 hours ({', '.join(t['outlets'])}).",
              "source": "Price Chat trend scan"}]
    if t.get("baseline"):
        facts.append({"text": f"Over the previous 7 days it averaged {t['baseline']:.1f} headlines a day in the same outlets.",
                      "source": "Price Chat trend scan"})
    elif t["status"] == "new":
        facts.append({"text": "It didn't appear in these outlets' headlines in the previous 7 days.", "source": "Price Chat trend scan"})
    for e in t["examples"][:5]:
        facts.append({"text": f"{e['source']} headline: “{e['title']}”", "source": e["source"]})
    numbers: dict[str, Any] = {"headlines_24h": n, "outlets": k}
    if t.get("baseline"):
        numbers["daily_average_previous_week"] = round(t["baseline"], 1)
    return Topic("finance_trends", f"“{label}” is all over finance headlines: {n} stories from {k} outlets", _key("trend", t["term"], t["day"]),
                 0.9 + min(0.6, 0.05 * t["strength"]), {
                     "headline": f"Why “{label}” is in so many finance headlines today", "facts": facts, "numbers": numbers, "images": [],
                     "subject": label, "trend": {"terms": brief_terms([t]), "boost": 1.0},
                     "sources": [{"name": e["source"], "url": e["url"], **({"published": e["at"]} if e.get("at") else {})}
                                 for e in t["examples"][:5] if e.get("url")],
                     "note": "A trend story: say what these headlines report, attributing each to its outlet, and that many outlets are "
                             "covering it now. Don't explain causes or consequences the headlines don't state."})


def topics(trends: list[dict[str, Any]], now: datetime, n: int = 3) -> list[Any]:
    out = []
    for t in trends:
        if t["status"] in ("new", "rising") or (t["status"] == "measuring" and t["docs"] >= 4):
            s = story(t, now)
            if s:
                out.append(s)
        if len(out) >= n:
            break
    return out


# --- learning from TikTok views ------------------------------------------------------------------------------------------


def learn(vids: list[Any], overall: float) -> dict[str, Any]:
    """Did videos that rode a trend get more views? -> {rows, multiplier (≥3 measured on each side), words that worked}."""
    rode = [v for v in vids if ((v.brief or {}).get("trend") or {}).get("terms")]
    rest = [v for v in vids if v not in rode]
    avg = lambda vs: sum(v.metrics["views"] for v in vs) / len(vs) if vs else None  # noqa: E731
    out: dict[str, Any] = {"rows": [{"key": "rode a trend", "videos": len(rode), "avg_views": round(avg(rode)) if rode else None},
                                    {"key": "no trend", "videos": len(rest), "avg_views": round(avg(rest)) if rest else None}],
                           "multiplier": None, "words": []}
    if len(rode) >= 3 and len(rest) >= 3 and avg(rest):
        out["multiplier"] = round(max(0.8, min(1.4, avg(rode) / avg(rest))), 2)
    per: dict[str, list[Any]] = defaultdict(list)
    for v in rode:
        for t in v.brief["trend"]["terms"]:
            per[t["label"]].append(v)
    words = [{"label": k, "videos": len(vs), "avg_views": round(avg(vs))} for k, vs in per.items()]
    out["words"] = sorted([w for w in words if overall and w["avg_views"] >= overall], key=lambda w: -w["avg_views"])[:5]
    return out
