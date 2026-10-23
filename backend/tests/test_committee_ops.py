"""Kiwi AI Investment Committee operations (no network): backend selection, the router / Anthropic / OpenAI backends,
budgets that survive restarts, the per-run cap, daily limits, the 15-minute cache, the concurrency limit, background runs,
the HTTP API, outcomes from candles, and the LLM-job expiry."""

import asyncio
import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.committee import llm, orchestrator, outcomes, remote
from app.committee.llm import Backend, Call
from app.committee.schemas import AnalystOut
from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.committee import CmOutcome, CmRun, CmUsage
from app.providers.base import ChatResult, ProviderHealth
from tests.committee_fixtures import FakeLLM, btc, grounded_answer, pack_for

WHO = {"tg_user_id": 222}


@pytest.fixture
def evidence(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    async def fake(db: Any, symbol: str, timeframe: str, as_of: Any, mode: str) -> dict[str, Any]:
        asked.append(symbol)
        return pack_for(symbol)

    monkeypatch.setattr(orchestrator, "_evidence", fake)
    return asked


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch) -> Any:
    s = get_settings()
    monkeypatch.setattr(s, "committee_backend", "rules")
    return s


# ---------------------------------------------------------------- backends
class CommitteeProvider:
    """TEST FIXTURE: an LLMProvider answering committee schemas with grounded JSON (by schema title)."""

    name, model = "fake", "fake-model"

    def __init__(self, ok: bool = True) -> None:
        self.ok, self.calls = ok, []

    async def chat(self, messages: Any, options: Any = None) -> ChatResult:
        self.calls.append(options)
        title = (options.response_schema or {}).get("title")
        role = {"AnalystOut": "technical analyst", "DebaterOut": "bull", "RebuttalsOut": "rebuttals", "ManagerOut": "research manager",
                "TraderOut": "trader", "RiskPanelOut": "risk panel", "PortfolioOut": "portfolio manager"}[title]
        ans = grounded_answer(Call(role, "committee.analyst", "", "", AnalystOut))
        return ChatResult(json.dumps(ans), options.model, "fake", 10, 5)

    async def health(self, model: str | None = None) -> ProviderHealth:
        return ProviderHealth("fake", "fake-model", available=self.ok)


def test_auto_picks_router_when_its_provider_answers_and_rules_otherwise(db: Session) -> None:
    b, why = asyncio.run(llm.choose(db, CommitteeProvider()))
    assert b.name == "router" and why is None
    assert b.models() == {"fast": "fast-model", "deep": "strong-model", "synthesis": "strong-model"}  # committee.* task routes
    b, why = asyncio.run(llm.choose(db, CommitteeProvider(ok=False)))
    assert b.name == "rules" and why == "no model backend available"


def test_auto_prefers_vortex_when_it_was_seen(db: Session) -> None:
    from app.tradingos import assist

    assist.mark_seen(db)
    db.commit()
    b, _ = asyncio.run(llm.choose(db, CommitteeProvider()))
    assert b.name == "vortex"


def test_router_backend_runs_the_committee_through_ai_routing(db: Session) -> None:
    p = CommitteeProvider()
    b, _ = asyncio.run(llm.choose(db, p))
    res = asyncio.run(orchestrator.run_committee(db, "BTC", evidence=btc(), backend=b)).result
    assert res["meta"]["backend"] == "router" and res["meta"]["calls"] == 11
    assert {o.model for o in p.calls} == {"fast-model", "strong-model"}
    assert res["debate"]["manager"]["author"] == "llm:strong-model" and res["analysts"]["technical"]["author"] == "llm:fast-model"


def test_paid_backends_need_a_key_and_models(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "committee_backend", "anthropic")
    b, why = asyncio.run(llm.choose(db))
    assert b.name == "rules" and why == "anthropic: ANTHROPIC_API_KEY not set"
    from app.security.credentials import secret_store

    secret_store().set("anthropic_api_key", "sk-ant-test")
    b, why = asyncio.run(llm.choose(db))
    assert b.name == "rules" and "COMMITTEE_MODEL_FAST" in (why or "")
    for k, v in (("committee_model_fast", "claude-haiku-4-5"), ("committee_model_deep", "claude-sonnet-4-5"), ("committee_model_synth", "claude-sonnet-4-5")):
        monkeypatch.setattr(s, k, v)
    b, why = asyncio.run(llm.choose(db))
    assert b.name == "anthropic" and b.paid and why is None


