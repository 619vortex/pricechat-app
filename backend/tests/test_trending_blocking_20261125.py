"""Trending's blocking and churn mechanics (2026-11-25). Production 11-22..24: Trending published once in three days (post 1872); 716
publish cycles found nothing queued. Made-up rows modelled on the production log lines; nothing real is contacted."""

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdEvent
from app.models.trending import TtPost, TtTopic
from app.trending import coordinate, engine

NOW = utcnow().replace(hour=10, minute=10, second=0, microsecond=0)
NVIDIA_A = "Nvidia stock surges after Jensen Huang rehired as CEO"
NVIDIA_Z = "Nvidia shares jump as Jensen Huang returns"
CODE_RED = "Microsoft declares 'Code Red' over AI rivals"


def _src(title: str, outlet: str = "Reuters") -> dict[str, Any]:
    return {"source": "news", "outlet": outlet, "title": title, "url": f"https://{outlet.lower()}.test/1", "reliable": True, "engagement": 3.0,
            "summary": "", "at": NOW.isoformat(), "extra": {}}


def _topic(db: Session, title: str, score: float, *, growth: bool = True, key: str = "") -> TtTopic:
    t = TtTopic(key=key or f"t-{title[:20]}-{score}", title=title, category="stocks", status="active", score=score, peak_score=score,
                parts={"growth": {"measured": growth}}, sources=[_src(title), _src(title, "CNBC")], first_seen=NOW - timedelta(hours=2), last_seen=NOW)
    db.add(t)
    db.flush()
    return t


def _post(db: Session, topic: TtTopic, status: str, score: float, *, ago: int = 60, **kw: Any) -> TtPost:
    p = TtPost(topic_id=topic.id, category="stocks", status=status, text=kw.pop("text", topic.title), score=score,
               sources=[{"name": "Reuters", "url": "https://r.test/1", "title": topic.title}], checks=[], metrics={},
               created_at=NOW - timedelta(minutes=ago), **kw)
    db.add(p)
    db.flush()
    return p


# --- (a) replace / expire churn -----------------------------------------------------------------------------------------------


def test_a_topic_over_the_line_keeps_its_draft_when_it_is_skipped_and_gets_a_fresh_tweet_otherwise(db: Session) -> None:
    """Production 11-23 15:29-16:44: "Nvidia stock surges after Jensen Huang rehired as CEO" (83.4 -> 86.4) logged "tweet expired
    (Replaced: the topic crossed the automatic line...)" every scan and never got its fresh tweet. The draft was retired BEFORE the
    same-story check, which then found a lower Nvidia topic's draft for approval and skipped the topic: no tweet at all."""
    s = engine.settings_row(db)
    assert coordinate.same_story(NVIDIA_A, NVIDIA_Z)
    low = _topic(db, NVIDIA_Z, 60.0)
    _post(db, low, "pending", 60.0)  # a lower telling's draft for the owner
    a = _topic(db, NVIDIA_A, 83.4)
    draft = _post(db, a, "pending", 70.0)  # written below the line
    db.commit()
    skip, retire = engine.next_step(db, s, a, NOW)
    assert skip == ""  # another topic's draft for approval no longer stops a topic over the automatic line
    assert retire == [draft] and draft.status == "pending"  # nothing retired until the fresh tweet is written
    # a telling that is actually going out does stop it, and then the topic keeps its draft (before: retired, then skipped)
    other = _topic(db, "Nvidia rehires Jensen Huang as chief executive", 79.0)
    queued = _post(db, other, "queued", 79.0, approved_at=NOW)
    db.commit()
    skip, retire = engine.next_step(db, s, a, NOW)
    assert skip.startswith(f"same story as another topic's tweet (post {queued.id}, queued)") and retire == []
    assert draft.status == "pending"


def test_a_retired_draft_with_no_fresh_tweet_after_it_never_blocks_its_topic(db: Session) -> None:
    """The rows production was left with: retired drafts (one of them held at the line for the News Desk) and no fresh tweet. Before,
    the at-the-line one blocked the topic for the whole cooldown ("already has a tweet")."""
    s = engine.settings_row(db)
    a = _topic(db, NVIDIA_A, 86.4)
    _post(db, a, "expired", 70.0, note=engine.REPLACED)
    _post(db, a, "expired", 82.7, note=engine.REPLACED, hold_reason="covered by the News Desk (event 1162, candidate)")
    db.commit()
    assert engine.next_step(db, s, a, NOW) == ("", [])
    fresh = _post(db, a, "held", 86.4, ago=1, hold_reason="Sensitive topic (allegations about a named person): the source's hedge words must stay")
    db.commit()
    assert engine.next_step(db, s, a, NOW)[0] == "already has a tweet"  # the fresh tweet decides: no churn every scan
    assert fresh.status == "held"


def test_the_scan_log_says_why_a_topic_with_an_expired_draft_got_no_fresh_tweet(db: Session) -> None:
    s = engine.settings_row(db)
    a = _topic(db, NVIDIA_A, 86.4)
    _post(db, a, "expired", 70.0, note=engine.REPLACED)
    a.parts = {**a.parts, "skip": "same story as another topic's tweet (post 9, queued)"}
    db.commit()
    lines = engine.scan_log_lines(db, s, {"stored": {}, "health": {}}, [a], 0, None, {}, NOW)
    assert any("tweet expired" in ln and "no fresh tweet (same story as another topic's tweet (post 9, queued))" in ln for ln in lines)


# --- (b) stale pending drafts ---------------------------------------------------------------------------------------------------


