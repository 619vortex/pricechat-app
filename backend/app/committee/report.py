"""committee.report: one committee run as a standalone HTML document (GET /api/committee/runs/{id}/report.html).

Self-contained: inline CSS, no scripts, no external fonts or images, works offline, prints cleanly, light and dark.
Model text is untrusted: every value goes through html.escape; the only links are ref / headline URLs with an http(s)
scheme, opened with rel="noopener noreferrer". No member data: the run JSON is passed through public_run() first.
"""

import html
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

# keys that identify who asked (or where): never in a public run, list, report or track record
PRIVATE_KEYS = frozenset({"requested_by", "requested_by_tg", "requested_by_member", "member_id", "tg_user_id", "chat_id", "message_id",
                          "member", "telegram_user", "requester"})


def public_run(value: Any) -> Any:
    """A deep copy of a run (or any part of one) without member / Telegram identities."""
    if isinstance(value, dict):
        return {k: public_run(v) for k, v in value.items() if k not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [public_run(v) for v in value]
    return value


def e(v: Any) -> str:
    if v is None:
        return "—"
    return html.escape(str(v), quote=True)


def safe_url(u: Any) -> str | None:
    if not isinstance(u, str) or len(u) > 2000:
        return None
    u = u.strip()
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or any(c in u for c in "\r\n\t\0 \"'<>`\\"):
        return None
    return u


def link(u: Any, text: Any) -> str:
    ok = safe_url(u)
    if ok is None:
        return e(text)
    return f'<a href="{html.escape(ok, quote=True)}" rel="noopener noreferrer" target="_blank">{e(text)}</a>'


def num(x: Any) -> str:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return e(x)
    a = abs(x)
    s = f"{x:,.1f}" if a >= 1000 else f"{x:,.4f}" if a >= 1 else f"{x:.6g}"
    return e(s)


def pct(x: Any) -> str:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "—"
    return e(f"{x:+.2f}%")


def when(iso: Any) -> str:
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return e(iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return e(d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


def filename(run: dict[str, Any], created_at: datetime | None = None) -> str:
    sym = re.sub(r"[^A-Za-z0-9]", "", str((run.get("asset") or {}).get("symbol") or "RUN"))[:20] or "RUN"
    tf = re.sub(r"[^A-Za-z0-9]", "", str(run.get("timeframe") or ""))[:4] or "tf"
    at = None
    for raw in (run.get("as_of"), created_at):
        if isinstance(raw, datetime):
            at = raw
        elif raw:
            try:
                at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                at = None
        if at is not None:
            break
    stamp = (at or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M")
    return f"kiwi-committee-{sym.upper()}-{tf}-{stamp}.html"


def _refs_html(ids: Any, known: set[str]) -> str:
    if not isinstance(ids, list) or not ids:
        return ""
    out = []
    for r in ids:
        r = str(r)
        out.append(f'<a class="ref" href="#ref-{e(r)}">{e(r)}</a>' if r in known else f'<span class="ref">{e(r)}</span>')
    return " " + " ".join(out)


def _points(points: Any, known: set[str]) -> str:
    if not isinstance(points, list) or not points:
        return '<p class="muted">No points.</p>'
    li = "".join(f"<li>{e((p or {}).get('text'))}{_refs_html((p or {}).get('refs'), known)}</li>" for p in points if isinstance(p, dict))
    return f"<ul>{li}</ul>"


def _why(run: dict[str, Any]) -> list[str]:
    """The engine's reasons, each said once (shared by both scenarios: one line), as the Telegram Risk Review shows them."""
    from app.committee import telegram_view as tv

    parts = tv.why_parts(run) or [("", tv.reason_groups(run.get("execution") or {})["general"])]
    return [(f"{label[0].upper() + label[1:]}: " if label else "") + "; ".join(rs) for label, rs in parts if rs]


def _list(items: Any, empty: str = "None.") -> str:
    if not isinstance(items, list) or not items:
        return f'<p class="muted">{e(empty)}</p>'
    return "<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>"


def _author(a: Any) -> str:
    return f'<span class="author">written by {e(a or "rules")}</span>'


ACTION_CLASS = {"LONG": "long", "SHORT": "short", "WAIT": "wait"}
EXEC_LABEL = {"APPROVED": "Risk engine: would approve", "REJECTED": "Risk engine: rejected", "NOT_APPLICABLE": "Risk engine: not applicable"}


def render(run: dict[str, Any], outcome_rows: dict[str, Any] | None = None, created_at: datetime | None = None) -> str:
    run = public_run(run)
    ev = run.get("evidence") or {}
    refs = [r for r in (ev.get("refs") or []) if isinstance(r, dict)]
    known = {str(r.get("id")) for r in refs}
    asset = run.get("asset") or {}
    price = run.get("price") or {}
    verdict = run.get("verdict") or {}
    execution = run.get("execution") or {}
    meta = run.get("meta") or {}
    sym = asset.get("symbol") or "?"
    tf = run.get("timeframe") or "?"
    action = str(verdict.get("action") or "WAIT")
    parts: list[str] = []
    add = parts.append

    # ---- header
    add(f"""<header class="top">
<p class="kicker">Kiwi AI Investment Committee · Research report</p>
<h1>{e(sym)} <span class="sub">{e(asset.get('label') or asset.get('venue'))} · {e(tf)}</span></h1>
<div class="hero">
  <div class="verdict v-{ACTION_CLASS.get(action, 'wait')}"><span class="lbl">Verdict</span><b>{e(action)}</b><span>Rating {e(verdict.get('rating'))}</span></div>
  <dl class="facts">
    <div><dt>Price at decision</dt><dd>{num(price.get('value'))} <small>{e(price.get('kind'))} · {e(price.get('source'))}{' · STALE' if price.get('stale') else ''}</small></dd></div>
    <div><dt>Price time</dt><dd>{when(price.get('ts'))}</dd></div>
    <div><dt>Decision time (as of)</dt><dd>{when(run.get('as_of'))}</dd></div>
    <div><dt>Execution</dt><dd>{e(EXEC_LABEL.get(str(execution.get('status')), execution.get('status')))}</dd></div>
  </dl>
</div>
<p class="research"><b>Research only — nothing was traded.</b> This is not your trades or P&amp;L, and not financial advice.</p>
<p class="summary">{e(verdict.get('summary'))}</p>
</header>""")

    # ---- analysts
    analysts = run.get("analysts") or {}
    sections = ev.get("sections") or {}
    cards = []
    for key, title in (("technical", "Technical"), ("fundamental", "Fundamental"), ("news", "News"), ("sentiment", "Sentiment")):
        a = analysts.get(key) or {}
        sec = sections.get(key) or {}
        extra = ""
        if key == "news":
            items = [i for i in (sec.get("items") or []) if isinstance(i, dict)][:10]
            if items:
                extra = "<h4>Headlines read</h4><ul>" + "".join(
                    f"<li>{link(i.get('url'), i.get('title'))} <small>{e(i.get('source'))} · {when(i.get('published'))}</small>"
                    f"{_refs_html([i['ref']] if i.get('ref') else [], known)}</li>" for i in items) + "</ul>"
        cards.append(f"""<article class="card">
<h3>{title} <span class="lean l-{e(a.get('lean') or 'unknown')}">{e(a.get('lean') or 'unknown')}</span> <span class="conf">confidence {e(a.get('confidence'))}</span></h3>
<p>{e(a.get('summary'))}</p>
{_points(a.get('points'), known)}
{extra}
<h4>Limitations</h4>{_list(a.get('limitations'), 'None reported.')}
<p class="data-status">Data: {e(sec.get('status') or 'unknown')}</p>
{_author(a.get('author'))}
</article>""")
    add('<section><h2>Analysts</h2><div class="grid">' + "".join(cards) + "</div></section>")

    # ---- debate
    deb = run.get("debate") or {}
    side_html = []
    for key, title in (("bull", "Bull case"), ("bear", "Bear case")):
        s = deb.get(key) or {}
        side_html.append(f"""<article class="card {key}">
<h3>{title}</h3><p>{e(s.get('thesis'))}</p>{_points(s.get('points'), known)}
<h4>Rebuttal</h4><p>{e(s.get('rebuttal') or '—')}</p>{_author(s.get('author'))}</article>""")
    mgr = deb.get("manager") or {}
    add(f"""<section><h2>Bull vs bear</h2><div class="grid two">{''.join(side_html)}</div>
<h4>Missing information</h4>{_list(deb.get('missing_info'), 'Nothing flagged.')}
<article class="card"><h3>Research manager · rating {e(mgr.get('rating'))}</h3><p>{e(mgr.get('summary'))}</p>{_author(mgr.get('author'))}</article>
</section>""")

    # ---- trader
    tr = run.get("trader") or {}
    plan = tr.get("plan") if isinstance(tr.get("plan"), dict) else None
    plan_html = '<p class="muted">No trade plan: the trader proposes no position.</p>'
    if plan:
        ez = plan.get("entry_zone") or []
        plan_html = (f"<dl class='kv'><dt>Entry zone</dt><dd>{' – '.join(num(x) for x in ez) or '—'}</dd><dt>Stop</dt><dd>{num(plan.get('stop'))}</dd>"
                     f"<dt>Targets</dt><dd>{', '.join(num(x) for x in plan.get('targets') or []) or '—'}</dd></dl>"
                     f"<p class='deriv'>{e(plan.get('derivation'))}</p>")
    add(f"""<section><h2>Trader proposal</h2><article class="card"><h3>{e(tr.get('action'))}</h3><p>{e(tr.get('rationale'))}</p>{plan_html}{_author(tr.get('author'))}</article></section>""")

    # ---- risk committee
    rk = run.get("risk_committee") or {}
    voices = []
    for key in ("aggressive", "neutral", "conservative"):
        v = rk.get(key) or {}
        voices.append(f"<article class='card'><h3>{key.title()} <span class='stance'>{e(v.get('stance'))}</span></h3>{_list(v.get('concerns'), 'No concerns.')}{_author(v.get('author'))}</article>")
    add(f"<section><h2>Risk committee</h2><div class='grid three'>{''.join(voices)}</div><p>{e(rk.get('summary'))}</p></section>")

    # ---- execution checks
    checks = [c for c in (execution.get("checks") or []) if isinstance(c, dict)]
    rows = "".join(f"<tr><td>{e(c.get('name'))}</td><td class='{'ok' if c.get('ok') else 'bad'}'>{'pass' if c.get('ok') else 'fail'}</td>"
                   f"<td>{e(c.get('value'))}</td><td>{e(c.get('limit'))}</td></tr>" for c in checks)
    table = (f"<div class='scroll'><table><thead><tr><th>Check</th><th>Result</th><th>Value</th><th>Limit</th></tr></thead><tbody>{rows}</tbody></table></div>"
             if checks else '<p class="muted">No checks ran (the risk engine only evaluates Hyperliquid perpetuals).</p>')
    add(f"""<section><h2>Kiwi risk engine</h2>
<p>Status: <b>{e(execution.get('status'))}</b> · engine {e(execution.get('engine'))}. The AI cannot override the risk engine; it is evaluated on a hypothetical reference account.</p>
<h4>Reasons</h4>{_list(_why(run), 'None.')}{table}</section>""")

    # ---- scenarios
    scen = verdict.get("scenarios") or {}
    sc_html = []
    for key, title in (("long", "Potential LONG"), ("short", "Potential SHORT")):
        s = scen.get(key)
        if not isinstance(s, dict):
            sc_html.append(f"<article class='card'><h3>{title}</h3><p class='muted'>No {key} scenario could be derived from the evidence.</p></article>")
            continue
        ez = s.get("entry_zone") or []
        sc_html.append(f"""<article class="card {key}"><h3>{title}</h3>
<dl class="kv"><dt>Trigger</dt><dd>{num(s.get('trigger'))}</dd><dt>Entry zone</dt><dd>{' – '.join(num(x) for x in ez) or '—'}</dd>
<dt>Invalidation</dt><dd>{num(s.get('invalidation'))}</dd><dt>Targets</dt><dd>{', '.join(num(x) for x in s.get('targets') or []) or '—'}</dd></dl>
<h4>How it was derived</h4>
<p class="deriv"><b>Trigger:</b> {e(s.get('trigger_rule'))}</p>
<p class="deriv"><b>Invalidation:</b> {e(s.get('invalidation_rule'))}</p>
<p class="deriv"><b>Targets:</b> {e(s.get('target_rule'))}</p>
<p class="deriv"><b>Full:</b> {e(s.get('derivation'))}</p></article>""")
    add(f"<section><h2>Scenarios</h2><p class='muted'>Levels come from the evidence (pivots, ATR multiples); none is invented.</p><div class='grid two'>{''.join(sc_html)}</div>"
        f"<h3>What changes the view</h3>{_list(verdict.get('what_changes_view'), 'Nothing listed.')}</section>")

    # ---- subsequent performance
    out_rows = outcome_rows or {}
    if out_rows:
        order = ["1h", "4h", "24h", "7d"]
        body = "".join(
            f"<tr><td>{e(h)}</td><td>{num(o.get('price_from'))}</td><td>{num(o.get('price_to'))}</td><td>{pct(o.get('ret_pct'))}</td>"
            f"<td>{'no position' if o.get('direction_ok') is None else 'right' if o.get('direction_ok') else 'wrong'}</td>"
            f"<td>{pct(o.get('mfe_pct'))}</td><td>{pct(o.get('mae_pct'))}</td><td>{e(o.get('source'))}</td></tr>"
            for h, o in sorted(out_rows.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 9))
        perf = (f"<div class='scroll'><table><thead><tr><th>Horizon</th><th>From (first close after)</th><th>To</th><th>Move</th><th>Direction</th>"
                f"<th>Best</th><th>Worst</th><th>Source</th></tr></thead><tbody>{body}</tbody></table></div>"
                "<p class='muted'>Direction only, before fees, slippage and funding. WAIT is no position: its row shows the move that followed.</p>")
    else:
        perf = "<p class='muted'>Not resolved yet (or this market is not scored: only Hyperliquid perpetuals are).</p>"
    add(f"<section><h2>Subsequent performance</h2>{perf}</section>")

    # ---- sources
    src = "".join(f"<li id='ref-{e(r.get('id'))}'><b>{e(r.get('id'))}</b> {link(r.get('url'), r.get('source'))}"
                  f" <small>data time {when(r.get('ts'))} · fetched {when(r.get('fetched_at'))}</small></li>" for r in refs)
    add(f"<section><h2>Sources</h2>{'<ol class=src>' + src + '</ol>' if src else '<p class=muted>No sources recorded.</p>'}</section>")

    # ---- meta
    models = meta.get("models") or {}
    degraded = list(meta.get("degraded") or [])  # already includes the evidence's own notes ("evidence: ...")
    add(f"""<section class="meta"><h2>How this report was made</h2><dl class="kv">
<dt>Backend</dt><dd>{e(meta.get('backend'))}</dd>
<dt>Models</dt><dd>fast {e(models.get('fast'))} · deep {e(models.get('deep'))} · synthesis {e(models.get('synthesis'))}</dd>
<dt>Calls / tokens</dt><dd>{e(meta.get('calls'))} calls · {e(meta.get('tokens_in'))} in · {e(meta.get('tokens_out'))} out</dd>
<dt>Cost</dt><dd>{e(f"${meta.get('cost_usd'):.4f}" if isinstance(meta.get('cost_usd'), (int, float)) else meta.get('cost_usd'))}</dd>
<dt>Duration</dt><dd>{e(meta.get('duration_s'))} s{' (cached run)' if meta.get('cached') else ''}</dd>
<dt>Framework</dt><dd>{e(meta.get('framework'))} · prompts {e(meta.get('prompt_version'))}</dd>
<dt>Run id</dt><dd>{e(run.get('id'))}</dd>
</dl><h4>Degraded / fallbacks</h4>{_list(degraded, 'None: every role answered normally.')}</section>""")

    add("""<footer><p><b>Disclaimer.</b> This report is automated research produced by a committee of AI and rule-based agents from public market
data. It is not financial advice, not a recommendation to buy or sell, and not a record of any trade: nothing was traded. Crypto is volatile;
levels and scenarios can fail. Past outcomes do not predict future ones. Do your own research.</p>
<p class="muted">Price Chat · Kiwi AI Investment Committee</p></footer>""")

    title = f"Kiwi Committee · {sym} {tf} · {action}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>{e(title)}</title><style>{CSS}</style></head>
<body><main>{''.join(parts)}</main></body></html>"""


CSS = """
:root{--bg:#f7f8f4;--card:#fff;--ink:#17201a;--muted:#5d6a61;--line:#dfe4dc;--kiwi:#3f8f2f;--long:#1b8a4b;--short:#c2343f;--wait:#8a6d00;
--chip:#eef3ea;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#0f1411;--card:#171e19;--ink:#e6ece7;--muted:#9aa89e;--line:#2a342d;--kiwi:#7ccf5a;
--long:#37d07c;--short:#ff6b75;--wait:#e7c14a;--chip:#1f2a22}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:28px;margin:4px 0 12px}h1 .sub{font-size:16px;font-weight:500;color:var(--muted)}
h2{font-size:20px;margin:32px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--kiwi)}
h3{font-size:16px;margin:0 0 8px}h4{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:14px 0 4px}
.kicker{margin:0;color:var(--kiwi);font-weight:700;font-size:13px;text-transform:uppercase;letter-spacing:.06em}
.hero{display:flex;flex-wrap:wrap;gap:16px;align-items:stretch}
.verdict{border-radius:12px;padding:14px 20px;display:flex;flex-direction:column;min-width:160px;color:#fff}
.verdict .lbl{font-size:12px;text-transform:uppercase;opacity:.85}.verdict b{font-size:30px;line-height:1.1}
.v-long{background:var(--long)}.v-short{background:var(--short)}.v-wait{background:var(--wait);color:#1b1600}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px 16px;margin:0;flex:1}
.facts dt{font-size:12px;color:var(--muted)}.facts dd{margin:0;font-weight:600}
.research{background:var(--chip);border-left:4px solid var(--kiwi);padding:10px 12px;border-radius:6px}
.summary{font-size:16px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;break-inside:avoid}
.card.bull,.card.long{border-top:4px solid var(--long)}.card.bear,.card.short{border-top:4px solid var(--short)}
.lean,.stance,.conf{font-size:12px;font-weight:600;padding:2px 8px;border-radius:99px;background:var(--chip);color:var(--muted)}
.l-bullish{color:var(--long)}.l-bearish{color:var(--short)}
.author,.data-status{display:block;font-size:12px;color:var(--muted);margin-top:8px}
.ref{font-size:11px;font-weight:700;color:var(--kiwi);text-decoration:none;border:1px solid var(--line);border-radius:4px;padding:0 4px}
ul,ol{padding-left:20px;margin:6px 0}li{margin:3px 0}
.muted{color:var(--muted)}small{color:var(--muted)}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:6px 0}.kv dt{color:var(--muted)}.kv dd{margin:0;font-weight:600}
.deriv{font-size:13px;color:var(--muted);word-break:break-word;margin:4px 0}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;background:var(--card)}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--chip)}
td.ok{color:var(--long);font-weight:700}td.bad{color:var(--short);font-weight:700}
a{color:var(--kiwi)}.src li{word-break:break-word}
footer{margin-top:36px;padding-top:12px;border-top:1px solid var(--line);font-size:13px}
@media print{:root{--bg:#fff;--card:#fff;--ink:#000;--muted:#444;--line:#bbb;--chip:#f1f1f1}
body{font-size:11pt}main{max-width:none;padding:0}h2{break-after:avoid}.card,tr{break-inside:avoid}
a{color:#000}.src a::after{content:" (" attr(href) ")";font-size:9pt;color:#444}.verdict{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""
