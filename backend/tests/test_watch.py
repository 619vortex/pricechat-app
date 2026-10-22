"""Watch & Earn: videos played inside the Kiwi Passport. XP only after the configured play time, never faster than the
clock, once per video, through the same mission / content-view rewards the rest of the site uses."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import missions, reputation, watch
from app.models.members import MemberAction, ReputationEvent
from tests.test_members import app_and_client, fake_tg, google_sign_in, mc, providers  # noqa: F401  (fixtures)
from tests.test_missions import member, short


def _rewind(db: Session, m_id: int, vid: str, seconds: int) -> None:
    """Pretend Watch was pressed `seconds` ago."""
    a = db.scalar(select(MemberAction).where(MemberAction.member_id == m_id, MemberAction.kind == watch.WATCH_KIND,
                                             MemberAction.target == f"content:youtube:{vid}"))
    assert a is not None
    a.created_at = utcnow() - timedelta(seconds=seconds)
    db.commit()


def test_lists_newest_videos_with_the_latest_as_the_mission(db: Session) -> None:
    mem = member(db)
    for i, vid in enumerate(["v1", "v2", "v3", "v4", "v5"]):
        short(db, vid, hours_ago=1 + i * 24)
    missions.generate(db)
    out = watch.videos(db, mem)
    assert out["threshold_seconds"] == 30
    vids = out["videos"]
    assert [v["external_id"] for v in vids] == ["v1", "v2", "v3", "v4"]  # featured + 3 recent
    assert vids[0]["latest"] and vids[0]["xp"] == 3 and vids[0]["status"] == "available"
    assert all(not v["latest"] and v["xp"] == 1 for v in vids[1:])  # website.content_view
    assert vids[0]["title"] == "He Put His Allowance Into Bitcoin"  # hashtags tidied for display


def test_xp_needs_the_play_time_and_the_clock(db: Session) -> None:
    mem = member(db)
    short(db, "v1")
    missions.generate(db)
    assert watch.complete(db, mem, "v1", 60)["code"] == "not_started"
    watch.start(db, mem, "v1")
    assert watch.complete(db, mem, "v1", 60)["code"] == "too_soon"  # the browser says 60 s, but only ~0 s passed
    _rewind(db, mem.id, "v1", 40)
    assert watch.complete(db, mem, "v1", 12)["code"] == "too_soon"  # time passed, but the player only played 12 s
    r = watch.complete(db, mem, "v1", 31)
    assert r == {"ok": True, "code": "paid", "xp": 3, "message": "Counted.", "combo": 1, "bonus": 0}  # the day's 1st: normal XP
    ev = db.scalar(select(ReputationEvent).where(ReputationEvent.member_id == mem.id, ReputationEvent.event_type == "mission.complete"))
    assert ev is not None and ev.reason.startswith("Played 30 seconds of") and "watched" not in ev.reason.lower()
    assert watch.complete(db, mem, "v1", 31)["code"] == "already"  # never twice
    assert watch.videos(db, mem)["videos"][0]["status"] == "completed"


def test_watch_combo_pays_double_then_double_plus_five(db: Session) -> None:
    mem = member(db)
    for i, vid in enumerate(["v1", "v2", "v3", "v4"]):
        short(db, vid, hours_ago=1 + i * 24)
    missions.generate(db)
    assert [v["xp"] for v in watch.videos(db, mem)["videos"]] == [3, 1, 1, 1]  # nothing watched yet: normal XP
    got = []
    for vid in ["v1", "v2", "v3", "v4"]:
        watch.start(db, mem, vid)
        _rewind(db, mem.id, vid, 31)
        for e in db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == mem.id)):  # real watching takes 30 s+
            e.occurred_at = e.occurred_at - timedelta(minutes=1)
        db.commit()
        r = watch.complete(db, mem, vid, 31)
        got.append((r["xp"], r["combo"], r["bonus"]))
        if vid == "v2":
            out = watch.videos(db, mem)
            assert out["combo"] == {"today": 2, "next": "double +5"} and out["videos"][2]["xp"] == 1 + 1 + 5  # the preview shows it
    assert got == [(3, 1, 0), (2, 2, 1), (7, 3, 6), (7, 4, 6)]  # normal, double, double +5, double +5
    assert watch.complete(db, mem, "v4", 31)["code"] == "already"  # a video never pays (or combos) twice
    bonus = list(db.scalars(select(ReputationEvent.xp).where(ReputationEvent.member_id == mem.id, ReputationEvent.event_type == "watch.combo")))
    assert sorted(bonus) == [1, 6, 6]


def test_a_short_shorter_than_the_threshold_needs_only_its_length(db: Session) -> None:
    mem = member(db)
    item = short(db, "v1")
    item.duration_seconds = 18
    db.commit()
    assert watch.videos(db, mem)["videos"][0]["needed_seconds"] == 17
    watch.start(db, mem, "v1")
    _rewind(db, mem.id, "v1", 20)
    assert watch.complete(db, mem, "v1", 17)["code"] == "paid"


def test_other_videos_share_the_homepage_content_view_reward(db: Session) -> None:
    mem = member(db)
    short(db, "old", hours_ago=24 * 30)  # too old for the mission: a plain video
    watch.start(db, mem, "old")
    _rewind(db, mem.id, "old", 31)
    r = watch.complete(db, mem, "old", 30)
    assert r["code"] == "paid" and r["xp"] == 1
    refs = list(db.scalars(select(ReputationEvent.source_ref).where(ReputationEvent.member_id == mem.id,
                                                                    ReputationEvent.event_type == "website.content_view")))
    assert refs == ["content:youtube:old"]  # the same key the homepage open uses: one payment per video, ever
    assert watch.videos(db, mem)["videos"][0]["status"] == "completed"


def test_threshold_is_configurable(db: Session) -> None:
    mem = member(db)
    short(db, "v1")
    reputation.settings(db).watch_seconds = 10
    db.commit()
    watch.start(db, mem, "v1")
    _rewind(db, mem.id, "v1", 11)
    assert watch.complete(db, mem, "v1", 10)["code"] == "paid"


def test_suspended_members_can_watch_but_earn_nothing(db: Session) -> None:
    mem = member(db)
    short(db, "old", hours_ago=24 * 30)
    mem.xp_suspended = True
    db.commit()
    assert watch.videos(db, mem)["videos"][0]["status"] == "no_xp"


def test_api_requires_a_member_and_a_real_public_video(mc: TestClient, db: Session) -> None:  # noqa: F811
    assert mc.get("/api/members/me/watch").status_code == 401
    google_sign_in(mc)
    short(db, "v1")
    assert mc.post("/api/members/me/watch/start", json={"external_id": "nope"}).status_code == 404
    assert mc.post("/api/members/me/watch/start", json={"external_id": "v1"}).json()["ok"] is True
    assert mc.post("/api/members/me/watch/complete", json={"external_id": "v1", "played_seconds": 30}).json()["code"] == "too_soon"
    assert mc.post("/api/members/me/watch/complete", json={"external_id": "v1", "played_seconds": -1}).status_code == 422
    assert mc.get("/api/members/me/watch").json()["videos"][0]["external_id"] == "v1"
    r = mc.put("/api/members-admin/reputation", json={"watch_seconds": 45})
    assert r.status_code == 200 and r.json()["settings"]["watch_seconds"] == 45
    assert mc.put("/api/members-admin/reputation", json={"watch_seconds": 1}).status_code == 422


def test_a_video_playing_across_midnight_utc_still_counts(db: Session) -> None:
    """Watch pressed at 23:59:50 UTC and completed after midnight: the start is recent, so it counts (not "not started")."""
    from app.members import xp as ledger

    mem = member(db)
    short(db, "v1")
    missions.generate(db)
    watch.start(db, mem, "v1")
    a = db.scalar(select(MemberAction).where(MemberAction.member_id == mem.id, MemberAction.kind == watch.WATCH_KIND))
    a.day, a.created_at = ledger.day_of(utcnow() - timedelta(days=1)), utcnow() - timedelta(seconds=40)  # yesterday's date
    db.commit()
    assert watch.complete(db, mem, "v1", 31)["code"] == "paid"


def test_a_stale_watch_start_does_not_count(db: Session) -> None:
    mem = member(db)
    short(db, "v1")
    missions.generate(db)
    watch.start(db, mem, "v1")
    _rewind(db, mem.id, "v1", int(watch.START_WINDOW.total_seconds()) + 60)
    assert watch.complete(db, mem, "v1", 31)["code"] == "not_started"
