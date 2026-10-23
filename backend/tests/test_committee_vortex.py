"""Committee LLM calls over the Vortex worker channel, through the real HTTP routes: claim (next to trade checks), answer
with model + tokens, fail, late answers, expiry, and a full VortexBackend round trip. No network."""

import asyncio
import json
import threading
import time
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.committee import llm, remote
from app.committee.llm import Call
from app.committee.schemas import TraderOut
from app.db.base import utcnow

TOKEN = "vx_" + "c0ffee12" * 5


@pytest.fixture
def auth() -> dict[str, str]:
    from app.security.credentials import secret_store

    secret_store().set("vortex_assist_token", TOKEN)
    return {"Authorization": f"Bearer {TOKEN}"}


def _job(db: Session, **kw: Any) -> Any:
    return remote.create(db, role=kw.get("role", "trader"), tier=kw.get("tier", "fast"), system="SYS", prompt="PROMPT",
                         schema=TraderOut.model_json_schema(), max_tokens=600, temperature=0.1)


def test_claim_answer_roundtrip(client: TestClient, db: Session, auth: dict[str, str]) -> None:
    j = _job(db)
    assert client.post("/api/vortex/claim", json={"max": 3, "wait": 0}).status_code == 401  # bearer token required
    jobs = client.post("/api/vortex/claim", json={"max": 3, "wait": 0}, headers=auth).json()["jobs"]
    assert jobs == [{"id": j.public_id, "kind": "llm", "system": "SYS", "prompt": "PROMPT", "json_schema": TraderOut.model_json_schema(),
                     "max_tokens": 600, "temperature": 0.1, "tier": "fast", "lease_seconds": 60}]
    assert client.post("/api/vortex/claim", json={"max": 3, "wait": 0}, headers=auth).json()["jobs"] == []  # leased
    text = json.dumps({"action": "WAIT", "rationale": "No trigger yet. <b>raw</b> https://x.test"})
    r = client.post(f"/api/vortex/jobs/{j.public_id}/answer", json={"text": text, "model": "qwen3:32b", "tokens_in": 812, "tokens_out": 64},
                    headers=auth)
    assert r.status_code == 200
    db.refresh(j)
    assert (j.status, j.answer, j.model, j.tokens_in, j.tokens_out) == ("answered", text, "qwen3:32b", 812, 64)  # raw, not sanitised
    again = client.post(f"/api/vortex/jobs/{j.public_id}/answer", json={"text": "x"}, headers=auth)
    assert again.status_code == 409
    assert remote.pending_count(db) == 0


def test_fail_late_answer_and_expiry(client: TestClient, db: Session, auth: dict[str, str]) -> None:
    a, b = _job(db), _job(db, role="bull", tier="deep")
    assert client.post(f"/api/vortex/jobs/{a.public_id}/fail", json={"reason": "invalid_json"}, headers=auth).json() == {"ok": True}
    db.refresh(a)
    assert a.status == "failed"
    b.created_at = utcnow() - timedelta(seconds=95)
    db.commit()
    assert client.post("/api/vortex/claim", json={"max": 3, "wait": 0}, headers=auth).json()["jobs"] == []  # too old to hand out
    r = client.post(f"/api/vortex/jobs/{b.public_id}/answer", json={"text": "{}"}, headers=auth)
    assert r.status_code == 409 and "Too late" in r.json()["detail"]
    db.refresh(b)
    assert b.status == "expired"
    assert client.post("/api/vortex/jobs/l_nope/answer", json={"text": "{}"}, headers=auth).status_code == 404


def test_trade_checks_still_come_first_and_keep_their_shape(client: TestClient, db: Session, auth: dict[str, str]) -> None:
    from app.models.tradingos import TsAssistConvo, TsAssistJob

    c = TsAssistConvo(chat_id=1, tg_user_id=2, coin="BTC", side="long", trade={}, facts={"price": 1}, followups=0, created_at=utcnow())
    db.add(c)
    db.flush()
    db.add(TsAssistJob(public_id="j_test", convo_id=c.id, kind="assess", status="pending", tg_user_id=2, chat_id=1, question="q",
                       facts={"price": 1}, fallback="fb", claims=0, created_at=utcnow()))
    db.commit()
    lj = _job(db)
    jobs = client.post("/api/vortex/claim", json={"max": 3, "wait": 0}, headers=auth).json()["jobs"]
    assert [j["id"] for j in jobs] == ["j_test", lj.public_id]
    assert jobs[0]["kind"] == "assess" and "facts" in jobs[0]


def test_vortex_backend_round_trip_through_http(client: TestClient, db: Session, auth: dict[str, str]) -> None:
    backend = llm.VortexBackend(timeout=10, poll=0.05)
    out: list[Any] = []
    t = threading.Thread(target=lambda: out.append(asyncio.run(backend.complete(
        Call("trader", "committee.trader", "SYS", "PROMPT", TraderOut, max_tokens=600, temperature=0.1)))))
    t.start()
    job = None
    for _ in range(100):
        jobs = client.post("/api/vortex/claim", json={"max": 1, "wait": 0}, headers=auth).json()["jobs"]
        if jobs:
            job = jobs[0]
            break
        time.sleep(0.05)
    assert job is not None and job["tier"] == "fast" and job["json_schema"]["title"] == "TraderOut"
    client.post(f"/api/vortex/jobs/{job['id']}/answer", json={"text": '{"action": "WAIT", "rationale": "ok"}', "model": "vx-8b",
                                                             "tokens_in": 5, "tokens_out": 3}, headers=auth)
    t.join(10)
    assert out and out[0].text == '{"action": "WAIT", "rationale": "ok"}' and out[0].model == "vx-8b" and out[0].tokens_in == 5


def test_vortex_backend_timeout_means_rules(db: Session) -> None:
    led = llm.Ledger(db, llm.VortexBackend(timeout=0.2, poll=0.05))
    parsed, _, note = asyncio.run(led.ask(Call("trader", "committee.trader", "S", "P", TraderOut)))
    assert parsed is None and note is not None and "Vortex timed out" in note
    j = db.query(remote.CmLlmJob).one()
    db.refresh(j)
    assert j.status == "expired"
