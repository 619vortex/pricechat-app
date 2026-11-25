"""Terminal Intelligence Engine, Phase 1: the gemma4:31b second opinion for borderline majors (app/intel/second_opinion). Pure functions,
not wired into the router or worker. Analyses below are shaped like qwen3.8:27b outputs from the Phase 1 eval (ops/intel/eval_v2.json)."""

from typing import Any

import pytest

from app.intel import second_opinion as so
from app.intel.schema import ItemAnalysis


def analysis(**kw: Any) -> dict[str, Any]:
    base = {"event_type": "conflict_military", "entities": ["Russia", "Poland"],
            "consequence": {"kind": "escalation", "stated_in_text": True, "magnitude": ""}, "materiality": "significant",
            "audiences": ["world"], "is_speculative": False, "is_opinion_or_feature": False,
            "claims": [{"text": "Russian missiles hit Poland", "attributed_to": "Polish government", "hedged": False, "model_inference": False}],
            "reason": "A strike on NATO territory."}
    return {**base, **kw}


def cons(kind: str, magnitude: str = "") -> dict[str, Any]:
    return {"kind": kind, "stated_in_text": True, "magnitude": magnitude}


@pytest.mark.parametrize("magnitude,size", [
    ("-4.6%", {"pct": 4.6}), ("Nasdaq −4.5%, S&P 500 -3.1%", {"pct": 4.5}), ("$44 billion", {"usd": 44e9}), ("$1.5 trillion", {"usd": 1.5e12}),
    ("$63 million", {"usd": 63e6}), ("$4.2bn", {"usd": 4.2e9}), ("19 killed", {"count": 19}), ("dozens dead", {"count": 24}), ("", {}),
])
def test_magnitude_size(magnitude: str, size: dict[str, float]) -> None:
    got = so.magnitude_size(magnitude)
    for k in ("pct", "usd", "count"):
        assert got[k] == pytest.approx(size.get(k, 0.0))


def test_large_consequences() -> None:
    assert so.is_large(cons("escalation"))  # an escalation needs no figure
    assert so.is_large(cons("price_move", "12%"))
    assert so.is_large(cons("deal", "$41 billion"))
    assert so.is_large(cons("deaths_injuries", "11 killed"))
    assert not so.is_large(cons("price_move", "2.5%"))
    assert not so.is_large(cons("money_amount", "$12 million"))
    assert not so.is_large(cons("deaths_injuries", "2 killed"))


def test_significant_with_large_hard_consequence_is_borderline() -> None:
    a = analysis()  # "Poland hit by at least one Russian missile, says Polish govt": significant escalation
    assert so.should_escalate(a)
    assert so.escalation_reasons(a)[0].startswith("significant with a large escalation")
    assert so.should_escalate(analysis(event_type="market_move", consequence=cons("price_move", "-5%")))
    assert so.should_escalate(analysis(event_type="merger_acquisition", consequence=cons("deal", "$41 billion")))


def test_routine_items_are_not_escalated() -> None:
    assert not so.should_escalate(analysis(consequence=cons("price_move", "3%"), event_type="market_move"))  # a 3% move stays primary
    assert not so.should_escalate(analysis(materiality="notable"))
    assert not so.should_escalate(analysis(materiality="noise", consequence=cons("none")))
    assert not so.should_escalate(analysis(consequence=cons("policy_change", "")))  # not a hard consequence kind
    assert not so.should_escalate(analysis(is_opinion_or_feature=True, materiality="notable"))
    assert not so.should_escalate(None)  # no primary analysis: router fallbacks, not a second opinion


def test_flags_suppressing_a_newsworthy_tier_are_borderline() -> None:
    # Phase 0's missed major: qwen marked "Russian missile hits Poland ..., says Tusk" speculative because it was attributed.
    a = analysis(materiality="major", is_speculative=True)
    assert so.escalation_reasons(a, policy="flags_only") == ["major suppressed by the speculative flag"]
    assert so.should_escalate(analysis(materiality="significant", is_opinion_or_feature=True, consequence=cons("price_move", "5%")),
                              policy="sig_hard_large+flags")
    assert not so.should_escalate(a)  # the default policy (sig_hard_large) leaves flag cases to the primary


