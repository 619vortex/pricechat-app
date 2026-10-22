"""Kiwi Passport growth layer: leaderboard with the member's own place, referrals (attribution, qualification,
anti-abuse, idempotency) and Telegram announcement missions (channel join verified by Telegram, discussion detected
from ingested messages)."""

import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import leaderboard, missions, referrals, service, telegram_missions, verified
from app.members import xp as ledger
from app.models.members import Member, MemberMission, Mission, Referral, ReputationEvent
from app.models.telegram import TelegramMessage, TelegramSource
from app.telegram.client import TelegramBotClient
from tests.fakes import BOT, TOKEN, FakeTelegram
from tests.test_members import (  # noqa: F401  (fixtures)
    _link, _tg_msg, app_and_client, fake_tg, google_sign_in, mc, me, member_by_name, new_member, other_browser, providers, x_sign_in,
)

CHANNEL_ID, GROUP_ID = -1004366986364, -100111


def events(db: Session, member: Member, event_type: str) -> list[ReputationEvent]:
    db.expire_all()
    return list(db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == member.id, ReputationEvent.event_type == event_type)))


# --- referrals ---------------------------------------------------------------------------------------------------


def test_invite_link_visit_counts_but_never_pays(mc: TestClient, other_browser: TestClient, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)
    mine = mc.get("/api/members/me/referrals").json()
    assert mine["code"].startswith("kiwi") and mine["link"] == f"http://127.0.0.1:5173/website/join/{mine['code']}"
    assert (mine["joined"], mine["qualified"], mine["xp_earned"], mine["reward_xp"], mine["welcome_xp"]) == (0, 0, 0, 25, 10)
    assert mc.get("/api/members/me/referrals").json()["code"] == mine["code"]  # stable
    r = other_browser.post("/api/members/referrals/visit", json={"code": mine["code"].upper()})
    assert r.json() == {"valid": True, "welcome_xp": 10, "signed_in": False} and r.cookies.get("pc_ref") == mine["code"]
    assert other_browser.post("/api/members/referrals/visit", json={"code": "kiwi000000"}).json() == {"valid": False}
    # the inviter opening their own link (signed in) stores nothing
    own = mc.post("/api/members/referrals/visit", json={"code": mine["code"]})
    assert own.json()["signed_in"] is True and own.cookies.get("pc_ref") is None
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.event_type.like("referral.%"))) == 0


def test_referral_qualifies_only_after_a_real_action_and_pays_once(mc: TestClient, other_browser: TestClient,  # noqa: F811
                                                                   db: Session) -> None:
    x_sign_in(mc)  # the inviter
    inviter = member_by_name(db, me(mc)["member"]["username"])
    code = mc.get("/api/members/me/referrals").json()["code"]
    other_browser.post("/api/members/referrals/visit", json={"code": code})
    r = google_sign_in(other_browser)  # the friend creates a Passport through the invite
    assert r.status_code == 303 and "pc_ref" not in other_browser.cookies  # the code is used once, then forgotten
    friend = member_by_name(db, me(other_browser)["member"]["username"])
    ref = db.scalar(select(Referral).where(Referral.referred_id == friend.id))
    assert ref is not None and ref.status == "joined" and ref.referrer_id == inviter.id
    # signing up (and its "Connected Google" reward) is not the qualifying action
    assert events(db, inviter, "referral.qualified") == [] and events(db, friend, "referral.welcome") == []
    # the friend links Telegram: their first verified action
    _link(db, friend, "telegram", "4242")
    db.expire_all()
    ref = db.get(Referral, ref.id)
    assert ref.status == "qualified" and ref.qualifying_event_type == "identity.telegram_linked"
    assert [e.xp for e in events(db, inviter, "referral.qualified")] == [25]
    assert [e.xp for e in events(db, friend, "referral.welcome")] == [10]
    assert (ref.referrer_xp, ref.welcome_xp) == (25, 10)
    # idempotent: more actions and sweeps pay nothing more
    ledger.award(db, friend, "telegram.first_participation", "tg-first:x", commit=True)
    referrals.sweep(db)
    assert len(events(db, inviter, "referral.qualified")) == 1 and len(events(db, friend, "referral.welcome")) == 1
    stats = mc.get("/api/members/me/referrals").json()
    assert (stats["joined"], stats["qualified"], stats["xp_earned"]) == (1, 1, 25)


