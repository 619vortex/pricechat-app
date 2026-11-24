"""The editor's judgement (app/newsdesk/importance) and the war-repeat fix (newsworthy.same_event), pinned on the production stories of
2026-11-16 .. 11-23 that the old gates refused or let through (docs/newsdesk/2026-11-23-diagnosis.md). No test reaches a real service."""

from datetime import datetime, timedelta, timezone

import pytest

from app.newsdesk import importance as imp
from app.newsdesk import newsworthy as nw

NOW = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("title", [
    "Nvidia reinstates Jensen Huang as CEO. Stock skyrockets 27%",
    "CrowdStrike to buy Zscaler in $44 billion deal as AI supercharges cyberattacks",
    "Chainflip confirms $12M hack, offers attacker 10% bounty",
    "Two injured, one critically, in Iranian attack on industrial plant in Bahrain",
    "Ukraine sinks Russian warship off Libya in latest expansion of the war",
    "11 killed in Russian strike on Ukraine’s Dymer in Kyiv region",
    "Wall Street sells off as Nasdaq enters correction",
])
def test_real_news_the_old_gates_refused_is_news(title: str) -> None:
    d = imp.judge(title, outlets=2, age_min=20)
    assert d.tier in (imp.SIGNIFICANT, imp.MAJOR), d.reason


@pytest.mark.parametrize("title", [
    "Trump Arrives in Asia Ahead of Summit with Xi, Putin",
    "The Xi-Trump summit has high stakes for toy stocks",
    "Athlete swims Strait of Hormuz to highlight Iran war's wildlife toll",
    "Gaza turned into ‘toxic wasteland’ by Israel’s war, says new report",
    "Trump calls on Nvidia board to rehire Jensen Huang after scandal",
    "Analysts say bitcoin price could test $100,000 next",
    "Citi and Circle to help World Bank study stablecoin payouts",
    "Brent falls below $90 a barrel for first time since August",
    "Nvidia NVDA stock up 3% as earnings beat Expectations. Neoclouds surge.",
    "Grand Theft Auto VI sets record with US$3.79 billion in first-day retail sales, Rockstar Games reports",
])
def test_what_is_not_news_stays_out(title: str) -> None:
    d = imp.judge(title, outlets=2, age_min=20)
    assert d.rank <= imp.ORDER[imp.NOTABLE] and (d.tier == imp.NOISE or "Grand Theft" in title), d.reason


def test_one_outlet_is_never_major_and_hedged_reports_lose_points() -> None:
    one = imp.judge("Missile strike hits Saudi oil terminal", outlets=1, age_min=10)
    two = imp.judge("Missile strike hits Saudi oil terminal", outlets=2, age_min=10)
    assert two.tier == imp.MAJOR and one.tier != imp.MAJOR and not one.verified
    assert imp.judge("Missile reportedly hits Saudi oil terminal", outlets=2).score < two.score


def test_a_measured_move_is_judged_against_its_own_asset_class() -> None:
    rally = imp.judge("US STOCKS RALLY", kind="market", facts={"assets": ["SPX", "IXIC", "DJI"], "change_pct": 5.4})
    small = imp.judge("BTC longs liquidated: $8 million in an hour", kind="market", facts={"assets": ["LIQ:BTC"]})
    year = imp.judge("Bitcoin vs the Nasdaq over the past year: −31.1% against +9.9%", kind="data", facts={"assets": ["BTC-USD", "^IXIC"]})
    assert rally.tier == imp.MAJOR and small.tier == imp.NOISE and year.rank < imp.ORDER[imp.MAJOR]


def test_only_exceptional_events_are_major() -> None:
    # owner, 2026-11-23: only a verified MAJOR event breaks; these are important but wait for the next slot
    assert imp.judge("Bybaz exchange says hackers drained $400 million from its hot wallet", outlets=2).tier == imp.SIGNIFICANT
    # owner, 2026-11-24: a routine strike inside an established war, no consequence stated, is a slot filler (notable), never major
    assert imp.judge("Russian drone attack on Kharkiv kills three", outlets=2).tier == imp.NOTABLE
    assert imp.judge("US jets strike IRGC missile launch site on Iran's Qeshm Island", outlets=2).tier == imp.MAJOR


def test_one_war_is_not_one_event() -> None:
    told = [nw.Told("telegram", "post 1", "Zelenskyy says Ukraine behind attacks on Russian ships near Libya", NOW - timedelta(hours=10),
                    nw.event_of("Zelenskyy says Ukraine behind attacks on Russian ships near Libya"))]
    assert nw.repeat_of("11 killed in Russian strike on Ukraine’s Dymer in Kyiv region", "", told, NOW) == ""
    assert nw.repeat_of("France confirms its troops are in Ukraine. Russia says they are legitimate targets.", "", told, NOW) == ""
    # the same attack told again is still a repeat
    assert nw.repeat_of("Ukraine sinks Russian warship off Libya in latest expansion of the war", "", told, NOW)


def test_the_decision_history_keeps_one_line_per_verdict() -> None:
    from types import SimpleNamespace

    ev = SimpleNamespace(facts={}, why="")
    d = imp.judge("Chainflip confirms $12M hack", outlets=2)
    imp.record(ev, d, "slot", "waiting", NOW)
    imp.record(ev, d, "slot", "waiting", NOW + timedelta(minutes=5))
    imp.record(ev, d, "release", "released", NOW + timedelta(minutes=10), "the 20:00 Dubai slot")
    assert [h["outcome"] for h in ev.facts["decisions"]] == ["waiting", "released"] and ev.facts["decisions"][0]["last_at"]
    assert ev.facts["editor"]["tier"] == d.tier and ev.why == "the 20:00 Dubai slot"
