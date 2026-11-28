"""Price Chat Predictions: one vote per person per call across Telegram and the Passport, XP written once and only to the Predictor
category (never the Kiwi rank or a money reward), pending XP for unlinked Telegram members and a safe link, verified settlement (two
matching reads, ties, missing data, cancellation), fair boards, the quiet Telegram flow and the feature flag. MADE-UP prices and people."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.members import service as members
from app.members.catalog import ensure_catalog
from app.models.members import Member, ReputationEvent
from app.models.predictions import PredQuestion, PredTgUser, PredVote
from app.predictions import engine, rules, service, settle
from app.predictions import telegram as tg

DAY = date(2026, 11, 30)  # a Monday
NOW = datetime(2026, 11, 30, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset() -> None:
    service._catalog_ok = False
    tg._taps.clear()


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Any:
    s = get_settings()
    monkeypatch.setattr(s, "predictions_mode", "live")
    monkeypatch.setattr(s, "predictions_group", "@testgroup")
    return s


def _member(db: Session, name: str, age_days: int = 3) -> tuple[Member, str]:
    ensure_catalog(db)
    m = members.create_member(db)
    m.username, m.username_key = name, name.lower()
    m.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    db.commit()
    return m, members.new_session(db, m, 30)


def _daily(db: Session, d: date = DAY, ref: float = 60000.0) -> PredQuestion:
    q = service.make_daily("BTC", d, ref)
    db.add(q)
    db.commit()
    return q


def _reader(closes: dict[date, float]) -> settle.Reader:
    def read(coin: str, day: date) -> dict[str, Any] | None:
        c = closes.get(day)
        return None if c is None else {"coin": coin, "day": day.isoformat(), "open_time": 0, "close_time": 0, "close": c, "trades": 10}
    return read


def _settle(db: Session, q: PredQuestion, close: float) -> None:
    q.status = "locked"
    db.commit()
    t = service._aware(q.settles_at)
    assert settle.try_settle(db, q, t + timedelta(minutes=3), _reader({q.settle_day: close})) == "read"
    assert settle.try_settle(db, q, t + timedelta(minutes=14), _reader({q.settle_day: close})) == "settled"


def _xp(db: Session, m: Member, category: str | None = None) -> int:
    stmt = select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(ReputationEvent.member_id == m.id)
    if category:
        stmt = stmt.where(ReputationEvent.category == category)
    return int(db.scalar(stmt) or 0)


# --- votes -------------------------------------------------------------------------------------------------------------------------


def test_one_vote_per_person_changeable_until_the_lock(db: Session) -> None:
    m, _ = _member(db, "alice")
    q = _daily(db)
    v = service.Voter(member=m)
    assert service.vote(db, q, v, "up", "web", NOW)["first"]
    assert service.vote(db, q, v, "down", "telegram", NOW + timedelta(minutes=5))["changed"]
    assert db.scalar(select(func.count()).select_from(PredVote)) == 1
    with pytest.raises(service.PredictionError):
        service.vote(db, q, v, "sideways", "web", NOW)
    with pytest.raises(service.PredictionError, match="closed"):
        service.vote(db, q, v, "up", "web", service._aware(q.locks_at))


def test_range_boundaries_times_and_ties() -> None:
    w = service.make_weekly("BTC", DAY, 60000.0)
    assert [o["label"] for o in w.options] == ["Under $57.5k", "$57.5k–60.0k", "$60.0k–62.5k", "Over $62.5k"]
    assert service.outcome_for(w, 60000.0) == "r3"  # a boundary belongs to the range above
    assert service.outcome_for(w, 57499.0) == "r1" and service.outcome_for(w, 70000.0) == "r4"
    d = service.make_daily("BTC", DAY, 60000.0)
    assert service.outcome_for(d, 60000.0) == "tie" and service.outcome_for(d, 60000.5) == "up"
    assert service._aware(w.locks_at) == datetime(2026, 12, 2, 23, 59, tzinfo=timezone.utc)
    assert service._aware(w.settles_at) == datetime(2026, 12, 7, 0, 0, tzinfo=timezone.utc) and w.settle_day == date(2026, 12, 6)
    assert service._aware(d.opens_at) == datetime(2026, 11, 30, 13, 30, tzinfo=timezone.utc)
    assert service._aware(d.locks_at) == datetime(2026, 11, 30, 20, 0, tzinfo=timezone.utc)


# --- XP: Predictor category only, written once -------------------------------------------------------------------------------------


def test_prediction_xp_never_reaches_the_rank_or_money_rewards(db: Session) -> None:
    m, _ = _member(db, "bob")
    db.refresh(m)
    rank_before, xp_before = m.rank_score, m.xp_total
    q = _daily(db)
    service.vote(db, q, service.Voter(member=m), "up", "web", NOW)
    _settle(db, q, 61000.0)
    assert service.credit(db, NOW + timedelta(days=1))["paid_xp"] == 2 + 5 + 8  # play, first call, right (alone: no crowd bonus)
    db.refresh(m)
    assert _xp(db, m, rules.CATEGORY) == 15
    assert (m.rank_score, m.xp_total) == (rank_before, xp_before)  # the Kiwi rank (and its rewards) never moves
    lb = members.leaderboard_v2(db, "week", "xp")
    assert all(r["username"] != "bob" or r["value"] == _xp(db, m, "reputation") for r in lb["rows"])  # its own XP only
    assert service.credit(db, NOW + timedelta(days=1))["paid_xp"] == 0  # nothing pays twice
    from app.models.members import XPRule

    rule = db.scalar(select(XPRule).where(XPRule.event_type == "predictions.daily_right"))
    rule.category = "reputation"  # someone moves it in the dashboard...
    db.commit()
    service._catalog_ok = False
    service.ensure_catalog(db)
    db.refresh(rule)
    assert rule.category == rules.CATEGORY  # ...and it is put back


def test_a_new_passport_waits_a_day_then_gets_its_xp(db: Session) -> None:
    m, _ = _member(db, "newbie", age_days=0)
    q = _daily(db)
    service.vote(db, q, service.Voter(member=m), "up", "web", NOW)
    _settle(db, q, 61000.0)
    assert service.credit(db)["waiting"] == 1 and _xp(db, m, rules.CATEGORY) == 0
    assert service.stats(db, service.Voter(member=m))["pending_xp"] == 10
    m.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit()
    service.credit(db)
    assert _xp(db, m, rules.CATEGORY) == 15


# --- Telegram members, pending XP and linking --------------------------------------------------------------------------------------


def test_unlinked_telegram_votes_wait_then_move_to_the_passport_once(db: Session) -> None:
    q = _daily(db)
    q2 = _daily(db, DAY + timedelta(days=1), 61000.0)
    tgv = service.Voter(tg_user_id=777)
    service.vote(db, q, tgv, "up", "telegram", NOW)
    service.vote(db, q2, tgv, "up", "telegram", NOW + timedelta(days=1))
    m, _ = _member(db, "carol")
    service.vote(db, q2, service.Voter(member=m), "down", "web", NOW + timedelta(days=1, minutes=-5))  # earlier than the Telegram vote
    _settle(db, q, 61000.0)
    assert service.stats(db, tgv)["pending_xp"] == 10 and service.credit(db)["votes"] == 0  # unlinked: nothing written
    members.link_identity(db, m, "telegram", "777", "carol_tg", "Carol")
    db.commit()
    keys = {v.question_id: (v.voter_key, v.status) for v in db.scalars(select(PredVote))}
    assert keys[q.id] == (f"m:{m.id}", "counted")
    assert sorted(st for k, st in [v for qid, v in keys.items() if qid == q2.id]) == ["counted"]
    assert db.scalar(select(func.count()).select_from(PredVote).where(PredVote.status == "duplicate")) == 1
    assert db.scalar(select(PredVote.option).where(PredVote.question_id == q2.id, PredVote.status == "counted")) == "down"  # the earlier one
    service.credit(db)
    assert _xp(db, m, rules.CATEGORY) == 15
    # the same Telegram account later linked to another Passport can't claim those rewards again
    other, _ = _member(db, "dave")
    for v in db.scalars(select(PredVote).where(PredVote.question_id == q.id)):
        v.voter_key, v.member_id, v.credited_at = f"m:{other.id}", other.id, None
    db.commit()
    service.credit(db)
    assert _xp(db, other, rules.CATEGORY) == 0


# --- settlement ----------------------------------------------------------------------------------------------------------------------


def test_settlement_needs_two_matching_reads_and_holds_missing_data(db: Session) -> None:
    q = _daily(db)
    q.status = "locked"
    db.commit()
    t = service._aware(q.settles_at)
    assert settle.try_settle(db, q, t + timedelta(minutes=1), _reader({q.settle_day: 61000.0})) == "waiting"
    assert settle.try_settle(db, q, t + timedelta(minutes=3), _reader({q.settle_day: 61000.0})) == "read"
    assert settle.try_settle(db, q, t + timedelta(minutes=8), _reader({q.settle_day: 61000.0})) == "read"  # too soon for the second
    assert settle.try_settle(db, q, t + timedelta(minutes=13), _reader({q.settle_day: 61050.0})) == "read"  # a mismatch: keep reading
    assert settle.try_settle(db, q, t + timedelta(minutes=24), _reader({q.settle_day: 61050.0})) == "settled"
    assert q.outcome == "up" and q.settle_price == 61050.0 and q.evidence["source"].startswith("Hyperliquid")
    q2 = _daily(db, DAY + timedelta(days=1))
    q2.status = "locked"
    db.commit()
    t2 = service._aware(q2.settles_at)
    assert settle.try_settle(db, q2, t2 + timedelta(hours=2), _reader({})) == "read"
    assert settle.try_settle(db, q2, t2 + timedelta(hours=6, minutes=1), _reader({})) == "held"


def test_a_tie_pays_participation_only_and_cancel_reverses_xp(db: Session) -> None:
    m, _ = _member(db, "erin")
    q = _daily(db)
    service.vote(db, q, service.Voter(member=m), "up", "web", NOW)
    _settle(db, q, 60000.0)
    assert q.outcome == "tie"
    service.credit(db)
    assert _xp(db, m, rules.CATEGORY) == 2 + 5
    out = service.cancel(db, q, "test call", "owner")
    assert out["reversed"] == 1 and q.status == "void"  # the play reward is reversed; the one-off first-call bonus stays
    assert _xp(db, m, rules.CATEGORY) == 5


def test_contrarian_streaks_and_fair_boards(db: Session) -> None:
    ms = [_member(db, f"p{i}")[0] for i in range(4)]
    for k in range(3):
        q = _daily(db, DAY + timedelta(days=k), 60000.0 + k)
        for i, m in enumerate(ms):
            service.vote(db, q, service.Voter(member=m), "down" if i == 0 else "up", "web", NOW + timedelta(days=k))
        _settle(db, q, 50000.0)  # p0 alone was right: 25% agreed
    service.credit(db)
    assert db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.member_id == ms[0].id,
                                                                         ReputationEvent.event_type == "predictions.contrarian")) == 3
    assert db.scalar(select(ReputationEvent.id).where(ReputationEvent.member_id == ms[0].id, ReputationEvent.event_type == "predictions.streak_3"))
    board = service.leaderboard(db, "week", datetime(2026, 12, 2, 12, tzinfo=timezone.utc))
    assert board["rows"][0]["name"] == "p0" and board["rows"][0]["points"] == 3 * (10 + 5) and board["min_calls"] == 3
    assert service.leaderboard(db, "month", datetime(2026, 12, 2, 12, tzinfo=timezone.utc))["rows"] == []  # 10 calls needed (only Dec counts)


# --- the tick and Telegram -----------------------------------------------------------------------------------------------------------


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.answers: list[tuple[str, bool]] = []
        self.edits: list[str] = []
        self.member_status = "member"

    async def send_photo(self, chat: Any, png: bytes, caption: str, buttons: Any = None, **kw: Any) -> dict[str, Any]:
        assert png[:4] == b"\x89PNG"
        self.sent.append(("photo", {"chat": chat, "caption": caption, "buttons": buttons}))
        return {"message_id": 42}

    async def send_message(self, chat: Any, text: str, buttons: Any = None, **kw: Any) -> dict[str, Any]:
        self.sent.append(("message", {"chat": chat, "text": text, "buttons": buttons}))
        return {"message_id": 43}

    async def edit_caption(self, chat: Any, mid: int, caption: str, buttons: Any = None, **kw: Any) -> None:
        self.edits.append(caption)

    async def answer_callback(self, cid: str, text: str, alert: bool = False) -> None:
        self.answers.append((text, alert))

    async def get_chat_member(self, chat: Any, uid: int) -> dict[str, Any]:
        return {"status": self.member_status}

    async def delete_message(self, chat: Any, mid: int) -> None:
        pass


def _cq(data: str, uid: int = 555, **frm: Any) -> dict[str, Any]:
    return {"id": "cb1", "data": data, "from": {"id": uid, "first_name": "Tess", **frm}, "message": {"chat": {"type": "supergroup"}}}


def test_tick_posts_one_quiet_post_hides_the_split_and_reveals_it_at_the_lock(db: Session, live: Any) -> None:
    bot = FakeBot()
    reader = _reader({DAY - timedelta(days=1): 60000.0, DAY: 61000.0})
    tg._launched.clear()
    out = asyncio.run(engine.tick(db, live, bot, NOW, reader))
    assert out["opened"] == ["daily:BTC:2026-11-30", "weekly:BTC:2026-11-30"] and out["posted"] == 2
    # the launch: one announcement, just before the very first group call, then never again
    assert [k for k, _ in bot.sent] == ["message", "photo", "photo"] and "Predictions is live" in bot.sent[0][1]["text"]
    assert bot.sent[0][1]["chat"] == "@testgroup"
    asyncio.run(engine.tick(db, live, bot, NOW + timedelta(minutes=1), reader))
    tg._launched.clear()  # a restart: the database still knows the group has had its first call
    asyncio.run(engine.tick(db, live, bot, NOW + timedelta(minutes=2), reader))
    assert len(bot.sent) == 3  # idempotent: no second post, no second announcement
    bot.sent.clear()
    q = db.scalar(select(PredQuestion).where(PredQuestion.kind == "daily"))
    asyncio.run(tg.handle_callback(db, bot, live, _cq(f"pd:v:{q.id}:up"), NOW + timedelta(minutes=2)))
    text, alert = bot.answers[-1]
    assert text.startswith("Your call is Higher") and "pending until you link a Passport" in text and not alert
    assert bot.sent == []  # a vote adds no message to the group
    cap = tg.caption(db, q, NOW)
    assert "1 call so far" in cap and "%" not in cap  # the split stays hidden while open
    asyncio.run(engine.tick(db, live, bot, service._aware(q.locks_at), reader))
    db.refresh(q)
    assert q.status == "locked" and "Higher 100%" in bot.edits[-1]
    asyncio.run(engine.tick(db, live, bot, service._aware(q.settles_at) + timedelta(minutes=3), reader))
    asyncio.run(engine.tick(db, live, bot, service._aware(q.settles_at) + timedelta(minutes=14), reader))
    db.refresh(q)
    assert q.status == "settled" and "closed <b>higher</b>" in bot.edits[-1]


def test_buttons_refuse_bots_outsiders_and_rapid_taps(db: Session, live: Any) -> None:
    bot = FakeBot()
    q = _daily(db)
    asyncio.run(tg.handle_callback(db, bot, live, _cq(f"pd:v:{q.id}:up", is_bot=True), NOW))
    assert bot.answers[-1][0] == "Bots can't take part."
    bot.member_status = "left"
    asyncio.run(tg.handle_callback(db, bot, live, _cq(f"pd:v:{q.id}:up", uid=556), NOW))
    assert "Join @testgroup" in bot.answers[-1][0]
    bot.member_status = "member"
    for _ in range(rules.TAPS_PER_MINUTE):
        asyncio.run(tg.handle_callback(db, bot, live, _cq("pd:r", uid=557), NOW))
    asyncio.run(tg.handle_callback(db, bot, live, _cq("pd:r", uid=557), NOW))
    assert bot.answers[-1][0].startswith("One moment")
    assert db.scalar(select(func.count()).select_from(PredVote)) == 0


def test_commands_are_quiet_in_the_group_and_reminders_are_opt_in(db: Session, live: Any) -> None:
    bot = FakeBot()
    q = _daily(db)

    async def run() -> None:
        assert await tg.handle_command(db, bot, live, -100, 600, "/stats@CryptoPriceChatBot", True, 9, "Una", NOW)
        assert await tg.handle_command(db, bot, live, 600, 600, "/remind on", False, None, "Una", NOW)
        assert not await tg.handle_command(db, bot, live, 600, 600, "/price BTC", False, None, "Una", NOW)
        assert not await tg.handle_command(db, bot, live, 600, 600, "/start pc_abc", False, None, "Una", NOW)  # Passport sign-in stays where it is

    asyncio.run(run())
    assert bot.sent[0][1]["buttons"] is None  # the group reply has no buttons (and deletes itself after a minute)
    tu = db.scalar(select(PredTgUser).where(PredTgUser.tg_user_id == 600))
    assert tu.reminders and tu.dm_ok
    n = asyncio.run(tg.remind(db, bot, q, service._aware(q.locks_at) - timedelta(minutes=59)))
    assert n == 1 and "locks at 20:00 UTC" in bot.sent[-1][1]["text"]
    assert asyncio.run(tg.remind(db, bot, q, service._aware(q.locks_at) - timedelta(minutes=30))) == 0  # once per call


def test_off_means_off(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "predictions_mode", "off")
    bot = FakeBot()
    assert asyncio.run(engine.tick(db, s, bot, NOW, _reader({}))) == {"mode": "off"}
    assert not asyncio.run(tg.handle_command(db, bot, s, 1, 1, "/predict", False))
    asyncio.run(tg.handle_callback(db, bot, s, _cq("pd:r"), NOW))
    assert bot.answers[-1][0] == "Predictions are paused right now."
    assert db.scalar(select(func.count()).select_from(PredQuestion)) == 0


def test_preview_answers_only_the_test_chat_and_preview_people(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "predictions_mode", "preview")
    monkeypatch.setattr(s, "predictions_preview_chat", "-1009")
    monkeypatch.setattr(s, "predictions_preview_members", "Owner")
    bot = FakeBot()
    q = _daily(db)

    def cq(uid: int, chat: int) -> dict[str, Any]:
        return {"id": "cb", "data": f"pd:v:{q.id}:up", "from": {"id": uid, "first_name": "T"}, "message": {"chat": {"id": chat, "type": "supergroup"}}}

    # the public group and strangers: nothing (commands fall through to the usual bot, buttons say not open)
    assert not asyncio.run(tg.handle_command(db, bot, s, -100777, 700, "/predict", True, 1, "Pub", NOW))
    assert not asyncio.run(tg.handle_command(db, bot, s, 700, 700, "/stats", False, None, "Pub", NOW))
    asyncio.run(tg.handle_callback(db, bot, s, cq(700, -100777), NOW))
    assert bot.answers[-1][0] == "Predictions aren't open yet."
    assert db.scalar(select(func.count()).select_from(PredVote)) == 0 and db.scalar(select(func.count()).select_from(PredTgUser)) == 0
    # the test chat: anyone there plays, and may then use the bot privately
    asyncio.run(tg.handle_callback(db, bot, s, cq(701, -1009), NOW))
    assert bot.answers[-1][0].startswith("Your call is Higher")
    assert asyncio.run(tg.handle_command(db, bot, s, 701, 701, "/stats", False, None, "Tess", NOW))
    # a linked Passport on the preview list, in a private chat
    m, _ = _member(db, "owner")
    members.link_identity(db, m, "telegram", "702", "owner_tg", "Own")
    db.commit()
    assert asyncio.run(tg.handle_command(db, bot, s, 702, 702, "/stats", False, None, "Own", NOW))


# --- the Passport API ------------------------------------------------------------------------------------------------------------------


def test_passport_api_flag_preview_and_one_shared_vote(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    m, token = _member(db, "fran")
    _, other_token = _member(db, "gus")
    q = service.make_daily("BTC", datetime.now(timezone.utc).date(), 60000.0)
    q.locks_at = datetime.now(timezone.utc) + timedelta(hours=2)
    db.add(q)
    db.commit()
    monkeypatch.setattr(s, "predictions_mode", "off")
    client.cookies.set("pc_session", token)
    assert client.get("/api/members/predictions").status_code == 404
    monkeypatch.setattr(s, "predictions_mode", "preview")
    monkeypatch.setattr(s, "predictions_preview_members", "Fran")
    body = client.get("/api/members/predictions").json()
    assert body["open"][0]["split"] is None and body["stats"]["calls"] == 0 and "never counts towards the Kiwi rank" in body["rules"]["xp"]
    r = client.post(f"/api/members/predictions/{q.id}/vote", json={"option": "up"})
    assert r.status_code == 200 and r.json()["question"]["my_call"] == "up"
    members.link_identity(db, m, "telegram", "888", None, None)
    db.commit()
    bot = FakeBot()
    asyncio.run(tg.handle_callback(db, bot, s, _cq(f"pd:v:{q.id}:down", uid=888), datetime.now(timezone.utc)))
    assert db.scalar(select(func.count()).select_from(PredVote)) == 1  # Telegram and the Passport share one vote
    assert db.scalar(select(PredVote.option)) == "down"
    assert client.get("/api/members/predictions/card.png").headers["content-type"] == "image/png"
    client.cookies.set("pc_session", other_token)
    assert client.get("/api/members/predictions").status_code == 404  # preview: only the listed members
