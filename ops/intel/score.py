"""Score benchmark runs against eval_v1 labels.  python ops/intel/score.py EVAL.json RUN1.jsonl [RUN2.jsonl ...] [--detail]

Positive = tier M or S (newsworthy enough for an automatic slot or breaking post). Reported per engine:
precision / recall / F1 on newsworthy; major recall (missed majors listed); false positives on NOISE items (junk that would publish)
and on NOTABLE items (routine stories promoted); exact tier accuracy; schema failures; latency p50/p95; tokens; cost.
"""

from __future__ import annotations

import json
import statistics
import sys


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, round(p / 100 * (len(xs) - 1))))
    return xs[k]


def score(items: dict, path: str) -> dict:
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    rows = [r for r in rows if r["id"] in items]
    tp = fp = fn = tn = 0
    fp_noise, fp_notable, missed_major, missed_sig, tier_ok, fails, ms, tin, tout, usd = [], [], [], [], 0, 0, [], 0, 0, 0.0
    tiers = {"M": "major", "S": "significant", "N": "notable", "X": "noise"}
    for r in rows:
        it = items[r["id"]]
        pos = it["tier"] in ("M", "S")
        pred = r["decision"]["newsworthy"]
        tp += pos and pred
        fn += pos and not pred
        fp += (not pos) and pred
        tn += (not pos) and not pred
        if pred and it["tier"] == "X":
            fp_noise.append(it["title"][:80])
        if pred and it["tier"] == "N":
            fp_notable.append(it["title"][:80])
        if it["tier"] == "M" and not r["decision"]["newsworthy"]:
            missed_major.append(it["title"][:80])
        if it["tier"] == "S" and not pred:
            missed_sig.append(it["title"][:80])
        tier_ok += r["decision"]["tier"] == tiers[it["tier"]]
        fails += not r.get("ok", True)
        if r.get("ms") is not None:
            ms.append(r["ms"])
        tin += r.get("in_tok", 0)
        tout += r.get("out_tok", 0)
        usd += r.get("usd", 0.0)
    # independent cross-check: the 64 replay-fixture stories carry the human editor's label (publish / covered / went_out = it was news,
    # stay_out = it was not). These labels predate the Phase 0 tiers and were not written by any model.
    etp = efp = efn = 0
    for r in rows:
        lab = items[r["id"]].get("editor_label")
        if not lab:
            continue
        pos, pred = lab != "stay_out", r["decision"]["newsworthy"]
        etp += pos and pred
        efp += (not pos) and pred
        efn += pos and not pred
    n = len(rows)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    warm = sorted(ms)[1:] if len(ms) > 1 else ms  # drop the cold model load
    majors = sum(1 for i in items.values() if i["tier"] == "M")
    return {"engine": rows[0]["engine"] if rows else path, "n": n, "precision": prec, "recall": rec,
            "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0, "major_recall": 1 - len(missed_major) / majors if majors else 0,
            "fp_noise": fp_noise, "fp_notable": fp_notable, "missed_major": missed_major, "missed_sig": missed_sig,
            "tier_acc": tier_ok / n if n else 0, "schema_fail": fails, "p50_ms": pct(warm, 50), "p95_ms": pct(warm, 95),
            "mean_ms": statistics.mean(warm) if warm else 0, "in_tok": tin, "out_tok": tout, "usd": usd,
            "usd_per_item": usd / n if n else 0,
            "ed_prec": etp / (etp + efp) if etp + efp else 0.0, "ed_rec": etp / (etp + efn) if etp + efn else 0.0, "ed_n": etp + efp + efn}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    detail = "--detail" in sys.argv
    items = {i["id"]: i for i in json.load(open(args[0], encoding="utf-8"))["items"]}
    print(f"{'engine':34} {'n':>4} {'prec':>5} {'rec':>5} {'F1':>5} {'major':>5} {'FPnoise':>7} {'FPnotbl':>7} {'tier':>5} "
          f"{'fail':>4} {'p50s':>6} {'p95s':>6} {'$/item':>8} {'$total':>7} {'edPrec':>6} {'edRec':>5}")
    for path in args[1:]:
        s = score(items, path)
        print(f"{s['engine'][:34]:34} {s['n']:>4} {s['precision']:.2f}  {s['recall']:.2f}  {s['f1']:.2f}  {s['major_recall']:.2f}  "
              f"{len(s['fp_noise']):>7} {len(s['fp_notable']):>7} {s['tier_acc']:.2f}  {s['schema_fail']:>4} {s['p50_ms'] / 1000:6.2f} "
              f"{s['p95_ms'] / 1000:6.2f} {s['usd_per_item']:8.5f} {s['usd']:7.3f} {s['ed_prec']:6.2f} {s['ed_rec']:5.2f}")
        if detail:
            for k in ("missed_major", "fp_noise", "fp_notable", "missed_sig"):
                print(f"   {k} ({len(s[k])}):")
                for t in s[k]:
                    print(f"      - {t}")


if __name__ == "__main__":
    main()
