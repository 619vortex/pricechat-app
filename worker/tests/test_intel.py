"""Intel worker, offline: the signature (fixed vector, same as the server's), query-purpose embedding prefix, job validation, and the
loop's back-off when Railway is unreachable. The full round trip against the real routes is backend/tests/test_intel_worker_roundtrip.py."""

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest

from pricechat_worker import intel

TOKEN = "pci_" + "k" * 43


def test_sign_matches_the_wire_format() -> None:
    obj = {"error": "x", "a": [1, 2.5]}
    expected = hmac.new(TOKEN.encode(), b'3\nn0nce\n{"a":[1,2.5],"error":"x"}', hashlib.sha256).hexdigest()
    assert intel.sign(TOKEN, 3, "n0nce", obj) == expected


def _ollama(seen: list[Any]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "nomic-embed-text:latest"}]})
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[1.0] * 768]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_query_embedding_uses_query_prefix() -> None:
    seen: list[Any] = []
    cfg = intel.Config(server="https://x.invalid", token=TOKEN)
    out = intel.do_job({"id": 1, "kind": "intel.embed", "text": "btc etf", "purpose": "query", "dim": 768}, intel.Ollama(cfg, _ollama(seen)), cfg, 10)
    assert seen[0]["input"] == "search_query: btc etf" and len(out["output"]) == 768


def test_bad_jobs_are_job_errors() -> None:
    cfg = intel.Config(server="https://x.invalid", token=TOKEN)
    ol = intel.Ollama(cfg, _ollama([]))
    for j in ({"kind": "intel.other"}, {"kind": "intel.analyze", "user": "u"}, {"kind": "intel.embed", "text": " "}):
        with pytest.raises(intel.JobError):
            intel.do_job(j, ol, cfg, 10)


def test_run_backs_off_when_railway_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def down(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectError("unreachable", request=request)

    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        if sum(slept) > 40:
            monkeypatch.setattr(intel, "STOP", True)

    monkeypatch.setattr(intel, "STOP", False)
    monkeypatch.setattr(intel.Worker, "_beat_loop", lambda self: None)  # no background thread in the test
    cfg = intel.Config(server="https://x.invalid", token=TOKEN, poll_s=5)
    w = intel.Worker(cfg, server_http=httpx.Client(transport=httpx.MockTransport(down)), ollama_http=_ollama([]), sleep=sleep)
    assert w.run() == 0
    assert calls and all(p.endswith("/heartbeat") for p in calls) and w.backoff > 5  # backed off, never crashed


def test_growth_write_uses_the_chat_model_with_the_jobs_schema() -> None:
    """growth.write (Growth Engine Originals writer): same Ollama chat path as intel.analyze, JSON {"text"} with {vN} placeholders."""
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": json.dumps({"text": "Bitcoin {v1} in {v2} days."})}})

    cfg = intel.Config(server="https://x.invalid", token=TOKEN, model="qwen3.8:27b")
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    out = intel.do_job({"id": 2, "kind": "growth.write", "system": "s", "user": "u", "schema": schema},
                       intel.Ollama(cfg, httpx.Client(transport=httpx.MockTransport(handler))), cfg, 10)
    assert out["output"] == {"text": "Bitcoin {v1} in {v2} days."} and out["model"] == "qwen3.8:27b"
    assert seen[0]["format"] == schema and seen[0]["think"] is False and seen[0]["messages"][1]["content"] == "u"
    with pytest.raises(intel.JobError):
        intel.do_job({"id": 3, "kind": "growth.write", "user": "u"}, intel.Ollama(cfg, httpx.Client(transport=httpx.MockTransport(handler))), cfg, 10)
