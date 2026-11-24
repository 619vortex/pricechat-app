"""Terminal Intelligence Engine: the AI router's policy (PC -> cloud -> rules), lease expiry, and OFF by default."""

import hashlib
import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.intel import channel, ledger, router
from app.models.intel import AiUsage, IntelJob, IntelWorker
from tests.test_intel_channel_ledger import GOOD

TOKEN = "pci_" + "r" * 43
ITEM = {"title": "Acme buys Globex for $41 billion", "summary": "", "outlets": ["Reuters"]}


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> Any:
    s = get_settings()
    monkeypatch.setattr(s, "intel_enabled", True)
    monkeypatch.setattr(s, "intel_worker_token_sha256", hashlib.sha256(TOKEN.encode()).hexdigest())
    monkeypatch.setattr(s, "anthropic_api_key", SecretStr("sk-test-not-real"))
    monkeypatch.setattr(s, "intel_daily_usd_cap", 3.0)
    return s


class Cloud:
    """A stand-in for the Anthropic API that counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(GOOD)}],
                                             "usage": {"input_tokens": 900, "output_tokens": 250}})
        return httpx.MockTransport(handler)


def pc_heartbeat(db: Session, ago: float = 0) -> IntelWorker:
    w = channel.worker_for(db, TOKEN)
    assert w is not None
    w.last_seen_at = utcnow() - timedelta(seconds=ago)
    db.commit()
    return w


# --- off by default -------------------------------------------------------------------------------------------------------------


async def test_disabled_by_default_nothing_enqueued_nothing_runs(client: TestClient, db: Session) -> None:
    s = get_settings()
    assert s.intel_enabled is False and s.intel_daily_usd_cap == 3.0
    assert router.enqueue(db, "intel.analyze", ITEM, "slot") is None
    cloud = Cloud()
    assert await router.run(db, "intel.analyze", ITEM, "breaking", transport=cloud.transport()) is None
    assert await router.tick(db) == {"skipped": "intel disabled"}
    assert db.query(IntelJob).count() == 0 and db.query(AiUsage).count() == 0 and cloud.calls == 0
    from app.jobs import build_jobs

    assert "intel.tick" not in {spec.name for spec in build_jobs(client.app.state.runtime)}  # type: ignore[attr-defined]


def test_tick_job_registered_only_when_enabled(client: TestClient, on: Any) -> None:
    from app.jobs import build_jobs

    names = {spec.name for spec in build_jobs(client.app.state.runtime)}  # type: ignore[attr-defined]
    assert "intel.tick" in names


# --- routing ----------------------------------------------------------------------------------------------------------------------


async def test_pc_offline_goes_to_cloud_then_rules_over_cap(db: Session, on: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = Cloud()
    out = await router.run(db, "intel.analyze", ITEM, "slot", transport=cloud.transport())
    assert out is not None and out.result_by == "cloud" and out.output["materiality"] == "significant" and cloud.calls == 1
    assert db.query(AiUsage).one().cost_usd == pytest.approx(ledger.cost("claude-haiku-5-5", 900, 250))
    monkeypatch.setattr(on, "intel_daily_usd_cap", 0.0)  # over the cap: rules, no call
    out = await router.run(db, "intel.analyze", ITEM, "slot", transport=cloud.transport())
    assert out is not None and out.result_by == "rules" and out.output is None and "over cap" in (out.reason or "") and cloud.calls == 1


async def test_no_key_means_rules(db: Session, on: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(on, "anthropic_api_key", None)
    out = await router.run(db, "intel.analyze", ITEM, "breaking")
    assert out is not None and out.result_by == "rules" and "no key" in (out.reason or "")


async def test_stale_heartbeat_counts_as_offline(db: Session, on: Any) -> None:
    pc_heartbeat(db, ago=61)
    j = router.enqueue(db, "intel.analyze", ITEM, "slot")
    assert j is not None and j.route == "cloud"
    pc_heartbeat(db, ago=5)
    j = router.enqueue(db, "intel.analyze", ITEM, "slot")
    assert j is not None and j.route == "pc" and j.offer_until is not None


async def test_breaking_is_cloud_first_with_pc_shadow(db: Session, on: Any) -> None:
    pc_heartbeat(db)
    cloud = Cloud()
    out = await router.run(db, "intel.analyze", ITEM, "breaking", transport=cloud.transport())
    assert out is not None and out.result_by == "cloud" and cloud.calls == 1
    shadow = db.query(IntelJob).filter(IntelJob.shadow.is_(True)).one()
    assert shadow.route == "pc" and shadow.status == "queued"  # the PC may still answer it, for comparison only


async def test_slot_unclaimed_past_window_falls_back_on_tick(db: Session, on: Any) -> None:
    pc_heartbeat(db)
    j = router.enqueue(db, "intel.analyze", ITEM, "slot")
    assert j is not None and j.route == "pc"
    cloud = Cloud()
    now = utcnow()
    assert await router.tick(db, now, transport=cloud.transport()) == {"requeued": 0, "cloud": 0, "rules": 0, "shadow_expired": 0, "deferred": 0}
    pc_heartbeat(db)
    counts = await router.tick(db, now + timedelta(seconds=181), transport=cloud.transport())
    db.refresh(j)
    assert counts["cloud"] == 1 and j.status == "done" and j.result_by == "cloud"


async def test_pc_goes_offline_while_job_waits(db: Session, on: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    pc_heartbeat(db)
    j = router.enqueue(db, "intel.analyze", ITEM, "slot")
    assert j is not None
    pc_heartbeat(db, ago=120)
    monkeypatch.setattr(on, "anthropic_api_key", None)
    counts = await router.tick(db)
    db.refresh(j)
    assert counts["rules"] == 1 and j.result_by == "rules"


async def test_expired_lease_requeued_once_then_falls_back(db: Session, on: Any) -> None:
    w = pc_heartbeat(db)
    j = router.enqueue(db, "intel.analyze", ITEM, "slot")
    assert j is not None
    now = utcnow()
    assert channel.claim(db, w, 1, now)[0]["id"] == j.id
    cloud = Cloud()
    later = now + channel.LEASE + timedelta(seconds=1)
    w.last_seen_at = later
    db.commit()
    assert (await router.tick(db, later, transport=cloud.transport()))["requeued"] == 1
    db.refresh(j)
    assert j.status == "queued" and j.route == "pc" and j.nonce is None  # the old nonce is dead
    offer = channel.claim(db, w, 1, later)[0]
    much_later = later + channel.LEASE + timedelta(seconds=1)
    w.last_seen_at = much_later
    db.commit()
    counts = await router.tick(db, much_later, transport=cloud.transport())  # second lease expired: attempts used up
    db.refresh(j)
    assert counts["cloud"] == 1 and j.result_by == "cloud" and cloud.calls == 1
    with pytest.raises(channel.ChannelError) as e:  # the PC's late answer is refused
        channel.complete(db, w, j.id, offer["nonce"], channel.sign(TOKEN, j.id, offer["nonce"], {"output": GOOD}), {"output": GOOD},
                         TOKEN, much_later)
    assert e.value.status == 409


async def test_bulk_waits_for_pc_then_expires_after_6h(db: Session, on: Any) -> None:
    out = await router.run(db, "intel.embed", {"text": "backfill me"}, "bulk")
    assert out is not None and out.result_by is None  # pending
    j = db.get(IntelJob, out.job_id)
    assert j is not None and j.route == "pc" and j.offer_until is None
    now = utcnow()
    assert (await router.tick(db, now))["rules"] == 0  # PC offline, but bulk waits
    counts = await router.tick(db, now + timedelta(hours=6, seconds=1))
    db.refresh(j)
    assert counts["rules"] == 1 and j.result_by == "rules" and "no cloud embeddings" in (j.error or "")


async def test_run_waits_for_pc_answer(db: Session, on: Any) -> None:
    import asyncio

    w = pc_heartbeat(db)

    async def pc() -> None:
        from app.db.session import SessionLocal

        await asyncio.sleep(0.05)
        with SessionLocal() as s2:
            w2 = s2.get(IntelWorker, w.id)
            assert w2 is not None
            offer = channel.claim(s2, w2, 1, utcnow())[0]
            res = {"output": GOOD, "model": "qwen3.6:27b", "seconds": 3.0}
            channel.complete(s2, w2, offer["id"], offer["nonce"], channel.sign(TOKEN, offer["id"], offer["nonce"], res), res, TOKEN, utcnow())

    cloud = Cloud()
    task = asyncio.create_task(pc())
    out = await router.run(db, "intel.analyze", ITEM, "slot", transport=cloud.transport(), poll_seconds=0.02)
    await task
    assert out is not None and out.result_by == "pc" and cloud.calls == 0


def test_enqueue_validates(db: Session, on: Any) -> None:
    with pytest.raises(ValueError):
        router.enqueue(db, "intel.publish", ITEM, "slot")
    with pytest.raises(ValueError):
        router.enqueue(db, "intel.analyze", {"title": " "}, "slot")
    with pytest.raises(ValueError):
        router.enqueue(db, "intel.analyze", ITEM, "urgent")


def test_admin_status(client: TestClient, db: Session, on: Any) -> None:
    pc_heartbeat(db)
    r = client.get("/api/intel/status").json()
    assert r["enabled"] and r["pc_online"] and r["ledger"]["cap_usd"] == 3.0 and r["workers"][0]["online"]