def test_self_referral_and_repeat_referrals_are_refused(db: Session) -> None:
    inviter, friend = new_member(db, "inviter"), new_member(db, "friend")
    code = referrals.code_for(db, inviter)
    # self: created in the inviter's own signed-in browser
    ref = referrals.attribute(db, friend, code, browser="b1", browser_member=inviter)
    assert ref is not None and (ref.status, ref.reason) == ("rejected", "self_referral")
    # referred once, ever
    assert referrals.attribute(db, friend, code, browser="b2", browser_member=None) is None
    db.commit()
    _link(db, friend, "telegram", "777")
    assert events(db, inviter, "referral.qualified") == []
    # the same EXTERNAL account joining again (account deleted, re-created through the same invite) pays nothing twice
    again = new_member(db, "friend_again")
    ref2 = referrals.attribute(db, again, code, browser="b3", browser_member=None)
    db.commit()
    other = new_member(db, "someone")
    ref3 = referrals.attribute(db, other, code, browser="b4", browser_member=None)
    db.commit()
    _link(db, other, "x", "555")  # a genuinely new person qualifies
    assert [e.xp for e in events(db, inviter, "referral.qualified")] == [25]
    service.delete_member(db, other)  # leaves the referral row with the id cleared
    db.expire_all()
    assert db.get(Referral, ref3.id).referred_id is None and db.get(Referral, ref3.id).status == "qualified"
    _link(db, again, "x", "555")  # same X account as the deleted member: "Connected X" was already claimed, so nothing qualifies
    db.expire_all()
    assert db.get(Referral, ref2.id).status == "joined"
    ledger.award(db, again, "telegram.first_participation", "tg-first:again")  # a real action later: still the same person
    db.expire_all()
    assert db.get(Referral, ref2.id).status == "rejected" and db.get(Referral, ref2.id).reason == "already_claimed"
    assert len(events(db, inviter, "referral.qualified")) == 1
    assert events(db, again, "referral.welcome") == []  # a rejected referral never pays the welcome bonus either


def test_referral_daily_limit_waits_and_same_browser_pattern_is_flagged(db: Session) -> None:
    inviter = new_member(db, "busy")
    referrals.program(db).referral_daily_limit = 1
    code = referrals.code_for(db, inviter)
    friends = [new_member(db, f"f{i}") for i in range(3)]
    for f in friends:
        referrals.attribute(db, f, code, browser="same-browser", browser_member=None)
    db.commit()
    for i, f in enumerate(friends):
        _link(db, f, "telegram", f"90{i}")
    db.expire_all()
    statuses = sorted(r.status for r in db.scalars(select(Referral).where(Referral.referrer_id == inviter.id)))
    assert statuses == ["joined", "joined", "qualified"]  # the others wait for tomorrow's sweep
    assert sum(len(events(db, f, "referral.welcome")) for f in friends) == 1  # and so do their welcome bonuses
    from app.models.members import ModerationFlag

    assert db.scalar(select(ModerationFlag.kind).where(ModerationFlag.member_id == inviter.id)) == "referral_pattern"


# --- leaderboard -------------------------------------------------------------------------------------------------


def test_leaderboard_shows_the_members_own_place_outside_the_list(mc: TestClient, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)
    mine = member_by_name(db, me(mc)["member"]["username"])
    for i, xp in enumerate([500, 400, 300]):
        m = new_member(db, f"top{i}")
        ledger.award(db, m, "manual.award", f"seed:{i}", reason="seed", xp=xp)
    hidden = new_member(db, "hidden")
    ledger.award(db, hidden, "manual.award", "seed:h", reason="seed", xp=900)
    hidden.leaderboard_opt_out = True
    db.commit()
    board = mc.get("/api/members/leaderboard", params={"limit": 2}).json()
    assert [r["username"] for r in board["rows"]] == ["top0", "top1"]  # opted-out members never appear
    assert board["me"]["position"] == 4 and board["me"]["username"] == mine.username  # computed by the server
    assert me(mc)["standing"] == {"position": 4, "board": "all"}
    assert mc.get("/api/members/leaderboard", params={"period": "week"}).json()["me"]["position"] == 4
    signed_out = TestClient(mc.app).get("/api/members/leaderboard").json()  # type: ignore[arg-type]
    assert signed_out["me"] is None and len(signed_out["rows"]) == 4


