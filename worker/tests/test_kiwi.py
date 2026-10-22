"""The Kiwi writer's hard gates (validators.py) and the fact card: pure code, no model."""

import sys
from pathlib import Path

import pytest

from pricechat_worker import kiwi, validators as v

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
factcard = pytest.importorskip("app.brand_manager.factcard")


def test_word_cap_counts_a_number_as_one_word() -> None:
    assert v.check_word_cap("April: $6,750. October: $17,225.") == (True, "")
    assert v.check_word_cap("one two three four five six seven")[0] is False


def test_banned_phrases_openers_and_hype() -> None:
    assert not v.check_banned_phrases("Here's why Bitcoin turned")[0]
    assert not v.check_banned_phrases("Let’s dive in")[0]  # curly apostrophes are normalised
    assert not v.check_opener("This card sold for $17,225")[0] and not v.check_opener("It’s just code")[0]
    assert v.check_opener("Italy's housing")[0]  # "Italy" isn't "It"
    assert not v.check_hype_words("Bitcoin is soaring")[0] and not v.check_hype_words("a massive sale")[0]


def test_number_present_allows_facts_derived_and_rounding_only() -> None:
    facts = ["Rocket's Mewtwo PSA 10 sold for $17,225 on October 12, 2026."]
    derived = ["2.6x the Apr 20, 2026 sale", "+160% since Apr 20, 2026"]
    assert v.check_number_present("$17K for one card", facts)[0]  # a rounding of 17,225
    assert v.check_number_present("2.6x April's price", facts, derived)[0]
    assert v.check_number_present("Now worth $25,000", facts)[1] == "number not in the facts: 25000"
    assert not v.check_number_present("The 451st buyer", ["450 were built."])[0]  # +1 arithmetic is not a rounding
    assert v.check_number_present("Down 9.3%", ["The close is 9.26% below it."])[0]
    assert v.check_number_present("A piece of cardboard", facts)[1] == "no number in the hook"


def test_report_verb_needs_a_qualifier() -> None:
    assert not v.check_report_verb("Rocket's Mewtwo sells for $17,225")[0]
    assert v.check_report_verb("Mewtwo sells for 2.6x April's price")[0]
    assert v.check_report_verb("$6,750 in April. $17,225 in October.")[0]


def test_format_repeat_duplicate_and_schema() -> None:
    hist = [{"hook": "A piece of cardboard. Worth $17,225.", "visual_format": "PRICE SLAM"}, {"hook": "x", "visual_format": "RECEIPT"}]
    assert not v.check_format_repeat("PRICE SLAM", hist)[0] and v.check_format_repeat("CHART DRAW", hist)[0]
    assert not v.check_duplicate("A piece of cardboard. Worth $16,000.", hist)[0]  # same formula, new number
    assert v.check_duplicate("$6,750 in April. $17,225 in October.", hist)[0]
    assert kiwi.pick_format("NUMBER SHOCK", hist) == "THEN/NOW"  # primary and alternate both used recently
    good = {"hook": "h", "shape": "STAKES", "on_screen_text": "s", "visual_format": "STAKE LINE", "first_3_seconds": "b", "caption": "c",
            "killed": [{"shape": "COMPARISON", "hook": "x", "reason": "r"}], "pick_reason": "p"}
    assert v.check_schema(good)[0]
    assert v.check_schema({**good, "visual_format": "CENTRED GIANT"})[1] == "unknown visual_format: CENTRED GIANT"
    assert v.check_schema({k: val for k, val in good.items() if k != "caption"})[1] == "missing field: caption"


def test_factcard_is_computed_from_the_brief_and_rounded() -> None:
    brief = {"headline": "Rocket's Mewtwo PSA 10 sold for $17,225",
             "facts": [{"text": "It is card 14 of 132 in the Gym Challenge set, released October 16, 2000."},
                       {"text": "PSA 10 sold for $17,225 on October 12, 2026."}],
             "numbers": {"sale_usd": 17225.0, "year": 2000},
             "chart": {"kind": "sales", "points": [[1776643200, 6750.0], [1780000000, 9000.0], [1791763200, 17225.41]]}}
    d = factcard.derive(brief)["derived"]
    assert "2.6x the Apr 20, 2026 sale" in d and "+160% since Apr 20, 2026" in d and "$10K more than the Apr 20, 2026 sale" in d
    assert "26 years after it came out" in d
    assert factcard.sig2(155.19) == 160 and factcard.sig2(2.5519) == 2.6 and factcard.money(3_396_296.0, "AED ") == "AED 3.4m"


def test_factcard_never_compares_across_periods() -> None:
    brief = {"numbers": {"transactions_aed_bn_2025": 917, "transactions_count_2025": 270000, "h1_2026_project_value_aed_bn": 100,
                         "investments_aed_bn_2025": 680}}
    d = factcard.derive(brief)["derived"]
    assert "about AED 3.4m per transaction on average" in d and "investments make up 74% of transactions value" in d
    assert not any("h1" in x.lower() and "transaction" in x.lower() for x in d)


