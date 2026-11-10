"""News Desk -> X in LIVE mode, with the real single-use permit check (no monkeypatching of guard.require_permit).

Production 2026-11-09/10: every live X send failed with "PermissionError: Write blocked: this permit was already used" because the picture
upload used up the one permit the post needed. The post was left "publishing", the tick crashed, and the next tick wrote a second post for the
same event. These tests use a mocked X API only: nothing reaches a real service.
"""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdPost
from app.newsdesk import engine, visual
from app.services.agent_service import get_agent_config
from app.services.safety import get_or_create_policy
from tests.conftest import connect_x_user

JPEG = b"\xff\xd8\xff" + b"0" * 64


class X:
    """A mocked X API v2: media upload and post create, each with a status to force."""

    def __init__(self) -> None:
        self.upload_status, self.post_status = 200, 201
        self.upload_body: dict[str, Any] = {"data": {"id": "1880", "media_key": "3_1880"}}
        self.post_body: dict[str, Any] = {"data": {"id": "1990000000000000001", "text": "t"}}
        self.calls: list[tuple[str, bytes]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, request.content))
        if request.url.path == "/2/media/upload":
            return httpx.Response(self.upload_status, json=self.upload_body if self.upload_status < 300 else self.error(self.upload_status))
        if request.url.path == "/2/tweets":
            return httpx.Response(self.post_status, json=self.post_body if self.post_status < 300 else self.error(self.post_status))
        return httpx.Response(404, json={"title": "Not Found"})

    @staticmethod
    def error(status: int) -> dict[str, Any]:
        return {401: {"title": "Unauthorized", "type": "about:blank", "status": 401, "detail": "Unauthorized"},
                403: {"title": "Forbidden", "type": "https://api.x.com/2/problems/oauth1-permissions", "status": 403,
                      "detail": "Your client app is not configured with the appropriate oauth1 app permissions for this endpoint."},
                429: {"title": "Too Many Requests", "type": "about:blank", "status": 429, "detail": "Too Many Requests"},
                400: {"title": "Invalid Request", "type": "https://api.x.com/2/problems/invalid-request", "status": 400, "detail": "media type unrecognized"}}.get(status, {"title": "Error"})

    @property
    def paths(self) -> list[str]:
        return [p for p, _ in self.calls]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def x() -> X:
    return X()


@pytest.fixture(autouse=True)
def production_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """conftest switches these off for older tests; this module runs the production pipeline: X follows Telegram, with the picture."""
    from app.newsdesk import events

    monkeypatch.setattr(events, "X_FOLLOWS_TELEGRAM", True)
    monkeypatch.setattr(engine, "X_FORMAT", "visual")


