"""Telegram views of a committee run (Kiwi AI Investment Committee, stage 2): the compact summary, the bull / bear / debate /
risk views and the full report, as Telegram HTML, plus the inline buttons that switch between them.

Pure functions of the stored run JSON (cm_runs.result): no I/O, no model calls. Every number shown is the run's own (the
evidence pack, its scenarios, the risk engine's checks); prices are only re-formatted by magnitude. Every piece of text is
HTML-escaped (model text included: it is untrusted). No probabilities are ever added here. The compact view never shows the
scenarios' verbose rule strings (they belong to the full report). Limits: compact ≤ COMPACT_MAX characters, every message
≤ 4096 (Telegram's limit), the full report at most two messages.
"""

import math
import re
from datetime import datetime
from typing import Any

COMPACT_MAX = 1300
TG_MAX = 4096
PART_MAX = 3900  # room for the footer the bot adds when it fits
NOTE = "Research only — nothing was traded. Not financial advice."
SECTIONS = ("technical", "fundamental", "news", "sentiment")
VIEWS = ("sum", "full", "bull", "bear", "debate", "risk")
VENUES = {"hyperliquid": "Hyperliquid", "okx": "OKX", "binance": "Binance", "bybit": "Bybit", "dex": "DEX"}


def esc(t: Any) -> str:
    return str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(t: Any, n: int) -> str:
    """At most n characters, cut at a word when possible (before escaping, so no entity is ever cut)."""
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    if len(t) <= n:
        return t
    cut = t[: n - 1]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n * 0.6 else cut).rstrip(" ,;:") + "…"


def first_sentence(t: Any, n: int = 170) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    m = re.search(r"(?<=[a-z0-9)\]%])[.!?](\s|$)", t)
    return clip(t[: m.start() + 1] if m and m.start() > 20 else t, n)


