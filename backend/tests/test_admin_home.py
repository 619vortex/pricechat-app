"""Admin home: the to-do counts point at the right admin pages and count only what's waiting. No network."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.rewards import RewardClaim
from tests.test_production import PASSWORD, prod  # noqa: F401  (fixture)
from tests.test_rank_rewards import member_at


def test_home_counts_what_is_waiting(client: TestClient, db: Session) -> None:
    out = client.get("/api/admin/home").json()
    todo = {t["key"]: t for t in out["todo"]}
    assert all(t["count"] == 0 for t in todo.values())
    assert todo["signals"]["to"] == "/trading-os" and todo["vip"]["to"] == "/rank-rewards?tab=royal"
    assert set(out["status"]) >= {"kill_switch", "telegram", "x", "ai", "last_scan_at", "failing_jobs"}

    a, b = member_at(db, 3600, "royal_a"), member_at(db, 3600, "royal_b")
    db.add_all([RewardClaim(member_id=a.id, reward_key="royal", status="PENDING", rank_level=6, public_id="c-a"),
                RewardClaim(member_id=b.id, reward_key="royal", status="APPROVED", rank_level=6, public_id="c-b")])
    db.commit()
    out = client.get("/api/admin/home").json()
    todo = {t["key"]: t for t in out["todo"]}
    assert todo["vip"]["count"] == 1 and todo["knight"]["count"] == 0
    assert out["numbers"]["members"] == 2


def test_home_needs_the_owner_login(prod: TestClient) -> None:  # noqa: F811
    assert prod.get("/api/admin/home").status_code == 401
    assert prod.get("/api/admin/session").status_code == 200  # the login itself stays reachable
    assert prod.post("/api/admin/logout").status_code != 401
    assert prod.post("/api/admin/login", json={"username": "owner", "password": PASSWORD}).status_code == 200
    assert prod.get("/api/admin/home").status_code == 200  # signed in now


def test_members_list_promotes_to_worker_or_manager(client: TestClient, db: Session) -> None:
    m = member_at(db, 100, "kiwi_promo")
    pid = m.public_id
    row = next(r for r in client.get("/api/members-admin/members").json()["members"] if r["id"] == pid)
    assert row["roles"] == []
    assert client.post(f"/api/members-admin/members/{pid}/roles", json={"role": "WORKER", "grant": True}).json()["roles"] == ["WORKER"]
    assert client.post(f"/api/members-admin/members/{pid}/roles", json={"role": "MANAGER", "grant": True}).json()["roles"] == ["MANAGER", "WORKER"]
    assert client.post(f"/api/members-admin/members/{pid}/roles", json={"role": "WORKER", "grant": False}).json()["roles"] == ["MANAGER"]
    assert client.post(f"/api/members-admin/members/{pid}/roles", json={"role": "ADMIN", "grant": True}).status_code == 422
    from app.workers import service as wk
    assert wk.roles_of(db, m.id) == {"MANAGER"}  # the same roles Admin → Workers uses
