"""Kiwi Passport 2.0: the feature flag (off changes nothing, preview only the listed members), numbered Founding Kiwis (join order,
first 100 or 1 Jan 2027, never reused), existing badges kept, the opt-in public page and its one-time invitation, identity choices
and their checks, the community score (genuine participation only), share cards, /passport, and no XP / rank / reward change.
MADE-UP people and activity."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.members import service as members
from app.members.catalog import ensure_catalog
from app.models.members import Badge, Member, MemberBadge, ModerationFlag, ReputationEvent
from app.passport import rules, service
from app.passport import telegram as ptg

NOW = datetime(2026, 12, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset() -> None:
    service.invalidate_rarity()


def _mode(monkeypatch: pytest.MonkeyPatch, mode: str, preview: str = "") -> Any:
    s = get_settings()
    monkeypatch.setattr(s, "passport2_mode", mode)
    monkeypatch.setattr(s, "passport2_preview_members", preview)
    return s


def _member(db: Session, name: str, age_days: int = 30) -> tuple[Member, str]:
    ensure_catalog(db)
    m = members.create_member(db)
    m.username, m.username_key = name, name.lower()
    m.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    db.commit()
    return m, members.new_session(db, m, 30)


def _event(db: Session, m: Member, event_type: str, when: datetime, n: int = 1, xp: int = 1) -> None:
    for i in range(n):
        db.add(ReputationEvent(member_id=m.id, event_type=event_type, xp=xp, source="telegram", source_ref=f"{event_type}:{when:%Y%m%d%H%M}:{i}",
                               reason="test", occurred_at=when, day=f"{when:%Y-%m-%d}"))
    db.commit()


def _ledger(db: Session) -> dict[int, tuple[int, int, int, int]]:
    return {m.id: (m.xp_total, m.rank_score, m.rank_level, m.bonus_xp) for m in db.scalars(select(Member))}


def _keys(db: Session, m: Member) -> set[str]:
    return set(db.scalars(select(Badge.key).join(MemberBadge, MemberBadge.badge_id == Badge.id).where(MemberBadge.member_id == m.id)))


# --- the flag -------------------------------------------------------------------------------------------------------------------------


def test_off_changes_nothing(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _mode(monkeypatch, "off")
    m, token = _member(db, "ana")
    m.public_profile = True
    db.commit()
    client.cookies.set("pc_session", token)
    assert client.get("/api/members/passport2").status_code == 404
    pub = client.get("/api/members/public/ana").json()
    assert "v" not in pub and set(pub) == {"username", "joined_at", "rank", "badges", "roles"}  # exactly the page as it was
    assert client.get("/api/members/public/ana/card.png").status_code == 404
    assert service.tick(db) == {"mode": "off"}
    assert db.scalar(select(Badge.id).where(Badge.key == rules.FOUNDING_KEY)) is None  # no new badge even exists
    assert not asyncio.run(ptg.handle_command(db, FakeBot(), s, 5, 5, "/passport", False))  # the bot answers as it always did


def test_preview_only_the_listed_members_earn_and_see_anything(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "preview", "Bea")
    bea, bea_token = _member(db, "bea", age_days=40)
    cal, cal_token = _member(db, "cal", age_days=50)  # joined earlier, but not in the preview
    for m in (bea, cal):
        members.link_identity(db, m, "telegram", str(1000 + m.id), None, None)
    db.commit()
    out = service.tick(db, NOW)
    assert out["founding"] == 1 and "telegram_verified" in _keys(db, bea)
    assert not (_keys(db, cal) & rules.NEW_KEYS)  # nothing new for anyone outside the preview, not even through the ledger
    from app.members.badges import evaluate_auto_badges

    evaluate_auto_badges(db, cal)
    assert not (_keys(db, cal) & rules.NEW_KEYS)
    client.cookies.set("pc_session", cal_token)
    assert client.get("/api/members/passport2").status_code == 404
    client.cookies.set("pc_session", bea_token)
    assert client.get("/api/members/passport2").status_code == 200


# --- Founding Kiwi ------------------------------------------------------------------------------------------------------------------


def test_founding_kiwis_are_numbered_in_join_order_until_the_cap(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "live")
    monkeypatch.setattr(rules, "FOUNDING_CAP", 3)
    people = [_member(db, f"p{i}", age_days=100 - i)[0] for i in range(5)]  # p0 joined first
    seat, _ = _member(db, "seat", age_days=200)
    seat.review_seat = True  # a store-review account is never a founder
    people[1].status = "banned"  # neither is a banned one
    db.commit()
    before = _ledger(db)
    service.ensure_catalog(db)
    got = dict(service.award_founding(db, NOW))
    assert got == {people[0].id: 1, people[2].id: 2, people[3].id: 3}
    assert service.award_founding(db, NOW) == []  # full: p4 is too late
    people[1].status = "active"
    db.commit()
    assert service.award_founding(db, NOW) == []  # numbers are never reused or moved
    assert _ledger(db) == before  # no XP, rank or reward moves
    assert all(k in _keys(db, people[0]) for k in ("early_kiwi", "first_100"))  # the existing badges are kept as they were


def test_founding_kiwi_closes_on_1_january_2027(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "live")
    _member(db, "late")
    service.ensure_catalog(db)
    assert service.award_founding(db, rules.FOUNDING_UNTIL) == []
    assert len(service.award_founding(db, rules.FOUNDING_UNTIL - timedelta(seconds=1))) == 1


# --- the editor and the public page --------------------------------------------------------------------------------------------------


def test_one_time_invitation_title_bio_featured_sections_and_invite_link(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "live")
    m, token = _member(db, "dee")
    members.link_identity(db, m, "telegram", "4242", "dee_secret_handle", None)
    db.commit()
    service.tick(db, NOW.replace(hour=0, minute=1))
    client.cookies.set("pc_session", token)
    ed = client.get("/api/members/passport2").json()
    assert ed["invited"] is False and ed["public_profile"] is False  # opt-in: private until the member says so
    assert ed["preview"]["username"] == "dee" and ed["preview"]["v"] == 2  # the live preview is the public page itself
    assert client.get("/api/members/public/dee").status_code == 404
    ed = client.post("/api/members/passport2/invite", json={"action": "later"}).json()
    assert ed["invited"] is True and ed["public_profile"] is False  # answered: never shown again, still private
    titles = {t["badge"]: t["title"] for t in ed["titles"]}
    assert titles[rules.FOUNDING_KEY] == "Founding Kiwi No. 001"
    assert client.patch("/api/members/passport2", json={"title_badge": "kiwi_ambassador"}).status_code == 400  # not earned
    for bad in ("follow me @deetrades", "best signals at dee.xyz", "Official Price Chat moderator"):
        assert client.patch("/api/members/passport2", json={"bio": bad}).status_code == 400, bad
    assert client.patch("/api/members/passport2", json={"featured": ["founding_kiwi", "early_kiwi", "first_100", "telegram_verified"]}).status_code == 422
    assert client.patch("/api/members/passport2", json={"featured": ["sharp_caller"]}).status_code == 400
    r = client.patch("/api/members/passport2", json={"public_profile": True, "title_badge": rules.FOUNDING_KEY, "bio": "Calls the close daily.",
                                                     "featured": ["telegram_verified", rules.FOUNDING_KEY], "sections": {"streak": False},
                                                     "ref_link": True})
    assert r.status_code == 200 and r.json()["featured"] == ["telegram_verified", rules.FOUNDING_KEY]
    pub = client.get("/api/members/public/dee").json()
    assert pub["v"] == 2 and pub["title"] == "Founding Kiwi No. 001" and pub["bio"] == "Calls the close daily."
    assert [b["key"] for b in pub["featured"]] == ["telegram_verified", rules.FOUNDING_KEY]
    assert "streak" not in pub and pub["platforms"] == ["telegram"] and pub["invite"].startswith("/join/")
    assert "dee_secret_handle" not in str(pub) and "4242" not in str(pub)  # a tick only: never a handle or an id
    assert client.patch("/api/members/passport2", json={"title_badge": ""}).json()["title_badge"] is None
    card = client.get("/api/members/public/dee/card.png?fmt=story")
    assert card.status_code == 200 and card.content[:4] == b"\x89PNG"
    og = client.get("/api/website/seo/og/u/dee").text
    assert "/api/members/public/dee/card.png" in og and "summary_large_image" in og


def test_the_frame_colour_keeps_its_rank_gate(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "live")
    _, token = _member(db, "egg")
    client.cookies.set("pc_session", token)
    r = client.patch("/api/members/passport2", json={"accent": "gold"})
    assert r.status_code == 403 and "Reach" in r.json()["detail"]


# --- the community score ---------------------------------------------------------------------------------------------------------------


def test_community_score_counts_genuine_participation_only(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "live")
    m, _ = _member(db, "eve")
    day = NOW - timedelta(days=2)
    _event(db, m, "telegram.first_participation", day)  # 10
    _event(db, m, "telegram.discussion", day, n=9)  # capped at 5 a day: 5
    _event(db, m, "mission.complete", day, n=2)  # 8
    _event(db, m, "referral.telegram", day)  # 25
    _event(db, m, "pack.open", day, n=20, xp=5)  # packs never count
    _event(db, m, "website.login", day, n=3)  # nor do sign-ins
    _event(db, m, "deposit.tier", day, xp=100)  # nor money
    _event(db, m, "telegram.first_participation", NOW - timedelta(days=120))  # outside the 90-day window
    before = _ledger(db)
    cs = service.community_score(db, m, NOW)
    assert cs["score"] == 10 + 5 + 8 + 25 and cs["level"]["name"] == "Newcomer"
    assert {p["key"]: p["points"] for p in cs["parts"]}["discussion"] == 5
    db.add(ModerationFlag(member_id=m.id, kind="manual", reason="spam", status="resolved", created_at=NOW - timedelta(days=1)))
    db.commit()
    assert service.community_score(db, m, NOW)["score"] == 0  # an upheld flag costs 50, never below zero
    assert _ledger(db) == before  # nothing stored, no XP or rank change


# --- Telegram -------------------------------------------------------------------------------------------------------------------------


class FakeBot:
    def __init__(self, dm_ok: bool = True) -> None:
        self.sent: list[tuple[str, Any, dict[str, Any]]] = []
        self.dm_ok = dm_ok

    async def send_photo(self, chat: Any, png: bytes, caption: str, buttons: Any = None, **kw: Any) -> dict[str, Any]:
        assert png[:4] == b"\x89PNG"
        if not self.dm_ok and isinstance(chat, int) and chat > 0:
            raise RuntimeError("Forbidden: bot can't initiate conversation")
        self.sent.append(("photo", chat, {"caption": caption, "buttons": buttons}))
        return {"message_id": 7}

    async def send_message(self, chat: Any, text: str, buttons: Any = None, **kw: Any) -> dict[str, Any]:
        self.sent.append(("message", chat, {"text": text, "buttons": buttons}))
        return {"message_id": 8}

    async def delete_message(self, chat: Any, mid: int) -> None:
        pass


def test_passport_command_makes_the_personal_card(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _mode(monkeypatch, "live")
    m, _ = _member(db, "fay")
    members.link_identity(db, m, "telegram", "5151", None, None)
    db.commit()
    bot = FakeBot()
    assert asyncio.run(ptg.handle_command(db, bot, s, 5151, 5151, "/passport", False))
    kind, chat, msg = bot.sent[-1]
    assert kind == "photo" and chat == 5151 and "private" in msg["caption"]  # private page: no share buttons yet
    assert len(msg["buttons"]) == 1
    m.public_profile = True
    db.commit()
    asyncio.run(ptg.handle_command(db, bot, s, 5151, 5151, "/start passport", False))
    assert any("x.com/intent/post" in b["url"] for row in bot.sent[-1][2]["buttons"] for b in row)
    # in the group: the card goes privately, the group sees one short line
    asyncio.run(ptg.handle_command(db, bot, s, -100500, 5151, "/passport", True, 99))
    assert bot.sent[-2][:2] == ("photo", 5151) and bot.sent[-1][0] == "message" and bot.sent[-1][1] == -100500
    # never started the bot: a button to do it
    quiet = FakeBot(dm_ok=False)
    asyncio.run(ptg.handle_command(db, quiet, s, -100500, 5151, "/passport", True, 99))
    assert quiet.sent[-1][0] == "message" and "Tap below" in quiet.sent[-1][2]["text"]
    # not linked: one button to link
    asyncio.run(ptg.handle_command(db, bot, s, 9090, 9090, "/passport", False))
    assert "Link Telegram" in bot.sent[-1][2]["text"]


def test_passport_command_in_preview_ignores_everyone_else(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _mode(monkeypatch, "preview", "Gil")
    m, _ = _member(db, "hal")
    members.link_identity(db, m, "telegram", "6161", None, None)
    db.commit()
    assert not asyncio.run(ptg.handle_command(db, FakeBot(), s, 6161, 6161, "/passport", False))
    assert not asyncio.run(ptg.handle_command(db, FakeBot(), s, 7171, 7171, "/passport", False))
