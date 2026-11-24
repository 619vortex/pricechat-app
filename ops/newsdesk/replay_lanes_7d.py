"""Seven-day replay of BOTH publishing lanes (News Desk + Trending) through the current, unified decision pipeline, next to what production
actually posted on Telegram and X.

    cd backend && python ../ops/newsdesk/replay_lanes_7d.py EXPORT.json [--logs LOG_CANDIDATES.json] [--start ISO] [--end ISO]
                                                                      [--json out.json] [--md out.md]

EXPORT.json: a read-only export of production's news tables (nd_events, tt_topics, tt_posts, nd_telegram_posts, nd_posts), as made by
the owner-approved backup export (headlines, outlets, decisions and post records only; no member data). --logs adds candidates seen in
production logs after the backup was taken (the "editorial candidate" lines).

Pure: no database, no network, nothing is posted. Simulated every 5 minutes, in time order, as ONE pipeline for both lanes:
- stories: News Desk news events and Trending topics backed by at least one reliable outlet, clustered into one story when they tell the
  same real event (trending.coordinate.same_story or newsworthy.same_event, within 48 h); a story's outlets are the distinct outlets seen;
- importance.judge at each step (age since the story was first seen); MAJOR + verified (2+ outlets or official) within 90 minutes goes at
  once (20-minute gap, the burst guard, NO daily count); each slot (20/00/04/08/12/16 UTC) takes the best SIGNIFICANT story, else NOTABLE;
- the repeat check: the same event as one already published within its window is a duplicate (suppressed) unless it is a substantial
  development (newsworthy.development), which goes as an update;
- measured market moves (News Desk market/data events): importance.judge on the move's facts; liquidation posts excluded when production
  held the figure; price moves never held by liquidation problems (the scoped pauses).
Not simulated: pictures, the live re-read of numbers, platform errors, X spend guards. Outlet counts are the final counts (optimistic early on).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.newsdesk import importance, newsworthy  # noqa: E402
from app.trending import coordinate  # noqa: E402

SLOT_HOURS_UTC = (20, 0, 4, 8, 12, 16)
STEP = timedelta(minutes=5)
BREAKING_AGE = 90.0
MIN_GAP = timedelta(minutes=20)
SLOT_CATCH_UP = timedelta(minutes=90)
STALE = timedelta(hours=12)
SAME_WINDOW = timedelta(hours=48)
RELIABLE_MIN = 1
VERIFIED = ROOT / "backend" / "tests" / "fixtures" / "market_events_verified_20261124.json"


def wrong_figures() -> dict[int, str]:
    """Measured market events an independent check (FRED, Nasdaq, exchanges; docs/newsdesk/2026-11-24-market-verification.md) found WRONG:
    they are not replayed as posts (the like-for-like second-source check now refuses them)."""
    import re

    if not VERIFIED.exists():
        return {}
    out: dict[int, str] = {}
    for e in json.loads(VERIFIED.read_text(encoding="utf-8")).get("events") or []:
        if str(e.get("verified")).lower() != "false":
            continue
        for n in re.findall(r"\b(\d{3,4})\b", f"{e.get('instrument')} {e.get('claim')}"):
            if 400 <= int(n) < 3000:
                out[int(n)] = f"held: wrong figure by independent check ({e['id']})"
    return out


def _j(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _t(s: Any) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class Story:
    sid: int
    title: str
    seen: datetime
    outlets: set[str] = field(default_factory=set)
    members: list[str] = field(default_factory=list)  # "event:1162", "topic:45974"
    titles: list[str] = field(default_factory=list)
    kind: str = "news"
    facts: dict[str, Any] = field(default_factory=dict)
    official: bool = False
    prod: list[dict[str, Any]] = field(default_factory=list)  # production posts of this story
    decision: Any = None
    best_title: str = ""
    published: dict[str, Any] | None = None
    fate: str = ""


def load(path: Path, logs: Path | None, start: datetime, end: datetime) -> tuple[list[Story], list[dict[str, Any]]]:
    d = json.loads(path.read_text(encoding="utf-8"))
    t = d["tables"]
    stories: list[Story] = []
    # production's posts, both lanes, both platforms
    prod: list[dict[str, Any]] = []
    for p in t.get("nd_telegram_posts") or []:
        if p.get("status") in ("delivered", "uncertain") and _t(p.get("delivered_at") or p.get("created_at")):
            prod.append({"lane": "telegram", "ref": f"TG {p.get('message_id')}", "title": p.get("title") or p.get("headline") or "",
                         "at": _t(p.get("delivered_at") or p.get("created_at")), "link": p.get("story_link") or ""})
    for p in t.get("nd_posts") or []:
        if p.get("status") in ("published", "uncertain"):
            f = _j(p.get("facts")) or {}
            prod.append({"lane": "x-newsdesk", "ref": f"X post {p.get('id')}", "title": f.get("title") or (p.get("text") or "").split("\n")[0],
                         "at": _t(p.get("published_at") or p.get("created_at")), "link": p.get("key") or ""})
    for p in t.get("tt_posts") or []:
        if p.get("status") in ("published", "uncertain"):
            src = _j(p.get("sources")) or [{}]
            prod.append({"lane": "x-trending", "ref": f"trend post {p.get('id')}", "title": (src[0] or {}).get("title") or (p.get("text") or "").split("\n")[0],
                         "at": _t(p.get("published_at") or p.get("created_at")), "link": f"topic:{p.get('topic_id')}"})
    prod = [p for p in prod if p["at"] and start <= p["at"] < end + timedelta(hours=6)]

    raw: list[dict[str, Any]] = []
    wrong = wrong_figures()
    for e in t.get("nd_events") or []:
        seen = _t(e.get("first_seen") or e.get("created_at"))
        if not seen or not (start <= seen < end):
            continue
        srcs = _j(e.get("sources")) or []
        outs = {str(s.get("name") or "") for s in srcs if isinstance(s, dict) and s.get("name")}
        raw.append({"ref": f"event:{e['id']}", "title": e.get("title") or e.get("headline") or "", "seen": seen, "outlets": outs,
                    "kind": e.get("kind") or "news", "facts": _j(e.get("facts")) or {}, "official": e.get("evidence") == "official",
                    "topic": e.get("topic") or "", "why": wrong.get(int(e["id"])) or e.get("why") or "", "status": e.get("status") or ""})
    for tp in t.get("tt_topics") or []:
        seen = _t(tp.get("first_seen") or tp.get("created_at"))
        if not seen or not (start <= seen < end):
            continue
        srcs = _j(tp.get("sources")) or []
        news = [s for s in srcs if isinstance(s, dict) and s.get("source") == "news" and s.get("outlet")]
        reliable = [s for s in news if s.get("reliable", True)]
        if len({s["outlet"] for s in reliable}) < RELIABLE_MIN or float(tp.get("peak_score") or 0) < 40:
            continue  # social-only or never near the line: Trending never posts these
        title = (reliable[0].get("title") if reliable else "") or tp.get("title") or ""
        raw.append({"ref": f"topic:{tp['id']}", "title": title, "seen": seen, "outlets": {s["outlet"] for s in reliable}, "kind": "news",
                    "facts": {}, "official": False, "topic": tp.get("category") or "", "why": "", "status": tp.get("status") or ""})
    if logs is not None and logs.exists():
        for c in json.loads(logs.read_text(encoding="utf-8")):
            seen = _t(c.get("seen"))
            if seen and start <= seen < end and not any(r["ref"] == f"event:{c['id']}" for r in raw):
                raw.append({"ref": f"event:{c['id']}", "title": c["title"], "seen": seen, "outlets": {f"outlet{i}" for i in range(int(c.get("outlets") or 1))},
                            "kind": "news", "facts": {}, "official": False, "topic": "", "why": c.get("prod") or "", "status": "log"})
    raw.sort(key=lambda r: r["seen"])
    # cluster: same real event within 48 h, either lane
    for r in raw:
        if r["kind"] not in ("news", "update"):
            st = Story(len(stories), r["title"], r["seen"], set(r["outlets"]), [r["ref"]], [r["title"]], r["kind"], r["facts"], r["official"])
            st.fate = r["why"]
            stories.append(st)
            continue
        ev = newsworthy.event_of(r["title"], "")
        home = None
        for st in reversed(stories):
            if r["seen"] - st.seen > SAME_WINDOW:
                break
            if st.kind not in ("news", "update"):
                continue
            # one story = the same event as the cluster's LEAD title (not any member's: members chained unrelated stories together)
            if coordinate.same_story(r["title"], st.title) or \
                    newsworthy.same_event(ev, newsworthy.event_of(st.title, ""), hours_apart=(r["seen"] - st.seen).total_seconds() / 3600):
                home = st
                break
        if home is None:
            home = Story(len(stories), r["title"], r["seen"], set(), [], [], "news", {}, r["official"])
            stories.append(home)
        home.members.append(r["ref"])
        home.titles.append(r["title"])
        home.outlets |= r["outlets"]
        home.official = home.official or r["official"]
    # production posts onto stories
    for p in prod:
        best = None
        for st in stories:
            if p["link"] and p["link"].replace("event:", "event:") in st.members:
                best = st
                break
        if best is None:
            for st in stories:
                if st.kind in ("news", "update") and abs((p["at"] - st.seen).total_seconds()) < 3 * 86400 and \
                        any(coordinate.same_story(p["title"], x) for x in st.titles[:8]):
                    best = st
                    break
        if best is not None:
            best.prod.append(p)
        else:
            p["unmatched"] = True
    return stories, prod


def _judge(st: Story, now: datetime) -> Any:
    age = (now - st.seen).total_seconds() / 60
    if st.kind in ("market", "data", "recap"):
        return importance.judge(st.title, "", kind=st.kind, facts=st.facts, age_min=age)
    best = None
    for title in st.titles[:8]:
        d = importance.judge(title, "", outlets=len(st.outlets), official=st.official, age_min=age)
        if best is None or (d.rank, d.score) > (best.rank, best.score):
            best = d
            st.best_title = title
    return best


def simulate(stories: list[Story], start: datetime, end: datetime) -> list[dict[str, Any]]:
    published: list[dict[str, Any]] = []
    out_events: list[tuple[datetime, Any, Story]] = []  # (at, event identity, story)
    majors: list[datetime] = []
    now = start
    done: set[int] = set()
    slot_done: set[datetime] = set()

    def repeat(st: Story, at: datetime) -> str:
        ev = newsworthy.event_of(st.best_title or st.title, "")
        for t0, e0, s0 in out_events:
            if at - t0 > SAME_WINDOW:
                continue
            if newsworthy.same_event(ev, e0, hours_apart=(at - t0).total_seconds() / 3600) or coordinate.same_story(
                    st.best_title or st.title, s0.best_title or s0.title):  # production's repeat check: what was told vs what is new
                if newsworthy.development(ev, [e0], st.title):
                    return ""
                return f"same event as story {s0.sid} ({s0.title[:60]})"
        return ""

    def publish(st: Story, d: Any, at: datetime, how: str) -> None:
        st.published = {"at": at, "how": how, "tier": d.tier, "score": round(d.score, 1), "reason": d.reason, "title": st.best_title or st.title}
        published.append({"story": st, **st.published})
        out_events.append((at, newsworthy.event_of(st.best_title or st.title, ""), st))
        done.add(st.sid)
        if d.tier == importance.MAJOR:
            majors.append(at)

    from types import SimpleNamespace

    while now <= end + timedelta(hours=4):
        importance.WARS = importance.established_wars([SimpleNamespace(event=e0, at=t0) for t0, e0, _s in out_events], now)
        live = [s for s in stories if s.sid not in done and s.seen <= now and now - s.seen <= STALE and not s.fate.startswith("held")]
        # majors at once
        last = max((p["at"] for p in published), default=None)
        for st in sorted(live, key=lambda s: s.seen):
            if st.kind in ("market", "data") and "liquidat" in st.title.lower() and ("held" in st.fate or "implausible" in st.fate):
                continue
            d = _judge(st, now)
            st.decision = d
            if d is None or d.tier != importance.MAJOR:
                continue
            verified = d.verified or st.kind in ("market", "data")
            if not verified or (now - st.seen).total_seconds() / 60 > BREAKING_AGE:
                continue
            if last is not None and now - last < MIN_GAP:
                break
            if len([m for m in majors if now - m < timedelta(hours=1)]) >= 4:
                st.fate = "burst guard"
                continue
            why = repeat(st, now)
            if why:
                st.fate = "duplicate: " + why
                done.add(st.sid)
                continue
            publish(st, d, now, "breaking")
            last = now
        # slots
        for h in SLOT_HOURS_UTC:
            slot = now.replace(hour=h, minute=0, second=0, microsecond=0)
            if slot > now or now - slot > SLOT_CATCH_UP or slot in slot_done:
                continue
            ranked = []
            for st in live:
                if st.sid in done:
                    continue
                d = _judge(st, now)
                st.decision = d
                if d is not None and d.tier in (importance.SIGNIFICANT, importance.NOTABLE, importance.MAJOR):
                    ranked.append((d.rank, d.score, st, d))
            ranked.sort(key=lambda x: (-x[0], -x[1]))
            for _r, _s, st, d in ranked:
                why = repeat(st, now)
                if why:
                    st.fate = "duplicate: " + why
                    done.add(st.sid)
                    continue
                publish(st, d, now, f"slot {h:02d}:00 UTC")
                slot_done.add(slot)
                break
            else:
                if not ranked:
                    slot_done.add(slot)  # nothing worth posting: the slot stays empty
        now += STEP
    return published


def report(stories: list[Story], prod: list[dict[str, Any]], published: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, Any]:
    def mins(a: datetime, b: datetime) -> float:
        return round((a - b).total_seconds() / 60, 1)

    days = defaultdict(lambda: Counter())
    for p in published:
        days[p["at"].strftime("%m-%d")]["replay"] += 1
        days[p["at"].strftime("%m-%d")]["replay_" + p["tier"]] += 1
    for p in prod:
        days[p["at"].strftime("%m-%d")][p["lane"]] += 1
    rows = []
    for p in published:
        st = p["story"]
        first_prod = min((x["at"] for x in st.prod), default=None)
        rows.append({"story": st.sid, "title": p.get("title") or st.title, "seen": st.seen.isoformat(), "published": p["at"].isoformat(), "how": p["how"],
                     "tier": p["tier"], "score": p["score"], "delay_min": mins(p["at"], st.seen), "outlets": len(st.outlets),
                     "lanes": sorted({m.split(":")[0] for m in st.members}),
                     "production": [f"{x['ref']} at {x['at']:%m-%d %H:%M}" for x in st.prod],
                     "production_delay_min": mins(first_prod, st.seen) if first_prod else None, "reason": p["reason"]})
    prod_only = []
    for st in stories:
        if st.prod and not st.published:
            d = st.decision or _judge(st, st.seen + timedelta(minutes=30))
            prod_only.append({"story": st.sid, "title": st.title, "production": [x["ref"] for x in st.prod],
                              "replay": (d.tier if d else "-"), "why": (st.fate or (d.reason if d else ""))[:200]})
    unmatched = [{"ref": p["ref"], "title": p["title"], "at": p["at"].isoformat()} for p in prod if p.get("unmatched")]
    near = []  # stories the replay didn't publish, ranked by reach, for an editor's review of misses
    for st in stories:
        if st.published or st.kind not in ("news", "update"):
            continue
        d = _judge(st, st.seen + timedelta(minutes=30))
        near.append({"story": st.sid, "title": st.title, "outlets": len(st.outlets), "tier": d.tier if d else "-", "score": round(d.score, 1) if d else 0,
                     "why": (st.fate or (d.reason if d else ""))[:220], "production": [x["ref"] for x in st.prod]})
    near.sort(key=lambda x: (-x["outlets"], -x["score"]))
    dup = [s for s in stories if s.fate.startswith("duplicate")]
    prod_dups = []
    seen_prod: list[dict[str, Any]] = []
    for p in sorted(prod, key=lambda x: x["at"]):
        for q in seen_prod:
            if q["lane"].split("-")[0] == p["lane"].split("-")[0] and p["at"] - q["at"] < SAME_WINDOW and coordinate.same_story(p["title"], q["title"]):
                prod_dups.append(f"{p['ref']} ({p['at']:%m-%d %H:%M}) repeats {q['ref']} ({q['at']:%m-%d %H:%M}): {p['title'][:80]}")
                break
        seen_prod.append(p)
    delays = [r["delay_min"] for r in rows]
    breaking = [r["delay_min"] for r in rows if r["how"] == "breaking"]
    pd = [mins(min(x["at"] for x in s.prod), s.seen) for s in stories if s.prod]
    return {"window": [start.isoformat(), end.isoformat()], "stories": len(stories),
            "story_lanes": dict(Counter("both" if len({m.split(':')[0] for m in s.members}) > 1 else s.members[0].split(":")[0] for s in stories if s.members)),
            "days": {k: dict(v) for k, v in sorted(days.items())},
            "replay_published": len(published), "replay_by_tier": dict(Counter(p["tier"] for p in published)),
            "production_posts": dict(Counter(p["lane"] for p in prod)),
            "delays": {"replay_median_min": statistics.median(delays) if delays else None, "replay_breaking_median_min": statistics.median(breaking) if breaking else None,
                       "replay_breaking_p90_min": sorted(breaking)[int(0.9 * (len(breaking) - 1))] if breaking else None,
                       "production_median_min": statistics.median(pd) if pd else None},
            "published": rows, "production_only": prod_only, "production_unmatched": unmatched, "duplicates_suppressed": len(dup),
            "duplicate_examples": [f"story {s.sid} \"{s.title[:80]}\": {s.fate[:140]}" for s in dup[:40]],
            "production_duplicates": prod_dups, "not_published_top": near[:80]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export", type=Path)
    ap.add_argument("--logs", type=Path)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    end = _t(a.end) or datetime.now(timezone.utc)
    start = _t(a.start) or end - timedelta(days=7)
    stories, prod = load(a.export, a.logs, start, end)
    published = simulate(stories, start, end)
    r = report(stories, prod, published, start, end)
    if a.json:
        a.json.write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(json.dumps({k: v for k, v in r.items() if k not in ("published", "production_only", "not_published_top", "duplicate_examples",
                                                              "production_duplicates", "production_unmatched")}, indent=1, default=str))


if __name__ == "__main__":
    main()
