"""Breaking-news latency (production 2026-11-15/16). "Iran launches drone attacks against US military base in Bahrain", confirmed by
three outlets, read as "no hard event" and scored 60, so it waited 130 minutes for the 00:00 Dubai slot. A hard attack in a Gulf producer or
on US forces now breaks between slots; talk, threats, hedged reports and routine strikes with no market or escalation tie still do not.
Every fresh verified story the breaking lane holds back says why, once, in the logs."""
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.newsdesk import NdEvent
from app.newsdesk import editorial, events
from tests.test_newsdesk_breaking_e2e import DW, NATIONAL, _desk_tick, _report, live  # noqa: F401 - live is a fixture

IRAN = "Iran launches drone attacks against US military base in Bahrain"
BAHRAIN = "Bahrain intercepts Iranian aerial attacks as Tehran says it attacked US base"


def _score(title: str, outlets: int = 1) -> int:
    sig = editorial.significance(title)
    assert sig is not None, title
    return editorial.importance_score(sig, evidence="two outlets", outlets=outlets, outlets_3h=outlets, age_minutes=20, title=title).total


def test_production_attack_headlines_are_hard_events() -> None:
    for t in (IRAN, BAHRAIN, "Iran strikes US air base in Iraq", "Houthis launch missiles at tankers in the Red Sea",
              "Israel intercepts ballistic missiles fired from Yemen", "Russian drone strikes hit Ukraine's power grid"):
        assert editorial.hard_event(t), t
        assert editorial.breaking_worthy(t, "geopolitics", {"kind": "geopolitics"})[0], t


def test_talk_threats_and_hedged_reports_are_not() -> None:
    for t in ("Iran warns it may attack US bases in the Gulf", "Trump says strikes on Iran will continue", "Fears of missile strikes grow in Gulf",
              "Pentagon plans air strikes on Houthi targets", "Iran could launch drones at Bahrain, analysts say", "Iran threatens attack on US base"):
        assert not editorial.hard_event(t), t
        assert not editorial.breaking_worthy(t, "geopolitics", {"kind": "geopolitics"})[0], t


def test_a_gulf_or_us_forces_attack_with_two_outlets_reaches_the_breaking_bar() -> None:
    bar = events.POLICY.breaking_bar
    assert _score(IRAN) >= bar and _score(BAHRAIN) >= bar  # two outlets: 56 + the oil-region tie
    assert _score("Iran strikes US air base in Iraq") < bar <= _score("Iran strikes US air base in Iraq", outlets=2)  # escalation: three outlets
    # a routine strike with no market or escalation tie stays a slot story, however many outlets
    assert _score("Russian drone attack on Kharkiv kills three", outlets=3) < bar
    assert editorial.consequence(editorial.significance("Iran warns it may attack US bases in Bahrain"), "Iran warns it may attack US bases in Bahrain") == 0


async def test_the_bahrain_drone_attack_breaks_between_slots(client: Any, db: Session, live: dict[str, Any]) -> None:
    live["feeds"]["items"] = {
        DW["url"]: [_report(DW, IRAN, 18, "Iran targeted an air base in Bahrain in the first direct strike on the Gulf state in weeks.")],
        NATIONAL["url"]: [_report(NATIONAL, "Iran launches drone attack on US base in Bahrain", 9, "Drones targeted a US base in Bahrain, officials said.")],
    }
    await _desk_tick(db)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    assert ev.slot == "breaking" and "major geopolitical event" in ev.why and events.verified_important(ev)
    assert ev.facts["latency"]["event_to_release_min"] <= 20  # minutes, not the 150 of production


async def test_a_held_back_story_says_why_in_the_logs(client: Any, db: Session, live: dict[str, Any], caplog: Any) -> None:
    routine = "Russian drone attack on Kharkiv kills three"
    live["feeds"]["items"] = {
        DW["url"]: [_report(DW, routine, 18, "A Russian drone attack on Kharkiv killed three people, officials said.")],
        NATIONAL["url"]: [_report(NATIONAL, routine + ", officials say", 9, "Three people were killed in a drone attack on Kharkiv.")],
    }
    with caplog.at_level(logging.INFO, logger="app.newsdesk.events"):
        await _desk_tick(db)
        await _desk_tick(db)  # a second pass: the same reason is not logged again
    assert db.scalar(select(NdEvent.id).where(NdEvent.status == "released")) is None
    misses = [r.getMessage() for r in caplog.records if "not breaking" in r.getMessage()]
    assert len(misses) == 1 and "under the breaking bar" in misses[0] and "KHARKIV" in misses[0].upper()
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "candidate")).one()
    assert "under the breaking bar" in ev.facts["breaking_miss"]