def test_major_on_a_hedged_claim_is_borderline() -> None:
    a = analysis(materiality="major", claims=[{"text": "may have hit a warship", "attributed_to": "", "hedged": True, "model_inference": False}])
    assert so.escalation_reasons(a, policy="flags_only") == ["major resting on a hedged claim"]
    assert not so.should_escalate(analysis(materiality="major"), policy="flags_only")  # a clean major is decided by the primary alone


def test_rules_disagreement_only_in_its_own_policy() -> None:
    a = analysis(materiality="notable")
    rules = {"ok": True, "tier": "major"}
    assert not so.should_escalate(a, rules)  # the default policy ignores the rules
    assert so.escalation_reasons(a, rules, policy="rules_disagree") == ["rules say major / primary says notable"]
    assert not so.should_escalate(analysis(materiality="major"), {"ok": True, "tier": "major"}, policy="rules_disagree")


def test_default_policy_is_the_measured_one() -> None:
    assert (so.DEFAULT_POLICY, so.DEFAULT_COMBINE) == ("sig_hard_large", "promote")


def test_policies_are_consistent() -> None:
    assert so.DEFAULT_POLICY in so.POLICIES and so.DEFAULT_COMBINE in so.COMBINE_MODES
    for triggers in so.POLICIES.values():
        assert set(triggers) <= set(so.TRIGGERS)
    assert not so.should_escalate(analysis(materiality="major", is_speculative=True), policy="none")


def test_combine_without_second_is_the_primary_decision() -> None:
    d = so.combine(analysis(), None)
    assert d == {"tier": "significant", "newsworthy": True, "major": False, "source": "primary"}
    d = so.combine(analysis(materiality="major", is_speculative=True), None)
    assert d["newsworthy"] is False and d["major"] is False


def test_combine_promote_raises_only() -> None:
    prim, major = analysis(), analysis(materiality="major")
    assert so.combine(prim, major)["major"] is True
    assert so.combine(prim, major)["tier"] == "major"
    d = so.combine(major, analysis(materiality="notable"))  # the second model cannot demote under promote
    assert d["major"] is True and d["newsworthy"] is True
    d = so.combine(analysis(materiality="major", is_speculative=True), major)  # suppressed primary, clean second: promoted
    assert d == {"tier": "major", "newsworthy": True, "major": True, "source": "combined:promote"}
    d = so.combine(analysis(materiality="major", is_speculative=True), analysis(materiality="major", is_speculative=True))
    assert d["newsworthy"] is False and d["tier"] == "notable"


def test_combine_both_and_replace() -> None:
    prim, major = analysis(materiality="major"), analysis(materiality="major")
    assert so.combine(prim, major, "both")["major"] is True
    d = so.combine(prim, analysis(), "both")
    assert d["major"] is False and d["newsworthy"] is True and d["tier"] == "significant"
    d = so.combine(prim, analysis(materiality="noise", consequence=cons("none")), "replace")
    assert d == {"tier": "noise", "newsworthy": False, "major": False, "source": "second"}
    with pytest.raises(ValueError):
        so.combine(prim, major, "vote")


def test_accepts_item_analysis_objects() -> None:
    a = ItemAnalysis.model_validate(analysis(materiality="major", is_speculative=True))
    assert so.should_escalate(a, policy="flags_only")
    assert so.should_escalate(ItemAnalysis.model_validate(analysis()))
    assert so.combine(a, ItemAnalysis.model_validate(analysis(materiality="major")))["major"] is True


def test_major_check_escalates_every_clean_major() -> None:
    a = analysis(materiality="major")
    assert not so.should_escalate(a, policy="sig_hard_large")
    assert so.escalation_reasons(a, policy="major_check") == ["primary says major: confirm before a breaking post"]
    assert not so.should_escalate(analysis(materiality="significant", consequence=cons("policy_change")), policy="major_check")


def test_combine_arbiter_lets_the_second_decide_major_only() -> None:
    prim = analysis(materiality="major")
    d = so.combine(prim, analysis(), "arbiter")  # second says significant: demoted from breaking, still published
    assert d == {"tier": "significant", "newsworthy": True, "major": False, "source": "combined:arbiter"}
    assert so.combine(analysis(), analysis(materiality="major"), "arbiter")["major"] is True
    d = so.combine(analysis(materiality="notable"), analysis(materiality="noise", consequence=cons("none")), "arbiter")
    assert d["newsworthy"] is False and d["major"] is False
