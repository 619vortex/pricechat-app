"""Terminal Intelligence Engine: the PC <-> Railway channel (/api/intel-worker/*): scoped pci_ token stored as a hash, heartbeat-gated
claims, leases, HMAC-signed results with single-use nonces, schema validation. Also: the admin guard treats these routes correctly and
the existing newsdesk-worker routes are unaffected."""

import hashlib
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.intel import channel, router
from app.models.intel import IntelJob, IntelWorker
from app.security import admin_auth
from tests.test_intel_channel_ledger import GOOD

P = "/api/intel-worker"
TOKEN = "pci_" + "t" * 43


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> Any:
    s = get_settings()
    monkeypatch.setattr(s, "intel_enabled", True)
    monkeypatch.setattr(s, "intel_worker_token_sha256", hashlib.sha256(TOKEN.encode()).hexdigest())
    monkeypatch.setattr(s, "anthropic_api_key", None)
    return s


def H(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def beat(client: TestClient, models: list[str] | None = None) -> None:
    assert client.post(f"{P}/heartbeat", json={"models": models or ["qwen3.6:27b"]}, headers=H()).status_code == 200


def job(db: Session, kind: str = "intel.analyze", priority: str = "slot") -> IntelJob:
    payload = {"title": "Acme buys Globex for $41 billion", "outlets": ["Reuters"]} if kind == "intel.analyze" else {"text": "hello"}
    j = router.enqueue(db, kind, payload, priority)
    assert j is not None
    return j


def claim_one(client: TestClient) -> dict[str, Any]:
    r = client.post(f"{P}/claim", json={"max": 1}, headers=H())
    assert r.status_code == 200, r.text
    return r.json()["jobs"][0]


def signed(job_id: int, nonce: str, result: dict[str, Any], token: str = TOKEN) -> dict[str, Any]:
    return {"nonce": nonce, "signature": channel.sign(token, job_id, nonce, result), "result": result}


# --- auth -------------------------------------------------------------------------------------------------------------------------


def test_token_auth_hash_only_missing_wrong_and_other_prefix(client: TestClient, db: Session, on: Any) -> None:
    for headers in ({}, H("pci_wrong"), H("pcw_" + "t" * 43), H(TOKEN + "x"), {"Authorization": TOKEN[:-1]}):
        for path in ("/heartbeat", "/claim"):
            assert client.post(P + path, json={}, headers=headers).status_code == 401
    beat(client)
    w = db.query(IntelWorker).one()
    assert w.token_hash == hashlib.sha256(TOKEN.encode()).hexdigest() and TOKEN not in str(w.info)  # only the hash is stored


def test_no_configured_hash_means_no_worker_and_rotation_retires_token(client: TestClient, on: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(on, "intel_worker_token_sha256", None)
    assert client.post(f"{P}/heartbeat", json={}, headers=H()).status_code == 401
    monkeypatch.setattr(on, "intel_worker_token_sha256", hashlib.sha256(TOKEN.encode()).hexdigest())
    beat(client)
    new, digest = channel.new_token()
    monkeypatch.setattr(on, "intel_worker_token_sha256", digest)
    assert client.post(f"{P}/heartbeat", json={}, headers=H()).status_code == 401  # the old token is gone at once
    assert client.post(f"{P}/heartbeat", json={}, headers=H(new)).status_code == 200


def test_disabled_worker_is_refused(client: TestClient, db: Session, on: Any) -> None:
    beat(client)
    w = db.query(IntelWorker).one()
    w.enabled = False
    db.commit()
    assert client.post(f"{P}/heartbeat", json={}, headers=H()).status_code == 401


def test_admin_guard_lets_worker_token_through_but_admin_status_needs_login(client: TestClient, on: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert admin_auth.is_public("POST", f"{P}/claim") and admin_auth.is_public("POST", f"{P}/jobs/1/result")
    assert not admin_auth.is_public("GET", "/api/intel/status")
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(admin_auth.hash_password("pw")))
    assert client.get("/api/intel/status").status_code == 401  # owner only
    assert client.post(f"{P}/heartbeat", json={}, headers=H("pci_nope")).status_code == 401  # the worker check, not the admin one
    r = client.post(f"{P}/heartbeat", json={}, headers=H())
    assert r.status_code == 200 and r.json()["enabled"] is True  # no admin cookie needed: the token is the auth


def test_newsdesk_worker_routes_unaffected(client: TestClient, on: Any) -> None:
    assert admin_auth.is_public("POST", "/api/newsdesk-worker/claim")
    assert client.post("/api/newsdesk-worker/claim", json={}, headers=H()).status_code == 401  # a pci_ token is not a pcw_ token
    assert client.post("/api/newsdesk-worker/claim", json={}, headers=H("pcw_unknown")).status_code == 401


# --- claim / result ---------------------------------------------------------------------------------------------------------------


def test_claim_needs_fresh_heartbeat(client: TestClient, db: Session, on: Any) -> None:
    beat(client)
    job(db)
    w = db.query(IntelWorker).one()
    w.last_seen_at = utcnow() - timedelta(seconds=61)
    db.commit()
    assert client.post(f"{P}/claim", json={}, headers=H()).status_code == 409
    beat(client)
    assert client.post(f"{P}/claim", json={}, headers=H()).status_code == 200


def test_nothing_to_claim_when_disabled(client: TestClient, db: Session, on: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    beat(client)
    job(db)
    monkeypatch.setattr(on, "intel_enabled", False)
    assert client.post(f"{P}/claim", json={}, headers=H()).status_code == 204


def test_analyze_round_trip_hmac_and_replay_rejected(client: TestClient, db: Session, on: Any) -> None:
    beat(client)
    j = job(db)
    assert j.route == "pc"
    offer = claim_one(client)
    assert offer["id"] == j.id and offer["schema"]["title"] == "ItemAnalysis" and "Acme" in offer["user"]
    assert client.post(f"{P}/claim", json={}, headers=H()).status_code == 204  # leased: not offered twice
    result = {"output": GOOD, "model": "qwen3.6:27b", "seconds": 4.2}
    bad = signed(j.id, offer["nonce"], result, token="pci_other")
    assert client.post(f"{P}/jobs/{j.id}/result", json=bad, headers=H()).status_code == 403
    tampered = signed(j.id, offer["nonce"], result)
    tampered["result"] = {**result, "model": "evil"}
    assert client.post(f"{P}/jobs/{j.id}/result", json=tampered, headers=H()).status_code == 403
    wrong_nonce = signed(j.id, "0" * 32, result)
    assert client.post(f"{P}/jobs/{j.id}/result", json=wrong_nonce, headers=H()).status_code == 409
    good = signed(j.id, offer["nonce"], result)
    assert client.post(f"{P}/jobs/{j.id}/result", json=good, headers=H()).status_code == 200
    assert client.post(f"{P}/jobs/{j.id}/result", json=good, headers=H()).status_code == 409  # replay: the nonce was single use
    db.refresh(j)
    assert j.status == "done" and j.result_by == "pc" and j.result["output"]["materiality"] == "significant" and j.nonce is None
    assert j.latency_ms is not None


def test_invalid_analysis_is_rejected_and_goes_to_railway(client: TestClient, db: Session, on: Any) -> None:
    beat(client)
    j = job(db)
    offer = claim_one(client)
    bad = {"output": {**GOOD, "materiality": "enormous"}, "model": "qwen", "seconds": 1}
    assert client.post(f"{P}/jobs/{j.id}/result", json=signed(j.id, offer["nonce"], bad), headers=H()).status_code == 422
    db.refresh(j)
    assert j.status == "queued" and j.route == "cloud" and j.result is None and "rejected" in (j.error or "")


def test_embed_dimension_and_finite_checked(client: TestClient, db: Session, on: Any) -> None:
    beat(client)
    j = job(db, "intel.embed", "slot")
    offer = claim_one(client)
    assert offer["dim"] == 768 and offer["text"] == "hello"
    short = {"output": [0.1] * 767, "model": "nomic-embed-text"}
    assert client.post(f"{P}/jobs/{j.id}/result", json=signed(j.id, offer["nonce"], short), headers=H()).status_code == 422
    j2 = job(db, "intel.embed", "slot")
    offer2 = claim_one(client)
    ok = {"output": [0.25] * 768, "model": "nomic-embed-text"}
    assert client.post(f"{P}/jobs/{j2.id}/result", json=signed(j2.id, offer2["nonce"], ok), headers=H()).status_code == 200
    db.refresh(j2)
    assert j2.result_by == "pc" and len(j2.result["output"]) == 768


def test_result_after_lease_expired_refused(client: TestClient, db: Session, on: Any) -> None:
    beat(client)
    j = job(db)
    offer = claim_one(client)
    db.refresh(j)
    j.lease_until = utcnow() - timedelta(seconds=1)
    db.commit()
    r = client.post(f"{P}/jobs/{j.id}/result", json=signed(j.id, offer["nonce"], {"output": GOOD, "model": "q"}), headers=H())
    assert r.status_code == 409 and "expired" in r.text


def test_fail_is_signed_and_hands_job_to_railway(client: TestClient, db: Session, on: Any) -> None:
    beat(client)
    j = job(db)
    offer = claim_one(client)
    body = {"nonce": offer["nonce"], "signature": channel.sign(TOKEN, j.id, offer["nonce"], {"error": "ollama down"}), "error": "ollama down"}
    assert client.post(f"{P}/jobs/{j.id}/fail", json={**body, "error": "other"}, headers=H()).status_code == 403
    assert client.post(f"{P}/jobs/{j.id}/fail", json=body, headers=H()).status_code == 200
    assert client.post(f"{P}/jobs/{j.id}/fail", json=body, headers=H()).status_code == 409  # replay
    db.refresh(j)
    assert j.status == "queued" and j.route == "cloud"


def test_claim_order_breaking_first_and_cap(client: TestClient, db: Session, on: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(on, "intel_breaking_cloud_first", False)
    beat(client)
    slot = job(db, priority="slot")
    brk = job(db, priority="breaking")
    r = client.post(f"{P}/claim", json={"max": 4}, headers=H())
    assert [o["id"] for o in r.json()["jobs"]] == [brk.id, slot.id]
    assert client.post(f"{P}/claim", json={"max": 5}, headers=H()).status_code == 422
