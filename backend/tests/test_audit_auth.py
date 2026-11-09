"""Audit fixes (2026-11-09), account security:
- SEC-4 = TG-1 = PP-1: pressing Start on a Telegram sign-in link someone else created must not sign them in (or link the
  presser's Telegram to their Passport). The bot asks "Sign in to Price Chat on <device>?"; only [Yes] from the same
  Telegram user completes it, once, within 5 minutes.
- SEC-1: the Telegram Login Widget route is refused while the deeplink mode is on.
- AR-10: owner sessions end when ADMIN_PASSWORD_HASH changes.
Every Telegram call goes to the in-memory fake: nothing real is sent."""

from datetime import timedelta
from typing import Any

from fastapi import Response
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.members import auth
from app.models.members import LinkedIdentity, MemberAuthRequest
from app.security import admin_auth
from tests.fakes import FakeTelegram
from tests.test_members import (  # noqa: F401 - pytest fixtures
    _press, _private_start, app_and_client, fake_tg, google_sign_in, mc, other_browser, providers,
)

VICTIM = 777
CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _status(c: TestClient, rid: int) -> dict[str, Any]:
    return c.get(f"/api/members/auth/telegram/status/{rid}").json()


def _signed_in(c: TestClient) -> bool:
    return bool(c.get("/api/members/session").json()["signed_in"])


def _start(c: TestClient, purpose: str = "login") -> dict[str, Any]:
    assert c.post("/api/telegram/verify").status_code == 200
    r = c.post("/api/members/auth/telegram/start", json={"purpose": purpose}, headers={"user-agent": CHROME_WIN})
    assert r.status_code == 200, r.text
    return r.json()


def test_start_alone_never_signs_in_the_link_creator(mc: TestClient, fake_tg: FakeTelegram) -> None:  # noqa: F811
    a = _start(mc)  # the attacker's browser creates the code and sends the t.me link to the victim
    _private_start(fake_tg, a["link"], user_id=VICTIM, username="victim")  # the victim presses Start
    mc.post("/api/telegram/sync")
    mc.post("/api/telegram/sync")
    assert _status(mc, a["request"]) == {"status": "pending"}
    assert not _signed_in(mc)
    q = [b for m, b in fake_tg.calls if m == "sendMessage"]
    assert len(q) == 1 and q[0]["chat_id"] == VICTIM
    assert q[0]["text"].startswith("Sign in to Price Chat on Chrome on Windows?") and "If someone sent you this link, tap No" in q[0]["text"]
    buttons = q[0]["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == [f"pcs:y:{a['request']}", f"pcs:n:{a['request']}"]


def test_yes_from_the_same_user_signs_in_once(mc: TestClient, fake_tg: FakeTelegram) -> None:  # noqa: F811
    a = _start(mc)
    _private_start(fake_tg, a["link"], user_id=VICTIM, username="victim")
    mc.post("/api/telegram/sync")
    _press(fake_tg, "y", a["request"], user_id=999)  # someone else's [Yes] (e.g. a forwarded message) is refused
    mc.post("/api/telegram/sync")
    assert _status(mc, a["request"]) == {"status": "pending"} and not _signed_in(mc)
    alerts = [b for m, b in fake_tg.calls if m == "answerCallbackQuery"]
    assert alerts and "expired or was already used" in alerts[-1]["text"]
    _press(fake_tg, "y", a["request"], user_id=VICTIM)
    mc.post("/api/telegram/sync")
    done = _status(mc, a["request"])
    assert done["status"] == "done" and done["username"] == "victim" and _signed_in(mc)
    assert _status(mc, a["request"]) == {"status": "used"}  # single use
    _press(fake_tg, "y", a["request"], user_id=VICTIM)  # a second tap
    mc.post("/api/telegram/sync")
    assert _status(mc, a["request"]) == {"status": "used"}
    _private_start(fake_tg, a["link"], user_id=VICTIM)  # the used code again: no new question
    mc.post("/api/telegram/sync")
    assert len([1 for m, _ in fake_tg.calls if m == "sendMessage"]) == 1


def test_no_cancels(mc: TestClient, fake_tg: FakeTelegram) -> None:  # noqa: F811
    a = _start(mc)
    _private_start(fake_tg, a["link"], user_id=VICTIM)
    mc.post("/api/telegram/sync")
    _press(fake_tg, "n", a["request"], user_id=VICTIM)
    mc.post("/api/telegram/sync")
    st = _status(mc, a["request"])
    assert st["status"] == "cancelled" and "tapped No" in st["error"] and not _signed_in(mc)
    _press(fake_tg, "y", a["request"], user_id=VICTIM)  # changing their mind afterwards does nothing
    mc.post("/api/telegram/sync")
    assert _status(mc, a["request"])["status"] == "cancelled" and not _signed_in(mc)
    edits = [b for m, b in fake_tg.calls if m == "editMessageText"]
    assert edits and edits[0]["text"].startswith("Cancelled")


def test_expired_code_and_question_are_refused(mc: TestClient, fake_tg: FakeTelegram, db: Session) -> None:  # noqa: F811
    a = _start(mc)
    req = db.get(MemberAuthRequest, a["request"])
    assert req is not None and req.expires_at - utcnow() <= timedelta(minutes=5)  # TTL at most 5 minutes
    _private_start(fake_tg, a["link"], user_id=VICTIM)
    mc.post("/api/telegram/sync")
    db.expire_all()
    req = db.get(MemberAuthRequest, a["request"])
    req.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    _press(fake_tg, "y", a["request"], user_id=VICTIM)
    mc.post("/api/telegram/sync")
    assert _status(mc, a["request"]) == {"status": "expired"} and not _signed_in(mc)
    # a code that expired before Start was pressed gets no question at all
    b = _start(mc)
    db.expire_all()
    req = db.get(MemberAuthRequest, b["request"])
    req.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    sent = len([1 for m, _ in fake_tg.calls if m == "sendMessage"])
    _private_start(fake_tg, b["link"], user_id=VICTIM)
    mc.post("/api/telegram/sync")
    assert len([1 for m, _ in fake_tg.calls if m == "sendMessage"]) == sent


def test_link_needs_yes_and_names_the_passport(mc: TestClient, fake_tg: FakeTelegram, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)  # the attacker's own Passport
    username = mc.get("/api/members/me").json()["member"]["username"]
    a = _start(mc, "link")
    _private_start(fake_tg, a["link"], user_id=888, username="bigcombot")  # someone else presses Start
    mc.post("/api/telegram/sync")
    assert _status(mc, a["request"]) == {"status": "pending"}
    assert db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == "telegram", LinkedIdentity.platform_user_id == "888")) is None
    q = [b for m, b in fake_tg.calls if m == "sendMessage"][-1]
    assert f"Kiwi Passport “{username}”" in q["text"] and q["reply_markup"]["inline_keyboard"][0][0]["text"] == "✅ Yes, connect it"
    _press(fake_tg, "y", a["request"], user_id=888)  # only an explicit Yes links it
    mc.post("/api/telegram/sync")
    assert _status(mc, a["request"])["status"] == "done"
    assert db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == "telegram", LinkedIdentity.platform_user_id == "888")) is not None


