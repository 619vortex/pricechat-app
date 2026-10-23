"""Kiwi AI Investment Committee runs end to end (no network): a full committee on a fake LLM, WAIT with both scenarios,
conflicting evidence, a missing section, stale data, the risk engine overruling the AI, grounding, provider failures."""

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from app.committee import grounding, orchestrator, roles
from app.committee.llm import Backend, LLMError
from app.models.committee import CmRun
from app.models.tradingos import TsSettings
from tests.committee_fixtures import TIMEOUT, FakeLLM, btc, conflicting, doge, no_news, okx_spot, stale

WHO = {"tg_user_id": 111}


def run(db: Session, ev: dict[str, Any], backend: Backend | None = None, depth: str = "full") -> dict[str, Any]:
    r = asyncio.run(orchestrator.run_committee(db, ev["asset"]["symbol"], "1h", requested_by=WHO, evidence=ev,
                                               backend=backend or Backend(), depth=depth))
    return r.result


def test_btc_full_committee_with_a_fake_llm(db: Session) -> None:
    llm = FakeLLM()
    res = run(db, btc(), llm)
    assert res["status"] == "done" and res["id"].startswith("cm_")
    assert [c.role for c in llm.calls][:4] == ["technical analyst", "fundamental analyst", "news analyst", "sentiment analyst"]
    assert len(llm.calls) == 11 <= 12  # 4 analysts, bull, bear, rebuttals, manager, trader, risk panel, portfolio manager
    assert res["meta"]["calls"] == 11 and res["meta"]["tokens_in"] == 1100 and res["meta"]["cost_usd"] == 0.0
    assert res["meta"]["models"] == {"fast": "fake-fast", "deep": "fake-deep", "synthesis": "fake-synth"}
    assert res["meta"]["framework"] == "TradingAgents v0.7.0 pattern (ported)" and res["meta"]["prompt_version"] == "cm-1"
    assert all(a["author"] == "llm:fake-fast" for a in res["analysts"].values())
    assert res["analysts"]["technical"]["points"][0]["refs"] == ["T1"]  # an unknown ref id ("ZZ9") is dropped
    assert res["debate"]["rounds"] == 1 and res["debate"]["bull"]["rebuttal"] and res["debate"]["manager"]["rating"] == "Overweight"
    assert res["trader"]["action"] == "LONG"
    plan = res["trader"]["plan"]
    assert plan["entry_zone"] == [67720.0, 67880.0] and plan["stop"] == 66200.0 and plan["targets"] == [68900.0]
    assert "ATR14 1h 320" in plan["derivation"] and "invalidation 66200" in plan["derivation"]
    assert res["verdict"]["action"] == "LONG" and res["verdict"]["rating"] == "Overweight"
    assert res["verdict"]["scenarios"]["long"]["trigger"] == 67800.0 and res["verdict"]["scenarios"]["short"]["trigger"] == 66200.0
    ex = res["execution"]
    assert ex["status"] == "APPROVED", ex["reasons"]
    assert ex["engine"] == "kiwi-risk" and ex["note"] == "Research only — nothing was traded."
    names = [c["name"] for c in ex["checks"]]
    assert "risk engine sizing (risk.plan_trade)" in names and "Account exposure" in names and any(n.startswith("risk team:") for n in names)
    assert res["meta"]["degraded"] == []
    row = db.query(CmRun).one()
    assert (row.status, row.verdict, row.rating, row.exec_status, row.calls, row.venue) == ("done", "LONG", "Overweight", "APPROVED", 11, "hyperliquid")
    assert "111" not in str(res)  # the public JSON never carries who asked


def test_quick_depth_skips_the_rebuttals(db: Session) -> None:
    llm = FakeLLM()
    res = run(db, btc(), llm, depth="quick")
    assert len(llm.calls) == 10 and res["debate"]["bull"]["rebuttal"] == ""


