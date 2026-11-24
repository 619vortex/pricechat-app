"""Less routine war coverage (owner, 2026-11-24: "excessive war coverage, prioritizing financial and geopolitical consequences rather than
routine military developments"). Production's 16:00 UTC slot on 11-24 posted "Zelensky confirms Ukraine was behind drone attack on Russian
cargo ship in Gulf of Finland" while BlackRock's record $6B bitcoin ETF month waited."""
import pytest

from app.newsdesk import importance as imp

ROUTINE = [
    "Zelensky confirms Ukraine was behind drone attack on Russian cargo ship in Gulf of Finland",
    "Ukrainian drones strike two Russian oil refineries",
    "Russian attack kills two people in Ukraine's Zaporizhzhia",
    "Russian drone attack on Kharkiv kills three",
]
CONSEQUENCE = [
    "Ukrainian drones hit Russian refinery, halting 10% of national fuel output",
    "Russian strike hits Zaporizhzhia nuclear power plant",
    "Ukraine war: Russia and Ukraine agree to ceasefire talks in Istanbul",
    "Ukraine sinks Russian warship off Libya in latest expansion of the war",
    "11 killed in Russian strike on Ukraine’s Dymer in Kyiv region",
]
ESCALATION = [
    "Houthis attack tanker in Red Sea",
    "Russian missile attack on Kyiv kills 60",
    "Russian drones crash in Poland, NATO scrambles jets",
]


@pytest.mark.parametrize("head", ROUTINE)
def test_a_routine_strike_in_an_established_war_is_a_slot_filler(head: str) -> None:
    d = imp.judge(head, outlets=2)
    assert d.tier == imp.NOTABLE, d.reason


@pytest.mark.parametrize("head", CONSEQUENCE)
def test_a_war_story_with_a_stated_consequence_stays_a_slot_story(head: str) -> None:
    assert imp.judge(head, outlets=3).tier in (imp.SIGNIFICANT, imp.MAJOR)


@pytest.mark.parametrize("head", ESCALATION)
def test_an_escalation_still_breaks(head: str) -> None:
    assert imp.judge(head, outlets=3).tier == imp.MAJOR


def test_the_financial_story_now_outranks_the_routine_strike() -> None:
    money = imp.judge("BlackRock drives bitcoin ETFs to worst month on record as crypto fund withdrawals near $6 billion", outlets=3)
    war = imp.judge(ROUTINE[0], outlets=2)
    assert imp.ORDER[money.tier] > imp.ORDER[war.tier]


def test_zaporizhzhia_the_city_is_not_the_nuclear_plant() -> None:
    assert not imp.NUCLEAR_SITE.search("Russian attack kills two people in Ukraine's Zaporizhzhia")
    assert imp.NUCLEAR_SITE.search("Drone hits Zaporizhzhia nuclear power plant") and imp.NUCLEAR_SITE.search("IAEA: ZNPP lost external power")