def test_device_label() -> None:
    assert auth.device_label(CHROME_WIN) == "Chrome on Windows"
    assert auth.device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1") == "Safari on iPhone"
    assert auth.device_label(None) == "a browser"


# --- SEC-1 -----------------------------------------------------------------------------------------------------------


def test_widget_route_refused_in_deeplink_mode(mc: TestClient) -> None:  # noqa: F811
    assert mc.app.state.runtime.settings.members_telegram_deeplink is True  # type: ignore[attr-defined]
    r = mc.post("/api/members/auth/telegram/widget", json={"id": 777, "first_name": "X", "auth_date": 1, "hash": "0" * 64})
    assert r.status_code == 404 and not _signed_in(mc)


# --- AR-10 -----------------------------------------------------------------------------------------------------------


def test_admin_session_ends_when_password_changes(db: Session) -> None:
    old = get_settings().model_copy(update={"admin_username": "owner", "admin_password_hash": SecretStr(admin_auth.hash_password("old-pass")),
                                            "app_encryption_key": SecretStr("k")})
    resp = Response()
    admin_auth.start_session(db, old, resp, "owner")
    token = resp.headers["set-cookie"].split("pc_admin=")[1].split(";")[0]
    assert admin_auth.current(db, token) is not None
    new = old.model_copy(update={"admin_password_hash": SecretStr(admin_auth.hash_password("new-pass"))})
    admin_auth.bind_credentials(new)  # what a restart with the new ADMIN_PASSWORD_HASH does (create_app)
    try:
        assert admin_auth.current(db, token) is None
        admin_auth.bind_credentials(old)  # the same hash again: the session is still valid
        assert admin_auth.current(db, token) is not None
    finally:
        admin_auth.bind_credentials(get_settings())
