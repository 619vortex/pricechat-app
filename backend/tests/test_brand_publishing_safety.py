"""Brand Manager publishing safety. TEST FIXTURES only: an in-memory fake TikTok (httpx.MockTransport); no real account,
no real network, nothing is published anywhere."""

import asyncio
import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand_manager import brands, discovery, engine, tiktok
from app.config.settings import get_settings
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.brand_manager import BmEvent, BmVideo
from app.security.credentials import secret_store

KEYS = ("tiktok_client_key", "tiktok_client_secret", "bm_tiktok_1_access", "bm_tiktok_1_refresh")


@pytest.fixture
def store() -> Any:
    s = secret_store(get_settings())
    s.set("tiktok_client_key", "tt-key")
    s.set("tiktok_client_secret", "tt-secret")
    yield s
    for k in KEYS:
        s.delete(k)


def _account(db: Session, store: Any, *, expired: bool) -> Any:
    b = brands.ensure_default(db)
    acc = tiktok.account(db, b.id)
    acc.status, acc.scopes = "connected", ["user.info.basic", "video.upload"]
    acc.access_expires_at = utcnow() + (timedelta(minutes=-1) if expired else timedelta(hours=20))
    db.commit()
    store.set(tiktok._tok(b.id, "access"), "act.0")
    store.set(tiktok._tok(b.id, "refresh"), "rft.0")
    return acc


class RotatingTikTok:
    """Each refresh hands out a new refresh token and the old one stops working, as TikTok may do."""

    def __init__(self, store: Any) -> None:
        self.store, self.n, self.refreshes, self.inits = store, 0, 0, 0
        self.init_error: Exception | None = None
        self.init_status = 200

    def transport(self) -> httpx.MockTransport:
        async def handle(r: httpx.Request) -> httpx.Response:
            if r.url.path == "/v2/oauth/token/":
                form = dict(x.split("=", 1) for x in r.content.decode().split("&"))
                self.refreshes += 1
                await asyncio.sleep(0.05)  # slow enough for a second caller to arrive meanwhile
                if form["refresh_token"] != f"rft.{self.n}":
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Refresh token is invalid."})
                self.n += 1
                return httpx.Response(200, json={"access_token": f"act.{self.n}", "refresh_token": f"rft.{self.n}", "expires_in": 86400,
                                                 "refresh_expires_in": 31536000, "scope": "user.info.basic,video.upload"})
            if r.url.path == "/v2/post/publish/inbox/video/init/":
                self.inits += 1
                if self.init_error is not None:
                    raise self.init_error
                if self.init_status != 200:
                    return httpx.Response(self.init_status, text="Bad Gateway")
                return httpx.Response(200, json={"data": {"publish_id": f"v_pub_{self.inits}"}, "error": {"code": "ok"}})
            if r.url.path == "/v2/post/publish/status/fetch/":
                return httpx.Response(200, json={"data": {"status": "PROCESSING_DOWNLOAD"}, "error": {"code": "ok"}})
            return httpx.Response(404, json={"error": {"code": "not_found", "message": r.url.path}})
        return httpx.MockTransport(handle)


# --- OAuth refresh ----------------------------------------------------------------------------------------------------


def test_two_refreshes_at_once_dont_mark_a_rotating_sign_in_expired(db: Session, store: Any) -> None:
    acc = _account(db, store, expired=True)
    fake = RotatingTikTok(store)

    async def both() -> list[str]:
        with SessionLocal() as other:  # e.g. the scheduler's tick and a dashboard request, each with its own session
            acc2 = tiktok.account(other, acc.brand_id)
            return list(await asyncio.gather(tiktok.access_token(db, get_settings(), acc, fake.transport()),
                                             tiktok.access_token(other, get_settings(), acc2, fake.transport())))

    tokens = asyncio.run(both())
    assert tokens == ["act.1", "act.1"] and fake.refreshes == 1
    db.refresh(acc)
    assert acc.status == "connected" and store.get("bm_tiktok_1_refresh") == "rft.1"


def test_a_refresh_lost_to_another_process_uses_that_process_token(db: Session, store: Any) -> None:
    acc = _account(db, store, expired=True)

    async def handle(r: httpx.Request) -> httpx.Response:
        # another process refreshed first: it rotated the tokens, so ours is refused
        store.set("bm_tiktok_1_access", "act.other")
        store.set("bm_tiktok_1_refresh", "rft.other")
        with SessionLocal() as other:
            a2 = tiktok.account(other, acc.brand_id)
            a2.access_expires_at = utcnow() + timedelta(hours=24)
            other.commit()
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Refresh token is invalid."})

    token = asyncio.run(tiktok.access_token(db, get_settings(), acc, httpx.MockTransport(handle)))
    assert token == "act.other"
    db.refresh(acc)
    assert acc.status == "connected"


