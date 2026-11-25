"""Admin's Intelligence (shadow) page: GET /api/intel/dashboard (app.intel.dashboard). Read-only: it never enqueues a job, calls a model or
writes a row; with intel off and empty tables every section is there with zeros. Also the PC's availability record (channel.accrue).
All rows here are TEST FIXTURES (made-up headlines, models and spend)."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.intel import anthropic_client, channel, dashboard
from app.intel import router as intel_router
from app.models.intel import AiUsage, IntelJob, IntelWorker
from app.models.intel_shadow import IntelShadow
from app.security import admin_auth

URL = "/api/intel/dashboard"


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """The page must never enqueue or call a model: both blow up if it tries."""

    def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("the dashboard must not enqueue or call a model")

    monkeypatch.setattr(intel_router, "enqueue", boom)
    monkeypatch.setattr(intel_router, "run", boom)
    monkeypatch.setattr(anthropic_client, "analyze", boom)


def _counts(db: Session) -> tuple[int, int, int, int]:
    return (db.query(IntelJob).count(), db.query(IntelShadow).count(), db.query(AiUsage).count(), db.query(IntelWorker).count())


def test_empty_state_with_intel_off(client: TestClient, db: Session) -> None:
    s = get_settings()
    assert s.intel_enabled is False and s.intel_shadow_enabled is False
    r = client.get(URL)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["mode"] == "shadow" and "nothing is published by AI" in b["banner"] and b["hours"] == 24
    assert b["switches"]["intel_enabled"] is False and b["switches"]["intel_shadow_enabled"] is False and b["switches"]["pc_online"] is False
    assert b["decisions"]["total"] == 0 and b["decisions"]["recent"] == [] and set(b["decisions"]["by_decider"].values()) == {0}
    assert b["disagreements"]["agreement_rate"] is None and b["disagreements"]["lane_yes_ai_no"] == [] and b["disagreements"]["lane_no_ai_yes"] == []
    assert b["latency"]["groups"] == [] and b["latency"]["jobs"]["total"] == 0 and b["workers"] == []
    assert b["missed"]["possible_misses"] == [] and b["missed"]["possible_false_positives"] == []
    sp = b["spend"]
    assert sp["today"]["spent_usd"] == 0 and sp["cap_usd"] == s.intel_daily_usd_cap and sp["per_model"] == [] and sp["refused_over_cap"] == 0
    assert all(d["usd"] == 0 for d in sp["per_day"]) and len(sp["per_day"]) in (1, 2)
    assert _counts(db) == (0, 0, 0, 0)  # nothing written


def test_window_bounds(client: TestClient) -> None:
    assert client.get(URL, params={"hours": 0}).status_code == 422
    assert client.get(URL, params={"hours": 24 * 14 + 1}).status_code == 422
    b = client.get(URL, params={"hours": 24 * 14}).json()
    assert b["hours"] == 336 and len(b["spend"]["per_day"]) in (15, 16)


def test_admin_login_required(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not admin_auth.is_public("GET", URL)
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(admin_auth.hash_password("pw")))
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={"Authorization": "Bearer pci_" + "t" * 43}).status_code == 401  # the PC's token is not the owner


# --- aggregation -----------------------------------------------------------------------------------------------------------------


def _job(db: Session, now: datetime, *, by: str | None, model: str = "", latency: int | None = None, status: str = "done",
         claimed: bool = False, error: str | None = None, seconds: float | None = None, age_h: float = 1.0) -> IntelJob:
    result = None if by in (None, "rules") else {"output": {}, "model": model, **({"seconds": seconds} if seconds is not None else {})}
    j = IntelJob(kind="intel.analyze", priority="bulk", route="cloud" if by == "cloud" else "pc", shadow=False, key=None, status=status,
                 payload={"title": "x"}, result=result, result_by=by, latency_ms=latency, error=error, attempts=1, max_attempts=2,
                 claimed_at=now - timedelta(hours=age_h) if claimed else None, created_at=now - timedelta(hours=age_h))
    db.add(j)
    db.commit()
    return j


def _row(db: Session, now: datetime, ref: str, title: str, *, rules_ok: bool, semantic_ok: bool, tier: str, state: str,
         job: IntelJob | None = None, model: str = "", veto: list[str] | None = None, age_h: float = 1.0) -> IntelShadow:
    v = {"state": state, "job_id": job.id if job else None, "model": model, "source": f"model:{model}" if state == "done" else "rules",
         "reasons": ["fixture reason"], "hard_veto": veto or [], "event_type": "fixture",
         "input": {"rules": {"ok": rules_ok, "reason": "" if rules_ok else "why should anyone care?"}}}
    r = IntelShadow(lane="newsdesk" if ref.startswith("event") else "trending", ref=ref, title=title, rules_ok=rules_ok,
                    semantic_ok=semantic_ok, agree=rules_ok == semantic_ok, tier=tier, source="model" if state == "done" else "rules",
                    seen=1, last_seen=now - timedelta(hours=age_h), verdict=v)
    db.add(r)
    db.commit()
    return r


def test_aggregates_decisions_disagreements_latency_misses_workers_and_spend(client: TestClient, db: Session) -> None:
    now = utcnow()
    pc1 = _job(db, now, by="pc", model="qwen3.8:27b", latency=4000, claimed=True, seconds=3.0)
    pc2 = _job(db, now, by="pc", model="qwen3.8:27b", latency=8000, claimed=True, seconds=5.0)
    cl = _job(db, now, by="cloud", model="claude-haiku-5-5", latency=2000)
    _job(db, now, by="rules", latency=100, error="cloud: over cap")
    _job(db, now, by=None, status="expired", claimed=True, error="pc lease expired", latency=None)
    _job(db, now, by="pc", model="qwen3.8:27b", latency=1, claimed=True, age_h=48)  # outside the 24 h window

    _row(db, now, "event:1", "Fixture Corp agrees $40 billion takeover", rules_ok=False, semantic_ok=True, tier="major", state="done", job=pc1,
         model="qwen3.8:27b")  # a possible miss
    _row(db, now, "event:2", "Fixture minister visits fixture town", rules_ok=True, semantic_ok=False, tier="noise", state="done", job=cl,
         model="claude-haiku-5-5")  # a possible false positive
    _row(db, now, "topic:3", "Fixture bank cuts rates", rules_ok=True, semantic_ok=True, tier="significant", state="done", job=pc2,
         model="qwen3.8:27b")  # agreement
    _row(db, now, "topic:4", "Fixture rumour says merger may come", rules_ok=False, semantic_ok=False, tier="major", state="done", job=pc2,
         model="qwen3.8:27b", veto=["hedged, rumour or forecast wording (may)"])  # a vetoed major: a miss candidate after the clean one
    _row(db, now, "event:5", "Fixture rules-only story", rules_ok=False, semantic_ok=False, tier="noise", state="rules")
    _row(db, now, "event:6", "Fixture pending story", rules_ok=True, semantic_ok=True, tier="significant", state="pending")
    _row(db, now, "event:7", "Fixture old story", rules_ok=False, semantic_ok=True, tier="major", state="done", job=pc1, age_h=72)

    today = now.strftime("%Y-%m-%d")
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    db.add_all([AiUsage(day=today, at=now, model="claude-haiku-5-5", purpose="intel.analyze", status="done", tokens_in=1000, tokens_out=200, cost_usd=0.25),
                AiUsage(day=today, at=now, model="claude-sonnet-5-5", purpose="intel.analyze", status="done", tokens_in=10, tokens_out=5, cost_usd=0.5),
                AiUsage(day=yday, at=now - timedelta(days=1), model="claude-haiku-5-5", purpose="intel.analyze", status="done", cost_usd=1.0),
                AiUsage(day=today, at=now, model="claude-haiku-5-5", purpose="intel.analyze", status="reserved", cost_usd=0.1)])
    w = IntelWorker(name="Owner PC", token_hash="f" * 64, enabled=True, last_seen_at=now - timedelta(seconds=10),
                    info={"models": ["qwen3.8:27b"], "busy": False, "version": "1.0", "gpu": "fixture GPU"})
    db.add(w)
    db.commit()
    before = _counts(db)

    b = client.get(URL, params={"hours": 24}).json()
    assert _counts(db) == before  # read-only

    d = b["decisions"]
    assert d["total"] == 6 and d["by_decider"] == {"pc": 3, "cloud": 1, "rules": 1, "pending": 1}
    assert d["by_model"] == {"qwen3.8:27b": 3, "claude-haiku-5-5": 1}
    assert d["tiers"]["major"] == 2 and d["tiers"]["noise"] == 2 and d["tiers"]["significant"] == 2 and d["model_tiers"]["noise"] == 1
    first = next(x for x in d["recent"] if x["ref"] == "event:1")
    assert first["decider"] == "pc" and first["latency_ms"] == 4000 and first["lane_ok"] is False and first["gate_ok"] is True

    g = b["disagreements"]
    assert g["counts"]["total"] == 6 and g["counts"]["pending"] == 1 and g["decided"] == 5 and g["counts"]["agree"] == 3
    assert g["agreement_rate"] == 0.6
    assert [x["ref"] for x in g["lane_yes_ai_no"]] == ["event:2"] and [x["ref"] for x in g["lane_no_ai_yes"]] == ["event:1"]

    m = b["missed"]
    assert [x["ref"] for x in m["possible_misses"]] == ["event:1", "topic:4"]
    assert m["possible_misses"][1]["hard_veto"] == ["hedged, rumour or forecast wording (may)"]
    assert [x["ref"] for x in m["possible_false_positives"]] == ["event:2"]

    lat = {(x["route"], x["model"]): x for x in b["latency"]["groups"]}
    assert lat[("pc", "qwen3.8:27b")]["n"] == 2 and lat[("pc", "qwen3.8:27b")]["p50_ms"] == 4000 and lat[("pc", "qwen3.8:27b")]["p95_ms"] == 8000
    assert lat[("pc", "qwen3.8:27b")]["compute_p50_ms"] == 3000 and lat[("cloud", "claude-haiku-5-5")]["p50_ms"] == 2000
    assert lat[("rules", "rules")]["n"] == 1
    assert b["latency"]["jobs"]["total"] == 5 and b["latency"]["jobs"]["refused_over_cap"] == 1

    [wk] = b["workers"]
    assert wk["online"] is True and wk["ready"] is True and wk["models"] == ["qwen3.8:27b"] and wk["gpu"] == "fixture GPU"
    assert wk["jobs"] == {"claimed": 3, "completed": 2, "failed": 0, "expired_or_taken": 1, "in_flight": 0}
    assert wk["availability"]["pct_online"] is None  # no heartbeat history recorded yet

    sp = b["spend"]
    assert sp["today"]["spent_usd"] == pytest.approx(0.85)  # 0.25 + 0.5 + the open 0.1 hold (the cap counts it)
    assert sp["window_usd"] == pytest.approx(1.75) and sp["window_calls"] == 3 and sp["refused_over_cap"] == 1
    assert [x["model"] for x in sp["per_model"]] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert sp["per_model"][0]["usd"] == pytest.approx(1.25) and sp["per_model"][0]["tokens_in"] == 1000
    days = {x["day"]: x["usd"] for x in sp["per_day"]}
    assert days[today] == pytest.approx(0.75) and days[yday] == pytest.approx(1.0)


def test_percentile_nearest_rank() -> None:
    assert dashboard.pct([], 50) is None
    assert dashboard.pct([5.0], 95) == 5.0
    vals = [float(i) for i in range(1, 101)]
    assert dashboard.pct(vals, 50) == 50.0 and dashboard.pct(vals, 95) == 95.0


# --- availability ----------------------------------------------------------------------------------------------------------------


def test_accrue_counts_fresh_intervals_split_by_hour_and_drops_old_hours() -> None:
    t0 = datetime(2026, 11, 25, 9, 59, 40, tzinfo=timezone.utc)
    a = channel.accrue({}, t0, True, t0 + timedelta(seconds=30))  # 20 s in 09:xx, 10 s in 10:xx
    assert a == {"2026-11-25T09": [20.0, 20.0], "2026-11-25T10": [10.0, 10.0]}
    a = channel.accrue(a, t0 + timedelta(seconds=30), False, t0 + timedelta(seconds=60))  # online, not ready (Ollama down)
    assert a["2026-11-25T10"] == [40.0, 10.0]
    assert channel.accrue(a, t0 + timedelta(seconds=60), True, t0 + timedelta(minutes=10)) == a  # a gap > 60 s: offline
    old = {"2026-11-01T00": [3600.0, 3600.0], **a}
    assert "2026-11-01T00" not in channel.accrue(old, None, False, t0 + timedelta(minutes=11))


def test_heartbeats_build_availability_shown_on_the_page(db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    now = utcnow()
    w = IntelWorker(name="Owner PC", token_hash="e" * 64, enabled=True, info={})
    db.add(w)
    db.commit()
    start = now - timedelta(minutes=30)
    for i in range(0, 61):  # 30 s heartbeats for 30 minutes
        channel.heartbeat(db, w, ["qwen3.8:27b"], {"busy": False}, start + timedelta(seconds=30 * i))
    av = dashboard.availability(w, now - timedelta(hours=24), now)
    assert av["online_seconds"] == 1800 and av["ready_seconds"] == 1800
    assert av["covered_seconds"] >= 1800 and 0 < av["pct_online"] <= 1
    body = client.get(URL).json()
    assert body["workers"][0]["availability"]["online_seconds"] == 1800
    assert body["workers"][0]["online"] is True
