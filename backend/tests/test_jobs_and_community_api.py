import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.activity import ActivityLog
from app.models.memory import Memory
from app.models.routing import ScheduledJob
from app.models.telegram import TelegramMessage, TelegramMessageVector
from app.scheduler.scheduler import JobContext, JobSkipped, JobSpec, Scheduler
from tests.conftest import FakeProvider
from tests.fakes import FakeTelegram

FUNDING = [
    "funding rates are spiking on bitcoin today",
    "bitcoin funding rates are spiking again",
    "why are funding rates spiking on bitcoin",
    "funding rates on bitcoin are spiking hard today",
]
QUESTIONS = ["what does price chat actually do?", "what does price chat do exactly?", "so what does price chat actually do?"]


# --- scheduler ------------------------------------------------------------------------------------


def _scheduler(func, interval: int = 60) -> Scheduler:  # type: ignore[no-untyped-def]
    s = Scheduler(SessionLocal, [JobSpec("test.job", "Test job", "testing", interval, func, initial_delay_seconds=0, max_backoff_seconds=600)])
    s.sync_jobs()
    return s


def _row() -> ScheduledJob:
    with SessionLocal() as db:
        row = db.get(ScheduledJob, "test.job")
        assert row is not None
        db.expunge(row)
        return row


async def test_scheduler_success_failure_backoff_and_recovery() -> None:
    state = {"fail": True}

    async def job(ctx: JobContext) -> dict:
        if state["fail"]:
            raise RuntimeError("boom")
        return {"done": 1}

    s = _scheduler(job)
    first = await s.run_job("test.job")
    assert first.status == "failed" and "boom" in (first.error or "")
    r1 = _row()
    assert r1.consecutive_failures == 1 and r1.next_run_at is not None
    await s.run_job("test.job")
    r2 = _row()
    # Backoff doubles (60s → 120s) and is capped; never a tight retry loop.
    assert r2.consecutive_failures == 2
    assert (r2.next_run_at - r2.last_finished_at) >= timedelta(seconds=119)

    state["fail"] = False
    ok = await s.run_job("test.job")
    r3 = _row()
    assert ok.status == "success" and r3.consecutive_failures == 0 and r3.last_result == {"done": 1} and r3.last_error is None
    with SessionLocal() as db:
        events = list(db.scalars(select(ActivityLog.event).where(ActivityLog.event.in_(["job_failed", "job_recovered"]))))
    assert events == ["job_failed", "job_recovered"]  # one failure event per streak, not per retry


async def test_scheduler_backoff_is_capped() -> None:
    async def job(ctx: JobContext) -> dict:
        raise RuntimeError("down")

    s = _scheduler(job)
    for _ in range(8):
        await s.run_job("test.job")
    row = _row()
    assert (row.next_run_at - row.last_finished_at) <= timedelta(seconds=601)


async def test_scheduler_skip_no_overlap_and_tick() -> None:
    gate = asyncio.Event()
    runs = {"n": 0}

    async def job(ctx: JobContext) -> dict:
        runs["n"] += 1
        await gate.wait()
        return {}

    s = _scheduler(job)
    started = await s.tick()
    assert started == ["test.job"]
    await asyncio.sleep(0.05)
    assert (await s.run_job("test.job", trigger="manual")).status == "busy"
    assert await s.tick() == []  # already running: not started twice
    gate.set()
    await asyncio.sleep(0.05)
    assert runs["n"] == 1 and _row().last_status == "success"

    async def skipper(ctx: JobContext) -> dict:
        raise JobSkipped("nothing to do")

    s2 = Scheduler(SessionLocal, [JobSpec("test.job", "Test job", "testing", 60, skipper)])
    out = await s2.run_job("test.job")
    assert out.status == "skipped" and _row().consecutive_failures == 0


async def test_interrupted_job_is_marked_on_restart() -> None:
    async def job(ctx: JobContext) -> dict:
        return {}

    _scheduler(job)
    with SessionLocal() as db:
        row = db.get(ScheduledJob, "test.job")
        assert row is not None
        row.last_status = "running"
        db.commit()
    _scheduler(job)  # simulated restart
    assert _row().last_status == "failed" and _row().last_error == "Interrupted by a restart"


