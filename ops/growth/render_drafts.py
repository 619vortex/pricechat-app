"""Generate Originals drafts from a Market Memory database and render every draft's graphic through the SAME code path as
GET /api/originals/drafts/{id}/image.png (app.api.routes.originals.image -> app.growth.graphics.render_graphic), in both formats, plus each
"Other chart" variant. Writes the PNGs and a review gallery (graphic, post text with its character count, sources, calculation, score,
format) to ops/growth/drafts_preview/.

Local only: it writes drafts into the database it is pointed at (use a scratch copy), calls no model (template wording) and posts nothing.

    cd backend
    set DATABASE_URL=sqlite:///C:/path/to/scratch-copy.db
    python ../ops/growth/render_drafts.py
"""

from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "backend"))

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Originals draft preview</title>
<style>
:root{{--bg:#0b0d11;--card:#12151b;--line:#232833;--text:#eef0f3;--muted:#8d95a3;--ok:#1ed882;--bad:#ff5252;--hi:#ffba2e}}
@media (prefers-color-scheme: light){{:root:not([data-theme="dark"]){{--bg:#f6f4ef;--card:#fff;--line:#e2ddd2;--text:#14161b;--muted:#646b76}}}}
.banner{{background:#0f2a1c;color:#9ff0c4;border-bottom:1px solid #1d5a3a;padding:10px 16px;text-align:center;font-weight:700;font-size:14px}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
header{{max-width:1180px;margin:0 auto;padding:40px 16px 8px}} h1{{margin:0 0 6px;font-size:28px;letter-spacing:-.01em}}
header p{{color:var(--muted);margin:4px 0;max-width:820px}}
main{{max-width:1180px;margin:0 auto;padding:8px 16px 64px;display:grid;gap:28px}}
article{{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}}
.head{{display:flex;gap:12px;align-items:baseline;padding:16px 20px;border-bottom:1px solid var(--line);flex-wrap:wrap}}
.n{{font-weight:800;color:var(--hi)}} .head h2{{margin:0;font-size:18px}} .det{{color:var(--muted);font-size:13px;margin-left:auto}}
.imgs{{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(0,1fr);gap:12px;padding:16px 20px 0;align-items:start}}
.imgs img{{width:100%;height:auto;border-radius:8px;border:1px solid var(--line);display:block}}
.alt{{padding:12px 20px 0}} .alt h3{{margin-bottom:8px}} .alt .imgs{{padding:0}}
.body{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:20px;padding:16px 20px 20px}}
@media (max-width:760px){{.body,.imgs{{grid-template-columns:1fr}}}}
h3{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 6px}}
.post{{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0;font:inherit}}
.count{{font-size:13px;color:var(--muted);margin-top:6px}} .count b{{color:var(--ok)}} .count b.over{{color:var(--bad)}}
ul{{margin:0;padding-left:18px}} li{{margin:2px 0;overflow-wrap:anywhere}} .src{{color:var(--muted);font-size:13px;margin-top:10px}}
.meta{{color:var(--muted);font-size:12px;margin-top:10px}}
</style></head><body>
<div class="banner">Drafts for review: nothing here has been posted. Licence-cleared sources only (Hyperliquid, alternative.me)</div>
<header><h1>Originals: {n} drafts from the approval queue</h1>
<p>Generated {at} by <code>ops/growth/render_drafts.py</code> from a local Market Memory copy (as of {as_of}): the G1 detectors, the
figures gate and the template writer (no model). Each graphic is the PNG that <code>GET /api/originals/drafts/{{id}}/image.png</code>
returns (X 1600&times;900 and portrait 1080&times;1350), drawn by <code>app.growth.render</code> from the spec that
<code>app.growth.graphics.to_spec</code> builds from the draft's chart and its Finding's numbers.</p>
<p>Below each draft: the post text with its character count, the sources, the calculation with its inputs, score and format, and the
"Other chart" variant(s) Regenerate offers.</p></header>
<main>{cards}</main></body></html>"""


def card(i: int, v: dict, imgs: list[str], alts: list[tuple[str, list[str]]], meta: dict) -> str:
    text = v["text"]
    srcs = "; ".join(f"{s['label']} ({s['attribution']})" if s.get("attribution") else s["label"] for s in v["sources"])
    calc = [c.strip() for c in v["calculation"].split("; ") if c.strip()]
    im = "".join(f'<img src="{p}" alt="draft {v["id"]} graphic" loading="lazy">' for p in imgs)
    alt_html = "".join(f'<div class="alt"><h3>Other chart: {html.escape(k)}</h3><div class="imgs">'
                       + "".join(f'<img src="{p}" alt="draft {v["id"]} {html.escape(k)}" loading="lazy">' for p in ps) + "</div></div>"
                       for k, ps in alts)
    n = len(text)
    return (f'<article id="d{v["id"]}"><div class="head"><span class="n">{i:02d}</span><h2>{html.escape(v["headline"])}</h2>'
            f'<span class="det">format: {html.escape(v["format"])} · detector: {html.escape(v["detector"])} · chart: {html.escape(v["chart"]["kind"])}'
            f' · score {v["score"]:.1f}</span></div><div class="imgs">{im}</div>{alt_html}<div class="body"><div><h3>Post text</h3>'
            f'<pre class="post">{html.escape(text)}</pre><div class="count"><b class="{"over" if n > 280 else ""}">{n}</b> / 280 characters'
            f' · writer: {html.escape(v["writer"])} · gate: {"pass" if not v["gate_problems"] else html.escape("; ".join(v["gate_problems"]))}</div>'
            f'<div class="src"><b>Sources:</b> {html.escape(srcs)}</div>'
            f'<div class="meta">render: {html.escape(str(meta.get("template")))} · {html.escape(str(meta.get("version")))} · spec {html.escape(str(meta.get("spec_version")))}'
            f' · label overlaps {meta.get("label_overlaps")} · smallest text on a phone {meta.get("phone_min_text_px")} px</div></div>'
            f'<div><h3>Calculation</h3><ul>{"".join(f"<li>{html.escape(c)}</li>" for c in calc)}</ul></div></div></article>')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "drafts_preview"))
    ap.add_argument("--no-generate", action="store_true", help="render the drafts already in the database")
    a = ap.parse_args(argv)

    from app.api.routes import originals
    from app.config.settings import get_settings
    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    from app.growth import drafts
    from app.models.market_memory import GrDraft

    Base.metadata.create_all(engine, tables=[GrDraft.__table__])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("draft-*.png"):
        old.unlink()
    cards = []
    with SessionLocal() as db:
        res = {"as_of": ""}
        if not a.no_generate:
            res = drafts.generate(db, settings=get_settings(), use_writer=False)
            print(f"generate: found {res['found']}, drafted {res['drafted']}, errors {res['errors']}")
        ids = res.get("draft_ids") or [d.id for d in db.query(GrDraft).order_by(GrDraft.id)]
        for i, did in enumerate(ids, 1):
            d = db.get(GrDraft, did)
            assert d is not None
            imgs, alts = [], []
            for fmt in ("x", "portrait"):
                r = originals.image(did, db, fmt)  # the endpoint itself
                name = f"draft-{i:02d}-{d.detector}-{fmt}.png"
                (out / name).write_bytes(r.body)
                imgs.append(name)
            meta = dict((d.image_cache or {}).get("x") or {})
            v = drafts.view(d)
            for kind in v["chart_kinds"][1:]:
                chart0 = dict(d.chart)
                d.chart = {k: x for k, x in chart0.items() if k != "variant_of"} | {"kind": kind, "variant_of": v["chart_kinds"][0]}
                ps = []
                for fmt in ("x", "portrait"):
                    r = originals.image(did, db, fmt)
                    name = f"draft-{i:02d}-{d.detector}-alt-{kind}-{fmt}.png"
                    (out / name).write_bytes(r.body)
                    ps.append(name)
                alts.append((kind, ps))
                d.chart, d.image_cache = chart0, {}
                db.commit()
                originals.image(did, db, "x")
                originals.image(did, db, "portrait")
            cards.append(card(i, v, imgs, alts, meta))
            print(f"{i:02d} #{did} {d.detector:16} {d.chart['kind']:18} chars={len(d.text)} overlaps={meta.get('label_overlaps')} "
                  f"phone_min={meta.get('phone_min_text_px')} alts={[k for k, _ in alts]}")
    (out / "index.html").write_text(PAGE.format(n=len(cards), at=datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
                                                as_of=res.get("as_of", ""), cards="\n".join(cards)), encoding="utf-8")
    print("wrote", out / "index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