def _models() -> dict[str, str]:
    return {"fast": "claude-haiku-4-5", "deep": "claude-sonnet-4-5", "synthesis": "claude-sonnet-4-5"}


def test_anthropic_backend_http_shape_cost_and_failures(db: Session) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = json.loads(req.content)
        if "fail" in body["messages"][0]["content"]:
            return httpx.Response(529, json={"error": "overloaded"})
        return httpx.Response(200, json={"model": body["model"], "content": [{"type": "text", "text": json.dumps(grounded_answer(
            Call("technical analyst", "committee.analyst", "", "", AnalystOut)))}], "usage": {"input_tokens": 1000, "output_tokens": 200}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    b = llm.AnthropicBackend("sk-ant-test", _models(), http=http)
    led = llm.Ledger(db, b)
    out, author, note = asyncio.run(led.ask(Call("technical analyst", "committee.analyst", "S", "P", AnalystOut)))
    assert out is not None and author == "llm:claude-haiku-4-5" and note is None
    r = seen[0]
    assert str(r.url) == llm.ANTHROPIC_URL and r.headers["x-api-key"] == "sk-ant-test" and r.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(r.content)
    assert body["max_tokens"] <= 900 and body["system"] == "S"
    assert led.cost_usd == pytest.approx(1000 * 1.0 / 1e6 + 200 * 5.0 / 1e6)
    _, _, note = asyncio.run(led.ask(Call("bull", "committee.debate", "S", "fail", AnalystOut)))
    assert note and note.startswith("bull: anthropic: HTTP 529")
    assert llm.budget(db)["calls_today"] == 2 and llm.budget(db)["usd_today"] == pytest.approx(0.002, abs=1e-6)


def test_openai_backend_and_timeouts(db: Session) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer sk-test"
        body = json.loads(req.content)
        if body["messages"][1]["content"].startswith("slow"):
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": json.dumps(grounded_answer(
            Call("news analyst", "committee.analyst", "", "", AnalystOut)))}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    b = llm.OpenAIBackend("sk-test", {"fast": "gpt-4o-mini", "deep": "gpt-4o", "synthesis": "gpt-4o"},
                          http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    led = llm.Ledger(db, b)
    out, author, _ = asyncio.run(led.ask(Call("news analyst", "committee.analyst", "S", "P", AnalystOut)))
    assert out is not None and author == "llm:gpt-4o-mini"
    out, _, note = asyncio.run(led.ask(Call("news analyst", "committee.analyst", "S", "slow", AnalystOut)))
    assert out is None and "ReadTimeout" in (note or "")


def test_cost_table_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "committee_costs", '{"my-model": [2, 4]}')
    assert llm.cost_of("openai", "my-model", 1_000_000, 1_000_000) == 6.0
    assert llm.cost_of("openai", "unknown-model", 1_000_000, 0) == 15.0  # unknown paid model: the strict default
    assert llm.cost_of("vortex", "anything", 10**9, 10**9) == 0.0  # local models are free


# ---------------------------------------------------------------- budgets and caps
def test_daily_budgets_are_persisted_and_force_rules(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "committee_daily_calls", 5)
    res = asyncio.run(orchestrator.run_committee(db, "BTC", evidence=btc(), backend=FakeLLM())).result
    assert res["meta"]["calls"] == 5  # the 6th call met the daily budget
    assert any("daily LLM call budget reached (5/5)" in d for d in res["meta"]["degraded"])
    row = db.query(CmUsage).filter_by(key="global").one()
    assert row.calls == 5 and row.tokens_in == 500  # stored: a restart doesn't reset it
    b, why = asyncio.run(llm.choose(db, CommitteeProvider()))
    assert b.name == "rules" and why == "daily LLM call budget reached (5/5)"
    monkeypatch.setattr(s, "committee_daily_calls", 300)
    monkeypatch.setattr(s, "committee_daily_usd", 0.001)
    llm.record(db, 0, 0, 0.002)
    assert llm.over_budget(db, paid=True) == "daily USD budget reached ($0.00/$0.00)"
    assert llm.over_budget(db, paid=False) is None  # the USD budget only stops paid backends


