"""The committee's roles (the TradingAgents v0.7.0 pattern, ported): four analysts in parallel → bull / bear (one round; one
rebuttal each on depth "full") → research manager (5-tier rating) → trader (LONG / SHORT / WAIT) → risk panel (aggressive /
neutral / conservative, one round) → portfolio manager (final research verdict).

Honest by construction:
- Every role has a deterministic `rules_*` writer from the evidence pack. It is the rules backend's text AND each role's
  fallback when its model call fails, times out, is over budget, or states a number the evidence doesn't have.
- Prices never come from a model: plans are copied from the evidence's own scenarios (plan_from), with the derivation
  written next to them. A model only picks a direction, and only one whose scenario exists.
- A stale price or stale / missing technical data forces WAIT (data_guard), whatever any model says.
- Missing sections stay missing: an unavailable section is reported "unknown" with its limitation, and its analyst is not
  even asked (no call spent on nothing).
"""

import asyncio
import math
from typing import Any

from app.committee import grounding, prompts
from app.committee import schemas as S
from app.committee.llm import Call, Ledger

SECTIONS = ("technical", "fundamental", "news", "sentiment")
USABLE = ("ok", "partial")


# ---------------------------------------------------------------- evidence helpers
def sec(ev: dict[str, Any], name: str) -> dict[str, Any]:
    s = (ev.get("sections") or {}).get(name) or {}
    return {"status": s.get("status") or "unavailable", "limitations": list(s.get("limitations") or []), "facts": s.get("facts") or {},
            "items": s.get("items") or [], "lean": s.get("lean") or "unknown", "score": s.get("score"),
            "reasons": list(s.get("reasons") or []), "refs": list(s.get("refs") or [])}


def tech(ev: dict[str, Any]) -> dict[str, Any]:
    return sec(ev, "technical")["facts"]


def frame(ev: dict[str, Any]) -> dict[str, Any]:
    fr = tech(ev).get("frames") or {}
    return fr.get(ev.get("timeframe") or "1h") or next(iter(fr.values()), {}) if fr else {}


def ref_ids(ev: dict[str, Any]) -> set[str]:
    return {r.get("id") for r in ev.get("refs") or [] if r.get("id")}


def data_guard(ev: dict[str, Any]) -> str | None:
    """Why no trade can be proposed on this data at all (None = data is usable)."""
    p = ev.get("price") or {}
    if not p.get("value"):
        return "no current price in the evidence"
    if p.get("stale"):
        return f"market data is stale (price from {p.get('ts') or 'an unknown time'})"
    t = sec(ev, "technical")
    if t["status"] not in USABLE:
        why = t["limitations"][0] if t["limitations"] else t["status"]
        return f"technical data is {t['status']} ({why})"
    return None


def _decimals(ref: float) -> int:
    """5 significant figures at the scale of `ref` (Hyperliquid's price precision)."""
    if ref <= 0:
        return 2
    return max(0, 4 - int(math.floor(math.log10(ref))))


def scenario(ev: dict[str, Any], side: str) -> dict[str, Any] | None:
    return (tech(ev).get("scenarios") or {}).get(side)


def plan_from(ev: dict[str, Any], action: str) -> dict[str, Any] | None:
    """The trade plan for LONG / SHORT, copied from the evidence scenario: entry zone = trigger ± 0.25 × ATR14 of the run's
    timeframe (just the trigger when there is no ATR), stop = the scenario's invalidation, targets = its targets."""
    if action not in ("LONG", "SHORT"):
        return None
    s = scenario(ev, action.lower())
    if not s or not s.get("trigger") or not s.get("invalidation") or not s.get("targets"):
        return None
    trig, stop, targets = float(s["trigger"]), float(s["invalidation"]), [float(t) for t in s["targets"]]
    long = action == "LONG"
    if (long and (stop >= trig or any(t <= trig for t in targets))) or (not long and (stop <= trig or any(t >= trig for t in targets))):
        return None  # an inconsistent scenario is never turned into a plan
    atr = frame(ev).get("atr14")
    d = _decimals(trig)
    tf = ev.get("timeframe") or "1h"
    if atr:
        half = 0.25 * float(atr)
        zone = [round(trig - half, d), round(trig + half, d)]
        if (long and zone[0] <= stop) or (not long and zone[1] >= stop):
            zone = [trig, trig]
            entry = f"entry: the trigger {trig:g} ({s.get('trigger_rule') or 'scenario trigger'})"
        else:
            entry = f"entry: trigger {trig:g} ± 0.25 × ATR14 {tf} {float(atr):g} ({s.get('trigger_rule') or 'scenario trigger'})"
    else:
        zone = [trig, trig]
        entry = f"entry: the trigger {trig:g} ({s.get('trigger_rule') or 'scenario trigger'}; no ATR reading)"
    return {"entry_zone": zone, "stop": stop, "targets": targets,
            "derivation": f"{entry}; stop: invalidation {stop:g} ({s.get('invalidation_rule') or 'scenario invalidation'}); "
                          f"targets: {', '.join(f'{t:g}' for t in targets)} ({s.get('target_rule') or 'scenario targets'})"}