def px(v: Any) -> str:
    """A price by magnitude: 81,851.5 · 261.8 · 2.793 · 0.07948 · 0.00001234 (4 significant digits below 1)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v) or v <= 0:
        return "n/a"
    if v >= 1000:
        return f"{v:,.1f}".removesuffix(".0") if v < 100_000 else f"{v:,.0f}"
    if v >= 100:
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if v >= 1:
        return f"{v:.3f}".rstrip("0").rstrip(".")
    d = min(12, 3 - int(math.floor(math.log10(v))))
    return f"{v:.{d}f}".rstrip("0").rstrip(".")


def hhmm(iso: Any) -> str:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%H:%M UTC")
    except ValueError:
        return ""


def market_label(asset: dict[str, Any]) -> str:
    """'Hyperliquid perp' / 'OKX spot' / 'Binance perp' / 'DEX'."""
    venue = VENUES.get(str(asset.get("venue") or ""), str(asset.get("venue") or "?").capitalize())
    kind = asset.get("market_type")
    return venue if venue == "DEX" else f"{venue} {'perp' if kind == 'perp' else kind or ''}".strip()


def informational(run: dict[str, Any]) -> bool:
    a = run.get("asset") or {}
    return not (a.get("venue") == "hyperliquid" and a.get("market_type") == "perp" and a.get("hl_tradable", True))


def _title(run: dict[str, Any], what: str = "KIWI AI INVESTMENT COMMITTEE") -> str:
    a = run.get("asset") or {}
    tf = str(run.get("timeframe") or "").upper()
    return f"🥝 <b>{esc(what)}</b> · <b>{esc(a.get('symbol') or '?')} {esc(tf)}</b> · {esc(market_label(a))}"


def _price_line(run: dict[str, Any]) -> str:
    p = run.get("price") or {}
    t = hhmm(p.get("ts") or run.get("as_of"))
    line = f"Price {px(p.get('value'))}" + (f" · {t}" if t else "")
    if p.get("stale"):
        line += " · ⚠️ stale"
    if (run.get("meta") or {}).get("cached"):
        line += " · cached run"
    return line


def _sections(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return ((run.get("evidence") or {}).get("sections") or {})


def missing_notes(run: dict[str, Any]) -> list[str]:
    """['data missing: news', 'data stale: technical'] from the evidence's section statuses (the analysts' when no evidence)."""
    secs, an = _sections(run), run.get("analysts") or {}
    missing, stale = [], []
    for n in SECTIONS:
        st = (secs.get(n) or {}).get("status") if secs else None
        if st == "unavailable" or (st is None and (an.get(n) or {}).get("lean") == "unknown"):
            missing.append(n)
        elif st == "stale":
            stale.append(n)
    out = []
    if missing:
        out.append("data missing: " + ", ".join(missing))
    if stale:
        out.append("data stale: " + ", ".join(stale))
    if (run.get("price") or {}).get("stale"):
        out.append("price stale")
    return out


def _leans(run: dict[str, Any]) -> str:
    an, secs = run.get("analysts") or {}, _sections(run)
    words = {"bullish": "Bullish", "bearish": "Bearish", "neutral": "Neutral"}

    def word(n: str) -> str:
        lean = (an.get(n) or {}).get("lean")
        if lean in words:
            return words[lean]
        st = (secs.get(n) or {}).get("status")
        return {"unavailable": "missing", "stale": "stale"}.get(st or "", "no signal" if st else "n/a")

    return " · ".join(f"{n.capitalize()}: {word(n)}" for n in SECTIONS)


def key_risk(run: dict[str, Any]) -> str:
    rk = run.get("risk_committee") or {}
    for voice in ("conservative", "neutral", "aggressive"):
        cs = (rk.get(voice) or {}).get("concerns") or []
        if cs:
            return str(cs[0])
    return str(rk.get("summary") or "no concern raised")


def _scenario_line(run: dict[str, Any], side: str) -> str | None:
    s = ((run.get("verdict") or {}).get("scenarios") or {}).get(side)
    if not s or not s.get("trigger"):
        return None
    tf = esc(run.get("timeframe") or "")
    op = "&gt;" if side == "long" else "&lt;"
    t = [f"{'Long' if side == 'long' else 'Short'} if: {tf} close {op} {px(s['trigger'])}"]
    if s.get("invalidation"):
        t.append(f"stop {px(s['invalidation'])}")
    if s.get("targets"):
        t.append("T " + " / ".join(px(x) for x in s["targets"][:2]))
    return " · ".join(t)


def _plan_line(run: dict[str, Any]) -> str | None:
    p = (run.get("trader") or {}).get("plan")
    if not p:
        return None
    lo, hi = (p.get("entry_zone") or [None, None])[:2]
    entry = px(lo) if lo == hi else f"{px(lo)}–{px(hi)}"
    return f"Plan: entry {entry} · stop {px(p.get('stop'))} · T " + " / ".join(px(x) for x in (p.get("targets") or [])[:2])


def _execution_lines(run: dict[str, Any], n: int = 3, width: int = 110) -> list[str]:
    ex = run.get("execution") or {}
    reasons = [clip(r, width) for r in (ex.get("reasons") or [])[:n]]
    st = ex.get("status")
    if st == "APPROVED":
        return ["✅ Kiwi risk engine: APPROVED on the reference paper account"]
    if st == "REJECTED":
        return ["⛔ No trade approved because: " + esc("; ".join(reasons) or "the risk engine rejected it")]
    return ["No trade approved because: " + esc("; ".join(reasons) or "no trade proposed")]


def compact(run: dict[str, Any]) -> str:
    """The summary every command starts from (Phase 6 format), ≤ COMPACT_MAX characters."""
    a, v = run.get("asset") or {}, run.get("verdict") or {}
    deb = run.get("debate") or {}

    def build(width: int) -> str:
        lines = [_title(run), _price_line(run)]
        if informational(run):
            lines.append(f"Market: {esc(clip(a.get('label') or market_label(a), 60))} · informational only, no execution")
        lines.append(_leans(run))
        lines.append(f"🐂 Bull: {esc(first_sentence((deb.get('bull') or {}).get('thesis'), width))}")
        lines.append(f"🐻 Bear: {esc(first_sentence((deb.get('bear') or {}).get('thesis'), width))}")
        lines.append(f"⚠️ Risk: {esc(clip(key_risk(run), width))}")
        lines.append(f"<b>Verdict: {esc(v.get('action') or 'WAIT')}</b> ({esc(v.get('rating') or 'Hold')})")
        plan = _plan_line(run) if v.get("action") in ("LONG", "SHORT") else None
        if plan:
            lines.append(plan)
        else:
            lines += [x for x in (_scenario_line(run, "long"), _scenario_line(run, "short")) if x]
        lines += _execution_lines(run, 3, width)
        lines += [esc(m) for m in missing_notes(run)]
        lines.append(f"<i>{NOTE}</i>")
        return "\n".join(lines)

    for width in (170, 130, 100, 80, 60):
        text = build(width)
        if len(text) <= COMPACT_MAX:
            return text
    return text[: COMPACT_MAX - 1]  # unreachable in practice (every line is clipped); never splits an entity at 60


def _points(points: list[dict[str, Any]], n: int = 4, width: int = 160) -> list[str]:
    return [f"· {esc(clip(p.get('text'), width))}" + (f" <i>[{esc(', '.join(p.get('refs') or []))}]</i>" if p.get("refs") else "")
            for p in (points or [])[:n] if p.get("text")]


def side_view(run: dict[str, Any], side: str) -> str:
    """Bull Case / Bear Case: the thesis, its supporting facts (with refs), the rebuttal."""
    s = (run.get("debate") or {}).get(side) or {}
    lines = [_title(run, "BULL CASE" if side == "bull" else "BEAR CASE"), _price_line(run), "",
             esc(clip(s.get("thesis") or "No case written.", 700))]
    pts = _points(s.get("points") or [])
    if pts:
        lines += ["", "<b>Supporting facts</b>", *pts]
    if s.get("rebuttal"):
        lines += ["", f"<b>Rebuttal:</b> {esc(clip(s['rebuttal'], 400))}"]
    scen = _scenario_line(run, "long" if side == "bull" else "short")
    if scen:
        lines += ["", scen]
    lines += ["", f"<i>{_author(s)} · {NOTE}</i>"]
    return _fit("\n".join(lines))


def debate_view(run: dict[str, Any]) -> str:
    """/debate: bull vs bear side by side (one message), and the research manager's call."""
    deb = run.get("debate") or {}
    lines = [_title(run, "BULL vs BEAR"), _price_line(run), ""]
    for side, icon in (("bull", "🐂"), ("bear", "🐻")):
        s = deb.get(side) or {}
        lines.append(f"{icon} <b>{side.capitalize()}:</b> {esc(clip(s.get('thesis') or 'no case', 520))}")
        lines += _points(s.get("points") or [], 3, 130)
        if s.get("rebuttal"):
            lines.append(f"↩️ {esc(clip(s['rebuttal'], 260))}")
        lines.append("")
    m = deb.get("manager") or {}
    if m:
        lines.append(f"⚖️ <b>Research manager: {esc(m.get('rating') or '?')}</b> · {esc(clip(m.get('summary'), 300))}")
    v = run.get("verdict") or {}
    lines += [f"<b>Verdict: {esc(v.get('action') or 'WAIT')}</b> ({esc(v.get('rating') or 'Hold')})", "", f"<i>{NOTE}</i>"]
    return _fit("\n".join(lines))


