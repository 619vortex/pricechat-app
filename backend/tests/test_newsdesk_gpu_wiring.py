"""GPU pictures wired into live posting (app/newsdesk/render_queue + visual + telegram): model per slot kind, prerender before a slot,
the post holds for its background only while the PC is online and inside the deadline, the cinematic format when a background is ready,
the Admin switch, and the headline's casing on the picture."""
import asyncio
import io
from datetime import timedelta
from types import SimpleNamespace as N

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdRenderJob
from app.newsdesk import brief, render_queue as rq, visual
from tests.test_brand_manager import _worker
from tests.test_newsdesk_render_queue import W, _event, _picture, _send


def _online(client: TestClient) -> dict[str, str]:
    h = _worker(client)
    assert client.post(f"{W}/claim", json={"info": {"gpu": "RTX 5090"}}, headers=h).status_code == 204  # seen, with News Desk info
    return h


def test_nothing_is_queued_while_the_pc_is_offline(db: Session) -> None:
    ev = _event(db)
    assert rq.on_release(db, ev, utcnow()) is None
    assert rq.waiting(db, ev, utcnow()) is False


def test_a_slot_story_gets_qwen_and_breaking_gets_klein(client: TestClient, db: Session) -> None:
    _online(client)
    now = utcnow()
    slot = _event(db, "Oil tanker traffic through Hormuz falls")
    slot.slot = "regular"
    hot = _event(db, "Exchange hacked for 400 million dollars")
    hot.slot, hot.label = "extra", brief.BREAKING
    a, b = rq.on_release(db, slot, now), rq.on_release(db, hot, now)
    assert a.payload["model"] == "qwen" and b.payload["model"] == "klein"
    assert b.priority > a.priority and b.deadline - now <= rq.WAIT_BREAKING + timedelta(seconds=1)
    assert rq.waiting(db, slot, now) and not rq.waiting(db, slot, now + rq.WAIT_SLOT + timedelta(seconds=5))


def test_charts_never_wait_for_a_background(client: TestClient, db: Session) -> None:
    _online(client)
    ev = _event(db)
    ev.kind = "market"
    assert rq.on_release(db, ev, utcnow()) is None


def test_prerender_paints_the_likely_stories_before_the_slot(client: TestClient, db: Session) -> None:
    _online(client)
    now = utcnow()
    evs = [_event(db, f"Story number {i} about oil") for i in range(5)]
    assert rq.prerender(db, now, now + timedelta(hours=2), evs) == 0  # too early
    assert rq.prerender(db, now, now + timedelta(minutes=10), evs) == rq.PRERENDER_TOP
    assert rq.prerender(db, now, now + timedelta(minutes=10), evs) == 0  # once per story
    # released while its prerender is still queued: no second job, the deadline stretches to the post's wait
    job = rq.on_release(db, evs[0], now)
    assert db.query(NdRenderJob).filter(NdRenderJob.event_id == evs[0].id).count() == 1 and job.payload["model"] == "qwen"


def test_a_ready_background_makes_the_cinematic_picture_and_x_gets_the_same(client: TestClient, db: Session) -> None:
    h = _online(client)
    ev = _event(db, "Oil tanker traffic through Hormuz falls as Iran war drags on")
    ev.status, ev.released_at, ev.sources = "released", utcnow(), [{"name": "Reuters", "url": "https://reuters.com/x"}]
    rq.on_release(db, ev, utcnow())
    db.commit()
    job = client.post(f"{W}/claim", json={}, headers=h).json()
    assert job["model"] == "qwen"
    assert _send(client, h, job["id"], _picture(3))["ok"] is True
    db.expire_all()
    ev = db.get(NdEvent, ev.id)
    assert rq.waiting(db, ev, utcnow()) is False
    data, meta = asyncio.run(visual.for_event(ev, db=db, now=utcnow()))
    assert meta["kind"] == "cinematic" and meta["render_job"] == job["id"]
    img = Image.open(io.BytesIO(data))
    assert img.size == (1600, 900)
    ev.facts = {**(ev.facts or {}), "visual": meta}
    again, meta2 = asyncio.run(visual.for_event(ev, db=db))
    assert meta2["kind"] == "cinematic" and meta2["render_job"] == meta["render_job"]


def test_without_a_background_the_desk_s_own_visual_goes(db: Session) -> None:
    ev = _event(db, "Oil tanker traffic through Hormuz falls")
    _, meta = asyncio.run(visual.for_event(ev, db=db, now=utcnow()))
    assert meta["kind"] != "cinematic"


def test_admin_switch_turns_gpu_pictures_off(client: TestClient, db: Session) -> None:
    r = client.put("/api/x/newsdesk/render", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert client.get("/api/x/newsdesk/render/health").json()["enabled"] is False
    assert client.put("/api/x/newsdesk/render", json={"enabled": True}).json()["enabled"] is True


def test_admin_sees_a_finished_background(client: TestClient, db: Session) -> None:
    h = _online(client)
    ev = _event(db)
    rq.enqueue(db, ev, utcnow())
    db.commit()
    job = client.post(f"{W}/claim", json={}, headers=h).json()
    _send(client, h, job["id"], _picture(9))
    got = client.get(f"/api/x/newsdesk/render/jobs/{job['id']}/image").json()
    assert got["image"].startswith("data:image/jpeg;base64,") and got["prompt"]


def test_retries_stop_after_three_jobs(client: TestClient, db: Session) -> None:
    h = _online(client)
    ev = _event(db)
    rq.enqueue(db, ev, utcnow(), deadline=utcnow() + timedelta(minutes=10))
    db.commit()
    flat = io.BytesIO()
    Image.new("RGB", (1536, 864), (40, 40, 40)).save(flat, "PNG")
    results = []
    for _ in range(4):
        r = client.post(f"{W}/claim", json={}, headers=h)
        if r.status_code == 204:
            break
        results.append(_send(client, h, r.json()["id"], flat.getvalue()))
    assert len(results) == 3 and "retry_job" not in results[-1]


def test_the_picture_headline_uses_the_outlet_s_capitals() -> None:
    ev = N(facts={}, headline="NVIDIA HIRES WACHTELL LIPTON TO INVESTIGATE C.E.O.’S AFFAIR", title="Nvidia Hires Wachtell Lipton to Investigate C.E.O.’s Affair")
    assert visual.display_headline(ev) == "Nvidia Hires Wachtell Lipton to Investigate C.E.O.’s Affair"
    ev = N(facts={}, headline="SEC APPROVES SOLANA ETF AS WTI JUMPS 4%", title="")
    assert visual.display_headline(ev) == "SEC Approves Solana ETF as WTI Jumps 4%"
    ev = N(facts={}, headline="OIL JUMPS AS IRAN SEIZES TANKER", title="")
    assert visual.display_headline(ev) == "Oil Jumps as Iran Seizes Tanker"
