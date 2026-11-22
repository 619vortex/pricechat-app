"""A parenthetical aside is not a round-up (production 2026-11-22, 16:00 Dubai slot skipped).

"Two injured, one critically, in Iranian attack on industrial plant in Bahrain" (score 73, two outlets) was refused as "a round-up
of several stories": three comma parts. A part opening with a preposition continues the sentence. Real round-ups stay refused."""
import pytest

from app.newsdesk import newsworthy, quality

ONE_STORY = [
    "Two injured, one critically, in Iranian attack on industrial plant in Bahrain",
    "Three killed, dozens hurt, at chemical plant blast near Houston",
    "Tanker seized, crew detained, off Oman coast by Iranian forces",
    "Two Injured, One Critically, In Iranian Attack On Industrial Plant In Bahrain",  # Title Case outlets
    "Trump Downplays Microsoft, JPMorgan Outages, Saying 'It's Life'",  # the 11-17 case, still one story
]
ROUNDUPS = [
    "Nationwide ground stop lifted, Nvidia spy scandal, starship's longest test flight",  # News Desk post 118
    "Bitcoin slides, gold rallies, oil steady as traders weigh Fed",
    "Apple earnings beat, Tesla recalls cars, Meta faces EU fine",
    # a preposition opening a MIDDLE part does not make a list one story (owner review of PR #12)
    "Fed cuts rates, Over 100 killed in Kenya floods, Apple earnings beat",
    "Oil jumps, On Wall Street stocks slide, Gold record high",
]


@pytest.mark.parametrize("title", ONE_STORY)
def test_one_story_with_an_aside_is_not_a_roundup(title: str) -> None:
    assert not quality.is_roundup(title)
    assert newsworthy.structural_weak(title) is None


@pytest.mark.parametrize("title", ROUNDUPS)
def test_real_roundups_stay_refused(title: str) -> None:
    assert quality.is_roundup(title)
