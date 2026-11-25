"""Terminal Intelligence Engine, Phase 1: the second opinion for BORDERLINE MAJOR stories (pure functions; not wired into anything yet).

Owner decision 2026-11-25: qwen3.8:27b is the primary local analyser; gemma4:31b is asked again only when the primary's answer is a
borderline major. Measured on ops/intel/eval_v2.json (docs/intelligence/2026-11-25-phase1-eval.md), which also scores every policy below.

    reasons = escalation_reasons(primary, rules)       # why this item is a borderline major ([] = it is not)
    if should_escalate(primary, rules):                  # == bool(reasons) under the default policy
        second = <gemma4:31b analysis of the same item, same prompt and schema>
    decision = combine(primary, second)                  # second None (not asked, failed, timed out) -> the primary's own decision

How the PC worker would call it (NOT done in this change; production behaviour is unchanged): after the qwen intel.analyze job, the
worker calls should_escalate(analysis, rules_view) with the lane's rules verdict (app.intel.gate.rules_view); when True it runs the
same prompt on gemma4:31b (one model resident at a time: the swap costs a load, so escalations are batched per poll) and returns both
analyses; Railway calls combine() and records the reasons next to the shadow verdict. A gemma failure or timeout never blocks:
combine(primary, None) is the primary's decision.

Inputs are plain dicts (ItemAnalysis.model_dump(), app.intel.gate.rules_view()) or ItemAnalysis objects.
"""

from __future__ import annotations

import re
from typing import Any

from app.intel.schema import ItemAnalysis

# Triggers (each returns a reason string). The default policy is the one chosen in the Phase 1 eval; the others are kept for scoring.
HARD_KINDS = ("deaths_injuries", "escalation", "price_move", "supply_disruption", "service_disruption", "money_amount", "deal")
HARD_EVENTS = ("conflict_military", "attack_terror", "market_move", "hack_exploit", "outage_incident", "energy_supply_shipping",
               "merger_acquisition", "disaster_accident")

TRIGGERS = ("sig_hard_large", "sig_hard_event", "flag_suppressed", "major_hedged", "primary_major", "rules_disagree")
POLICIES: dict[str, tuple[str, ...]] = {
    "none": (),
    "sig_hard_large": ("sig_hard_large",),
    "sig_hard_large+flags": ("sig_hard_large", "flag_suppressed", "major_hedged"),
    "sig_hard_event+flags": ("sig_hard_event", "flag_suppressed", "major_hedged"),
    "flags_only": ("flag_suppressed", "major_hedged"),
    "major_check": ("primary_major", "flag_suppressed"),
    "major_check+sig_hard_large": ("primary_major", "sig_hard_large", "flag_suppressed"),
    "major_check+sig_hard_event": ("primary_major", "sig_hard_event", "flag_suppressed"),
    "rules_disagree": ("rules_disagree",),
    "all": TRIGGERS,
}
COMBINE_MODES = ("promote", "both", "arbiter", "replace")
DEFAULT_POLICY = "sig_hard_large"  # Phase 1 eval (test split): best major-as-major recall at ~13% escalated, F1 unchanged
DEFAULT_COMBINE = "promote"

_PCT = re.compile(r"([-+−–]?\d+(?:\.\d+)?)\s*%")
_MONEY = re.compile(r"\$\s?(\d+(?:[.,]\d+)?)\s*(trillion|tn|billion|bn|b\b|million|mn|m\b)?", re.I)
_COUNT = re.compile(r"(\d[\d,]*)\s*(?:people\s+)?(?:killed|dead|deaths|died|wounded|injured|casualties)", re.I)
_WORDS = {"dozens": 24, "hundreds": 200, "thousands": 2000}


def _as_dict(a: Any) -> dict[str, Any] | None:
    if a is None:
        return None
    if isinstance(a, ItemAnalysis):
        return a.model_dump()
    return dict(a)


def magnitude_size(magnitude: str) -> dict[str, float]:
    """The largest percent, US-dollar amount and casualty count written in a magnitude string (0 when absent)."""
    m = magnitude or ""
    pct = max((abs(float(x.replace("−", "-").replace("–", "-"))) for x in _PCT.findall(m)), default=0.0)
    usd = 0.0
    for num, unit in _MONEY.findall(m):
        v = float(num.replace(",", ""))
        u = (unit or "").lower()
        v *= 1e12 if u in ("trillion", "tn") else 1e9 if u in ("billion", "bn", "b") else 1e6 if u in ("million", "mn", "m") else 1
        usd = max(usd, v)
    count = max((float(x.replace(",", "")) for x in _COUNT.findall(m)), default=0.0)
    for w, n in _WORDS.items():
        if w in m.lower():
            count = max(count, n)
    return {"pct": pct, "usd": usd, "count": count}


