import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.security.secrets import SecretStore, dpapi_available, protect, unprotect
from tests.fakes import TOKEN, FakeTelegram


def test_secret_store_round_trip_is_encrypted(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "s.dpapi")
    store.set("telegram_bot_token", TOKEN)
    raw = (tmp_path / "s.dpapi").read_text(encoding="utf-8")
    assert TOKEN not in raw  # never stored in clear text (DPAPI on Windows)
    assert store.get("telegram_bot_token") == TOKEN
    assert store.saved_at("telegram_bot_token") is not None
    assert store.delete("telegram_bot_token") and store.get("telegram_bot_token") is None
    with pytest.raises(ValueError):
        store.set("x", "   ")


@pytest.mark.skipif(not dpapi_available(), reason="DPAPI is Windows-only")
def test_dpapi_protect_unprotect() -> None:
    blob = protect(b"secret")
    assert blob != b"secret" and unprotect(blob) == b"secret"


def _token_absent(resp_text: str) -> None:
    assert TOKEN not in resp_text and TOKEN.split(":")[1] not in resp_text


def test_telegram_token_saved_verified_and_never_returned(client: TestClient, fake_telegram: FakeTelegram) -> None:
    client.app.state.runtime.telegram.transport = fake_telegram.transport  # type: ignore[attr-defined]
    setup = client.get("/api/telegram/setup").json()
    assert setup["token"]["configured"] is False
    assert [s["key"] for s in setup["steps"]] == ["create_bot", "privacy", "token", "add_to_group", "authorise", "receiving"]

    bad = client.put("/api/telegram/credentials", json={"token": "not-a-token"})
    assert bad.status_code == 422

    saved = client.put("/api/telegram/credentials", json={"token": TOKEN})
    assert saved.status_code == 200
    body = saved.json()
    assert body["token"] == {"name": "telegram_bot_token", "configured": True, "source": "dashboard", "saved_at": body["token"]["saved_at"]}
    assert body["bot_username"] == "pricechat_test_bot"
    _token_absent(saved.text)
    for path in ("/api/telegram/setup", "/api/telegram/status", "/api/telegram/credentials", "/api/activity", "/api/connectors"):
        _token_absent(client.get(path).text)
    assert TOKEN not in Path(os.environ["SECRETS_FILE"]).read_text(encoding="utf-8")

    # Works without restart: the ingestion job now runs instead of being skipped.
    assert client.post("/api/telegram/sync").json()["status"] == "success"
    events = [a["event"] for a in client.get("/api/activity", params={"level": "audit"}).json()]
    assert "telegram_credentials_saved" in events

    removed = client.delete("/api/telegram/credentials").json()
    assert removed["token"]["configured"] is False
    assert client.post("/api/telegram/sync").json()["status"] == "skipped"


def test_invalid_token_is_not_saved(client: TestClient, fake_telegram: FakeTelegram) -> None:
    client.app.state.runtime.telegram.transport = fake_telegram.transport  # type: ignore[attr-defined]
    fake_telegram.error = (401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
    resp = client.put("/api/telegram/credentials", json={"token": TOKEN})
    assert resp.status_code == 502 and resp.json()["code"] == "telegram_auth"
    assert client.get("/api/telegram/credentials").json()["configured"] is False


def test_wizard_progress_privacy_and_authorisation(client: TestClient, fake_telegram: FakeTelegram) -> None:
    rt = client.app.state.runtime  # type: ignore[attr-defined]
    rt.telegram.transport = fake_telegram.transport
    client.put("/api/telegram/credentials", json={"token": TOKEN})
    steps = {s["key"]: s["done"] for s in client.get("/api/telegram/setup").json()["steps"]}
    assert steps["token"] is True and steps["authorise"] is False and steps["privacy"] is None  # getMe didn't report it

    fake_telegram.message(-100111, "hello from the group")
    chats = client.post("/api/telegram/refresh-chats").json()
    assert [c["chat_id"] for c in chats["discovered_chats"]] == [-100111]
    assert client.get("/api/telegram/messages").json() == []  # added != authorised: nothing ingested

    client.post("/api/telegram/sources", json={"chat_id": -100111})
    fake_telegram.message(-100111, "after authorisation")
    client.post("/api/telegram/sync")
    steps = {s["key"]: s["done"] for s in client.get("/api/telegram/setup").json()["steps"]}
    assert steps["authorise"] is True and steps["receiving"] is True


def test_pause_and_start_ingestion(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    tg_client.post("/api/telegram/sources", json={"chat_id": -100111})
    paused = tg_client.put("/api/telegram/ingestion", json={"enabled": False}).json()
    assert paused["ingestion_enabled"] is False
    assert tg_client.get("/api/community/pilot").json()["ingestion_enabled"] is False
    started = tg_client.put("/api/telegram/ingestion", json={"enabled": True}).json()
    assert started["ingestion_enabled"] is True


def test_pilot_stats_and_message_trace(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    tg_client.post("/api/telegram/sources", json={"chat_id": -100111})
    for i, t in enumerate(["funding rates are spiking on bitcoin today", "bitcoin funding rates are spiking again",
                           "why are funding rates spiking on bitcoin", "funding rates on bitcoin are spiking hard today"]):
        fake_telegram.message(-100111, t, user_id=10 + i)
    fake_telegram.message(-100222, "unauthorised chat message")
    tg_client.post("/api/telegram/sync")
    for job in ("community.process", "community.topics"):
        tg_client.post(f"/api/jobs/{job}/run")
    tg_client.post("/api/community/summaries", json={"kind": "recent", "hours": 24 * 30})
    tg_client.post("/api/jobs/community.memories/run")

    stats = tg_client.get("/api/community/pilot").json()
    assert stats["messages_total"] == 4 and stats["messages_processed"] == 4 and stats["messages_ignored"] >= 1
    assert stats["topics"] == 1 and stats["community_memories"] >= 1 and stats["pending_memories"] >= 1
    assert {j["name"] for j in stats["jobs"]} >= {"telegram.ingest", "community.process"}

    msg = tg_client.get("/api/telegram/messages").json()[-1]
    trace = tg_client.get(f"/api/telegram/messages/{msg['id']}/trace").json()
    assert trace["source_name"] == "Price Chat Community"
    assert trace["topic"]["status"] == "active"
    assert trace["summaries"] and trace["summaries"][0]["model"]
    assert tg_client.get("/api/telegram/messages/999999/trace").status_code == 404
