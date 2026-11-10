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
    monkeypatch.setattr(sources, "WORLDMONITOR_FEEDS", [])
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


# --- held eligibility and breaking news (owner, 2026-11-10: important stories were losing their scores while they waited for a slot) --


def _news(db: Session, key: str, title: str, score: float, outlets: list[str], *, age_min: float = 20, now: Any = None, peak: float | None = None,
          evidence: str = "two outlets", topic: str = "geopolitics", importance: bool = True) -> NdEvent:
    now = now or SLOT
    facts: dict[str, Any] = {"told": title, "published_at": (now - timedelta(minutes=age_min)).isoformat(), "peak": peak or score}
    if importance:
        facts["importance"] = {"kind": topic, "why": "test"}
    ev = NdEvent(key=key, kind="news", title=title, headline=title.upper(), summary="", topic=topic, evidence=evidence, category="politics",
                 virality=score, parts={"significance": 20}, status="candidate", first_seen=now - timedelta(minutes=age_min),
                 expires_at=now + timedelta(hours=4), facts=facts, sources=[{"name": o, "url": f"https://example.org/{o}/{key}"} for o in outlets])
    db.add(ev)
    db.commit()
    return ev


def test_a_verified_important_story_keeps_its_best_score_fading_a_point_an_hour() -> None:
    p = events.policy({})
    ev = NdEvent(key="h", kind="news", topic="geopolitics", evidence="two outlets", virality=43, title="Ukraine peace talks resume",
                 sources=[{"name": "CNBC"}, {"name": "Bloomberg"}], facts={"importance": {"kind": "geopolitics"}, "peak": 67,
                                                                            "published_at": (SLOT - timedelta(hours=7)).isoformat()})
    assert events.held_score(ev, SLOT, p) == pytest.approx(64)  # 67, three hours past the first 4 h interval
    ev.facts = {**ev.facts, "published_at": (SLOT - timedelta(hours=11)).isoformat()}
    assert events.held_score(ev, SLOT, p) == 43  # past the 10 h hold: its own current score
    ev.facts = {**ev.facts, "published_at": (SLOT - timedelta(hours=7)).isoformat()}
    ev.sources = [{"name": "CNBC"}]
    assert events.held_score(ev, SLOT, p) == 43  # one outlet: not held


def test_a_held_story_is_not_expired_at_six_hours(db: Session) -> None:
    ev = _news(db, "x", "Ukraine peace talks resume this week in Abu Dhabi", 43, ["CNBC", "Bloomberg", "New York Times"], age_min=7 * 60, peak=67)
    ev.expires_at = SLOT - timedelta(hours=1)  # its 6 h news window is over
    db.commit()
    events._expire(db, [ev], SLOT, events.policy({}))
    assert ev.status == "candidate" and ev.expires_at > SLOT
    single = _news(db, "y", "Something reported once", 43, ["CNBC"], age_min=7 * 60, peak=67, evidence="attributed")
    single.expires_at = SLOT - timedelta(hours=1)
    events._expire(db, [single], SLOT, events.policy({}))
    assert single.status == "missed"


def test_the_held_story_takes_the_slot_it_would_have_missed(db: Session) -> None:
    ev = _news(db, "z", "Ukraine peace talks resume this week in Abu Dhabi", 43, ["CNBC", "Bloomberg", "New York Times"], age_min=7 * 60, peak=67)
    got = events.release(db, SLOT + timedelta(minutes=1), events.policy({}))
    assert got is not None and got.id == ev.id


BETWEEN = SLOT + timedelta(hours=2)  # 06:00 Dubai: no slot open, the next is two hours away


def test_major_breaking_news_confirmed_twice_goes_at_once(db: Session) -> None:
    ev = _news(db, "b1", "Missile strike hits Saudi oil terminal", 72, ["Financial Times", "Bloomberg"], age_min=25, now=BETWEEN)
    got = events.release(db, BETWEEN, events.policy({}))
    assert got is not None and got.id == ev.id and got.slot == "breaking" and "confirmed by 2 outlets" in got.why