async def test_job_timeout() -> None:
    async def slow(ctx: JobContext) -> dict:
        await asyncio.sleep(5)
        return {}

    s = Scheduler(SessionLocal, [JobSpec("test.job", "Slow", "x", 60, slow, timeout_seconds=0)])  # type: ignore[arg-type]
    s.sync_jobs()
    s.specs["test.job"].timeout_seconds = 0.05  # type: ignore[assignment]
    out = await s.run_job("test.job")
    assert out.status == "failed" and "Timed out" in (out.error or "")


# --- end-to-end community pipeline through the API --------------------------------------------------


def _ingest(tg_client: TestClient, tg: FakeTelegram) -> None:
    tg_client.post("/api/telegram/sources", json={"chat_id": -100111})
    for i, text in enumerate(FUNDING):
        tg.message(-100111, text, user_id=10 + i, name=f"Member{i}", date=int(utcnow().timestamp()) - 3600 + i)
    for i, text in enumerate(QUESTIONS):
        tg.message(-100111, text, user_id=20 + i % 2, name=f"Asker{i}", date=int(utcnow().timestamp()) - 1800 + i)
    tg.message(-100111, "gm", user_id=30)
    assert tg_client.post("/api/telegram/sync").json()["result"]["stored"] == 8
    for job in ("community.process", "community.topics"):
        assert tg_client.post(f"/api/jobs/{job}/run").json()["status"] == "success"


def test_community_pipeline_via_api(tg_client: TestClient, fake_telegram: FakeTelegram, fake_provider: FakeProvider) -> None:
    _ingest(tg_client, fake_telegram)

    overview = tg_client.get("/api/community/overview").json()
    assert overview["messages_total"] == 8 and overview["unprocessed"] == 0
    assert overview["active_members_today"] >= 1
    assert overview["top_topics"][0]["message_count"] == 4
    assert overview["recurring_questions"][0]["ask_count"] == 3

    topic_id = overview["top_topics"][0]["id"]
    detail = tg_client.get(f"/api/community/topics/{topic_id}").json()
    assert len(detail["recent"]) == 4 and detail["representative"] and detail["volume_by_day"]

    q = overview["recurring_questions"][0]
    msgs = tg_client.get(f"/api/community/questions/{q['id']}/messages").json()
    assert len(msgs) == 3

    # Summary on demand, with provenance back to the exact messages.
    summary = tg_client.post("/api/community/summaries", json={"kind": "recent", "hours": 6}).json()
    sdetail = tg_client.get(f"/api/community/summaries/{summary['id']}").json()
    assert len(sdetail["messages"]) == 7 and summary["model"] == "fast-model"  # recent summary → fast tier
    assert tg_client.post("/api/community/summaries", json={"kind": "topic"}).status_code == 422

    # Memory extraction → pending review → approve.
    assert tg_client.post("/api/jobs/community.memories/run").json()["result"]["created"] == 1
    pending = tg_client.get("/api/knowledge", params={"review_status": "pending"}).json()
    assert len(pending) == 1 and pending[0]["layer"] == "community" and pending[0]["extra"]["generated_by"]["model"] == "strong-model"
    prov = tg_client.get(f"/api/community/provenance/memory/{pending[0]['id']}").json()
    assert prov["messages"] and prov["generated"]["confidence"] == 0.8
    assert tg_client.get("/api/knowledge/search", params={"q": "how the product works"}).json()["results"] == []
    approved = tg_client.patch(f"/api/knowledge/{pending[0]['id']}", json={"review_status": "approved", "importance": 0.9}).json()
    assert approved["memory"]["review_status"] == "approved" and approved["memory"]["importance"] == 0.9
    events = [a["event"] for a in tg_client.get("/api/activity", params={"category": "memory"}).json()]
    assert "memory_reviewed" in events and "community_memories_proposed" in events

    # Content opportunities → review → draft. Never published.
    assert tg_client.post("/api/jobs/content.opportunities/run").json()["result"]["created"] >= 1
    opp = tg_client.get("/api/opportunities", params={"status": "detected"}).json()[0]
    assert tg_client.get(f"/api/opportunities/{opp['id']}").json()["messages"]
    assert tg_client.patch(f"/api/opportunities/{opp['id']}", json={"status": "published"}).status_code == 422
    assert tg_client.patch(f"/api/opportunities/{opp['id']}", json={"status": "approved"}).json()["status"] == "approved"
    draft = tg_client.post(f"/api/opportunities/{opp['id']}/draft", json={"channel": "youtube_shorts"})
    assert draft.status_code == 201 and draft.json()["status"] == "draft"
    assert tg_client.get(f"/api/opportunities/{opp['id']}").json()["opportunity"]["status"] == "drafted"

    # Search with a grounded answer.
    found = tg_client.get("/api/community/search", params={"q": "bitcoin funding rates", "answer": True}).json()
    assert found["messages"] and found["messages"][0]["source_name"] == "Price Chat Community"
    assert found["answer"]["cited"] and found["answer"]["model"] == "fast-model"

    # FAQ from a recurring question: an approved brand memory written by a human.
    faq = tg_client.post(f"/api/community/questions/{q['id']}/faq", json={"answer": "It turns market chat into insight."}).json()
    assert faq["status"] == "answered" and faq["faq_memory_id"]


