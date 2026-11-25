"""The shared semantic gate (app/intel/gate) must not veto legitimate news, and must keep every accuracy protection (gate audit 2026-11-25).

The gate is in SHADOW today; it is meant to become the publishing gate, where a false veto is a missed story. In the Phase 0 replay
(ops/intel/gate_replay.py) the round-up rule was the only veto that removed stories the model had right: "Nasdaq sinks more than -4.5%,
is down for 2026, fueled by AI trade selloff" (major), two "Middle East latest: ..." live-page headlines and "Green Week Ahead? Nvidia
Futures Rip 6%, ..." (significant). Headlines marked "eval" are verbatim from ops/intel/eval_v1.json; "shape" marks a constructed headline
of a form the desk sees.

Every MUST-PASS case is judged with a model analysis that says major and the rules' own assessment (newsworthy.assess), as the lanes call
it: the gate may refuse only through a veto, so no veto may fire. Every MUST-BLOCK case is judged with a model that WRONGLY says major and
not speculative: the veto alone must stop it, so accuracy never rests on the model."""

from typing import Any

import pytest

from app.intel import classes, gate
from app.newsdesk import newsworthy


def major(title: str, **kw: Any) -> dict[str, Any]:
    base = {"event_type": "other", "entities": [], "consequence": {"kind": "escalation", "stated_in_text": True, "magnitude": ""},
            "materiality": "major", "audiences": ["markets", "world"], "is_speculative": False, "is_opinion_or_feature": False,
            "claims": [{"text": title, "attributed_to": "", "hedged": False, "model_inference": False}], "reason": "a stated major event"}
    return {**base, **kw}


def judge(title: str, **kw: Any) -> gate.SemanticVerdict:
    return gate.verdict(major(title, **kw), title, "", ["Reuters", "CNBC"], "", newsworthy.assess(title), model="test")


MUST_PASS = [
    # the majors of the Phase 0 eval set (eval)
    "Iran says three tankers hit mines in Hormuz despite U.S. escort, as Treasury widens sanctions",
    "Russian missile hits Poland as Ukraine's allies hold military exercise, says Tusk",
    "Nvidia reinstates Jensen Huang as CEO. Stock skyrockets 27%",
    "CrowdStrike to buy Zscaler in $44 billion deal as AI supercharges cyberattacks",
    "Cybersecurity stocks have been hot — and now CrowdStrike inks deal to buy Zscaler",
    "Nasdaq sinks more than -4.5%, is down for 2026, fueled by AI trade selloff",  # was a "round-up" (three comma clauses)
    "Nasdaq slumps more than 5% as fear gripping AI trade spreads across markets",
    "Anthropic hits $1.5 trillion valuation in record IPO as ECB warns AI funding could dry up",  # the hedge is in a trailing clause
    "France confirms its troops are in Ukraine. Russia says they are legitimate targets.",
    "Poland issues air raid alerts as Russia attacks Ukraine. Poland’s defense minister says two Russian missiles and a drone fell on "
    "Polish soil.",
    "US Strikes Iranian Missile Site after Bahrain Attacks",
    "Iran attacks Kuwait in widening escalation",
    "Major U.S. banks report widespread service outages",
    "Nvidia stock plunges 12%, rides longest weekly losing streak since 2008 financial crisis",
    "Nasdaq plunges 10% for the week after Friday's bloodbath",
    "Anthropic shares soar more than 34% to $2 trillion on Wall Street debut",
    # significant stories the old round-up rule refused (eval)
    "Middle East latest: Bahrain says civilian industrial facility damaged in Iran attack",
    "Middle East latest: Bahrain issues alert as Iran claims strike on US radar base",
    "Green Week Ahead? Nvidia Futures Rip 6%, Nasdaq and S&P 500 Futures Snap Back 2%",
    # "set to" is scheduling, not a forecast, in these significant stories (eval): the new lead-clause forecast check leaves it out
    "Anthropic set to start trading after completing largest-ever IPO",
    "AI rally set to add $2 trillion to Nasdaq-100 as Huang returns to Nvidia",
    # shapes a lexical rule could trip on
    "Ukraine blames Russia for missile strike that killed 20 in Kyiv",  # shape: "blames" next to a stated hard event is sourcing
    "Bitcoin hits record high above $150,000, its first since May",  # shape: "May" the month is not the hedge "may"
    "Exchange halts withdrawals after $1,200,000,000 hack, says CEO",  # shape: thousands separators are not list commas
    "Two injured, one critically, in Iranian attack on industrial plant in Bahrain",  # production 2026-11-22 (quality.CONTINUATION)
]


@pytest.mark.parametrize("title", MUST_PASS)
def test_legitimate_news_is_never_vetoed(title: str) -> None:
    v = judge(title)
    assert v.hard_veto == [], v.hard_veto
    assert v.newsworthy and v.major


def test_a_thousands_separator_is_not_a_round_up() -> None:
    # eval (tier notable, so the model decides it; the gate must not call one story a list)
    assert gate.roundup("Trump administration signed off nearly 900,000 Nvidia, AMD AI chips for China in six months") == ""


