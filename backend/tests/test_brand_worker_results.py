"""Brand Manager: what the backend accepts from an AI worker, and the files it keeps. TEST FIXTURES only (a tiny fake MP4)."""

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.brand_manager import brands
from app.brand_manager.store import MediaStore
from app.models.brand_manager import BmVideo
from tests.test_brand_manager import GOOD_SCRIPT, P, TOPIC, _mp4, _worker


def _rendering(client: TestClient, db: Session) -> tuple[BmVideo, dict[str, str], dict[str, Any]]:
    b = brands.ensure_default(db)
    v = BmVideo(brand_id=b.id, category=TOPIC.category, template=TOPIC.template, topic=TOPIC.topic, topic_key=TOPIC.key, score=1.0,
                brief=TOPIC.brief, status="ready", stages={}, generation=1, script=dict(GOOD_SCRIPT))
    db.add(v)
    db.commit()
    client.post(f"{P}/videos/{v.id}/rerender", json={"scope": "captions", "creative": {"captions": "boxed"}})
    h = _worker(client)
    job = client.post(f"{P}/worker/claim", json={"info": {}}, headers=h).json()
    return v, h, job


def _send(client: TestClient, h: dict[str, str], job_id: int, meta: Any, thumb: bytes | None = b"\xff\xd8\xff\xe0jpeg") -> Any:
    data, _ = _mp4()
    files: dict[str, Any] = {"video": ("v.mp4", io.BytesIO(data), "video/mp4")}
    if thumb is not None:
        files["thumb"] = ("t.jpg", io.BytesIO(thumb), "image/jpeg")
    return client.post(f"{P}/worker/jobs/{job_id}/result", headers=h, files=files, data={"meta": meta if isinstance(meta, str) else json.dumps(meta)})


def test_malformed_worker_reports_are_refused_or_failed_not_500(client: TestClient, db: Session) -> None:
    v, h, job = _rendering(client, db)
    assert _send(client, h, job["job_id"], "[1, 2]").status_code == 400
    _, meta = _mp4()
    r = _send(client, h, job["job_id"], {**meta, "duration": "long", "width": "wide"})
    assert r.status_code == 200 and r.json()["status"] == "queued"  # a quality-check failure, retried; not a crash
    db.refresh(v)
    assert any("Length" in p for p in v.qc["problems"]) and any("Wrong size" in p for p in v.qc["problems"])


def test_a_thumbnail_that_isnt_a_jpeg_is_dropped_but_the_video_kept(client: TestClient, db: Session) -> None:
    v, h, job = _rendering(client, db)
    _, meta = _mp4()
    r = _send(client, h, job["job_id"], meta, thumb=b"<html>not an image</html>")
    assert r.json() == {"ok": True, "status": "ready"}
    db.refresh(v)
    assert v.video_key and v.thumb_key is None


def test_a_new_render_deletes_the_previous_renders_files(client: TestClient, db: Session) -> None:
    v, h, job = _rendering(client, db)
    _, meta = _mp4()
    assert _send(client, h, job["job_id"], meta).json()["status"] == "ready"
    db.refresh(v)
    store = MediaStore(client.app.state.runtime.settings)  # type: ignore[attr-defined]
    first = (v.video_key, v.thumb_key)
    assert all(store.size(k) for k in first)
    client.post(f"{P}/videos/{v.id}/rerender", json={"scope": "video"})
    job2 = client.post(f"{P}/worker/claim", json={"info": {}}, headers=h).json()
    assert _send(client, h, job2["job_id"], meta).json()["status"] == "ready"
    db.refresh(v)
    assert v.video_key != first[0] and store.size(v.video_key) and store.size(v.thumb_key)
    assert all(store.size(k) is None for k in first)  # nothing points at them any more: they don't pile up in storage
    assert not Path(store.local / first[0]).exists()


def test_a_wall_clock_slot_means_the_brands_time_zone(client: TestClient, db: Session) -> None:
    b = brands.ensure_default(db)
    v = BmVideo(brand_id=b.id, category=TOPIC.category, template=TOPIC.template, topic=TOPIC.topic, topic_key=TOPIC.key, score=1.0,
                brief=TOPIC.brief, status="ready", stages={}, generation=1, script=dict(GOOD_SCRIPT))
    db.add(v)
    db.commit()
    assert brands.strategy(b)["timezone"] == "Asia/Dubai"  # UTC+4, no DST
    r = client.patch(f"{P}/videos/{v.id}", json={"slot_at": "2026-11-02T18:00"})
    assert r.status_code == 200
    db.refresh(v)
    assert v.slot_at == datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)  # 18:00 in Dubai, not 18:00 UTC
    client.patch(f"{P}/videos/{v.id}", json={"slot_at": "2026-11-02T18:00Z"})
    db.refresh(v)
    assert v.slot_at == datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
    assert client.patch(f"{P}/videos/{v.id}", json={"slot_at": "tomorrow"}).status_code == 400


def test_a_manual_video_is_claimed_behind_a_long_queue_left_by_stop_auto(client: TestClient, db: Session) -> None:
    from datetime import timedelta

    from app.brand_manager import queue
    from app.db.base import utcnow

    b = brands.ensure_default(db)
    a = brands.automation(db, b)
    a.running = False  # STOP AUTO: its planned videos stay queued until START
    for i in range(25):
        v = BmVideo(brand_id=b.id, category=TOPIC.category, template=TOPIC.template, topic=f"planned {i}", topic_key=f"p{i}",
                    brief=TOPIC.brief, status="queued", stages={}, generation=1, slot_at=utcnow() + timedelta(hours=i))
        db.add(v)
        db.flush()
        queue.enqueue_render(db, v)
    mine = BmVideo(brand_id=b.id, category=TOPIC.category, template=TOPIC.template, topic="made by hand", topic_key="m",
                   brief=TOPIC.brief, status="queued", stages={}, generation=1)
    db.add(mine)
    db.flush()
    queue.enqueue_render(db, mine, manual=True)
    db.commit()
    job = client.post(f"{P}/worker/claim", json={}, headers=_worker(client)).json()
    assert job["video_id"] == mine.id