def test_jobs_api_and_model_routes(tg_client: TestClient) -> None:
    jobs = {j["name"]: j for j in tg_client.get("/api/jobs").json()}
    assert set(jobs) >= {"telegram.ingest", "community.process", "community.topics", "community.summary.recent",
                         "community.summary.daily", "community.memories", "content.opportunities", "knowledge.reindex"}
    assert tg_client.patch("/api/jobs/community.topics", json={"enabled": False}).status_code == 200
    assert {j["name"]: j for j in tg_client.get("/api/jobs").json()}["community.topics"]["enabled"] is False
    assert tg_client.post("/api/jobs/nope/run").status_code == 404

    routes = tg_client.get("/api/model/routes").json()
    assert routes["tiers"] == {"fast": "fast-model", "strong": "strong-model", "default": "fake-model"}
    by_task = {r["task"]: r for r in routes["routes"]}
    assert by_task["telegram.classify"]["model"] == "fast-model" and by_task["content.opportunity"]["model"] == "strong-model"
    updated = tg_client.put("/api/model/routes/telegram.classify", json={"tier": "strong"}).json()
    assert {r["task"]: r for r in updated["routes"]}["telegram.classify"]["model"] == "strong-model"
    reset = tg_client.put("/api/model/routes/telegram.classify", json={}).json()
    assert {r["task"]: r for r in reset["routes"]}["telegram.classify"]["customised"] is False
    assert tg_client.put("/api/model/routes/telegram.classify", json={"tier": "huge"}).status_code == 422
    assert tg_client.put("/api/model/routes/unknown.task", json={"tier": "fast"}).status_code == 404


def test_model_failure_during_pipeline(tg_client: TestClient, fake_telegram: FakeTelegram, fake_provider: FakeProvider) -> None:
    _ingest(tg_client, fake_telegram)
    fake_provider.fail = True
    run = tg_client.post("/api/jobs/content.opportunities/run").json()
    assert run["status"] == "failed" and "offline" in run["error"]
    assert tg_client.get("/api/opportunities").json() == []  # nothing half-saved
    assert tg_client.post("/api/community/summaries", json={"kind": "recent"}).status_code == 503
    assert tg_client.get("/api/community/summaries").json() == []
    assert tg_client.get("/api/health").status_code == 200
    fake_provider.fail = False
    assert tg_client.post("/api/jobs/content.opportunities/run").json()["status"] == "success"