def test_doge_wait_with_both_scenarios_and_the_engines_reasons(db: Session) -> None:
    llm = FakeLLM({"research manager": {"rating": "Hold", "summary": "Balanced: range-bound."},
                   "trader": {"action": "WAIT", "rationale": "Neither trigger has fired."},
                   "portfolio manager": {"action": "WAIT", "rating": "Hold", "summary": "Wait for a break of the range.",
                                         "what_changes_view": ["1h close above resistance 0.158", "1h close below support 0.148"]}})
    res = run(db, doge(), llm)
    v = res["verdict"]
    assert v["action"] == "WAIT" and v["rating"] == "Hold" and res["trader"]["plan"] is None
    assert v["scenarios"]["long"]["trigger"] == 0.158 and v["scenarios"]["long"]["invalidation"] == 0.148
    assert v["scenarios"]["short"]["trigger"] == 0.148 and v["scenarios"]["short"]["targets"] == [0.145]
    ex = res["execution"]
    assert ex["status"] == "NOT_APPLICABLE" and ex["reasons"][0] == "no trade proposed"
    # why no trade would be approved right now, from the real engine (risk.plan_trade): price outside each entry zone
    assert any(r.startswith("long scenario now:") and "outside the entry zone" in r for r in ex["reasons"])
    assert any(r.startswith("short scenario now:") and "outside the entry zone" in r for r in ex["reasons"])
    assert v["what_changes_view"] == ["1h close above resistance 0.158", "1h close below support 0.148"]
    assert res["meta"]["degraded"] == []


def test_rules_backend_doge_is_wait_too(db: Session) -> None:
    res = run(db, doge())
    assert res["meta"]["backend"] == "rules" and res["meta"]["calls"] == 0
    assert res["verdict"]["action"] == "WAIT" and res["verdict"]["author"] == "rules"
    assert "trend: 4h EMA20 below EMA50" in res["trader"]["rationale"] or "Hold" in res["trader"]["rationale"]
    assert res["execution"]["status"] == "NOT_APPLICABLE"


def test_conflicting_evidence_is_a_hold_and_both_sides_are_heard(db: Session) -> None:
    res = run(db, conflicting())
    assert res["analysts"]["technical"]["lean"] == "bullish" and res["analysts"]["news"]["lean"] == "bearish"
    assert "technical" in res["debate"]["bull"]["thesis"] and "news" in res["debate"]["bear"]["thesis"]
    assert res["debate"]["manager"]["rating"] == "Hold" and "conflicts" in res["debate"]["manager"]["summary"]
    assert res["verdict"]["action"] == "WAIT"
    assert res["debate"]["bull"]["rebuttal"] and res["debate"]["bear"]["rebuttal"]


def test_missing_news_section_is_reported_missing_not_filled_in(db: Session) -> None:
    llm = FakeLLM()
    res = run(db, no_news(), llm)
    n = res["analysts"]["news"]
    assert n["lean"] == "unknown" and n["author"] == "rules" and n["points"] == []
    assert n["limitations"] == ["all news feeds failed (timeout)"]
    assert "news analyst" not in [c.role for c in llm.calls]  # no call spent on an empty section
    assert any(m.startswith("news: unavailable") for m in res["debate"]["missing_info"])
    assert "evidence: news: all feeds failed" in res["meta"]["degraded"]


def test_stale_market_data_forces_wait_whatever_the_model_says(db: Session) -> None:
    llm = FakeLLM()  # it answers LONG
    res = run(db, stale(), llm)
    assert res["trader"]["action"] == "WAIT" and res["verdict"]["action"] == "WAIT"
    assert any("trader: LONG refused, market data is stale" in d for d in res["meta"]["degraded"])
    assert any("portfolio manager: LONG refused" in d for d in res["meta"]["degraded"])
    ex = res["execution"]
    assert ex["status"] in ("NOT_APPLICABLE", "REJECTED")
    assert any("stale" in r for r in ex["reasons"])


def test_kill_switch_rejects_and_the_ai_cannot_override_it(db: Session) -> None:
    db.add(TsSettings(paused=True))
    db.commit()
    res = run(db, btc(), FakeLLM())
    assert res["verdict"]["action"] == "LONG"  # research verdict and execution approval are separate
    ex = res["execution"]
    assert ex["status"] == "REJECTED"
    assert any("trading is paused for everyone" in r for r in ex["reasons"])
    assert next(c for c in ex["checks"] if c["name"].startswith("global kill switch"))["ok"] is False


def test_leverage_over_the_cap_is_rejected(db: Session) -> None:
    db.add(TsSettings(strategies={"committee": {"reference": {"leverage": 10, "max_leverage": 10}}}))
    db.commit()
    ex = run(db, btc(), FakeLLM())["execution"]
    assert ex["status"] == "REJECTED"
    lev = next(c for c in ex["checks"] if c["name"] == "leverage within caps")
    assert lev["ok"] is False and lev["value"] == "10x" and lev["limit"].startswith("5x")


