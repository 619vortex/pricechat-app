"""Production safety: config that refuses to start incomplete, the owner login in front of every admin API, docs off,
secrets encrypted without Windows DPAPI, and jobs that can be switched off per server."""

import os
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app
from app.security import admin_auth, secrets as secret_mod

PASSWORD = "correct horse battery staple"
DOMAIN = "https://pricechat.example"


def prod_settings(**over: object) -> Settings:
    base: dict[str, object] = dict(
        environment="production", members_public_url=DOMAIN, dashboard_url=f"{DOMAIN}/admin", cors_origins=[DOMAIN],
        database_url=os.environ.get("DATABASE_URL", "sqlite:////data/price_chat.db"), secrets_file=os.environ.get("SECRETS_FILE"),
        admin_username="owner", admin_password_hash=admin_auth.hash_password(PASSWORD),
        app_encryption_key=Fernet.generate_key().decode(),
    )
    base.update(over)
    return Settings(**{k: v for k, v in base.items() if v is not None})  # type: ignore[arg-type]


@pytest.fixture
def prod() -> Iterator[TestClient]:
    admin_auth.reset_for_tests()
    app = create_app(app_settings=prod_settings(), background_jobs=False)
    with TestClient(app, base_url=DOMAIN) as c:
        yield c
    admin_auth.reset_for_tests()


# --- configuration --------------------------------------------------------------------------------------------------


def test_production_refuses_local_or_missing_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)  # the test run sets it; production must set it explicitly
    with pytest.raises(ValueError) as e:
        Settings(environment="production")
    msg = str(e.value)
    for name in ("MEMBERS_PUBLIC_URL", "DASHBOARD_URL", "CORS_ORIGINS", "DATABASE_URL", "ADMIN_USERNAME", "APP_ENCRYPTION_KEY"):
        assert name in msg
    with pytest.raises(ValueError, match="MEMBERS_PUBLIC_URL must be the public https address"):
        prod_settings(members_public_url="http://127.0.0.1:5173")
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        prod_settings(cors_origins=["http://localhost:5173"])