MUST_BLOCK = [
    # hedged, rumour or forecast lead: nothing has happened yet
    ("NATO warns Putin could strike before European army responds, points to US as military model", "hedged"),  # eval
    ("Bitcoin’s next crash may come from holders, not leverage, as $10.7 billion gets liquidated", "hedged"),  # eval
    ("FAA signed off on repairs before nationwide ground stop: report", "hedged"),  # eval: a report of a report
    ("Alice Huang in touch with Chinese spy agency during affair with Nvidia CEO: Report", "hedged"),  # eval
    ("Anthropic could hit $1.5 trillion valuation in IPO", "hedged"),  # shape
    ("Binance reportedly in talks to buy a US exchange", "hedged"),  # shape
    # pure forecasts
    ("Fed expected to cut rates by half a point in December", "hedged"),  # shape: FORECAST_LEAD
    ("Goldman forecasts Brent at $120 by year-end as supply tightens", "hedged"),  # shape
    ("Bitcoin poised to break $200,000 as ETF inflows surge", "hedged"),  # shape
    ("FTSE 100 Live: UK Stocks Set to Miss Out on AI Rebound", "not one story"),  # eval: a live page's preview
    # round-ups: several stories under one title
    ("Trump ousts top cybersecurity official; Anthropic's record debut", "not one story"),  # eval: "A; B"
    ("Anthropic raises more than $100B in IPO. And, Dems nominate Jeffries for House speaker", "not one story"),  # eval
    ("DOJ moves to recover $1.17 billion from Binance hack, Strategy buys another 457 BTC, and more", "not one story"),  # eval
    ("Iran missile sites under scrutiny, Cop31 climate talks collapse and GTA 6 prompts warnings in UAE", "not one story"),  # eval
    ("Midday Need to Know: Anthropic shares fall, consumer sentiment revised up & more", "not one story"),  # eval
    ("Week Ahead? Fed, CPI and Nvidia earnings", "not one story"),  # shape: a teaser kicker does not hide a list
    ("Nationwide ground stop lifted, Nvidia spy scandal, starship's longest test flight", "not one story"),  # News Desk post 118
    # features, explainers, reactions
    ("Microsoft had rallied back to record highs. Then came the election and Outlook outage", "not one story"),  # eval
    ("Bitcoin Crashes to $17,000 This Weekend: Inside the Bizarre Flash Crash Mystery", "not one story"),  # eval
    ("Crypto Stocks Rebound As Nvidia’s Earnings Beat Lifts Risk Appetite — Why COIN, CRCL, NXXT Are Moving Today", "not one story"),
    ("Invesco QQQ Is 14% Below Its October Record. Here's Where I Think It Will Be in 2030.", "not one story"),  # eval
    ("The S&P 500 Has More Than Tripled in 10 Years, Even After Its Latest Pullback. Here’s What $1,000 in VOO Could Have Become.",
     "not one story"),  # eval: curly apostrophe
    ("Markets react to Jensen Huang’s return", "not one story"),  # eval
    ("Countries race to secure COP31 deal after fossil fuel row", "not one story"),  # eval
    ("Trump blames the Fed as stocks slide", "not one story"),  # shape: "blames" with no hard event stays a reaction
]


@pytest.mark.parametrize("title,veto", MUST_BLOCK)
def test_speculation_round_ups_and_features_stay_blocked_even_when_the_model_says_major(title: str, veto: str) -> None:
    v = judge(title)
    assert v.consequence_ok  # the model (wrongly) said yes: only the veto stops it
    assert not v.newsworthy and not v.major
    assert any(x.startswith(veto) for x in v.hard_veto), v.hard_veto


def test_the_models_own_speculation_or_opinion_flag_still_refuses() -> None:
    title = "Nasdaq sinks more than -4.5%, is down for 2026, fueled by AI trade selloff"
    assert not judge(title, is_speculative=True).newsworthy
    assert not judge(title, is_opinion_or_feature=True).newsworthy


def test_a_lane_veto_still_wins_over_a_legitimate_major() -> None:
    title = "Iran says three tankers hit mines in Hormuz despite U.S. escort, as Treasury widens sanctions"
    v = gate.verdict(major(title), title, "", ["Reuters", "CNBC"], "", None, rules_ok=False, reason="already told on Telegram (post 201) 2 hours ago")
    assert not v.newsworthy and v.hard_veto == ["already told on Telegram (post 201) 2 hours ago"]


def test_a_passing_story_still_never_publishes_an_inferred_or_hedged_claim() -> None:
    # the gate letting the story through does not make the model's inference or a hedged claim publishable (app.intel.classes)
    title = "Iran attacks Kuwait in widening escalation"
    claims = [{"text": title, "attributed_to": "", "hedged": False, "model_inference": False},
              {"text": "Oil will spike as Gulf supply is cut", "attributed_to": "", "hedged": False, "model_inference": True},
              {"text": "Kuwait may retaliate", "attributed_to": "", "hedged": True, "model_inference": False}]
    v = judge(title, claims=claims)
    assert v.newsworthy
    by_text = {c["text"]: c for c in v.claims}
    assert by_text[title]["class"] == classes.VERIFIED
    assert by_text["Oil will spike as Gulf supply is cut"]["class"] == classes.INFERENCE
    assert by_text["Kuwait may retaliate"]["class"] == classes.SPECULATION
    assert by_text["Oil will spike as Gulf supply is cut"]["auto_post"] == by_text["Kuwait may retaliate"]["auto_post"] == "never"