def test_per_run_call_cap(db: Session) -> None:
    led = llm.Ledger(db, FakeLLM(), max_calls=2)
    for _ in range(2):
        assert asyncio.run(led.ask(Call("technical analyst", "committee.analyst", "S", "P", AnalystOut)))[0] is not None
    out, _, note = asyncio.run(led.ask(Call("news analyst", "committee.analyst", "S", "P", AnalystOut)))
    assert out is None and note == "news analyst: per-run call cap (2) reached"


def test_daily_limit_cache_and_ranked_members(db: Session, evidence: list[str], cfg: Any) -> None:
    async def go() -> None:
        ids = []
        for sym in ("BTC", "DOGE", "ETH"):
            ids.append(orchestrator.start_run(db, sym, requested_by=WHO))
            await orchestrator.wait(ids[-1])
        # the same market within 15 minutes: the cached run, no new run, not counted
        assert orchestrator.start_run(db, "BTC", requested_by=WHO) == ids[0]
        with pytest.raises(orchestrator.LimitReached):
            orchestrator.start_run(db, "SOL", requested_by=WHO)
        rid = orchestrator.start_run(db, "SOL", requested_by={"admin": True})  # the owner is unlimited
        await orchestrator.wait(rid)

    asyncio.run(go())
    assert evidence == ["BTC", "DOGE", "ETH", "SOL"]
    assert orchestrator.used_today(db, WHO) == 3
    cached = asyncio.run(orchestrator.run_committee(db, "BTC", requested_by=WHO))
    assert cached.cached and cached.result["meta"]["cached"] is True
    db.expire_all()
    assert db.query(CmRun).count() == 4

    from tests.test_rank_rewards import member_at, min_xp

    m = member_at(db, min_xp(db, "warrior"))
    assert orchestrator.daily_limit(db, {"member_id": m.id}) == (10, True)
    assert orchestrator.daily_limit(db, {"tg_user_id": 999}) == (3, False)


def test_concurrency_limit_queues_runs(db: Session, monkeypatch: pytest.MonkeyPatch, cfg: Any) -> None:
    monkeypatch.setattr(cfg, "committee_max_concurrent", 1)
    gate = asyncio.Event()

    async def slow(db: Any, symbol: str, timeframe: str, as_of: Any, mode: str) -> dict[str, Any]:
        await gate.wait()
        return pack_for(symbol)

    monkeypatch.setattr(orchestrator, "_evidence", slow)

    async def go() -> None:
        a = orchestrator.start_run(db, "BTC", requested_by={"admin": True})
        b = orchestrator.start_run(db, "DOGE", requested_by={"admin": True})
        for _ in range(20):
            await asyncio.sleep(0.05)
        db.expire_all()
        assert orchestrator.running() == 1
        assert {r.public_id: r.status for r in db.query(CmRun)} == {a: "running", b: "queued"}
        gate.set()
        await orchestrator.wait(a)
        await orchestrator.wait(b)

    asyncio.run(go())
    db.expire_all()
    assert {r.status for r in db.query(CmRun)} == {"done"} and orchestrator.running() == 0


def test_evidence_error_fails_the_run(db: Session, monkeypatch: pytest.MonkeyPatch, cfg: Any) -> None:
    async def bad(*a: Any) -> dict[str, Any]:
        raise orchestrator.CommitteeError("ambiguous", "SHIB matches 3 markets; pick one.")

    monkeypatch.setattr(orchestrator, "_evidence", bad)

    async def go() -> str:
        rid = orchestrator.start_run(db, "SHIB", requested_by={"admin": True})
        await orchestrator.wait(rid)
        return rid

    rid = asyncio.run(go())
    db.expire_all()
    row = orchestrator.find(db, rid)
    assert row is not None and row.status == "failed" and row.error == "SHIB matches 3 markets; pick one."


