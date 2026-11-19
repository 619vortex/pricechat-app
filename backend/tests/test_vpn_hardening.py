"""Kiwi VPN hardening (docs/hardening/TRIAL-AND-LIMITS.md): security audit M1 (check first, then charge), M4 (trial abuse),
L6 (one PC = one slot, opt-in), L7 (sync limits, opt-in), L8 (sign out removes the app's devices), L9 (reviewer by id), I4."""

import datetime as dt
import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.db.migrate import alembic_config, upgrade_database
from app.db.session import build_engine
from app.members import service as members
from app.members import xp as ledger
from app.members.desktop_scope import DESKTOP_TOKEN_PREFIX
from app.models.members import Member
from app.models.vpn import VpnDevice, VpnTrialClaim, VpnTrialSignal
from app.security import client_ip
from app.security.rate_limit import LIMITS
from app.vpn import billing, reviewer, trial_guard
from app.vpn import service as vpn
from tests.test_vpn import FakeNode, add_server, fake, member_at, open_vpn, signed_in  # noqa: F401

SECRET = "edge-secret-for-tests-0123456789"
TRIAL = "/api/members/vpn/trial"


def trial_on(db: Session, **over: Any) -> None:
    """The trial on, with the guards enforced unless a test says otherwise (the default mode is "monitor")."""
    open_vpn(db, payments_enabled=False, trial_enabled=True, **{"trial_guard_mode": "enforce", **over})


def fresh(db: Session, name: str, email: str | None = None, age_days: float = 30) -> Member:
    m = member_at(db, "egg", name, paid=False)
    m.created_at = utcnow() - dt.timedelta(days=age_days)
    if email:
        m.email, m.email_verified_at = email, utcnow()
    db.commit()
    return m


def link(db: Session, m: Member, platform: str, uid: str) -> None:
    members.link_identity(db, m, platform, uid, None, None)
    db.commit()


def as_member(client: TestClient, db: Session, m: Member) -> TestClient:
    client.cookies.clear()
    return signed_in(client, db, m)


@pytest.fixture
def edge(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured PROXY_SHARED_SECRET and one trusted hop: a direct caller's X-Forwarded-For entry is its real address."""
    s = get_settings().model_copy(update={"proxy_shared_secret": SecretStr(SECRET), "trusted_proxy_hops": 1})
    monkeypatch.setattr(client_ip, "_settings", lambda request: s)


def direct(ip: str) -> dict[str, str]:
    return {"x-forwarded-for": ip}


def website_egress(ip: str) -> dict[str, str]:
    """Through Vercel without the shared secret: the address is Vercel's shared egress, not the visitor's."""
    return {"x-forwarded-for": ip, "x-vercel-id": "fra1::abc"}


def website_visitor(ip: str) -> dict[str, str]:
    return {"x-forwarded-for": f"{ip}, 76.76.21.1", client_ip.EDGE_HEADER: SECRET}


def untouched(rule: str) -> bool:
    """Nobody spent a token from this rule's shared site-wide bucket."""
    return "*" not in LIMITS[rule]._seen


# --- M1: check first, then charge -------------------------------------------------------------------------------------


def test_refused_devices_spend_no_tokens_not_even_the_shared_ones(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db)  # paid only: a free Passport without time can't add a device
    c = signed_in(client, db, member_at(db, "egg", "freeloader", paid=False))
    tries = int(LIMITS["vpn_device"].burst) * 3
    codes = [c.post("/api/members/vpn/devices", json={"name": f"d{i}", "server_id": server.id}).status_code for i in range(tries)]
    assert codes == [403] * tries
    assert untouched("vpn_device") and not LIMITS["vpn_device"]._seen


def test_refused_browser_logins_and_trials_spend_no_tokens(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db)
    c = signed_in(client, db, member_at(db, "egg", "nobody", paid=False))
    n = int(LIMITS["vpn_browser"].burst) * 3
    assert [c.post("/api/members/vpn/browser", json={"server_id": server.id}).status_code for _ in range(n)] == [403] * n
    assert [c.post(TRIAL).status_code for _ in range(n)] == [403] * n  # the trial is off here
    assert untouched("vpn_browser") and untouched("vpn_trial")


def test_new_anonymous_keys_while_payments_are_off_spend_nothing(client: TestClient, db: Session) -> None:
    open_vpn(db, payments_enabled=False)
    n = int(LIMITS["vpn_new"].burst) * 3
    assert [client.post("/api/members/vpn/anon").status_code for _ in range(n)] == [403] * n
    assert untouched("vpn_new") and not LIMITS["vpn_new"]._seen


def _anon(db: Session) -> dict[str, str]:
    acct, key = billing.new_anonymous(db)
    acct.paid_until = utcnow() + dt.timedelta(days=30)
    db.commit()
    return {"x-kiwi-key": key}


def test_anonymous_devices_are_also_limited_per_real_address(client: TestClient, db: Session, fake: FakeNode, edge: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=10, trial_guard_mode="enforce")  # only when enforcing: monitor mode adds no limit main doesn't have
    per_ip = int(LIMITS["vpn_anon_ip"].burst)
    codes = []
    for k in range(3):  # three keys from one address: each key's own bucket (5) isn't the limit, the address is
        head = {**_anon(db), **direct("198.51.100.7")}
        codes += [client.post("/api/members/vpn/anon/devices", json={"name": f"k{k} {i}", "server_id": server.id}, headers=head).status_code for i in range(5)]
    assert codes == [201] * per_ip + [429] * (15 - per_ip)


def test_monitor_mode_adds_no_per_address_device_limit(client: TestClient, db: Session, fake: FakeNode, edge: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=10)
    codes = []
    for k in range(3):
        head = {**_anon(db), **direct("198.51.100.7")}
        codes += [client.post("/api/members/vpn/anon/devices", json={"name": f"k{k} {i}", "server_id": server.id}, headers=head).status_code for i in range(5)]
    assert codes == [201] * 15


def test_website_visitors_on_shared_egress_are_not_limited_together(client: TestClient, db: Session, fake: FakeNode, edge: None) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=10)
    codes = []
    for k in range(3):
        head = {**_anon(db), **website_egress("76.76.21.9")}
        codes += [client.post("/api/members/vpn/anon/devices", json={"name": f"k{k} {i}", "server_id": server.id}, headers=head).status_code for i in range(5)]
    assert codes == [201] * 15


