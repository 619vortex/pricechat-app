"""Community members + Kiwi XP. Every Google / X / Telegram call goes to an in-memory fake: nothing real is
posted, liked or read, and no social activity is performed."""

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import base64
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.main import create_app
from app.members import service, verified
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.content_items import ContentItem
from app.models.enums import Platform
from app.models.members import (
    LinkedIdentity, Member, MemberAuditLog, MemberSession, MemberXToken, ModerationFlag, ReputationEvent, XPClaim,
)
from app.models.social import SocialPost
from app.models.social_intel import SocialProfile
from app.models.telegram import TelegramMessage, TelegramSource
from tests.conftest import FakeEmbedder, FakeProvider
from tests.fakes import BOT, TOKEN, FakeTelegram

GOOGLE_CLIENT = "123-test.apps.googleusercontent.com"


# --- fakes ------------------------------------------------------------------------------------------------------


class FakeProviders:
    """Google token endpoint + X OAuth 2.0 endpoints. `google_sub` / `x_user` choose who 'signs in' next."""

    def __init__(self) -> None:
        self.google_sub, self.google_name = "g-1", "Gina Google"
        self.x_user, self.x_name = "42", "kiwifan"
        self.x_token_error: tuple[int, dict] | None = None  # e.g. (400, {"error": "invalid_request", ...})
        self.x_me_status = 200
        self.revoked: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.x_scope = "users.read tweet.read tweet.write offline.access"
        self.x_access, self.x_refresh, self.x_expires_in = "x-user-token", "x-refresh-1", 7200
        self.refreshes = 0
        self.tweets: list[dict] = []  # replies "sent" to X (fake): {"text", "reply", "auth"}
        self.reposts: list[tuple[str, str, str]] = []  # (user id, tweet id, auth)
        self.x_write_error: tuple[int, dict] | None = None  # e.g. (429, {...}) for the next writes

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url))
        if url == "https://oauth.test/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"] and form["code_verifier"][0]
            claims = {"aud": GOOGLE_CLIENT, "iss": "https://accounts.google.com", "sub": self.google_sub, "name": self.google_name,
                      "exp": int(time.time()) + 600}
            payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
            return httpx.Response(200, json={"access_token": "discard-me", "id_token": f"h.{payload}.s"})
        if url == "https://api.x.test/2/oauth2/token" and parse_qs(request.content.decode()).get("grant_type") == ["refresh_token"]:
            form = parse_qs(request.content.decode())
            assert form["refresh_token"] == [self.x_refresh] and request.headers["authorization"].startswith("Basic ")
            self.refreshes += 1
            self.x_access, self.x_refresh = f"x-user-token-{self.refreshes}", f"x-refresh-{self.refreshes + 1}"
            return httpx.Response(200, json={"token_type": "bearer", "access_token": self.x_access, "refresh_token": self.x_refresh,
                                             "expires_in": 7200, "scope": self.x_scope})
        if url == "https://api.x.test/2/oauth2/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"] and form["code_verifier"][0] and form["client_id"] == ["member-client"]
            assert form["redirect_uri"] == ["http://127.0.0.1:5173/api/members/auth/x/callback"]
            assert request.headers["authorization"].startswith("Basic ")  # confidential client: HTTP Basic
            if self.x_token_error:
                return httpx.Response(self.x_token_error[0], json=self.x_token_error[1])
            return httpx.Response(200, json={"token_type": "bearer", "access_token": self.x_access, "refresh_token": self.x_refresh,
                                             "expires_in": self.x_expires_in, "scope": self.x_scope})
        if url == "https://api.x.test/2/users/me":
            assert request.headers["authorization"] == f"Bearer {self.x_access}"
            if self.x_me_status != 200:
                return httpx.Response(self.x_me_status, json={"title": "Forbidden"})
            return httpx.Response(200, json={"data": {"id": self.x_user, "username": self.x_name, "name": "Kiwi Fan"}})
        if url == "https://api.x.test/2/tweets" and request.method == "POST":
            if self.x_write_error:
                return httpx.Response(self.x_write_error[0], json=self.x_write_error[1])
            if request.headers["authorization"] != f"Bearer {self.x_access}":
                return httpx.Response(401, json={"title": "Unauthorized"})
            body = json.loads(request.content)
            self.tweets.append({**body, "auth": request.headers["authorization"]})
            return httpx.Response(201, json={"data": {"id": f"9{len(self.tweets)}", "text": body["text"]}})
        if url.startswith("https://api.x.test/2/users/") and url.endswith("/retweets") and request.method == "POST":
            if self.x_write_error:
                return httpx.Response(self.x_write_error[0], json=self.x_write_error[1])
            if request.headers["authorization"] != f"Bearer {self.x_access}":
                return httpx.Response(401, json={"title": "Unauthorized"})
            self.reposts.append((url.split("/")[-2], json.loads(request.content)["tweet_id"], request.headers["authorization"]))
            return httpx.Response(200, json={"data": {"retweeted": True}})
        if url == "https://api.x.test/2/oauth2/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200, json={"revoked": True})
        return httpx.Response(404)


@pytest.fixture
def providers() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
def fake_tg() -> FakeTelegram:
    tg = FakeTelegram()
    tg.add_chat(-100111, "Price Chat Community")
    tg.add_chat(-100222, "Random Other Group")
    return tg