# ---------------------------------------------------------------- API
def test_api_status_runs_and_placeholders(client: TestClient, db: Session, evidence: list[str], cfg: Any) -> None:
    st = client.get("/api/committee/status").json()
    assert st["backend"] == "rules" and st["backends"]["rules"]["ok"] and st["budget"]["calls_limit"] == 300
    assert client.post("/api/committee/runs", json={"symbol": "BTC"}).status_code == 401
    from tests.test_rank_rewards import member_at, signed_in

    signed_in(client, db, member_at(db, 0))
    r = client.post("/api/committee/runs", json={"symbol": "BTC", "timeframe": "1h"})
    assert r.status_code == 202 and r.json()["cached"] is False
    rid = r.json()["id"]
    for _ in range(100):
        run = client.get(f"/api/committee/runs/{rid}").json()
        if run["status"] == "done":
            break
        import time

        time.sleep(0.05)
    assert run["status"] == "done" and run["verdict"]["action"] == "LONG" and run["execution"]["status"] == "APPROVED"
    assert client.post("/api/committee/runs", json={"symbol": "BTC"}).json() == {"id": rid, "cached": True}
    lst = client.get("/api/committee/runs?symbol=BTC").json()["runs"]
    assert lst[0]["id"] == rid and lst[0]["verdict"] == "LONG" and "requested_by" not in json.dumps(lst)
    assert client.get(f"/api/committee/runs/{rid}/report.html").headers["content-disposition"].startswith("attachment; filename=\"kiwi-committee-BTC-1h-")
    assert client.get("/api/committee/paper").json()["mode"] == "PAPER"
    assert client.get("/api/committee/backtests").status_code == 401  # owner only (cm-backtest)
    assert client.get("/api/committee/backtests/1").status_code == 401
    assert client.get("/api/committee/runs/cm_nope").status_code == 404
    from app.security import rate_limit

    rate_limit.reset_for_tests()
    assert client.post("/api/committee/runs", json={"symbol": "BTC", "timeframe": "3h"}).status_code == 400
    assert "by_verdict" in client.get("/api/committee/track").json()


# ---------------------------------------------------------------- outcomes and expiry
def _done_run(db: Session, verdict: str, hours_ago: float) -> CmRun:
    r = CmRun(public_id=f"cm_{verdict.lower()}{int(hours_ago)}", symbol="BTC", venue="hyperliquid", market_type="perp", market_id="BTC",
              timeframe="1h", mode="live", depth="full", status="done", verdict=verdict, rating="Hold", exec_status="NOT_APPLICABLE",
              created_at=utcnow() - timedelta(hours=hours_ago), cost_usd=0, calls=0)
    db.add(r)
    db.commit()
    return r


def test_outcomes_start_at_the_first_close_after_the_decision(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    long_run = _done_run(db, "LONG", 5)
    wait_run = _done_run(db, "WAIT", 5.5)
    t0 = int(long_run.created_at.timestamp() * 1000)

    def candles(coin: str, interval: str, start: int, end: int) -> list[dict[str, Any]]:
        assert coin == "BTC" and interval == "5m"
        out, px, t = [], 100.0, start - start % 300_000
        while t <= end:
            out.append({"t": t, "o": px, "h": px + 1, "l": px - 1, "c": px})
            px += 0.5
            t += 300_000
        return out

    monkeypatch.setattr(outcomes, "fetch_candles", candles)
    res = asyncio.run(outcomes.resolve(db))
    assert res["resolved"] == 4  # 1h and 4h for both runs; 24h / 7d aren't due
    o = db.query(CmOutcome).filter_by(run_id=long_run.id, horizon="1h").one()
    first_close_after = min((c for c in candles("BTC", "5m", t0 - 300_000, t0 + 3_600_000) if c["t"] + 300_000 > t0), key=lambda c: c["t"])
    assert o.price_from == first_close_after["c"] and o.price_to > o.price_from and o.direction_ok is True and o.ret_pct > 0
    w = db.query(CmOutcome).filter_by(run_id=wait_run.id, horizon="4h").one()
    assert w.direction_ok is None and w.ret_pct > 0  # WAIT: no position, the move is still recorded
    assert asyncio.run(outcomes.resolve(db))["resolved"] == 0  # never twice
    tr = outcomes.track(db)
    assert tr["by_verdict"]["LONG"]["1h"]["hit_rate"] == 1.0 and tr["by_verdict"]["WAIT"]["4h"]["hit_rate"] is None


def test_llm_job_expiry(db: Session) -> None:
    j = remote.create(db, role="trader", tier="fast", system="S", prompt="P", schema=None, max_tokens=100, temperature=0.1)
    assert remote.expire(db) == 0
    j.created_at = utcnow() - timedelta(seconds=120)
    db.commit()
    assert remote.expire(db) == 1
    db.refresh(j)
    assert j.status == "expired"
    with pytest.raises(remote.Conflict):
        remote.answer(db, j, "late")


def test_jobs_are_registered(client: TestClient) -> None:
    from app.jobs import build_jobs

    names = {s.name for s in build_jobs(client.app.state.runtime)}
    assert {"committee.outcomes", "committee.llm_expire", "committee.paper"} <= names