def test_referral_leaderboard_counts_qualified_invites(mc: TestClient, db: Session) -> None:  # noqa: F811
    a, b = new_member(db, "alpha"), new_member(db, "bravo")
    for inviter, n in ((a, 1), (b, 2)):
        code = referrals.code_for(db, inviter)
        for i in range(n):
            f = new_member(db, f"{inviter.username}_f{i}")
            referrals.attribute(db, f, code, browser=f"{inviter.username}{i}", browser_member=None)
            db.commit()
            _link(db, f, "telegram", f"{inviter.id}{i}0")
    rows = mc.get("/api/members/leaderboard", params={"metric": "referrals"}).json()["rows"]
    assert [(r["username"], r["value"]) for r in rows] == [("bravo", 2), ("alpha", 1)]
    assert leaderboard.position(db, b, "all", "referrals") == {"position": 1, "value": 2}
    with _statements(db) as sql:
        leaderboard.board(db, "all", "referrals", 50, None)
    assert sum("FROM ranks" in q for q in sql) <= 2  # the catalog check + one read for all rows, not one per row


class _statements:
    """Collect the SQL a block runs."""

    def __init__(self, db: Session) -> None:
        self.engine, self.sql = db.get_bind(), []

    def _on(self, _conn, _cursor, statement, *_a):  # type: ignore[no-untyped-def]
        self.sql.append(statement)

    def __enter__(self) -> list[str]:
        from sqlalchemy import event

        event.listen(self.engine, "before_cursor_execute", self._on)
        return self.sql

    def __exit__(self, *_a) -> None:  # type: ignore[no-untyped-def]
        from sqlalchemy import event

        event.remove(self.engine, "before_cursor_execute", self._on)


def test_referral_sweep_skips_friends_who_never_did_anything(db: Session) -> None:
    inviter = new_member(db, "patient")
    code = referrals.code_for(db, inviter)
    idle = [new_member(db, f"idle{i}") for i in range(15)]
    for f in idle:
        referrals.attribute(db, f, code, browser=None, browser_member=None)
    db.commit()
    with _statements(db) as sql:
        assert referrals.sweep(db) == 0
    assert sum("FROM reputation_events" in q for q in sql) <= 1  # one query, not one per waiting referral
    referrals.program(db).referral_daily_limit = 0  # a friend acts while the inviter is at today's limit...
    _link(db, idle[0], "telegram", "31337")
    db.expire_all()
    assert db.scalar(select(Referral.status).where(Referral.referred_id == idle[0].id)) == "joined"
    referrals.program(db).referral_daily_limit = 5  # ...and the next sweep pays it
    assert referrals.sweep(db) == 1


# --- Telegram announcement channel ---------------------------------------------------------------------------------


def _channel(tg: FakeTelegram, bot_is_admin: bool) -> TelegramBotClient:
    tg.add_chat(CHANNEL_ID, "Price Updates", "channel")
    tg.chats[CHANNEL_ID]["username"] = "Pricechatupdates"
    tg.chats[CHANNEL_ID]["linked_chat_id"] = GROUP_ID
    tg.handles["@pricechatupdates"] = CHANNEL_ID
    if bot_is_admin:
        tg.members[(CHANNEL_ID, BOT["id"])] = "administrator"
        tg.member_list_hidden.discard(CHANNEL_ID)
    else:
        tg.member_list_hidden.add(CHANNEL_ID)
    return TelegramBotClient(TOKEN, "https://api.telegram.test", transport=tg.transport)