def _app(providers: FakeProviders, fake_tg: FakeTelegram):  # type: ignore[no-untyped-def]
    settings = Settings(
        google_token_url="https://oauth.test/token", google_auth_url="https://accounts.test/auth",
        youtube_client_id=SecretStr(GOOGLE_CLIENT), youtube_client_secret=SecretStr("g-secret"),
        x_api_base="https://api.x.test", x_api_key=SecretStr("brand-ck"), x_api_secret=SecretStr("brand-cs"),
        x_member_client_id=SecretStr("member-client"), x_member_client_secret=SecretStr("member-secret"),
        x_oauth2_authorize_url="https://x.test/i/oauth2/authorize", x_oauth2_token_url="https://api.x.test/2/oauth2/token",
        x_oauth2_revoke_url="https://api.x.test/2/oauth2/revoke",
        telegram_bot_token=TOKEN, telegram_api_base="https://api.telegram.test", telegram_long_poll_seconds=0,
    )
    app = create_app(app_settings=settings, provider=FakeProvider(), embedder=FakeEmbedder(dims=512), background_jobs=False)
    return app


@pytest.fixture
def app_and_client(providers: FakeProviders, fake_tg: FakeTelegram) -> Iterator[tuple[object, TestClient]]:
    app = _app(providers, fake_tg)
    with TestClient(app) as c:
        app.state.runtime.members_transport = providers.transport
        app.state.runtime.telegram.transport = fake_tg.transport
        yield app, c


@pytest.fixture
def mc(app_and_client: tuple[object, TestClient]) -> TestClient:
    return app_and_client[1]


@pytest.fixture
def other_browser(app_and_client: tuple[object, TestClient]) -> Iterator[TestClient]:
    """A second, independent browser (its own cookie jar) on the same app."""
    app = app_and_client[0]
    yield TestClient(app)  # type: ignore[arg-type]  # no `with`: shares the running app, has its own cookies


# --- helpers ----------------------------------------------------------------------------------------------------


def google_sign_in(c: TestClient, purpose: str = "login") -> httpx.Response:
    start = c.get("/api/members/auth/google/start", params={"purpose": purpose}, follow_redirects=False)
    assert start.status_code == 303, start.text
    loc = urlparse(start.headers["location"])
    assert loc.netloc == "accounts.test"
    q = parse_qs(loc.query)
    assert q["scope"] == ["openid"] and q["code_challenge_method"] == ["S256"]  # an account id only: no name, email or YouTube
    return c.get("/api/members/auth/google/callback", params={"state": q["state"][0], "code": "c-1"}, follow_redirects=False)


def x_start(c: TestClient, purpose: str = "login") -> str:
    start = c.get("/api/members/auth/x/start", params={"purpose": purpose}, follow_redirects=False)
    loc = urlparse(start.headers["location"])
    assert start.status_code == 303 and loc.netloc == "x.test", start.headers.get("location")
    q = parse_qs(loc.query)
    assert q["client_id"] == ["member-client"]  # the member-login client, never the brand connector's keys
    # signing in asks only who you are (read-only consent screen); connecting X for missions adds refresh, and reply /
    # repost (tweet.write) only when the X app is set up for it (settings.x_member_write, off by default): never like.write
    write = c.app.state.runtime.settings.x_member_write  # type: ignore[attr-defined]
    want = "users.read tweet.read" if purpose == "login" else f"users.read tweet.read{' tweet.write' if write else ''} offline.access"
    assert q["scope"] == [want] and q["code_challenge_method"] == ["S256"]
    return q["state"][0]


def x_sign_in(c: TestClient, purpose: str = "login") -> httpx.Response:
    state = x_start(c, purpose)
    return c.get("/api/members/auth/x/callback", params={"state": state, "code": "x-code"}, follow_redirects=False)


def me(c: TestClient) -> dict:
    r = c.get("/api/members/me")
    assert r.status_code == 200, r.text
    return r.json()


def member_by_name(db: Session, username: str) -> Member:
    db.expire_all()
    m = db.scalar(select(Member).where(Member.username_key == username.lower()))
    assert m is not None
    return m


def new_member(db: Session, name: str = "tester") -> Member:
    ensure_catalog(db)
    m = service.create_member(db)
    m.username, m.username_key = name, name.lower()
    db.commit()
    return m


# --- accounts, sign-in, linking ---------------------------------------------------------------------------------


def test_google_sign_up_creates_member_and_session(mc: TestClient, db: Session) -> None:
    assert mc.get("/api/members/session").json() == {"signed_in": False, "member": None}
    r = google_sign_in(mc)
    assert r.status_code == 303 and r.headers["location"] == "/passport?welcome=google"
    cookie = r.headers["set-cookie"]
    assert "pc_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    p = me(mc)
    assert p["member"]["username"].startswith("Kiwi") and p["rank"]["current"]["name"] == "The Egg"  # never the Google name
    assert "Gina" not in json.dumps(p)
    assert {e["event_type"] for e in p["recent"]} == {"website.account_created", "identity.google_linked"}
    assert p["member"]["xp"] == 40
    assert [l["platform"] for l in p["linked"] if l["connected"]] == ["google"]
    s = mc.get("/api/members/session").json()  # daily visit, once per day
    assert s["signed_in"] and s["member"]["xp"] == 42
    assert mc.get("/api/members/session").json()["member"]["xp"] == 42
    # nothing secret is stored: no tokens anywhere in the identity, only a hash of the session cookie
    ident = db.scalar(select(LinkedIdentity))
    assert ident is not None and ident.platform_user_id == "g-1"
    assert db.scalar(select(MemberSession)).token_hash != mc.cookies.get("pc_session")


