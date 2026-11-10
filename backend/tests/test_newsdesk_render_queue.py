"""News Desk pictures from the owner's GPU PC (app/newsdesk/render_queue.py): the worker's token, leases, retries, the quality gate,
idempotency, the offline fallback, and that prompts never carry a real name, text or numbers."""
import base64
import io
import random
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdRenderJob
from app.newsdesk import render_queue as rq
from tests.test_brand_manager import _worker

W = "/api/newsdesk-worker"


def _event(db: Session, title: str = "Oil tanker traffic through Hormuz falls as Iran war drags on", eid: int | None = None) -> NdEvent:
    ev = NdEvent(key=f"story:{title}", kind="news", title=title, headline=title.upper(), topic="geopolitics", category="politics", status="candidate", facts={})
    if eid:
        ev.id = eid
    db.add(ev)
    db.commit()
    return ev


def _picture(seed: int = 1, size: tuple[int, int] = (1536, 864)) -> bytes:
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (20, 24, 30))
    d = ImageDraw.Draw(img)
    for _ in range(160):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        r = rnd.randrange(10, 140)
        d.ellipse((x, y, x + r, y + r), fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    img = img.filter(ImageFilter.GaussianBlur(1))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _send(client: TestClient, h: dict[str, str], job_id: int, data: bytes) -> dict:
    r = client.post(f"{W}/jobs/{job_id}/result", headers=h, json={"image_b64": base64.b64encode(data).decode(), "result": {"model": "z-image-turbo", "seconds": 3.1}})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_worker_needs_its_own_token(client: TestClient) -> None:
    assert client.post(f"{W}/claim", json={}).status_code == 401
    assert client.post(f"{W}/claim", json={}, headers={"Authorization": "Bearer pcw_nope"}).status_code == 401


def test_claim_render_and_the_post_gets_the_background(client: TestClient, db: Session) -> None:
    h = _worker(client)
    ev = _event(db)
    rq.enqueue(db, ev, utcnow())
    db.commit()
    job = client.post(f"{W}/claim", json={"info": {"gpu": "RTX 5090", "model": "z-image-turbo"}}, headers=h).json()
    assert job["event_id"] == ev.id and job["width"] == 1536 and job["height"] == 864
    assert client.post(f"{W}/claim", json={}, headers=h).status_code == 204  # one job, already claimed
    assert _send(client, h, job["id"], _picture())["ok"] is True
    db.expire_all()
    got = rq.background_for(db, ev.id)
    assert got is not None and got[0].size == (1536, 864) and got[1]["model"] == "z-image-turbo"
    health = rq.health(db)
    assert health["online"] and health["last_24h"]["done"] == 1 and health["cost_usd"] == 0.0


def test_one_job_per_event_and_attempt(db: Session) -> None:
    ev = _event(db)
    a = rq.enqueue(db, ev, utcnow())
    b = rq.enqueue(db, ev, utcnow())
    assert a.id == b.id


def test_a_crashed_worker_s_job_goes_back_to_the_queue_then_fails(client: TestClient, db: Session) -> None:
    h = _worker(client)
    ev = _event(db)
    rq.enqueue(db, ev, utcnow())
    db.commit()
    for i in range(3):
        job = client.post(f"{W}/claim", json={}, headers=h).json()
        row = db.get(NdRenderJob, job["id"])
        db.refresh(row)
        row.lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
    rq.expire(db, utcnow())
    db.commit()
    db.refresh(row)
    assert row.status == "failed" and "stopped responding" in row.error
    assert rq.background_for(db, ev.id) is None  # the post falls back to the desk's own visuals


@pytest.mark.parametrize(("size", "why"), [((1024, 1024), "not 16:9"), ((960, 540), "too small")])
def test_the_quality_gate_refuses_bad_pictures_and_retries_with_a_new_direction(client: TestClient, db: Session, size: tuple[int, int], why: str) -> None:
    data = _picture(size=size)
    h = _worker(client)
    ev = _event(db)
    rq.enqueue(db, ev, utcnow())
    db.commit()
    job = client.post(f"{W}/claim", json={}, headers=h).json()
    got = _send(client, h, job["id"], data)
    assert got["ok"] is False and any(why in p for p in got["rejected"]) and got["retry_job"] != job["id"]


def test_a_flat_or_repeated_background_is_refused(client: TestClient, db: Session) -> None:
    h = _worker(client)
    flat = io.BytesIO()
    Image.new("RGB", (1536, 864), (40, 40, 40)).save(flat, "PNG")
    a, b = _event(db, "Gold hits record"), _event(db, "Silver hits record")
    rq.enqueue(db, a, utcnow())
    rq.enqueue(db, b, utcnow())
    db.commit()
    j1 = client.post(f"{W}/claim", json={}, headers=h).json()
    assert any("flat" in p for p in _send(client, h, j1["id"], flat.getvalue())["rejected"])
    j2 = client.post(f"{W}/claim", json={}, headers=h).json()
    assert _send(client, h, j2["id"], _picture(5))["ok"] is True
    j3 = client.post(f"{W}/claim", json={}, headers=h).json()  # a's retry
    got = _send(client, h, j3["id"], _picture(5))  # the very same picture again
    assert got["ok"] is False and any("recent background" in p for p in got["rejected"])


def test_a_job_past_its_deadline_is_cancelled_not_rendered(client: TestClient, db: Session) -> None:
    h = _worker(client)
    ev = _event(db)
    rq.enqueue(db, ev, utcnow(), deadline=utcnow() - timedelta(seconds=1))
    db.commit()
    assert client.post(f"{W}/claim", json={}, headers=h).status_code == 204


def test_a_failed_render_backs_off_then_gives_up(client: TestClient, db: Session) -> None:
    h = _worker(client)
    ev = _event(db)
    rq.enqueue(db, ev, utcnow())
    db.commit()
    job = client.post(f"{W}/claim", json={}, headers=h).json()
    assert client.post(f"{W}/jobs/{job['id']}/fail", json={"error": "CUDA out of memory"}, headers=h).json()["status"] == "queued"
    assert client.post(f"{W}/claim", json={}, headers=h).status_code == 204  # backing off


@pytest.mark.parametrize("title", [
    "Trump says FBI investigating Nvidia boss's affair with alleged Chinese spy",
    "Pete Hegseth decries impeachment push, defends Iran war handling",
    "Bitcoin drops below $74,000 as core inflation runs above forecasts",
    "Gold up 124.7% in five years while Litecoin fell 80.5%",
])
def test_prompts_never_name_people_or_carry_text_or_numbers(title: str) -> None:
    ev = NdEvent(id=7, key="k", kind="news", title=title, headline=title.upper(), category="stocks")
    d = rq.direction(ev)
    p = d.prompt.lower()
    for word in ("trump", "nvidia", "hegseth", "huang", "fbi", "74,000", "124.7", "80.5", "$"):
        assert word not in p, (word, p)
    assert not any(ch.isdigit() for ch in d.prompt) and "text" in rq.NEGATIVE and "numbers" in rq.NEGATIVE


def test_scenes_follow_the_subject_and_vary() -> None:
    oil = NdEvent(id=1, key="a", kind="news", title="Brent falls below $90 a barrel", headline="", category="commodities")
    assert rq.subject_of(oil) == "oil"
    first = rq.direction(oil).scene
    assert rq.direction(oil, recent_scenes=[first, first]).scene != first