def test_channel_join_is_only_offered_when_telegram_lets_us_verify_it(db: Session) -> None:
    tg = FakeTelegram()
    st = asyncio.run(telegram_missions.check_channel(db, _channel(tg, bot_is_admin=False)))
    assert st["bot_can_verify_members"] is False and "not an administrator" in st["error"] and st["discussion_chat_id"] == GROUP_ID
    missions.generate(db)
    assert db.scalar(select(Mission).where(Mission.kind == "telegram_join_channel", Mission.status == "active")) is None

    client = _channel(tg, bot_is_admin=True)
    assert asyncio.run(telegram_missions.check_channel(db, client))["bot_can_verify_members"] is True
    missions.generate(db)
    mission = db.scalar(select(Mission).where(Mission.kind == "telegram_join_channel", Mission.status == "active"))
    assert mission is not None and mission.xp == 10 and mission.target["url"] == "https://t.me/Pricechatupdates"

    m = new_member(db, "joiner")
    out = asyncio.run(telegram_missions.verify_join(db, m, mission, client))
    assert out["code"] == "link_telegram" and not out["ok"]
    _link(db, m, "telegram", "5150")
    out = asyncio.run(telegram_missions.verify_join(db, m, mission, client))  # clicked, but not in the channel
    assert out["code"] == "not_joined" and events(db, m, "mission.complete") == []
    tg.members[(CHANNEL_ID, 5150)] = "member"
    out = asyncio.run(telegram_missions.verify_join(db, m, mission, client))
    assert out["ok"] and out["xp"] == 10 and [e.xp for e in events(db, m, "mission.complete")] == [10]
    assert asyncio.run(telegram_missions.verify_join(db, m, mission, client))["code"] == "done"  # once
    assert len(events(db, m, "mission.complete")) == 1
    # Telegram stops letting us check: the mission is no longer offered
    asyncio.run(telegram_missions.check_channel(db, _channel(tg, bot_is_admin=False)))
    missions.generate(db)
    db.expire_all()
    assert db.get(Mission, mission.id).status == "paused"


def _discussion_setup(db: Session) -> tuple[TelegramSource, Mission]:
    tg = FakeTelegram()
    asyncio.run(telegram_missions.check_channel(db, _channel(tg, bot_is_admin=False)))
    src = TelegramSource(chat_id=GROUP_ID, display_name="Price Chat", enabled=True)
    db.add(src)
    db.flush()
    _tg_msg(db, src, telegram_missions.TELEGRAM_SERVICE_USER, "Bitcoin weekly outlook is out", timedelta(hours=2), 500,
            forward_from="Price Updates")
    db.commit()
    missions.generate(db)
    mission = db.scalar(select(Mission).where(Mission.kind == "telegram_discussion", Mission.status == "active"))
    assert mission is not None
    return src, mission


def test_discussion_mission_comes_from_the_real_announcement(db: Session) -> None:
    _, mission = _discussion_setup(db)
    t = mission.target
    assert (t["chat_id"], t["message_id"], t["text"]) == (GROUP_ID, 500, "Bitcoin weekly outlook is out")
    assert t["url"] == "https://t.me/c/111/500" and mission.xp == 5 and mission.requires_platform == "telegram"


def test_discussion_rewards_one_genuine_comment_once(db: Session) -> None:
    src, mission = _discussion_setup(db)
    m = new_member(db, "commenter")
    _link(db, m, "telegram", "6060", ago=timedelta(hours=3))
    _tg_msg(db, src, 6060, "nice", timedelta(minutes=50), 501, reply_to_message_id=500)  # too short
    _tg_msg(db, src, 6060, "funding rates still look stretched to me", timedelta(minutes=45), 502)  # not under the update
    db.commit()
    verified.telegram_participation(db)
    assert events(db, m, "mission.complete") == []
    # a reply in the update's thread (to someone else's comment) counts: the chain leads to the update
    _tg_msg(db, src, 7070, "first comment from somebody else entirely", timedelta(minutes=40), 503, reply_to_message_id=500)
    _tg_msg(db, src, 6060, "I disagree, the weekly close matters more here", timedelta(minutes=30), 504, reply_to_message_id=503)
    db.commit()
    verified.telegram_participation(db)
    assert [e.xp for e in events(db, m, "mission.complete")] == [5]
    _tg_msg(db, src, 6060, "and another thought on the same update", timedelta(minutes=20), 505, reply_to_message_id=500)
    db.commit()
    verified.telegram_participation(db)
    assert len(events(db, m, "mission.complete")) == 1  # one reward per announcement
    mm = db.scalar(select(MemberMission).where(MemberMission.member_id == m.id, MemberMission.mission_id == mission.id))
    assert mm is not None and mm.status == "completed"