def scenarios_view(ev: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for side in ("long", "short"):
        s = scenario(ev, side)
        p = plan_from(ev, side.upper())
        out[side] = ({**{k: s.get(k) for k in ("trigger", "trigger_rule", "invalidation", "invalidation_rule", "targets", "target_rule")},
                      "entry_zone": p["entry_zone"], "derivation": p["derivation"]} if s and p else None)
    return out


def _clean_points(points: list[dict[str, Any]], known: set[str]) -> list[dict[str, Any]]:
    return [{"text": p["text"], "refs": [r for r in p.get("refs") or [] if r in known]} for p in points if p.get("text")]


# ---------------------------------------------------------------- numbers in rules text
def fnum(v: Any, sig: int = 6) -> str:
    """A number as rules text writes it: `sig` significant digits, thousands commas, no trailing zeros. Written this way it
    is the evidence value at the precision shown, so the grounder (and a reader) can match it to the evidence."""
    v = float(v)
    if v == 0 or not math.isfinite(v):
        return "0"
    d = max(0, sig - 1 - int(math.floor(math.log10(abs(v)))))
    s = f"{v:,.{d}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def signed(v: Any, sig: int = 4) -> str:
    return ("+" if float(v) > 0 else "") + fnum(v, sig)


def usd(v: Any) -> str:
    v = float(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= div:
            return f"${fnum(v / div, 4)}{suf}"
    return f"${fnum(v, 4)}"


def pc(v: Any) -> str:
    return f"{fnum(abs(float(v)), 2)}%"


def _ref_of(ev: dict[str, Any], *needles: str) -> list[str]:
    """The id of the first evidence ref whose source names all the needles ("1h candles", "funding"...)."""
    for r in ev.get("refs") or []:
        src = str(r.get("source") or "").lower()
        if r.get("id") and all(n.lower() in src for n in needles):
            return [r["id"]]
    return []


def derived(ev: dict[str, Any]) -> dict[str, Any]:
    """Deterministic derivations of the evidence that the rules text quotes: distances to the nearest levels and to each
    scenario's trigger in %, stop distance in % and in ATR14 of the run's timeframe, reward-to-risk to T1. They join the
    grounder's numbers (a model may repeat them) and are always written next to what they were derived from."""
    out: dict[str, Any] = {}
    p = float((ev.get("price") or {}).get("value") or 0)
    t, f = tech(ev), frame(ev)
    atr = f.get("atr14")
    if p > 0:
        res = sorted(float(x) for x in t.get("resistance") or [] if float(x) > p)
        sup = sorted((float(x) for x in t.get("support") or [] if float(x) < p), reverse=True)
        if res:
            out["resistance"] = {"level": res[0], "pct": round((res[0] - p) / p * 100, 2)}
        if sup:
            out["support"] = {"level": sup[0], "pct": round((p - sup[0]) / p * 100, 2)}
    for side in ("long", "short"):
        s = scenario(ev, side)
        if not s or not s.get("trigger") or not s.get("invalidation"):
            continue
        trig, stop = float(s["trigger"]), float(s["invalidation"])
        risk = abs(trig - stop)
        d: dict[str, Any] = {"trigger": trig, "stop": stop, "stop_pct": round(risk / trig * 100, 2)}
        if p > 0:
            d["trigger_pct"] = round((trig - p) / p * 100, 2)
        if atr:
            d["stop_atr"] = round(risk / float(atr), 1)
        targets = [float(x) for x in s.get("targets") or []]
        if targets and risk:
            d["t1"] = targets[0]
            d["t1_pct"] = round(abs(targets[0] - trig) / trig * 100, 2)
            d["rr1"] = round(abs(targets[0] - trig) / risk, 1)
        out[side] = d
    listed = sec(ev, "fundamental")["facts"].get("listed_on")
    if isinstance(listed, list):
        out["venues"] = len(listed)
    return out


def _frame_phrase(tf: str, fr: dict[str, Any], full: bool = True) -> str:
    head = f"{tf} trend {fr.get('trend') or 'n/a'}"
    if full and fr.get("structure") and fr["structure"] != "mixed":
        head += f" ({fr['structure']})"
    bits = [head]
    if fr.get("rsi14") is not None:
        bits.append(f"RSI {fnum(fr['rsi14'], 3)}")
    h = fr.get("macd_hist")
    if full and isinstance(h, (int, float)):
        bits.append(f"MACD histogram {signed(h)} ({'above' if h > 0 else 'below' if h < 0 else 'at'} zero)")
    if full and fr.get("breakout"):
        bits.append(f"breakout {fr['breakout']}")
    return ", ".join(bits)


def _lean_head(s: dict[str, Any]) -> str:
    head = s["lean"].capitalize() if s["lean"] != "unknown" else "No lean"
    if s["score"] is not None:
        head += f" (score {float(s['score']):+g} of ±2)"
    return head


def _technical_summary(ev: dict[str, Any], s: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    tf = ev.get("timeframe") or "1h"
    frames = tech(ev).get("frames") or {}
    main = frames.get(tf) or frame(ev)
    parts = [_frame_phrase(tf, main)] if main else []
    others = [f"{k} {v.get('trend')}" + (f" (RSI {fnum(v['rsi14'], 3)})" if v.get("rsi14") is not None else "")
              for k, v in frames.items() if k != tf and v.get("trend")]
    if others:
        parts.append(", ".join(others))
    dv = derived(ev)
    lv = []
    if "support" in dv:
        lv.append(f"{pc(dv['support']['pct'])} above support {fnum(dv['support']['level'])}")
    if "resistance" in dv:
        lv.append(f"{pc(dv['resistance']['pct'])} under resistance {fnum(dv['resistance']['level'])}")
    if lv:
        parts.append("price " + " and ".join(lv))
    sig = tech(ev).get("signal") or {}
    tail = ""
    if sig.get("verdict"):
        tail = f" Rule signal {sig['verdict']}" + (f" ({sig['first_fail']})" if sig.get("first_fail") and sig["verdict"] == "WAIT" else "") + "."
    text = f"{_lean_head(s)}: " + ("; ".join(parts) if parts else "no indicator readings") + "." + tail
    return text, []


def _fundamental_summary(ev: dict[str, Any], s: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    f = s["facts"]
    cg = f.get("coingecko") if isinstance(f.get("coingecko"), dict) else {}
    meta, cgref = _ref_of(ev, "hyperliquid", "meta"), _ref_of(ev, "coingecko")
    pts: list[dict[str, Any]] = []
    if cg.get("rank") or cg.get("market_cap"):
        t = (f"#{cg['rank']} by market cap" if cg.get("rank") else "market cap") + (f" ({usd(cg['market_cap'])})" if cg.get("market_cap") else "")
        if isinstance(cg.get("ath_change_pct"), (int, float)):
            t += f", {pc(cg['ath_change_pct'])} {'under' if cg['ath_change_pct'] < 0 else 'above'} its all-time high"
        pts.append({"text": t, "refs": cgref})
    liq = []
    if isinstance(f.get("volume_24h_usd"), (int, float)):
        liq.append(f"24h volume {usd(f['volume_24h_usd'])}")
    if isinstance(f.get("oi_usd"), (int, float)):
        liq.append(f"open interest {usd(f['oi_usd'])}")
    if liq:
        venue = (ev.get("asset") or {}).get("venue") or ""
        t = ", ".join(liq) + (" on Hyperliquid" if venue == "hyperliquid" else "")
        if f.get("max_leverage"):
            t += f" (max leverage {f['max_leverage']}x)"
        pts.append({"text": t, "refs": meta})
    if isinstance(f.get("spread_bps"), (int, float)):
        pts.append({"text": f"spread {fnum(f['spread_bps'], 3)} bps", "refs": meta})
    if isinstance(f.get("listed_on"), list) and f["listed_on"]:
        pts.append({"text": f"listed on {len(f['listed_on'])} venues", "refs": _ref_of(ev, "listings")})
    for flag in (f.get("risk_flags") or [])[:2]:
        pts.append({"text": f"risk flag: {flag}", "refs": meta})
    text = f"{_lean_head(s)}: " + ("; ".join(p["text"] for p in pts[:3]) if pts else "no market facts beyond the listing") + "."
    return text, pts


def _news_summary(ev: dict[str, Any], s: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    f, items = s["facts"], [i for i in s["items"] if isinstance(i, dict)]
    sym = (ev.get("asset") or {}).get("symbol") or "the coin"
    window = f.get("window_h") or 48
    coin = sorted((i for i in items if i.get("match") in ("symbol", "name")), key=lambda i: float(i.get("age_h") or 0))
    macro = sorted((i for i in items if i.get("match") == "macro"), key=lambda i: float(i.get("age_h") or 0))
    matched = f.get("matched", len(coin))
    pts: list[dict[str, Any]] = []

    def head(i: dict[str, Any]) -> dict[str, Any]:
        title = str(i.get("title") or "")
        title = title if len(title) <= 90 else title[:89] + "…"
        age = f", {fnum(i['age_h'], 3)} h ago" if isinstance(i.get("age_h"), (int, float)) else ""
        return {"text": f"\"{title}\" ({i.get('source') or 'news'}{age})", "refs": [i["ref"]] if i.get("ref") else []}

    if matched:
        first = f"{matched} headline{'s' if matched != 1 else ''} about {sym} in {window} h"
        if coin:
            first += f", latest {head(coin[0])['text']}"
    else:
        first = f"no {sym} headline in the last {window} h"
    if macro:
        first += f"; {f.get('macro', len(macro))} macro headlines, latest {head(macro[0])['text']}"
    pts += [head(i) for i in coin[:2]] + [head(i) for i in macro[:1]]
    reasons = [r.get("text") for r in s["reasons"] if r.get("text")]
    text = f"{_lean_head(s)}: {first}." + (f" {reasons[0]}." if reasons and s["lean"] in ("bullish", "bearish") else "")
    return text, pts


def _sentiment_summary(ev: dict[str, Any], s: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    f = s["facts"]
    pts: list[dict[str, Any]] = []
    fu = f.get("funding_1h_pct")
    if isinstance(fu, (int, float)):
        t = f"funding {signed(fu)}%/h ({'longs pay' if fu > 0 else 'shorts pay' if fu < 0 else 'flat'})"
        avg = [f"{k} avg {signed(f[key])}%/h" for k, key in (("24h", "funding_24h_avg_pct"), ("7d", "funding_7d_avg_pct"))
               if isinstance(f.get(key), (int, float))]
        pts.append({"text": t + (f", {', '.join(avg)}" if avg else ""), "refs": [f["funding_ref"]] if f.get("funding_ref") else []})
    fg = f.get("fear_greed") if isinstance(f.get("fear_greed"), dict) else None
    if fg and fg.get("value") is not None:
        t = f"Fear & Greed {fg['value']} ({fg.get('label') or 'n/a'})" + (f", {fg['value_7d_ago']} a week ago" if fg.get("value_7d_ago") is not None else "")
        pts.append({"text": t, "refs": [fg["ref"]] if fg.get("ref") else []})
    if isinstance(f.get("oi_change_24h_pct"), (int, float)):
        pts.append({"text": f"open interest {signed(f['oi_change_24h_pct'], 3)}% in 24h", "refs": [f["oi_ref"]] if f.get("oi_ref") else []})
    tf_ = f.get("taker_flow") if isinstance(f.get("taker_flow"), dict) else None
    if tf_ and tf_.get("buy_share_pct") is not None:
        pts.append({"text": f"takers bought {fnum(tf_['buy_share_pct'], 3)}% of the last {fnum(tf_.get('window_min') or 0, 3)} min flow (noisy)",
                    "refs": [tf_["ref"]] if tf_.get("ref") else []})
    cm = f.get("community") if isinstance(f.get("community"), dict) else None
    if cm and cm.get("mentions_24h") is not None:
        pts.append({"text": f"{cm['mentions_24h']} community mentions in 24h vs {fnum(cm.get('mentions_7d_avg') or 0, 3)} a day before (noisy)",
                    "refs": [cm["ref"]] if cm.get("ref") else []})
    text = f"{_lean_head(s)}: " + ("; ".join(p["text"] for p in pts[:3]) if pts else "no positioning data") + "."
    return text, pts


_SUMMARY = {"technical": _technical_summary, "fundamental": _fundamental_summary, "news": _news_summary, "sentiment": _sentiment_summary}


# ---------------------------------------------------------------- rules writers (deterministic; also every role's fallback)
def rules_analyst(ev: dict[str, Any], name: str) -> dict[str, Any]:
    """The section's lean and score, with its 2–3 decisive facts and their numbers (all from the evidence, or labelled
    derivations of it: distances in %)."""
    s = sec(ev, name)
    lim = s["limitations"] or ([f"{name} section {s['status']}"] if s["status"] != "ok" else [])
    if s["status"] == "unavailable":
        return {"lean": "unknown", "confidence": "low", "summary": f"No {name} data: {lim[0] if lim else 'unavailable'}.",
                "points": [], "limitations": lim, "author": "rules"}
    score = s["score"]
    conf = "low" if s["status"] != "ok" or score is None else ("high" if abs(score) >= 2 else "medium" if abs(score) >= 1 else "low")
    points = [{"text": r.get("text", ""), "refs": list(r.get("refs") or [])} for r in s["reasons"][:4] if r.get("text")]
    try:
        summary, extra = _SUMMARY[name](ev, s)
    except (TypeError, ValueError, KeyError, AttributeError):  # an odd fact never breaks the rules text: the plain lean then
        summary, extra = f"{_lean_head(s)}.", []
    if s["status"] != "ok":
        summary += f" Section {s['status']}" + (f": {lim[0]}." if lim else ".")
    if not points:
        points = extra[:4]
    return {"lean": s["lean"], "confidence": conf, "summary": summary, "points": points, "limitations": lim, "author": "rules"}


def _leaning(analysts: dict[str, Any], lean: str) -> list[str]:
    return [k for k, v in analysts.items() if v["lean"] == lean]


def side_facts(ev: dict[str, Any], side: str) -> list[dict[str, Any]]:
    """The evidence facts that support one side ("bull" / "bear"), whatever the sections' overall lean, best first."""
    bull = side == "bull"
    tf = ev.get("timeframe") or "1h"
    frames = tech(ev).get("frames") or {}
    order = [tf] + [k for k in frames if k != tf]
    out: list[dict[str, Any]] = []

    def add(text: str, refs: list[str]) -> None:
        out.append({"text": text, "refs": refs})

    for k in order:
        fr = frames.get(k) or {}
        if not fr:
            continue
        refs = _ref_of(ev, f" {k} candles")
        want = "up" if bull else "down"
        if fr.get("trend") == want:
            add(f"{k} trend {want}" + (f" ({fr['structure']})" if fr.get("structure") == ("HH/HL" if bull else "LH/LL") else ""), refs)
        elif fr.get("structure") == ("HH/HL" if bull else "LH/LL"):
            add(f"{k} structure {fr['structure']}", refs)
        h = fr.get("macd_hist")
        if isinstance(h, (int, float)) and ((bull and h > 0) or (not bull and h < 0)):
            add(f"{k} MACD histogram {signed(h)}", refs)
        r = fr.get("rsi14")
        if isinstance(r, (int, float)) and ((bull and r <= 35) or (not bull and r >= 65)):
            add(f"{k} RSI {fnum(r, 3)} ({'oversold side' if bull else 'overbought side'})", refs)
        c, e200 = fr.get("close"), fr.get("ema200")
        if k in (tf, "1d") and isinstance(c, (int, float)) and isinstance(e200, (int, float)) and ((bull and c > e200) or (not bull and c < e200)):
            add(f"{k} close {'above' if bull else 'below'} EMA200 {fnum(e200)}", refs)
        if fr.get("breakout") == ("up" if bull else "down"):
            add(f"{k} breakout {fr['breakout']}", refs)
    sig = tech(ev).get("signal") or {}
    if sig.get("verdict") == ("LONG" if bull else "SHORT"):
        add(f"rule signal {sig['verdict']} ({sig.get('rule') or 'rules'})", [sig["ref"]] if sig.get("ref") else [])
    dv = derived(ev)
    lvl = dv.get("support" if bull else "resistance")
    if lvl and lvl["pct"] <= 1.0:
        add(f"{'support' if bull else 'resistance'} {fnum(lvl['level'])} only {pc(lvl['pct'])} {'below' if bull else 'above'} the price", _ref_of(ev, f" {tf} candles"))
    st = sec(ev, "sentiment")["facts"]
    fu = st.get("funding_1h_pct")
    if isinstance(fu, (int, float)) and ((bull and fu < 0) or (not bull and fu > 0)):
        add(f"{'shorts' if bull else 'longs'} pay funding {signed(fu)}%/h", [st["funding_ref"]] if st.get("funding_ref") else [])
    fg = st.get("fear_greed") if isinstance(st.get("fear_greed"), dict) else None
    if fg and isinstance(fg.get("value"), (int, float)) and ((bull and fg["value"] <= 25) or (not bull and fg["value"] >= 75)):
        add(f"Fear & Greed {fg['value']} ({fg.get('label')}): {'extreme fear, contrarian' if bull else 'extreme greed, crowded'}", [fg["ref"]] if fg.get("ref") else [])
    for name in ("news", "fundamental"):
        s = sec(ev, name)
        if s["status"] in USABLE and s["lean"] == ("bullish" if bull else "bearish") and s["reasons"]:
            r0 = s["reasons"][0]
            add(f"{name}: {r0.get('text')}", list(r0.get("refs") or []))
    return out


def _setup_line(ev: dict[str, Any], side: str) -> str:
    """'The long setup needs a 1h close above 67,800 (0.1% away), stop 66,200 (2.4%, 5 x ATR), T1 68,900 (R/R 0.7).'"""
    d = derived(ev).get(side)
    if not d:
        return f"There is no {side} scenario in the evidence."
    tf = ev.get("timeframe") or "1h"
    t = f"The {side} setup needs a {tf} close {'above' if side == 'long' else 'below'} {fnum(d['trigger'])}"
    if "trigger_pct" in d:
        t += f" ({pc(d['trigger_pct'])} away)"
    t += f", stop {fnum(d['stop'])} ({pc(d['stop_pct'])}" + (f", {fnum(d['stop_atr'], 2)} x ATR14" if "stop_atr" in d else "") + ")"
    if "t1" in d:
        t += f", T1 {fnum(d['t1'])} (R/R {fnum(d['rr1'], 2)})"
    return t + "."


def rules_debater(ev: dict[str, Any], analysts: dict[str, Any], side: str) -> dict[str, Any]:
    lean, scen = ("bullish", "long") if side == "bull" else ("bearish", "short")
    names = _leaning(analysts, lean)
    facts = side_facts(ev, side)
    best = "; ".join(f["text"] for f in facts[:3])
    if names:
        thesis = f"{len(names)} of {len(SECTIONS)} sections lean {lean} ({', '.join(names)})" + (f": {best}." if best else ".")
    elif facts:
        thesis = f"Thin {side} case: no section leans {lean}; its best facts: {best}."
    else:
        thesis = f"Thin {side} case: no section leans {lean} and no fact in the evidence supports a {scen}."
    thesis += " " + _setup_line(ev, scen)
    points = [p for n in names for p in analysts[n]["points"]]
    seen = {p["text"] for p in points}
    points = (points + [f for f in facts if f["text"] not in seen])[:4]
    return {"thesis": thesis, "points": points, "rebuttal": "", "author": "rules"}


def rules_rebuttals(ev: dict[str, Any], analysts: dict[str, Any]) -> dict[str, str]:
    unknown = [k for k, v in analysts.items() if v["lean"] == "unknown"]
    miss = f" ({', '.join(unknown)} missing)" if unknown else ""

    def line(me: str, other: str, other_lean: str) -> str:
        theirs = _leaning(analysts, other_lean)
        mine = side_facts(ev, me)
        if not theirs and not side_facts(ev, other):
            return f"No section leans {other_lean} and the evidence has no {other} fact to answer{miss}."
        basis = f"The {other} case rests on {', '.join(theirs)}" if theirs else f"The {other} case has no leaning section"
        if not mine:
            return f"{basis}; nothing in the evidence points the other way{miss}."
        return f"{basis}; against it: {'; '.join(f['text'] for f in mine[:2])}{miss}."

    return {"bull": line("bull", "bear", "bearish"), "bear": line("bear", "bull", "bullish")}


def missing_info(ev: dict[str, Any]) -> list[str]:
    out = []
    for n in SECTIONS:
        s = sec(ev, n)
        if s["status"] != "ok":
            out.append(f"{n}: {s['status']}" + (f" ({s['limitations'][0]})" if s["limitations"] else ""))
    p = ev.get("price") or {}
    if p.get("stale"):
        out.append(f"price: stale (from {p.get('ts') or 'an unknown time'})")
    return out + [d for d in ev.get("degraded") or [] if isinstance(d, str)][:4]


def rules_manager(ev: dict[str, Any], analysts: dict[str, Any]) -> dict[str, Any]:
    used = [(n, sec(ev, n)["score"]) for n in SECTIONS if sec(ev, n)["status"] in USABLE and sec(ev, n)["score"] is not None]
    total = sum(float(v) for _, v in used)
    guard = data_guard(ev)
    bull, bear = _leaning(analysts, "bullish"), _leaning(analysts, "bearish")
    if guard:
        rating, why = "Hold", guard
    elif bull and bear and abs(total) <= 1:
        rating, why = "Hold", f"the evidence conflicts ({', '.join(bull)} bullish vs {', '.join(bear)} bearish)"
    else:
        rating = "Buy" if total >= 3 else "Overweight" if total >= 1 else "Sell" if total <= -3 else "Underweight" if total <= -1 else "Hold"
        lead = [n for n, v in used if float(v) != 0]
        why = ("the section scores are balanced" if rating == "Hold" else
               f"carried by {', '.join(lead)}" + (f" while {', '.join(n for n in SECTIONS if n not in lead)} stay neutral or missing"
                                                  if len(lead) < len(SECTIONS) else ""))
    detail = ", ".join(f"{n} {float(v):+g}" for n, v in used) or "no scored section"
    return {"rating": rating, "summary": f"{rating}: section scores add up to {total:+g} ({detail}); {why}.", "author": "rules"}


def _want(rating: str) -> str | None:
    return "LONG" if rating in ("Buy", "Overweight") else "SHORT" if rating in ("Sell", "Underweight") else None


def _trigger_words(ev: dict[str, Any], side: str) -> str:
    d = derived(ev).get(side)
    tf = ev.get("timeframe") or "1h"
    return f"a {tf} close {'above' if side == 'long' else 'below'} {fnum(d['trigger'])}" if d else f"a {side} setup"


def rules_trader(ev: dict[str, Any], rating: str) -> dict[str, Any]:
    guard = data_guard(ev)
    sig = tech(ev).get("signal") or {}
    want = _want(rating)
    if guard:
        action, why = "WAIT", f"No trade: {guard}."
    elif want is None:
        both = [f"long on {_trigger_words(ev, 'long')}" if derived(ev).get("long") else "",
                f"short on {_trigger_words(ev, 'short')}" if derived(ev).get("short") else ""]
        why = f"The research rating is {rating}: no side has the edge in the evidence."
        why += (" Watch: " + " or ".join(b for b in both if b) + ".") if any(both) else ""
        action = "WAIT"
    elif not plan_from(ev, want):
        action, why = "WAIT", f"The rating leans {want} but the evidence has no usable {want.lower()} scenario."
    elif sig.get("verdict") != want:
        fail = f" (first failed rule: {sig['first_fail']})" if sig.get("first_fail") else ""
        action, why = "WAIT", (f"The rating leans {want} but the rule-based signal says {sig.get('verdict') or 'nothing'}{fail}; "
                               f"wait for {_trigger_words(ev, want.lower())}.")
    else:
        action, why = want, f"Rating {rating} and the {sig.get('rule') or 'rule-based'} signal agree on {want}."
    return {"action": action, "rationale": why, "plan": plan_from(ev, action), "author": "rules"}


def risk_notes(ev: dict[str, Any], side: str | None) -> dict[str, list[str]]:
    """Concrete risk facts: {"stop": [...], "liquidity": [...], "volatility": [...], "funding": [...], "data": [...]} for one
    side ("long" / "short") or for none (data, liquidity and volatility only)."""
    tf = ev.get("timeframe") or "1h"
    f, fund, st = frame(ev), sec(ev, "fundamental")["facts"], sec(ev, "sentiment")["facts"]
    out: dict[str, list[str]] = {"stop": [], "liquidity": [], "volatility": [], "funding": [], "data": [], "partial": []}
    d = derived(ev).get(side) if side else None
    if d:
        t = f"{side} stop {fnum(d['stop'])} is {pc(d['stop_pct'])} from the trigger"
        if "stop_atr" in d:
            t += f" ({fnum(d['stop_atr'], 2)} x ATR14 {tf})"
            if d["stop_atr"] <= 1:
                t += ": tight, about one ATR or less"
        out["stop"].append(t)
        if "rr1" in d and d["rr1"] < 1:
            out["stop"].append(f"{side} T1 {fnum(d['t1'])} pays only R/R {fnum(d['rr1'], 2)}")
    vol = fund.get("volume_24h_usd")
    if isinstance(vol, (int, float)) and vol < 50e6:
        out["liquidity"].append(f"thin liquidity: 24h volume {usd(vol)}, slippage risk")
    sp = fund.get("spread_bps")
    if isinstance(sp, (int, float)) and sp > 5:
        out["liquidity"].append(f"spread {fnum(sp, 3)} bps")
    if isinstance(f.get("atr_pct"), (int, float)):
        t = f"ATR14 {fnum(f['atr_pct'], 3)}% of price per {tf} candle"
        day = (tech(ev).get("frames") or {}).get("1d") or {}
        if tf != "1d" and isinstance(day.get("atr_pct"), (int, float)):
            t += f", {fnum(day['atr_pct'], 3)}% a day"
        out["volatility"].append(t)
    fu = st.get("funding_1h_pct")
    if isinstance(fu, (int, float)) and abs(fu) >= 0.005:  # well above Hyperliquid's baseline 0.00125%/h
        payer = "longs" if fu > 0 else "shorts"
        if side is None or (side == "long") == (fu > 0):
            out["funding"].append(f"{payer} pay funding {signed(fu)}%/h")
    p = ev.get("price") or {}
    if p.get("stale"):
        out["data"].append(f"price is stale (from {p.get('ts') or 'an unknown time'})")
    gone = [n for n in SECTIONS if sec(ev, n)["status"] not in USABLE]
    if gone:
        out["data"].append(f"missing or stale data: {', '.join(gone)}")
    part = [n for n in SECTIONS if sec(ev, n)["status"] == "partial"]
    if part:
        out["partial"].append(f"partial data: {', '.join(part)}")
    return out


def rules_risk(ev: dict[str, Any], analysts: dict[str, Any], trader: dict[str, Any]) -> dict[str, Any]:
    action = trader["action"]
    if action == "WAIT":
        dv = derived(ev)
        near = [f"{s} trigger {fnum(dv[s]['trigger'])} is {pc(dv[s]['trigger_pct'])} {'above' if dv[s]['trigger_pct'] > 0 else 'below'} the price"
                for s in ("long", "short") if s in dv and "trigger_pct" in dv[s]]
        sig = tech(ev).get("signal") or {}
        agg = ([f"waiting can miss the move: {' and '.join(near)}"] if near else []) + [
            n for s in ("long", "short") for n in risk_notes(ev, s)["stop"][:1]][:2]
        neu = (["no trigger has fired" + (f"; the rule signal fails on: {sig['first_fail']}" if sig.get("first_fail") else "")]
               + risk_notes(ev, None)["volatility"])
        base = risk_notes(ev, None)
        tight = [n for s in ("long", "short") for n in risk_notes(ev, s)["stop"] if "tight" in n][:1]
        cons = base["data"] + base["liquidity"] + tight + base["funding"] + base["partial"] + base["volatility"]
        voices = {"aggressive": {"stance": "support", "concerns": agg[:3]}, "neutral": {"stance": "support", "concerns": neu[:3]},
                  "conservative": {"stance": "support", "concerns": cons[:3]}}
        key = (cons or neu or agg or ["nothing stands out"])[0]
        return {**{k: {**v, "author": "rules"} for k, v in voices.items()},
                "summary": f"All three support waiting: nothing is proposed. Key risk: {key}.", "author": "rules"}
    side = action.lower()
    n = risk_notes(ev, side)
    against = _leaning(analysts, "bearish" if action == "LONG" else "bullish")
    opp = [f"{', '.join(against)} lean{'s' if len(against) == 1 else ''} the other way"] if against else []
    concerns = n["stop"] + opp + n["funding"]
    cons = n["data"] + opp + n["liquidity"] + n["stop"] + n["partial"] + n["volatility"]
    weak = bool(n["data"] or against)
    voices = {"aggressive": {"stance": "support", "concerns": (n["stop"] + n["funding"])[:1]},
              "neutral": {"stance": "reduce" if (opp or n["funding"] or any("noise" in c or "R/R" in c for c in n["stop"])) else "support",
                          "concerns": (concerns + n["volatility"])[:3]},
              "conservative": {"stance": "reject" if weak else "reduce", "concerns": cons[:3]}}
    say = {"support": "supports", "reduce": "says reduce", "reject": "rejects"}
    stn = {k: say[v["stance"]] for k, v in voices.items()}
    key = (cons or concerns or ["nothing stands out"])[0]
    return {**{k: {**v, "author": "rules"} for k, v in voices.items()},
            "summary": f"Aggressive {stn['aggressive']}, neutral {stn['neutral']}, conservative {stn['conservative']} the {action}. Key risk: {key}.",
            "author": "rules"}


def what_changes(ev: dict[str, Any], action: str) -> list[str]:
    out = []
    plan = plan_from(ev, action)
    tf = ev.get("timeframe") or "1h"
    if action in ("LONG", "SHORT") and plan:
        out.append(f"Invalidated: a {tf} close {'below' if action == 'LONG' else 'above'} {fnum(plan['stop'])} (the stop)")
    for side in ("long", "short"):
        d = derived(ev).get(side)
        if d and side.upper() != action:
            out.append(f"{side.capitalize()} setup: {_trigger_words(ev, side)}" + (f", stop {fnum(d['stop'])}" if d.get("stop") else ""))
    if sec(ev, "news")["status"] not in USABLE:
        out.append("News coverage returns (the news section is missing)")
    return out[:3]


def rules_portfolio(ev: dict[str, Any], manager: dict[str, Any], trader: dict[str, Any]) -> dict[str, Any]:
    action, rating = trader["action"], manager["rating"]
    if action == "WAIT":
        summary = f"WAIT. Research rating {rating}. {trader['rationale']}"
    else:
        summary = f"{action}. {trader['rationale']} The plan is the evidence's {action.lower()} scenario; execution is the risk engine's call."
    return {"action": action, "rating": rating, "summary": summary, "what_changes_view": what_changes(ev, action), "author": "rules"}


# ---------------------------------------------------------------- the model versions (each grounded, each falling back to rules)
class Context:
    """One run's shared state: the evidence, the ledger, the grounder and the degraded notes."""

    def __init__(self, ev: dict[str, Any], ledger: Ledger, depth: str) -> None:
        self.ev, self.ledger, self.depth = ev, ledger, depth
        self.refs = ref_ids(ev)
        self.degraded: list[str] = []
        self.grounder = grounding.Grounder(ev, scenarios_view(ev), plan_from(ev, "LONG"), plan_from(ev, "SHORT"), derived(ev))

    async def ask(self, role: str, task: str, system: str, prompt: str, schema: type, temperature: float = 0.2) -> tuple[dict | None, str]:
        out, author, note = await self.ledger.ask(Call(role, task, prompts.system(system), prompt, schema, temperature=temperature))
        if out is None:
            if note:
                self.degraded.append(note)
            return None, "rules"
        d = out.model_dump()
        bad = grounding.check(role, d, self.grounder)
        if bad:
            self.degraded.append(bad)
            return None, "rules"
        return d, author or "rules"


async def analyst(ctx: Context, name: str) -> dict[str, Any]:
    base = rules_analyst(ctx.ev, name)
    if sec(ctx.ev, name)["status"] == "unavailable":
        return base  # nothing to read: no call spent, the limitation is the report
    d, author = await ctx.ask(f"{name} analyst", "committee.analyst", prompts.ANALYST[name], prompts.analyst_prompt(ctx.ev, name), S.AnalystOut)
    if d is None:
        return base
    lim = base["limitations"] + [x for x in d["limitations"] if x not in base["limitations"]]
    return {"lean": d["lean"], "confidence": d["confidence"], "summary": d["summary"],
            "points": _clean_points(d["points"], ctx.refs) or base["points"], "limitations": lim[:6], "author": author}


async def analysts(ctx: Context) -> dict[str, Any]:
    res = await asyncio.gather(*(analyst(ctx, n) for n in SECTIONS))
    return dict(zip(SECTIONS, res, strict=True))


async def debate(ctx: Context, reports: dict[str, Any]) -> dict[str, Any]:
    async def side(name: str) -> dict[str, Any]:
        base = rules_debater(ctx.ev, reports, name)
        d, author = await ctx.ask(name, "committee.debate", prompts.BULL if name == "bull" else prompts.BEAR,
                                  prompts.debate_prompt(ctx.ev, reports, name), S.DebaterOut, 0.4)
        if d is None:
            return base
        return {"thesis": d["thesis"], "points": _clean_points(d["points"], ctx.refs), "rebuttal": "", "author": author}

    bull, bear = await asyncio.gather(side("bull"), side("bear"))
    if ctx.depth == "full":
        rb = rules_rebuttals(ctx.ev, reports)
        d, author = await ctx.ask("rebuttals", "committee.debate", prompts.REBUTTALS, prompts.rebuttal_prompt(ctx.ev, bull, bear), S.RebuttalsOut, 0.4)
        bull["rebuttal"], bear["rebuttal"] = (d["bull"], d["bear"]) if d else (rb["bull"], rb["bear"])
    out = {"rounds": 1, "bull": bull, "bear": bear, "missing_info": missing_info(ctx.ev)}
    base = rules_manager(ctx.ev, reports)
    d, author = await ctx.ask("research manager", "committee.synthesis", prompts.MANAGER, prompts.manager_prompt(ctx.ev, reports, {**out, "manager": base}), S.ManagerOut)
    out["manager"] = {"rating": d["rating"], "summary": d["summary"], "author": author} if d else base
    return out


def _constrain(ctx: Context, role: str, action: str) -> str | None:
    """Why a model's LONG / SHORT can't stand (None = it can)."""
    if action == "WAIT":
        return None
    guard = data_guard(ctx.ev)
    if guard:
        return f"{role}: {action} refused, {guard}"
    if not plan_from(ctx.ev, action):
        return f"{role}: {action} refused, the evidence has no usable {action.lower()} scenario"
    return None


async def trader(ctx: Context, reports: dict[str, Any], deb: dict[str, Any]) -> dict[str, Any]:
    base = rules_trader(ctx.ev, deb["manager"]["rating"])
    d, author = await ctx.ask("trader", "committee.trader", prompts.TRADER, prompts.trader_prompt(ctx.ev, reports, deb), S.TraderOut, 0.1)
    if d is None:
        return base
    bad = _constrain(ctx, "trader", d["action"])
    if bad:
        ctx.degraded.append(bad)
        return base
    return {"action": d["action"], "rationale": d["rationale"], "plan": plan_from(ctx.ev, d["action"]), "author": author}


async def risk_panel(ctx: Context, reports: dict[str, Any], tr: dict[str, Any]) -> dict[str, Any]:
    base = rules_risk(ctx.ev, reports, tr)
    d, author = await ctx.ask("risk panel", "committee.risk", prompts.RISK, prompts.risk_prompt(ctx.ev, reports, tr), S.RiskPanelOut)
    if d is None:
        return base
    return {**{k: {"stance": d[k]["stance"], "concerns": d[k]["concerns"][:3], "author": author}
               for k in ("aggressive", "neutral", "conservative")}, "summary": d["summary"], "author": author}


async def portfolio(ctx: Context, deb: dict[str, Any], tr: dict[str, Any], rk: dict[str, Any]) -> dict[str, Any]:
    base = rules_portfolio(ctx.ev, deb["manager"], tr)
    d, author = await ctx.ask("portfolio manager", "committee.synthesis", prompts.PORTFOLIO, prompts.portfolio_prompt(ctx.ev, deb, tr, rk), S.PortfolioOut)
    if d is None:
        return base
    bad = _constrain(ctx, "portfolio manager", d["action"])
    if bad:
        ctx.degraded.append(bad)
        return base
    return {"action": d["action"], "rating": d["rating"], "summary": d["summary"],
            "what_changes_view": d["what_changes_view"][:3] or base["what_changes_view"], "author": author}
