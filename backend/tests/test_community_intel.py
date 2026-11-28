"""Community Intelligence: the flag (off changes nothing), intake (links, forwards, limits, safety), independent verification,
duplicate detection, the conflict-of-interest question, editorial decisions (never publishing), appeals, reputation that can't be
farmed (community score and Researcher badges only: never XP), leaderboards, track records, the Telegram flow and the Terminal credit.
MADE-UP people, companies, tickers, links and figures."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.community_intel import rules
from app.community_intel import service as ci
from app.community_intel import telegram as citg
from app.config.settings import get_settings
from app.members import service as members
from app.members.catalog import ensure_catalog
from app.models.community_intel import CiEvent, CiSubmission
from app.models.members import Badge, Member, MemberBadge, MemberNotification, ReputationEvent
from app.models.trending import TtSignal, TtTopic
from app.passport import service as passport
from app.telegram.parser import parse_update

NOW = datetime(2026, 12, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset() -> None:
    passport.invalidate_rarity()


def _on(monkeypatch: pytest.MonkeyPatch, mode: str = "live", preview: str = "") -> Any:
    s = get_settings()
    monkeypatch.setattr(s, "passport2_mode", "live")
    monkeypatch.setattr(s, "community_intel_mode", mode)
    monkeypatch.setattr(s, "community_intel_preview_members", preview)
    return s


def _member(db: Session, name: str, age_days: int = 30) -> tuple[Member, str]:
    ensure_catalog(db)
    m = members.create_member(db)
    m.username, m.username_key = name, name.lower()
    m.created_at = NOW - timedelta(days=age_days)
    db.commit()
    return m, members.new_session(db, m, 30)


def _signal(db: Session, title: str, url: str, outlet: str, reliable: bool = True, at: datetime = NOW - timedelta(hours=1)) -> None:
    db.add(TtSignal(source="news", external_id=url, title=title, url=url, outlet=outlet, reliable=reliable, published_at=at, first_seen=at, last_seen=at))
    db.commit()


def _tip(db: Session, m: Member, url: str, note: str = "", at: datetime = NOW, disclosure: str | None = "none", **kw: Any) -> CiSubmission:
    return ci.submit(db, m, via="web", url=url, text=note, disclosure=disclosure, now=at, **kw)


def _ledger(db: Session) -> dict[int, tuple[int, int, int, int]]:
    return {m.id: (m.xp_total, m.rank_score, m.rank_level, m.bonus_xp) for m in db.scalars(select(Member))}


def _points(db: Session, m: Member) -> int:
    return ci._scored_rows(ci._decided(db, member_id=m.id)).get(m.id, {}).get("points", 0)


# --- the flag -------------------------------------------------------------------------------------------------------------------------


def test_off_changes_nothing(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _on(monkeypatch, "off")
    m, token = _member(db, "ana")
    members.link_identity(db, m, "telegram", "501", None, None)
    db.commit()
    client.cookies.set("pc_session", token)
    assert client.get("/api/members/intel").status_code == 404
    assert client.get("/api/members/intel/leaderboard").status_code == 404
    assert client.get("/api/community-intel/queue").status_code == 404
    bot = FakeBot()
    assert not asyncio.run(citg.handle_message(db, bot, s, 501, 501, "look https://www.reuters.com/x"))
    assert not asyncio.run(citg.handle_command(db, bot, s, 501, 501, "/tip", False))
    assert bot.sent == [] and db.scalar(select(func.count()).select_from(CiSubmission)) == 0
    assert ci.score_parts(db, m, NOW - timedelta(days=90)) == []
    passport.ensure_catalog(db)
    assert db.scalar(select(Badge.id).where(Badge.key == "ci_first_lead")) is None  # no Researcher badges while off


def test_preview_only_listed_members(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _on(monkeypatch, "preview", "Bea")
    bea, _ = _member(db, "bea")
    cal, _ = _member(db, "cal")
    for m, uid in ((bea, 601), (cal, 602)):
        members.link_identity(db, m, "telegram", str(uid), None, None)
    db.commit()
    bot = FakeBot()
    assert not asyncio.run(citg.handle_message(db, bot, s, 602, 602, "https://www.reuters.com/markets/a"))  # cal: as before
    assert asyncio.run(citg.handle_message(db, bot, s, 601, 601, "https://www.reuters.com/markets/a"))
    assert db.scalar(select(func.count()).select_from(CiSubmission)) == 1


# --- intake ---------------------------------------------------------------------------------------------------------------------------


def test_links_are_normalised_and_unsafe_input_refused(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    assert ci.canonical("https://www.Reuters.com/markets/story/?utm_source=x&id=7#top") == "https://reuters.com/markets/story?id=7"
    assert ci.canonical("https://twitter.com/SomeOne/status/123?s=20") == "https://x.com/someone/status/123"
    assert ci.source_class("sec.gov")["class"] == "official" and ci.source_class("treasury.gov")["class"] == "official"
    assert ci.source_class("reuters.com")["outlet"] == "Reuters" and ci.source_class("x.com")["class"] == "social"
    m, _ = _member(db, "dan")
    with pytest.raises(ci.CiError, match="Shortened"):
        _tip(db, m, "https://bit.ly/abc")
    with pytest.raises(ci.CiError, match="private key"):
        _tip(db, m, "", note="abandon " * 11 + "about https://reuters.com/a")
    with pytest.raises(ci.CiError, match="link"):
        _tip(db, m, "", note="no link here")
    young, _ = _member(db, "newbie", age_days=1)
    with pytest.raises(ci.CiError, match="3 days"):
        _tip(db, young, "https://reuters.com/a")


def test_limits_one_a_minute_daily_cap_and_no_self_duplicates(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m, _ = _member(db, "eli")
    a = _tip(db, m, "https://reuters.com/a?utm_campaign=z", at=NOW)
    with pytest.raises(ci.CiError, match="minute"):
        _tip(db, m, "https://reuters.com/b", at=NOW + timedelta(seconds=30))
    with pytest.raises(ci.CiError, match=ci.ref(a)):
        _tip(db, m, "https://www.reuters.com/a", at=NOW + timedelta(minutes=2))  # the same page again
    _tip(db, m, "https://reuters.com/c", at=NOW + timedelta(minutes=3))
    with pytest.raises(ci.CiError, match="today's 2 tips"):  # a Newcomer: 2 a day
        _tip(db, m, "https://reuters.com/d", at=NOW + timedelta(minutes=5))


def test_no_review_before_the_conflict_question_is_answered(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m, _ = _member(db, "fay")
    s = _tip(db, m, "https://reuters.com/a", disclosure=None)
    assert s.status == "checking"
    with pytest.raises(ci.CiError, match="conflict-of-interest"):
        ci.decide(db, s, "accept", "owner")
    ci.set_disclosure(db, s, "holds")
    assert s.status == "review" and s.disclosure == "holds"


# --- verification and duplicates ----------------------------------------------------------------------------------------------------


def test_independent_verification(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m, _ = _member(db, "gus")
    official = _tip(db, m, "https://www.sec.gov/Archives/edgar/8-K-northwind.htm", note="Northwind Mining 8-K: CFO resigned")
    assert official.verified and "official" in official.verified_by
    rumour = _tip(db, m, "https://x.com/someone/status/42", note="Northwind Mining CFO resigns", at=NOW + timedelta(minutes=2))
    assert not rumour.verified and rumour.checks["claim"] == "unverified"
    # the member's own page showing up in the Terminal is not independent confirmation
    _signal(db, "Northwind Mining CFO resigns", "https://x.com/someone/status/42", "X")
    ci.follow_stories(db, NOW + timedelta(minutes=5))
    db.refresh(rumour)
    assert not rumour.verified
    # an outlet the Terminal reads reports the same event: independently confirmed by the next run
    _signal(db, "Northwind Mining CFO steps down after audit", "https://www.reuters.com/business/northwind-cfo", "Reuters")
    ci.follow_stories(db, NOW + timedelta(minutes=10))
    db.refresh(rumour)
    assert rumour.verified and rumour.verified_by == "reported by reuters"


def test_anomaly_figures_against_live_data(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m, _ = _member(db, "hal")
    live = {"ZEPHX": {"funding_pct_h": -0.108, "change_pct": 8.7, "price": 1.23}}
    good = ci.submit(db, m, via="web", url="https://app.hyperliquid.xyz/trade/ZEPHX", text="$ZEPHX funding -0.11%/h while up 9% today",
                     disclosure="none", now=NOW, reader=live.get)
    assert good.kind == "anomaly" and good.verified and good.verified_by == "live market data"
    assert [f["ok"] for f in good.checks["figures"]["figures"]] == [True, True]
    bad = ci.submit(db, m, via="web", url="https://app.hyperliquid.xyz/trade/ZEPHX?x=1", text="$ZEPHX funding -0.9%/h, up 40%",
                    disclosure="none", now=NOW + timedelta(minutes=2), reader=live.get)
    assert not bad.verified and bad.checks["figures"]["contradicted"] == 2


def test_duplicates_keep_the_first_submitter(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    monkeypatch.setitem(rules.PER_DAY, "Newcomer", 50)
    a, _ = _member(db, "ivy")
    b, _ = _member(db, "jon")
    first = _tip(db, a, "https://reuters.com/scoop", note="Northwind Mining CFO resigns")
    second = _tip(db, b, "https://www.reuters.com/scoop?utm_source=tg", at=NOW + timedelta(minutes=1))
    assert second.status == "duplicate" and second.dup["ref"] == ci.ref(first) and second.dup["first_member_id"] == a.id
    fwd1 = ci.submit(db, a, via="telegram", text="", forward_from="Channel", forward_key="somechannel/77", disclosure="none", now=NOW + timedelta(minutes=5))
    fwd2 = ci.submit(db, b, via="telegram", text="", forward_from="Channel", forward_key="somechannel/77", disclosure="none", now=NOW + timedelta(minutes=6))
    assert fwd1.url == "https://t.me/somechannel/77" and fwd2.status == "duplicate"
    # the Terminal already had the story from a reliable source before the tip: covered, never "first spotted"
    db.add(TtTopic(key="t-quake", title="Earthquake hits Northwind Mining's Chile site", category="news", status="active", score=50, peak_score=50,
                   parts={}, signal_ids=[], sources=[{"source": "news", "outlet": "AP", "reliable": True, "at": (NOW - timedelta(hours=2)).isoformat()}],
                   metrics={}, first_seen=NOW - timedelta(hours=2), last_seen=NOW))
    db.commit()
    late = _tip(db, b, "https://apnews.com/quake", note="Earthquake hits Northwind Mining's Chile site", at=NOW + timedelta(minutes=8))
    assert late.dup["kind"] == "covered" and late.dup["story_key"] == "t-quake"


# --- decisions, appeals and reputation ----------------------------------------------------------------------------------------------


def test_reputation_cannot_be_farmed_and_never_touches_xp(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    for level in ("Newcomer", "Participant", "Regular"):
        monkeypatch.setitem(rules.PER_DAY, level, 50)
    m, _ = _member(db, "kim")
    before = _ledger(db)
    xp_rows = db.scalar(select(func.count()).select_from(ReputationEvent))
    unverified = _tip(db, m, "https://someblog.example/a", note="something odd")
    ci.decide(db, unverified, "accept", "owner", now=NOW)
    assert _points(db, m) == 0  # accepted, but nobody independent confirmed it: no points yet
    tips = [_tip(db, m, f"https://reuters.com/{i}", at=NOW + timedelta(minutes=2 * i + 2)) for i in range(5)]
    for t in tips:
        ci.decide(db, t, "accept", "owner", now=NOW + timedelta(hours=1))
    assert _points(db, m) == 3 * rules.POINTS["accepted"]  # 3 a day count
    tips[0].story_key = tips[1].story_key = "same-story"  # two tips about one story: one scores
    db.commit()
    research = _tip(db, m, "https://defillama.com/stables", note="Stablecoin supply up 4 weeks running", kind="research", at=NOW + timedelta(minutes=30))
    ci.decide(db, research, "accept", "owner", data_checked=True, now=NOW + timedelta(days=1))
    assert research.verified and research.verified_by == "an editor checked the data"
    pts = _points(db, m)
    assert pts == 3 * rules.POINTS["accepted"] + rules.POINTS["research"]
    # a near-copy of the member's own earlier tip, accepted anyway, never scores twice
    again = _tip(db, m, "https://www.bloomberg.com/stables-again", note="Stablecoin supply up 4 weeks running", kind="research", at=NOW + timedelta(minutes=40))
    assert again.dup.get("kind") == "tip" and again.dup.get("first_member_id") == m.id
    ci.decide(db, again, "accept", "owner", data_checked=True, now=NOW + timedelta(days=2))
    assert _points(db, m) == pts
    assert ci.headline("Stablecoin supply has risen four weeks running while exchange balances fell. Data attached.") == "Stablecoin supply has risen four weeks running while exchange balances fell."
    assert ci.headline("word " * 60).endswith("…") and len(ci.headline("word " * 60)) <= 141
    assert _ledger(db) == before and db.scalar(select(func.count()).select_from(ReputationEvent)) == xp_rows  # no XP, ever


def test_reject_appeal_and_spam_penalty(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m, token = _member(db, "lou")
    s = _tip(db, m, "https://moon-token.example/x", note="$MOONX about to 100x, get in now", at=NOW - timedelta(days=1))
    assert "Pump wording" in s.checks["flags"]
    ci.decide(db, s, "reject", "owner", reason="promotional", now=NOW - timedelta(days=1))
    assert _points(db, m) == rules.POINTS["spam"]
    assert db.scalar(select(MemberNotification.title).where(MemberNotification.member_id == m.id, MemberNotification.kind == "intel")).endswith("Promotional or a pump")
    client.cookies.set("pc_session", token)
    assert client.post(f"/api/members/intel/{s.id}/appeal", json={"text": "short"}).status_code == 400
    r = client.post(f"/api/members/intel/{s.id}/appeal", json={"text": "It's a real listing announcement, not promotion. Here is the exchange post."})
    assert r.status_code == 200 and r.json()["tip"]["appeal"]["status"] == "open"
    assert client.post(f"/api/members/intel/{s.id}/appeal", json={"text": "again and again and again"}).status_code == 400  # once only
    assert client.post(f"/api/community-intel/{s.id}/appeal", json={"uphold": True, "note": "Fair point."}).status_code == 200
    db.refresh(s)
    assert s.status == "review" and _points(db, m) == 0  # the penalty is gone; the tip is back with the desk
    late, _ = _member(db, "max")
    old = _tip(db, late, "https://reuters.com/old", at=NOW - timedelta(days=20))
    ci.decide(db, old, "reject", "owner", reason="not_enough", now=NOW - timedelta(days=20))
    with pytest.raises(ci.CiError, match="14 days"):
        ci.appeal(db, old, "Please look again, it was important.", now=NOW)


def test_repeated_spam_locks_tips_until_an_appeal_is_upheld(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    monkeypatch.setitem(rules.PER_DAY, "Newcomer", 50)
    m, _ = _member(db, "ned")
    rows = [_tip(db, m, f"https://spam.example/{i}", at=NOW - timedelta(hours=10 - i)) for i in range(3)]
    for r in rows:
        ci.decide(db, r, "reject", "owner", reason="misleading", now=NOW - timedelta(hours=5))
    with pytest.raises(ci.CiError, match="paused"):
        _tip(db, m, "https://reuters.com/real", at=NOW)
    ci.appeal(db, rows[0], "This was a genuine report, see the outlet.", now=NOW)
    ci.decide_appeal(db, rows[0], True, "owner", now=NOW)
    _tip(db, m, "https://reuters.com/real", at=NOW + timedelta(minutes=1))


def test_publication_first_spotted_badges_and_credit(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m, _ = _member(db, "ola")
    s = _tip(db, m, "https://www.sec.gov/8-k/northwind", note="Northwind Mining CFO resigns", at=NOW)
    ci.decide(db, s, "accept", "owner", story_key="t-nw", now=NOW + timedelta(minutes=5))
    assert ci.credit_line(db, "t-nw") == "Spotted by ola (Price Chat community)"
    db.add(TtTopic(key="t-nw", title="Northwind Mining CFO resigns", category="news", status="posted", score=70, peak_score=70, parts={}, signal_ids=[],
                   sources=[{"source": "news", "outlet": "Reuters", "reliable": True, "at": (NOW + timedelta(minutes=30)).isoformat()}],
                   metrics={}, first_seen=NOW + timedelta(minutes=30), last_seen=NOW + timedelta(hours=1), posted_at=NOW + timedelta(hours=1)))
    db.commit()
    ci.follow_stories(db, NOW + timedelta(hours=2))
    db.refresh(s)
    assert s.published_at is not None and s.first_spotted
    assert _points(db, m) == rules.POINTS["accepted"] + rules.POINTS["published"]
    passport.ensure_catalog(db)
    from app.members.badges import evaluate_auto_badges

    got = evaluate_auto_badges(db, m)
    assert {"ci_first_lead", "ci_first_spotted"} <= set(got)
    s.credit = False
    db.commit()
    assert ci.credit_line(db, "t-nw") is None  # no consent, no credit


def test_leaderboards_and_track_record(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    a, token = _member(db, "pia")
    b, _ = _member(db, "quin")
    c, _ = _member(db, "ray")
    c.leaderboard_opt_out = True
    db.commit()
    now = datetime.now(timezone.utc)
    for who, n in ((a, 2), (b, 1), (c, 2)):
        for i in range(n):
            t = _tip(db, who, f"https://reuters.com/{who.username}/{i}", at=now - timedelta(minutes=10 - 2 * i))
            ci.decide(db, t, "accept", "owner", now=now)
    client.cookies.set("pc_session", token)
    for period in ("week", "month", "all"):
        rows = client.get(f"/api/members/intel/leaderboard?period={period}").json()["rows"]
        assert [r["username"] for r in rows] == ["pia", "quin"], period  # the opted-out member isn't listed
    me = client.get("/api/members/intel").json()
    assert me["record"]["sent"] == 2 and me["record"]["accepted"] == 2 and me["record"]["acceptance"] is None  # shown after 5 decided


# --- Telegram ---------------------------------------------------------------------------------------------------------------------------


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[Any, str, Any]] = []
        self.answers: list[str] = []
        self.edits: list[str] = []

    async def send_message(self, chat: Any, text: str, buttons: Any = None, **kw: Any) -> dict[str, Any]:
        self.sent.append((chat, text, buttons))
        return {"message_id": 9}

    async def answer_callback(self, cid: str, text: str, alert: bool = False) -> None:
        self.answers.append(text)

    async def edit_message_text(self, chat: Any, mid: int, text: str, html: bool = False, buttons: Any = None) -> dict[str, Any]:
        self.edits.append(text)
        return {}

    async def delete_message(self, chat: Any, mid: int) -> None:
        pass


def test_paste_a_link_or_forward_a_post_to_the_bot(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _on(monkeypatch)
    m, _ = _member(db, "sam")
    members.link_identity(db, m, "telegram", "777", None, None)
    db.commit()
    bot = FakeBot()
    assert asyncio.run(citg.handle_message(db, bot, s, 777, 777, "check this https://www.reuters.com/markets/nw?utm_source=x"))
    tip = db.scalar(select(CiSubmission))
    assert tip.status == "checking" and tip.canonical_url == "https://reuters.com/markets/nw"
    text, buttons = bot.sent[-1][1], bot.sent[-1][2]
    assert f"Received · {ci.ref(tip)}" in text and "do you hold a position" in text
    cq = {"id": "c1", "data": buttons[0][0]["callback_data"], "from": {"id": 777}, "message": {"message_id": 5, "chat": {"id": 777}}}
    other = {**cq, "id": "c2", "from": {"id": 999}}
    asyncio.run(citg.handle_callback(db, bot, s, other))
    assert bot.answers[-1] == "That button isn't for you." and tip.disclosure is None
    asyncio.run(citg.handle_callback(db, bot, s, cq))
    db.refresh(tip)
    assert tip.status == "review" and tip.disclosure == "none" and "Nothing is published automatically" in bot.edits[-1]
    # a forward with no link at all: the post's own address
    upd = {"update_id": 1, "message": {"message_id": 3, "date": 1764331200, "chat": {"id": 777, "type": "private"}, "from": {"id": 777, "first_name": "S"},
           "text": "Big if true", "forward_origin": {"type": "channel", "chat": {"id": -1001, "title": "Wire", "username": "somewire"}, "message_id": 55, "date": 1764331100},
           "entities": [{"type": "text_link", "offset": 0, "length": 3, "url": "https://example.org/hidden"}]}}
    p = parse_update(upd)
    assert p.forward_key == "somewire/55" and p.links == ("https://example.org/hidden",)
    # /tip explains; in the group it points to a private chat
    asyncio.run(citg.handle_command(db, bot, s, 777, 777, "/tip", False))
    assert "Paste a link" in bot.sent[-1][1]
    asyncio.run(citg.handle_command(db, bot, s, -100500, 777, "/tip", True, 12))
    assert bot.sent[-1][0] == -100500 and "private chat" in bot.sent[-1][1]
    # not linked yet: one button to link a Passport
    asyncio.run(citg.handle_message(db, bot, s, 888, 888, "https://reuters.com/z"))
    assert "Link Telegram" in bot.sent[-1][1]


def test_decisions_reach_the_member_in_telegram(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m, _ = _member(db, "tia")
    members.link_identity(db, m, "telegram", "4040", None, None)
    db.commit()
    s = _tip(db, m, "https://reuters.com/q")
    ci.decide(db, s, "ask", "owner", note="Which exchange was it?")
    bot = FakeBot()
    assert asyncio.run(citg.deliver_notices(db, bot)) == 1
    assert bot.sent[-1][0] == 4040 and "Which exchange was it?" in bot.sent[-1][1]
    assert asyncio.run(citg.deliver_notices(db, bot)) == 0  # once
    ci.answer(db, s, "Binance, at 14:05 UTC.")
    assert s.status == "review"
    assert db.scalar(select(func.count()).select_from(CiEvent).where(CiEvent.submission_id == s.id)) >= 4


def test_editor_queue_api(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m, _ = _member(db, "uma")
    s = _tip(db, m, "https://reuters.com/e")
    q = client.get("/api/community-intel/queue?filter=review").json()
    assert q["items"][0]["ref"] == ci.ref(s) and q["items"][0]["member"]["username"] == "uma" and "record" in q["items"][0]
    assert client.post(f"/api/community-intel/{s.id}/decide", json={"action": "reject"}).status_code == 400  # a reason is required
    r = client.post(f"/api/community-intel/{s.id}/decide", json={"action": "accept", "note": "Good find."})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert client.post(f"/api/community-intel/{s.id}/decide", json={"action": "accept"}).status_code == 400  # decided: reopen first
    d = client.get(f"/api/community-intel/{s.id}").json()
    assert [e["action"] for e in d["events"]][:2] == ["submitted", "accept"] or "accept" in [e["action"] for e in d["events"]]
    from app.models.trending import TtPost

    assert db.scalar(select(func.count()).select_from(TtPost)) == 0  # nothing was published or queued for publishing
    assert db.scalar(select(func.count()).select_from(MemberBadge).join(Badge).where(Badge.key == "ci_researcher_month")) == 0