def test_risk_team_veto_rejects(db: Session, monkeypatch: Any) -> None:
    from app.tradingos import riskteam

    real = riskteam.review

    def veto(ctx: Any, cfg: Any, coin: str, side: str, rng: float, atr: Any) -> dict[str, Any]:
        rv = real(ctx, cfg, coin, side, rng, atr)
        rv["votes"][0] = {**rv["votes"][0], "vote": "veto", "detail": "#007 ETH LONG is already open"}
        return {**rv, "verdict": "VETO", "reason": "Correlation desk: #007 ETH LONG is already open"}

    monkeypatch.setattr(riskteam, "review", veto)
    ex = run(db, btc(), FakeLLM())["execution"]
    assert ex["status"] == "REJECTED" and "Correlation desk: #007 ETH LONG is already open" in ex["reasons"]


def test_non_hyperliquid_market_is_informational_only(db: Session) -> None:
    res = run(db, okx_spot(), FakeLLM())
    assert res["execution"]["status"] == "NOT_APPLICABLE"
    assert "execution only on Hyperliquid perpetuals" in res["execution"]["reasons"]


def test_an_ungrounded_number_falls_back_to_rules_and_is_recorded(db: Session) -> None:
    llm = FakeLLM({"bull": {"thesis": "BTC goes to 91,250 next week.", "points": []},
                   "portfolio manager": {"action": "LONG", "rating": "Buy", "summary": "There is a 70% chance of 68,900.",
                                         "what_changes_view": []}})
    res = run(db, btc(), llm)
    assert res["debate"]["bull"]["author"] == "rules" and "91,250" not in res["debate"]["bull"]["thesis"]
    assert "bull: ungrounded number 91,250" in res["meta"]["degraded"]
    assert res["verdict"]["author"] == "rules" and "portfolio manager: probability language" in res["meta"]["degraded"]
    assert res["debate"]["bear"]["author"].startswith("llm:")  # only the offending roles fall back


def test_grounding_tolerance() -> None:
    g = grounding.Grounder({"p": 67123.5, "f": 0.0125, "frac": 0.008, "rule": "1h close above 67,800"})
    assert g.ungrounded("67,123.5 67124 67.1k 67k $67,800 0.0125% 0.8% 1.25% 15m 4h EMA 200 3x") == []
    assert g.ungrounded("67,000 and 67.2k and 0.014%") == ["67,000", "67.2k", "0.014%"]
    assert grounding.check("x", {"summary": "Plain words, 1h close above 67,800.", "refs": ["T99"]}, g) is None
    assert grounding.check("x", {"summary": "Odds favour bulls"}, g) == "x: probability language"


def test_provider_timeouts_mean_rules_and_the_run_still_finishes(db: Session) -> None:
    llm = FakeLLM({"*": TIMEOUT})
    res = run(db, btc(), llm)
    assert res["status"] == "done" and res["verdict"]["author"] == "rules"
    assert all(a["author"] == "rules" for a in res["analysts"].values())
    assert any("timeout after 90 s" in d for d in res["meta"]["degraded"])
    assert res["meta"]["calls"] == 11  # attempts are counted


def test_unreadable_and_crashing_answers_fall_back(db: Session) -> None:
    llm = FakeLLM({"trader": "not json at all", "risk panel": RuntimeError("boom"), "bear": LLMError("HTTP 500")})
    res = run(db, btc(), llm)
    d = res["meta"]["degraded"]
    assert "trader: unreadable model answer" in d and "risk panel: backend error (RuntimeError)" in d
    assert any(x.startswith("bear: HTTP 500") for x in d)
    assert res["trader"]["author"] == "rules" and res["risk_committee"]["author"] == "rules"


def test_plan_numbers_only_come_from_the_evidence() -> None:
    ev = btc()
    p = roles.plan_from(ev, "LONG")
    assert p is not None and p["stop"] == ev["sections"]["technical"]["facts"]["scenarios"]["long"]["invalidation"]
    ev["sections"]["technical"]["facts"]["scenarios"]["long"]["targets"] = [60000.0]  # inconsistent: target below the trigger
    assert roles.plan_from(ev, "LONG") is None
    assert roles.plan_from(btc(), "WAIT") is None
