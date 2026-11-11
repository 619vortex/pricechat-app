"""End to end (owner, 2026-11-11): a major event reported by World Monitor-catalog publishers between slots is detected from their feeds,
verified by two independent outlets, released as BREAKING at once (no slot), painted by FLUX.2 klein on the GPU PC, then posted on
Telegram and X with the same picture, automatically. Duplicates never go out again; scheduled slots keep working. No real service."""
import io
from datetime import timedelta
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.members.referrals import program
from app.models.brand_manager import BmWorker
from app.models.newsdesk import NdEvent, NdPost, NdRenderJob, NdTelegramPost
from app.newsdesk import engine, events, render_queue as rq, sources, telegram as lane
from tests.test_brand_manager import _worker
from tests.test_newsdesk_editorial import SLOT
from tests.test_newsdesk_render_queue import _picture

BETWEEN = SLOT + timedelta(hours=2)  # 06:00 Dubai: no slot open, the next is two hours away
DW = next(f for f in sources.WORLDMONITOR_FEEDS if f["name"] == "DW")
NATIONAL = next(f for f in sources.WORLDMONITOR_FEEDS if f["name"] == "The National")
HEAD = "Missile strike hits Saudi Aramco oil terminal at Ras Tanura"
SAYS_DW = "A missile strike hit Saudi Aramco's Ras Tanura oil export terminal, Saudi officials said."
SAYS_NATIONAL = "Saudi officials said a missile hit the Ras Tanura terminal, the kingdom's largest oil export facility."


class Tg:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def get_me(self) -> dict[str, Any]:
        return {"id": 9, "username": "Cryptopricechatbot"}

    async def get_chat(self, chat: Any) -> dict[str, Any]:
        return {"id": -100123, "type": "channel", "title": "Price Chat Updates"}

    async def get_chat_member(self, chat: Any, user: int) -> dict[str, Any]:
        return {"status": "administrator", "can_post_messages": True}

    async def send_photo(self, chat: Any, image: bytes, caption: str, **kw: Any) -> dict[str, Any]:
        self.sent.append({"image": image, "caption": caption, **kw})
        return {"message_id": 500 + len(self.sent), "chat": {"id": -100123, "type": "channel"}}

    async def send_message(self, chat: Any, text: str, **kw: Any) -> dict[str, Any]:
        raise AssertionError("a breaking post goes with its picture")


class X:
    def __init__(self) -> None:
        self.uploads: list[bytes] = []
        self.posts: list[dict[str, Any]] = []

    async def upload_image(self, data: bytes, permit: Any = None) -> str:
        self.uploads.append(data)
        return f"media{len(self.uploads)}"

    async def create_post(self, text: str, permit: Any = None, media_ids: Any = None) -> dict[str, Any]:
        self.posts.append({"text": text, "media": media_ids})
        return {"id": f"19{len(self.posts):017d}"}

    async def aclose(self) -> None:
        return None


