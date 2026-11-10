"""The 24-hour hard block, feed diversity and skip-not-fill (owner, 2026-11-10, after four "market check (Oil (WTI))" posts in a row on
Telegram: 153 at 00:03, 154 at 04:05, 155 at 08:05, 156 at 12:03 UTC). No test reaches a real service."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.members.referrals import program
from app.models.newsdesk import NdEvent
from app.newsdesk import editorial, events, recap, sources, telegram as lane

from tests.test_newsdesk_editorial import SLOT, decide, oil, sig


@pytest.fixture(autouse=True)
def _desk(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    monkeypatch.setattr(sources, "EXTRA_FEEDS", [])
    monkeypatch.setattr(lane, "FORMAT", "visual")
    monkeypatch.setattr(recap, "VISUAL", True)
    events._alerted.clear()
    program(db).updates_channel = "@Pricechatupdates"
    db.commit()


def test_the_market_check_fallback_is_off_and_admin_cannot_turn_it_on() -> None:
    assert events.RECAP_FALLBACK is False
    assert events.policy({}).recap is False
    assert events.policy({"policy": {"recap": True}}).recap is False


async def test_a_quiet_slot_is_skipped_never_filled_with_a_market_check(db: Session) -> None:
    for hours, pct in ((0, -4.4), (4, -5.2), (8, -6.0), (12, -5.6)):  # the 2026-11-10 channel, slot by slot
        now = SLOT + timedelta(hours=hours, minutes=1)
        assert await decide(db, now, oil(now, pct)) is None
        assert (now - timedelta(minutes=1)).isoformat() in events.skipped_slots(db)  # skipped on purpose, not missed (no owner alert)
    assert db.query(NdEvent).filter(NdEvent.status == "released").count() == 0


@pytest.mark.parametrize("pct", [5.2, 6.0, 5.6, 3.1])
def test_oil_s_24h_number_changing_is_never_a_new_story_within_24h(pct: float) -> None:
    covered = [editorial.Covered(sig(["OIL"], magnitude=4.4), SLOT, 153)]
    for hours in (4, 8, 12, 23):
        v = editorial.judge(sig(["OIL"], magnitude=pct), covered, SLOT + timedelta(hours=hours))
        assert not v.ok and v.same_as == 153
        assert not editorial.judge(sig(["OIL"], direction="up", magnitude=pct), covered, SLOT + timedelta(hours=hours)).ok  # nor the bounce


def test_a_price_move_on_a_subject_covered_as_news_today_is_blocked() -> None:
    covered = [editorial.Covered(sig(["OIL"], typ="news", direction=""), SLOT, 160)]
    v = editorial.judge(sig(["OIL"], magnitude=6.0), covered, SLOT + timedelta(hours=6))
    assert not v.ok and "isn't a new story" in v.reason
    assert editorial.judge(sig(["OIL"], magnitude=6.0), covered, SLOT + timedelta(hours=25)).ok  # the next day it can be


def test_only_a_major_escalation_or_a_verified_cause_gets_through() -> None:
    covered = [editorial.Covered(sig(["OIL"], magnitude=4.4), SLOT, 153)]
    later = SLOT + timedelta(hours=8)
    assert editorial.judge(sig(["OIL"], magnitude=9.5), covered, later).ok  # x2.2, into the 7.5% tier and beyond
    assert editorial.judge(sig(["OIL"], magnitude=6.5, cause=True), covered, later).ok  # "Oil falls 6.5% as OPEC+ ..." from an outlet
    assert not editorial.judge(sig(["OIL"], magnitude=8.0), covered, later).ok  # x1.8


def _cov(subjects: list[str], hours_ago: float, cls: str = "crypto_major", typ: str = "market") -> editorial.Covered:
    return editorial.Covered(sig(subjects, typ=typ, cls=cls), SLOT - timedelta(hours=hours_ago), None)


def _cand(title: str, topic: str = "move", kind: str = "news", assets: list[str] | None = None, category: str = "crypto") -> NdEvent:
    return NdEvent(key=title, kind=kind, title=title, headline=title, topic=topic, category=category, facts={"assets": assets or []})


def test_no_third_post_in_a_row_from_one_desk() -> None:
    covered = [_cov(["BTC"], 4), _cov(["ETH"], 8)]  # newest first
    pen, held = editorial.diversity(_cand("BITCOIN ETF OUTFLOWS HIT A RECORD", topic="etf", assets=["BTC"]), covered, SLOT)
    assert held and "crypto" in held and pen == 2 * editorial.DIVERSITY_PENALTY
    pen, held = editorial.diversity(_cand("FED HOLDS RATES", topic="macro", category="economics"), covered, SLOT)
    assert held == "" and pen == 0  # another desk: free, and first in line
    # a material update of a story already out is exempt
    assert editorial.diversity(_cand("BINANCE HALTS WITHDRAWALS", topic="hack", kind="update"), covered, SLOT) == (0.0, "")


def test_the_same_desk_ranks_lower_the_more_it_posted_today() -> None:
    covered = [_cov(["OIL"], 4, "commodity"), _cov(["BTC"], 8), _cov(["GOLD"], 12, "commodity")]
    pen, held = editorial.diversity(_cand("OIL JUMPS AFTER STRIKES ON TANKERS", topic="geopolitics", assets=["OIL"], category="commodities"), covered, SLOT)
    assert held == "" and pen == 2 * editorial.DIVERSITY_PENALTY


def test_each_desk_is_recognised() -> None:
    assert editorial.desk_of(_cand("x", assets=["BTC"])) == "crypto"
    assert editorial.desk_of(_cand("x", assets=["SPX"])) == "stocks"
    assert editorial.desk_of(_cand("x", assets=["GOLD"])) == "commodities"
    assert editorial.desk_of(_cand("CPI RISES", topic="macro", category="economics")) == "macro"
    assert editorial.desk_of(_cand("CEASEFIRE TALKS COLLAPSE", topic="geopolitics", category="politics")) == "geopolitics"


async def test_the_decisions_record_says_why_each_candidate_went_or_did_not(db: Session) -> None:
    db.add_all([
        NdEvent(key="a", kind="news", title="A", headline="A", topic="hack", status="released", why="the 04:00 Dubai slot: scores 70", released_at=SLOT),
        NdEvent(key="b", kind="recap", title="B", headline="OIL (WTI) SLIDES", topic="move", status="superseded", why="same story as #1 4 h ago"),
        NdEvent(key="c", kind="news", title="C", headline="C", topic="macro", status="candidate", why="scores 40, under the bar of 56"),
    ])
    db.commit()
    got = {d["headline"]: d for d in editorial.decisions(db, SLOT + timedelta(hours=1))}
    assert got["A"]["decision"] == "selected" and got["OIL (WTI) SLIDES"]["decision"] == "merged (repeat)" and got["C"]["decision"] == "waiting"
    assert "same story" in got["OIL (WTI) SLIDES"]["why"] and "under the bar" in got["C"]["why"]


# --- the lower bar for verified important news (owner, 2026-11-10: 52 for important stories confirmed by 2+ credible outlets, else 56) --


def _news(db: Session, key: str, title: str, score: float, outlets: list[str], *, evidence: str = "two outlets", topic: str = "geopolitics",
          importance: bool = True, kind: str = "news") -> NdEvent:
    facts: dict[str, Any] = {"told": title, "published_at": (SLOT - timedelta(minutes=20)).isoformat()}
    if importance:
        facts["importance"] = {"kind": topic, "why": "test"}
    ev = NdEvent(key=key, kind=kind, title=title, headline=title.upper(), summary="", topic=topic, evidence=evidence, category="politics",
                 virality=score, parts={"significance": 20}, status="candidate", first_seen=SLOT - timedelta(minutes=20),
                 expires_at=SLOT + timedelta(hours=4), facts=facts, sources=[{"name": o, "url": f"https://example.org/{o}/{key}"} for o in outlets])
    db.add(ev)
    db.commit()
    return ev


def test_which_stories_get_the_lower_bar() -> None:
    p = events.policy({})
    assert (p.regular_bar, p.verified_bar) == (56, 52)

    def mk(**kw: Any) -> NdEvent:
        base = {"kind": "news", "evidence": "two outlets", "topic": "geopolitics", "sources": [{"name": "BBC"}, {"name": "Financial Times"}],
                "facts": {"importance": {"kind": "geopolitics"}}}
        base.update(kw)
        return NdEvent(key="k", title="t", headline="T", **base)

    assert events.slot_bar(mk(), p) == 52
    assert events.slot_bar(mk(facts={}, topic="hack"), p) == 52  # a chaos topic, confirmed twice
    assert events.slot_bar(mk(sources=[{"name": "BBC"}]), p) == 56  # one outlet
    assert events.slot_bar(mk(sources=[{"name": "BBC"}, {"name": "bbc"}]), p) == 56  # one outlet through two of its feeds
    assert events.slot_bar(mk(evidence="attributed"), p) == 56
    assert events.slot_bar(mk(facts={}, topic="move"), p) == 56  # only a price moving: not "important"
    assert events.slot_bar(mk(kind="market", evidence="price data"), p) == 56
    assert events.slot_bar(mk(kind="recap", evidence="price data"), p) == 56


def test_a_verified_important_story_at_53_takes_the_slot(db: Session) -> None:
    ev = _news(db, "a", "Saudi Arabia's east-west pipeline struck in attack", 53, ["Financial Times", "OilPrice"])
    got = events.release(db, SLOT + timedelta(minutes=1), events.policy({}))
    assert got is not None and got.id == ev.id


@pytest.mark.parametrize(("outlets", "evidence", "importance", "topic"), [
    (["Financial Times"], "attributed", True, "geopolitics"),  # one outlet
    (["Financial Times", "OilPrice"], "two outlets", False, "move"),  # a price story
])
def test_the_same_score_without_independent_confirmation_or_importance_waits(db: Session, outlets: list[str], evidence: str, importance: bool,
                                                                             topic: str) -> None:
    ev = _news(db, "b", "Oil falls as traders weigh supply", 53, outlets, evidence=evidence, importance=importance, topic=topic)
    assert events.release(db, SLOT + timedelta(minutes=1), events.policy({})) is None
    assert "under the bar of 56" in (ev.why or "")


def test_under_52_still_waits_even_when_verified(db: Session) -> None:
    ev = _news(db, "c", "Poland's lower house backs sanctions bill covering crypto freezes", 51, ["Cointelegraph", "The Defiant"])
    assert events.release(db, SLOT + timedelta(minutes=1), events.policy({})) is None
    assert "under the bar of 52 (verified important news)" in (ev.why or "")


def test_the_lower_bar_never_lets_a_repeat_or_a_third_in_a_row_through(db: Session) -> None:
    for i, (title, subj) in enumerate((("Bitcoin ETF outflows hit record", "BTC"), ("Ethereum foundation sued", "ETH"))):
        db.add(NdEvent(key=f"old{i}", kind="news", title=title, headline=title.upper(), topic="etf", evidence="two outlets", status="released",
                       slot="slot", released_at=SLOT - timedelta(hours=4 * (i + 1)), facts={"assets": [subj]}))
    db.commit()
    for e in db.query(NdEvent).filter(NdEvent.status == "released"):
        from app.newsdesk import editorial as ed
        ed.stamp(e, e.released_at)
    db.commit()
    ev = _news(db, "d", "Bitcoin treasury firm halts withdrawals", 54, ["CoinDesk", "The Block"], topic="failure", importance=False)
    ev.facts = {**ev.facts, "assets": ["BTC"]}
    db.commit()
    assert events.release(db, SLOT + timedelta(minutes=1), events.policy({})) is None  # crypto twice in a row: diversity holds the third
    assert "feed diversity" in (ev.why or "")
