"""Backtest of the News Desk's decision pipeline on the last 7 days of REAL stories (backend/tests/fixtures/nd_replay_7d.json): what the
new pipeline (app/newsdesk/importance + events._release_slot's rules) would have published, slot by slot and between slots, next to what
production did.

    cd backend && python ../ops/newsdesk/backtest_7d.py [--unpaused] [--md out.md]

Pure: no database, no network, nothing is posted. Simulated, in time order, every 5 minutes:
- MAJOR verified (2+ outlets or official) news within 90 minutes of first seen: published at once (min gap 20 min, at most 6 a day); the
  only immediate path (owner, 2026-11-23): significant and notable stories take the six slots;
- each slot (00/04/08/12/16/20 Dubai): the best waiting SIGNIFICANT, else NOTABLE story (NOTABLE: not a third from one desk in 12 h);
- the repeat check: a story that is the same event as one already published in the last 48 h (quality.same_story or newsworthy.same_event)
  is a repeat; production's own "superseded" verdicts are kept;
- market moves: held while paused (production since 2026-11-21), unless --unpaused.
Not simulated: the factual checks before a send (claims, numbers, live price), pictures, platform limits. Those can only refuse more."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.newsdesk import importance, newsworthy, quality  # noqa: E402

DATA = ROOT / "backend" / "tests" / "fixtures" / "nd_replay_7d.json"
SLOT_HOURS_UTC = (20, 0, 4, 8, 12, 16)  # 00/04/08/12/16/20 Dubai
CATCH_UP = timedelta(minutes=90)
BREAKING_AGE = 90.0
MIN_GAP = timedelta(minutes=20)


def _t(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _desk(it: dict) -> str:
    from types import SimpleNamespace

    from app.newsdesk import editorial

    return editorial.desk_of(SimpleNamespace(id=it.get("id"), parts={}, evidence=it.get("evidence") or "", virality=0.0, title=it.get("title") or "", headline=it.get("headline") or "", summary=it.get("summary") or "", topic=it.get("topic") or "", category=it.get("category") or "",
                                             facts=it.get("facts") or {}, kind=it.get("kind") or "news"))


def run(paused: bool = True) -> dict:
    ds = json.loads(DATA.read_text(encoding="utf8"))
    items = [it for it in ds["items"] if _t(it.get("first_seen") or it.get("created_at"))]
    for it in items:
        it["_seen"] = _t(it.get("first_seen") or it.get("created_at"))
    start = min(it["_seen"] for it in items).replace(minute=0, second=0, microsecond=0)
    end = max(it["_seen"] for it in items) + timedelta(hours=4)
    published: list[dict] = []
    decisions: dict = {}
    repeats: dict = {}
    best: dict = {}  # the highest tier each story reached while it waited

    def repeat(it: dict, now: datetime) -> str:
        if it.get("status") == "superseded":
            return "production: " + (it.get("why_last") or "superseded")[:80]
        # the production repeat rules: the same story told in the last 7 days (quality.same_story), or the same event with no substantial
        # development (newsworthy.repeat_of: more deaths, a new country drawn in, a move or sum twice the size, a hard outcome)
        head = it.get("title") or ""
        for p in published:
            if now - p["at"] <= timedelta(days=7) and quality.same_story(head, p["item"].get("title") or ""):
                return f"same story as #{p['item']['id']}"
        told = [newsworthy.Told("released", f"#{p['item']['id']}", p["item"].get("title") or "", p["at"],
                                newsworthy.event_of(p["item"].get("title") or "", p["item"].get("summary") or "")) for p in published]
        return newsworthy.repeat_of(head, it.get("summary") or "", told, now)

    def verdict(it: dict, now: datetime) -> importance.Decision:
        age = (now - it["_seen"]).total_seconds() / 60
        return importance.judge(it.get("title") or "", it.get("summary") or "", outlets=int(it.get("outlets") or 1),
                                official=it.get("evidence") == "official", age_min=age, kind=it.get("kind") or "news", facts=it.get("facts") or {})

    def waiting(now: datetime) -> list[tuple[dict, importance.Decision]]:
        out = []
        done = {p["item"]["id"] for p in published}
        for it in items:
            if it["id"] in done or it["_seen"] > now or now - it["_seen"] > timedelta(hours=12):
                continue
            if (it.get("kind") or "news") in ("market", "data", "recap") or (it.get("topic") or "") == "liquidations":
                if paused or now - it["_seen"] > timedelta(minutes=90):
                    continue
                if str((it.get("facts") or {}).get("verified") or "").startswith("one price source"):
                    continue  # owner, 2026-11-23: a measured move goes out only when a second market confirms it
            rep = repeat(it, now)
            if rep:
                repeats[it["id"]] = rep
                continue
            d = verdict(it, now)
            decisions[it["id"]] = d
            if d.rank > best.get(it["id"], (-1, None))[0]:
                best[it["id"]] = (d.rank, d)
            if d.tier != importance.NOISE:
                out.append((it, d))
        return sorted(out, key=lambda x: (-x[1].rank, -x[1].score, str(x[0]["id"])))

    def publish(it: dict, d: importance.Decision, now: datetime, how: str) -> None:
        published.append({"item": it, "at": now, "how": how, "tier": d.tier, "score": round(d.score), "reason": d.reason, "desk": _desk(it)})

    now = start
    while now <= end:
        last = max((p["at"] for p in published), default=None)
        day = [p for p in published if now - p["at"] < timedelta(hours=24)]
        ranked = waiting(now)
        # the market lane between slots (events._release_market_major): a MAJOR measured move goes promptly; 3 a day, min gap (owner,
        # 2026-11-23: only major events go at once; a significant move takes the next slot)
        for it, d in ranked:
            if (it.get("kind") or "news") != "market" or d.tier != importance.MAJOR:
                continue
            if (last is None or now - last >= MIN_GAP) and sum(1 for p in day if p["how"] == "market") < 3:
                publish(it, d, now, "market")
                last = now
                break
        for it, d in ranked:  # breaking
            age = (now - it["_seen"]).total_seconds() / 60
            if (it.get("kind") or "news") not in ("news", "update") or not d.verified or age > BREAKING_AGE:
                continue
            if d.tier == importance.MAJOR and (last is None or now - last >= MIN_GAP) and sum(1 for p in day if p["tier"] == "major" and p["how"] == "breaking") < 6:
                publish(it, d, now, "breaking")
                break
        slot = next((now.replace(hour=h, minute=0, second=0, microsecond=0) for h in SLOT_HOURS_UTC
                     if timedelta(0) <= now - now.replace(hour=h, minute=0, second=0, microsecond=0) <= CATCH_UP), None)
        if slot is not None and not any(p.get("slot") == slot for p in published):
            last = max((p["at"] for p in published), default=None)
            if last is None or now - last >= MIN_GAP:
                recent = sorted([p for p in published if now - p["at"] <= timedelta(hours=12)], key=lambda p: p["at"], reverse=True)
                for it, d in waiting(now):
                    if d.tier == importance.NOTABLE:
                        desk = _desk(it)
                        if desk != "other" and len(recent) >= 2 and all(p["desk"] == desk for p in recent[:2]):
                            continue
                    publish(it, d, now, "slot")
                    published[-1]["slot"] = slot
                    break
        now += timedelta(minutes=5)
    return {"published": published, "items": items, "decisions": decisions, "repeats": repeats, "best": best, "ds": ds}


def production(ds: dict) -> dict[str, str]:
    out = {}
    for it in ds["items"]:
        if it.get("released_at") and it.get("status") == "released":
            went = "went out" if "delivered" in (it.get("tg_status"), it.get("x_status")) or "published" in (it.get("x_status"),) else "refused at delivery"
            out[str(it["id"])] = went
    return out


def quality_report(r: dict) -> list[str]:
    """Volume per day, duplicates among what was published, major stories that never went out, published stories labelled stay_out, and
    real headlines per tier."""
    pub, ds = r["published"], r["ds"]
    out = ["", "## Quality", "", "**Volume per day (UTC)**", "", "| day | slot | breaking | market | total |", "|---|---|---|---|---|"]
    days = sorted({p["at"].date() for p in pub})
    for day in days:
        c = Counter(p["how"] for p in pub if p["at"].date() == day)
        out.append(f"| {day:%m-%d} | {c.get('slot', 0)} | {c.get('breaking', 0)} | {c.get('market', 0)} | {sum(c.values())} |")
    dups = []
    for i, a in enumerate(pub):
        for b in pub[i + 1:]:
            hours = abs((b["at"] - a["at"]).total_seconds()) / 3600
            if hours > 48:
                continue
            ta, tb = a["item"].get("title") or "", b["item"].get("title") or ""
            same = quality.same_story(ta, tb) or newsworthy.same_event(newsworthy.event_of(ta), newsworthy.event_of(tb), hours_apart=hours)
            if same:
                dups.append((a, b, hours))
    out += ["", f"**Possible duplicates among the published** (same story, or the same event by the cross-lane rule, within 48 h): {len(dups)}"]
    out += [f"- #{a['item']['id']} \"{(a['item'].get('title') or '')[:70]}\" / #{b['item']['id']} \"{(b['item'].get('title') or '')[:70]}\" ({h:.0f} h apart)"
            for a, b, h in dups[:12]]
    got = {p["item"]["id"] for p in pub}
    majors = [(i, d) for i, (rank, d) in r["best"].items() if d.tier == importance.MAJOR and i not in got]
    out += ["", f"**Rated MAJOR at some point but never published**: {len(majors)}"]
    by_id = {it["id"]: it for it in ds["items"]}
    for i, d in majors[:15]:
        last = r["decisions"].get(i)
        out.append(f"- #{i} {(by_id[i].get('title') or '')[:90]}: {('later ' + last.reason[:120]) if last else 'held'}")
    fp = [p for p in pub if p["item"].get("label") == "stay_out"]
    out += ["", f"**Published but labelled stay_out (false positives)**: {len(fp)}"] + [f"- #{p['item']['id']} {p['item'].get('title')}" for p in fp]
    out += ["", "**Examples of what it would publish** (real headlines)"]
    for tier in (importance.MAJOR, importance.SIGNIFICANT, importance.NOTABLE):
        sel = [p for p in pub if p["tier"] == tier]
        out.append(f"- {tier} ({len(sel)}):")
        out += [f"  - {p['at']:%m-%d %H:%M} {p['how']}: {(p['item'].get('title') or '')[:110]}" for p in sel[:8]]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--unpaused", action="store_true", help="market posts not paused (production paused them from 2026-11-21)")
    ap.add_argument("--md", help="write the report here")
    args = ap.parse_args()
    r = run(paused=not args.unpaused)
    pub, ds = r["published"], r["ds"]
    prod = [it for it in ds["items"] if it.get("released_at") and it.get("status") == "released"]
    went = [it for it in prod if it.get("tg_status") == "delivered" or it.get("x_status") == "published"]
    lines = [f"# Backtest, {min(p['at'] for p in pub):%m-%d %H:%M} to {max(p['at'] for p in pub):%m-%d %H:%M} UTC "
             f"({'market posts paused, as production' if not args.unpaused else 'market posts NOT paused'})", "",
             f"Production released {len(prod)} events; {len(went)} went out. The new pipeline would have published **{len(pub)}**: "
             + ", ".join(f"{n} {k}" for k, n in Counter(p['how'] for p in pub).most_common()) + "; by tier "
             + ", ".join(f"{n} {k}" for k, n in Counter(p['tier'] for p in pub).most_common()) + ".", ""]
    lab = {it["id"]: it.get("label") for it in ds["items"]}
    got = {p["item"]["id"] for p in pub}
    for label in ("publish", "stay_out"):
        ids = [i for i, l in lab.items() if l == label]
        lines.append(f"- labelled **{label}**: {sum(1 for i in ids if i in got)} of {len(ids)} published")
    lines += ["", "| when (UTC) | how | tier | score | story | production |", "|---|---|---|---|---|---|"]
    for p in pub:
        it = p["item"]
        prod_s = (it.get("status") or "") + (": " + (it.get("why_last") or "")[:70] if it.get("status") != "released" else "")
        lines.append(f"| {p['at']:%m-%d %H:%M} | {p['how']} | {p['tier']} | {p['score']} | #{it['id']} {(it.get('title') or '')[:90].replace('|', '/')} | "
                     f"{prod_s.replace('|', '/')} |")
    missed = [i for i, l in lab.items() if l == "publish" and i not in got]
    if missed:
        lines += ["", "Labelled publish but not published by the new pipeline:"]
        for i in missed:
            it = next(x for x in ds["items"] if x["id"] == i)
            d = r["decisions"].get(i)
            why = d.reason if d else ("a repeat: " + r["repeats"][i] if i in r["repeats"] else "a market move while market posts are paused")
            lines.append(f"- #{i} {(it.get('title') or '')[:100]}: {why}")
    lines += quality_report(r)
    text = "\n".join(lines)
    print(text)
    if args.md:
        Path(args.md).write_text(text + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