@pytest.mark.parametrize(("why", "kw"), [
    ("one outlet", {"outlets": ["Financial Times"], "evidence": "attributed"}),
    ("not major", {"score": 60}),
    ("not breaking any more", {"age_min": 120}),
    ("only a price story", {"importance": False, "topic": "move"}),
])
def test_breaking_needs_two_outlets_a_major_score_freshness_and_importance(db: Session, why: str, kw: dict[str, Any]) -> None:
    args = {"outlets": ["Financial Times", "Bloomberg"], "score": 72, "age_min": 25, "evidence": "two outlets", "importance": True, "topic": "geopolitics"}
    args.update(kw)
    _news(db, "b2", "Missile strike hits Saudi oil terminal", args.pop("score"), args.pop("outlets"), now=BETWEEN, **args)
    assert events.release(db, BETWEEN, events.policy({})) is None, why


def test_breaking_waits_for_the_slot_when_one_is_near_and_never_crowds_another_post(db: Session) -> None:
    near = SLOT + timedelta(hours=3, minutes=30)  # 30 minutes before the next slot: the slot takes it
    _news(db, "b3", "Missile strike hits Saudi oil terminal", 72, ["Financial Times", "Bloomberg"], age_min=25, now=near)
    assert events.release(db, near, events.policy({})) is None
    db.query(NdEvent).delete()
    db.add(NdEvent(key="prev", kind="news", title="Fed holds rates", headline="FED HOLDS RATES", topic="macro", evidence="official", status="released",
                   slot="slot", released_at=BETWEEN - timedelta(minutes=60), facts={}))
    db.commit()
    _news(db, "b4", "Missile strike hits Saudi oil terminal", 72, ["Financial Times", "Bloomberg"], age_min=25, now=BETWEEN)
    assert events.release(db, BETWEEN, events.policy({})) is None  # 60 minutes after the last post: under the 90-minute gap


def test_at_most_two_breaking_posts_a_day(db: Session) -> None:
    for i in range(2):
        db.add(NdEvent(key=f"br{i}", kind="news", title=f"Earlier breaking {i}", headline=f"EARLIER BREAKING {i}", topic="hack", evidence="two outlets",
                       status="released", slot="breaking", released_at=BETWEEN - timedelta(hours=3 + i * 3), facts={}))
    db.commit()
    _news(db, "b5", "Missile strike hits Saudi oil terminal", 72, ["Financial Times", "Bloomberg"], age_min=25, now=BETWEEN)
    assert events.release(db, BETWEEN, events.policy({})) is None


def test_breaking_never_repeats_a_story_already_out(db: Session) -> None:
    old = NdEvent(key="o", kind="news", title="Missile strike hits Saudi oil terminal at Ras Tanura", headline="MISSILE STRIKE HITS SAUDI OIL TERMINAL",
                  topic="geopolitics", evidence="two outlets", status="released", slot="slot", released_at=BETWEEN - timedelta(hours=3), facts={})
    db.add(old)
    db.commit()
    from app.newsdesk import editorial as ed
    ed.stamp(old, old.released_at)
    db.commit()
    _news(db, "b6", "Missile strike hits Saudi oil terminal, Saudi Aramco says", 72, ["Financial Times", "Bloomberg"], age_min=25, now=BETWEEN)
    assert events.release(db, BETWEEN, events.policy({})) is None


# --- breaking at 70, financial / market / major geopolitical only; developments are new sourced facts (owner, 2026-11-10) -------------


def test_breaking_needs_seventy_now() -> None:
    assert events.policy({}).breaking_bar == 70


def test_69_does_not_break(db: Session) -> None:
    _news(db, "b7", "Missile strike hits Saudi oil terminal", 69, ["Financial Times", "Bloomberg"], age_min=25, now=BETWEEN)
    assert events.release(db, BETWEEN, events.policy({})) is None


