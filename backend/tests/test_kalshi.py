"""Kalshi bot reports: the bot's own token (shown once, only its hash kept, replaced = old one refused), report kinds and
size, pruning, the admin overview (latest of each kind, history, stale), and owner-only admin routes."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.kalshi import service
from app.models.kalshi import KalshiBot, KalshiReport
from tests.test_production import prod  # noqa: F401  (fixture)

STATUS = {"env": "production", "balance_usd": 348.18, "positions": {"open": 0}, "kill_switch": {"execution_armed": False}}
DISC = {"markets": 151715, "two_sided": 91809, "with_volume_24h": 22135, "elapsed_s": 98.0, "quote_warnings": 0, "rate_limited": 0}
BOT = {"version": "abc123", "env": "production", "host": "pc"}


def _report(client: TestClient, token: str, kind: str, payload: dict) -> int:
    return client.post("/api/kalshi/bot/report", json={"kind": kind, "payload": payload, "bot": BOT},
                       headers={"Authorization": f"Bearer {token}"}).status_code


def test_bot_reports_with_its_own_token_and_the_admin_sees_the_latest(client: TestClient, db: Session) -> None:
    assert client.get("/api/kalshi/admin/overview").json()["connected"] is False
    token = client.post("/api/kalshi/admin/token").json()["token"]
    assert token.startswith("pck_") and db.get(KalshiBot, 1).token_hash == service._hash(token)  # only the hash is stored
    assert _report(client, token, "status", STATUS) == 200
    assert _report(client, token, "discovery", DISC) == 200
    assert _report(client, token, "discovery", {**DISC, "markets": 151800}) == 200
    o = client.get("/api/kalshi/admin/overview").json()
    assert o["connected"] and not o["stale"] and o["bot"] == BOT
    assert o["status"]["balance_usd"] == 348.18 and o["discovery"]["markets"] == 151800
    assert [h["markets"] for h in o["history"]] == [151715, 151800]  # oldest first, for the chart


def test_wrong_old_or_missing_tokens_are_refused(client: TestClient) -> None:
    old = client.post("/api/kalshi/admin/token").json()["token"]
    new = client.post("/api/kalshi/admin/token").json()["token"]
    assert _report(client, old, "status", STATUS) == 401  # a new token replaces the old one
    assert _report(client, "pck_" + "x" * 43, "status", STATUS) == 401
    assert client.post("/api/kalshi/bot/report", json={"kind": "status", "payload": STATUS}).status_code == 401
    assert _report(client, new, "status", STATUS) == 200
    client.delete("/api/kalshi/admin/token")
    assert _report(client, new, "status", STATUS) == 401


def test_report_kind_and_size_are_checked(client: TestClient) -> None:
    token = client.post("/api/kalshi/admin/token").json()["token"]
    assert _report(client, token, "orders", STATUS) == 400
    assert _report(client, token, "status", {"blob": "x" * (service.MAX_PAYLOAD + 10)}) == 413


def test_old_reports_are_pruned_and_a_silent_bot_is_stale(db: Session) -> None:
    token = service.new_token(db)
    bot = service.authorize(db, f"Bearer {token}")
    for i in range(service.KEEP_PER_KIND + 5):
        service.save_report(db, bot, "discovery", {"markets": i}, BOT)
    assert db.scalar(select(func.count(KalshiReport.id))) == service.KEEP_PER_KIND
    assert service.overview(db)["discovery"]["markets"] == service.KEEP_PER_KIND + 4
    later = db.get(KalshiBot, 1).last_seen_at + service.STALE_AFTER + timedelta(minutes=1)
    assert service.overview(db, now=later)["stale"] is True


def test_admin_routes_need_the_owner_login_and_the_report_route_does_not(prod: TestClient) -> None:  # noqa: F811
    for method, path in [("GET", "/api/kalshi/admin/overview"), ("POST", "/api/kalshi/admin/token"), ("DELETE", "/api/kalshi/admin/token")]:
        assert prod.request(method, path).status_code == 401, path
    # public route: reaches its own token check (401 from the bot token, not the admin login)
    r = prod.post("/api/kalshi/bot/report", json={"kind": "status", "payload": {}}, headers={"Authorization": "Bearer pck_nope"})
    assert r.status_code == 401 and "Kalshi bot token" in r.text
