"""Holds the Android app's unified-state fixture against the real backend, and checks that the app's own session can delete its account. Throwaway database; nothing here changes the backend."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.members import referrals
from app.members import service as members
from app.members import xp as ledger
from app.members.desktop_scope import DESKTOP_TOKEN_PREFIX
from app.models.members import Member
from app.models.vpn import VpnAccount, VpnDevice
from app.vpn import service as vpn
from tests.test_ios_contract import same_shape
from tests.test_vpn import FakeNode, add_server, fake, member_at, open_vpn  # noqa: F401

FIXTURE = Path(__file__).resolve().parents[2] / "android-client/app/src/test/resources/contract/state_android.json"
PUBLIC_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="android-client isn't part of this checkout")


def _app_session(db: Session, username: str = "androiduser") -> tuple[Member, dict[str, str]]:
    open_vpn(db, payments_enabled=False, trial_enabled=True)
    m = member_at(db, "egg", username, paid=False)
    vpn.start_trial(db, m)
    m.display_name = "Android User"
    ledger.adjust(db, m, 500, actor="test", reason="test", ref="android-1")
    referrals.program(db).referrals_enabled = True
    db.commit()
    return m, {"Cookie": f"pc_session={members.new_session(db, m, 90, DESKTOP_TOKEN_PREFIX, kind='desktop')}"}


@pytest.mark.parametrize("version", ["0.2.0", "0.2.0-debug", "0.1.0-rc1"])
def test_the_android_state_fixture_matches_and_its_features_are_on(client: TestClient, db: Session, version: str) -> None:
    _, headers = _app_session(db)
    real = client.get("/api/members/vpn/v1/state", params={"platform": "android", "version": version}, headers=headers)
    assert real.status_code == 200
    same_shape(json.loads(FIXTURE.read_text(encoding="utf-8")), real.json())
    caps = real.json()["capabilities"]
    for key in ("passport_enabled", "referrals_enabled", "auto_call_enabled", "app_routing_enabled"):
        assert caps[key] is True, key
    assert real.json()["vpn"] == client.get("/api/members/vpn", headers=headers).json()


def test_the_public_capabilities_agree_with_the_state(client: TestClient, db: Session) -> None:
    _, headers = _app_session(db)
    public = client.get("/api/members/vpn/v1/capabilities", params={"platform": "android", "version": "0.2.0"}).json()
    state = client.get("/api/members/vpn/v1/state", params={"platform": "android", "version": "0.2.0"}, headers=headers).json()
    assert public == state["capabilities"]


def test_the_apps_session_deletes_the_account_and_its_vpn_devices(client: TestClient, db: Session, fake: FakeNode) -> None:  # noqa: F811
    m, headers = _app_session(db, "deleteme")
    server = add_server(db, fake)
    made = client.post("/api/members/vpn/devices", json={"name": "Android Pixel abcd1234 (2)", "server_id": server.id, "public_key": PUBLIC_KEY}, headers=headers)
    assert made.status_code == 201
    member_id = m.id
    account_id = db.scalar(select(VpnAccount.id).where(VpnAccount.member_id == member_id))
    assert db.scalar(select(func.count()).select_from(VpnDevice).where(VpnDevice.account_id == account_id)) == 1

    wrong = client.request("DELETE", "/api/members/me", json={"confirm_username": "DeleteMe"}, headers=headers)
    assert wrong.status_code == 400  # exact match only, as the app requires
    done = client.request("DELETE", "/api/members/me", json={"confirm_username": "deleteme"}, headers=headers)
    assert done.status_code == 200 and done.json() == {"deleted": True}

    db.expire_all()
    assert db.get(Member, member_id) is None
    assert db.scalar(select(func.count()).select_from(VpnAccount).where(VpnAccount.member_id == member_id)) == 0
    assert db.scalar(select(func.count()).select_from(VpnDevice).where(VpnDevice.account_id == account_id)) == 0
    # the app's session ended with the account
    assert client.get("/api/members/vpn", headers=headers).status_code == 401