def risk_view(run: dict[str, Any]) -> str:
    """/risk and Risk Review: the three risk voices, then the Kiwi risk engine (status, reasons, the checks that failed and
    the key ones that passed)."""
    rk, ex = run.get("risk_committee") or {}, run.get("execution") or {}
    lines = [_title(run, "RISK REVIEW"), _price_line(run), "", "<b>Risk committee</b>"]
    for voice in ("aggressive", "neutral", "conservative"):
        x = rk.get(voice) or {}
        cs = "; ".join(clip(c, 150) for c in (x.get("concerns") or [])[:3]) or "no concern"
        lines.append(f"· {voice.capitalize()} ({esc(x.get('stance') or '?')}): {esc(cs)}")
    if rk.get("summary"):
        lines.append(esc(clip(rk["summary"], 300)))
    st = ex.get("status") or "NOT_APPLICABLE"
    lines += ["", f"<b>Kiwi risk engine: {esc(st.replace('_', ' '))}</b>"]
    if informational(run):
        lines.append(f"{esc(market_label(run.get('asset') or {}))}: informational only, execution only on Hyperliquid perpetuals")
    lines += [f"· {esc(clip(r, 200))}" for r in (ex.get("reasons") or [])[:5]]
    checks = ex.get("checks") or []
    bad = [c for c in checks if not c.get("ok")]
    keys = ("warn", "sizing", "fees", "spread", "slippage", "exposure", "drawdown", "stop distance", "risk team", "leverage")

    def rank(c: dict[str, Any]) -> int:  # warnings and the substantive checks first, the switches last
        text = f"{c.get('value') or ''} {c.get('name') or ''}".lower()
        return next((i for i, k in enumerate(keys) if k in text), len(keys))

    good = sorted((c for c in checks if c.get("ok")), key=rank)
    if bad:
        lines += ["", "<b>Failed checks</b>", *(f"❌ {esc(clip(c.get('name'), 80))}: {esc(clip(c.get('value'), 140))}" for c in bad[:6])]
    if good:
        lines += ["", f"<b>Passed</b> ({len(good)} checks)", *(f"{'⚠️' if str(c.get('value') or '').startswith('warn') else '✅'} {esc(clip(c.get('name'), 80))}: {esc(clip(c.get('value'), 90))}"
                                                     for c in good[:6])]
    lines += ["", f"<i>{esc(ex.get('note') or 'Research only — nothing was traded.')} The AI cannot override the risk engine. Not financial advice.</i>"]
    return _fit("\n".join(lines))


