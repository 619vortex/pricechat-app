"""Trending Topics calibration (owner, 2026-11-15: "63 overnight scans produced zero qualifying trends"). Real headlines from production's
2026-11-14/15 scans, with the outlet counts and timings the scan logs measured, labelled by what they are:
hot = genuinely hot, verified, on our beat: reaches the automatic line (75) on its own merits (the News Desk coordination still decides);
mid = worth a suggestion, not automatic; weak = never a topic (sport, TV, reviews, features, crime that mentions gold, royals);
stale = first reported long ago with nothing new: capped below the suggestion line. The thresholds are NOT lowered: the scoring is."""

from datetime import datetime, timedelta, timezone

import pytest

from app.trending import rank

NOW = datetime(2026, 11, 15, 5, 0, tzinfo=timezone.utc)
_id = [0]


def sig(title, outlet, pub_min, seen_min=None, source="news", reliable=True, eng=2.0, **extra):
    _id[0] += 1
    seen = NOW - timedelta(minutes=pub_min if seen_min is None else seen_min)
    return rank.Sig(id=_id[0], source=source, title=title, outlet=outlet, reliable=reliable, engagement=eng, first_seen=seen, last_seen=NOW,
                    published_at=NOW - timedelta(minutes=pub_min), extra=extra)


def google(query, traffic, pub_min, article=None, history=None):
    out = [sig(query, "Google Trends", pub_min, source="google", reliable=False, eng=traffic, query=query, group=f"google:{query}", geo="US")]
    out[0].history = history or []
    if article:
        out.append(sig(article[1], article[0], pub_min, source="google", reliable=True, kind="article", group=f"google:{query}"))
    return out


CASES = {
    "HOT btc -14% week": ("hot", [sig("Bitcoin price falls 14% in a week to $65,000 as weekend sell-off deepens", "CoinDesk", 200),
                                  sig("Bitcoin falls 14% in a week to $65,000 in weekend sell-off", "Decrypt", 150),
                                  sig("Bitcoin sinks to $65,000, down 14% in a week, as sell-off deepens", "Bloomberg", 60),
                                  sig("Bitcoin's weekend sell-off deepens: price falls 14% in a week to $65,000", "Cointelegraph", 30)]),
    "MID trump putin xi (no market words)": ("mid", [sig("Trump, Putin and Xi plan historic joint meeting in China next month", "Reuters", 100),
                                   sig("Trump, Putin and Xi to hold historic joint meeting in China next month", "Bloomberg", 80),
                                   sig("Trump, Putin and Xi plan joint meeting in China, a historic first", "AP", 50)]),
    "HOT iran us site": ("hot", [sig("Iran targets US military site in Gulf for first time in two months", "Reuters", 90),
                                 sig("Iran targets US military site in the Gulf for the first time in two months", "Al Jazeera", 70),
                                 sig("Iran targets a US military site in the Gulf, first time in two months", "BBC", 40)]),
    "HOT fed collins": ("hot", [sig("Fed's Collins says 'steady, gradual' rate hikes appropriate", "Bloomberg", 100),
                                sig("Fed's Collins says steady, gradual rate hikes are appropriate", "Reuters", 60),
                                sig("Fed's Collins: steady, gradual rate hikes appropriate", "CNBC", 30)]),
    "BREAKING hack (first scan)": ("hot", [sig("Exchange Bybaz halts withdrawals after $400 million hack", "CoinDesk", 35),
                                           sig("Bybaz halts withdrawals after $400 million hack", "The Block", 25),
                                           sig("Crypto exchange Bybaz halts withdrawals after $400 million hack", "Reuters", 15)]),
    "MID xrp 2 outlets": ("mid", [sig("XRP crashes to its lowest since November 2024 as money leaves crypto", "CoinDesk", 90),
                                  sig("XRP crashes to lowest since November 2024 as money leaves crypto", "Decrypt", 40)]),
    "MID binance liq 1 outlet": ("mid", [sig("Binance liquidations near $800 million as Bitcoin sinks", "CoinDesk", 170)]),
    "MID apple ipad 3 outlets": ("mid", [sig("Apple launches a new iPad mini with a faster chip", "The Verge", 90), sig("Apple launches new iPad mini with faster chip", "TechCrunch", 60),
                                         sig("Apple's new iPad mini gets a faster chip", "CNBC", 30)]),
    "MID solana validators 3 crypto": ("mid", [sig("Solana's retired validators still vote with 15% of active stake", "CoinDesk", 100),
                                               sig("Retired Solana validators still vote with 15% of active stake", "The Block", 70),
                                               sig("Solana's retired validators still control 15% of active stake votes", "Decrypt", 40)]),
    "MID oil steady 3 outlets": ("mid", [sig("Oil prices steady as traders weigh supply outlook", "Reuters", 100), sig("Oil steady as traders weigh supply outlook", "Bloomberg", 70),
                                         sig("Oil prices hold steady as traders weigh the supply outlook", "CNBC", 40)]),
    "BREAKING all in one scan": ("hot", [sig("Exchange Lumora halts withdrawals after $250 million exploit", "CoinDesk", 30, seen_min=0),
                                         sig("Lumora halts withdrawals after $250 million exploit", "Reuters", 20, seen_min=0),
                                         sig("Crypto exchange Lumora halts withdrawals after $250 million exploit", "Bloomberg", 10, seen_min=0)]),
    # exceptional off-beat stories (owner, 2026-11-15): kept as topics for financial impact or global significance; momentum still decides
    "EXC louvre heist (global)": ("mid", [sig("Thieves steal crown jewels from the Louvre in daylight heist", o, m) for o, m in
                                          (("Reuters", 55), ("AP", 50), ("BBC", 45), ("CNN", 40), ("The Guardian", 30), ("New York Times", 20))]),
    "EXC club sale (financial)": ("mid", [sig("Los Angeles Lakers sold for $10 billion in record sports deal", "CNBC", 60),
                                          sig("Lakers sold for $10 billion, a record sports deal", "Bloomberg", 40),
                                          sig("Los Angeles Lakers sold in record $10 billion deal", "ESPN", 20, reliable=True)]),
    "WEAK tennis final (6 outlets, a result)": ("weak", [sig("Sinner beats Alcaraz to win the ATP Finals title", o, m) for o, m in
                                                         (("Reuters", 55), ("AP", 50), ("BBC", 45), ("CNN", 40), ("The Guardian", 30), ("New York Times", 20))]),
    "WEAK celebrity tour (3 outlets)": ("weak", [sig("Taylor Swift announces new tour dates for 2027", o, m) for o, m in (("BBC", 50), ("CNN", 40), ("The Guardian", 30))]),
    "WEAK tennis": ("weak", [sig("Pegula saves match point to beat Noskova and reach WTA Finals semi-finals", "BBC", 60),
                             sig("Pegula saves match point to beat Noskova at WTA Finals", "The Guardian", 50),
                             sig("Pegula beats Noskova after saving match point at WTA Finals", "Reuters", 30)]),
    "WEAK love island": ("weak", [sig("Maya Jama steps down as host of reality TV show Love Island", "BBC", 60),
                                  sig("Maya Jama steps down as Love Island host", "The Guardian", 40)]),
    "WEAK tv review": ("weak", [sig("Heartland review – Jessica Chastain's Netflix country music drama", "The Guardian", 60)]),
    "WEAK anthropic feature": ("weak", [sig("Meet the seven co-founders of Anthropic", "Fortune", 40),
                                        sig("Meet the seven co-founders of Anthropic, the AI startup", "Business Insider", 30),
                                        sig("Meet the seven people who co-founded Anthropic", "Forbes", 20)]),
    "WEAK gold theft": ("weak", [sig("This man stole $9 million in gold bars on the Upper West Side", "New York Times", 100),
                                 sig("Man stole $9 million in gold bars from Upper West Side apartment", "CNN", 60)]),
    "WEAK princess": ("weak", google("catherine, princess of wales", 20000, 60)),
    "STALE btc 8h": ("stale", [sig("Bitcoin falls below $66,000 as sell-off deepens", "CoinDesk", 520), sig("Bitcoin falls below $66,000 in sell-off", "Decrypt", 480),
                               sig("Bitcoin drops below $66,000 as sell-off deepens", "Bloomberg", 430)]),
}




