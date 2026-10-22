"""Settings › Pokémon API keys: saved encrypted, never returned, only known names accepted."""

from fastapi.testclient import TestClient

from app.security.credentials import resolve


def test_pokemon_api_keys_save_list_and_remove(client: TestClient) -> None:
    keys = {k["name"]: k for k in client.get("/api/settings/api-keys").json()["keys"]}
    assert "pricecharting_api_key" in keys and "ebay_client_secret" in keys
    assert keys["pricecharting_api_key"]["configured"] is False

    r = client.put("/api/settings/api-keys/pricecharting_api_key", json={"value": "  pc-secret-123  "})
    assert r.status_code == 200
    assert "pc-secret-123" not in r.text  # never returned
    saved = {k["name"]: k for k in r.json()["keys"]}["pricecharting_api_key"]
    assert saved["configured"] is True and saved["source"] == "dashboard" and saved["saved_at"]
    assert resolve("pricecharting_api_key") == "pc-secret-123"  # the provider sees it

    assert client.put("/api/settings/api-keys/telegram_bot_token", json={"value": "x"}).status_code == 404
    assert client.put("/api/settings/api-keys/pricecharting_api_key", json={"value": "   "}).status_code == 400

    removed = {k["name"]: k for k in client.delete("/api/settings/api-keys/pricecharting_api_key").json()["keys"]}
    assert removed["pricecharting_api_key"]["configured"] is False


def test_env_writer_adds_replaces_and_fills_commented_lines(client: TestClient, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.api.routes import api_keys

    env = tmp_path / ".env"
    env.write_text("LOG_LEVEL=INFO\n# PSA_API_TOKEN=\nGEMRATE_API_KEY=old\n", encoding="utf-8")
    monkeypatch.setattr(api_keys, "ENV_FILE", env)

    r = client.put("/api/settings/api-keys/env", json={"name": "psa_api_token", "value": "tok-1"})
    assert r.status_code == 200 and r.json()["restart_needed"] is True and "tok-1" not in r.text
    assert {k["name"]: k for k in r.json()["keys"]}["psa_api_token"]["in_env_file"] is True
    client.put("/api/settings/api-keys/env", json={"name": "GEMRATE_API_KEY", "value": "new value"})
    client.put("/api/settings/api-keys/env", json={"name": "MY_OTHER_KEY", "value": "abc"})
    assert env.read_text(encoding="utf-8") == 'LOG_LEVEL=INFO\nPSA_API_TOKEN=tok-1\nGEMRATE_API_KEY="new value"\nMY_OTHER_KEY=abc\n'

    assert client.put("/api/settings/api-keys/env", json={"name": "BAD NAME", "value": "x"}).status_code == 400
    assert client.put("/api/settings/api-keys/env", json={"name": "OK_NAME", "value": "a\nEVIL=1"}).status_code == 400