def test_delete_source_removes_all_its_data(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    _ingest(tg_client, fake_telegram)
    tg_client.post("/api/community/summaries", json={"kind": "recent", "hours": 6})
    tg_client.post("/api/jobs/community.memories/run")
    tg_client.post("/api/jobs/content.opportunities/run")
    source = tg_client.get("/api/telegram/status").json()["sources"][0]

    report = tg_client.delete(f"/api/telegram/sources/{source['id']}").json()
    assert report["messages"] == 8 and report["summaries"] >= 1 and report["memories"] == 1 and report["opportunities"] >= 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TelegramMessage)) == 0
        assert db.scalar(select(func.count()).select_from(TelegramMessageVector)) == 0
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.layer == "community")) == 0
    overview = tg_client.get("/api/community/overview").json()
    assert overview["top_topics"] == [] and overview["recurring_questions"] == [] and overview["latest_summary"] is None
    assert tg_client.get("/api/telegram/status").json()["sources"] == []
    audit = [a for a in tg_client.get("/api/activity", params={"level": "audit"}).json() if a["event"] == "telegram_source_deleted"]
    assert audit and "deleted all its data" in audit[0]["message"]


def test_agent_chat_uses_approved_community_memory(tg_client: TestClient, fake_telegram: FakeTelegram, fake_provider: FakeProvider) -> None:
    _ingest(tg_client, fake_telegram)
    tg_client.post("/api/community/summaries", json={"kind": "recent", "hours": 6})
    tg_client.post("/api/jobs/community.memories/run")
    memory = tg_client.get("/api/knowledge", params={"review_status": "pending"}).json()[0]

    reply = tg_client.post("/api/agent/chat", json={"message": "Do members ask how the product works?"}).json()
    assert all(m["memory"]["id"] != memory["id"] for m in reply["retrieval"]["memories"])  # pending: not used

    tg_client.patch(f"/api/knowledge/{memory['id']}", json={"review_status": "approved"})
    reply = tg_client.post("/api/agent/chat", json={"message": "Do members ask how the product works?"}).json()
    assert any(m["memory"]["id"] == memory["id"] for m in reply["retrieval"]["memories"])
    system = fake_provider.calls[-1][0][0].content
    assert "[community; community insight" in system and "not verified brand facts" in system


def test_prompt_preview_includes_community_context(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    _ingest(tg_client, fake_telegram)
    body = tg_client.get("/api/agent/prompt", params={"query": "What is the community saying: are bitcoin funding rates spiking today?"}).json()
    assert "community" in [s["key"] for s in body["sections"]]
    assert "## Community intelligence" in body["text"]


async def test_request_run_brings_job_forward_but_never_delays() -> None:
    async def job(ctx: JobContext) -> dict:
        return {}

    s = Scheduler(SessionLocal, [JobSpec("test.job", "Test job", "x", 3600, job, initial_delay_seconds=3000)])
    s.sync_jobs()
    far = _row().next_run_at
    s.request_run("test.job", delay_seconds=5)
    soon = _row().next_run_at
    assert soon < far and soon <= utcnow() + timedelta(seconds=6)
    s.request_run("test.job", delay_seconds=600)
    assert _row().next_run_at == soon  # a later request never pushes it back


def test_pipeline_stages_chain(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    tg_client.post("/api/telegram/sources", json={"chat_id": -100111})
    for i, text in enumerate(FUNDING):
        fake_telegram.message(-100111, text, user_id=10 + i, date=int(utcnow().timestamp()) - 60)
    tg_client.post("/api/telegram/sync")
    jobs = {j["name"]: j for j in tg_client.get("/api/jobs").json()}
    # Scheduler loop is off in tests, so next_run_at is hidden; check the stored rows instead.
    with SessionLocal() as db:
        process = db.get(ScheduledJob, "community.process")
        assert process is not None and process.next_run_at <= utcnow() + timedelta(seconds=10)
    assert "community.process" in jobs
    tg_client.post("/api/jobs/community.process/run")
    with SessionLocal() as db:
        topics = db.get(ScheduledJob, "community.topics")
        assert topics is not None and topics.next_run_at <= utcnow() + timedelta(seconds=15)