def _author(x: dict[str, Any]) -> str:
    a = str(x.get("author") or "rules")
    return "Kiwi rules engine" if a == "rules" else f"AI: {esc(a.removeprefix('llm:'))}"


def _fit(text: str, limit: int = PART_MAX) -> str:
    """Whole lines only, ≤ limit (HTML tags never span lines here)."""
    if len(text) <= limit:
        return text
    out, n = [], 0
    for line in text.split("\n"):
        if n + len(line) + 1 > limit - 2:
            break
        out.append(line)
        n += len(line) + 1
    return "\n".join(out) + "\n…"


def full_report(run: dict[str, Any]) -> list[str]:
    """The detailed report: every role's text, the scenarios with their rules, the engine's checks, the run's meta. One or
    two messages of ≤ PART_MAX characters each (whole lines; the second is cut with a note if it still overflows)."""
    a, v = run.get("asset") or {}, run.get("verdict") or {}
    deb, tr, rk, ex, meta = (run.get(k) or {} for k in ("debate", "trader", "risk_committee", "execution", "meta"))
    L = [_title(run, "KIWI AI COMMITTEE · FULL REPORT"), _price_line(run) + f" · {esc(a.get('label') or '')}"]
    if informational(run):
        L.append("Informational only: execution only on Hyperliquid perpetuals.")
    L += ["", f"<b>Verdict: {esc(v.get('action') or 'WAIT')}</b> ({esc(v.get('rating') or 'Hold')}) · {esc(clip(v.get('summary'), 400))}"]
    for w in (v.get("what_changes_view") or [])[:3]:
        L.append(f"↪ {esc(clip(w, 200))}")
    L += ["", "<b>Analysts</b>"]
    for n in SECTIONS:
        x = (run.get("analysts") or {}).get(n) or {}
        L.append(f"<b>{n.capitalize()}</b> ({esc(x.get('lean') or 'unknown')}, {esc(x.get('confidence') or 'low')}): {esc(clip(x.get('summary'), 420))}")
        lim = x.get("limitations") or []
        if lim:
            L.append(f"  <i>limits: {esc(clip('; '.join(lim[:2]), 200))}</i>")
    L += ["", "<b>Debate</b>"]
    for side in ("bull", "bear"):
        s = deb.get(side) or {}
        L.append(f"{'🐂' if side == 'bull' else '🐻'} {esc(clip(s.get('thesis'), 520))}")
        if s.get("rebuttal"):
            L.append(f"  ↩️ {esc(clip(s['rebuttal'], 260))}")
    m = deb.get("manager") or {}
    L.append(f"⚖️ Research manager {esc(m.get('rating') or '?')}: {esc(clip(m.get('summary'), 300))}")
    if deb.get("missing_info"):
        L.append(f"<i>Missing: {esc(clip('; '.join(deb['missing_info'][:3]), 250))}</i>")
    L += ["", f"<b>Trader: {esc(tr.get('action') or 'WAIT')}</b> · {esc(clip(tr.get('rationale'), 300))}"]
    if (tr.get("plan") or {}).get("derivation"):
        L.append(f"<i>{esc(clip(tr['plan']['derivation'], 600))}</i>")
    L += ["", "<b>Scenarios (from the evidence)</b>"]
    for side in ("long", "short"):
        line = _scenario_line(run, side)
        s = ((v.get("scenarios") or {}).get(side)) or {}
        if line:
            L.append(line)
            L.append(f"  <i>{esc(clip('trigger: ' + str(s.get('trigger_rule') or ''), 300))}</i>")
            L.append(f"  <i>{esc(clip('stop: ' + str(s.get('invalidation_rule') or ''), 300))}</i>")
            L.append(f"  <i>{esc(clip('targets: ' + str(s.get('target_rule') or ''), 300))}</i>")
        else:
            L.append(f"{side.capitalize()}: no scenario in the evidence")
    L += ["", "<b>Risk committee</b>"]
    for voice in ("aggressive", "neutral", "conservative"):
        x = rk.get(voice) or {}
        L.append(f"· {voice.capitalize()} ({esc(x.get('stance') or '?')}): {esc(clip('; '.join(x.get('concerns') or []) or 'no concern', 300))}")
    L += ["", f"<b>Kiwi risk engine: {esc(str(ex.get('status') or '?').replace('_', ' '))}</b>"]
    L += [f"· {esc(clip(r, 220))}" for r in (ex.get("reasons") or [])[:5]]
    bad = [c for c in ex.get("checks") or [] if not c.get("ok")]
    L += [f"❌ {esc(clip(c.get('name'), 80))}: {esc(clip(c.get('value'), 160))}" for c in bad[:6]]
    L.append(f"{sum(1 for c in ex.get('checks') or [] if c.get('ok'))} checks passed")
    notes = missing_notes(run)
    deg = meta.get("degraded") or []
    L += ["", f"<i>Run {esc(run.get('id') or '')} · {esc(meta.get('backend') or 'rules')} backend · {int(meta.get('calls') or 0)} model calls"
          + (f" · ${float(meta.get('cost_usd') or 0):.4f}" if meta.get("cost_usd") else "") + f" · {esc(meta.get('framework') or '')}</i>"]
    if notes or deg:
        L.append(f"<i>{esc(clip('; '.join(notes + [str(d) for d in deg[:3]]), 300))}</i>")
    L.append(f"<i>{NOTE}</i>")
    return _split("\n".join(L))


