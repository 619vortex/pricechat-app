from fastapi.testclient import TestClient

from app.providers.errors import ModelNotFoundError, ProviderTimeoutError, ProviderUnavailableError
from tests.conftest import FakeProvider


def test_health(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["llm"]["provider"] == "fake"
    assert body["llm"]["endpoint"] == "fake://local"


def test_overview_shape(client: TestClient) -> None:
    body = client.get("/api/overview").json()
    assert body["agent_name"] == "Price Chat"
    assert body["llm"]["available"] is True
    assert body["effective_model"] == "fake-model"
    assert body["social_accounts_connected"] == 0
    assert {c["status"] for c in body["connectors"]} == {"not_connected"}
    assert len(body["active_goals"]) == 6
    assert body["memory_count"] == 0
    assert any(a["event"] == "system_started" for a in body["recent_activity"])
    assert body["recent_activity"][0]["label"]


def test_agent_brain(client: TestClient) -> None:
    brain = client.get("/api/agent").json()
    assert brain["config"]["behavior_rules"]
    assert brain["available_providers"] == ["ollama"]
    assert brain["memory"]["total"] == 0
    assert brain["memory"]["embedding_model"] == "fake-embed"
    assert 0 < brain["brand_completeness"] < 100


def test_chat_end_to_end(client: TestClient, fake_provider: FakeProvider) -> None:
    client.post("/api/knowledge", json={"title": "Meetup", "content": "Community meetup every Thursday", "kind": "brand_fact"})
    resp = client.post("/api/agent/chat", json={"message": "When is the community meetup?", "conversation_id": "t-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "echo: When is the community meetup?"
    assert body["model"] == "fake-model"
    assert body["latency_ms"] == 12.5
    assert (body["prompt_tokens"], body["completion_tokens"], body["tokens_per_second"]) == (42, 7, 100.0)
    assert body["retrieval"]["memories"][0]["memory"]["title"] == "Meetup"
    assert body["retrieval"]["strategy"] == "hybrid"

    history = client.get("/api/agent/conversations/t-1").json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["memory_ids"] == [body["retrieval"]["memories"][0]["memory"]["id"]]

    model = client.get("/api/model").json()
    assert model["last_success_at"] is not None
    assert model["last_inference_latency_ms"] == 12.5


def test_chat_validation(client: TestClient) -> None:
    assert client.post("/api/agent/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/agent/chat", json={"message": "hi", "conversation_id": "../etc"}).status_code == 422
    assert client.post("/api/agent/chat", json={"message": "hi", "unknown": 1}).status_code == 422
    assert client.post("/api/agent/chat", json={"message": "x" * 8001}).status_code == 422


def test_chat_error_codes(client: TestClient, fake_provider: FakeProvider) -> None:
    cases = [
        (ProviderUnavailableError("Cannot reach Ollama"), 503, "provider_unavailable"),
        (ProviderTimeoutError("too slow"), 504, "provider_timeout"),
        (ModelNotFoundError("gemma3:27b", "Run: ollama pull gemma3:27b"), 503, "model_unavailable"),
    ]
    for error, status, code in cases:
        fake_provider.error = error
        resp = client.post("/api/agent/chat", json={"message": "Hello"})
        assert resp.status_code == status
        assert resp.json()["code"] == code
        assert resp.json()["detail"]


def test_provider_failure_and_recovery_logged(client: TestClient, fake_provider: FakeProvider) -> None:
    client.post("/api/agent/chat", json={"message": "warm up"})
    fake_provider.fail = True
    client.post("/api/agent/chat", json={"message": "fails"})
    fake_provider.fail = False
    client.post("/api/agent/chat", json={"message": "works again"})
    events = [a["event"] for a in client.get("/api/activity", params={"category": "ai"}).json()]
    assert "provider_failure" in events and "provider_recovery" in events
    assert events.index("provider_recovery") < events.index("provider_failure")  # newest first


def test_paused_agent_returns_409(client: TestClient) -> None:
    assert client.patch("/api/agent/config", json={"status": "paused"}).status_code == 200
    resp = client.post("/api/agent/chat", json={"message": "hi"})
    assert resp.status_code == 409 and resp.json()["code"] == "agent_paused"


def test_agent_config_rules(client: TestClient) -> None:
    body = client.patch("/api/agent/config", json={"behavior_rules": ["  Rule one ", "", "Rule two"]}).json()
    assert body["behavior_rules"] == ["Rule one", "Rule two"]
    prompt = client.get("/api/agent/prompt").json()
    assert "- Rule one" in prompt["text"]


def test_compiled_prompt_preview(client: TestClient) -> None:
    client.post("/api/knowledge", json={"content": "Logo colour is deep green", "tags": ["design"]})
    body = client.get("/api/agent/prompt", params={"task": "draft_post", "platform": "x", "query": "logo colour"}).json()
    keys = [s["key"] for s in body["sections"]]
    assert keys[0] == "identity" and "memory" in keys and keys[-1] == "guardrails"
    assert "280 characters" in body["text"]
    assert body["retrieval"]["memories"][0]["memory"]["content"] == "Logo colour is deep green"
    assert body["approx_tokens"] == body["chars"] // 4


def test_model_status_test_and_switch(client: TestClient, fake_provider: FakeProvider) -> None:
    status = client.get("/api/model").json()
    assert status["status"] == "online"
    assert status["provider"] == "fake" and status["endpoint"] == "fake://local"
    assert status["embedding_model"] == "fake-embed"

    test = client.post("/api/model/test").json()
    assert test["ok"] is True and test["latency_ms"] == 12.5

    switched = client.patch("/api/model", json={"model_name": "gemma3:27b", "temperature": 0.3}).json()
    assert switched["model"] == "gemma3:27b" and switched["model_overridden"] is True
    assert switched["status"] == "model_missing"  # not installed on the fake provider
    assert switched["temperature"] == 0.3

    fake_provider.installed.append("gemma3:27b")
    assert client.get("/api/model").json()["status"] in ("model_missing", "online")  # health is cached briefly
    assert client.patch("/api/model", json={"model_name": ""}).json()["model"] == "fake-model"


def test_model_test_failure(client: TestClient, fake_provider: FakeProvider) -> None:
    fake_provider.fail = True
    resp = client.post("/api/model/test")
    assert resp.status_code == 503 and resp.json()["code"] == "provider_unavailable"
    assert client.get("/api/model").json()["status"] == "offline"


def test_content_queue_and_publish_guard(client: TestClient) -> None:
    created = client.post("/api/content", json={"body": "gm", "status": "queued", "target_platform": "x"})
    assert created.status_code == 201
    assert len(client.get("/api/overview").json()["queued_content"]) == 1
    resp = client.patch(f"/api/content/{created.json()['id']}", json={"status": "published"})
    assert resp.status_code == 422


def test_connectors_not_connected(client: TestClient) -> None:
    body = client.get("/api/connectors").json()
    assert {c["platform"] for c in body} == {"x", "telegram"}
    assert all(c["status"] == "not_connected" for c in body)


def test_safety_settings_kill_switch_and_check(client: TestClient) -> None:
    settings = client.get("/api/settings/safety").json()
    assert settings["policy"]["mode"] == "approval"
    assert settings["policy"]["outbound_enabled"] is True  # social actions active, but every X action needs approval
    assert len(settings["limits"]) == 7  # X: post, reply, like, repost, follow; Telegram: post, reply

    updated = client.patch("/api/settings/safety", json={"kill_switch": True}).json()
    assert updated["policy"]["kill_switch"] is True
    assert client.get("/api/overview").json()["kill_switch"] is True

    check = client.post("/api/settings/safety/check", json={"platform": "x", "action": "post"}).json()
    assert check == {"allowed": False, "requires_approval": False, "reason": "Social actions are paused"}

    events = [a["event"] for a in client.get("/api/activity", params={"level": "audit"}).json()]
    assert "kill_switch" in events and "safety_check" in events


def test_update_action_limit(client: TestClient) -> None:
    limit = client.get("/api/settings/safety").json()["limits"][0]
    resp = client.patch(f"/api/settings/safety/limits/{limit['id']}", json={"enabled": True, "max_per_hour": 3})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True and resp.json()["max_per_hour"] == 3


def test_activity_categories_are_friendly(client: TestClient) -> None:
    client.post("/api/knowledge", json={"content": "A fact"})
    client.patch("/api/brand", json={"mission": "Help people"})
    entries = client.get("/api/activity").json()
    by_event = {e["event"]: e for e in entries}
    assert by_event["memory_created"]["category"] == "memory"
    assert by_event["memory_created"]["label"] == "Knowledge added"
    assert by_event["brand_profile_updated"]["category"] == "config"
    assert "mission" in by_event["brand_profile_updated"]["message"]
    config_only = client.get("/api/activity", params={"category": "config"}).json()
    assert {e["category"] for e in config_only} == {"config"}


def test_timestamps_are_utc_aware(client: TestClient) -> None:
    client.post("/api/knowledge", json={"content": "tz check"})
    for stamp in (client.get("/api/knowledge").json()[0]["created_at"], client.get("/api/activity").json()[0]["created_at"]):
        assert stamp.endswith("Z") or stamp.endswith("+00:00"), stamp


def test_content_patch_with_null_required_fields_is_not_a_500(client: TestClient) -> None:
    created = client.post("/api/content", json={"body": "gm", "title": "Morning", "target_platform": "x"}).json()
    resp = client.patch(f"/api/content/{created['id']}", json={"body": None, "status": None, "title": None})
    assert resp.status_code == 200
    assert (resp.json()["body"], resp.json()["title"]) == ("gm", None)  # optional fields can still be cleared
