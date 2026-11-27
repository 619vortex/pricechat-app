"""Terminal Intelligence Engine: the owner-PC worker (worker/pricechat_worker/intel.py) against the REAL /api/intel-worker routes, in-process.
FastAPI's TestClient (an httpx.Client) stands in for Railway, httpx.MockTransport for the local Ollama. Proves the worker's canonical()/sign()
match the server's byte for byte, that a heartbeat -> claim -> signed result is accepted, that a tampered or wrongly-keyed signature is
refused, and that every failure (Ollama erroring or down) is reported signed and re-routes the job to Railway (cloud)."""

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.intel import channel
from app.models.intel import IntelJob, IntelWorker
from tests.test_intel_channel_api import TOKEN, job, on  # noqa: F401  (on: the fixture)
from tests.test_intel_channel_ledger import GOOD

WORKER_FILE = Path(__file__).resolve().parents[2] / "worker" / "pricechat_worker" / "intel.py"


def _load_worker() -> Any:
    """The worker module straight from its file (it runs in its own venv on the PC; no backend imports either way)."""
    spec = importlib.util.spec_from_file_location("pc_intel_worker", WORKER_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


intel = _load_worker()


class FakeOllama:
    """/api/tags, /api/chat, /api/embed. mode: ok | error (HTTP 500 on chat/embed) | down (connection refused everywhere)."""

    def __init__(self, models: list[str] | None = None, analysis: Any = GOOD, dims: int = 768) -> None:
        self.models = ["qwen3.6:27b", "nomic-embed-text:latest"] if models is None else models
        self.analysis, self.dims, self.mode = analysis, dims, "ok"
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if self.mode == "down":
                raise httpx.ConnectError("connection refused", request=request)
            path = request.url.path
            if path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
            body = json.loads(request.content)
            self.seen.append((path, body))
            if self.mode == "error":
                return httpx.Response(500, json={"error": "model failed to load"})
            if path == "/api/chat":
                text = self.analysis if isinstance(self.analysis, str) else json.dumps(self.analysis)
                return httpx.Response(200, json={"message": {"role": "assistant", "content": text}, "done": True})
            if path == "/api/embed":
                return httpx.Response(200, json={"embeddings": [[((i % 7) - 3) / 10 for i in range(self.dims)]]})
            return httpx.Response(404)
        return httpx.MockTransport(handler)


def make_worker(client: TestClient, ollama: FakeOllama, token: str = TOKEN, **kw: Any) -> Any:
    cfg = intel.Config(server=str(client.base_url), token=token, model="qwen3.6:27b", embed_model="nomic-embed-text",
                       ollama="http://127.0.0.1:11434", gpu="RTX 5090", **kw)
    return intel.Worker(cfg, server_http=client, ollama_http=httpx.Client(transport=ollama.transport()), sleep=lambda s: None)


def test_worker_signature_is_byte_for_byte_the_servers() -> None:
    samples: list[Any] = [{"output": GOOD, "model": "qwen3.6:27b", "seconds": 4.25}, {"error": "ollama down — café"},
                          {"output": [0.1, -1e-9, 3.0], "model": "nomic-embed-text", "seconds": 0}, {"b": 1, "a": [None, True, "ü"]}]
    for obj in samples:
        assert intel.canonical(obj) == channel.canonical(obj)
        assert intel.sign(TOKEN, 7, "ab" * 16, obj) == channel.sign(TOKEN, 7, "ab" * 16, obj)
        assert channel.verify(TOKEN, 7, "ab" * 16, obj, intel.sign(TOKEN, 7, "ab" * 16, obj))
    with pytest.raises(ValueError):
        intel.canonical({"x": math.nan})  # same refusal as the server


def test_heartbeat_claim_signed_result_accepted(client: TestClient, db: Session, on: Any) -> None:  # noqa: F811
    fake = FakeOllama()
    w = make_worker(client, fake)
    w.beat()
    rec = db.query(IntelWorker).one()
    assert rec.info["models"] == ["nomic-embed-text:latest", "qwen3.6:27b"] and rec.info["busy"] is False
    assert rec.info["version"] == intel.VERSION and rec.info["gpu"] == "RTX 5090" and TOKEN not in json.dumps(rec.info)
    j = job(db)
    assert j.route == "pc"
    assert w.step() == 1
    db.refresh(j)
    assert j.status == "done" and j.result_by == "pc" and j.result["model"] == "qwen3.6:27b" and j.nonce is None
    assert j.result["output"]["materiality"] == "significant" and isinstance(j.result["seconds"], float)
    path, body = fake.seen[0]
    assert path == "/api/chat" and body["format"]["title"] == "ItemAnalysis" and body["stream"] is False
    assert body["think"] is False and body["options"] == {"temperature": 0, "num_ctx": 4096} and body["keep_alive"] == "2m"
    assert "Acme" in body["messages"][1]["content"] and body["messages"][0]["role"] == "system"
    assert w.step() == 0 and w.done == 1  # nothing left (204)


def test_embed_round_trip_prefix_matryoshka_and_norm(client: TestClient, db: Session, on: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(on, "intel_embed_dim", 256)
    fake = FakeOllama()
    w = make_worker(client, fake)
    w.beat()
    j = job(db, "intel.embed")
    assert w.step() == 1
    db.refresh(j)
    assert j.status == "done" and j.result_by == "pc" and j.result["model"] == "nomic-embed-text"
    vec = j.result["output"]
    assert len(vec) == 256 and abs(math.sqrt(sum(x * x for x in vec)) - 1) < 1e-9
    path, body = fake.seen[0]
    assert path == "/api/embed" and body["model"] == "nomic-embed-text" and body["input"] == "search_document: hello"


def test_tampered_or_wrongly_keyed_signature_refused(client: TestClient, db: Session, on: Any) -> None:  # noqa: F811
    fake = FakeOllama()
    w = make_worker(client, fake)
    w.beat()
    j = job(db)
    [offer] = w.server.claim(1)
    result = intel.do_job(offer, w.ollama, w.cfg, timeout=30)
    body = {"nonce": offer["nonce"], "signature": intel.sign(TOKEN, j.id, offer["nonce"], result), "result": result}
    tampered = {**body, "result": {**result, "output": {**result["output"], "materiality": "major"}}}
    assert client.post(f"/api/intel-worker/jobs/{j.id}/result", json=tampered, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 403
    w.server._token = "pci_" + "x" * 43  # right bearer token, wrong HMAC key: the worker's own result path is refused
    assert w.handle(offer) == "rejected"
    db.refresh(j)
    assert j.status == "leased" and j.result is None  # nothing stored; the nonce is still unused
    w.server._token = TOKEN
    assert w.handle(offer) == "done"  # the honest signature over the same nonce still goes through
    db.refresh(j)
    assert j.status == "done" and j.result_by == "pc"


def test_ollama_error_posts_signed_fail_and_job_goes_to_cloud(client: TestClient, db: Session, on: Any) -> None:  # noqa: F811
    fake = FakeOllama()
    fake.mode = "error"
    w = make_worker(client, fake)
    w.beat()
    j = job(db)
    assert w.step() == 1 and w.failed == 1
    db.refresh(j)
    assert j.status == "queued" and j.route == "cloud" and "pc failed" in (j.error or "") and "HTTP 500" in (j.error or "") and j.nonce is None


def test_non_json_reply_fails_to_cloud(client: TestClient, db: Session, on: Any) -> None:  # noqa: F811
    w = make_worker(client, FakeOllama(analysis="sorry, I can't"))
    w.beat()
    j = job(db)
    assert w.step() == 1
    db.refresh(j)
    assert j.status == "queued" and j.route == "cloud" and "not JSON" in (j.error or "")


def test_ollama_down_keeps_heartbeating_claims_nothing_and_fails_held_jobs(client: TestClient, db: Session, on: Any) -> None:  # noqa: F811
    fake = FakeOllama()
    w = make_worker(client, fake)
    w.beat()
    held = job(db)
    [offer] = w.server.claim(1)
    queued = job(db)
    fake.mode = "down"
    w.last_beat = float("-inf")
    assert w.step() == 0  # heartbeat sent (models=[], busy), no claim made
    rec = db.query(IntelWorker).one()
    db.refresh(rec)
    assert rec.info["models"] == [] and rec.info["busy"] is True
    db.refresh(queued)
    assert queued.status == "queued" and queued.route == "pc"  # left for Railway's claim window / fallback
    assert w.handle(offer) == "failed"  # a job held when Ollama died is handed back signed, at once
    db.refresh(held)
    assert held.status == "queued" and held.route == "cloud" and "ollama is not reachable" in (held.error or "")


def test_missing_analysis_model_fails_job_to_cloud(client: TestClient, db: Session, on: Any) -> None:  # noqa: F811
    w = make_worker(client, FakeOllama(models=["nomic-embed-text:latest"]))
    w.beat()
    j = job(db)
    assert w.step() == 1
    db.refresh(j)
    assert j.route == "cloud" and "not installed" in (j.error or "")


def test_railway_unreachable_backs_off_and_unknown_token_is_refused(client: TestClient, on: Any) -> None:  # noqa: F811
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    cfg = intel.Config(server="https://railway.invalid", token=TOKEN, ollama="http://127.0.0.1:11434")
    w = intel.Worker(cfg, server_http=httpx.Client(transport=httpx.MockTransport(down)),
                     ollama_http=httpx.Client(transport=FakeOllama().transport()), sleep=lambda s: None)
    with pytest.raises(httpx.HTTPError):
        w.step()
    stranger = make_worker(client, FakeOllama(), token="pci_" + "z" * 43)
    with pytest.raises(intel.TokenRefused):
        stranger.beat()
    assert TOKEN not in repr(cfg) and "set" in repr(cfg)


def test_short_lease_is_handed_straight_back(client: TestClient, db: Session, on: Any) -> None:  # noqa: F811
    fake = FakeOllama()
    w = make_worker(client, fake)
    w.beat()
    j = job(db)
    [offer] = w.server.claim(1)
    offer["lease_until"] = "2000-01-01T00:00:00+00:00"  # as if the claim reply arrived late
    assert w.handle(offer) == "failed" and not fake.seen  # Ollama never called
    db.refresh(j)
    assert j.route == "cloud" and "lease nearly over" in (j.error or "")


def test_think_flags_and_model_names() -> None:
    assert intel.think_for("qwen3.6:27b") is False and intel.think_for("deepseek-r1:14b") is False
    assert intel.think_for("gpt-oss:20b") == "low" and intel.think_for("gemma3:27b") is None
    assert intel.has_model(["nomic-embed-text:latest"], "nomic-embed-text") and not intel.has_model(["qwen3:8b"], "qwen3.6:27b")
    v = intel.matryoshka([3.0, 4.0], 2)
    assert v == [0.6, 0.8]
    with pytest.raises(intel.JobError):
        intel.matryoshka([1.0] * 4, 8)