def test_a_suggestion_that_went_stale_at_the_line_gets_one_fresh_attempt(db: Session) -> None:
    """Anthropic IPO topic 46560 (11-23): "pending (not newsworthy ...)" then "expired (Went stale before it was sent)", then blocked
    for the cooldown however the story developed."""
    s = engine.settings_row(db)
    t = _topic(db, "Anthropic shares break below $83 IPO price", 75.7)
    stale = _post(db, t, "expired", 75.7, ago=400, note=engine.STALE)
    db.commit()
    assert engine.next_step(db, s, t, NOW) == ("", [])  # every gate decides the fresh one again
    t.score = 70.0
    assert engine.next_step(db, s, t, NOW)[0] == "already has a tweet"  # under the line: one draft per topic, as before
    t.score = 75.7
    stale.approved_at = NOW - timedelta(hours=5)  # queued automatically (or approved), then stale while it waited: that decision stands
    db.commit()
    assert engine.next_step(db, s, t, NOW)[0] == "already has a tweet"


def test_a_draft_left_for_approval_at_the_line_gives_way_once_the_story_passes_the_automatic_rules(db: Session) -> None:
    s = engine.settings_row(db)
    t = _topic(db, "Markets react to Jensen Huang's return", 80.0)
    draft = _post(db, t, "pending", 80.0, note="not newsworthy enough to post on its own: a reaction piece, not the event")
    db.commit()
    assert engine.next_step(db, s, t, NOW)[0] == "already has a tweet"  # nothing changed: no churn
    t.title, t.sources = "Nvidia shares soar 27% after Jensen Huang reinstated as CEO", [_src("Nvidia shares soar 27% after Jensen Huang reinstated as CEO")]
    db.commit()
    assert engine.next_step(db, s, t, NOW) == ("", [draft])  # the lead now states a consequence: written and checked again
    engine.settings_row(db).mode = "approval"
    assert engine.next_step(db, s, t, NOW)[0] == "already has a tweet"  # approval mode: the owner's queue, untouched


def test_expired_and_replaced_drafts_count_neither_as_the_story_nor_against_the_caps(db: Session) -> None:
    s = engine.settings_row(db)
    z = _topic(db, NVIDIA_Z, 60.0)
    _post(db, z, "expired", 60.0, note=engine.STALE)
    _post(db, z, "expired", 70.0, note=engine.REPLACED)
    a = _topic(db, NVIDIA_A, 70.0)  # under the line: other topics' drafts still count there
    db.commit()
    assert engine._recent_duplicate(db, a, s, NOW) is None
    assert coordinate.x_posts_today(db, s, NOW)["trending"] == 0
    _post(db, z, "pending", 60.0)
    db.commit()
    assert engine._recent_duplicate(db, a, s, NOW) is not None  # below the line: one draft per story, as before


# --- (c) cross-lane holds -------------------------------------------------------------------------------------------------------


def test_a_hold_for_a_news_desk_candidate_is_rechecked_below_the_line_too(db: Session) -> None:
    """Production 11-24 16:56-17:12, three hours after "only what a lane sent holds another lane" went live: Microsoft "Code Red" (69.6)
    still showed "held (covered by the News Desk (event 1209, candidate))": a topic under the line was never re-checked."""
    s = engine.settings_row(db)
    ev = NdEvent(key="story:code-red", kind="news", title=CODE_RED, headline=CODE_RED, status="candidate", created_at=NOW - timedelta(hours=2))
    db.add(ev)
    t = _topic(db, CODE_RED, 69.6)
    held = _post(db, t, "held", 69.6, hold_reason="covered by the News Desk (event 1209, candidate)")
    db.commit()
    assert engine.next_step(db, s, t, NOW) == ("", [held])  # a fresh draft (for approval, under the line) is written and checked
    ev.status, ev.released_at, ev.tg_status, ev.x_status = "released", NOW - timedelta(hours=1), "delivered", "skipped"
    db.commit()
    assert engine.next_step(db, s, t, NOW)[0] == "already has a tweet"  # the desk did carry it: one story, one post
    ev.status = "rejected"
    db.commit()
    assert engine.next_step(db, s, t, NOW)[0] == ""  # a rejected event never holds Trending
    assert held.status == "held"  # next_step changes nothing; tick retires the draft when it writes the fresh one


async def test_the_tick_writes_the_fresh_tweet_and_retires_the_old_draft_together(db: Session, monkeypatch: Any) -> None:
    """The whole loop body, as tick runs it: a topic over the line with a retired draft and a lower topic's draft for approval gets its
    fresh tweet (before 2026-11-25: nothing)."""
    s = engine.settings_row(db)
    low = _topic(db, NVIDIA_Z, 60.0)
    _post(db, low, "pending", 60.0)
    a = _topic(db, NVIDIA_A, 83.4)
    draft = _post(db, a, "pending", 70.0)
    db.commit()
    written: list[int] = []

    async def fake_suggest(db: Session, s: Any, settings: Any, topic: TtTopic, now: Any, **_: Any) -> TtPost:
        written.append(topic.id)
        return _post(db, topic, "queued", topic.score, ago=0, approved_at=now)

    monkeypatch.setattr(engine, "suggest", fake_suggest)
    monkeypatch.setattr(engine, "build_topics", lambda db, s, now: [a, low])

    async def no_read(*_: Any, **__: Any) -> dict[str, Any]:
        return {"stored": {}, "health": {}}

    monkeypatch.setattr(engine, "read_sources", no_read)

    async def no_publish(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(engine, "publish_due", no_publish)
    from app.config.settings import get_settings
    from app.services.agent_service import get_agent_config

    await engine.tick(db, get_settings(), None, get_agent_config(db).id, NOW)
    assert written == [a.id]  # the lower topic: "same story as another topic's tweet" (A's queued one); A: its fresh tweet
    db.refresh(draft)
    assert draft.status == "expired" and draft.note == engine.REPLACED