def test_gate_kills_the_old_generic_hooks() -> None:
    facts = ["Rocket's Mewtwo (Gym Challenge, 2000) PSA 10 sold for $17,225 on October 12, 2026."]
    res = dict((n, ok) for n, ok, _ in v.gate({"hook": "This piece of cardboard from 2000 cost $17,225.", "on_screen_text": "October 12, 2026",
                                                 "caption": "", "shape": "NUMBER SHOCK"}, facts, [], []))
    assert res["opener"] is False and res["shape_signal"] is False


def test_the_hard_gate_and_the_fact_card_never_call_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from pricechat_worker import script2

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("a model / network call in the hard-gate path")

    monkeypatch.setattr(script2, "_call", boom)
    monkeypatch.setattr(httpx, "post", boom)
    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)
    brief = {"headline": "Nvidia closes at $217.09", "facts": [{"text": "NVDA closed at $217.09 on October 19, 2026."}],
             "numbers": {"price_usd": 217.09, "peak_close_usd": 239.24}, "chart": {"points": [[1777665600, 198.45], [1791316800, 239.24], [1792440000, 217.09]]}}
    derived = factcard.derive(brief)["derived"]
    cand = {"shape": "STAKES", "hook": "October 6 buyers are down 9.3%.", "on_screen_text": "Bought October 6? Down 9.3%.", "caption": "Nvidia closed at $217.09."}
    res = v.gate(cand, kiwi.facts_of(brief), derived, [{"hook": "x", "visual_format": "PRICE SLAM"}])
    assert all(ok for _, ok, _ in res), res
    assert kiwi.pick_format("STAKES", [{"visual_format": "STAKE LINE"}]) == "PRICE SLAM"
    assert v.check_schema({"hook": "h", "shape": "STAKES", "on_screen_text": "s", "visual_format": "PRICE SLAM", "first_3_seconds": kiwi.beat_sheet("PRICE SLAM", "h", "s"),
                           "caption": "c", "killed": [], "pick_reason": "p"})[0]


def test_period_mix_substance_and_currency_number_shock() -> None:
    facts = ["HEADLINE: Nvidia 9% below its October 6 peak after a $96.2B quarter",
             "NVIDIA reported revenue of $96.2 billion, up 106% from a year ago (quarter ended July 26, 2026).",
             "The highest close was $239.24 on October 6, 2026; the October 19 close of $217.09 is 9.26% below it."]
    assert v.check_period_mix("Revenue up 106%. Shares down 9.26%.", "CONTRADICTION", facts)[1] == "mixes two periods as one moment"
    derived = ["-9.3% from the Oct 6, 2026 high", "+9.4% since May 1, 2026"]
    assert not v.check_period_mix("Revenue up 106%. Stock down 9.3%.", "CONTRADICTION", facts + ["The window low was $190.01 on July 29, 2026."], derived)[0]
    assert v.check_period_mix("$239.24 on October 6. $217.09 on October 19.", "COMPARISON", facts)[0]  # one fact holds both
    mw = ["Price Chat recorded 22 sales since April 20, 2026, when one sold for $6,750.", "It sold for $17,225 on October 12, 2026."]
    assert v.check_period_mix("$6,750 in April. $17,225 in October.", "COMPARISON", mw)[0]  # then/now, both periods said
    assert not v.check_period_mix("$6,750 then $17,225 for the same card.", "COMPARISON", mw)[0]
    assert not v.check_hook_substance("$96.2 billion.")[0] and v.check_hook_substance("$96.2 billion, and the stock fell")[0]
    assert v.check_shape_signal("NUMBER SHOCK", "AED 917 billion of Dubai property in 2025")[0]
    assert v.amounts("PSA 10 on October 19, 2026 for $17,225, up 20%") == [17225.0, 20.0]


def test_caption_question_is_about_taste_not_money() -> None:
    for bad in ("Did you sell at the top or are you holding the bag?", "Who is holding the bag now?", "Do you hold or fold?", "Would you buy one?"):
        assert not v.check_caption_question("Nvidia closed at $217.09. " + bad)[0], bad
    assert v.check_caption_question("It sold for $17,225 on eBay. Which Gym Challenge card would you frame?")[0]


def test_loss_language_needs_a_decline_in_the_research() -> None:
    dubai = ["Dubai's market recorded 270,000 transactions worth AED 917 billion in 2025, up 20% year on year."]
    assert not v.check_unsupported_loss("The wrong side of the peak.", dubai)[0]
    assert not v.check_unsupported_loss("They held the bag.", ["450 Veyrons were built."])[0]
    nvda = ["The October 19 close of $217.09 is 9.26% below the October 6 high."]
    assert v.check_unsupported_loss("Wrong side of the peak.", nvda)[0]
    assert v.check_unsupported_loss("Somebody sold too early.", dubai)[0]


def test_the_storys_trend_words_reach_the_hook_writer() -> None:
    job = {"topic": "Stablecoin bill", "brief": {"headline": "Stablecoin bill passes"}, "trends": ["Stablecoin", "GENIUS Act"]}
    user = kiwi._user(job, ["Stablecoin bill passes the Senate."], [])
    assert user.endswith("TRENDING NOW (words many finance headlines use today, and they are in this story): Stablecoin, GENIUS Act. "
                         "Put one in the hook or the on-screen text if it fits naturally. It adds no facts.")
    assert "TRENDING" not in kiwi._user({**job, "trends": []}, ["x"], [])