def _score(sigs: list[rank.Sig]) -> tuple[float, dict]:
    cs = rank.cluster(sigs)
    if not cs:
        return 0.0, {}
    return rank.score(max(cs, key=lambda c: len(c.signals)), NOW)


@pytest.mark.parametrize("name", list(CASES))
def test_calibration(name: str) -> None:
    label, sigs = CASES[name]
    sc, parts = _score(sigs)
    if label == "hot":
        assert sc >= 75, (name, sc, parts.get("why"))
    elif label == "mid":
        assert 45 <= sc < 75, (name, sc, parts.get("why"))
    elif label == "weak":
        assert sc < 45, (name, sc, parts.get("why"))
    else:
        assert parts.get("stale") and sc <= rank.STALE_CAP, (name, sc, parts.get("why"))


def test_breaking_qualifies_on_the_first_scan_that_sees_it() -> None:
    sc, parts = _score(CASES["BREAKING all in one scan"][1])
    assert sc >= 75 and "burst" in parts["growth"]["evidence"] and parts["growth"]["observed_min"] == 0


def test_niche_coverage_counts_less_than_a_crossover() -> None:
    _, niche = _score(CASES["MID solana validators 3 crypto"][1])
    _, cross = _score(CASES["BREAKING hack (first scan)"][1])
    assert niche["growth"]["mainstream"] is False and cross["growth"]["mainstream"] is True and niche["velocity"] < cross["velocity"]


def test_an_exceptional_off_beat_story_is_a_topic_but_must_still_earn_the_line() -> None:
    cs = rank.cluster(CASES["EXC louvre heist (global)"][1])
    assert len(cs) == 1 and cs[0].exception and cs[0].exception.startswith("global significance: 6 independent outlets")
    sc, parts = rank.score(cs[0], NOW)
    assert parts["exception"] and "off-beat but exceptional" in parts["why"] and sc < 75  # world news, no market impact: a suggestion
    [deal] = rank.cluster(CASES["EXC club sale (financial)"][1])
    assert deal.category is not None  # a $10 billion deal is a topic either way (here its own words already classify it)
    assert rank.cluster(CASES["WEAK tennis final (6 outlets, a result)"][1]) == []
    assert rank.money("Thieves take EUR jewels worth €88 million") == 88e6 and rank.money("a $1.2 billion fine") == 1.2e9 and rank.money("no sums") == 0


def test_a_crime_with_a_huge_sum_is_kept_for_its_financial_impact() -> None:
    heist = [sig("Thieves stole $600 million in gold bars in record heist", "Reuters", 50), sig("Record heist: thieves stole $600 million in gold bars", "BBC", 30)]
    [c] = rank.cluster(heist)
    assert c.exception == "financial impact: $600M"
    assert rank.cluster(CASES["WEAK gold theft"][1]) == []  # $9 million: an ordinary crime story
