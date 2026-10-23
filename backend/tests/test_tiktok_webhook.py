"""TikTok webhooks at /api/webhooks/tiktok: public (no Price Chat session), TikTok-Signature checked over the raw body with the
app's client secret, 5-minute replay window, duplicates acknowledged once. TEST FIXTURES only: a made-up secret and events."""

import json
import logging
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.brand_manager import tiktok_webhook as wh
from app.models.brand_manager import BmTiktokWebhookEvent
from app.security.admin_auth import hash_password
from app.security.credentials import secret_store

URL = "/api/webhooks/tiktok"
SECRET = "tt-webhook-test-secret"


@pytest.fixture
def app_keys(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    # production-like: every non-public /api route needs the owner login
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(hash_password("pw")))
    store = secret_store(runtime.settings)
    store.set("tiktok_client_key", "tt-key")
    store.set("tiktok_client_secret", SECRET)
    yield
    for k in ("tiktok_client_key", "tiktok_client_secret"):
        store.delete(k)


def body(event: str = "authorization.removed", **extra: object) -> bytes:
    return json.dumps({"client_key": "tt-key", "event": event, "create_time": 1761220000, "user_openid": "act.example0001",
                       "content": json.dumps({"reason": 1}), **extra}, separators=(",", ":")).encode()


def signed(raw: bytes, t: int | None = None, secret: str = SECRET) -> dict[str, str]:
    t = int(time.time()) if t is None else t
    return {"TikTok-Signature": f"t={t},s={wh.sign(secret, t, raw)}", "Content-Type": "application/json"}


def test_signature_matches_tiktoks_documented_scheme() -> None:
    # HMAC-SHA256(client_secret, "<t>.<raw body>"), hex (developers.tiktok.com/doc/webhooks-verification)
    import hashlib
    import hmac

    assert wh.sign("k", 1633174587, b'{"a":1}') == hmac.new(b"k", b'1633174587.{"a":1}', hashlib.sha256).hexdigest()
    assert wh.parse_header("t=1633174587, s=" + "A" * 64) == (1633174587, "a" * 64)


def test_a_signed_event_is_accepted_without_any_session(client: TestClient, db: Session, app_keys: None) -> None:
    raw = body()
    r = client.post(URL, content=raw, headers=signed(raw))
    assert r.status_code == 200 and r.json() == {"ok": True, "event": "authorization.removed", "duplicate": False}
    row = db.query(BmTiktokWebhookEvent).one()
    assert (row.event, row.user_openid, row.create_time) == ("authorization.removed", "act.example0001", 1761220000)


def test_a_retried_delivery_is_acknowledged_once(client: TestClient, db: Session, app_keys: None) -> None:
    raw = body("video.publish.completed")
    assert client.post(URL, content=raw, headers=signed(raw)).json()["duplicate"] is False
    again = client.post(URL, content=raw, headers=signed(raw, int(time.time()) + 1))  # TikTok re-signs each retry
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert db.query(BmTiktokWebhookEvent).count() == 1


@pytest.mark.parametrize("headers", [
    {},  # no signature
    {"TikTok-Signature": "garbage"},
    {"TikTok-Signature": f"t={int(time.time())},s={'0' * 64}"},  # wrong signature
])
def test_unsigned_or_wrongly_signed_is_401(client: TestClient, db: Session, app_keys: None, headers: dict[str, str]) -> None:
    r = client.post(URL, content=body(), headers=headers)
    assert r.status_code == 401 and r.json()["code"] == "bad_signature"
    assert db.query(BmTiktokWebhookEvent).count() == 0


def test_another_secret_a_tampered_body_or_a_replay_is_refused(client: TestClient, app_keys: None) -> None:
    raw = body()
    assert client.post(URL, content=raw, headers=signed(raw, secret="someone-else")).status_code == 401
    assert client.post(URL, content=raw.replace(b"removed", b"REMOVED"), headers=signed(raw)).status_code == 401
    old = client.post(URL, content=raw, headers=signed(raw, int(time.time()) - wh.MAX_SKEW_S - 5))
    assert old.status_code == 401 and old.json()["code"] == "stale"


def test_malformed_signed_bodies_are_400(client: TestClient, app_keys: None) -> None:
    for raw in (b"not json", b"[]", json.dumps({"client_key": "tt-key"}).encode(), body(client_key="another-app")):
        r = client.post(URL, content=raw, headers=signed(raw))
        assert r.status_code == 400, raw


def test_without_a_client_secret_it_says_not_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "tiktok_client_secret", None)
    secret_store(runtime.settings).delete("tiktok_client_secret")
    raw = body()
    r = client.post(URL, content=raw, headers=signed(raw))
    assert r.status_code == 503 and r.json()["code"] == "not_configured"


def test_logs_never_carry_the_secret_signature_or_body(client: TestClient, app_keys: None, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="pricechat.tiktok.webhook")
    raw = body(content="private-content-xyz")
    h = signed(raw)
    client.post(URL, content=raw, headers=h)
    client.post(URL, content=raw, headers={"TikTok-Signature": h["TikTok-Signature"][:-2] + "00"})
    text = caplog.text
    assert "accepted" in text and "refused" in text
    assert SECRET not in text and h["TikTok-Signature"].split("s=")[1] not in text and "private-content-xyz" not in text


def test_a_refusal_says_why_without_secrets(client: TestClient, app_keys: None, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="pricechat.tiktok.webhook")
    raw = body()
    t = int(time.time())
    import hashlib
    import hmac

    body_only = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()  # a layout we don't accept
    assert client.post(URL, content=raw, headers={"TikTok-Signature": f"t={t},s={body_only}"}).status_code == 401
    assert client.post(URL, content=raw, headers=signed(raw, secret="rotated-in-portal")).status_code == 401
    lines = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert "'variant': 'body_only'" in lines[0] and "'client_key': 'ours'" in lines[0] and f"'secret': 'dashboard/{len(SECRET)}'" in lines[0]
    assert "'variant': 'none'" in lines[1]
    assert SECRET not in caplog.text and body_only not in caplog.text