def _split(text: str) -> list[str]:
    if len(text) <= PART_MAX:
        return [text]
    lines, first, n = text.split("\n"), [], 0
    for i, line in enumerate(lines):
        if n + len(line) + 1 > PART_MAX:
            rest = "\n".join(lines[i:])
            return ["\n".join(first), _fit(rest)]
        first.append(line)
        n += len(line) + 1
    return [text]


def view(run: dict[str, Any], name: str) -> str:
    """One message for a button / command view ('full' gives its first part; full_report has both)."""
    if name == "full":
        return full_report(run)[0]
    if name in ("bull", "bear"):
        return side_view(run, name)
    if name == "debate":
        return debate_view(run)
    if name == "risk":
        return risk_view(run)
    return compact(run)


def buttons(run_id: str, name: str = "sum", web: str | None = None) -> list[list[dict[str, str]]]:
    """The inline keyboard: callback data cm:<run id>:<view> (well under Telegram's 64 bytes); `web`: the run's page on the
    website (a URL button, 🌐 Web report)."""
    def b(text: str, what: str) -> dict[str, str]:
        return {"text": text, "callback_data": f"cm:{run_id}:{what}"}

    nav = [b("📄 Full Report", "full"), b("🐂 Bull Case", "bull"), b("🐻 Bear Case", "bear")]
    tools = [b("🛡 Risk Review", "risk"), b("📊 Chart", "chart"), b("🔄 Refresh", "refresh")]
    link = [[{"text": "🌐 Web report", "url": web}]] if web else []
    if name == "sum":
        return [nav, tools, *link]
    others = [x for x in nav + [tools[0]] if not x["callback_data"].endswith(":" + name)]
    return [[b("⬅️ Back", "sum"), tools[1], tools[2]], others, *link]