def test_signing_in_again_uses_the_same_account(mc: TestClient, other_browser: TestClient, db: Session) -> None:
    google_sign_in(mc)
    google_sign_in(other_browser)
    assert db.scalar(select(func.count()).select_from(Member)) == 1
    assert me(other_browser)["member"]["username"] == me(mc)["member"]["username"]


def test_link_x_to_signed_in_member(mc: TestClient, db: Session) -> None:
    google_sign_in(mc)
    r = x_sign_in(mc, purpose="link")
    assert r.headers["location"] == "/passport?linked=x"
    p = me(mc)
    linked = {l["platform"]: l for l in p["linked"]}
    assert linked["x"]["connected"] and linked["x"]["username"] == "kiwifan" and linked["google"]["connected"]
    assert p["member"]["xp"] == 60  # joined 20 + Google 20 + X 20


def test_duplicate_social_identity_is_refused(mc: TestClient, other_browser: TestClient, providers: FakeProviders, db: Session) -> None:
    google_sign_in(mc)
    x_sign_in(mc, purpose="link")  # X 42 belongs to member 1
    providers.google_sub = "g-2"
    google_sign_in(other_browser)  # member 2
    r = x_sign_in(other_browser, purpose="link")
    assert "error=" in r.headers["location"] and "already%20linked" in r.headers["location"]
    owner = db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == "x"))
    assert owner is not None and owner.member_id == member_by_name(db, me(mc)["member"]["username"]).id


def test_accounts_are_never_matched_by_username(mc: TestClient, other_browser: TestClient, providers: FakeProviders, db: Session) -> None:
    providers.google_name = "kiwifan"
    google_sign_in(mc)  # member "kiwifan" via Google
    x_sign_in(other_browser)  # X @kiwifan signs in: same name, but nobody proved it's the same person
    assert db.scalar(select(func.count()).select_from(Member)) == 2
    assert me(other_browser)["member"]["username"] != "kiwifan"
    assert [l["platform"] for l in me(other_browser)["linked"] if l["connected"]] == ["x"]


