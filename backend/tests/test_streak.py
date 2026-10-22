"""Daily consistency streak (Kiwi Passport). TEST FIXTURES only."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import missions, service, streak
from app.members.catalog import ensure_catalog
from app.models.members import Member, MemberMission, Mission, ReputationEvent


def _member(db: Session, name: str = "streaker") -> Member:
    ensure_catalog(db)
    m = service.create_member(db)
    m.username, m.username_key = name, name.lower()
    m.created_at = utcnow() - timedelta(days=30)
    db.commit()
    return m


def _xp(db: Session, m: Member) -> list[tuple[str, int]]:
    return [(e.day, e.xp) for e in db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == m.id, ReputationEvent.event_type == "streak.day")
                                                .order_by(ReputationEvent.day))]


def test_streak_grows_each_day_pays_once_per_day_with_milestones_and_resets_after_a_missed_day(db: Session) -> None:
    m = _member(db)
    t0 = utcnow().replace(hour=12) - timedelta(days=10)
    assert streak.on_task(db, m, t0) == 1
    assert streak.on_task(db, m, t0 + timedelta(hours=3)) is None  # a second task the same day: nothing more
    assert streak.on_task(db, m, t0 + timedelta(days=1)) == 2
    assert streak.on_task(db, m, t0 + timedelta(days=2)) == 3
    db.commit()
    assert [x for _, x in _xp(db, m)] == [5, 7, 9 + 15]  # +2 a day, day 3 milestone +15
    assert streak.on_task(db, m, t0 + timedelta(days=4)) == 1  # missed a day: starts again
    db.commit()
    assert [x for _, x in _xp(db, m)][-1] == 5
    s = streak.status(db, m, t0 + timedelta(days=4))
    assert s["current"] == 1 and s["best"] == 3 and s["done_today"] and s["next_reward"] == 7
    s = streak.status(db, m, t0 + timedelta(days=5))
    assert s["current"] == 1 and s["at_risk"] and not s["done_today"]  # yesterday counted: still alive, do a task today
    s = streak.status(db, m, t0 + timedelta(days=6))
    assert s["current"] == 0 and not s["at_risk"]  # a whole day missed: shown as ended
    assert [streak.day_reward(n) for n in (1, 2, 10, 11, 50)] == [5, 7, 23, 25, 25] and streak.reward(7) == 17 + 40


def test_completing_missions_counts_but_a_visit_alone_does_not(db: Session, client: TestClient) -> None:
    m = _member(db, "doer")
    visit = missions._upsert(db, dict(missions.STANDING[0]))  # the standing "Check in on Price Chat"
    task = missions._upsert(db, dict(key="test:watch", kind="watch_video", cadence="daily", title="Watch today's short", platform="website",
                                     description="TEST FIXTURE", verification="verified", priority=50, xp=3, verification_method="test"))
    db.commit()
    assert visit.kind == "daily_visit"
    now = utcnow()
    for mission in (visit, task):
        period = missions.period_for(mission.cadence, now)
        mm = MemberMission(member_id=m.id, mission_id=mission.id, period=period, status="available", progress=0)
        db.add(mm)
        db.flush()
        missions._reward(db, m, mission, period, mm)
        db.commit()
        if mission is visit:
            assert _xp(db, m) == []  # checking in isn't a task
    assert len(_xp(db, m)) == 1 and _xp(db, m)[0][1] == 5  # the real task started the streak
    assert db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.member_id == m.id,
                                                                            ReputationEvent.event_type == "streak.day")) == 1


def test_passport_shows_the_streak(db: Session, client: TestClient) -> None:
    from app.members import service as members

    m = _member(db, "shower")
    streak.on_task(db, m, utcnow())
    db.commit()
    client.cookies.set("pc_session", members.new_session(db, m, 30))
    s = client.get("/api/members/me").json()["streak"]
    assert s["current"] == 1 and s["done_today"] and len(s["days"]) == 14 and s["days"][-1]["today"] and s["days"][-1]["done"]
    assert s["next_milestone"] == 3 and s["next_milestone_bonus"] == 15
