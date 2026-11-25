"""Re-decide benchmark runs through the shadow gate (app.intel.gate.verdict) of THIS worktree, and attribute every refusal to its rule.

    python ops/intel/gate_replay.py RUNS_DIR [OUT_DIR] [--engines qwen3.8_27b,qwen3.6_27b] [--detail]

RUNS_DIR holds ollama_*.jsonl (one line per eval story, with "analysis"). For each run it prints precision / recall / major recall of
the gated decision, then, per rule, how many positives (eval tier M or S) and negatives (N or X) that rule removed:
- "model:*" rows are the model's own refusals (materiality below significant, speculative, opinion/feature, no consequence kind); the
  gate never overrides those, so they are the model's errors, not the gate's.
- "veto:*" rows are refusals the gate adds on top of a model "yes" (hedge, round-up, feature/explainer, reaction, the lane's own veto).
  A story removed by two vetoes counts under each ("removed by") and once under "first veto".
With OUT_DIR it also writes the gated runs (decision replaced) for ops/intel/score.py.

Replay uses newsworthy.assess() as the rules side (no history, so no repeat / cap / hold: the lane-side vetoes cannot fire here).
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))

from app.intel import gate  # noqa: E402
from app.intel.schema import ItemAnalysis  # noqa: E402
from app.newsdesk import newsworthy  # noqa: E402

EVAL = os.path.join(HERE, "eval_v1.json")


def veto_kind(v: str) -> str:
    if v.startswith("hedged, rumour or forecast wording"):
        m = re.search(r"\((.*?)\)", v)
        return f"veto:hedge ({(m.group(1) if m else '').lower()})"
    if v.startswith(("not one story: a round-up", "not one story: two stories", "not one story: a live page")):
        return "veto:round-up"
    if v.startswith("not one story: a reaction"):
        m = re.search(r"\((.*?)\)", v)
        return f"veto:reaction ({(m.group(1) if m else '').lower()})"
    if v.startswith("not one story: "):
        m = re.search(r"\((.*?)\)\s*$", v)
        return f"veto:feature ({(m.group(1) if m else v[15:60]).lower()})"
    return f"veto:lane ({v[:50]})"


def model_kinds(a: ItemAnalysis) -> list[str]:
    out = []
    if a.materiality not in ("major", "significant"):
        out.append(f"model:materiality={a.materiality}")
    if a.is_speculative:
        out.append("model:speculative")
    if a.is_opinion_or_feature:
        out.append("model:opinion/feature")
    if not out and a.consequence.kind == "none":
        out.append("model:consequence kind none")
    return out


def replay(path: str, items: dict, out_dir: str | None, detail: bool) -> dict:
    tp = fp = fn = 0
    missed_major = []
    removed_by: dict[str, Counter] = defaultdict(Counter)  # kind -> {"pos","neg","M"}
    first: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)
    rows_out = []
    majors = sum(1 for i in items.values() if i["tier"] == "M")
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        it = items.get(r["id"])
        if it is None:
            continue
        pos = it["tier"] in ("M", "S")
        ra = newsworthy.assess(it["title"], it.get("summary") or "")
        a = ItemAnalysis.model_validate(r["analysis"]) if r.get("ok") and r.get("analysis") else None
        v = gate.verdict(a, it["title"], it.get("summary") or "", it.get("outlets") or [], "", ra, model=r["engine"])
        pred = bool(v.newsworthy) if a is not None else False
        tp += pos and pred
        fp += (not pos) and pred
        fn += pos and not pred
        if it["tier"] == "M" and not pred:
            missed_major.append(it["title"][:90])
        if not pred:
            kinds = ["model:schema failure"] if a is None else (model_kinds(a) if not v.consequence_ok else [veto_kind(x) for x in v.hard_veto])
            for k in dict.fromkeys(kinds):
                removed_by[k]["pos" if pos else "neg"] += 1
                removed_by[k]["M"] += it["tier"] == "M"
                if len(examples[k]) < 40:
                    examples[k].append(f"[{it['tier']}] {it['title'][:100]}")
            k0 = kinds[0] if kinds else "?"
            first[k0]["pos" if pos else "neg"] += 1
            first[k0]["M"] += it["tier"] == "M"
        if out_dir:
            r2 = dict(r)
            r2["engine"] = r["engine"] + "+gate"
            r2["decision"] = {"tier": v.tier if a is not None else "noise", "newsworthy": pred, "major": pred and v.tier == "major"}
            r2["veto"] = v.hard_veto
            rows_out.append(r2)
    if out_dir:
        with open(os.path.join(out_dir, os.path.basename(path)), "w", encoding="utf-8") as w:
            for r2 in rows_out:
                w.write(json.dumps(r2, ensure_ascii=False) + "\n")
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    name = os.path.basename(path).removeprefix("ollama_").removesuffix(".jsonl")
    print(f"\n== {name}+gate  precision {prec:.2f}  recall {rec:.2f}  major recall {1 - len(missed_major) / majors:.2f}  "
          f"(tp {tp} fp {fp} fn {fn}; missed majors {len(missed_major)})")
    print(f"   {'rule':58} {'pos':>4} {'(M)':>4} {'neg':>4}   first: {'pos':>4} {'(M)':>4} {'neg':>4}")
    for k in sorted(removed_by, key=lambda k: (not k.startswith("veto"), -removed_by[k]["pos"], k)):
        c, f = removed_by[k], first.get(k, Counter())
        print(f"   {k[:58]:58} {c['pos']:>4} {c['M']:>4} {c['neg']:>4}          {f['pos']:>4} {f['M']:>4} {f['neg']:>4}")
    for t in missed_major:
        print(f"   missed major: {t}")
    if detail:
        for k in sorted(examples):
            if k.startswith("veto"):
                print(f"   -- {k}")
                for e in examples[k]:
                    print(f"        {e}")
    return {"precision": prec, "recall": rec, "major_recall": 1 - len(missed_major) / majors, "removed_by": removed_by}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    detail = "--detail" in sys.argv
    engines = None
    for a in sys.argv[1:]:
        if a.startswith("--engines="):
            engines = a.split("=", 1)[1].split(",")
    runs, out_dir = args[0], (args[1] if len(args) > 1 else None)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    items = {i["id"]: i for i in json.load(open(EVAL, encoding="utf-8"))["items"]}
    for f in sorted(glob.glob(os.path.join(runs, "ollama_*.jsonl"))):
        name = os.path.basename(f).removeprefix("ollama_").removesuffix(".jsonl")
        if engines and name not in engines:
            continue
        replay(f, items, out_dir, detail)


if __name__ == "__main__":
    main()