def test_sign_in_attempts_are_single_use_and_bound_to_the_browser(mc: TestClient, other_browser: TestClient) -> None:
    start = mc.get("/api/members/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    stolen = other_browser.get("/api/members/auth/google/callback", params={"state": state, "code": "c"}, follow_redirects=False)
    assert "same%20browser" in stolen.headers["location"]
    assert other_browser.get("/api/members/session").json()["signed_in"] is False
    ok = mc.get("/api/members/auth/google/callback", params={"state": state, "code": "c"}, follow_redirects=False)
    assert ok.headers["location"] == "/passport?welcome=google"  # a wrong browser cannot burn the real attempt
    replay = mc.get("/api/members/auth/google/callback", params={"state": state, "code": "c"}, follow_redirects=False)
    assert "no%20longer%20valid" in replay.headers["location"]  # single use
    cancelled = mc.get("/api/members/auth/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert "cancelled" in cancelled.headers["location"]


def _private_start(tg: FakeTelegram, link: str, user_id: int = 555, username: str = "alice_tg") -> None:
    param = link.split("start=")[1]
    chat = {"id": user_id, "type": "private", "username": username, "first_name": "Alice"}
    tg.message(user_id, f"/start {param}", user_id=user_id, name="Alice", extra={"chat": chat})


def test_telegram_sign_in_with_bot_start_link(mc: TestClient, other_browser: TestClient, fake_tg: FakeTelegram, db: Session) -> None:
    assert mc.post("/api/telegram/verify").status_code == 200  # the bot knows its username
    start = mc.post("/api/members/auth/telegram/start", json={"purpose": "login"}).json()
    assert start["link"].startswith(f"https://t.me/{BOT['username']}?start=pc_")
    assert mc.get(f"/api/members/auth/telegram/status/{start['request']}").json() == {"status": "pending"}
    _private_start(fake_tg, start["link"])
    mc.post("/api/telegram/sync")
    assert other_browser.get(f"/api/members/auth/telegram/status/{start['request']}").json() == {"status": "unknown"}
    done = mc.get(f"/api/members/auth/telegram/status/{start['request']}").json()
    assert done["status"] == "done" and done["username"] == "alice_tg"
    p = me(mc)
    linked = {l["platform"]: l for l in p["linked"] if l["connected"]}
    assert set(linked) == {"telegram"} and linked["telegram"]["username"] == "alice_tg"
    assert p["member"]["username"].startswith("Kiwi")  # the Telegram handle is not made public automatically
    assert db.scalar(select(func.count()).select_from(TelegramMessage)) == 0  # the /start message is never stored
    assert mc.get(f"/api/members/auth/telegram/status/{start['request']}").json() == {"status": "used"}
    sends = [b for m, b in fake_tg.calls if m == "sendMessage"]
    assert len(sends) == 1 and "signed in to Price Chat" in sends[0]["text"]  # one fixed "it worked" in that private chat
    _private_start(fake_tg, start["link"])  # the same (used) code again: no second message
    mc.post("/api/telegram/sync")
    assert len([1 for m, _ in fake_tg.calls if m == "sendMessage"]) == 1


def test_telegram_login_widget_signature(mc: TestClient) -> None:
    data = {"id": 777, "first_name": "Wid", "username": "widget_user", "auth_date": int(time.time())}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    good = hmac.new(hashlib.sha256(TOKEN.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    assert mc.post("/api/members/auth/telegram/widget", json={**data, "hash": "0" * 64}).status_code == 400
    assert mc.post("/api/members/auth/telegram/widget", json={**data, "username": "someone_else", "hash": good}).status_code == 400
    old = {**data, "auth_date": int(time.time()) - 3 * 86400}
    check_old = "\n".join(f"{k}={old[k]}" for k in sorted(old))
    stale = hmac.new(hashlib.sha256(TOKEN.encode()).digest(), check_old.encode(), hashlib.sha256).hexdigest()
    assert mc.post("/api/members/auth/telegram/widget", json={**old, "hash": stale}).status_code == 400
    assert mc.post("/api/members/auth/telegram/widget", json={**data, "hash": good}).json() == {"status": "done"}
    assert {l["platform"]: l["username"] for l in me(mc)["linked"] if l["connected"]} == {"telegram": "widget_user"}


# --- XP engine --------------------------------------------------------------------------------------------------


def test_xp_award_dedupe_caps_and_cooldown(db: Session) -> None:
    m = new_member(db)
    start = m.xp_total
    t0 = utcnow().replace(hour=0, minute=1, second=0, microsecond=0)  # early in the UTC day: "+30 min" must stay the same day
    r = ledger.award(db, m, "website.content_view", "content:a", occurred_at=t0)
    assert r.awarded and r.xp == 1
    assert ledger.award(db, m, "website.content_view", "content:a", occurred_at=t0 + timedelta(minutes=5)).refused == "duplicate"
    assert ledger.award(db, m, "website.content_view", "content:b", occurred_at=t0 + timedelta(seconds=5)).refused == "cooldown"
    for i in range(2, 9):
        ledger.award(db, m, "website.content_view", f"content:{i}", occurred_at=t0 + timedelta(minutes=i))
    assert m.xp_total - start == 5  # daily cap: 5 XP from this rule per day
    assert ledger.award(db, m, "website.content_view", "content:x", occurred_at=t0 + timedelta(minutes=30)).refused == "daily_cap"


def test_overall_daily_cap_limits_automatic_xp_but_not_staff_awards(db: Session) -> None:
    m = new_member(db)
    for i in range(20):
        ledger.award(db, m, "telegram.discussion", f"d{i}", occurred_at=utcnow())  # same day: rule cap 5
    paid_today = int(db.scalar(select(func.sum(ReputationEvent.xp)).where(ReputationEvent.member_id == m.id)) or 0)
    ledger.DAILY_XP_CAP, old = paid_today + 3, ledger.DAILY_XP_CAP
    try:
        r = ledger.award(db, m, "website.content_view", "content:z")
        assert r.awarded and r.xp == 1
        ledger.award(db, m, "website.content_view", "content:y", occurred_at=utcnow() + timedelta(minutes=1))
        ledger.award(db, m, "website.content_view", "content:w", occurred_at=utcnow() + timedelta(minutes=2))
        assert ledger.award(db, m, "website.content_view", "content:v", occurred_at=utcnow() + timedelta(minutes=3)).refused == "overall_cap"
        assert ledger.adjust(db, m, 50, actor="dashboard", reason="Ran the AMA", ref="ama").awarded  # staff not capped
    finally:
        ledger.DAILY_XP_CAP = old


def test_rank_calculation_and_progression(db: Session) -> None:
    m = new_member(db)  # 20 XP
    p = service.rank_progress(db, m)
    assert p["current"]["key"] == "egg" and p["next"]["key"] == "chick" and p["xp_to_next"] == 30 and p["progress"] == 0.4
    ledger.adjust(db, m, 30, actor="dashboard", reason="test", ref="a")
    assert m.rank_level == 2 and service.rank_progress(db, m)["current"]["name"] == "Kiwi Chick"
    ledger.adjust(db, m, 16000, actor="dashboard", reason="test", ref="b")
    top = service.rank_progress(db, m)
    assert top["current"]["key"] == "mythic" and top["next"] is None and top["progress"] == 1.0
    assert [r.min_xp for r in ledger.ranks(db)] == sorted(r.min_xp for r in ledger.ranks(db))


def test_manual_adjustment_and_reversal_are_audited(db: Session) -> None:
    m = new_member(db)
    r = ledger.adjust(db, m, 500, actor="dashboard", reason="Community call host", ref="call-1")
    assert m.xp_total == 520 and m.rank_level == 4
    with pytest.raises(ValueError):
        ledger.adjust(db, m, 5, actor="dashboard", reason="  ", ref="x")
    rev = ledger.reverse(db, r.event, actor="dashboard", reason="Entered twice")
    assert m.xp_total == 20 and m.rank_level == 1 and rev.xp == -500
    assert r.event.reversed_at is not None  # the original stays in the ledger, marked
    with pytest.raises(ValueError):
        ledger.reverse(db, r.event, actor="dashboard", reason="again")
    with pytest.raises(ValueError):
        ledger.reverse(db, rev, actor="dashboard", reason="undo the undo")
    actions = [a.action for a in db.scalars(select(MemberAuditLog).where(MemberAuditLog.member_id == m.id))]
    assert "xp_adjusted" in actions and "xp_reversed" in actions


def test_premium_xp_never_counts_towards_rank(db: Session) -> None:
    m = new_member(db)
    db.add(ReputationEvent(member_id=m.id, event_type="purchase.future", xp=50000, category="premium", source="partner",
                           source_ref="order-1", reason="Partner purchase", occurred_at=utcnow(), day=ledger.day_of(utcnow())))
    db.flush()
    ledger.recalc(db, m)
    assert m.bonus_xp == 50000 and m.xp_total == 20 and m.rank_level == 1


def test_unavailable_and_future_rules_cannot_be_switched_on(mc: TestClient) -> None:
    for rule in ("x.like_pricechat", "google.youtube_watch", "purchase.future", "telegram.message_quality"):
        r = mc.patch(f"/api/members-admin/rules/{rule}", json={"enabled": True})
        assert r.status_code == 400, rule
    ok = mc.patch("/api/members-admin/rules/website.content_view", json={"daily_cap": 3})
    assert next(x for x in ok.json()["rules"] if x["event_type"] == "website.content_view")["daily_cap"] == 3
    public = {r["event_type"]: r for r in mc.get("/api/members/rules").json()["rules"]}
    assert "purchase.future" not in public  # premium is not part of the public reputation rules
    assert public["google.youtube_watch"]["availability"] == "unavailable" and "never" in public["google.youtube_watch"]["availability_note"]


def test_suspended_xp_and_banned_members(mc: TestClient, db: Session) -> None:
    google_sign_in(mc)
    m = member_by_name(db, me(mc)["member"]["username"])
    mc.post(f"/api/members-admin/members/{m.public_id}/status", json={"xp_suspended": True, "reason": "Farming review"})
    db.expire_all()
    assert ledger.award(db, m, "website.streak", "s1").refused == "suspended"
    assert ledger.adjust(db, m, 10, actor="dashboard", reason="still possible for staff", ref="s").awarded
    mc.post(f"/api/members-admin/members/{m.public_id}/status", json={"status": "banned", "reason": "Bot account"})
    assert mc.get("/api/members/session").json()["signed_in"] is False  # session no longer valid
    assert "suspended" in google_sign_in(mc).headers["location"]  # cannot sign back in


def test_burst_of_refused_attempts_raises_one_flag(db: Session) -> None:
    m = new_member(db)
    ledger.award(db, m, "website.content_view", "content:same")
    for i in range(ledger.FLAG_AFTER_REFUSALS + 10):
        ledger.award(db, m, "website.content_view", "content:same")
    db.commit()
    flags = list(db.scalars(select(ModerationFlag).where(ModerationFlag.member_id == m.id)))
    assert len(flags) == 1 and flags[0].kind == "rapid_activity" and flags[0].status == "open"
    assert m.status == "active" and not m.xp_suspended  # a flag never acts on its own


# --- badges & entitlements --------------------------------------------------------------------------------------


def test_badges_auto_and_manual(mc: TestClient, db: Session) -> None:
    google_sign_in(mc)
    pid = me(mc)["member"]["id"]
    assert {b["key"] for b in me(mc)["badges"]} == {"early_kiwi", "first_100"}
    assert mc.post(f"/api/members-admin/members/{pid}/badges", json={"badge": "og", "reason": "Day-one supporter"}).status_code == 200
    assert mc.post(f"/api/members-admin/members/{pid}/badges", json={"badge": "og", "reason": "again"}).status_code == 400
    assert mc.post(f"/api/members-admin/members/{pid}/badges", json={"badge": "partner", "reason": "not yet"}).status_code == 400
    m = member_by_name(db, me(mc)["member"]["username"])
    for d in range(10):
        ledger.award(db, m, "telegram.first_participation", f"tg-first:{d}", occurred_at=utcnow() - timedelta(days=d))
    assert "conversation_starter" in {b["key"] for b in me(mc)["badges"]}
    mc.post(f"/api/members-admin/members/{pid}/badges/og/remove", json={"reason": "Given by mistake"})
    assert "og" not in {b["key"] for b in me(mc)["badges"]}


def test_entitlements_follow_rank_and_manual_grants(mc: TestClient, db: Session) -> None:
    google_sign_in(mc)
    pid = me(mc)["member"]["id"]
    ent = {e["key"]: e["unlocked"] for e in me(mc)["entitlements"]}
    assert not any(ent.values())
    mc.post(f"/api/members-admin/members/{pid}/xp", json={"amount": 400, "reason": "Rank 4 for the test"})
    ent = {e["key"]: e["unlocked"] for e in me(mc)["entitlements"]}
    assert ent == {"profile_cosmetic": True, "community_badge": False, "special_content": False, "vip_access": False}
    mc.post(f"/api/members-admin/members/{pid}/entitlements", json={"entitlement": "special_content", "reason": "Beta tester"})
    assert {e["key"]: e["unlocked"] for e in me(mc)["entitlements"]}["special_content"] is True
    mc.patch("/api/members-admin/entitlements/profile_cosmetic", json={"enabled": False})
    assert {e["key"]: e["unlocked"] for e in me(mc)["entitlements"]}["profile_cosmetic"] is False


# --- profile, leaderboard, privacy ------------------------------------------------------------------------------


def test_profile_username_rules(mc: TestClient, other_browser: TestClient, providers: FakeProviders) -> None:
    google_sign_in(mc)
    for bad, why in (("ab", "3-20"), ("admin", "reserved"), ("no spaces", "3-20")):
        r = mc.patch("/api/members/me", json={"username": bad})
        assert r.status_code == 400 and why in r.json()["detail"]
    ok = mc.patch("/api/members/me", json={"username": "KiwiKing"}).json()
    assert ok["member"]["username"] == "KiwiKing" and "website.profile_completed" in {e["event_type"] for e in ok["recent"]}
    mc.patch("/api/members/me", json={"username": "KiwiQueen"})
    assert [e["event_type"] for e in me(mc)["recent"]].count("website.profile_completed") == 1  # once only
    providers.google_sub = "g-2"
    google_sign_in(other_browser)
    assert "taken" in other_browser.patch("/api/members/me", json={"username": "kiwiqueen"}).json()["detail"]


def test_leaderboard_privacy(mc: TestClient, other_browser: TestClient, providers: FakeProviders, db: Session) -> None:
    google_sign_in(mc)
    mc.patch("/api/members/me", json={"username": "Visible"})
    providers.google_sub = "g-2"
    google_sign_in(other_browser)
    other_browser.patch("/api/members/me", json={"username": "Hidden", "leaderboard_opt_out": True})
    rows = mc.get("/api/members/leaderboard", params={"period": "all"}).json()["rows"]
    assert [r["username"] for r in rows] == ["Visible"]
    assert set(rows[0]) == {"position", "username", "value", "rank_score", "xp", "legacy_points", "rank"}  # no ids, no linked accounts
    blob = json.dumps(rows)
    assert "g-1" not in blob and "google" not in blob.lower()
    m = member_by_name(db, "Visible")
    old = utcnow() - timedelta(days=40)
    for e in db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == m.id)):
        e.occurred_at = old
    db.commit()
    assert mc.get("/api/members/leaderboard", params={"period": "week"}).json()["rows"] == []
    mc.post(f"/api/members-admin/members/{m.public_id}/status", json={"status": "banned", "reason": "test"})
    assert mc.get("/api/members/leaderboard", params={"period": "all"}).json()["rows"] == []


def test_disconnect_platform(mc: TestClient, db: Session, providers: FakeProviders) -> None:
    google_sign_in(mc)
    r = mc.post("/api/members/me/disconnect/google")
    assert r.status_code == 400 and "only sign-in method" in r.json()["detail"]
    x_sign_in(mc, purpose="link")
    p = mc.post("/api/members/me/disconnect/x").json()
    assert not {l["platform"]: l for l in p["linked"]}["x"]["connected"]
    assert db.scalar(select(func.count()).select_from(LinkedIdentity).where(LinkedIdentity.platform == "x")) == 0
    assert db.scalar(select(func.count()).select_from(MemberXToken)) == 0
    assert sorted(providers.revoked) == ["x-refresh-1", "x-user-token"]  # the member's tokens are invalidated at X too
    assert mc.post("/api/members/me/disconnect/telegram").status_code == 400


def test_member_deletion_removes_everything(mc: TestClient, db: Session, providers: FakeProviders) -> None:
    google_sign_in(mc)
    x_sign_in(mc, purpose="link")
    username = me(mc)["member"]["username"]
    assert mc.request("DELETE", "/api/members/me", json={"confirm_username": "wrong"}).status_code == 400
    assert mc.request("DELETE", "/api/members/me", json={"confirm_username": username}).json() == {"deleted": True}
    assert sorted(providers.revoked) == ["x-refresh-1", "x-user-token"]
    for model in (Member, LinkedIdentity, ReputationEvent, MemberSession):
        assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__
    assert mc.get("/api/members/me").status_code == 401
    entry = db.scalar(select(MemberAuditLog).where(MemberAuditLog.action == "member_deleted"))
    assert entry is not None and username not in json.dumps(entry.details)
    # one-time rewards cannot be farmed by deleting and re-linking the same account
    assert db.scalar(select(func.count()).select_from(XPClaim)) == 2
    google_sign_in(mc)
    events = {e["event_type"] for e in me(mc)["recent"]}
    assert "website.account_created" in events and "identity.google_linked" not in events


def test_content_view_xp_endpoint(mc: TestClient, db: Session) -> None:
    db.add(ContentItem(platform=Platform.YOUTUBE, external_id="vid1", title="A real Short", url="https://youtube.com/shorts/vid1",
                       privacy_status="public", available=True, website_visible=True, published_at=utcnow()))
    db.commit()
    assert mc.post("/api/members/me/content-view", json={"external_id": "vid1"}).json()["refused"] == "signed_out"
    google_sign_in(mc)
    assert mc.post("/api/members/me/content-view", json={"external_id": "nope"}).status_code == 400
    assert mc.post("/api/members/me/content-view", json={"external_id": "vid1"}).json() == {"awarded": True, "xp": 1, "refused": None}
    assert mc.post("/api/members/me/content-view", json={"external_id": "vid1"}).json()["refused"] == "duplicate"
    assert "Opened “A real Short”" in {e["reason"] for e in me(mc)["recent"]}


def test_seven_day_streak(db: Session) -> None:
    m = new_member(db)
    for d in range(6, 0, -1):
        when = utcnow() - timedelta(days=d)
        ledger.award(db, m, "website.login", f"day:{ledger.day_of(when)}", occurred_at=when)
    service.daily_visit(db, m)
    types = [e.event_type for e in db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == m.id))]
    assert types.count("website.streak") == 1 and types.count("website.login") == 7


