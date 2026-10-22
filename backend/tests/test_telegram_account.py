"""Telegram account mode (official client API). Everything runs against an in-memory fake: no network,
no real Telegram account, and nothing is ever sent to Telegram."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.agent_service import get_agent_config  # noqa: F401 - ensures app imports cleanly
from tests.fakes import TG_API_HASH, TG_API_ID, TG_CODE, TG_PHONE, FakeTelegramAccount

B = "/api/telegram/account"


@pytest.fixture
def tg_account(client: TestClient) -> FakeTelegramAccount:
    fake = FakeTelegramAccount()
    client.app.state.runtime.telegram_account.factory = fake.factory  # type: ignore[attr-defined]
    return fake


def _configure(c: TestClient) -> None:
    assert c.put(f"{B}/credentials", json={"api_id": TG_API_ID, "api_hash": TG_API_HASH}).status_code == 200


def _login(c: TestClient) -> dict:
    _configure(c)
    c.post(f"{B}/login", json={"phone": TG_PHONE})
    return c.post(f"{B}/code", json={"code": TG_CODE}).json()


def _authorize(c: TestClient, chat_id: int = -1001111) -> dict:
    c.post(f"{B}/dialogs")
    return c.post(f"{B}/sources", json={"chat_id": chat_id}).json()


# --- credentials & login ------------------------------------------------------------------------------


def test_api_credentials_are_stored_encrypted_and_never_returned(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    assert client.get(B).json()["status"] == "not_configured"
    assert client.put(f"{B}/credentials", json={"api_id": TG_API_ID, "api_hash": "not-a-hash"}).status_code == 422
    _configure(client)
    st = client.get(B).json()
    assert st["status"] == "needs_login" and st["api_hash"]["configured"] is True and st["api_id"] == TG_API_ID
    for path in (B, "/api/telegram/connection", "/api/activity"):
        assert TG_API_HASH not in client.get(path).text
    assert TG_API_HASH not in Path(os.environ["SECRETS_FILE"]).read_text(encoding="utf-8")


def test_login_with_code(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _configure(client)
    assert client.post(f"{B}/login", json={"phone": "+19999999999"}).json()["code"] == "telegram_phone_invalid"
    st = client.post(f"{B}/login", json={"phone": TG_PHONE}).json()
    assert st["status"] == "code_sent" and st["phone_hint"] == "••••42" and tg_account.codes_sent == 1
    bad = client.post(f"{B}/code", json={"code": "11111"})
    assert bad.status_code == 400 and bad.json()["code"] == "telegram_code_invalid"
    ok = client.post(f"{B}/code", json={"code": TG_CODE}).json()
    assert ok["result"] == "connected" and ok["status"] == "connected" and ok["session"] == "active"
    assert ok["display_name"] == "Price Chat Admin" and ok["username"] == "pricechat_admin"
    # the session is a secret: encrypted on disk, never returned, never logged
    secrets = Path(os.environ["SECRETS_FILE"]).read_text(encoding="utf-8")
    assert "fake-session" not in secrets and "fake-session" not in client.get(B).text
    activity = client.get("/api/activity").text
    assert TG_CODE not in activity and "fake-session" not in activity


def test_two_step_verification(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    tg_account.password = "correct horse"
    _configure(client)
    client.post(f"{B}/login", json={"phone": TG_PHONE})
    step = client.post(f"{B}/code", json={"code": TG_CODE}).json()
    assert step["result"] == "password_needed" and step["status"] == "password_needed"
    assert client.post(f"{B}/code", json={"code": TG_CODE}).status_code == 400  # now waiting for the password
    wrong = client.post(f"{B}/password", json={"password": "nope"})
    assert wrong.status_code == 400 and wrong.json()["code"] == "telegram_password_invalid"
    assert client.post(f"{B}/password", json={"password": "correct horse"}).json()["status"] == "connected"
    assert "correct horse" not in client.get("/api/activity").text


def test_code_without_login_and_cancel(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _configure(client)
    assert client.post(f"{B}/code", json={"code": TG_CODE}).status_code == 400
    client.post(f"{B}/login", json={"phone": TG_PHONE})
    assert client.post(f"{B}/cancel").json()["status"] == "needs_login"


def test_wrong_api_credentials(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    client.put(f"{B}/credentials", json={"api_id": 1, "api_hash": "f" * 32})
    r = client.post(f"{B}/login", json={"phone": TG_PHONE})
    assert r.status_code == 401 and r.json()["code"] == "telegram_api_invalid"


# --- allow-list & ingestion ------------------------------------------------------------------------------


def test_discovery_and_explicit_authorisation(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _login(client)
    dialogs = client.post(f"{B}/dialogs").json()
    assert {d["title"] for d in dialogs} == {"Price Chat Community", "Price Chat Announcements", "Unrelated Group"}
    assert all(d["chat_type"] in ("group", "supergroup", "channel") and not d["authorized"] for d in dialogs)
    assert tg_account.read_chats == []  # listing reads no messages
    assert client.post(f"{B}/sources", json={"chat_id": -1009999}).status_code == 404  # not visible to the account
    src = client.post(f"{B}/sources", json={"chat_id": -1001111}).json()
    assert src["via"] == "account" and src["display_name"] == "Price Chat Community"
    assert client.post(f"{B}/sources", json={"chat_id": -1001111}).status_code == 409
    assert [d["authorized"] for d in client.post(f"{B}/dialogs").json() if d["chat_id"] == -1001111] == [True]


def test_only_authorised_chats_are_read_starting_from_now(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _login(client)
    tg_account.add(-1001111, "old message before authorisation", minutes_ago=120)
    tg_account.add(-1003333, "unrelated group chatter")
    _authorize(client)
    tg_account.add(-1001111, "why is funding so high today?", minutes_ago=-1, reply_to=101, thread_id=None)
    tg_account.add(-1001111, "[photo]", minutes_ago=-1, media="photo")
    run = client.post("/api/telegram/sync").json()
    assert run["status"] == "success" and run["result"]["account"]["stored"] == 2
    assert set(tg_account.read_chats) == {-1001111}  # never the unrelated group
    msgs = client.get("/api/telegram/messages").json()
    texts = {m["text"] for m in msgs}
    assert "old message before authorisation" not in texts and "unrelated group chatter" not in texts
    q = next(m for m in msgs if m["text"].startswith("why"))
    assert q["reply_to_message_id"] == 101 and q["author_name"] == "Member" and q["chat_id"] == -1001111
    assert any(m["media_type"] == "photo" for m in msgs)

    again = client.post("/api/telegram/sync").json()  # dedup: nothing new
    assert again["result"]["account"]["stored"] == 0

    # a restart (fresh runtime, same saved session) does not duplicate either
    rt = client.app.state.runtime  # type: ignore[attr-defined]
    from app.telegram.account_service import TelegramAccountRuntime

    rt.telegram_account = TelegramAccountRuntime(factory=tg_account.factory)
    assert client.post("/api/telegram/sync").json()["result"]["account"]["stored"] == 0
    assert len(client.get("/api/telegram/messages").json()) == 2


def test_disabled_source_is_not_read(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _login(client)
    src = _authorize(client)
    client.patch(f"/api/telegram/sources/{src['id']}", json={"enabled": False})
    tg_account.add(-1001111, "hello", minutes_ago=-1)
    client.post("/api/telegram/sync")
    assert tg_account.read_chats == []


def test_history_import_is_bounded_and_estimated(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _login(client)
    for i in range(5):
        tg_account.add(-1001111, f"history message {i}", minutes_ago=60 * (i + 1))
    tg_account.add(-1001111, "two weeks ago", minutes_ago=60 * 24 * 14)
    src = _authorize(client)
    est = client.get(f"{B}/sources/{src['id']}/import-estimate", params={"hours": 24}).json()
    assert est["estimated_messages"] == 5 and est["cap"] == 5000
    assert client.get(f"{B}/sources/{src['id']}/import-estimate", params={"hours": 5}).status_code == 400
    done = client.post(f"{B}/sources/{src['id']}/import", json={"hours": 24}).json()
    assert done["stored"] == 5 and done["capped"] is False
    assert client.post(f"{B}/sources/{src['id']}/import", json={"hours": 24}).json()["stored"] == 0  # idempotent
    assert client.post(f"{B}/sources/{src['id']}/import", json={"hours": 168}).json()["stored"] == 0  # 14 days ago is outside 7 days
    assert client.post(f"{B}/sources/{src['id']}/import", json={"hours": 720}).json()["stored"] == 1


def test_logout_revokes_the_session_but_keeps_sources(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _login(client)
    _authorize(client)
    st = client.post(f"{B}/logout").json()
    assert tg_account.logged_out is True and st["session"] == "none" and st["status"] == "needs_login"
    assert client.post("/api/telegram/sync").json()["status"] == "skipped"
    assert len(client.get("/api/telegram/status").json()["sources"]) == 1  # data and allow-list stay until you remove them


def test_session_revoked_on_telegram_side(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _login(client)
    _authorize(client)
    tg_account.valid_session = None  # e.g. terminated from Telegram › Settings › Devices
    assert client.post("/api/telegram/sync").json()["status"] == "failed"
    st = client.get(B).json()
    assert st["session"] == "none" and st["status"] == "needs_login"


def test_flood_wait_is_reported_not_slept(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _login(client)
    tg_account.flood = 120
    r = client.post(f"{B}/dialogs")
    assert r.status_code == 429 and "120 seconds" in r.json()["detail"]


def test_connection_overview(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    assert client.get("/api/telegram/connection").json()["status"] == "not_configured"
    _login(client)
    _authorize(client)
    tg_account.add(-1001111, "hello community", minutes_ago=-1)
    client.post("/api/telegram/sync")
    c = client.get("/api/telegram/connection").json()
    assert c["mode"] == ["account"] and c["status"] == "connected" and c["account"]["display_name"] == "Price Chat Admin"
    assert c["sources"] == {"total": 1, "enabled": 1, "bot": 0, "account": 1}
    assert c["messages_today"] >= 1 and c["last_message_at"]


def test_no_send_capability_exists() -> None:
    from app.telegram.account import AccountGateway, TelethonGateway

    for cls in (AccountGateway, TelethonGateway):
        assert not any(hasattr(cls, m) for m in ("send_message", "send", "post", "forward", "reply"))



def test_qr_login_needs_no_code(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _configure(client)
    r = client.post(f"{B}/qr").json()
    assert r["url"].startswith("tg://login?token=") and r["account"]["status"] == "qr_waiting"
    assert client.post(f"{B}/qr/poll").json()["status"] == "waiting"  # not scanned yet
    tg_account.qr_scanned = True  # the member scans it: Telegram › Settings › Devices › Link Desktop Device
    done = client.post(f"{B}/qr/poll").json()
    assert done["status"] == "connected" and done["account"]["status"] == "connected"
    assert tg_account.codes_sent == 0  # no login code was ever needed


def test_qr_login_with_two_step_password(client: TestClient, tg_account: FakeTelegramAccount) -> None:
    _configure(client)
    tg_account.password = "hunter2"
    client.post(f"{B}/qr")
    tg_account.qr_scanned = True
    assert client.post(f"{B}/qr/poll").json()["status"] == "password_needed"
    assert client.post(f"{B}/password", json={"password": "hunter2"}).json()["status"] == "connected"