def test_a_really_revoked_refresh_token_still_marks_the_account_expired(db: Session, store: Any) -> None:
    acc = _account(db, store, expired=True)
    store.set("bm_tiktok_1_refresh", "rft.revoked")
    with pytest.raises(tiktok.TikTokError):
        asyncio.run(tiktok.access_token(db, get_settings(), acc, RotatingTikTok(store).transport()))
    db.refresh(acc)
    assert acc.status == "expired"


# --- sends whose answer is lost are never re-sent ---------------------------------------------------------------------


def _due_video(db: Session, store: Any, monkeypatch: pytest.MonkeyPatch) -> BmVideo:
    acc = _account(db, store, expired=False)
    b = brands.ensure_default(db)
    a = brands.automation(db, b)
    a.running, a.status, a.upload_consent_at = True, "running", utcnow()
    a.last_analytics_at = utcnow()
    v = BmVideo(brand_id=b.id, category="facts", template="facts", topic="Bitcoin facts", topic_key="k1", status="ready", stages={},
                video_key="1/1-g1.mp4", slot_at=utcnow() - timedelta(minutes=1), publish_mode="drafts", generation=1)
    db.add(v)
    db.commit()

    async def no_topics(*_a: Any, **_k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(discovery, "discover", no_topics)
    assert acc.status == "connected"
    return v


@pytest.mark.parametrize("lost", [httpx.ReadTimeout("read timed out"), httpx.RemoteProtocolError("server disconnected"), 502])
def test_a_send_whose_answer_was_lost_is_failed_for_the_owner_to_check_not_retried(db: Session, store: Any, monkeypatch: pytest.MonkeyPatch,
                                                                                 lost: Any) -> None:
    v = _due_video(db, store, monkeypatch)
    fake = RotatingTikTok(store)
    if isinstance(lost, int):
        fake.init_status = lost
    else:
        fake.init_error = lost
    asyncio.run(engine.tick(db, get_settings(), fake.transport()))
    db.refresh(v)
    assert v.status == "failed" and "Check TikTok before sending it again" in (v.error or "") and fake.inits == 1
    # later ticks never send it again (TikTok may already have it: a second send would post it twice)
    fake.init_error, fake.init_status = None, 200
    asyncio.run(engine.tick(db, get_settings(), fake.transport(), now=utcnow() + timedelta(hours=1)))
    assert fake.inits == 1
    assert db.scalar(select(BmEvent).where(BmEvent.video_id == v.id, BmEvent.level == "error")) is not None


def test_a_send_that_never_left_is_retried_later(db: Session, store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v = _due_video(db, store, monkeypatch)
    fake = RotatingTikTok(store)
    fake.init_error = httpx.ConnectError("connection refused")
    now = utcnow()
    asyncio.run(engine.tick(db, get_settings(), fake.transport(), now=now))
    db.refresh(v)
    assert v.status == "ready" and v.slot_at > now
    fake.init_error = None
    asyncio.run(engine.tick(db, get_settings(), fake.transport(), now=now + timedelta(minutes=31)))
    db.refresh(v)
    assert v.status == "publishing" and v.publish_id == "v_pub_2"


def test_publish_now_after_a_lost_answer_cant_be_pressed_twice(db: Session, store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    v = _due_video(db, store, monkeypatch)
    fake = RotatingTikTok(store)
    fake.init_error = httpx.ReadTimeout("read timed out")
    b = brands.ensure_default(db)
    with pytest.raises(tiktok.TikTokError) as e:
        asyncio.run(engine.publish(db, get_settings(), b, v, fake.transport()))
    assert e.value.code == tiktok.UNCERTAIN
    db.refresh(v)
    assert v.status == "failed"
    with pytest.raises(tiktok.TikTokError) as again:
        asyncio.run(engine.publish(db, get_settings(), b, v, fake.transport()))
    assert again.value.code == "already_sending" and fake.inits == 1


def test_polling_checks_fresh_uploads_before_old_unposted_drafts(db: Session, store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _due_video(db, store, monkeypatch)
    b = brands.ensure_default(db)
    for i in range(12):  # drafts the owner hasn't posted from the TikTok app yet
        db.add(BmVideo(brand_id=b.id, category="facts", template="facts", topic=f"draft {i}", topic_key=f"d{i}", status="sent_to_drafts",
                       stages={}, publish_id=f"draft_{i}", generation=1))
    db.commit()
    fresh = BmVideo(brand_id=b.id, category="facts", template="facts", topic="fresh", topic_key="f", status="publishing", stages={},
                    publish_id="fresh_1", generation=1)
    db.add(fresh)
    db.commit()
    asked: list[str] = []
    fake = RotatingTikTok(store)
    inner = fake.transport()

    async def spy(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/v2/post/publish/status/fetch/":
            asked.append(json.loads(r.content)["publish_id"])
        return await inner.handle_async_request(r)

    asyncio.run(engine.tick(db, get_settings(), httpx.MockTransport(spy)))
    assert asked[0] == "fresh_1" and len([x for x in asked if x.startswith("draft_")]) <= 9
