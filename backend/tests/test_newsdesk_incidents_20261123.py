"""One incident told in other words, and the low-value stories that filled slots (owner, 2026-11-23: "eliminate the duplicate pairs without
suppressing genuinely new developments" and "identify low-value stories that would unnecessarily fill slots"). Every headline is real, from
the 7-day backtest (tests/fixtures/nd_replay_7d.json). No test reaches a real service."""

import pytest

from app.newsdesk import importance, quality


@pytest.mark.parametrize(("a", "b", "hours"), [
    ("FBI crime database hacked: CNN", "USDA, FBI breached by hackers amid spate of tech outages – reports", 4),
    ("FBI crime database hacked: CNN", "FBI investigates apparent hack of critical crime database", 13),
    ("SEC: Eight Entities Used Fake Crypto Asset Trading Platforms to Steal More Than $37 Million From U.S. Investors",
     "SEC charges eight firms over fake crypto platforms tied to $37M fraud", 16),
    ("Anthropic Shares Jump 35% in Their Nasdaq Trading Debut", "Anthropic begins public trading at $2tn valuation after largest-ever IPO", 13),
    ("Middle East latest: Bahrain says civilian industrial facility damaged in Iran attack",
     "Two injured, one critically, in Iranian attack on industrial plant in Bahrain", 12),
    ("Ukrainian drone attack damages Crimea Bridge, shutting down road traffic for hours", "Russia’s Prized Kerch Bridge Struck In Ukrainian Drone Onslaught", 4),
])
def test_one_incident_in_other_words_is_one_incident(a: str, b: str, hours: float) -> None:
    assert quality.same_incident(a, b, hours)
    assert quality.incident_development(b, "", [a]) == ""  # nothing materially new: folded away


@pytest.mark.parametrize(("a", "b", "hours", "dev"), [
    ("Anthropic prices shares at $83, valuing the AI firm at $1.5 trillion on the eve of its IPO",
     "Anthropic Shares Jump 35% in Their Nasdaq Trading Debut", 1, "the first market move"),
    ("Binance Hack Wallet Moves Another $142 Million in ETH", "Binance receives Abu Dhabi court order to 'freeze' assets after $6.3bn heist", 20, "the sum is now"),
    ("US jets strike IRGC missile launch site on Iran's Qeshm Island", "Kuwait reports drone attacks as Iran, US trade attacks on Qeshm and Bahrain", 2,
     "drawn into the conflict"),
])
def test_a_real_development_of_the_incident_goes_out_as_its_update(a: str, b: str, hours: float, dev: str) -> None:
    assert quality.same_incident(a, b, hours)
    assert dev in quality.incident_development(b, "", [a])


@pytest.mark.parametrize(("a", "b", "hours"), [
    ("BBC reports from Kyiv bridge hit by Russian attack", "Ukraine missile strike severs Russia’s Crimean bridge", 10),  # two bridges
    ("SEC charges eight firms over fake crypto platforms tied to $37M fraud", "SEC sues Coinbase over staking", 20),  # two cases of one agency
    ("ADNOC Ship Attacked in Hormuz as Maritime Security Risks Rise", "US Strikes Iranian Missile Site after Bahrain Attacks", 5),
    ("Crimea bridge attacked again, Russian official says", "Ukrainian drone attack damages Crimea Bridge, shutting down road traffic", 60),  # days later
    ("CrowdStrike to buy Zscaler in $44 billion deal", "Chainflip confirms $12M hack, offers attacker 10% bounty", 30),
])
def test_different_incidents_stay_different(a: str, b: str, hours: float) -> None:
    assert not quality.same_incident(a, b, hours)


@pytest.mark.parametrize("title", [
    "‘They were keeping it for a rainy day’: Ukraine’s Kerch bridge attack is proof Kyiv still has long-range punch",  # analysis
    "BBC reports from Kyiv bridge hit by Russian attack",  # a correspondent's piece
    "Ethereum block builders received the most in a $3.4 million vault hack, data shows",  # a data write-up
    "Musk's control ambitions could drive Tesla-SpaceX merger, HSBC says",  # speculation
    "Israeli attacks on medics in occupied West Bank surge by 350%: Rights group",  # a report's statistic
])
def test_low_value_stories_never_fill_a_slot(title: str) -> None:
    assert importance.judge(title, outlets=3).tier == importance.NOISE


@pytest.mark.parametrize(("title", "most"), [
    ("US spot Bitcoin ETFs are almost $1 billion short of a record monthly outflow at $3.5 billion", importance.SIGNIFICANT),  # a sum, no event
    ("Goldman Sachs More Than Doubles Its Bitcoin ETF Holdings to $1.28 Billion", importance.NOTABLE),  # a filing
    ("S&P 500 is on track for the worst week since the April 2025 'Liberation Day' sell-off", importance.NOTABLE),  # a forecast
    ("Bitcoin Price Holds Near $65,000 as Nvidia Turmoil Drags Nasdaq Down 2.5%", importance.SIGNIFICANT),  # a 2.5% index day: not breaking
])
def test_these_never_break(title: str, most: str) -> None:
    assert importance.ORDER[importance.judge(title, outlets=3).tier] <= importance.ORDER[most]


@pytest.mark.parametrize("title", [
    "Nvidia reinstates Jensen Huang as CEO. Stock skyrockets 27%",
    "Missile strike hits Saudi oil terminal",
    "11 killed in Russian strike on Ukraine’s Dymer in Kyiv region",
])
def test_major_news_still_breaks(title: str) -> None:
    assert importance.judge(title, outlets=3).tier == importance.MAJOR
