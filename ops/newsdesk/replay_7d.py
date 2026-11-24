"""Replay the News Desk's real stories (backend/tests/fixtures/nd_replay_7d.json) through the CURRENT editorial gates, stage by stage.

    cd backend && python ../ops/newsdesk/replay_7d.py [--json out.json] [--all]

Pure functions only: no database, no network, nothing is posted. Stages, in the order the desk applies them:
  repeat        production said it was the same event as one already out (status superseded): kept as recorded, not recomputed
  newsworthy    app/newsdesk/newsworthy.qualify (consequence path, then the exceptional path; no cross-lane history)
  weak_story    app/newsdesk/quality.weak_story (round-ups, statements, previews)
  bar           the slot bar on the story's best score (Policy.regular_bar); market moves: Policy.market_bar unless urgency major/significant
  market_pause  app/newsdesk/events.market_post: every desk-measured market post is held while state.market_pause is on
Not replayed (need the live database or a slot clock): feed diversity, the 24 h asset cool-down, the delivery gate, freshness/staleness.
The production outcome (status, last why, delivery) is printed next to the replay for each labelled story.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.newsdesk import events, newsworthy, quality  # noqa: E402

DATA = ROOT / "backend" / "tests" / "fixtures" / "nd_replay_7d.json"
PAUSED = True  # production since 2026-11-22 09:35 UTC (and by default since the 2026-11-21 deploy)


def stages(it: dict, paused: bool = PAUSED) -> list[tuple[str, bool, str]]:
    """[(stage, passed, why)] up to and including the first failure."""
    out: list[tuple[str, bool, str]] = []
    p = events.POLICY
    title, summary = it.get("title") or it.get("headline") or "", it.get("summary") or ""
    kind = it.get("kind") or "news"
    if it.get("status") == "superseded":
        out.append(("repeat", False, it.get("why_last") or "superseded"))
        return out
    if kind in ("news", "update"):
        a = newsworthy.qualify(title, summary, outlets=int(it.get("outlets") or 0))
        out.append(("newsworthy", a.ok, a.why if a.ok else a.reason))
        if not a.ok:
            return out
        weak = quality.weak_story(title)
        out.append(("weak_story", not weak, weak or ""))
        if weak:
            return out
        peak = float(it.get("virality_peak") or it.get("virality") or 0)
        out.append(("bar", peak >= p.regular_bar, f"best score {peak:g} vs bar {p.regular_bar}"))
        return out
    ev = SimpleNamespace(kind=kind, facts=it.get("facts") or {}, topic=it.get("topic") or "")
    if paused and events.market_post(ev):  # type: ignore[arg-type]
        out.append(("market_pause", False, "desk-measured market posts are paused (state.market_pause)"))
        return out
    urg = ((it.get("facts") or {}).get("urgency") or {}).get("level")
    peak = float(it.get("virality_peak") or it.get("virality") or 0)
    ok = urg in ("major", "significant") or peak >= p.market_bar
    out.append(("bar", ok, f"urgency {urg or '-'}, score {peak:g} vs market bar {p.market_bar}"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write per-item results here")
    ap.add_argument("--all", action="store_true", help="print every item, not only the labelled ones")
    ap.add_argument("--unpaused", action="store_true", help="replay as if market posts were not paused")
    args = ap.parse_args()
    ds = json.loads(DATA.read_text(encoding="utf8"))
    rows = []
    for it in ds["items"]:
        st = stages(it, paused=not args.unpaused)
        failed = next((s for s in st if not s[1]), None)
        rows.append({"id": it["id"], "kind": it.get("kind"), "label": it.get("label"), "title": (it.get("title") or "")[:110],
                     "replay": "pass" if failed is None else f"{failed[0]}: {failed[2][:140]}", "stage": "pass" if failed is None else failed[0],
                     "production": f"{it.get('status')} | {(it.get('why_last') or '')[:120]}"})
    by_stage = Counter(r["stage"] for r in rows)
    print(f"{len(rows)} items. First failing stage (current code): " + ", ".join(f"{k} {v}" for k, v in by_stage.most_common()))
    for lab in ("publish", "stay_out", "covered", "went_out"):
        sub = [r for r in rows if r["label"] == lab]
        c = Counter(r["stage"] for r in sub)
        print(f"\n== label {lab}: {len(sub)} | " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
        if lab in ("publish", "stay_out") or args.all:
            for r in sub:
                print(f"  #{r['id']} [{r['kind']}] {r['title']}\n      replay: {r['replay']}\n      production: {r['production']}")
    if args.all:
        for r in rows:
            if r["label"] is None:
                print(f"  #{r['id']} [{r['kind']}] {r['stage']:12} {r['title']}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf8")


if __name__ == "__main__":
    main()
