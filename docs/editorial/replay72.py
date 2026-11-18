"""Replay the 270 real candidates of the last 72 h (candidates.json) through the editorial gate in time order. History = what the gate let
out before (a true replay). With --exceptional the exceptional-story path is included. Output replay72[_exc].json + .txt."""
import json
import sys
from datetime import datetime

import os
sys.path.insert(0, os.environ.get("NW_BACKEND", "D:/pc-wt/terminal/backend"))
from app.newsdesk import newsworthy as nw  # noqa: E402

exc = "--exceptional" in sys.argv
rows = json.load(open(os.environ.get("NW_CANDIDATES", "candidates.json"), encoding="utf-8"))
history: list[nw.Told] = []
# the channel as it really stood before the window (Telegram posts from the t.me mirror): repeats are judged against what was already out
start = rows[0]["first"]
for p in json.load(open("tg_posts.json", encoding="utf-8")):
    if p["at"] and p["at"] < start and p["text"]:
        t = p["text"].splitlines()[0].split(". ")[0]
        history.append(nw.Told("telegram", f"telegram {p['n']}", t, datetime.fromisoformat(p["at"]), nw.event_of(t)))
out = []
for r in rows:
    now = datetime.fromisoformat(r["first"])
    title = r["title"].rstrip("…").strip()
    title = title if not title.isupper() else title  # News Desk logs keep the shouted headline; the gate reads shouting
    rep = nw.repeat_of(title, "", history, now)
    a = nw.assess(title, "", repeat=rep)
    if not a.ok and not rep and a.strength == "none" and (dev := nw.development_of(title, "", history, now)):
        a = nw.Assessment(True, "strong", dev, "", 70, a.parts, a.event)
    via = "consequence" if a.ok else ""
    if exc and not a.ok and not rep:
        e = nw.exceptional(title, "", outlets=int(r.get("outlets") or 0), category=r.get("category") or "", interest=r.get("interest") or {})
        if e.ok:
            a, via = nw.Assessment(True, "strong", e.why, "", 70, a.parts, a.event), "exceptional"
    if a.ok:
        history.append(nw.Told("replay", f"candidate {len(out)}", title, now, nw.event_of(title)))
    out.append({**r, "verdict": "PUBLISH" if a.ok else "REJECT", "via": via, "why": a.why, "reason": (a.reason if not a.ok else "") + (" " + rep if rep else ""), "repeat": bool(rep)})

name = os.environ.get("NW_OUT", "replay72_exc" if exc else "replay72")
json.dump(out, open(f"{name}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open(f"{name}.txt", "w", encoding="utf-8") as f:
    for o in out:
        f.write(f"{o['verdict']:7} {o['first'][5:16]} o{o['outlets']} s{o['score']:.0f} {'+'.join(o['src'])[:9]:9} {'P' if o['posted'] else ' '} "
                f"{o['title'][:100]}\n        -> {(o['why'] or o['reason'])[:150]}\n")
    pub = [o for o in out if o["verdict"] == "PUBLISH"]
    f.write(f"\n{len(out)} candidates: {len(pub)} publish ({sum(o['via'] == 'exceptional' for o in pub)} via exceptional), "
            f"{sum(o['repeat'] for o in out)} blocked as repeats, {len(out) - len(pub)} rejected\n")
print(open(f"{name}.txt", encoding="utf-8").read().splitlines()[-1])
