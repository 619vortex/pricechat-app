"""News filter calibration and the "since" wording (2026-11-22).

Production 2026-11-21 08:02 UTC to 2026-11-22 06:00 UTC published no regular news: every slot was skipped because the newsworthy gate
refused all 25 candidates. Most were rightly refused (features, hedges, reactions, small hacks). Four real events were refused for want of a
rule: tankers hit in the Strait of Hormuz, a ship struck by drones in the Baltic (two headlines, one event), Bahrain's own account of an
Iranian strike, and "tech's worst week on Wall Street since 2020". The titles below are the production candidates, verbatim.

The market-data pause also held "The S&P 500's biggest one-day drop since November 2026: -3.7%". The figure was right (7,175.83 to
6,910.39 on 20 Nov); the bigger drop was 5 November (-4.36%), 15 days before, so the record framing was wrong and the words read as if the
mark were the current month."""
from datetime import datetime, timedelta, timezone

import pytest

from app.newsdesk import datapost, newsworthy as nw

PASS_NOW = [  # real events, two or three outlets each in production
    "Tankers hit in Strait of Hormuz as Iran tightens grip on shipping",
    "Bahrain says Iranian attack causes fire at industrial facility",
    "Russia says Kaliningrad-bound ship struck by drones in Gulf of Finland",
    "Russian cargo ship struck by drone in Baltic: Finnish authorities",
    "Anthropic’s record IPO collides with tech’s worst week on Wall Street since 2020",
]
STILL_REFUSED = [  # the other 20 production candidates: features, hedges, statements, small or unquantified hacks
    "Bitquery Traces at Least $1.58 Million in Fluidkey Hack",
    "Anthropic IPO fails to calm AI jitters as tech stocks plummet",
    "From Crimea to Kyiv, Bridges Become Targets in Russia’s War on Ukraine",
    "New Orleans Catholic clergy abuse survivors begin receiving payments from $305m bankruptcy settlement",
    "FBI's criminal information database hacked, CNN reports",
    "Larry McDonald survived Lehman’s collapse. Now he warns the credit market is signaling a growing risk",
    "BTCS Warns of Liquidation Risk on $54M Aave Debt",
    "USDA, FBI breached by hackers amid spate of tech outages – reports",
    "Kraken Says Trades Stand After Bitcoin’s CAD Plunge",
    "Market tumbles after tech stock sell-off",
    'Trump says FBI hack might expose Russia "hoax"',
    "Trump’s Putin Ties Draw Focus After Summit",
    "FBI national crime database suffers suspected breach: Reports",
    "Deadly attack in Kyiv as Russia launches barrage against Ukraine",
    "Chainflip Offers 10% Bounty for Return of Stolen Funds",
    "FBI investigates apparent hack of critical crime database",
    "Russia escalates attacks on Ukraine as concerns grow in Europe",
    "Athlete swims Strait of Hormuz to highlight Iran war's wildlife toll",
    "Fluidkey Says Unpatched Tool Enabled $1.6 Million Hack",
    "Chainflip confirms $12M hack, offers attacker 10% bounty",
]
MUST_REFUSE = [  # what the new rules must not let through: threats, hedges, warnings, prices, non-official speakers, ended events, nature
    "Iran says it could hit tankers in Strait of Hormuz",
    "Iran warns ships in Strait of Hormuz",
    "Iran threatens to attack tankers in Hormuz",
    "Houthis vow to attack ships in Red Sea",
    "Russia says it will strike ships in Black Sea",
    "Ukraine says Russia plans to attack ports",
    "Tanker insurance rates hit record in Strait of Hormuz",
    "Oil tanker hits record freight rates on Red Sea",
    "Trump says Iran attacked US base",
    "Analysts say tanker attacks in Red Sea could rise",
    "Shipping firms say Red Sea tankers rerouted",
    "Tankers hit in Strait of Hormuz last month, report says",
    "What we know about the tankers hit in the Strait of Hormuz",
    "Tanker seized in Strait of Hormuz released",
    "Ship struck by lightning in Baltic",
    "Ship struck iceberg in North Sea",
    "Worst week since 2008? Analysts weigh the risk",
    "Stocks could see worst week since 2020",
]
MUST_PASS = [
    "Houthis attack tanker in Red Sea",
    "Two tankers hit in Gulf of Oman",
    "Saudi defence ministry says Houthi missiles struck Aramco facility",
    "Wall Street suffers worst week since 2020",
]


