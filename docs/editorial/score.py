"""Score a replay (replay72*.json) against labels.py at EVENT level: important events covered once, important events missed, events posted
more than once, filler posted. Usage: score.py replay72.json [replay72_exc.json]"""
import json
import sys
from collections import defaultdict

from labels import label

for path in sys.argv[1:]:
    rows = json.load(open(path, encoding="utf-8"))
    posted = defaultdict(list)
    important = set()
    filler_posted, border_posted, repeats_blocked = [], [], 0
    for r in rows:
        kind, ev = label(r["title"])
        if kind == "important":
            important.add(ev)
        if r.get("repeat"):
            repeats_blocked += 1
        if r["verdict"] != "PUBLISH":
            continue
        if kind == "important":
            posted[ev].append(r["title"])
        elif kind == "borderline":
            border_posted.append(r["title"])
        else:
            filler_posted.append(r["title"])
    covered = [e for e in important if posted.get(e)]
    missed = sorted(important - set(covered))
    dupes = {e: v for e, v in posted.items() if len(v) > 1}
    print(f"== {path}")
    print(f"important events: {len(important)} | covered {len(covered)} | MISSED {len(missed)} | posted twice {len(dupes)}")
    print(f"filler posted: {len(filler_posted)} | borderline posted: {len(border_posted)} | rows blocked as repeats: {repeats_blocked}")
    print(f"total posts: {sum(1 for r in rows if r['verdict'] == 'PUBLISH')}")
    for e in missed:
        print("   missed:", e)
    for e, v in dupes.items():
        print("   twice:", e, "|", " || ".join(x[:50] for x in v))
    for t in filler_posted:
        print("   filler:", t[:100])
