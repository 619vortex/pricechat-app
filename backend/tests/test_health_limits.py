"""Public health check for uptime monitors (GET/HEAD /api/health/public) and the light in-memory rate limits on public,
unauthenticated endpoints (referral clicks, Earn boards, the health check)."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes import health
from app.db.base import utcnow
from app.models.routing import ScheduledJob
from app.models.tradingbot import TbReferralClick
from app.security import admin_auth, rate_limit
from tests.test_production import prod  # noqa: F401 - the production app (owner login required)

UA = {"user-agent": "Mozilla/5.0 (iPhone) Safari"}


def _sched(started_ago: timedelta | None = None, dead: bool = False) -> Any:
    task = SimpleNamespace(done=lambda: dead)
    return SimpleNamespace(_loop_task=task, started_at=utcnow() - started_ago if started_ago else None, specs={"a": 1, "b": 1}, disabled={"b"})


def _get(c: TestClient, **h: str) -> Any:
    health._cache["body"] = None
    return c.get("/api/health/public", headers=h)


def test_public_health_needs_no_login_and_says_nothing_private(prod: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef1234567890")
    assert admin_auth.is_public("GET", "/api/health/public") and admin_auth.is_public("HEAD", "/api/health/public")
    assert not admin_auth.is_public("GET", "/api/health") and not admin_auth.is_public("POST", "/api/health/public")
    assert prod.get("/api/health").status_code == 401  # the detailed one stays the owner's
    r = _get(prod)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"ok", "db", "scheduler", "jobs", "backup", "version", "time"}
    assert body["ok"] is True and body["db"] == "ok" and body["scheduler"] == "off" and body["version"] == "abcdef1"
    assert prod.head("/api/health/public").status_code == 200


def test_scheduler_ok_stale_and_dead(client: TestClient, db: Session) -> None:
    rt = client.app.state.runtime  # type: ignore[attr-defined]
    rt.scheduler = _sched(timedelta(minutes=1))  # just started: grace period
    assert _get(client).json()["scheduler"] == "ok"
    rt.scheduler = _sched(timedelta(hours=2))
    db.add_all([ScheduledJob(name="a", interval_seconds=60, last_run_at=utcnow() - timedelta(hours=1)),
                ScheduledJob(name="b", interval_seconds=60, last_run_at=utcnow())])  # b is off on this server: ignored
    db.commit()
    r = _get(client)
    assert r.status_code == 503 and r.json()["ok"] is False and r.json()["scheduler"] == "stale" and r.json()["db"] == "ok"
    db.get(ScheduledJob, "a").last_run_at = utcnow() - timedelta(minutes=2)
    db.commit()
    assert _get(client).status_code == 200
    rt.scheduler = _sched(timedelta(hours=2), dead=True)  # the loop crashed
    assert _get(client).json()["scheduler"] == "stale"
    rt.scheduler = None


def test_database_down_is_503_without_the_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def __enter__(self) -> Any:
            raise RuntimeError("password=hunter2 host=db.internal")

        def __exit__(self, *a: Any) -> None: ...

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: Broken())
    r = _get(client)
    assert r.status_code == 503 and r.json()["db"] == "error" and r.json()["ok"] is False
    assert "hunter2" not in r.text and "internal" not in r.text


def test_answer_is_shared_for_a_few_seconds(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(health, "_probe", lambda s: calls.append(1) or (True, "off", {"jobs": "off", "backup": "off"}))
    _get(client)
    for _ in range(3):
        client.get("/api/health/public")
    assert len(calls) == 1


def test_health_rate_limit_429_with_retry_after(client: TestClient) -> None:
    codes = [client.get("/api/health/public", headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1"}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    r = client.get("/api/health/public", headers={"x-forwarded-for": "1.2.3.4"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert client.get("/api/health/public", headers={"x-forwarded-for": "5.6.7.8"}).status_code == 200  # another visitor


def test_earn_boards_are_limited(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routes import arbitrage

    async def board() -> dict[str, Any]:
        return {"rows": []}

    monkeypatch.setattr(arbitrage.funding, "board", board)
    h = {"x-forwarded-for": "9.9.9.9"}
    codes = [client.get("/api/members/arbitrage/funding", headers=h).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    assert client.get("/api/members/arbitrage/lunch", headers=h).status_code == 429  # one budget for both boards
    assert client.get("/api/members/arbitrage/lunch", headers={"x-forwarded-for": "8.8.8.8"}).status_code == 200


def test_referral_redirect_still_works_when_limited_but_isnt_counted(client: TestClient, db: Session) -> None:
    h = {**UA, "x-forwarded-for": "7.7.7.7"}
    for _ in range(15):
        r = client.get("/api/r/card?src=tg", headers=h, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "https://app.hyperliquid.xyz/join/BANTER"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(TbReferralClick)) == 10


def test_bucket_refills_shares_a_global_cap_and_stays_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    b = rate_limit.Bucket(burst=2, every=10, shared_burst=3, shared_every=100)
    assert b.hit("x", 0) is None and b.hit("x", 0) is None and b.hit("x", 0) == 10
    assert b.hit("x", 10) is None  # one token back after `every` seconds
    assert b.hit("y", 10) == 90  # the shared bucket (3) is spent: everyone waits
    monkeypatch.setattr(rate_limit, "MAX_KEYS", 50)
    big = rate_limit.Bucket(burst=1, every=60, shared_burst=10_000, shared_every=0.001)
    for i in range(500):
        big.hit(f"10.0.{i // 250}.{i % 250}", float(i))
    assert len(big._seen) <= 50
    assert big.hit("10.0.1.249", 500.0) is not None  # recent ones are kept (still limited)
    assert big.hit("10.0.0.0", 500.0) is None  # the oldest were forgotten
