"""Kiwi VPN support: members (and the Windows app's desktop session) send bug reports / feedback; secrets never get stored;
5 an hour per member; the owner lists them in Admin and marks them done."""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.members import service as members
from app.members.desktop_scope import DESKTOP_TOKEN_PREFIX, desktop_token_may
from app.models.vpn import VpnSupportReport
from app.security import admin_auth, rate_limit
from app.vpn import support
from tests.test_vpn import member_at, signed_in

URL, ADMIN = "/api/members/vpn/support", "/api/vpn/admin/support"
PRIV = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="  # WireGuard's documented example private key
PUB = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg="


@pytest.fixture(autouse=True)
def _fresh_limits() -> None:
    rate_limit.reset_for_tests()


def desktop(client: TestClient, db: Session, name: str = "desk") -> TestClient:
    m = member_at(db, "egg", name, paid=False)
    client.cookies.set("pc_session", members.new_session(db, m, 7, DESKTOP_TOKEN_PREFIX, kind="desktop"))
    return client


def test_signed_out_is_refused(client: TestClient, db: Session) -> None:
    assert client.post(URL, json={"kind": "bug", "message": "it broke"}).status_code == 401
    assert db.query(VpnSupportReport).count() == 0


def test_a_member_session_sends_feedback(client: TestClient, db: Session) -> None:
    c = signed_in(client, db, member_at(db, "egg", "webby", paid=False))
    r = c.post(URL, json={"kind": "feedback", "message": "  Love it  "})
    assert r.status_code == 201 and r.json()["ok"]
    row = db.query(VpnSupportReport).one()
    assert (row.kind, row.source, row.message, row.app_version, row.diagnostics) == ("feedback", "web", "Love it", None, None)


def test_the_desktop_app_session_may_send_and_is_marked_desktop(client: TestClient, db: Session) -> None:
    assert desktop_token_may("/api/members/vpn/support")
    c = desktop(client, db)
    r = c.post(URL, json={"kind": "bug", "message": "Failover never fired", "app_version": "2.1.0", "diagnostics": "service: running"})
    assert r.status_code == 201, r.text
    row = db.query(VpnSupportReport).one()
    assert (row.source, row.app_version, row.diagnostics) == ("desktop", "2.1.0", "service: running")


@pytest.mark.parametrize("body", [
    {"kind": "rant", "message": "x"},
    {"kind": "bug", "message": ""},
    {"kind": "bug", "message": "   "},
    {"kind": "bug", "message": "x" * 4001},
    {"kind": "bug"},
    {"kind": "bug", "message": "x", "app_version": "1" * 33},
    {"kind": "bug", "message": "x", "member_id": 1},
    {"kind": "bug", "message": "x", "diagnostics": "d" * 100_000},
    {"kind": "bug", "message": "x", "diagnostics": 12},
])
def test_validation(client: TestClient, db: Session, body: dict) -> None:
    c = signed_in(client, db, member_at(db, "egg", "valid", paid=False))
    assert c.post(URL, json=body).status_code == 422
    assert db.query(VpnSupportReport).count() == 0


def test_four_thousand_characters_is_fine_and_long_diagnostics_are_cut(client: TestClient, db: Session) -> None:
    c = signed_in(client, db, member_at(db, "egg", "long", paid=False))
    assert c.post(URL, json={"kind": "bug", "message": "m" * 4000, "diagnostics": "line\n" * 6000}).status_code == 201
    row = db.query(VpnSupportReport).one()
    assert len(row.message) == 4000 and len(row.diagnostics) <= support.MAX_DIAGNOSTICS and row.diagnostics.endswith("[cut: too long]")


def test_five_an_hour_per_member(client: TestClient, db: Session) -> None:
    c = signed_in(client, db, member_at(db, "egg", "spam", paid=False))
    for i in range(5):
        assert c.post(URL, json={"kind": "feedback", "message": f"n{i}"}).status_code == 201
    refused = c.post(URL, json={"kind": "feedback", "message": "n5"})
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) > 0
    assert db.query(VpnSupportReport).count() == 5
    other = signed_in(client, db, member_at(db, "egg", "other", paid=False))  # someone else isn't held up
    assert other.post(URL, json={"kind": "feedback", "message": "hi"}).status_code == 201


def test_keys_and_configs_are_redacted_before_storing(client: TestClient, db: Session) -> None:
    c = desktop(client, db, "leaky")
    config = f"[Interface]\nPrivateKey = {PRIV}\nAddress = 10.8.0.2/32\n[Peer]\nPublicKey = {PUB}\nPresharedKey={PRIV}\nEndpoint = 1.2.3.4:51820"
    hexkey = "ab" * 32
    r = c.post(URL, json={"kind": "bug", "message": f"my key {PRIV} broke", "app_version": "2.1.0",
                          "diagnostics": {"config": config, "private_key": "secret!", "api_token": "t", "note": f"0x{hexkey}", "ok": "service running"}})
    assert r.status_code == 201
    row = db.query(VpnSupportReport).one()
    stored = row.message + row.diagnostics
    for secret in (PRIV, PUB, "secret!", hexkey, "PrivateKey", "PresharedKey"):
        assert secret not in stored, secret
    assert "service running" in row.diagnostics and "[Interface]" in row.diagnostics and "my key [redacted] broke" == row.message


def test_redact_keeps_ordinary_text() -> None:
    text = "Connected to Frankfurt in 812 ms; handshake 3 s ago; version 2.1.0"
    assert support.redact(text) == text
    assert support.redact(f"PrivateKey = {PRIV}") == support.REDACTED


def test_owner_alert_is_logged_without_the_message(client: TestClient, db: Session, caplog: pytest.LogCaptureFixture) -> None:
    c = signed_in(client, db, member_at(db, "egg", "alerty", paid=False))
    with caplog.at_level(logging.WARNING, logger="app.alerts"):
        assert c.post(URL, json={"kind": "bug", "message": "very private words", "app_version": "2.1.0"}).status_code == 201
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "new bug report from alerty (app 2.1.0)" in logged and "very private words" not in logged


def test_admin_lists_newest_first_and_marks_done(client: TestClient, db: Session) -> None:
    c = signed_in(client, db, member_at(db, "egg", "lister", paid=False))
    c.post(URL, json={"kind": "bug", "message": "first", "diagnostics": "diag"})
    c.post(URL, json={"kind": "feedback", "message": "second", "app_version": "2.1.0"})
    c.cookies.clear()
    items = client.get(ADMIN).json()["items"]
    assert [i["message"] for i in items] == ["second", "first"]
    assert items[0]["member"] == "lister" and items[0]["app_version"] == "2.1.0" and items[1]["diagnostics"] == "diag" and not items[0]["done"]
    done = client.post(f"{ADMIN}/{items[1]['id']}/done", json={"done": True})
    assert done.status_code == 200 and done.json()["done"] and done.json()["done_at"]
    assert client.post(f"{ADMIN}/{items[1]['id']}/done", json={"done": False}).json()["done"] is False
    assert client.post(f"{ADMIN}/999999/done", json={"done": True}).status_code == 404


def test_admin_routes_are_owner_only_and_the_app_cant_reach_them(client: TestClient, db: Session) -> None:
    assert not admin_auth.is_public("GET", ADMIN) and not admin_auth.is_public("POST", f"{ADMIN}/1/done")
    assert admin_auth.is_public("POST", URL)
    c = desktop(client, db, "nosy")
    refused = c.get(ADMIN)
    assert refused.status_code == 403 and refused.json()["code"] == "desktop_scope"
