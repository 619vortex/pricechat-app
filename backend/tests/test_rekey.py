"""Key rotation: APP_ENCRYPTION_KEY_PREVIOUS + the re-encryption pass (app/security/rekey.py)."""

import base64
import itertools
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import Boolean, DateTime, Integer, Numeric, insert, select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import Base
from app.db.session import engine
from app.security import credentials, rekey
from app.security.secrets import SecretStoreError, protect, unprotect

OLD, NEW, OTHER = Fernet.generate_key().decode(), Fernet.generate_key().decode(), Fernet.generate_key().decode()
APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # type: ignore[no-untyped-def]
    s = get_settings()
    monkeypatch.setattr(s, "secrets_file", tmp_path / "secrets.json")

    def use(key: str | None, previous: str | None = None) -> None:
        monkeypatch.setattr(s, "app_encryption_key", SecretStr(key) if key else None)
        monkeypatch.setattr(s, "app_encryption_key_previous", SecretStr(previous) if previous else None)

    return use


_n = itertools.count(1000)


def _dummy(col):  # type: ignore[no-untyped-def]
    """A distinct filler value (unique columns and indexes never collide)."""
    t = col.type
    if isinstance(t, Boolean):
        return False
    if isinstance(t, (Integer, Numeric)):
        return next(_n)
    if isinstance(t, DateTime) or "DATETIME" in str(t).upper():
        return datetime.now(timezone.utc)
    return str(next(_n))


def _seed(values: dict[tuple[str, str], str]) -> dict[tuple[str, str], int]:
    """One row per table, its registered columns holding the given values (foreign keys off: these rows only carry
    ciphertext)."""
    ids: dict[tuple[str, str], int] = {}
    tables: dict[str, dict[str, str]] = {}
    for model, attr, _wrap in rekey.columns():
        tables.setdefault(model.__tablename__, {})[getattr(model, attr).expression.name] = values[(model.__tablename__, attr)]
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            for name, cols in tables.items():
                t = Base.metadata.tables[name]
                row = {c.name: _dummy(c) for c in t.columns
                       if not c.nullable and c.default is None and c.server_default is None and not c.primary_key}
                rid = conn.execute(insert(t).values({**row, **cols})).inserted_primary_key[0]
                ids.update({(name, a): rid for (tn, a) in values if tn == name})
            conn.commit()
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    return ids


def _read(db: Session, ids: dict[tuple[str, str], int]) -> dict[tuple[str, str], str]:
    out = {}
    for model, attr, _w in rekey.columns():
        out[(model.__tablename__, attr)] = db.scalar(select(getattr(model, attr)).where(model.id == ids[(model.__tablename__, attr)]))
    return out


def _sealed(secret: str) -> dict[tuple[str, str], str]:
    """The value each site would store for `secret`, wrapped the way that site wraps it."""
    out = {}
    for model, attr, (_un, wrap) in rekey.columns():
        out[(model.__tablename__, attr)] = wrap(protect(f"{secret}:{attr}".encode()))
    return out


def _plain(value: str, wrap) -> str:  # type: ignore[no-untyped-def]
    return unprotect(wrap[0](value)).decode()


def test_old_key_values_readable_with_previous_and_new_values_use_new_key(keys) -> None:  # type: ignore[no-untyped-def]
    keys(OLD)
    token = protect(b"hello")
    keys(NEW)
    with pytest.raises(SecretStoreError):
        unprotect(token)
    keys(NEW, f"{OTHER}, {OLD}")
    assert unprotect(token) == b"hello"
    fresh = protect(b"fresh")
    assert Fernet(NEW.encode()).decrypt(fresh.removeprefix(b"fernet:")) == b"fresh"  # encryption: the new key only


def test_bad_previous_key_is_named(keys) -> None:  # type: ignore[no-untyped-def]
    keys(NEW, "not-a-key")
    with pytest.raises(SecretStoreError, match="APP_ENCRYPTION_KEY_PREVIOUS"):
        protect(b"x")


def test_rekey_every_site_then_new_key_alone(keys, db: Session) -> None:  # type: ignore[no-untyped-def]
    keys(OLD)
    ids = _seed(_sealed("s"))
    store = credentials.secret_store()
    store.set("telegram_bot_token", "tok-123")
    keys(NEW, OLD)

    out = rekey.run(db)
    n = len(rekey.columns())
    assert out["totals"] == {"rotated": n + 1, "current": 0, "failed": 0, "other": 0}
    assert out["previous_keys"] == 1
    assert all(out["sites"][f"{m.__tablename__}.{a}"]["rotated"] == 1 for m, a, _ in rekey.columns())
    assert out["sites"]["secret store"]["rotated"] == 1

    keys(NEW)  # previous key removed: everything still decrypts
    db.expire_all()
    stored = _read(db, ids)
    for model, attr, wrap in rekey.columns():
        assert _plain(stored[(model.__tablename__, attr)], wrap) == f"s:{attr}"
    assert store.get("telegram_bot_token") == "tok-123"

    # re-running is a no-op: nothing changes, everything counted as current
    again = rekey.run(db)
    assert again["totals"] == {"rotated": 0, "current": n + 1, "failed": 0, "other": 0}
    db.expire_all()
    assert _read(db, ids) == stored


