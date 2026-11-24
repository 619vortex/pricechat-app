"""Who did what, not which words (owner, 2026-11-24): production's News Desk candidates of 2026-11-23 06:15 -> 11-24 06:15 UTC, replayed.

Each case is a real headline with the outlet count production saw, and the tier an editor would give it. Before this change the desk
refused the day's biggest stories ("no concrete consequence": Nvidia +27%, a $44B takeover, tankers ablaze in Hormuz) and PR #16's first
judgement misread the rest by keyword (a warning scored as an attack, a rescue as major, "targets shadow banking" as a US strike).
"""
from __future__ import annotations

import pytest

from app.newsdesk import importance as imp

MAJOR, SIG, NOT, NOISE = imp.MAJOR, imp.SIGNIFICANT, imp.NOTABLE, imp.NOISE

CASES = [
    # market-moving facts the old gate refused
    ("Nvidia reinstates Jensen Huang as CEO. Stock skyrockets 27%", 3, {MAJOR}),
    ("Middle East latest: IRGC says three tankers ablaze in Strait of Hormuz", 2, {MAJOR}),
    ("CrowdStrike to buy Zscaler in $44 billion deal as AI supercharges cyberattacks", 2, {SIG, MAJOR}),
    ("U.S. adds Megaspeed to trade blacklist after Nvidia scandal", 3, {SIG}),
    ("NATO to Send AWACS Plane to Poland After Russian Missiles Crash", 2, {SIG}),
    ("US escalates sanctions on Iran, targets ‘shadow banking’ networks", 2, {SIG}),
    ("Spot bitcoin ETFs shed $1.8 billion in past week as outflows become ‘structural’", 2, {SIG}),
    ("Binance hackers push $247 million through transactions designed to cloud the source", 2, {SIG}),
    # a party's claim of its own action: never major until confirmed
    ("Yemen's Houthis claim attacks on Saudi oil refinery as Trump warns of new strikes", 2, {SIG}),
    # strikes inside an established war: the next slot, not breaking
    ("11 killed in Russian strike on Ukraine’s Dymer in Kyiv region", 2, {SIG}),
    ("Ukrainian drones strike two Russian oil refineries and pumping station in overnight attack", 2, {SIG}),
    ("Ukraine sinks Russian warship off Libya in latest expansion of the war", 2, {SIG}),
    # background is not the news; a stance reports no event; human interest is never above a slot filler
    ("Child rescued in Ukraine after Russian drone attack kills 10", 2, {NOT}),
    ("Boy, 7, pulled alive from rubble hours after Russian strike on Ukraine, rescuers say", 2, {NOT}),
    ("Russia warns Ukraine of ‘dangerous trend’ after Libya and Baltic attacks", 2, {NOT, NOISE}),
    ("UN warns Black Sea drone attacks threaten food exports as Russia-Ukraine war widens", 2, {NOT, NOISE}),
    ("Zelenskyy praises ‘exemplary’ drone operation that sank Russian warship in the Mediterranean", 2, {NOT, NOISE}),
    ("Trump doubles down on call for Nvidia to rehire Jensen Huang amid alleged China ties", 2, {NOISE}),
    # threats: consistent, at most a slot filler
    ("Iran vows to end U.S. naval blockade by \"all available means\"", 2, {NOT}),
    ("Iran says it will use all means to end U.S. naval blockade", 2, {NOT}),
    ("Russia threatens ‘hostile’ Estonia with ‘open war’", 2, {NOT}),
    # consistent tellings of one story
    ("Capita boss exits and shares crash after civil service pensions failures", 1, {NOT}),
    ("Capita shares plunge and boss steps down as civil service pensions saga drags on", 2, {NOT}),
    # one exchange's book is not the market; a look back is not news; an analyst's view is opinion
    ("Kraken Won’t Reverse Trades From a 71% Bitcoin Drop on Its Canadian Dollar Book", 2, {NOT, NOISE}),
    ("Microsoft Outage Review Finds Gaps in Server Testing", 1, {NOISE}),
    ("Stocks soared, but the VIX is still at crisis level, says SA Analyst", 1, {NOISE}),
    ("Bitcoin Price Fades Near $61,000 as Stocks Soar on Huang’s Return", 1, {NOT, NOISE}),
    # talks and plans: nothing decided yet
    ("SpaceX and Tesla entrusts five directors for merger talks, FT reports", 2, {NOT, NOISE}),
    ("Tokenized stocks face pushback as NYSE, Nasdaq pivot for a new market", 2, {NOT, NOISE}),
    # the event is in the "after" clause: it counts, but never as major on its own
    ("Chainflip founder admits heavy losses after second hack in 10 weeks drains $12 million", 1, {NOT, SIG}),
    # official acts
    ("Reuters: Circle, World Liberty Ordered to Keep Tokens Frozen", 1, {NOT, SIG}),
    ("CFTC staff advisory says crypto can count toward reserves, not liquidity rule", 2, {NOT, SIG}),
]


@pytest.mark.parametrize("title,outlets,want", CASES, ids=[c[0][:60] for c in CASES])
def test_production_headlines_of_11_23_get_an_editors_tier(title: str, outlets: int, want: set[str]) -> None:
    d = imp.judge(title, outlets=outlets)
    assert d.tier in want, d.reason


def test_the_parts_of_a_headline() -> None:
    s = imp.structure("Child rescued in Ukraine after Russian drone attack kills 10")
    assert s.main == "Child rescued in Ukraine" and s.context == "Russian drone attack kills 10" and not s.stance
    s = imp.structure("Russia threatens ‘hostile’ Estonia with ‘open war’")
    assert s.stance == "threatens" and s.threat == imp.STATED_THREAT and s.main == ""
    s = imp.structure("Middle East latest: IRGC says three tankers ablaze in Strait of Hormuz".split(": ", 1)[1])
    assert not s.stance and not s.claimed and "tankers ablaze" in s.main
    assert imp.structure("Yemen's Houthis claim attacks on Saudi oil refinery").claimed
    s = imp.structure("Nvidia reinstates Jensen Huang as CEO. Stock skyrockets 27%")
    assert "skyrockets 27%" in s.main and not s.context  # "as CEO" is not a background clause; two sentences, both reported


def test_no_daily_count_on_major_news_only_a_burst_guard() -> None:
    # owner, 2026-11-24: "Do not impose an arbitrary daily limit on genuinely major breaking news"
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from app.newsdesk import events

    now = datetime(2026, 11, 23, 22, 0, tzinfo=timezone.utc)
    spread = [SimpleNamespace(facts={"major": "x"}, released_at=now - timedelta(hours=2 * i + 1)) for i in range(10)]  # ten majors in a day
    assert events.major_burst(spread, now) == ""
    burst = [SimpleNamespace(facts={"major": "x"}, released_at=now - timedelta(minutes=10 * i + 5)) for i in range(4)]
    assert "unusual burst" in events.major_burst(burst, now)
    assert events.MAJOR_PER_DAY is None
