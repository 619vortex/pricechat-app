"""Holds the iOS app's test fixtures against the real backend, because nothing Swift can run on the Windows machine. If a route loses a field or changes its type, this fails, on a throwaway database."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.members import referrals
from app.members import service as members
from app.members import xp as ledger
from app.members.desktop_scope import DESKTOP_TOKEN_PREFIX
from app.vpn import service as vpn
from tests.test_vpn import FakeNode, add_server, fake, member_at, open_vpn  # noqa: F401

FIXTURES = Path(__file__).resolve().parents[2] / "ios-client/Packages/KiwiCore/Tests/KiwiCoreTests/Fixtures"
PUBLIC_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

pytestmark = pytest.mark.skipif(not FIXTURES.exists(), reason="ios-client isn't part of this checkout")

# Values the backend may send as null or as a number, whose type in a fixture says nothing.
FLEXIBLE = {"reason", "billing", "paid_until", "last_handshake", "paused_until", "until", "days_left", "next", "standing", "referral", "load", "progress"}


def same_shape(fixture: Any, real: Any, path: str = "") -> None:
    """Every field the app's fixture has must really exist, with the same JSON type. The real answer may have more."""
    if isinstance(fixture, dict):
        assert isinstance(real, dict), f"{path} isn't an object"
        for key, value in fixture.items():
            where = f"{path}.{key}"
            assert key in real, f"The backend has no {where}"
            if key not in FLEXIBLE or (value is not None and real[key] is not None and key not in ("load", "progress", "days_left")):
                same_shape(value, real[key], where)
    elif isinstance(fixture, list):
        assert isinstance(real, list), f"{path} isn't a list"
        if fixture and real:
            same_shape(fixture[0], real[0], f"{path}[0]")
    else:
        assert type(fixture) is type(real), f"{path} is {type(real).__name__}, and the fixture says {type(fixture).__name__}"


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def app_headers(db: Session) -> dict[str, str]:
    open_vpn(db, payments_enabled=False, trial_enabled=True)
    m = member_at(db, "egg", "iosuser", paid=False)
    vpn.start_trial(db, m)
    m.display_name = "iOS User"
    ledger.adjust(db, m, 500, actor="test", reason="test", ref="ios-1")
    referrals.program(db).referrals_enabled = True
    db.commit()
    return {"Cookie": f"pc_session={members.new_session(db, m, 90, DESKTOP_TOKEN_PREFIX, kind='desktop')}"}


def test_the_account_new_device_and_peer_fixtures_match(client: TestClient, db: Session, fake: FakeNode, app_headers: dict[str, str]) -> None:
    server = add_server(db, fake)
    made = client.post("/api/members/vpn/devices", json={"name": "iPhone abcd1234 (2)", "server_id": server.id, "public_key": PUBLIC_KEY}, headers=app_headers)
    assert made.status_code == 201
    same_shape(load("new_device"), made.json())
    device_id = made.json()["device"]["id"]
    same_shape(load("peer"), client.get(f"/api/members/vpn/devices/{device_id}/peer", headers=app_headers).json())
    same_shape(load("account"), client.get("/api/members/vpn", headers=app_headers).json())


def test_the_state_fixture_matches(client: TestClient, app_headers: dict[str, str]) -> None:
    real = client.get("/api/members/vpn/v1/state", params={"platform": "ios", "version": "0.1.0"}, headers=app_headers)
    assert real.status_code == 200
    same_shape(load("state"), real.json())
    assert real.json()["passport"]["referral"] is not None


def test_the_me_fixture_matches(client: TestClient, app_headers: dict[str, str]) -> None:
    same_shape(load("me"), client.get("/api/members/me", headers=app_headers).json())


def test_the_names_the_device_is_given_are_ones_the_backend_accepts(client: TestClient, db: Session, fake: FakeNode, app_headers: dict[str, str]) -> None:
    server = add_server(db, fake)
    longest = "iPhone abcdefgh (123456)"
    assert client.post("/api/members/vpn/devices", json={"name": longest, "server_id": server.id, "public_key": PUBLIC_KEY}, headers=app_headers).status_code == 201


def test_an_iphone_session_stays_as_limited_as_every_other_app(client: TestClient, app_headers: dict[str, str]) -> None:
    for path in ("/api/members/me/referrals", "/api/members/me/xp", "/api/members/leaderboard"):
        assert client.get(path, headers=app_headers).status_code == 403, path