# --- verified platform XP ---------------------------------------------------------------------------------------


def _link(db: Session, m: Member, platform: str, uid: str, ago: timedelta = timedelta(hours=1)) -> LinkedIdentity:
    i = service.link_identity(db, m, platform, uid, "user", "User")
    i.verified_at = utcnow() - ago
    db.commit()
    return i


def _tg_msg(db: Session, source: TelegramSource, user: int, text: str, ago: timedelta, n: int, **kw) -> None:  # type: ignore[no-untyped-def]
    db.add(TelegramMessage(source_id=source.id, chat_id=source.chat_id, message_id=n, author_id=user, author_name="U",
                           sent_at=utcnow() - ago, text=text, **kw))


def test_telegram_participation_counts_meaningful_messages_only(db: Session) -> None:
    m = new_member(db, "tguser")
    _link(db, m, "telegram", "555", ago=timedelta(hours=3))
    src = TelegramSource(chat_id=-100111, display_name="Price Chat Community", enabled=True)
    other = TelegramSource(chat_id=-100333, display_name="Disabled chat", enabled=False)
    db.add_all([src, other])
    db.flush()
    _tg_msg(db, src, 555, "this was before linking, should not count", timedelta(hours=5), 1)
    for i, short in enumerate(["gm", "lol", "🚀", "/start@bot please"]):  # too short, emoji, command
        _tg_msg(db, src, 555, short, timedelta(hours=2, minutes=i), 10 + i)
    _tg_msg(db, src, 555, "forwarded long message from somewhere else", timedelta(hours=2), 20, forward_from="Some channel")
    _tg_msg(db, other, 555, "a long message in a chat that is not enabled", timedelta(hours=2), 21)
    _tg_msg(db, src, 999, "someone else who never linked an account", timedelta(hours=2), 22)
    db.commit()
    assert verified.telegram_participation(db)["awarded"] == 0  # nothing meaningful yet
    _tg_msg(db, src, 555, "bitcoin looks strong into the weekly close", timedelta(minutes=50), 30)
    db.commit()
    assert verified.telegram_participation(db)["awarded"] == 1  # first meaningful message today: +3
    _tg_msg(db, src, 555, "gold is the quiet winner this year honestly", timedelta(minutes=40), 31)
    _tg_msg(db, src, 555, "bitcoin looks strong into the weekly close", timedelta(minutes=30), 32)  # repeat: no XP
    db.commit()
    assert verified.telegram_participation(db)["awarded"] == 1  # one more distinct message: +1
    assert verified.telegram_participation(db)["awarded"] == 0  # nothing pays twice
    rows = {e.event_type: e.xp for e in db.scalars(select(ReputationEvent).where(ReputationEvent.event_type.like("telegram.%")))}
    assert rows == {"telegram.first_participation": 3, "telegram.discussion": 1}
    for i in range(8):  # further discussion is capped per day
        _tg_msg(db, src, 555, f"another genuinely different thought number {i}", timedelta(minutes=20 - i), 40 + i)
    db.commit()
    verified.telegram_participation(db)
    disc = int(db.scalar(select(func.sum(ReputationEvent.xp)).where(ReputationEvent.event_type == "telegram.discussion")) or 0)
    assert disc == 5