def test_production_derives_what_it_safely_can(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRETS_FILE", raising=False)
    s = prod_settings(secrets_file=None, database_url="sqlite:////data/price_chat.db")
    assert s.youtube_redirect_uri == f"{DOMAIN}/api/youtube/oauth/callback"  # never 127.0.0.1:8000
    assert str(s.secrets_file).replace("\\", "/").endswith("/data/secrets.json")  # on the volume, next to the database
    assert s.admin_auth_required


def test_development_is_unchanged_without_a_password() -> None:
    s = Settings(environment="development", admin_password_hash=None)
    assert not s.admin_auth_required
    assert s.dashboard_url == "http://127.0.0.1:5173/admin"


# --- who can reach what -------------------------------------------------------------------------------------------


def test_anonymous_visitors_get_the_website_and_member_apis_only(prod: TestClient) -> None:
    for path in ("/api/website/home", "/api/website/latest", "/api/website/content", "/api/members/session",
                 "/api/members/ranks", "/api/members/rules", "/api/members/leaderboard", "/api/health/live", "/api/admin/session"):
        assert prod.get(path).status_code == 200, path
    assert prod.get("/api/members/me").status_code == 401  # member-only, with its own member sign-in
    for method, path in (("GET", "/api/overview"), ("GET", "/api/health"), ("GET", "/api/settings"), ("GET", "/api/x/status"),
                         ("GET", "/api/members-admin/members"), ("GET", "/api/members-admin/reputation"), ("GET", "/api/jobs"),
                         ("GET", "/api/website/status"), ("PUT", "/api/website/settings"), ("GET", "/api/telegram/status"),
                         ("POST", "/api/members-admin/members/1/xp"), ("GET", "/api/youtube"), ("GET", "/api/brand"),
                         ("GET", "/api/approvals"), ("GET", "/api/some-route-added-later")):
        r = prod.request(method, path, json={})
        assert r.status_code == 401 and r.json()["code"] == "admin_auth", (method, path, r.status_code)
    s = prod.get("/api/admin/session").json()
    assert s == {"required": True, "authenticated": False, "username": None}


def test_api_docs_are_off_in_production(prod: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert prod.get(path).status_code == 404, path


def test_owner_login_logout_and_cookie_flags(prod: TestClient) -> None:
    bad = prod.post("/api/admin/login", json={"username": "owner", "password": "wrong password!"})
    assert bad.status_code == 401 and "pc_admin" not in bad.cookies
    ok = prod.post("/api/admin/login", json={"username": "owner", "password": PASSWORD})
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"].lower()
    assert "pc_admin=" in cookie and "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert PASSWORD not in ok.text and admin_auth.COOKIE not in ok.text
    assert prod.get("/api/overview").status_code == 200
    assert prod.get("/api/admin/session").json() == {"required": True, "authenticated": True, "username": "owner"}
    assert prod.post("/api/admin/logout").status_code == 200
    assert prod.get("/api/overview").status_code == 401  # the server forgot the session, not just the browser


def test_failed_logins_are_rate_limited(prod: TestClient) -> None:
    for _ in range(admin_auth.PER_CLIENT):
        assert prod.post("/api/admin/login", json={"username": "owner", "password": "nope nope nope"}).status_code == 401
    blocked = prod.post("/api/admin/login", json={"username": "owner", "password": PASSWORD})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0  # even the right password waits


def test_cross_site_writes_are_refused(prod: TestClient) -> None:
    prod.post("/api/admin/login", json={"username": "owner", "password": PASSWORD})
    evil = prod.put("/api/website/settings", json={}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["code"] == "bad_origin"
    same = prod.put("/api/website/settings", json={}, headers={"Origin": DOMAIN})
    assert same.status_code != 403


def test_secrets_never_reach_the_browser(prod: TestClient) -> None:
    prod.post("/api/admin/login", json={"username": "owner", "password": PASSWORD})
    blob = " ".join(prod.get(p).text for p in ("/api/settings", "/api/x/status", "/api/members-admin/reputation", "/api/youtube"))
    for name in ("admin_password_hash", "app_encryption_key", "scrypt$", PASSWORD):
        assert name not in blob


# --- secrets without DPAPI ------------------------------------------------------------------------------------------


def test_secrets_are_encrypted_with_the_key_and_never_stored_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr("app.config.settings.get_settings", lambda: prod_settings(app_encryption_key=key))
    sealed = secret_mod.protect(b"member-token")
    assert sealed.startswith(b"fernet:") and b"member-token" not in sealed
    assert secret_mod.unprotect(sealed) == b"member-token"
    # production on Linux without a key: refuses to store anything unencrypted
    monkeypatch.setattr(secret_mod, "dpapi_available", lambda: False)
    monkeypatch.setattr("app.config.settings.get_settings", lambda: prod_settings(app_encryption_key="placeholder"))
    with pytest.raises(secret_mod.SecretStoreError):
        secret_mod.protect(b"x")  # an invalid key is refused, not ignored


# --- duplicate background readers -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_can_be_switched_off_per_server() -> None:
    from app.db.session import SessionLocal
    from app.scheduler.scheduler import JobSpec, Scheduler

    ran: list[str] = []

    async def job(ctx):  # type: ignore[no-untyped-def]
        ran.append("x")
        return {}

    sched = Scheduler(SessionLocal, [JobSpec("telegram.ingest", "Telegram", "", 60, job)], disabled={"telegram.ingest"})
    sched.sync_jobs()
    assert await sched.tick() == []
    outcome = await sched.run_job("telegram.ingest", trigger="manual")
    assert outcome.status == "disabled" and ran == []
    assert Settings(scheduler_disabled_jobs="telegram.ingest, x.ingest").disabled_jobs == {"telegram.ingest", "x.ingest"}
