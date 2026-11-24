"""Terminal Intelligence Engine: the shared AI ledger (price table, daily cap, reservations) and the one Anthropic client."""

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.intel import anthropic_client, ledger, prompt
from app.models.intel import AiUsage

GOOD = {"event_type": "merger_acquisition", "entities": ["Acme", "Globex"],
        "consequence": {"kind": "deal", "stated_in_text": True, "magnitude": "$41 billion"}, "materiality": "significant",
        "audiences": ["markets"], "is_speculative": False, "is_opinion_or_feature": False,
        "claims": [{"text": "Acme buys Globex for $41 billion", "attributed_to": "Reuters", "hedged": False, "model_inference": False}],
        "reason": "A stated $41B takeover."}


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch) -> Any:
    s = get_settings()
    monkeypatch.setattr(s, "intel_daily_usd_cap", 3.0)
    monkeypatch.setattr(s, "anthropic_api_key", SecretStr("sk-test-not-real"))
    return s


def anthropic_reply(analysis: Any = GOOD, stop: str = "end_turn", tin: int = 1000, tout: int = 300, status: int = 200,
                    seen: list[dict[str, Any]] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        if status != 200:
            return httpx.Response(status, json={"type": "error"})
        text = analysis if isinstance(analysis, str) else json.dumps(analysis)
        return httpx.Response(200, json={"stop_reason": stop, "content": [{"type": "text", "text": text}],
                                         "usage": {"input_tokens": tin, "output_tokens": tout}})
    return httpx.MockTransport(handler)


def test_price_table_and_unknown_model_charged_as_opus() -> None:
    assert ledger.cost("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert ledger.cost("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.0)
    assert ledger.cost("claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
    assert ledger.cost("claude-sonnet-5-5", 0, 0, cache_read=1_000_000) == pytest.approx(0.10)
    assert ledger.cost("some-new-model", 1_000_000, 1_000_000) == ledger.cost("claude-opus-5-5", 1_000_000, 1_000_000)
    assert ledger.estimate("claude-haiku-5-5", 2000, 2000) == pytest.approx((2000 * 0.10 + 2000 * 0.50) / 1e6)


def test_cap_math_counts_records_and_open_reservations(db: Session, cfg: Any) -> None:
    assert ledger.can_spend(db, 3.0)
    ledger.record(db, "claude-opus-5-5", 0, 100_000, "test")  # $2.00
    assert ledger.spent_today(db) == pytest.approx(2.0)
    assert ledger.can_spend(db, 0.99) and not ledger.can_spend(db, 1.01)
    hold = ledger.reserve(db, "claude-haiku-5-5", 0.9, "test")
    assert hold is not None and ledger.spent_today(db) == pytest.approx(2.9)
    assert ledger.reserve(db, "claude-haiku-5-5", 0.2, "test") is None  # would pass the cap: refused, nothing written
    ledger.settle(db, hold, 1000, 100)  # actual: tiny
    assert ledger.spent_today(db) == pytest.approx(2.0 + ledger.cost("claude-haiku-5-5", 1000, 100))
    assert ledger.status(db)["over_cap"] is False


def test_stale_reservation_and_yesterday_dont_count(db: Session, cfg: Any) -> None:
    now = utcnow()
    db.add(AiUsage(day=ledger.day_of(now), at=now - timedelta(hours=1), model="m", purpose="p", status="reserved", cost_usd=5.0))
    db.add(AiUsage(day=ledger.day_of(now - timedelta(days=1)), at=now - timedelta(days=1), model="m", purpose="p", status="done", cost_usd=9.0))
    db.commit()
    assert ledger.spent_today(db, now) == 0.0


def test_zero_cap_refuses_everything(db: Session, cfg: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "intel_daily_usd_cap", 0.0)
    assert not ledger.can_spend(db, 0.0001)


async def test_client_sends_structured_output_no_sampling_params_and_records(db: Session, cfg: Any) -> None:
    seen: list[dict[str, Any]] = []
    res = await anthropic_client.analyze(db, "Acme buys Globex for $41 billion", "Deal.", ["Reuters"], transport=anthropic_reply(seen=seen))
    assert res.analysis.materiality == "significant" and res.model == "claude-haiku-5-5"
    body = seen[0]["body"]
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": prompt.json_schema()}
    assert "temperature" not in body and "top_p" not in body and "top_k" not in body
    assert seen[0]["headers"]["x-api-key"] == "sk-test-not-real"
    rows = db.query(AiUsage).all()
    assert len(rows) == 1 and rows[0].status == "done" and rows[0].tokens_in == 1000
    assert rows[0].cost_usd == pytest.approx(ledger.cost("claude-haiku-5-5", 1000, 300))


@pytest.mark.parametrize("kw,reason", [({"stop": "refusal"}, "refusal"), ({"stop": "max_tokens"}, "max_tokens"),
                                       ({"analysis": {**GOOD, "materiality": "huge"}}, "invalid answer"),
                                       ({"analysis": "not json"}, "invalid answer")])
async def test_client_checks_stop_reason_and_schema_and_still_bills(db: Session, cfg: Any, kw: dict[str, Any], reason: str) -> None:
    with pytest.raises(anthropic_client.CloudUnavailable) as e:
        await anthropic_client.analyze(db, "Headline", transport=anthropic_reply(**kw))
    assert e.value.reason == reason
    row = db.query(AiUsage).one()
    assert row.status == "failed" and row.cost_usd > 0  # the tokens were billed: the ledger says so


async def test_client_http_error_bills_nothing(db: Session, cfg: Any) -> None:
    with pytest.raises(anthropic_client.CloudUnavailable):
        await anthropic_client.analyze(db, "Headline", transport=anthropic_reply(status=529))
    assert db.query(AiUsage).one().cost_usd == 0.0


async def test_client_refuses_without_key_or_over_cap_without_calling(db: Session, cfg: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(cfg, "anthropic_api_key", None)
    with pytest.raises(anthropic_client.CloudUnavailable, match="no key"):
        await anthropic_client.analyze(db, "Headline", transport=anthropic_reply(seen=seen))
    monkeypatch.setattr(cfg, "anthropic_api_key", SecretStr("sk-test-not-real"))
    monkeypatch.setattr(cfg, "intel_daily_usd_cap", 0.0)
    with pytest.raises(anthropic_client.CloudUnavailable, match="over cap"):
        await anthropic_client.analyze(db, "Headline", transport=anthropic_reply(seen=seen))
    assert seen == [] and db.query(AiUsage).count() == 0
