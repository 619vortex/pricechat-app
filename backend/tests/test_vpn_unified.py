"""One account across every Kiwi VPN client, on a throwaway database. No real account or XP balance is touched: `client` and `db` are the suite's test fixtures."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import referrals
from app.members import service as members
from app.members import xp as ledger
from app.members.desktop_scope import DESKTOP_TOKEN_PREFIX
from app.models.members import Member, Referral, ReputationEvent, XPRule
from app.models.vpn import VpnAccount
from app.vpn import service as vpn
from app.vpn import unified
from tests.test_vpn import FakeNode, add_server, fake, member_at, open_vpn  # noqa: F401

STATE = "/api/members/vpn/v1/state"
CAPS = "/api/members/vpn/v1/capabilities"
LEGACY = "/api/members/vpn"
DEVICES = "/api/members/vpn/devices"
PUBLIC_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


def browser(db: Session, m: Member) -> dict[str, str]:
    return {"Cookie": f"pc_session={members.new_session(db, m, 7)}"}


def app_session(db: Session, m: Member) -> dict[str, str]:
    return {"Cookie": f"pc_session={members.new_session(db, m, 90, DESKTOP_TOKEN_PREFIX, kind='desktop')}"}


def state_of(client: TestClient, headers: dict[str, str], platform: str = "android", version: str = "0.1.0") -> dict:
    out = client.get(STATE, params={"platform": platform, "version": version}, headers=headers)
    assert out.status_code == 200, out.text
    return out.json()


def account_with_trial(db: Session, name: str) -> Member:
    open_vpn(db, payments_enabled=False, trial_enabled=True)
    m = member_at(db, "egg", name, paid=False)
    vpn.start_trial(db, m)
    return m


# --- one account everywhere ---


def test_every_client_sees_the_same_account(client: TestClient, db: Session, fake: FakeNode) -> None:
    m = account_with_trial(db, "sameall")
    add_server(db, fake)
    phone, website, pc = app_session(db, m), browser(db, m), app_session(db, m)
    a, b, c = state_of(client, phone), state_of(client, website, "website", "1"), state_of(client, pc, "windows", "0.2.0")
    for key in ("eligible", "plan", "servers", "devices", "max_devices"):
        assert a["vpn"][key] == b["vpn"][key] == c["vpn"][key], key
    assert a["passport"]["xp"] == b["passport"]["xp"] == c["passport"]["xp"] == m.xp_total
    assert a["passport"]["username"] == b["passport"]["username"] == "sameall"


def test_the_vpn_block_is_exactly_what_the_existing_route_answers(client: TestClient, db: Session, fake: FakeNode) -> None:
    m = account_with_trial(db, "exact")
    add_server(db, fake)
    h = app_session(db, m)
    assert state_of(client, h)["vpn"] == client.get(LEGACY, headers=h).json()


def test_a_trial_started_on_one_client_shows_on_the_other_with_no_second_trial(client: TestClient, db: Session) -> None:
    open_vpn(db, payments_enabled=False, trial_enabled=True)
    m = member_at(db, "egg", "trialone", paid=False)
    phone, pc = app_session(db, m), app_session(db, m)
    assert state_of(client, phone)["vpn"]["trial"]["available"] is True
    assert client.post("/api/members/vpn/trial", headers=pc).status_code == 200
    seen = state_of(client, phone)["vpn"]
    assert seen["plan"]["kind"] == "trial" and seen["trial"]["available"] is False and seen["eligible"] is True
    assert client.post("/api/members/vpn/trial", headers=phone).status_code == 409


def test_reading_the_state_never_starts_a_trial_or_writes_xp(client: TestClient, db: Session) -> None:
    open_vpn(db, payments_enabled=False, trial_enabled=True)
    m = member_at(db, "egg", "readonly", paid=False)
    before = db.scalar(select(func.count()).select_from(ReputationEvent))
    h = app_session(db, m)
    for _ in range(3):
        out = state_of(client, h)
    assert out["vpn"]["plan"]["kind"] == "not_activated"
    db.expire_all()
    acct = db.scalar(select(VpnAccount).where(VpnAccount.member_id == m.id))
    assert acct.trial_started_at is None
    assert db.scalar(select(func.count()).select_from(ReputationEvent)) == before


def test_trial_expiry_applies_to_every_client_at_once(client: TestClient, db: Session, fake: FakeNode) -> None:
    m = account_with_trial(db, "expiry")
    server = add_server(db, fake)
    phone, pc = app_session(db, m), app_session(db, m)
    made = client.post(DEVICES, json={"name": "Phone", "server_id": server.id, "public_key": PUBLIC_KEY}, headers=phone)
    assert made.status_code == 201
    acct = db.scalar(select(VpnAccount).where(VpnAccount.member_id == m.id))
    acct.trial_ends_at = utcnow() - timedelta(minutes=1)
    db.commit()
    for h in (phone, pc):
        seen = state_of(client, h)["vpn"]
        assert seen["eligible"] is False and seen["plan"]["kind"] == "expired"
        assert client.get(f"{DEVICES}/{made.json()['device']['id']}/peer", headers=h).status_code == 403


def test_subscription_status_is_the_same_everywhere(client: TestClient, db: Session) -> None:
    open_vpn(db)
    m = member_at(db, "egg", "paying", paid=False)
    phone, site = app_session(db, m), browser(db, m)
    state_of(client, phone)
    acct = db.scalar(select(VpnAccount).where(VpnAccount.member_id == m.id))
    acct.paid_until = utcnow() + timedelta(days=10)
    db.commit()
    a, b = state_of(client, phone)["vpn"]["plan"], state_of(client, site, "website", "1")["vpn"]["plan"]
    assert a["kind"] == b["kind"] == "paid" and a["days_left"] == b["days_left"]


def test_a_new_server_reaches_every_client(client: TestClient, db: Session, fake: FakeNode) -> None:
    m = account_with_trial(db, "newserver")
    phone, pc = app_session(db, m), app_session(db, m)
    assert state_of(client, phone)["vpn"]["servers"] == []
    server = add_server(db, fake, "Helsinki")
    assert [s["id"] for s in state_of(client, phone)["vpn"]["servers"]] == [server.id]
    assert [s["id"] for s in client.get(LEGACY, headers=pc).json()["servers"]] == [server.id]


# --- Passport ---


def test_xp_earned_anywhere_shows_everywhere_from_the_one_ledger(client: TestClient, db: Session) -> None:
    m = account_with_trial(db, "xpsync")
    phone, site = app_session(db, m), browser(db, m)
    start = state_of(client, phone)["passport"]["xp"]
    ledger.adjust(db, m, 100, actor="test", reason="test award", ref="sync-1")
    assert state_of(client, phone)["passport"]["xp"] == start + 100
    assert client.get("/api/members/me", headers=site).json()["member"]["xp"] == start + 100


def test_the_passport_matches_the_websites_figures(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "matches", paid=False)
    ledger.adjust(db, m, 250, actor="test", reason="test", ref="m-1")
    h = browser(db, m)
    passport, site = state_of(client, h)["passport"], client.get("/api/members/me", headers=h).json()
    assert passport["rank"] == site["rank"]
    assert passport["standing"] == site["standing"]
    assert passport["badges"] == site["badges"]


def test_the_same_xp_activity_never_pays_twice(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "twice", paid=False)
    rule = db.scalar(select(XPRule).where(XPRule.event_type == "website.profile_completed"))
    assert rule is not None
    first = ledger.award(db, m, rule.event_type, "same-ref")
    db.refresh(m)
    total = m.xp_total
    second = ledger.award(db, m, rule.event_type, "same-ref")
    db.refresh(m)
    assert first.awarded and not second.awarded and m.xp_total == total
    assert db.scalar(select(func.count()).select_from(ReputationEvent).where(ReputationEvent.member_id == m.id, ReputationEvent.source_ref == "same-ref")) == 1


def test_a_referral_cant_be_claimed_twice_or_by_yourself(client: TestClient, db: Session) -> None:
    referrals.program(db).referrals_enabled = True
    db.commit()
    owner, friend = member_at(db, "egg", "owner", paid=False), member_at(db, "egg", "friend", paid=False)
    code = referrals.code_for(db, owner)
    assert referrals.attribute(db, friend, code, browser="b1", browser_member=None).status == "joined"
    assert referrals.attribute(db, friend, code, browser="b2", browser_member=None) is None
    assert db.scalar(select(func.count()).select_from(Referral).where(Referral.referred_id == friend.id)) == 1
    assert referrals.attribute(db, owner, code, browser="b3", browser_member=None).status == "rejected"


def test_the_referral_link_comes_from_the_existing_program_and_respects_its_switch(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "linker", paid=False)
    referrals.program(db).referrals_enabled = True
    db.commit()
    h = app_session(db, m)
    on = state_of(client, h)
    assert on["capabilities"]["referrals_enabled"] is True
    assert on["passport"]["referral"]["link"].endswith(f"/website/join/{referrals.code_for(db, m)}")
    referrals.program(db).referrals_enabled = False
    db.commit()
    off = state_of(client, h)
    assert off["capabilities"]["referrals_enabled"] is False and off["passport"]["referral"] is None


# --- capabilities ---


def test_each_platform_gets_only_what_it_supports(client: TestClient, db: Session) -> None:
    android = client.get(CAPS, params={"platform": "android", "version": "0.1.0-beta1"}).json()
    windows_today = client.get(CAPS, params={"platform": "windows", "version": "0.1.1"}).json()
    website = client.get(CAPS, params={"platform": "website", "version": "1"}).json()
    assert android["auto_call_enabled"] is True and android["passport_enabled"] is True and android["app_routing_enabled"] is True
    assert windows_today["app_routing_enabled"] is False and website["app_routing_enabled"] is False
    assert windows_today["auto_call_enabled"] is False and windows_today["passport_enabled"] is False
    assert website["auto_call_enabled"] is False and website["passport_enabled"] is True


def test_unfinished_features_are_off_everywhere(db: Session) -> None:
    for platform in unified.PLATFORMS:
        caps = unified.capabilities(db, platform, "99.0")
        assert caps["gaming_mode_enabled"] is False and caps["smart_routing_enabled"] is False and caps["vpn_missions_enabled"] is False


def test_an_unknown_platform_or_unreadable_version_gets_nothing(db: Session) -> None:
    for platform, version in (("ios", "1.0"), (None, "1.0"), ("android", None), ("android", ""), ("android", "abc"), ("android", "1.0; drop table"), ("android", "0.0.9")):
        assert not any(unified.capabilities(db, platform, version).values()), (platform, version)


def test_versions_compare_as_numbers(db: Session) -> None:
    assert unified.capabilities(db, "windows", "0.10.0")["passport_enabled"] is True
    assert unified.capabilities(db, "windows", "0.2")["passport_enabled"] is True
    assert unified.capabilities(db, "windows", "0.1.9")["passport_enabled"] is False


def test_a_capability_never_grants_vpn_access(client: TestClient, db: Session, fake: FakeNode) -> None:
    open_vpn(db)
    m = member_at(db, "egg", "noaccess", paid=False)
    server = add_server(db, fake)
    h = app_session(db, m)
    seen = state_of(client, h, "android", "99.0")
    assert seen["capabilities"]["auto_call_enabled"] is True and seen["vpn"]["eligible"] is False
    assert client.post(DEVICES, json={"name": "Phone", "server_id": server.id, "public_key": PUBLIC_KEY}, headers=h).status_code == 403


# --- proposed missions pay nothing ---


def test_proposed_missions_are_never_sent_or_payable(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "missions", paid=False)
    h = app_session(db, m)
    assert state_of(client, h)["missions"] == []
    for mission in unified.PROPOSED_MISSIONS:
        assert db.scalar(select(XPRule).where(XPRule.event_type == mission.key)) is None, "No XP rule may exist until approved"
    routes = {getattr(r, "path", "") for r in client.app.routes}
    assert not any("mission" in p and "/vpn/" in p for p in routes)


def test_only_what_the_server_can_prove_could_ever_pay() -> None:
    assert {m.key for m in unified.PROPOSED_MISSIONS if not m.verifiable} == {"vpn.onboarding", "vpn.gaming_mode", "vpn.security_check"}
    assert unified.missions_out({"vpn_missions_enabled": False}) == []
    assert {m["key"] for m in unified.missions_out({"vpn_missions_enabled": True})} == {"vpn.first_connection", "vpn.referral"}


# --- nothing live changes ---


def test_the_fields_windows_0_1_1_reads_are_still_there(client: TestClient, db: Session, fake: FakeNode) -> None:
    """From windows-client/client/src/api.rs, which is what's installed on customers' PCs."""
    m = account_with_trial(db, "winclient")
    server = add_server(db, fake)
    h = app_session(db, m)
    account = client.get(LEGACY, headers=h).json()
    assert {"enabled", "eligible", "reason", "max_devices", "servers", "devices", "plan", "trial"} <= set(account)
    assert {"id", "name", "region", "country_code", "online", "recommended"} <= set(account["servers"][0])
    assert {"kind", "days_left"} <= set(account["plan"]) and {"available", "days"} <= set(account["trial"])
    made = client.post(DEVICES, json={"name": "PC", "server_id": server.id, "public_key": PUBLIC_KEY}, headers=h)
    assert made.status_code == 201
    assert {"id", "name", "server_id"} <= set(made.json()["device"])
    peer = client.get(f"{DEVICES}/{made.json()['device']['id']}/peer", headers=h).json()
    assert peer == made.json()["peer"]
    assert {"server_public_key", "endpoint_host", "listen_port", "addresses", "dns", "allowed_ips", "keepalive"} <= set(peer)
    assert client.get("/api/members/vpn/ip").status_code == 200
    assert client.post("/api/members/logout", headers=h).status_code in (200, 204)


def test_the_apps_limited_session_stays_exactly_as_limited(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "scoped", paid=False)
    h = app_session(db, m)
    assert client.get(STATE, params={"platform": "android", "version": "0.1.0"}, headers=h).status_code == 200
    for path in ("/api/members/me/referrals", "/api/members/me/xp", "/api/members/leaderboard", "/api/members/feed", "/api/members/me/crew"):
        assert client.get(path, headers=h).status_code == 403, path
    assert client.post("/api/members/missions/x/claim", headers=h).status_code == 403


def test_expired_or_missing_sessions_are_refused(client: TestClient, db: Session) -> None:
    assert client.get(STATE).status_code == 401
    assert client.get(STATE, headers={"Cookie": "pc_session=kd.not-a-real-token"}).status_code == 401
    m = member_at(db, "egg", "ended", paid=False)
    h = app_session(db, m)
    assert client.post("/api/members/logout", headers=h).status_code in (200, 204)
    assert client.get(STATE, headers=h).status_code == 401


def test_an_unknown_platform_still_gets_its_account_and_no_features(client: TestClient, db: Session) -> None:
    m = member_at(db, "egg", "oldclient", paid=False)
    seen = state_of(client, app_session(db, m), "something", "x")
    assert seen["passport"] is None and not any(seen["capabilities"].values()) and "plan" in seen["vpn"]