def test_repeated_telegram_messages_are_flagged_not_rewarded(db: Session) -> None:
    m = new_member(db, "spammer")
    _link(db, m, "telegram", "777", ago=timedelta(hours=3))
    src = TelegramSource(chat_id=-100111, display_name="Price Chat Community", enabled=True)
    db.add(src)
    db.flush()
    for i in range(6):
        _tg_msg(db, src, 777, "join my channel for free signals now", timedelta(minutes=30 + i), 100 + i)
    _tg_msg(db, src, 777, "and also something else that is long enough", timedelta(minutes=20), 200)
    db.commit()
    out = verified.telegram_participation(db)
    assert out["awarded"] == 0 and out["flags"] == 1  # a spamming day earns nothing at all
    flag = db.scalar(select(ModerationFlag).where(ModerationFlag.member_id == m.id))
    assert flag is not None and flag.kind == "repeated_messages"


def test_x_activity_never_earns_xp_x_developer_policy(db: Session) -> None:
    """X's developer policy forbids rewards for actions on X: replies and mentions are still recognised (missions can
    show "Done"), but nothing is paid, whatever the rule settings say."""
    m = new_member(db, "xuser")
    m.created_at = utcnow() - timedelta(days=3)
    _link(db, m, "x", "42", ago=timedelta(days=2))
    me_profile = SocialProfile(platform=Platform.X, external_id="9000", username="pricechat", is_self=True)
    fan = SocialProfile(platform=Platform.X, external_id="42", username="kiwifan")
    stranger = SocialProfile(platform=Platform.X, external_id="77", username="kiwifan2")
    db.add_all([me_profile, fan, stranger])
    db.flush()
    now = utcnow()
    db.add_all([
        SocialPost(platform=Platform.X, external_id="own-1", text="Our new Short", is_own=True, posted_at=now - timedelta(days=1)),
        SocialPost(platform=Platform.X, external_id="p1", text="@pricechat great breakdown of the gold move today", author_profile_id=fan.id,
                   in_reply_to_external_id="9000", referenced=[{"type": "replied_to", "id": "own-1"}],
                   posted_at=now - timedelta(hours=5), source_kinds=["mention"]),
        SocialPost(platform=Platform.X, external_id="p2", text="@pricechat 🔥🔥 https://x.com/a", author_profile_id=fan.id,
                   posted_at=now - timedelta(hours=4), source_kinds=["mention"]),
        SocialPost(platform=Platform.X, external_id="p3", text="Watching @pricechat for the Bitcoin halving coverage", author_profile_id=fan.id,
                   posted_at=now - timedelta(hours=3), source_kinds=["mention"]),
        SocialPost(platform=Platform.X, external_id="p4", text="old post before linking, mentions @pricechat a lot", author_profile_id=fan.id,
                   posted_at=now - timedelta(days=3), source_kinds=["mention"]),
        SocialPost(platform=Platform.X, external_id="p5", text="@pricechat not linked to any member account at all", author_profile_id=stranger.id,
                   posted_at=now - timedelta(hours=2), source_kinds=["mention"]),
    ])
    db.commit()
    ensure_catalog(db)
    from app.models.members import XPRule

    for rule in db.scalars(select(XPRule).where(XPRule.event_type.like("x.%"))):
        assert rule.enabled is False and rule.availability == "unavailable" and "developer policy" in rule.availability_note
        rule.enabled, rule.availability = True, "available"  # even switched back on by hand...
    db.commit()
    out = verified.x_interactions(db)
    assert out["awarded"] == 0  # ...the ledger refuses
    assert db.scalar(select(ReputationEvent.id).where(ReputationEvent.event_type.like("x.%"))) is None
    ensure_catalog(db)  # and the lock is restored on the next start
    assert db.scalar(select(XPRule.enabled).where(XPRule.event_type == "x.reply_pricechat")) is False


