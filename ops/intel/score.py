"""Score benchmark runs against an eval set's labels.

    python ops/intel/score.py EVAL.json RUN1.jsonl [RUN2.jsonl ...] [--split dev|test] [--detail] [--misses]
    python ops/intel/score.py EVAL.json --second PRIMARY.jsonl SECOND.jsonl RULES.jsonl [--split test]   (second-opinion policies)

Positive = tier M or S (newsworthy enough for an automatic slot or breaking post). Reported per engine:
precision / recall / F1 on newsworthy; major recall = majors that would publish (missed majors listed); "major-as-major" recall and
precision = items the decision marks MAJOR (breaking) vs the M label; false positives on NOISE items (junk that would publish) and on
NOTABLE items (routine stories promoted); exact tier accuracy; schema failures; latency p50/p95; tokens; cost.
--split restricts to the eval's held-out "test" items or the "dev" items (eval_v2 and later; eval_v1 has no split).
--misses prints, for every missed major, the model's materiality / flags / consequence / reason.
--second scores every escalation policy x combine mode of backend/app/intel/second_opinion.py (the primary's analysis decides which
items are escalated; the second run supplies the second analysis for those only; latency = the second model on escalated items).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

TIERS = {"M": "major", "S": "significant", "N": "notable", "X": "noise"}


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, round(p / 100 * (len(xs) - 1))))
    return xs[k]


def load_rows(items: dict, path: str) -> list[dict]:
    rows = {}
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if r["id"] in items:
            rows[r["id"]] = r  # last line wins (a re-run of an id replaces the earlier one)
    return list(rows.values())


def metrics(items: dict, decisions: dict[str, dict]) -> dict:
    """Classification metrics of {id: decision} against the labels of `items` (ids missing from decisions count as not newsworthy)."""
    tp = fp = fn = 0
    mtp = mfp = mfn = 0
    fp_noise, fp_notable, missed_major, missed_sig, tier_ok = [], [], [], [], 0
    for iid, it in items.items():
        d = decisions.get(iid) or {"tier": "noise", "newsworthy": False, "major": False}
        pos, pred = it["tier"] in ("M", "S"), bool(d["newsworthy"])
        tp += pos and pred
        fn += pos and not pred
        fp += (not pos) and pred
        mtp += it["tier"] == "M" and bool(d["major"])
        mfn += it["tier"] == "M" and not d["major"]
        mfp += it["tier"] != "M" and bool(d["major"])
        if pred and it["tier"] == "X":
            fp_noise.append(it["title"][:80])
        if pred and it["tier"] == "N":
            fp_notable.append(it["title"][:80])
        if it["tier"] == "M" and not pred:
            missed_major.append(iid)
        if it["tier"] == "S" and not pred:
            missed_sig.append(it["title"][:80])
        tier_ok += d["tier"] == TIERS[it["tier"]]
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    majors = sum(1 for i in items.values() if i["tier"] == "M")
    return {"n": len(items), "precision": prec, "recall": rec, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
            "major_recall": 1 - len(missed_major) / majors if majors else 0.0, "majors": majors,
            "mm_recall": mtp / (mtp + mfn) if mtp + mfn else 0.0, "mm_precision": mtp / (mtp + mfp) if mtp + mfp else 0.0,
            "mm_fp": mfp, "fp_noise": fp_noise, "fp_notable": fp_notable, "missed_major": missed_major, "missed_sig": missed_sig,
            "tier_acc": tier_ok / len(items) if items else 0.0}


def score(items: dict, path: str) -> dict:
    rows = load_rows(items, path)
    scored = {r["id"]: items[r["id"]] for r in rows}
    m = metrics(scored, {r["id"]: r["decision"] for r in rows})
    ms = [r["ms"] for r in rows if r.get("ms") is not None]
    warm = sorted(ms)[1:] if len(ms) > 1 else ms  # drop the cold model load
    # independent cross-check: replay-fixture stories carry the human editor's label (publish / covered / went_out = it was news,
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
    usd = sum(r.get("usd", 0.0) for r in rows)
    return {**m, "engine": rows[0]["engine"] if rows else path, "rows": {r["id"]: r for r in rows},
            "schema_fail": sum(not r.get("ok", True) for r in rows), "p50_ms": pct(warm, 50), "p95_ms": pct(warm, 95),
            "mean_ms": statistics.mean(warm) if warm else 0, "in_tok": sum(r.get("in_tok", 0) for r in rows),
            "out_tok": sum(r.get("out_tok", 0) for r in rows), "usd": usd, "usd_per_item": usd / n if n else 0,
            "ed_prec": etp / (etp + efp) if etp + efp else 0.0, "ed_rec": etp / (etp + efn) if etp + efn else 0.0, "ed_n": etp + efp + efn}


def print_misses(items: dict, s: dict) -> None:
    for iid in s["missed_major"]:
        r = s["rows"].get(iid, {})
        a = r.get("analysis") or {}
        c = a.get("consequence") or {}
        it = items[iid]
        print(f"      - {iid} [{it.get('split', '')}] {it['title'][:110]}")
        print(f"        summary={'yes' if it.get('summary') else 'NO'} materiality={a.get('materiality')} spec={a.get('is_speculative')} "
              f"opinion={a.get('is_opinion_or_feature')} consequence={c.get('kind')}/{c.get('stated_in_text')}/{c.get('magnitude')!r}")
        print(f"        reason: {(a.get('reason') or r.get('why') or r.get('error') or '')[:260]}")


def second_opinion_table(items: dict, primary: str, second: str, rules: str) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.intel import second_opinion as so

    prim = {r["id"]: r for r in load_rows(items, primary)}
    sec = {r["id"]: r for r in load_rows(items, second)}
    rul = {r["id"]: r for r in load_rows(items, rules)}
    ids = [i for i in items if i in prim and i in sec]
    sub = {i: items[i] for i in ids}
    print(f"second opinion on {len(ids)} items ({sum(1 for i in sub.values() if i['tier'] == 'M')} majors); primary {prim[ids[0]]['engine']}, "
          f"second {sec[ids[0]]['engine']}")
    print(f"{'policy':24} {'combine':8} {'esc%':>5} {'nEsc':>4} {'MMrec':>5} {'MMprec':>6} {'MMfp':>4} {'majRec':>6} {'prec':>5} {'rec':>5} "
          f"{'F1':>5} {'p50s':>5} {'p95s':>5}")
    for policy in so.POLICIES:
        for mode in (so.COMBINE_MODES if policy != "none" else ("-",)):
            dec, esc_ms = {}, []
            for i in ids:
                pa = prim[i].get("analysis") if prim[i].get("ok") else None
                rd = rul.get(i, {}).get("decision") or {}
                rv = {"ok": rd.get("newsworthy", False), "tier": rd.get("tier", "")}
                if pa is not None and so.should_escalate(pa, rv, policy):
                    sa = sec[i].get("analysis") if sec[i].get("ok") else None
                    esc_ms.append(sec[i].get("ms", 0.0))
                    dec[i] = so.combine(pa, sa, mode)
                else:
                    dec[i] = prim[i]["decision"] if pa is None else so.combine(pa, None)
            m = metrics(sub, dec)
            print(f"{policy:24} {mode:8} {100 * len(esc_ms) / len(ids):5.1f} {len(esc_ms):>4} {m['mm_recall']:.2f}  {m['mm_precision']:.2f}  "
                  f"{m['mm_fp']:>4} {m['major_recall']:.2f}   {m['precision']:.2f}  {m['recall']:.2f}  {m['f1']:.2f}  "
                  f"{pct(esc_ms, 50) / 1000:5.1f} {pct(esc_ms, 95) / 1000:5.1f}")
    m = metrics(sub, {i: sec[i]["decision"] for i in ids})
    print(f"{'(second model alone)':24} {'-':8} {100.0:5.1f} {len(ids):>4} {m['mm_recall']:.2f}  {m['mm_precision']:.2f}  {m['mm_fp']:>4} "
          f"{m['major_recall']:.2f}   {m['precision']:.2f}  {m['recall']:.2f}  {m['f1']:.2f}")


def main() -> None:
    argv = sys.argv[1:]
    split = None
    if "--split" in argv:
        k = argv.index("--split")
        split = argv[k + 1]
        del argv[k:k + 2]
    second = None
    if "--second" in argv:
        k = argv.index("--second")
        second = argv[k + 1:k + 4]
        del argv[k:k + 4]
    args = [a for a in argv if not a.startswith("--")]
    detail, misses = "--detail" in argv, "--misses" in argv
    items = {i["id"]: i for i in json.load(open(args[0], encoding="utf-8"))["items"]}
    if split:
        items = {k: v for k, v in items.items() if v.get("split") == split}
    if second:
        second_opinion_table(items, *second)
        return
    print(f"{'engine':34} {'n':>4} {'prec':>5} {'rec':>5} {'F1':>5} {'major':>5} {'MMrec':>5} {'MMprc':>5} {'FPnoise':>7} {'FPnotbl':>7} "
          f"{'tier':>5} {'fail':>4} {'p50s':>6} {'p95s':>6} {'$/item':>8} {'$total':>7} {'edPrec':>6} {'edRec':>5}")
    for path in args[1:]:
        s = score(items, path)
        print(f"{s['engine'][:34]:34} {s['n']:>4} {s['precision']:.2f}  {s['recall']:.2f}  {s['f1']:.2f}  {s['major_recall']:.2f}  "
              f"{s['mm_recall']:.2f}  {s['mm_precision']:.2f}  {len(s['fp_noise']):>7} {len(s['fp_notable']):>7} {s['tier_acc']:.2f}  "
              f"{s['schema_fail']:>4} {s['p50_ms'] / 1000:6.2f} {s['p95_ms'] / 1000:6.2f} {s['usd_per_item']:8.5f} {s['usd']:7.3f} "
              f"{s['ed_prec']:6.2f} {s['ed_rec']:5.2f}")
        if misses:
            print(f"   missed majors ({len(s['missed_major'])} of {s['majors']}):")
            print_misses(items, s)
        if detail:
            for k in ("fp_noise", "fp_notable", "missed_sig"):
                print(f"   {k} ({len(s[k])}):")
                for t in s[k]:
                    print(f"      - {t}")


if __name__ == "__main__":
    main()
