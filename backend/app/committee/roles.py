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


# ---------------------------------------------------------------- rules writers (deterministic; also every role's fallback)
def rules_analyst(ev: dict[str, Any], name: str) -> dict[str, Any]:
    s = sec(ev, name)
    lim = s["limitations"] or ([f"{name} section {s['status']}"] if s["status"] != "ok" else [])
    if s["status"] == "unavailable":
        return {"lean": "unknown", "confidence": "low", "summary": f"No {name} data: {lim[0] if lim else 'unavailable'}.",
                "points": [], "limitations": lim, "author": "rules"}
    score = s["score"]
    conf = "low" if s["status"] != "ok" or score is None else ("high" if abs(score) >= 2 else "medium" if abs(score) >= 1 else "low")
    points = [{"text": r.get("text", ""), "refs": list(r.get("refs") or [])} for r in s["reasons"][:4] if r.get("text")]
    summary = f"The {name} evidence leans {s['lean']}" + (f" (score {score:+g} on -2..2)" if score is not None else "")
    summary += f"; the section is {s['status']}." if s["status"] != "ok" else "."
    return {"lean": s["lean"], "confidence": conf, "summary": summary, "points": points, "limitations": lim, "author": "rules"}


def _leaning(analysts: dict[str, Any], lean: str) -> list[str]:
    return [k for k, v in analysts.items() if v["lean"] == lean]


def rules_debater(ev: dict[str, Any], analysts: dict[str, Any], side: str) -> dict[str, Any]:
    lean, scen = ("bullish", "long") if side == "bull" else ("bearish", "short")
    names = _leaning(analysts, lean)
    points = [p for n in names for p in analysts[n]["points"]][:4]
    s = scenario(ev, scen)
    if not names:
        thesis = f"The {side} case is thin: no section of the evidence leans {lean}."
    else:
        thesis = f"{len(names)} of {len(SECTIONS)} sections lean {lean} ({', '.join(names)})."
    if s and s.get("trigger_rule"):
        thesis += f" The {scen} setup needs: {s['trigger_rule']}."
    return {"thesis": thesis, "points": points, "rebuttal": "", "author": "rules"}


def rules_rebuttals(analysts: dict[str, Any]) -> dict[str, str]:
    bull, bear = _leaning(analysts, "bullish"), _leaning(analysts, "bearish")
    unknown = [k for k, v in analysts.items() if v["lean"] == "unknown"]

    def line(mine: list[str], theirs: list[str], other: str) -> str:
        if not theirs:
            return f"No section leans {other}; the other side has no evidence to answer."
        return (f"The {other} points come from {', '.join(theirs)} only; " + (f"{', '.join(mine)} point{'s' if len(mine) == 1 else ''} the other way" if mine
                else "nothing points the other way") + (f", and {', '.join(unknown)} is missing." if unknown else "."))

    return {"bull": line(bull, bear, "bearish"), "bear": line(bear, bull, "bullish")}


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
        why = "the section scores " + ("are balanced" if rating == "Hold" else "carry it")
    detail = ", ".join(f"{n} {float(v):+g}" for n, v in used) or "no scored section"
    return {"rating": rating, "summary": f"{rating}: section scores add up to {total:+g} ({detail}); {why}.", "author": "rules"}


def _want(rating: str) -> str | None:
    return "LONG" if rating in ("Buy", "Overweight") else "SHORT" if rating in ("Sell", "Underweight") else None


def rules_trader(ev: dict[str, Any], rating: str) -> dict[str, Any]:
    guard = data_guard(ev)
    sig = tech(ev).get("signal") or {}
    want = _want(rating)
    if guard:
        action, why = "WAIT", f"No trade: {guard}."
    elif want is None:
        action, why = "WAIT", f"The research rating is {rating}: no side has the edge in the evidence."
    elif not plan_from(ev, want):
        action, why = "WAIT", f"The rating leans {want} but the evidence has no usable {want.lower()} scenario."
    elif sig.get("verdict") != want:
        fail = f" (first failed rule: {sig['first_fail']})" if sig.get("first_fail") else ""
        action, why = "WAIT", f"The rating leans {want} but the rule-based signal says {sig.get('verdict') or 'nothing'}{fail}; wait for the trigger."
    else:
        action, why = want, f"Rating {rating} and the {sig.get('rule') or 'rule-based'} signal agree on {want}."
    return {"action": action, "rationale": why, "plan": plan_from(ev, action), "author": "rules"}


def rules_risk(ev: dict[str, Any], analysts: dict[str, Any], trader: dict[str, Any]) -> dict[str, Any]:
    action = trader["action"]
    missing = [n for n in SECTIONS if sec(ev, n)["status"] not in USABLE]
    stale = [n for n in SECTIONS if sec(ev, n)["status"] == "stale"]
    funding = sec(ev, "sentiment")["facts"].get("funding_1h_pct")
    if action == "WAIT":
        long_s, short_s = scenario(ev, "long"), scenario(ev, "short")
        trig = [f"{k}: {s['trigger_rule']}" for k, s in (("long", long_s), ("short", short_s)) if s and s.get("trigger_rule")]
        voices = {"aggressive": {"stance": "support", "concerns": ([f"waiting can miss the move ({trig[0]})"] if trig else [])},
                  "neutral": {"stance": "support", "concerns": ["no trigger has fired yet"] if trig else []},
                  "conservative": {"stance": "support", "concerns": [f"missing data: {', '.join(missing)}"] if missing else []}}
        return {**{k: {**v, "author": "rules"} for k, v in voices.items()},
                "summary": "All three support waiting: nothing is proposed.", "author": "rules"}
    against = _leaning(analysts, "bearish" if action == "LONG" else "bullish")
    concerns = [f"{', '.join(against)} lean{'s' if len(against) == 1 else ''} the other way"] if against else []
    if isinstance(funding, (int, float)) and ((action == "LONG" and funding > 0) or (action == "SHORT" and funding < 0)):
        concerns.append(f"funding {funding:g}% per hour is paid by the {action.lower()} side")
    cons = concerns + ([f"missing or stale data: {', '.join(sorted(set(missing + stale)))}"] if missing or stale else [])
    voices = {"aggressive": {"stance": "support", "concerns": concerns[:1]},
              "neutral": {"stance": "reduce" if concerns else "support", "concerns": concerns[:3]},
              "conservative": {"stance": "reject" if (missing or stale or against) else "reduce", "concerns": cons[:3]}}
    say = {"support": "supports", "reduce": "says reduce", "reject": "rejects"}
    st = {k: say[v["stance"]] for k, v in voices.items()}
    return {**{k: {**v, "author": "rules"} for k, v in voices.items()},
            "summary": f"Aggressive {st['aggressive']}, neutral {st['neutral']}, conservative {st['conservative']} the {action}.",
            "author": "rules"}


def what_changes(ev: dict[str, Any], action: str) -> list[str]:
    out = []
    plan = plan_from(ev, action)
    if action in ("LONG", "SHORT") and plan:
        s = scenario(ev, action.lower()) or {}
        out.append(f"Invalidated: {s.get('invalidation_rule') or 'close beyond ' + format(plan['stop'], 'g')}")
    for side in ("long", "short"):
        s = scenario(ev, side)
        if s and s.get("trigger_rule") and side.upper() != action:
            out.append(f"{side.capitalize()} setup: {s['trigger_rule']}")
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
        self.grounder = grounding.Grounder(ev, scenarios_view(ev), plan_from(ev, "LONG"), plan_from(ev, "SHORT"))

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
        rb = rules_rebuttals(reports)
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
