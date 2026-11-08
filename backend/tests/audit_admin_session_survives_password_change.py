"""AUDIT (2026-11-08, AR-9): an owner session made under the old ADMIN_PASSWORD_HASH stays valid after the hash is changed
(admin_auth.current checks only token + expiry). Known-device cookies ARE revoked by a password change; sessions are not.
Asserts CURRENT behaviour; flip when sessions are bound to the password/credential version."""

from fastapi import Response
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.security import admin_auth


def test_session_outlives_password_change(db: Session) -> None:
    old = get_settings().model_copy(update={"admin_username": "owner", "admin_password_hash": SecretStr(admin_auth.hash_password("old-pass")),
                                            "app_encryption_key": SecretStr("k")})
    resp = Response()
    admin_auth.start_session(db, old, resp, "owner")
    token = resp.headers["set-cookie"].split("pc_admin=")[1].split(";")[0]
    device = admin_auth.device_token(old)
    new = old.model_copy(update={"admin_password_hash": SecretStr(admin_auth.hash_password("new-pass"))})
    assert admin_auth.known_device(new, device) is None          # device cookie revoked by the change
    assert admin_auth.current(db, token) is not None             # ...but the session (up to ADMIN_SESSION_HOURS) still works
