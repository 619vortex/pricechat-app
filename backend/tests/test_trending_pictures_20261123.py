"""Owner, 2026-11-23: "Apply the same image-or-valid-link requirement to the Trending Topics X lane. Hold posts that have neither, with a
manual text-only override." A trend tweet goes to X with a licensed picture (the News Desk's Visual Editor) or a link; with neither it
is held, and only the owner's "Post without picture" (then Approve) sends it as words alone. X is mocked; nothing real is called."""
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.models.trending import TtPost
from app.services.agent_service import get_agent_config
from app.trending import engine
from tests.test_trending import NOW, approval_mode, hack_story, newsdesk_live, posts, tick
from tests.trending_fixtures import Web

pytestmark = pytest.mark.real_trending_picture  # this file decides what the picture step finds


@pytest.fixture
def web() -> Web:
    return Web(NOW)


def found(monkeypatch: pytest.MonkeyPatch, image: bytes, why: str = "") -> list[str]:
    asked: list[str] = []

    async def picture_for(db: Any, post: Any, topic: Any, now: Any) -> tuple[bytes, dict[str, Any]]:
        asked.append(post.text)
        return image, ({"kind": "photo"} if image else {"kind": "text", "why": why})

    monkeypatch.setattr(engine, "picture_for", picture_for)
    return asked


async def pending(db: Session, web: Web) -> TtPost:
    newsdesk_live(db, web)
    approval_mode(db)
    hack_story(db, web)
    await tick(db, web)
    [post] = [p for p in posts(db) if p.status == "pending"]
    return post


async def approve(db: Session, web: Web, post: TtPost) -> str:
    return await engine.approve(db, get_settings(), web.runtime, get_agent_config(db).id, post, NOW)


async def test_a_trend_tweet_with_no_picture_and_no_link_is_held_never_sent(db: Session, web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    found(monkeypatch, b"", "no licensed photo of Bybaz")
    post = await pending(db, web)
    assert await approve(db, web, post) == "held"
    db.refresh(post)
    assert post.status == "held" and post.hold_reason.startswith("No picture and no link: no licensed photo of Bybaz")
    assert web.posted == [] and web.uploaded == []  # nothing reached X, not even a media upload
    out = engine.post_out(post)
    assert out["no_picture"] is True and out["text_ok"] is False


async def test_with_a_licensed_picture_it_goes_with_the_picture_attached(db: Session, web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    found(monkeypatch, b"\xff\xd8jpeg")
    post = await pending(db, web)
    assert await approve(db, web, post) == "published"
    assert len(web.uploaded) == 1 and b"jpeg" in web.uploaded[0]
    assert len(web.posted) == 1 and b"1880000000000000001" in web.posted[0]  # the tweet carries the uploaded media id


async def test_a_tweet_with_a_link_goes_without_looking_for_a_picture(db: Session, web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = found(monkeypatch, b"")
    post = await pending(db, web)
    post.text = post.text + "\n\nhttps://coindesk.test/1"  # X shows the linked article's own card
    db.commit()
    monkeypatch.setattr(engine.gates, "gate", lambda *a, **k: {"ok": True, "reason": "", "checks": []})
    monkeypatch.setattr(engine, "final_problems", lambda p: [])
    assert await approve(db, web, post) == "published"
    assert asked == [] and web.uploaded == [] and len(web.posted) == 1


@pytest.mark.parametrize("text,link", [("See https://www.reuters.com/markets/x", True), ("no link here", False), ("http://insecure.test/x", False),
                                       ("https://", False), ("https://localhost", False)])
def test_what_counts_as_a_link(text: str, link: bool) -> None:
    assert engine.has_link(text) is link


async def test_only_the_owner_sends_it_as_words_alone_and_it_still_needs_approve(client: TestClient, db: Session, web: Web,
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    asked = found(monkeypatch, b"", "nothing licensed")
    post = await pending(db, web)
    assert await approve(db, web, post) == "held"
    out = client.post(f"/api/x/trending/posts/{post.id}/release", json={"action": "text"}).json()
    assert out["status"] == "pending" and out["text_ok"] is True and web.posted == []  # released, not sent
    db.refresh(post)
    assert await approve(db, web, post) == "published"
    assert len(web.posted) == 1 and web.uploaded == [] and len(asked) == 1  # words alone, by the owner's choice; no second picture search


async def test_try_again_looks_for_a_picture_again(client: TestClient, db: Session, web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    found(monkeypatch, b"", "nothing yet")
    post = await pending(db, web)
    assert await approve(db, web, post) == "held"
    out = client.post(f"/api/x/trending/posts/{post.id}/release", json={"action": "retry"}).json()
    assert out["status"] == "pending" and out["text_ok"] is False
    found(monkeypatch, b"\xff\xd8jpeg")  # a licensed photo turned up
    db.refresh(post)
    assert await approve(db, web, post) == "published" and len(web.uploaded) == 1


async def test_release_refuses_a_tweet_held_for_another_reason(client: TestClient, db: Session, web: Web) -> None:
    post = TtPost(topic_id=1, category="crypto", status="held", text="x", sources=[], checks=[], metrics={}, hold_reason="No reliable source backs this topic yet")
    db.add(post)
    db.commit()
    r = client.post(f"/api/x/trending/posts/{post.id}/release", json={"action": "text"})
    assert r.status_code == 409
    assert client.post(f"/api/x/trending/posts/{post.id}/release", json={"action": "post"}).status_code == 422


async def test_automatic_mode_holds_too(db: Session, web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    found(monkeypatch, b"", "nothing licensed")
    newsdesk_live(db, web)
    s = engine.settings_row(db)
    s.hot_score = 50
    db.commit()
    hack_story(db, web)
    await tick(db, web)
    await tick(db, web, NOW + timedelta(minutes=10))
    [post] = [p for p in posts(db) if "Bybaz" in p.text]
    assert post.status == "held" and post.hold_reason.startswith("No picture and no link") and web.posted == []


async def test_the_picture_step_never_crashes_the_lane_and_never_stores_its_stand_in_event(db: Session, web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.newsdesk import NdEvent
    from app.newsdesk import visual

    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("Commons down")

    monkeypatch.setattr(visual, "for_event", boom)
    post = TtPost(id=99, topic_id=1, category="crypto", status="queued", text="Bybaz halts withdrawals", sources=[{"name": "CoinDesk", "url": "https://coindesk.test/1"}],
                  checks=[], metrics={})
    image, meta = await engine.picture_for(db, post, None, NOW)
    assert image == b"" and "RuntimeError" in meta["why"]
    assert db.query(NdEvent).count() == 0