def is_large(consequence: dict[str, Any]) -> bool:
    """A consequence the major rubric could reach: a 4%+ move, $10B+, 10+ casualties, or an escalation / disruption with no figure."""
    kind = consequence.get("kind", "none")
    size = magnitude_size(str(consequence.get("magnitude") or ""))
    if kind in ("escalation", "supply_disruption", "service_disruption"):
        return True
    return size["pct"] >= 4 or size["usd"] >= 1e10 or size["count"] >= 10


def decision(a: dict[str, Any] | None) -> dict[str, Any]:
    """The Phase 0 deterministic decision (app.intel.gate.decide / ops/intel/bench.py decide), on a dict."""
    if not a:
        return {"tier": "noise", "newsworthy": False, "major": False}
    newsworthy = a["materiality"] in ("major", "significant") and not a["is_speculative"] and not a["is_opinion_or_feature"]
    return {"tier": a["materiality"], "newsworthy": newsworthy, "major": newsworthy and a["materiality"] == "major"}


def escalation_reasons(primary: Any, rules: dict[str, Any] | None = None, policy: str = DEFAULT_POLICY) -> list[str]:
    """Why the primary's analysis is a borderline major under `policy` ([] = not borderline). rules = app.intel.gate.rules_view()."""
    a = _as_dict(primary)
    if not a:
        return []  # no primary analysis: the router's fallbacks apply, not a second opinion
    want = POLICIES[policy]
    mat, cons = a["materiality"], a.get("consequence") or {}
    spec, opin = bool(a["is_speculative"]), bool(a["is_opinion_or_feature"])
    hedged_claim = any(c.get("hedged") for c in a.get("claims") or [])
    out: list[str] = []
    if "sig_hard_large" in want and mat == "significant" and not opin and cons.get("kind") in HARD_KINDS and is_large(cons):
        out.append(f"significant with a large {cons.get('kind')} ({cons.get('magnitude') or 'no figure'})")
    if "sig_hard_event" in want and mat == "significant" and not opin and a.get("event_type") in HARD_EVENTS:
        out.append(f"significant {a.get('event_type')}")
    if "flag_suppressed" in want and mat in ("major", "significant") and (spec or opin):
        out.append(f"{mat} suppressed by the {'speculative' if spec else 'opinion/feature'} flag")
    if "major_hedged" in want and mat == "major" and not spec and not opin and hedged_claim:
        out.append("major resting on a hedged claim")
    if "primary_major" in want and mat == "major" and not spec and not opin:
        out.append("primary says major: confirm before a breaking post")
    if "rules_disagree" in want and rules is not None:
        r_major = rules.get("tier") == "major" and rules.get("ok")
        p = decision(a)
        if (r_major and not p["major"]) or (bool(rules.get("ok")) and not p["newsworthy"]):
            out.append(f"rules say {rules.get('tier')} / primary says {mat}")
    return out


def should_escalate(primary: Any, rules: dict[str, Any] | None = None, policy: str = DEFAULT_POLICY) -> bool:
    return bool(escalation_reasons(primary, rules, policy))


def combine(primary: Any, second: Any, mode: str = DEFAULT_COMBINE) -> dict[str, Any]:
    """Final decision {tier, newsworthy, major, source}. second=None (not escalated, failed, timed out): the primary's decision.

    promote: the second model can only RAISE: major if either says major (newsworthy), newsworthy if either is newsworthy.
    both:    major only if both say major; newsworthy if either is newsworthy.
    arbiter: the second model decides MAJOR (breaking) on its own; newsworthy if either is newsworthy.
    replace: the second model's decision replaces the primary's.
    """
    p = decision(_as_dict(primary))
    s_a = _as_dict(second)
    if s_a is None:
        return {**p, "source": "primary"}
    s = decision(s_a)
    if mode == "replace":
        return {**s, "source": "second"}
    newsworthy = p["newsworthy"] or s["newsworthy"]
    if mode == "promote":
        major = p["major"] or s["major"]
    elif mode == "both":
        major = p["major"] and s["major"]
    elif mode == "arbiter":
        major = s["major"]
    else:
        raise ValueError(f"unknown combine mode {mode!r}")
    tier = "major" if major else ("significant" if newsworthy and "significant" in (p["tier"], s["tier"]) else p["tier"])
    if newsworthy and tier not in ("major", "significant"):
        tier = "significant"
    if not newsworthy and tier in ("major", "significant"):
        tier = "notable"  # the flags suppressed it on both sides
    return {"tier": tier, "newsworthy": newsworthy, "major": major, "source": f"combined:{mode}"}