# --- M4: one trial per verified identity ------------------------------------------------------------------------------


def test_email_normalisation() -> None:
    n = trial_guard.normalise_email
    assert n(" A.B+vpn@GoogleMail.com ") == n("ab@gmail.com") == "ab@gmail.com"
    assert n("a.b+x@example.com") == "a.b@example.com" and n("nobody") == "nobody"


def test_a_new_passport_with_the_same_email_gets_no_second_trial(client: TestClient, db: Session) -> None:
    trial_on(db)
    first = fresh(db, "first", "kiwi.lover@gmail.com")
    assert as_member(client, db, first).post(TRIAL).status_code == 200
    assert db.scalar(select(VpnTrialClaim.id)) is not None
    members.delete_member(db, first)  # deleting the account doesn't free the identity
    again = fresh(db, "again", "kiwilover+2@googlemail.com")  # the same Gmail mailbox
    c = as_member(client, db, again)
    me = c.get("/api/members/vpn").json()
    assert me["trial"]["available"] is False and me["trial"]["code"] == "identity_used" and "email address" in me["trial"]["reason"]
    assert me["reason"] == me["trial"]["reason"]  # what the apps show instead of "start your free trial"
    r = c.post(TRIAL)
    assert r.status_code == 403 and r.headers["X-Kiwi-Reason"] == "identity_used" and "one free trial per person" in r.json()["detail"]
    other = fresh(db, "other", "someone.else@gmail.com")
    assert as_member(client, db, other).post(TRIAL).status_code == 200


def test_linked_accounts_carry_the_trial_including_ones_linked_later(client: TestClient, db: Session) -> None:
    trial_on(db)
    a = fresh(db, "alpha", "alpha@example.com")
    link(db, a, "google", "g-1")
    assert as_member(client, db, a).post(TRIAL).status_code == 200
    link(db, a, "telegram", "777")  # linked after the trial started: remembered too
    members.delete_member(db, a)
    for platform, uid, label in (("google", "g-1", "Google account"), ("telegram", "777", "Telegram account")):
        b = fresh(db, f"b-{platform}")
        link(db, b, platform, uid)
        r = as_member(client, db, b).post(TRIAL)
        assert r.status_code == 403 and label in r.json()["detail"], r.text