@pytest.mark.parametrize("title", PASS_NOW + MUST_PASS)
def test_real_events_pass(title: str) -> None:
    a = nw.assess(title, "", age_min=30)
    assert a.ok, a.reason
    assert a.strength == "strong"


@pytest.mark.parametrize("title", STILL_REFUSED + MUST_REFUSE)
def test_filler_threats_and_hedges_stay_refused(title: str) -> None:
    a = nw.assess(title, "", age_min=30)
    assert not a.ok, a.why


def test_what_each_real_event_is_called() -> None:
    assert nw.assess(PASS_NOW[0], "", age_min=30).why == "shipping hit: Tankers hit (Strait of Hormuz)"
    assert nw.assess(PASS_NOW[1], "", age_min=30).why == "BAHRAIN and IRAN: a state strikes another"
    assert nw.assess(PASS_NOW[4], "", age_min=30).why.startswith("a market regime: worst week on Wall Street since 2020")


def test_only_a_state_or_an_official_body_makes_says_the_sourcing() -> None:
    assert nw.reported_clause("Bahrain says Iranian attack causes fire at industrial facility") == "Iranian attack causes fire at industrial facility"
    assert nw.reported_clause("Kraken Says Trades Stand After Bitcoin’s CAD Plunge") == ""
    assert nw.reported_clause("Iran says it could hit tankers in Strait of Hormuz") == ""  # hedged
    assert nw.reported_clause("Ukraine says Russia plans to attack ports") == ""  # not a hard event


def test_the_baltic_ship_is_one_event_in_two_headlines() -> None:
    a = nw.event_of(PASS_NOW[2])
    b = nw.event_of(PASS_NOW[3])
    assert nw.same_event(a, b)  # the repeat gate keeps the second off the channel


# --- "the biggest one-day drop since ..." ---------------------------------------------------------------------------------------------

def _series(last_at: datetime, last_pct: float, earlier_pct: float, earlier_days: int, n: int = 200) -> list[tuple[datetime, float]]:
    """Calm daily closes (+-0.4%), a bigger drop `earlier_days` before the last day, and the last day's move."""
    out, p = [], 7000.0
    for i in range(n, 0, -1):
        at = last_at - timedelta(days=i)
        p *= 1 + (earlier_pct if i == earlier_days else 0.004 * (1 if i % 2 else -1)) / (1 if i == earlier_days else 1)
        out.append((at, p))
    out.append((last_at, p * (1 + last_pct)))
    return out


def _anomaly(earlier_days: int) -> datapost.DataPost | None:
    last_at = datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)
    s = _series(last_at, -0.037, -0.0436, earlier_days)
    datapost.NAME.setdefault("^GSPC", "the S&P 500")
    quote = datapost.Last("close", s[-1][1], s[-2][1], last_at)
    return datapost.anomaly("^GSPC", s, last_at + timedelta(hours=9), quote)


def test_a_two_week_old_mark_is_no_record() -> None:
    assert _anomaly(15) is None  # production: 5 Nov -4.36%, 20 Nov -3.7%


def test_a_recent_record_names_the_day_and_an_old_one_the_month() -> None:
    p = _anomaly(120)
    assert p is not None and "since 23 July" in p.headline and "2026" not in p.headline
    old = datetime(2025, 4, 3, tzinfo=timezone.utc)
    assert datapost.since_words(old, datetime(2026, 11, 20, tzinfo=timezone.utc)) == "April 2025"
    assert datapost.since_words(datetime(2025, 12, 5, tzinfo=timezone.utc), datetime(2026, 11, 20, tzinfo=timezone.utc)) == "5 December 2025"
    assert datapost.since_words(datetime(2026, 9, 5, tzinfo=timezone.utc), datetime(2026, 11, 20, tzinfo=timezone.utc)) == "5 September"
