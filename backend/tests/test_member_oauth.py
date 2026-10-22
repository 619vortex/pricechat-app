"""Member Google / X connection: one canonical callback per provider, visible errors (never a silent no-op), and a
callback can only link to the member who pressed Connect. Every provider call goes to an in-memory fake."""

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.main import create_app
from app.members.auth import callback_url
from app.models.members import LinkedIdentity
from tests.conftest import FakeEmbedder, FakeProvider
from tests.test_members import (  # noqa: F401  (fixtures)
    app_and_client, fake_tg, google_sign_in, mc, me, other_browser, providers, x_sign_in, x_start,
)


def test_callbacks_are_derived_from_one_setting() -> None:
    s = Settings(members_public_url="http://127.0.0.1:5173/")
    assert callback_url(s, "google") == "http://127.0.0.1:5173/api/members/auth/google/callback"
    assert callback_url(s, "x") == "http://127.0.0.1:5173/api/members/auth/x/callback"
    s = Settings(members_public_url="https://pricechat.example", member_x_redirect_uri="https://auth.pricechat.example/x/callback")
    assert callback_url(s, "google") == "https://pricechat.example/api/members/auth/google/callback"
    assert callback_url(s, "x") == "https://auth.pricechat.example/x/callback"


def test_the_exact_redirect_uri_sent_matches_what_setup_shows(mc: TestClient) -> None:  # noqa: F811
    p = mc.get("/api/members/auth/providers").json()
    start = mc.get("/api/members/auth/google/start", params={"purpose": "login"}, follow_redirects=False)
    sent = parse_qs(urlparse(start.headers["location"]).query)["redirect_uri"][0]
    assert sent == p["google"]["callback"] == "http://127.0.0.1:5173/api/members/auth/google/callback"
    assert p["google"]["javascript_origin"] == p["public_url"] == "http://127.0.0.1:5173"
    xs = mc.get("/api/members/auth/x/start", params={"purpose": "login"}, follow_redirects=False)
    assert parse_qs(urlparse(xs.headers["location"]).query)["redirect_uri"][0] == p["x"]["callback"]
    assert p["x"]["scopes"] == "users.read tweet.read offline.access" and p["x"]["write"] is False  # read-only unless switched on
    assert "secret" not in str(p).lower().replace("client_secret", "") and "g-secret" not in str(p) and "member-secret" not in str(p)


def test_connect_x_without_setup_comes_back_with_a_visible_error(providers, fake_tg) -> None:  # noqa: F811
    settings = Settings(x_api_base="https://api.x.test", x_member_client_id=None, x_member_client_secret=None,
                        youtube_client_id=SecretStr("123-test.apps.googleusercontent.com"), youtube_client_secret=SecretStr("g"))
    app = create_app(app_settings=settings, provider=FakeProvider(), embedder=FakeEmbedder(dims=512), background_jobs=False)
    with TestClient(app) as c:
        r = c.get("/api/members/auth/x/start", params={"purpose": "link"}, follow_redirects=False)
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert r.status_code == 303 and q["platform"] == ["x"] and q["code"] == ["not_configured"]
        assert c.get("/api/members/auth/providers").json()["x"]["available"] is False


def test_a_callback_only_links_to_the_member_who_pressed_connect(mc: TestClient, providers, db: Session) -> None:  # noqa: F811
    google_sign_in(mc)  # member A
    state = x_start(mc, "link")  # A presses Connect X ...
    binding = mc.cookies.get("pc_auth")
    mc.post("/api/members/logout")
    providers.google_sub = "g-other"
    google_sign_in(mc)  # ... but by the time X calls back, member B is signed in in this browser
    mc.cookies.set("pc_auth", binding)  # same browser binding: only the member check can stop it
    r = mc.get("/api/members/auth/x/callback", params={"state": state, "code": "x-code"}, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code"] == ["session_mismatch"] and q["platform"] == ["x"]
    assert db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == "x")) is None


def test_cancel_and_provider_errors_come_back_to_the_passport(mc: TestClient, providers) -> None:  # noqa: F811
    google_sign_in(mc)
    state = x_start(mc, "link")
    r = mc.get("/api/members/auth/x/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"].startswith("/passport?error=") and "platform=x" in r.headers["location"]
    r = mc.get("/api/members/auth/google/callback", params={"state": "unknown", "error": "access_denied"}, follow_redirects=False)
    assert "code=cancelled" in r.headers["location"] and "platform=google" in r.headers["location"]


def test_google_sign_in_uses_its_own_client_and_leaves_youtube_alone() -> None:
    from app.config.settings import Settings
    from app.members.auth import google_client

    yt = Settings(_env_file=None, youtube_client_id=SecretStr("yt.apps.googleusercontent.com"), youtube_client_secret=SecretStr("yt-s"))  # type: ignore[call-arg]
    assert google_client(yt) == ("yt.apps.googleusercontent.com", "yt-s")  # no login client: the YouTube one, as before
    both = Settings(_env_file=None, youtube_client_id=SecretStr("yt.apps.googleusercontent.com"), youtube_client_secret=SecretStr("yt-s"),  # type: ignore[call-arg]
                    google_member_client_id=SecretStr("login.apps.googleusercontent.com"), google_member_client_secret=SecretStr("login-s"))
    assert google_client(both) == ("login.apps.googleusercontent.com", "login-s")


def test_connect_x_asks_for_write_access_only_when_switched_on() -> None:
    """Asking a Read-only X app for tweet.write makes X refuse the whole sign-in, so it's opt-in (X_MEMBER_WRITE)."""
    from app.config.settings import Settings
    from app.members import auth

    assert "tweet.write" not in auth.x_link_scopes(Settings(x_member_write=False))
    assert auth.x_link_scopes(Settings(x_member_write=True)) == "users.read tweet.read tweet.write offline.access"


def test_a_read_only_x_link_is_complete_not_a_reconnect(db) -> None:  # noqa: ANN001
    from app.members import service, x_actions
    from app.models.members import LinkedIdentity

    m = service.create_member(db)
    from app.db.base import utcnow

    db.add(LinkedIdentity(member_id=m.id, platform="x", platform_user_id="42", username="kiwifan", status="active", verified_at=utcnow()))
    db.commit()
    assert x_actions.status(db, m, write_enabled=False) == {"connected": True, "username": "kiwifan", "can_act": False, "needs_reconnect": False}
    assert x_actions.status(db, m, write_enabled=True)["needs_reconnect"] is True  # write on, but this link has no write token