def test_an_email_added_after_the_trial_is_remembered(db: Session) -> None:
    trial_on(db)
    a = fresh(db, "late-email")
    link(db, a, "x", "x-9")
    vpn.start_trial(db, a)
    a.email = "late@example.com"
    db.commit()
    trial_guard.mark_if_trial_used(db, a)  # what email_login does when an address is added
    db.commit()
    b = fresh(db, "late-copy")
    members.delete_member(db, a)
    b.email = "late@example.com"
    db.commit()
    assert vpn.trial_unavailable(db, vpn.account_of(db, b), vpn.settings(db))[0] == "identity_used"


def test_identity_rule_can_be_switched_off(db: Session) -> None:
    trial_on(db)
    a = fresh(db, "one", "same@example.com")
    vpn.start_trial(db, a)
    members.delete_member(db, a)
    b = fresh(db, "two", "same@example.com")
    assert vpn.trial_unavailable(db, vpn.account_of(db, b), vpn.settings(db))[0] == "identity_used"
    vpn.update_settings(db, {"trial_one_per_identity": False})
    assert vpn.trial_unavailable(db, vpn.account_of(db, b), vpn.settings(db)) is None


def test_minimum_account_age(client: TestClient, db: Session) -> None:
    trial_on(db)
    newbie = fresh(db, "newbie", age_days=0.25)  # 6 hours old; the default is 24
    c = as_member(client, db, newbie)
    r = c.post(TRIAL)
    assert r.status_code == 403 and r.headers["X-Kiwi-Reason"] == "account_too_new" and "about 18 hours" in r.json()["detail"]
    assert untouched("vpn_trial")
    vpn.update_settings(db, {"trial_min_account_age_hours": 6})
    assert c.post(TRIAL).status_code == 200


def test_minimum_xp(client: TestClient, db: Session) -> None:
    trial_on(db, trial_min_xp=100)
    m = fresh(db, "quiet")
    c = as_member(client, db, m)
    r = c.post(TRIAL)
    assert r.status_code == 403 and r.headers["X-Kiwi-Reason"] == "needs_xp" and "need 100 XP" in r.json()["detail"]
    ledger.adjust(db, m, 150, actor="test", reason="active", ref="xp-quiet")
    assert c.post(TRIAL).status_code == 200


def test_trial_starts_are_throttled_per_real_address_and_network(client: TestClient, db: Session, edge: None) -> None:
    trial_on(db, trial_per_ip_day=1, trial_per_network_day=2)
    one, two, three, four = (fresh(db, f"net{i}") for i in range(4))
    assert as_member(client, db, one).post(TRIAL, headers=direct("203.0.113.5")).status_code == 200
    r = as_member(client, db, two).post(TRIAL, headers=direct("203.0.113.5"))
    assert r.status_code == 429 and r.headers["X-Kiwi-Reason"] == "network_limit" and int(r.headers["Retry-After"]) > 3600
    assert as_member(client, db, two).post(TRIAL, headers=website_visitor("203.0.113.6")).status_code == 200  # same /24, other address
    assert as_member(client, db, three).post(TRIAL, headers=direct("203.0.113.7")).status_code == 429  # the /24 is at 2
    assert as_member(client, db, three).post(TRIAL, headers=direct("192.0.2.1")).status_code == 200
    # Vercel's shared egress is never counted: the whole website would share one address
    assert as_member(client, db, four).post(TRIAL, headers=website_egress("203.0.113.5")).status_code == 200


def test_no_address_throttle_without_a_proxy_secret(client: TestClient, db: Session) -> None:
    trial_on(db, trial_per_ip_day=1)
    a, b = fresh(db, "nosecret-a"), fresh(db, "nosecret-b")
    assert as_member(client, db, a).post(TRIAL, headers=direct("203.0.113.5")).status_code == 200
    assert as_member(client, db, b).post(TRIAL, headers=direct("203.0.113.5")).status_code == 200  # forgeable address: not used