@pytest.mark.parametrize("title", [
    "Pete Hegseth decries impeachment push, defends Iran war handling",  # broke at 67 in the 48 h replay
    "Trump says Iran war to end 'fairly soon'",
    "Ukraine peace talks face an uphill battle as winter approaches",
    "Senate hearing on Iran war turns into partisan row over troops",
])
def test_routine_political_controversy_or_talk_never_breaks(db: Session, title: str) -> None:
    _news(db, "b8", title, 75, ["Financial Times", "Bloomberg", "Reuters"], age_min=20, now=BETWEEN)
    assert events.release(db, BETWEEN, events.policy({})) is None
    assert not editorial.breaking_worthy(title, "geopolitics", {"kind": "geopolitics"})[0]


@pytest.mark.parametrize(("title", "topic"), [
    ("Missile strike hits Saudi oil terminal", "geopolitics"),
    ("US imposes sweeping sanctions on Russian oil exports", "geopolitics"),
    ("Binance halts withdrawals after $400 million hack", "hack"),
    ("US inflation jumps to 4.1%, highest in two years", "macro"),
])
def test_financial_market_or_hard_geopolitical_news_breaks(db: Session, title: str, topic: str) -> None:
    ev = _news(db, "b9", title, 74, ["Financial Times", "Bloomberg"], age_min=20, now=BETWEEN, topic=topic)
    got = events.release(db, BETWEEN, events.policy({}))
    assert got is not None and got.id == ev.id and got.slot == "breaking", title
    assert got.why.startswith("breaking (")


def _story(title: str, tier: int = 1, source: str = "Reuters") -> Any:
    from types import SimpleNamespace
    return SimpleNamespace(title=title, tier=tier, source=source, link="https://example.org/" + title[:20])


def _parent(title: str, evidence: str = "two outlets") -> NdEvent:
    return NdEvent(key="p", kind="news", title=title, evidence=evidence, facts={"told": title})


def test_a_development_is_a_new_reported_fact() -> None:
    parent = _parent("Nvidia board investigates Jensen Huang after spy report")
    assert events.development(_story("FBI investigating Nvidia boss over alleged Chinese spy"), parent) == "new: fbi"
    # a maybe is not a fact
    assert events.development(_story("FBI could investigate Nvidia boss, sources say"), parent) == ""
    assert events.development(_story("Will the FBI investigate Nvidia's Jensen Huang?"), parent) == ""
    assert events.development(_story("Nvidia boss reportedly set to be charged"), parent) == ""
    # "confirms" from an outlet repeating the story is not new; from an official source it is
    assert events.development(_story("Nvidia confirms board review of Jensen Huang"), parent) == ""
    assert events.development(_story("Nvidia confirms board review of Jensen Huang", tier=0, source="SEC"), parent) != ""


def test_a_development_already_told_in_an_earlier_update_is_not_new() -> None:
    parent = _parent("Nvidia board investigates Jensen Huang after spy report")
    story = _story("FBI widens investigation into Nvidia boss")
    assert events.development(story, parent) == "new: fbi"
    assert events.development(story, parent, ["Trump says FBI investigating Nvidia boss"]) == ""


def test_an_allowed_development_names_its_source_and_new_fact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editorial, "repeat_verdict", lambda *a, **k: editorial.Verdict(True, "a development of a story already out"))
    ev = NdEvent(key="u", kind="update", title="FBI investigating Nvidia boss", topic="corporate",
                 facts={"development": "new: fbi", "development_source": {"outlet": "Reuters", "headline": "FBI investigating Nvidia boss"}})
    assert events._repeat(ev, [], BETWEEN) is False
    assert ev.facts["new_because"] == 'a development of a story already out (new: fbi, reported by Reuters: "FBI investigating Nvidia boss")'
