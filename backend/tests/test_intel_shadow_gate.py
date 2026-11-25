"""Terminal Intelligence Engine, Phase 0: the shared semantic gate in SHADOW (app/intel/gate). Off by default; when on it records, next to
each lane's decision, what the semantic gate would decide, and never changes a publish, hold or reject. Headlines are the production
misses listed in docs/intelligence/2026-11-25-terminal-intelligence-engine.md section 1, or marked "fixture"."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.intel import classes, gate
from app.intel import router as intel_router
from app.intel.schema import ItemAnalysis
from app.models.intel import IntelJob
from app.models.intel_shadow import IntelShadow
from app.models.trending import TtPost, TtTopic
from app.newsdesk import events, newsworthy
from app.trending import engine, writer
from tests.test_newsdesk_hardblock import SLOT, _news

SANOFI = "Sanofi to buy Insmed for $41 billion"
NOW = utcnow()


def analysis(**kw: Any) -> dict[str, Any]:
    base = {"event_type": "merger_acquisition", "entities": ["Sanofi", "Insmed"],
            "consequence": {"kind": "deal", "stated_in_text": True, "magnitude": "$41 billion"}, "materiality": "significant",
            "audiences": ["markets"], "is_speculative": False, "is_opinion_or_feature": False,
            "claims": [{"text": SANOFI, "attributed_to": "Reuters", "hedged": False, "model_inference": False}],
            "reason": "A stated $41B takeover."}
    return {**base, **kw}


@pytest.fixture
def shadow(monkeypatch: pytest.MonkeyPatch) -> Any:
    s = get_settings()
    monkeypatch.setattr(s, "intel_shadow_enabled", True)
    return s


@pytest.fixture
def intel_on(shadow: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(shadow, "intel_enabled", True)
    return shadow


@pytest.fixture(autouse=True)
def _no_writer_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(writer, "model", none)


def _topic(db: Session, title: str, score: float = 90.0) -> TtTopic:
    src = [{"source": "news", "outlet": o, "title": title, "url": f"https://{o.lower()}.test/1", "reliable": True, "engagement": 3.0,
            "summary": "", "at": NOW.isoformat(), "extra": {}} for o in ("Reuters", "CNBC")]
    t = TtTopic(key=f"t-{title[:30]}", title=title, category="stocks", status="active", score=score, peak_score=score,
                parts={"growth": {"measured": True}}, sources=src, first_seen=NOW - timedelta(hours=1), last_seen=NOW)
    db.add(t)
    db.commit()  # the lane's session holds no write lock when it judges (SQLite: the shadow write would give up after 250 ms)
    return t


async def _suggest(db: Session, topic: TtTopic) -> tuple[str, str | None, str | None, str]:
    s = engine.settings_row(db)
    p = await engine.suggest(db, s, get_settings(), topic, NOW)
    out = (p.status, p.hold_reason, p.note, p.text)
    db.delete(p)
    db.commit()
    return out


# --- off by default ---------------------------------------------------------------------------------------------------------------


async def test_off_by_default_writes_nothing_and_queues_nothing(db: Session) -> None:
    s = get_settings()
    assert s.intel_shadow_enabled is False and s.intel_enabled is False
    ev = _news(db, "off", SANOFI, 70, ["Reuters", "CNBC"])
    events._newsworthy(db, ev, "", SLOT)
    await _suggest(db, _topic(db, SANOFI))
    gate.observe(db, "newsdesk", "event:999", SANOFI, "", ["Reuters"], False)
    assert gate.refresh(db) == {"skipped": "intel shadow disabled"}
    assert db.query(IntelShadow).count() == 0 and db.query(IntelJob).count() == 0


def test_off_does_not_even_query(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise AssertionError("no session may be opened while the shadow is off")

    monkeypatch.setattr(gate, "_own_session", boom)
    gate.observe(db, "newsdesk", "event:1", SANOFI, "", ["Reuters"], False)  # returns at the switch


# --- on: both lanes write rows, and their decisions are unchanged ----------------------------------------------------------------


async def test_on_rows_for_both_lanes_and_identical_lane_decisions(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    ev = _news(db, "nd", SANOFI, 70, ["Reuters", "CNBC"])
    big = _news(db, "nd2", "Hackers drain $1.2 billion from exchange Bybaz", 80, ["The Block", "Decrypt"], topic="hack")
    topic = _topic(db, SANOFI)
    quiet = _topic(db, "Capita boss exits, shares crash 57%", 91.0)
    off = [events._newsworthy(db, e, "⚡ JUST IN", SLOT) for e in (ev, big)] + [await _suggest(db, t) for t in (topic, quiet)]
    assert db.query(IntelShadow).count() == 0
    monkeypatch.setattr(s, "intel_shadow_enabled", True)
    on = [events._newsworthy(db, e, "⚡ JUST IN", SLOT) for e in (ev, big)] + [await _suggest(db, t) for t in (topic, quiet)]
    assert on == off  # the shadow never changes what either lane decides
    rows = {(r.lane, r.ref): r for r in db.query(IntelShadow).all()}
    assert set(rows) == {("newsdesk", f"event:{ev.id}"), ("newsdesk", f"event:{big.id}"), ("trending", f"topic:{topic.id}"),
                         ("trending", f"topic:{quiet.id}")}
    nd = rows[("newsdesk", f"event:{ev.id}")]
    assert nd.rules_ok is False and nd.source == "rules" and nd.agree and nd.verdict["state"] == "rules"  # no analysis: the rules verdict
    assert rows[("newsdesk", f"event:{big.id}")].rules_ok is True
    assert db.query(IntelJob).count() == 0  # intel itself is off: nothing queued
    events._newsworthy(db, ev, "", SLOT)
    db.expire_all()
    assert db.get(IntelShadow, nd.id).seen == 2


async def test_with_intel_on_a_bulk_job_is_queued_once_per_candidate(db: Session, intel_on: Any) -> None:
    ev = _news(db, "q", SANOFI, 70, ["Reuters", "CNBC"])
    events._newsworthy(db, ev, "", SLOT)
    events._newsworthy(db, ev, "", SLOT)
    jobs = db.query(IntelJob).all()
    assert len(jobs) == 1 and jobs[0].priority == "bulk" and jobs[0].key == gate.job_key("newsdesk", f"event:{ev.id}")
    assert jobs[0].payload["title"] == SANOFI
    row = db.query(IntelShadow).one()
    assert row.verdict["state"] == "pending" and row.verdict["job_id"] == jobs[0].id


async def test_refresh_fills_the_model_verdict_and_reports_the_disagreement(db: Session, intel_on: Any) -> None:
    ev = _news(db, "s", SANOFI, 70, ["Reuters", "CNBC"])
    assert events._newsworthy(db, ev, "", SLOT)[0].startswith("not published")  # the rules: no concrete consequence
    job = db.query(IntelJob).one()
    job.status, job.result_by, job.result = "done", "pc", {"output": analysis(), "model": "qwen3.6:27b"}
    db.commit()
    assert gate.refresh(db, SLOT)["model"] == 1
    row = db.query(IntelShadow).one()
    assert row.rules_ok is False and row.semantic_ok is True and row.agree is False and row.tier == "significant" and row.source == "model"
    assert row.verdict["source"] == "model:qwen3.6:27b" and row.verdict["claims"][0]["class"] == classes.VERIFIED  # two outlets
    rep = gate.report(db, SLOT - timedelta(hours=1))
    assert rep["counts"]["semantic_only"] == 1 and rep["counts"]["agree"] == 0
    item = rep["semantic_only"][0]
    assert item["title"] == SANOFI and "no concrete consequence" in item["rules_reason"] and item["semantic_reasons"]


async def test_refresh_without_an_analysis_keeps_the_rules_verdict(db: Session, intel_on: Any) -> None:
    ev = _news(db, "r", SANOFI, 70, ["Reuters"])
    events._newsworthy(db, ev, "", SLOT)
    job = db.query(IntelJob).one()
    job.status, job.result_by, job.result = "done", "rules", None
    db.commit()
    assert gate.refresh(db, SLOT)["rules"] == 1
    row = db.query(IntelShadow).one()
    assert row.source == "rules" and row.agree and row.verdict["state"] == "rules"


def test_admin_endpoint_reports(client: Any, db: Session, shadow: Any) -> None:
    gate.observe(db, "newsdesk", "event:7", SANOFI, "", ["Reuters"], False)
    r = client.get("/api/intel/shadow")
    assert r.status_code == 200
    body = r.json()
    assert body["shadow_enabled"] is True and body["report"]["counts"]["total"] == 1 and "ledger" in body["router"]


# --- the verdict --------------------------------------------------------------------------------------------------------------


def test_a_significant_takeover_is_newsworthy_where_the_rules_said_no() -> None:
    a = newsworthy.assess(SANOFI)
    assert not a.ok
    v = gate.verdict(analysis(), SANOFI, "", ["Reuters"], "", a, model="claude-haiku-5-5")
    assert v.newsworthy and not v.major and v.tier == "significant" and v.source == "model:claude-haiku-5-5" and v.hard_veto == []
    assert v.event_type == "merger_acquisition" and v.consequence["magnitude"] == "$41 billion"


def test_a_veto_wins_over_a_model_major() -> None:
    title = "Bitcoin could crash 50% after exchange collapse, analysts warn"  # fixture: hedged
    v = gate.verdict(analysis(materiality="major", event_type="market_move"), title, "", ["Reuters", "CNBC"], "", newsworthy.assess(title))
    assert not v.newsworthy and not v.major and v.consequence_ok and any("hedged" in x for x in v.hard_veto)
    # a lane veto (a repeat, a cap, the liquidation pause) wins as well
    v = gate.verdict(analysis(materiality="major"), SANOFI, "", ["Reuters"], "", None, rules_ok=False,
                     reason="already told on X (post 12) 3 hours ago")
    assert not v.newsworthy and v.hard_veto == ["already told on X (post 12) 3 hours ago"]
    v = gate.verdict(analysis(materiality="major"), SANOFI, "", ["Reuters"], "", None, rules_ok=False, vetoes=["liquidation posts paused"])
    assert not v.newsworthy and "liquidation posts paused" in v.hard_veto
    # the consequence test itself is what the model replaces: it is not a veto
    v = gate.verdict(analysis(), SANOFI, "", ["Reuters"], "", None, rules_ok=False, reason="not newsworthy enough to post on its own: noise")
    assert v.newsworthy and v.hard_veto == []


def test_the_audience_gates_outright_refusal_is_a_veto() -> None:
    title = "Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south"
    v = gate.verdict(analysis(materiality="significant", event_type="conflict_military"), title, "", ["Reuters", "AP"], "", newsworthy.assess(title))
    assert not v.newsworthy and v.hard_veto and v.hard_veto[0].startswith("a routine military update") and len(v.hard_veto) == 1


@pytest.mark.parametrize("kw", [{"is_speculative": True}, {"is_opinion_or_feature": True, "event_type": "opinion_analysis_feature"},
                                {"materiality": "notable"}, {"materiality": "noise"},
                                {"consequence": {"kind": "deal", "stated_in_text": False, "magnitude": ""}}])
def test_speculation_opinion_routine_or_inferred_is_not_newsworthy(kw: dict[str, Any]) -> None:
    v = gate.verdict(analysis(**kw), SANOFI, "", ["Reuters", "CNBC"], "", None, rules_ok=False)
    assert not v.newsworthy and not v.consequence_ok


def test_no_analysis_is_the_rules_verdict() -> None:
    a = newsworthy.assess("Hackers drain $1.2 billion from exchange Bybaz")
    v = gate.verdict(None, "Hackers drain $1.2 billion from exchange Bybaz", "", ["The Block", "Decrypt"], "", a)
    assert v.source == "rules" and v.newsworthy == a.ok and v.tier == ("major" if a.strength == "major" else "significant")
    v = gate.verdict(None, SANOFI, "", ["Reuters"], "", newsworthy.assess(SANOFI))
    assert v.source == "rules" and not v.newsworthy and v.tier == "noise"


def test_claim_classes_come_from_the_outlets_never_the_model() -> None:
    claims = [{"text": SANOFI, "attributed_to": "Reuters", "hedged": False, "model_inference": False},
              {"text": "The deal makes Sanofi the leader in lung disease", "attributed_to": "", "hedged": False, "model_inference": True}]
    one = gate.verdict(analysis(claims=claims), SANOFI, "", ["Reuters"], "", None, rules_ok=False)
    two = gate.verdict(analysis(claims=claims), SANOFI, "", ["Reuters", "CNBC"], "", None, rules_ok=False)
    same = gate.verdict(analysis(claims=claims), SANOFI, "", ["AP", "AP News"], "", None, rules_ok=False)  # one outlet, two feeds
    assert [c["class"] for c in one.claims] == [classes.REPORTED, classes.INFERENCE]
    assert [c["class"] for c in two.claims] == [classes.VERIFIED, classes.INFERENCE]
    assert same.claims[0]["class"] == classes.REPORTED and one.headline_class == classes.REPORTED and two.headline_class == classes.VERIFIED
    assert two.claims[1]["auto_post"] == "never"


def test_decide_matches_the_benchmark() -> None:
    a = ItemAnalysis.model_validate(analysis())
    assert gate.decide(a) == {"tier": "significant", "newsworthy": True, "major": False}


# --- never raises into a lane -----------------------------------------------------------------------------------------------------


def test_observe_never_raises_when_the_db_or_router_throws(db: Session, intel_on: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("router down")

    monkeypatch.setattr(intel_router, "enqueue", broken)
    gate.observe(db, "newsdesk", "event:5", SANOFI, "", ["Reuters"], False)  # no exception
    assert db.query(IntelShadow).count() == 0  # the failed write rolled back on its own session

    def no_db(*_: Any, **__: Any) -> None:
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(gate, "_own_session", no_db)
    gate.observe(db, "trending", "topic:5", SANOFI, "", ["Reuters"], True)  # no exception


async def test_a_broken_gate_never_changes_the_lane(db: Session, shadow: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ev = _news(db, "b", "Hackers drain $1.2 billion from exchange Bybaz", 80, ["The Block", "Decrypt"], topic="hack")
    topic = _topic(db, SANOFI)
    before = (events._newsworthy(db, ev, "⚡ JUST IN", SLOT), await _suggest(db, topic))

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("gate bug")

    monkeypatch.setattr(gate, "observe", boom)  # even a gate that raises past its own guard: the hook's try/except holds
    after = (events._newsworthy(db, ev, "⚡ JUST IN", SLOT), await _suggest(db, topic))
    assert before == after
    assert db.query(TtPost).count() == 0


def test_shadow_job_registered_only_when_the_shadow_is_on(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.jobs import build_jobs

    names = lambda: {spec.name for spec in build_jobs(client.app.state.runtime)}  # noqa: E731
    assert "intel.shadow" not in names() and "intel.tick" not in names()
    monkeypatch.setattr(get_settings(), "intel_shadow_enabled", True)
    assert "intel.shadow" in names()
    monkeypatch.setattr(get_settings(), "intel_enabled", True)  # then intel.tick refreshes the shadow itself
    assert "intel.tick" in names() and "intel.shadow" not in names()