def test_x_engagement_xp_is_refused_by_the_ledger(db: Session) -> None:
    m = new_member(db, "fresh")
    for kind in ("x.mention_pricechat", "x.reply_pricechat", "x.like_pricechat", "x.repost_pricechat"):
        assert ledger.award(db, m, kind, "x-post:1").refused == "x_policy"


# --- admin ------------------------------------------------------------------------------------------------------


def test_admin_overview_member_table_and_audit(mc: TestClient, db: Session) -> None:
    google_sign_in(mc)
    pid = me(mc)["member"]["id"]
    ov = mc.get("/api/members-admin/overview").json()
    assert ov["totals"]["members"] == 1 and ov["totals"]["new_7d"] == 1 and ov["linked"]["google"] == 1
    assert ov["rank_distribution"][0]["members"] == 1 and ov["members_over_time"][-1]["members"] == 1
    assert {r["source"] for r in ov["xp_by_source"]} >= {"website", "google"}
    table = mc.get("/api/members-admin/members").json()
    assert table["total"] == 1 and table["members"][0]["linked"] == ["google"]
    mc.post(f"/api/members-admin/members/{pid}/award", json={"event_type": "community.event", "reason": "Joined the live AMA",
                                                             "reference": "ama-2026-10"})
    again = mc.post(f"/api/members-admin/members/{pid}/award", json={"event_type": "community.event", "reason": "dup",
                                                                     "reference": "ama-2026-10"})
    assert again.status_code == 400  # one award per event per member
    mc.post(f"/api/members-admin/members/{pid}/flags", json={"reason": "Check this account"})
    detail = mc.get(f"/api/members-admin/members/{pid}").json()
    flag_id = detail["flags"][0]["id"]
    mc.post(f"/api/members-admin/flags/{flag_id}/resolve", json={"status": "dismissed", "resolution": "Looked fine"})
    ev = next(e for e in detail["events"] if e["event_type"] == "community.event")
    mc.post(f"/api/members-admin/events/{ev['id']}/reverse", json={"reason": "Wrong person"})
    actions = {e["action"] for e in mc.get("/api/members-admin/audit").json()["entries"]}
    assert {"xp_staff_award", "flagged", "flag_dismissed", "xp_reversed", "member_created", "identity_linked"} <= actions
    bad = mc.patch("/api/members-admin/ranks/3", json={"min_xp": 10})
    assert bad.status_code == 400  # thresholds must keep increasing
    assert mc.patch("/api/members-admin/ranks/3", json={"name": "Kiwi Regular"}).status_code == 200