def test_rekey_keeps_updated_at(keys, db: Session) -> None:  # type: ignore[no-untyped-def]
    from app.models.trading import TrAccount

    keys(OLD)
    ids = _seed(_sealed("t"))
    rid = ids[("tr_accounts", "mainnet_agent_key")]
    before = db.scalar(select(TrAccount.updated_at).where(TrAccount.id == rid))
    keys(NEW, OLD)
    rekey.run(db)
    db.expire_all()
    assert db.scalar(select(TrAccount.updated_at).where(TrAccount.id == rid)) == before


def test_undecryptable_and_non_fernet_values_left_alone(keys, db: Session) -> None:  # type: ignore[no-untyped-def]
    keys(OTHER)  # a key nobody configures later
    lost = _sealed("lost")
    keys(NEW)
    plain = {k: base64.b64encode(b"dpapi-or-plain").decode() if k != ("market_redemptions", "address_enc") else "plain"
             for k in lost}
    ids_lost = _seed(lost)
    ids_plain = _seed(plain)
    keys(NEW, OLD)
    out = rekey.run(db)
    n = len(rekey.columns())
    assert out["totals"] == {"rotated": 0, "current": 0, "failed": n, "other": n}
    db.expire_all()
    assert _read(db, ids_lost) == lost
    assert _read(db, ids_plain) == plain


def test_secret_store_counts_failed_and_other(keys) -> None:  # type: ignore[no-untyped-def]
    keys(OTHER)
    store = credentials.secret_store()
    store.set("x_api_key", "k1")
    keys(NEW, OLD)
    data = store._read()
    data["legacy"] = {"value": base64.b64encode(b"plain").decode(), "saved_at": datetime.now(timezone.utc).isoformat()}
    store._write(data)
    before = store.path.read_text(encoding="utf-8")
    assert store.reseal() == {"rotated": 0, "current": 0, "failed": 1, "other": 1}
    assert store.path.read_text(encoding="utf-8") == before


def test_endpoint_returns_counts_and_is_owner_only(keys, client) -> None:  # type: ignore[no-untyped-def]
    from app.security import admin_auth

    assert not admin_auth.is_public("POST", "/api/system/rekey")
    keys(NEW, OLD)
    r = client.post("/api/system/rekey")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"previous_keys", "totals", "sites"}
    assert "secret store" in body["sites"]


# --- every storage site is registered ---------------------------------------------------------------------------------


def test_every_protect_user_is_registered() -> None:
    users = set()
    for p in APP.rglob("*.py"):
        src = p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(APP.parent).as_posix()
        if re.search(r"from app\.security\.secrets import [^\n]*\bprotect\b", src) or re.search(r"\bdef protect\(data: bytes\)", src):
            users.add(rel)
    # app/tradingos/exchange.py wraps protect() as enc(); its callers store into tr_accounts (registered)
    assert users == rekey.SEALING_MODULES, "a new protect() user: register its columns in app/security/rekey.py"
    callers = {p.relative_to(APP.parent).as_posix() for p in APP.rglob("*.py")
               if re.search(r"\b(xch|exchange)\.enc\(", p.read_text(encoding="utf-8", errors="replace"))}
    assert callers <= {"app/tradingos/accounts.py"}, "a new exchange.enc() caller: register its columns in rekey.py"


def test_every_encrypted_looking_column_is_registered() -> None:
    import app.models  # noqa: F401 - every table

    registered = {(m.__tablename__, getattr(m, a).expression.name) for m, a, _ in rekey.columns()}
    suspects = {(t.name, c.name) for t in Base.metadata.tables.values() for c in t.columns
                if c.name.endswith(("_enc", "_agent_key"))}
    assert suspects <= registered


def test_startup_runs_the_pass_in_the_background_and_never_raises(keys, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import threading

    from fastapi.testclient import TestClient

    from app.db.session import SessionLocal
    from app.main import create_app

    keys(OLD)
    ids = _seed(_sealed("boot"))
    keys(NEW, OLD)
    started: list[str] = []
    real = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda self: (started.append(self.name), real(self))[1])
    with TestClient(create_app(background_jobs=False)):
        pass
    assert "rekey" in started
    for t in threading.enumerate():
        if t.name == "rekey":
            t.join(10)
    keys(NEW)
    with SessionLocal() as db:
        for model, attr, wrap in rekey.columns():
            assert _plain(_read(db, ids)[(model.__tablename__, attr)], wrap) == f"boot:{attr}"

    keys(NEW, "not-a-key")  # a broken setting is logged, not raised
    rekey.run_at_startup(SessionLocal)