def test_copied_comments_and_comments_before_linking_earn_nothing(db: Session) -> None:
    src, _ = _discussion_setup(db)
    copier, late = new_member(db, "copier"), new_member(db, "latelinker")
    _link(db, copier, "telegram", "8080", ago=timedelta(hours=3))
    _tg_msg(db, src, 9090, "the halving narrative is overdone this cycle", timedelta(minutes=50), 510, reply_to_message_id=500)
    _tg_msg(db, src, 8080, "The halving narrative is overdone   this cycle", timedelta(minutes=40), 511, reply_to_message_id=500)
    _tg_msg(db, src, 1111, "an honest comment written before linking Telegram", timedelta(minutes=90), 512, reply_to_message_id=500)
    db.commit()
    _link(db, late, "telegram", "1111", ago=timedelta(minutes=30))
    verified.telegram_participation(db)
    assert events(db, copier, "mission.complete") == [] and events(db, late, "mission.complete") == []


# --- admin -------------------------------------------------------------------------------------------------------


def test_program_settings_are_configurable_and_validated(mc: TestClient, db: Session) -> None:  # noqa: F811
    out = mc.get("/api/members-admin/program").json()
    assert out["settings"]["referral_qualifying_events"] == ["identity.telegram_linked", "identity.x_linked", "telegram.first_participation"]
    assert out["settings"]["updates_channel"] == "@Pricechatupdates"
    assert {r["event_type"]: r["xp"] for r in out["referral_rules"]} == {"referral.qualified": 25, "referral.welcome": 10}
    bad = mc.patch("/api/members-admin/program", json={"referral_qualifying_events": ["referral.welcome"]})
    assert bad.status_code == 400
    ok = mc.patch("/api/members-admin/program", json={"discussion_xp": 7, "referral_daily_limit": 2,
                                                      "referral_qualifying_events": ["mission.complete"]}).json()
    assert ok["settings"]["discussion_xp"] == 7 and ok["settings"]["referral_daily_limit"] == 2


def test_admin_creates_a_discussion_mission_from_a_post_link(mc: TestClient, db: Session) -> None:  # noqa: F811
    src = TelegramSource(chat_id=GROUP_ID, display_name="Price Chat", enabled=True)
    db.add(src)
    db.flush()
    _tg_msg(db, src, 4444, "New Price Chat update: gold vs bitcoin", timedelta(hours=1), 900)
    db.commit()
    assert mc.post("/api/members-admin/missions/telegram-discussion", json={"message_url": "https://t.me/c/111/999"}).status_code == 400
    r = mc.post("/api/members-admin/missions/telegram-discussion", json={"message_url": "https://t.me/c/111/900", "title": "NEW PRICE CHAT UPDATE",
                                                                         "xp": 5})
    assert r.status_code == 200, r.text
    mission = db.get(Mission, r.json()["id"])
    assert mission.kind == "telegram_discussion" and mission.created_by == "dashboard" and mission.target["message_id"] == 900
    assert mc.post("/api/members-admin/missions/telegram-discussion", json={"message_url": "https://t.me/c/111/900"}).status_code == 400


def test_new_missions_have_one_obvious_action(db: Session) -> None:
    _, discussion = _discussion_setup(db)
    m = new_member(db, "viewer")
    _link(db, m, "telegram", "3030")
    rows = {r["kind"]: r for r in missions.for_member(db, m)}
    assert rows["telegram_discussion"]["cta"] == {"label": "Open discussion", "action": "open", "url": "https://t.me/c/111/500"}
    assert rows["telegram_discussion"]["xp"] == 5 and rows["telegram_discussion"]["earns_xp"] is True
    unlinked = new_member(db, "nolink")
    rows = {r["kind"]: r for r in missions.for_member(db, unlinked)}
    assert rows["telegram_discussion"]["status"] == "unavailable" and rows["telegram_discussion"]["cta"]["platform"] == "telegram"
    assert utcnow() - discussion.created_at < timedelta(minutes=5)
