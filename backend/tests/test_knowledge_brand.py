from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.agent import AgentConfig
from app.services.activity import REDACTED, ActivityLogger, redact
from app.providers.errors import ProviderUnavailableError
from tests.conftest import FakeEmbedder

# --- knowledge -----------------------------------------------------------------------


def test_knowledge_crud_and_search(client: TestClient) -> None:
    created = client.post(
        "/api/knowledge",
        json={"title": "Launch date", "content": "The mobile app launches on 1 November.", "kind": "brand_fact",
              "tags": ["Launch", "mobile"], "source": "Team call", "importance": 0.9},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["created"] and body["indexed"]
    memory = body["memory"]
    assert memory["layer"] == "brand" and memory["source_type"] == "manual"
    assert memory["tags"] == ["launch", "mobile"] and memory["source"] == "Team call"

    client.post("/api/knowledge", json={"content": "People love the weekly AMA", "kind": "community_insight"})

    listed = client.get("/api/knowledge").json()
    assert len(listed) == 2
    assert [m["layer"] for m in client.get("/api/knowledge", params={"layer": "community"}).json()] == ["community"]
    assert len(client.get("/api/knowledge", params={"tag": "launch"}).json()) == 1

    search = client.get("/api/knowledge/search", params={"q": "when does the app launch"}).json()
    assert search["results"][0]["memory"]["title"] == "Launch date"
    assert search["strategy"] == "hybrid"

    updated = client.patch(f"/api/knowledge/{memory['id']}", json={"content": "The mobile app launches on 8 November."}).json()
    assert updated["memory"]["content"].endswith("8 November.") and updated["indexed"]
    assert client.get(f"/api/knowledge/{memory['id']}").status_code == 200

    assert client.delete(f"/api/knowledge/{memory['id']}").status_code == 204
    assert client.delete(f"/api/knowledge/{memory['id']}").status_code == 404
    assert client.get(f"/api/knowledge/{memory['id']}").status_code == 404


def test_knowledge_validation(client: TestClient) -> None:
    assert client.post("/api/knowledge", json={"content": "   "}).status_code == 422
    assert client.post("/api/knowledge", json={"content": "x", "importance": 2}).status_code == 422
    assert client.post("/api/knowledge", json={"content": "x", "kind": "secret"}).status_code == 422
    assert client.post("/api/knowledge", json={"content": "x", "tags": ["t"] * 21}).status_code == 422
    assert client.post("/api/knowledge", json={"content": "x", "source_type": "telegram"}).status_code == 422


def test_knowledge_saved_even_if_embedder_down(client: TestClient, fake_embedder: FakeEmbedder) -> None:
    fake_embedder.error = ProviderUnavailableError("embedder offline")
    body = client.post("/api/knowledge", json={"content": "Kept despite outage"}).json()
    assert body["created"] and body["indexed"] is False
    assert client.get("/api/knowledge/stats").json()["embedded"] == 0

    fake_embedder.error = None
    assert client.post("/api/knowledge/reindex").json()["indexed"] == 1
    assert client.get("/api/knowledge/stats").json()["embedded"] == 1


def test_knowledge_sources_are_placeholders(client: TestClient) -> None:
    sources = client.get("/api/knowledge/sources").json()
    assert {s["key"] for s in sources} == {"telegram", "x", "documents", "urls"}
    assert not any(s["available"] for s in sources)
    assert {s["layer"] for s in sources if s["key"] in ("telegram", "x")} == {"community", "social"}


def test_knowledge_is_used_in_chat(client: TestClient) -> None:
    client.post("/api/knowledge", json={"content": "Price Chat's mascot is a green owl called Pip", "kind": "brand_fact"})
    body = client.post("/api/agent/chat", json={"message": "What is the mascot called?"}).json()
    assert "green owl" in body["retrieval"]["memories"][0]["memory"]["content"]


# --- brand profile -------------------------------------------------------------------


def test_brand_profile_read_update(client: TestClient) -> None:
    brand = client.get("/api/brand").json()
    assert brand["profile"]["brand_name"] == "Price Chat"
    assert "mission" in brand["empty_fields"]

    updated = client.patch(
        "/api/brand",
        json={"mission": "  Make markets understandable  ", "products_services": ["Alerts", "", "Alerts", "Community"],
              "allowed_claims": ["Free to join"]},
    ).json()
    assert updated["profile"]["mission"] == "Make markets understandable"
    assert updated["profile"]["products_services"] == ["Alerts", "Community"]
    assert "mission" not in updated["empty_fields"]
    assert updated["completeness"] > brand["completeness"]

    prompt = client.get("/api/agent/prompt").json()["text"]
    assert "Mission: Make markets understandable" in prompt
    assert "- Alerts" in prompt and "You may state:\n- Free to join" in prompt


def test_brand_validation(client: TestClient) -> None:
    assert client.patch("/api/brand", json={"brand_name": ""}).status_code == 422
    assert client.patch("/api/brand", json={"nonsense": "x"}).status_code == 422
    assert client.patch("/api/brand", json={"mission": "x" * 2001}).status_code == 422


# --- goals -----------------------------------------------------------------------------


def test_goals_crud_and_prompt(client: TestClient) -> None:
    goals = client.get("/api/goals").json()
    assert len(goals) == 6 and goals[0]["priority"] == 1

    created = client.post("/api/goals", json={"name": "Launch the newsletter", "description": "Weekly digest", "priority": 1})
    assert created.status_code == 201
    goal_id = created.json()["id"]
    assert "Launch the newsletter: Weekly digest" in client.get("/api/agent/prompt").json()["text"]

    client.patch(f"/api/goals/{goal_id}", json={"enabled": False})
    assert "Launch the newsletter" not in client.get("/api/agent/prompt").json()["text"]
    assert len(client.get("/api/overview").json()["active_goals"]) == 6

    assert client.patch(f"/api/goals/{goal_id}", json={"priority": 9}).status_code == 422
    assert client.delete(f"/api/goals/{goal_id}").status_code == 204
    assert client.patch(f"/api/goals/{goal_id}", json={"enabled": True}).status_code == 404


# --- security ----------------------------------------------------------------------------


def test_redaction_of_secrets() -> None:
    data = {"api_key": "sk-123", "nested": {"Bearer_Token": "abc", "ok": 1}, "items": [{"password": "p"}], "model": "m",
            "completion_tokens": 7, "TELEGRAM_BOT_TOKEN": "123:abc"}
    assert redact(data) == {"api_key": REDACTED, "nested": {"Bearer_Token": REDACTED, "ok": 1}, "items": [{"password": REDACTED}],
                            "model": "m", "completion_tokens": 7, "TELEGRAM_BOT_TOKEN": REDACTED}


def test_activity_logger_never_stores_secrets(db: Session, agent_config: AgentConfig) -> None:
    entry = ActivityLogger(db, agent_config.id).log("test", "msg", details={"x_api_secret": "s3cret", "count": 2})
    stored = db.get(ActivityLog, entry.id)
    assert stored is not None and stored.details == {"x_api_secret": REDACTED, "count": 2}


def test_secrets_not_exposed_by_api(client: TestClient) -> None:
    for path in ("/api/model", "/api/health", "/api/agent", "/api/overview"):
        text = client.get(path).text.lower()
        assert "api_key" not in text and "secret" not in text
