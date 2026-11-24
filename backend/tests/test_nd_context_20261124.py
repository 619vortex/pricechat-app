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


def test_one_war_is_many_events_in_the_desks_own_repeat_memory() -> None:
    # production 11-23: #1136 "Zelensky confirms strike on Russian warship in Libya" was superseded as "same story as #1053 47 h ago
    # (RUSSIA, UKRAINE, geopolitics)": the countries named, not the event
    from datetime import timedelta

    from app.newsdesk import editorial

    new = {"headline": "‘AN EXEMPLARY UKRAINIAN OPERATION’: ZELENSKY CONFIRMS STRIKE ON RUSSIAN WARSHIP IN LIBYA"}
    old = {"headline": "RUSSIAN MISSILE AND DRONE ATTACK ON KYIV KILLS THREE AND DAMAGES THREE DNIPRO BRIDGES"}
    assert not editorial._same_war_event(new, old, timedelta(hours=47))
    same = {"headline": "UKRAINIAN NAVAL DRONES SINK RUSSIAN WARSHIP IN LIBYA"}
    assert editorial._same_war_event(new, same, timedelta(hours=3))


CLASSIC = [  # the judgement must also hold for the kinds of event the desk exists for, not only last week's
    ("Fed cuts rates by 50 basis points in emergency move", {MAJOR}),
    ("Fed holds rates steady as expected", {SIG, NOT}),
    ("Israel strikes Iran nuclear site at Natanz", {MAJOR}),
    ("China fires missiles near Taiwan in largest drills", {SIG}),
    ("US says it will impose 50% tariffs on Chinese chips from Friday", {SIG, MAJOR}),
    ("Trump warns China of 100% tariffs if talks fail", {NOT}),
    ("Bitcoin claims $100,000 for the first time", {SIG, MAJOR}),
    ("Ethereum exchange Bybit hacked for $1.5 billion", {MAJOR}),
    ("S&P 500 plunges 6% in worst day since 2020", {MAJOR}),
    ("Poland Says Two Russian Missiles, Drone Crashed on Its Soil", {MAJOR}),
    ("India’s Oil Imports Hit Record High as Saudi Shipments Surge", {NOISE, NOT}),
    ("Market rout kills 50 basis points December Fed rate hike bets", {NOISE, NOT}),
    ("Anthropic’s record IPO collides with tech’s worst week on Wall Street since 2020", {SIG, NOT}),
]


@pytest.mark.parametrize("title,want", CLASSIC, ids=[c[0][:60] for c in CLASSIC])
def test_classic_events_get_an_editors_tier(title: str, want: set[str]) -> None:
    d = imp.judge(title, outlets=3)
    assert d.tier in want, d.reason


# the final 7-day replay of both lanes (11-17 -> 11-24, production backup of 11-24 10:46 UTC)
FINAL_REPLAY = [
    # an adjective is not an act: "convicted" names the person; the sum alone is a slot story
    ("Convicted FTX swindler Bankman-Fried misses out on $37B Anthropic IPO windfall", 4, {SIG, NOT}),
    ("Bankman-Fried convicted of fraud on all seven counts", 4, {SIG, MAJOR}),
    # a chart pattern is not a death
    ("Bitcoin Enters Death Cross as Anthropic IPO Fails to Lift Wall Street", 4, {NOISE, NOT}),
    # a hack's follow-up (funds moving, laundered, recovered) is not a new hack
    ("Binance Hack Wallet Moves Another $142 Million in ETH", 9, {SIG}),
    ("Binance hack laundering ramps up as $149 million in bitcoin mixed through coinjoins: Elliptic", 3, {SIG}),
    ("US moves to recover over $1B in crypto stolen by North Korean hackers in Binance heist", 8, {SIG}),
    # a % over four weeks is not a move that just happened
    ("Jito's JTO Falls 46% in 28 Days, Lagging BTC and ETH", 8, {NOT, NOISE}),
    # the % belongs to its own clause's subject
    ("Bitcoin rebounds 17% from $55K lows as Nvidia rally sparks $500M crypto liquidations", 21, {MAJOR}),
    # a person's fortune is the move's echo
    ("Jensen Huang’s Wealth Rises $36 Billion as Nvidia Surge Burns Short Sellers for $12 Billion", 3, {SIG}),
]


@pytest.mark.parametrize("title,outlets,want", FINAL_REPLAY)
def test_final_replay_headlines(title: str, outlets: int, want: set[str]) -> None:
    d = imp.judge(title, "", outlets=outlets)
    assert d.tier in want, (title, d.reason)


def test_a_crypto_move_is_the_crypto_s_own_percent() -> None:
    assert imp._pct_in_clause_of("Bitcoin rebounds 17% as Nvidia rallies", imp.CRYPTO_MAJOR)
    assert not imp._pct_in_clause_of("Bitcoin crashes to $59,000 as Anthropic falls 25%", imp.CRYPTO_MAJOR)
    d = imp.judge("Bitcoin crashes to $59,000 as Solana falls 25%", "", outlets=4)
    assert "bitcoin/ether move" not in d.reason


def test_a_mega_cap_s_day_move_of_ten_percent_is_major() -> None:
    # Nvidia -14.0% on 2026-11-20 (verified: Nasdaq official close 125.53 vs 145.98), its biggest drop since January 2025
    d = imp.judge("NVIDIA TUMBLES 14.0% IN 24 HOURS TO $125.53", "", kind="market", facts={"assets": ["NVDA"], "change_pct": -14.0})
    assert d.tier == MAJOR, d.reason
    # a stock outside the largest companies keeps the 20% line
    d = imp.judge("ACME TUMBLES 14.0% IN 24 HOURS TO $12.53", "", kind="market", facts={"assets": ["ACME"], "change_pct": -14.0})
    assert d.tier != MAJOR, d.reason