def test_existing_trials_and_access_are_untouched(db: Session, fake: FakeNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    trial_on(db, trial_one_per_identity=False, trial_min_account_age_hours=0)
    old = fresh(db, "veteran", "vet@example.com", age_days=0)
    vpn.start_trial(db, old)
    vpn.create_device(db, old, "laptop", server.id)
    vpn.update_settings(db, {"trial_one_per_identity": True, "trial_min_account_age_hours": 24 * 30, "trial_min_xp": 10_000})
    acct = vpn.account_of(db, old)
    assert vpn.access(db, acct) == "active" and vpn.eligibility(db, acct)[0] is True
    vpn.sync_all(db)
    assert len(fake.peers) == 1


# --- guard modes: off / monitor (the default) / enforce ----------------------------------------------------------------


def test_the_default_mode_is_monitor() -> None:
    assert vpn.SETTINGS_DEFAULTS["trial_guard_mode"] == "monitor"


def test_monitor_mode_never_refuses_but_records_what_enforce_would_have(client: TestClient, db: Session, edge: None) -> None:
    open_vpn(db, payments_enabled=False, trial_enabled=True, trial_per_ip_day=1, trial_min_xp=100)
    assert vpn.settings(db).trial_guard_mode == "monitor"
    first = fresh(db, "first", "dup@example.com")
    assert as_member(client, db, first).post(TRIAL, headers=direct("203.0.113.5")).status_code == 200
    members.delete_member(db, first)
    twin = fresh(db, "twin", "dup@example.com", age_days=0.1)  # same email, 2 hours old, no XP, same address
    c = as_member(client, db, twin)
    me = c.get("/api/members/vpn").json()
    assert me["trial"] == {"available": True, "days": 30, "code": None, "reason": None} and me["reason"] == vpn.START_TRIAL
    assert c.post(TRIAL, headers=direct("203.0.113.5")).status_code == 200  # enforce would have refused it four ways
    rows = db.scalars(select(VpnTrialSignal).order_by(VpnTrialSignal.id)).all()
    assert [(r.code, r.member_id) for r in rows[:1]] == [("needs_xp", None)]  # the first start: its member deleted since (SET NULL)
    rows = rows[1:]
    by_code = {r.code: r for r in rows}
    assert set(by_code) == {"needs_xp", "identity_used", "account_too_new", "ip_limit"} and all(r.member_id == twin.id for r in rows)
    assert by_code["ip_limit"].net_hash == trial_guard.network_hash("203.0.113.5") and "203.0.113" not in by_code["ip_limit"].net_hash
    assert by_code["identity_used"].net_hash is None
    assert trial_guard.used_identity(db, twin) == "email address"  # claims still written: enforcing later works
    sig = client.get("/api/vpn/admin/overview").json()["trial_signals"]
    assert sig["counts"]["identity_used"] == {"7d": 1, "30d": 1} and sig["counts"]["needs_xp"]["30d"] == 2
    assert sig["recent"][0]["member_id"] == twin.id and sig["retention_days"] == 30
    assert untouched("vpn_trial") is False  # successful starts are charged as on main


def test_monitor_rows_expire(db: Session) -> None:
    from app.models.vpn import VpnTrialSignal as Row
    old = Row(member_id=None, code="needs_xp", created_at=utcnow() - dt.timedelta(days=31))
    db.add(old)
    db.commit()
    trial_guard.record_signals(db, None, ["account_too_new"], None)  # type: ignore[arg-type]
    db.commit()
    assert [r.code for r in db.scalars(select(Row))] == ["account_too_new"]


def test_off_mode_evaluates_nothing_but_still_remembers_trials(client: TestClient, db: Session, edge: None) -> None:
    open_vpn(db, payments_enabled=False, trial_enabled=True, trial_guard_mode="off", trial_per_ip_day=1)
    a = fresh(db, "off-a", "same@example.com", age_days=0)
    assert as_member(client, db, a).post(TRIAL, headers=direct("203.0.113.5")).status_code == 200
    members.delete_member(db, a)
    b = fresh(db, "off-b", "same@example.com", age_days=0)
    assert as_member(client, db, b).post(TRIAL, headers=direct("203.0.113.5")).status_code == 200
    assert db.scalar(select(VpnTrialSignal.id)) is None and trial_guard.used_identity(db, b) == "email address"
    vpn.update_settings(db, {"trial_guard_mode": "enforce"})
    members.delete_member(db, b)
    c = fresh(db, "off-c", "same@example.com")
    assert as_member(client, db, c).post(TRIAL, headers=direct("198.51.100.1")).headers["X-Kiwi-Reason"] == "identity_used"


def test_trial_guard_settings_are_validated(client: TestClient, db: Session) -> None:
    assert client.put("/api/vpn/admin/settings", json={"trial_min_account_age_hours": -1}).status_code == 422
    assert client.put("/api/vpn/admin/settings", json={"trial_per_ip_day": 1001}).status_code == 422
    assert client.put("/api/vpn/admin/settings", json={"trial_guard_mode": "strict"}).status_code == 422
    assert client.put("/api/vpn/admin/settings", json={"trial_guard_mode": "enforce"}).json()["trial_guard_mode"] == "enforce"
    out = client.put("/api/vpn/admin/settings", json={"trial_min_xp": 50, "trial_per_network_day": 0, "trial_one_per_identity": False}).json()
    assert out["trial_min_xp"] == 50 and out["trial_per_network_day"] == 0 and out["trial_one_per_identity"] is False
    assert out["payments_enabled"] is False  # untouched


def test_migration_remembers_trials_started_before_it(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    engine = build_engine(url, enforce_foreign_keys=False)
    with engine.begin() as conn:
        config = alembic_config()
        config.attributes["connection"] = conn
        command.upgrade(config, "0114_trending_topics")  # production's revision before this release
        now = utcnow().isoformat()

        def insert(table: str, **values: Any) -> None:
            cols = {c["name"]: c for c in inspect(conn).get_columns(table)}
            for name, c in cols.items():
                if name not in values and not c["nullable"] and c.get("default") is None and name != "id":
                    t = str(c["type"]).upper()
                    values[name] = 0 if ("INT" in t or "BOOL" in t) else now if ("DATE" in t or "TIME" in t) else "{}" if "JSON" in t else f"x{name}-{uuid.uuid4().hex[:8]}"
            conn.execute(text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"), values)

        insert("members", id=1, email="Old.Timer+vpn@gmail.com")
        insert("members", id=2, email="never@example.com")
        insert("linked_identities", member_id=1, platform="telegram", platform_user_id="4242", verified_at=now)
        insert("vpn_accounts", member_id=1, trial_started_at=now)
        insert("vpn_accounts", member_id=2)
        insert("member_audit_logs", member_id=2, actor="member", action="vpn_reviewer_created", details="{}")
        insert("vpn_settings", id=1)
    engine.dispose()
    upgrade_database(url)
    check = build_engine(url)
    with check.connect() as conn:
        claims = set(conn.execute(text("SELECT claim_hash FROM vpn_trial_claims")).scalars())
        reviewer_id = conn.execute(text("SELECT reviewer_member_id FROM vpn_settings WHERE id = 1")).scalar()
    check.dispose()
    assert claims == {trial_guard.claim_hash("email:oldtimer@gmail.com"), trial_guard.claim_hash("telegram:4242")}
    assert reviewer_id == 2



def test_kiwi_migrations_follow_productions_trending_revision(tmp_path: Path) -> None:
    """Production ran 0114_trending_topics (based on 0111) before this release: the Kiwi VPN migrations must come after it, so a
    database at 0114 gets only them, and downgrading back to 0114 keeps Trending Topics' tables and rows. Never rechain 0114."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == ["0117_nd_validation"]  # 0117: News Desk validation records (create-only)
    assert script.get_revision("0117_nd_validation").down_revision == "0116_vpn_trial_guards"
    assert script.get_revision("0114_trending_topics").down_revision == "0111_vpn_server_traffic_allowance"
    assert script.get_revision("0115_vpn_server_checks").down_revision == "0114_trending_topics"
    assert script.get_revision("0116_vpn_trial_guards").down_revision == "0115_vpn_server_checks"
    url = f"sqlite:///{(tmp_path / 'prod.db').as_posix()}"
    engine = build_engine(url, enforce_foreign_keys=False)

    def tables() -> set[str]:
        with engine.connect() as conn:
            return set(inspect(conn).get_table_names())

    def to(target: str, down: bool = False) -> None:
        with engine.begin() as conn:
            config = alembic_config()
            config.attributes["connection"] = conn
            (command.downgrade if down else command.upgrade)(config, target)

    kiwi = {"vpn_server_checks", "vpn_trial_claims", "vpn_trial_signals"}
    to("0114_trending_topics")
    before = tables()
    assert "tt_settings" in before and not kiwi & before
    now = utcnow().isoformat()
    with engine.begin() as conn:
        cols = [c for c in inspect(conn).get_columns("tt_settings") if not c["nullable"] and c.get("default") is None and c["name"] != "id"]
        values = {c["name"]: (now if "DATE" in str(c["type"]).upper() else 0) for c in cols}
        conn.execute(text(f"INSERT INTO tt_settings (id{''.join(', ' + k for k in values)}) VALUES (1{''.join(', :' + k for k in values)})"), values)
    to("head")
    assert tables() - before == kiwi | {"nd_validation"}  # 0117 (News Desk validation) follows the Kiwi migrations
    to("0114_trending_topics", down=True)
    assert tables() == before
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM tt_settings")).scalar() == 1
    to("head")
    assert kiwi <= tables()
    engine.dispose()

# --- L6: one PC, one slot (opt-in) -----------------------------------------------------------------------------------


def test_one_pc_on_several_servers_uses_one_slot_only_when_switched_on(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    a, b = add_server(db, fake, "Frankfurt"), add_server(db, fake, "Helsinki")
    open_vpn(db, max_devices=1)
    c = signed_in(client, db, member_at(db, "warrior", "roamer"))
    add = lambda name, sid: c.post("/api/members/vpn/devices", json={"name": name, "server_id": sid}).status_code  # noqa: E731
    assert add(f"DESKTOP-1 ({a.id})", a.id) == 201
    assert add(f"DESKTOP-1 ({b.id})", b.id) == 409  # off (the default): one slot per server
    vpn.update_settings(db, {"count_pc_once": True})
    assert add(f"DESKTOP-1 ({b.id})", b.id) == 201  # the same PC on another server
    assert add(f"DESKTOP-1 ({a.id})", a.id) == 409  # a second device of that PC on the same server is a second device
    assert add(f"DESKTOP-2 ({a.id})", a.id) == 409  # another PC
    assert add(f"DESKTOP-1 ({a.id + 50})", b.id) == 409  # the number must be the device's own server
    assert add("phone", a.id) == 409


def test_slot_counting() -> None:
    used = vpn.slots_used
    pcs = [(1, "PC (1)", 1), (2, "PC (2)", 2), (3, "PC (3)", 3)]
    assert used(pcs, False) == 3 and used(pcs, True) == 1
    assert used(pcs + [(4, "PC (1)", 1)], True) == 2 and used(pcs + [(5, "phone", 1), (6, "phone", 1)], True) == 3


# --- L7: sync limits (opt-in, never deletes) ---------------------------------------------------------------------------


def test_sync_keeps_devices_over_a_lowered_limit_unless_switched_on(db: Session, fake: FakeNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=3)
    m = member_at(db, "warrior", "many")
    devs = [vpn.create_device(db, m, f"d{i}", server.id)[0] for i in range(3)]
    keys_ = [d.public_key for d in devs]
    vpn.update_settings(db, {"max_devices": 1})
    vpn.sync_all(db)
    assert set(fake.peers) == set(keys_)  # the default: nothing changes
    vpn.update_settings(db, {"sync_enforce_limits": True})
    out = vpn.sync_all(db)
    assert set(fake.peers) == {keys_[0]} and out["suspended"] == 2 and out["revoked"] == 0  # the oldest keeps its slot
    assert db.scalar(select(text("count(*)")).select_from(VpnDevice)) == 3  # rows kept
    vpn.update_settings(db, {"max_devices": 3})
    vpn.sync_all(db)
    assert set(fake.peers) == set(keys_)  # back on


def test_sync_switches_off_non_testers_on_a_testers_only_server_only_when_switched_on(db: Session, fake: FakeNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db)
    public, tester = member_at(db, "warrior", "public"), member_at(db, "warrior", "tester")
    vpn.set_tester(db, "tester", 10)
    k_public = vpn.create_device(db, public, "p", server.id)[0].public_key
    k_tester = vpn.create_device(db, tester, "t", server.id)[0].public_key
    vpn.update_server(db, server.id, {"testers_only": True})
    vpn.sync_all(db)
    assert set(fake.peers) == {k_public, k_tester}  # "testers_only never removes existing devices" still holds by default
    vpn.update_settings(db, {"sync_enforce_limits": True})
    vpn.sync_all(db)
    assert set(fake.peers) == {k_tester}
    assert db.scalar(select(VpnDevice.id).where(VpnDevice.public_key == k_public)) is not None
    vpn.update_server(db, server.id, {"testers_only": False})
    vpn.sync_all(db)
    assert set(fake.peers) == {k_public, k_tester}


# --- L8: signing the app out everywhere can take its devices ------------------------------------------------------------


def test_signing_the_app_out_everywhere_can_remove_its_devices(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    server = add_server(db, fake)
    open_vpn(db, max_devices=5)
    m = member_at(db, "warrior", "pc-owner")
    app = members.new_session(db, m, 90, DESKTOP_TOKEN_PREFIX, kind="desktop")
    client.cookies.set("pc_session", app)
    assert client.post("/api/members/vpn/devices", json={"name": "Work laptop", "server_id": server.id}).status_code == 201
    signed_in(client, db, m)  # the website
    assert client.post("/api/members/vpn/devices", json={"name": "phone", "server_id": server.id}).status_code == 201
    legacy = vpn.create_device(db, m, f"OLD-PC ({server.id})", server.id)[0]  # made before created_via existed
    legacy.created_via = None
    db.commit()
    assert {d.created_via for d in db.scalars(select(VpnDevice))} == {"app", "web", None}
    assert client.post("/api/members/desktop/sessions/revoke").json() == {"removed": 1, "devices_removed": 0}
    assert len(fake.peers) == 3
    out = client.post("/api/members/desktop/sessions/revoke", json={"remove_devices": True}).json()
    assert out == {"removed": 0, "devices_removed": 2}
    assert [d.name for d in db.scalars(select(VpnDevice))] == ["phone"] and len(fake.peers) == 1


# --- L9: the reviewer is a stored member, not an email ---------------------------------------------------------------


REVIEW_EMAIL, REVIEW_PASSWORD = "reviewer@example.com", "a-long-random-test-password"


@pytest.fixture
def review(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "vpn_reviewer_email", REVIEW_EMAIL)
    monkeypatch.setattr(s, "vpn_reviewer_password_hash", SecretStr(reviewer.hash_password(REVIEW_PASSWORD)))
    monkeypatch.setattr(s, "vpn_reviewer_until", (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)).isoformat())


def test_the_reviewer_is_bound_by_member_id_and_its_trial_is_used(client: TestClient, db: Session, review: None) -> None:
    assert client.post("/api/members/auth/reviewer", json={"email": REVIEW_EMAIL, "password": REVIEW_PASSWORD}).status_code == 200
    bound = vpn.settings(db).reviewer_member_id
    rev = db.get(Member, bound)
    assert rev is not None and rev.email == REVIEW_EMAIL and reviewer.is_reviewer(db, rev)
    acct = vpn.account_of(db, rev)
    assert acct.trial_started_at is not None and vpn.access(db, acct) == "active"  # the review rights, not a trial
    assert trial_guard.used_identity(db, rev) == "email address"
    rev.email = "changed@example.com"  # its email no longer matters
    db.commit()
    assert reviewer.is_reviewer(db, rev)
    other = fresh(db, "impostor")
    other.email = REVIEW_EMAIL
    db.commit()
    assert not reviewer.is_reviewer(db, other)


def test_a_passport_already_holding_the_review_address_never_becomes_the_reviewer(client: TestClient, db: Session, review: None) -> None:
    person = fresh(db, "person", REVIEW_EMAIL)
    assert client.post("/api/members/auth/reviewer", json={"email": REVIEW_EMAIL, "password": REVIEW_PASSWORD}).status_code == 401
    assert vpn.settings(db).reviewer_member_id is None and not reviewer.is_reviewer(db, person)


# --- I4: nested JSON in the payment webhook ---------------------------------------------------------------------------


def test_deeply_nested_ipn_is_a_bad_request_not_a_crash(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "nowpayments_ipn_secret", SecretStr("ipn-test-secret"), raising=False)
    body = ("[" * 100_000 + "]" * 100_000).encode()
    sig = hmac.new(b"ipn-test-secret", body, hashlib.sha512).hexdigest()
    r = client.post("/api/members/vpn/webhooks/nowpayments", content=body, headers={"x-nowpayments-sig": sig})
    assert r.status_code == 400
    flat = json.dumps({"order_id": "nope"}).encode()
    assert client.post("/api/members/vpn/webhooks/nowpayments", content=flat, headers={"x-nowpayments-sig": "0"}).status_code == 401
