"""Intelligent price milestones (owner, 2026-11-24). Production 2026-11-20 missed bitcoin breaking below $60,000: the crossing was stored as
a 1% move and judged routine. A level is judged by what it means in the asset's own record."""
from datetime import datetime, timedelta, timezone

from app.newsdesk import headline, importance, moves, sources

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
BTC = next(x for x in sources.DEFAULT_WATCHLIST if x["symbol"] == "BTC-USD")
NVDA = next(x for x in sources.DEFAULT_WATCHLIST if x["symbol"] == "NVDA")


def closes(values: list[float], end: datetime = NOW) -> list[tuple[datetime, float]]:
    return [(end.replace(hour=0) - timedelta(days=len(values) - i), v) for i, v in enumerate(values)]


def facts_for(m: moves.Move, series: list[tuple[datetime, float]]) -> dict:
    ms = moves.milestone_context(m, series, NOW)
    return {"kind": m.kind, "assets": [moves.KEYS.get(m.symbol, m.symbol)], "change_pct": 1.0, "milestone": ms,
            "round_level": moves.is_round(m.symbol, m.level, moves.step_of(m.item))}


def test_bitcoin_below_60000_for_the_first_time_in_months_is_major() -> None:
    # 300 days above $60,000, then the break on 2026-11-20
    series = closes([75000 - i * 40 for i in range(300)] + [61500])
    m = moves.Move("milestone", BTC, 59400.0, NOW, change=-1.0, level=60000.0)
    f = facts_for(m, series)
    assert f["round_level"] and f["milestone"]["since_days"] is None and f["milestone"]["span_days"] >= 300
    d = importance.judge("BITCOIN FALLS BELOW $60,000, NOW $59,400", "", kind="market", facts=f, age_min=5)
    assert d.tier == importance.MAJOR, d.reason
    # below every close in the record, so the record is the line ("lower than at any daily close since ..."), never a guess past it
    assert headline.milestone_line(m, f["milestone"]).startswith("Bitcoin is lower than at any daily close since ")
    f["milestone"]["record"] = False
    assert headline.milestone_line(m, f["milestone"]).startswith("Bitcoin has not closed below $60,000 since at least ")


def test_a_round_level_crossed_again_within_weeks_is_not_news() -> None:
    series = closes([62000.0] * 200 + [59000.0] * 5 + [61000.0] * 5)  # below $60,000 nine days ago
    m = moves.Move("milestone", BTC, 59400.0, NOW, change=-1.0, level=60000.0)
    f = facts_for(m, series)
    assert 5 <= f["milestone"]["since_days"] <= 10
    d = importance.judge("BITCOIN FALLS BELOW $60,000, NOW $59,400", "", kind="market", facts=f, age_min=5)
    assert d.tier == importance.NOISE and "within weeks" in d.reason
    assert headline.milestone_line(m, f["milestone"]).startswith("Bitcoin last closed below $60,000 on ")


def test_a_round_level_last_seen_months_ago_is_a_slot_story() -> None:
    series = closes([58000.0] * 100 + [64000.0] * 60)  # last below $60,000 sixty days ago
    m = moves.Move("milestone", BTC, 59400.0, NOW, change=-1.0, level=60000.0)
    d = importance.judge("BITCOIN FALLS BELOW $60,000, NOW $59,400", "", kind="market", facts=facts_for(m, series), age_min=5)
    assert d.tier == importance.SIGNIFICANT, d.reason


def test_a_new_high_past_every_close_in_the_record_is_major() -> None:
    series = closes([100000 + i * 10 for i in range(400)])
    m = moves.Move("milestone", BTC, 110500.0, NOW, change=1.0, level=110000.0)
    f = facts_for(m, series)
    assert f["milestone"]["record"]
    d = importance.judge("BITCOIN BREAKS ABOVE $110,000, NOW $110,500", "", kind="market", facts=f, age_min=5)
    assert d.tier == importance.MAJOR
    assert "higher than at any daily close since" in headline.milestone_line(m, f["milestone"])


def test_a_single_stock_s_level_never_breaks_on_its_own() -> None:
    series = closes([150.0 + i * 0.01 for i in range(400)])
    m = moves.Move("high", NVDA, 160.0, NOW)
    d = importance.judge("NVIDIA HITS A NEW 52-WEEK HIGH AT $160.00", "", kind="market", facts=facts_for(m, series), age_min=5)
    assert d.tier != importance.MAJOR


def test_round_numbers_are_the_ones_readers_use() -> None:
    assert moves.is_round("BTC-USD", 60000, 5000) and not moves.is_round("BTC-USD", 65000, 5000)
    assert moves.is_round("GC=F", 4000, 100) and not moves.is_round("GC=F", 4100, 100)
    assert moves.is_round("^GSPC", 7000, 250) and moves.step_of({"symbol": "^GSPC"}) == 250
    assert moves.step_of(BTC) == 5000  # the watchlist's own step wins


def test_without_a_daily_record_a_level_waits() -> None:
    m = moves.Move("milestone", BTC, 59400.0, NOW, change=-1.0, level=60000.0)
    assert moves.milestone_context(m, [], NOW) == {}
    d = importance.judge("BITCOIN FALLS BELOW $60,000, NOW $59,400", "", kind="market",
                         facts={"kind": "milestone", "assets": ["BTC"], "round_level": True}, age_min=5)
    assert d.tier == importance.SIGNIFICANT  # a round level: a slot at most, never breaking blind