def placeholder(symbol: str, tf: str) -> str:
    return (f"🥝 Kiwi Committee is meeting on <b>{esc(symbol)} {esc(tf)}</b>…\n"
            "Four analysts, a bull/bear debate, the risk panel, then the Kiwi risk engine. Usually seconds, up to ~3 min.")


def failed_text(symbol: str, tf: str, error: str | None, usage: str) -> str:
    err = clip(error or "the run failed", 300)
    hint = f"\n{usage}" if re.search(r"isn't a market|unknown|not found|no market", err, re.I) else ""
    return f"⚠️ Kiwi Committee couldn't finish <b>{esc(symbol)} {esc(tf)}</b>: {esc(err)}{hint}"


def status_text(st: dict[str, Any], mine: tuple[int, int] | None = None) -> str:
    """/committee status from the /api/committee/status payload (the same function, not HTTP)."""
    lines = ["🥝 <b>Kiwi Committee status</b>",
             f"{'On' if st.get('enabled') else 'Off'} · backend now: <b>{esc(st.get('backend') or 'rules')}</b>"
             + (f" ({esc(clip(st['backend_note'], 120))})" if st.get("backend_note") else "")]
    bk = st.get("backends") or {}
    if bk:
        lines.append("Backends: " + " · ".join(f"{'✅' if x.get('ok') else '❌'} {esc(n)}" + ("" if x.get("ok") else f" ({esc(clip(x.get('detail'), 60))})")
                                                for n, x in bk.items()))
    pv = st.get("providers") or {}
    if pv:
        def age(s: Any) -> str:
            if s is None:
                return "never"
            s = float(s)
            return f"{s:.0f} s" if s < 120 else f"{s / 60:.0f} min" if s < 7200 else f"{s / 3600:.0f} h"
        lines.append("Data: " + " · ".join(f"{'✅' if x.get('ok') else '❌'} {esc(n)} {age(x.get('age_s'))}" for n, x in pv.items()))
    b = st.get("budget") or {}
    if b:
        lines.append(f"Budget today: {b.get('calls_today', 0)}/{b.get('calls_limit', 0)} model calls · "
                     f"${float(b.get('usd_today') or 0):.2f}/${float(b.get('usd_limit') or 0):.2f}")
    c = st.get("concurrency") or {}
    if c:
        lines.append(f"Runs: {c.get('running', 0)} running (max {c.get('max', 0)}) · {c.get('queued', 0)} queued")
    if mine:
        lines.append(f"Your runs today: {mine[0]} of {mine[1]}")
    lines.append(f"<i>{NOTE}</i>")
    return "\n".join(lines)