@pytest.fixture
def live(db: Session, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Production's setup: six slots, the desk live on X, Telegram on with a channel; the outside world faked."""
    monkeypatch.setattr(events, "DEFAULT_SCHEDULE", "slots")
    program(db).updates_channel = "@Pricechatupdates"
    lane.settings_row(db).enabled = True
    desk = engine.settings_row(db)
    desk.mode, desk.automation, desk.state = "live", True, {"last_run": {"at": BETWEEN.isoformat()}}
    db.commit()
    feeds: dict[str, Any] = {"items": {}}

    async def fetch(client: Any, feed: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        return list(feeds["items"].get(feed["url"], [])), None

    monkeypatch.setattr(sources, "fetch_feed", fetch)
    x = X()
    monkeypatch.setattr("app.social.x_service.make_x_client", lambda *a, **k: x)
    from app.social.guard import SocialActionGuard

    monkeypatch.setattr(SocialActionGuard, "news_problem", lambda self, req: None)
    monkeypatch.setattr(SocialActionGuard, "issue_news_permit", lambda self, req: "permit")
    monkeypatch.setattr(SocialActionGuard, "record_attempt", lambda self, *a, **k: None)
    return {"feeds": feeds, "x": x, "tg": Tg()}


def _report(feed: dict[str, Any], title: str, minutes_ago: int, summary: str) -> dict[str, Any]:
    return {"title": title, "link": f"{feed['url']}#{abs(hash(title))}", "at": BETWEEN - timedelta(minutes=minutes_ago), "summary": summary}


def _both_report(live: dict[str, Any]) -> None:
    live["feeds"]["items"] = {
        DW["url"]: [_report(DW, HEAD, 20, SAYS_DW)],
        NATIONAL["url"]: [_report(NATIONAL, "Missile strike hits Saudi Aramco's Ras Tanura oil terminal, officials say", 12, SAYS_NATIONAL)],
    }


def _gpu_online(client: Any, db: Session) -> BmWorker:
    _worker(client)
    w = db.scalars(select(BmWorker)).one()
    rq.claim(db, w, {"gpu": "NVIDIA GeForce RTX 5090", "models": ["klein", "qwen"]}, BETWEEN - timedelta(seconds=30))  # polling, nothing yet
    return w


async def _desk_tick(db: Session) -> None:
    desk = engine.settings_row(db)
    await engine.collect(db, desk, None, BETWEEN)
    await events.decide(db, BETWEEN, quotes={}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, liquidations=False)


async def test_a_world_monitor_event_breaks_on_telegram_and_x_with_a_klein_picture(client: Any, db: Session, live: dict[str, Any]) -> None:
    w = _gpu_online(client, db)
    # 1. detection: two World Monitor-catalog publishers report the same major event, 20 and 12 minutes ago
    _both_report(live)
    await _desk_tick(db)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    # 2. verification: two independent outlets, a major score, released as breaking at once, not held for the 08:00 Dubai slot
    assert ev.slot == "breaking" and ev.virality >= events.POLICY.breaking_bar and events.verified_important(ev)
    assert {s["name"] for s in ev.sources} >= {"DW", "The National"} and "confirmed by 2 outlets" in ev.why
    assert all(sources.via(s["name"]) == sources.WORLDMONITOR_VIA for s in ev.sources)
    # 3. image generation: one klein job, at the top of the queue; Telegram holds the post for it
    job = db.scalars(select(NdRenderJob).where(NdRenderJob.event_id == ev.id)).one()
    assert job.payload["model"] == "klein" and job.priority == 10 and "text" in job.payload["negative"]
    tg = live["tg"]
    held = await lane.tick(db, get_settings(), tg, BETWEEN + timedelta(seconds=5))
    assert "waiting for its picture" in held["skipped"] and tg.sent == []
    got = rq.claim(db, w, {"gpu": "NVIDIA GeForce RTX 5090"}, BETWEEN + timedelta(seconds=10))
    assert got["model"] == "klein" and got["event_id"] == ev.id
    done = rq.complete(db, got["id"], w, _picture(42), {"model": "black-forest-labs/FLUX.2-klein-4B", "seconds": 2.05, "ocr": {"checked": True, "found": []}},
                       BETWEEN + timedelta(seconds=25))
    assert done["ok"] is True
    # 4. publication: Telegram with the cinematic picture, then X with the same picture, no approval step
    sent = await lane.tick(db, get_settings(), tg, BETWEEN + timedelta(seconds=40))
    assert sent["sent"] == 501 and len(tg.sent) == 1
    pic = Image.open(io.BytesIO(tg.sent[0]["image"]))
    assert pic.size == (1600, 900) and "<b>" in tg.sent[0]["caption"] and "Sources: DW · The National" in tg.sent[0]["caption"]  # feed links: named, not linked
    db.expire_all()
    ev = db.get(NdEvent, ev.id)
    assert ev.tg_status == "delivered" and ev.facts["visual"]["kind"] == "cinematic" and "klein" in ev.facts["visual"]["model"].lower()
    x = live["x"]
    result = await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, BETWEEN + timedelta(minutes=1))
    assert result == "published" and len(x.posts) == 1 and x.posts[0]["media"] == ["media1"]
    assert x.uploads[0] == tg.sent[0]["image"]  # the very same picture
    db.expire_all()
    ev = db.get(NdEvent, ev.id)
    post = db.get(NdPost, ev.x_post_id)
    assert ev.x_status == "published" and post.priority == "breaking" and post.x_id
    # strict dedup: the same event again (reworded, a later report) never posts again on either platform
    live["feeds"]["items"][DW["url"]].append(_report(DW, "Saudi Ras Tanura oil terminal hit by missile strike", 5, "Aramco's Ras Tanura terminal was hit, officials said."))
    await _desk_tick(db)
    await lane.tick(db, get_settings(), tg, BETWEEN + timedelta(minutes=3))
    await engine.deliver_x(db, engine.settings_row(db), get_settings(), None, 0, BETWEEN + timedelta(minutes=3))
    assert len(tg.sent) == 1 and len(x.posts) == 1
    assert len(list(db.scalars(select(NdTelegramPost.id).where(NdTelegramPost.status == "sent")))) <= 1


async def test_with_the_gpu_pc_offline_breaking_news_goes_at_once_with_the_desks_own_picture(client: Any, db: Session, live: dict[str, Any]) -> None:
    _both_report(live)
    await _desk_tick(db)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    assert db.scalar(select(NdRenderJob.id)) is None  # nobody to paint it: no job, no wait
    sent = await lane.tick(db, get_settings(), live["tg"], BETWEEN + timedelta(seconds=5))
    assert sent["sent"]
    db.expire_all()
    assert db.get(NdEvent, ev.id).facts["visual"]["kind"] != "cinematic"


async def test_a_single_outlet_never_breaks(client: Any, db: Session, live: dict[str, Any]) -> None:
    live["feeds"]["items"] = {DW["url"]: [_report(DW, HEAD, 20, SAYS_DW)]}
    await _desk_tick(db)
    assert db.scalar(select(NdEvent.id).where(NdEvent.status == "released")) is None


async def test_talk_about_a_strike_never_breaks(client: Any, db: Session, live: dict[str, Any]) -> None:
    talk = "Iran warns it may strike Saudi oil terminals"
    live["feeds"]["items"] = {DW["url"]: [_report(DW, talk, 20, "Iran warned it could target Saudi oil terminals, officials said.")],
                              NATIONAL["url"]: [_report(NATIONAL, talk + ", officials say", 12, "Iran threatened Saudi oil terminals.")]}
    await _desk_tick(db)
    assert db.scalar(select(NdEvent.id).where(NdEvent.status == "released")) is None


async def test_scheduled_slots_still_post_and_get_qwen(client: Any, db: Session, live: dict[str, Any]) -> None:
    w = _gpu_online(client, db)
    at = SLOT + timedelta(minutes=2)  # the 04:00 Dubai slot is open
    rq.claim(db, w, {"gpu": "NVIDIA GeForce RTX 5090"}, at - timedelta(seconds=20))
    title = "Missile strike hits Saudi Aramco oil terminal at Ras Tanura"
    live["feeds"]["items"] = {DW["url"]: [{**_report(DW, title, 0, SAYS_DW), "at": at - timedelta(minutes=30)}],
                              NATIONAL["url"]: [{**_report(NATIONAL, title + ", officials say", 0, SAYS_NATIONAL), "at": at - timedelta(minutes=25)}]}
    desk = engine.settings_row(db)
    await engine.collect(db, desk, None, at)
    await events.decide(db, at, quotes={}, watchlist=[], categories=desk.categories or {}, desk_state=desk.state, liquidations=False)
    ev = db.scalars(select(NdEvent).where(NdEvent.status == "released")).one()
    assert ev.slot == "slot"
    job = db.scalars(select(NdRenderJob).where(NdRenderJob.event_id == ev.id)).one()
    assert job.payload["model"] == "qwen"
