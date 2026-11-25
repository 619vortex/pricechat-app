"""Run the Growth Engine detectors over a Market Memory database and dump the top findings (text, numbers, calculation, chart) as JSON.
Read-only: it writes no drafts and calls no model. Example (a scratch SQLite filled by `python -m app.growth.backfill`):

    cd backend
    set DATABASE_URL=sqlite:///C:/path/to/scratch.db
    python ../ops/growth/dump_findings.py --out ../ops/growth/sample_findings.json --top 20
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args(argv)

    from app.db.session import SessionLocal
    from app.growth import detectors, drafts, gate, writer
    from app.growth.memory import Memory

    with SessionLocal() as db:
        mem = Memory.from_db(db)
        as_of = detectors.default_as_of(mem)
        found, errors = detectors.run_all(mem, as_of)
    publishable = [f for f in found if f.publishable]
    top = drafts.select_top(publishable, per_format=6, max_total=a.top)
    items = []
    for f in top:
        text, variant = writer.template_text(f)
        items.append({"score": f.score, "format": f.format, "detector": f.detector, "headline": f.headline, "text": text,
                      "chars": len(text or ""), "template_variant": variant, "all_variants": [f.text(i) for i in range(len(f.templates))],
                      "gate_problems": gate.problems(text or "", f), "finding": f.to_dict()})
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": as_of.isoformat() if as_of else None,
           "note": "Real public data (Hyperliquid info API, alternative.me Fear & Greed), computed locally; drafts for review, not posts. "
                   "Template wording (no model). Only licence-cleared sources are used.",
           "detector_errors": errors, "findings_total": len(found), "publishable_total": len(publishable),
           "by_detector": {k: sum(1 for f in found if f.detector == k) for k in detectors.DETECTORS}, "top": items}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    for i in items:
        print(f"{i['score']:6.1f} [{i['format']}/{i['detector']}] {i['headline']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