@pytest.fixture(autouse=True)
def picture(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The picture is made without the network; the list records the events it was made for."""
    made: list[Any] = []

    async def for_event(ev: Any, **_: Any) -> tuple[bytes, dict[str, Any]]:
        made.append(ev.id)
        return JPEG, {"kind": "card"}

    monkeypatch.setattr(visual, "for_event", for_event)
    return made


def go_live(db: Session, x: X, mode: str = "live") -> Any:
    connect_x_user(db)
    policy = get_or_create_policy(db, get_agent_config(db).id)
    policy.outbound_enabled, policy.kill_switch = True, False
    s = engine.settings_row(db)
    s.mode, s.feeds, s.watchlist = mode, [], []
    db.commit()
    return type("Runtime", (), {"transport": x.transport})()


def event(db: Session, tg: str = "delivered", n: int = 1, minutes_ago: int = 5) -> NdEvent:
    now = utcnow()
    ev = NdEvent(key=f"story:https://coindesk.test/{n}", kind="news", title=f"Exchange Bybaz halts withdrawals {n}", headline=f"Exchange Bybaz halts withdrawals {n}",
                 category="crypto", sources=[{"name": "CoinDesk", "url": f"https://coindesk.test/{n}", "at": None}], facts={}, status="released", slot="slot",
                 first_seen=now - timedelta(minutes=minutes_ago), released_at=now - timedelta(minutes=minutes_ago), tg_status=tg, evidence="two outlets", virality=70)
    db.add(ev)
    db.commit()
    return ev


async def deliver(db: Session, runtime: Any, now: Any = None) -> str | None:
    return await engine.deliver_x(db, engine.settings_row(db), get_settings(), runtime, get_agent_config(db).id, now or utcnow())


def x_posts(db: Session) -> list[NdPost]:
    db.expire_all()
    return list(db.scalars(select(NdPost).order_by(NdPost.id)))


async def test_a_live_event_goes_out_with_its_picture_using_one_permit_for_the_upload_and_one_for_the_post(db: Session, x: X, picture: list[Any]) -> None:
    runtime = go_live(db, x)
    ev = event(db)
    assert await deliver(db, runtime) == "published"
    assert x.paths == ["/2/media/upload", "/2/tweets"]
    assert b'"media_ids":["1880"]' in x.calls[1][1].replace(b" ", b"")
    assert b"EXCHANGE BYBAZ HALTS WITHDRAWALS 1" in x.calls[1][1] and b"Source: CoinDesk" in x.calls[1][1]
    db.refresh(ev)
    assert ev.x_status == "published" and picture == [ev.id]
    [post] = x_posts(db)
    assert post.status == "published" and post.x_id == "1990000000000000001" and post.error is None
    lane = engine.x_lane(db, engine.settings_row(db), utcnow())
    assert lane["live"] and lane["last_ok"]["x_id"] == "1990000000000000001" and lane["events"][0]["url"] == "https://x.com/i/status/1990000000000000001"


async def test_x_waits_for_telegram_and_admin_says_so(db: Session, x: X) -> None:
    runtime = go_live(db, x)
    ev = event(db, tg="pending")
    assert await deliver(db, runtime) is None and x.calls == []
    db.refresh(ev)
    assert ev.x_status == "pending"
    row = engine.x_lane(db, engine.settings_row(db), utcnow())["events"][0]
    assert row["x"] == "pending" and row["telegram"] == "pending"


async def test_401_pauses_the_desk_with_x_s_code_and_keeps_the_post(db: Session, x: X) -> None:
    runtime = go_live(db, x)
    event(db)
    x.post_status = 401
    assert await deliver(db, runtime) == "paused"
    [post] = x_posts(db)
    assert post.status == "queued" and post.error.startswith("X 401 x_auth at the post")
    s = engine.settings_row(db)
    assert s.paused_until and "401" in s.pause_reason
    err = s.state["x_last_error"]
    assert err["stage"] == "post" and err["outcome"] == "paused" and "401" in err["message"]


async def test_a_403_permission_error_on_the_upload_names_the_upload_and_x_s_words(db: Session, x: X) -> None:
    runtime = go_live(db, x)
    event(db)
    x.upload_status = 403
    assert await deliver(db, runtime) == "paused"
    assert x.paths == ["/2/media/upload"]  # nothing was posted
    [post] = x_posts(db)
    assert "X 403 x_forbidden at the picture upload" in post.error and "oauth1 app permissions" in post.error
    assert "oauth1 app permissions" in engine.x_lane(db, engine.settings_row(db), utcnow())["last_error"]["message"]


async def test_a_rate_limit_retries_later_without_resending_twice(db: Session, x: X) -> None:
    runtime = go_live(db, x)
    ev = event(db)
    x.post_status = 429
    now = utcnow()
    assert await deliver(db, runtime, now) == "queued"
    [post] = x_posts(db)
    assert post.not_before and post.attempts == 1 and "X 429 x_rate_limited at the post" in post.error
    db.refresh(ev)
    assert ev.x_status == "queued" and ev.x_post_id == post.id
    assert await deliver(db, runtime, now + timedelta(minutes=1)) is None  # still waiting: not before its retry time
    x.post_status = 201
    assert await deliver(db, runtime, now + timedelta(minutes=61)) == "published"  # 30 min x 2^attempts
    assert len(x_posts(db)) == 1 and x.paths.count("/2/tweets") == 2


async def test_a_media_failure_fails_the_post_clearly_and_posts_nothing(db: Session, x: X) -> None:
    runtime = go_live(db, x)
    ev = event(db)
    x.upload_status = 400
    assert await deliver(db, runtime) == "failed"
    assert "/2/tweets" not in x.paths
    [post] = x_posts(db)
    assert post.status == "failed" and "X 400 x_bad_request at the picture upload" in post.error and "media type unrecognized" in post.error
    db.refresh(ev)
    assert ev.x_status == "failed" and "picture upload" in ev.x_note


async def test_a_picture_that_cant_be_made_fails_the_post_and_the_tick_goes_on(db: Session, x: X, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(ev: Any, **_: Any) -> tuple[bytes, dict[str, Any]]:
        raise OSError("font missing")

    monkeypatch.setattr(visual, "for_event", broken)
    runtime = go_live(db, x)
    event(db)
    assert await deliver(db, runtime) == "failed"
    [post] = x_posts(db)
    assert "OSError at making the picture" in post.error and "Nothing was posted" in post.error and x.calls == []


async def test_an_unexpected_refusal_never_leaves_the_post_publishing_or_writes_a_second_one(db: Session, x: X, monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact production failure, forced: create_post refuses the permit. The tick must not raise, and the event gets one post, not one per tick."""
    from app.connectors.x.client import XClient

    async def refuse(self: Any, text: str, **_: Any) -> dict[str, Any]:
        raise PermissionError("Write blocked: this permit was already used")

    monkeypatch.setattr(XClient, "create_post", refuse)
    runtime = go_live(db, x)
    ev = event(db)
    assert await deliver(db, runtime) == "failed"
    [post] = x_posts(db)
    assert post.status == "failed" and "PermissionError at the post" in post.error and "Nothing was posted" in post.error
    db.refresh(ev)
    assert ev.x_status == "failed" and ev.x_post_id == post.id
    assert await deliver(db, runtime) is None and len(x_posts(db)) == 1


async def test_dry_run_still_sends_nothing(db: Session, x: X) -> None:
    runtime = go_live(db, x, mode="dry_run")
    ev = event(db)
    assert await deliver(db, runtime) == "dry_run" and x.calls == []
    db.refresh(ev)
    assert ev.x_status == "dry_run"


# --- Admin: health line, per-event status, and the owner's one test post ------------------------------------------------


def test_admin_shows_x_publishing_and_previews_a_test_post_without_sending(client: TestClient, db: Session, x: X) -> None:
    go_live(db, x, mode="dry_run")
    client.app.state.runtime.x.transport = x.transport  # type: ignore[attr-defined]
    ev = event(db)
    lane = client.get("/api/x/newsdesk").json()["x_lane"]
    assert lane["mode"] == "dry_run" and lane["last_ok"] is None and lane["events"][0]["id"] == ev.id
    preview = client.get("/api/x/newsdesk/test-post").json()
    assert preview["event_id"] == ev.id and preview["ok"] and preview["text"] == "EXCHANGE BYBAZ HALTS WITHDRAWALS 1\n\nSource: CoinDesk"
    assert preview["image"].startswith("data:image/jpeg;base64,")
    assert x.calls == [] and x_posts(db) == []


def test_the_test_post_needs_confirmation_sends_once_even_in_dry_run_and_respects_the_stop(client: TestClient, db: Session, x: X) -> None:
    go_live(db, x, mode="dry_run")
    client.app.state.runtime.x.transport = x.transport  # type: ignore[attr-defined]
    ev = event(db)
    assert client.post("/api/x/newsdesk/test-post", json={"event_id": ev.id}).status_code == 422
    out = client.post("/api/x/newsdesk/test-post", json={"event_id": ev.id, "confirm": True}).json()
    assert out["status"] == "published" and out["url"] == "https://x.com/i/status/1990000000000000001" and out["facts"]["test"] is True
    assert x.paths == ["/2/media/upload", "/2/tweets"]
    db.expire_all()
    assert db.get(NdEvent, ev.id).x_status == "published"  # the desk won't post this event to X again
    assert client.post("/api/x/newsdesk/test-post", json={"event_id": ev.id, "confirm": True}).status_code == 409
    assert engine.settings_row(db).mode == "dry_run"  # the test doesn't switch the desk live
    other = event(db, n=2)
    client.put("/api/x/newsdesk/settings", json={"kill_switch": True})
    assert client.post("/api/x/newsdesk/test-post", json={"event_id": other.id, "confirm": True}).status_code == 409
    assert x.paths.count("/2/tweets") == 1
