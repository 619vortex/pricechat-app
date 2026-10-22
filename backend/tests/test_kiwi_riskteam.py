"""Kiwi Risk Team: the desks that can only veto a kitchen setup (correlation, loss streak, daily loss, volatility shock), the
edge desk that always says whether there's a proven edge, the kitchen check, and the AI Chief Risk Officer's memo (used only
when every number in it is in the evidence; otherwise, or without a model, the rules' own memo). No network."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingos import KbEvent, TsKitchen, TsSignal
from app.tradingos import bot, kitchen, riskteam

from tests.test_kiwi_kitchen import BASE, KitchenMD, at, kitchen_on, no_ml_model, setups  # noqa: F401 (fixtures)

CFG = dict(riskteam.DEFAULTS)


def ctx(open_: list[tuple[int, str, str]] | None = None, closed: list[tuple[float, float]] | None = None) -> riskteam.Context:
    """TEST FIXTURE: closed = (hours ago, R)."""
    now = utcnow()
    return riskteam.Context(now, open_ or [], [(now - timedelta(hours=h), r) for h, r in closed or []])


def votes(rv: dict[str, Any]) -> dict[str, str]:
    return {v["desk"]: v["vote"] for v in rv["votes"]}


def test_a_clean_book_is_approved_but_the_edge_desk_always_warns() -> None:
    rv = riskteam.review(ctx(), CFG, "BTC", "LONG", 1.0, 1.0)
    assert rv["verdict"] == "APPROVE" and votes(rv) == {"exposure": "approve", "streak": "approve", "daily": "approve", "shock": "approve", "edge": "warn"}
    edge = next(v for v in rv["votes"] if v["desk"] == "edge")["detail"]
    assert "no proven edge" in edge and "-0.121R" in edge and "no live record yet" in edge
    assert riskteam.check(CFG, rv) == ("pass", rv["reason"]) and "approved by 4 of 5 desks" in rv["reason"]


def test_correlation_desk_stops_a_second_bet_on_the_same_side() -> None:
    rv = riskteam.review(ctx([(12, "ETH", "LONG")]), CFG, "BTC", "LONG", 1.0, 1.0)
    assert rv["verdict"] == "VETO" and rv["reason"].startswith("Correlation desk: #012 ETH LONG is already open")
    assert riskteam.review(ctx([(12, "ETH", "SHORT")]), CFG, "BTC", "LONG", 1.0, 1.0)["verdict"] == "APPROVE"
    assert riskteam.review(ctx([(12, "ETH", "LONG")]), {**CFG, "same_side": False}, "BTC", "LONG", 1.0, 1.0)["verdict"] == "APPROVE"
    assert riskteam.check(CFG, riskteam.review(ctx([(12, "ETH", "LONG")]), CFG, "BTC", "LONG", 1.0, 1.0))[0] == "fail"


def test_loss_streak_pauses_then_lets_go() -> None:
    four = [(5, -1.0), (4, -1.0), (3, -0.4), (2, -1.0)]
    rv = riskteam.review(ctx(closed=four), CFG, "BTC", "LONG", 1.0, 1.0)
    assert rv["verdict"] == "VETO" and "4 losing signals in a row" in rv["reason"] and "4.0 h left" in rv["reason"]
    late = [(h + 5, r) for h, r in four]  # the last loss was 7 h ago: the 6 h pause is over
    assert votes(riskteam.review(ctx(closed=late), CFG, "BTC", "LONG", 1.0, 1.0))["streak"] == "approve"
    broken = [*four[:3], (2, 0.8)]
    assert votes(riskteam.review(ctx(closed=broken), CFG, "BTC", "LONG", 1.0, 1.0))["streak"] == "approve"


def test_daily_loss_desk_and_volatility_desk() -> None:
    c = ctx(closed=[(0.5, -1.6), (0.2, -1.5)])
    c.closed = [(c.now - timedelta(minutes=20), -1.6), (c.now - timedelta(minutes=10), -1.5)]  # both today
    rv = riskteam.review(c, CFG, "BTC", "LONG", 1.0, 1.0)
    assert rv["verdict"] == "VETO" and rv["reason"].startswith("Daily-loss desk: today's kitchen signals -3.10R")
    rv = riskteam.review(ctx(), CFG, "BTC", "LONG", 3.5, 1.0)
    assert rv["verdict"] == "VETO" and "3.5 ATR" in rv["reason"] and rv["reason"].startswith("Volatility desk")


def test_signal_r_is_the_result_over_the_stop_distance() -> None:
    sig = SimpleNamespace(model_pnl_pct=2.0, entry_lo=99.0, entry_hi=101.0, stop=99.0)  # 1% stop, +2%
    assert riskteam.signal_r(sig) == 2.0
    assert riskteam.signal_r(SimpleNamespace(model_pnl_pct=None, entry_lo=1, entry_hi=1, stop=0.5)) is None


def test_the_kitchen_burns_a_setup_the_team_vetoes(db: Session, setups: dict[str, Any]) -> None:  # noqa: F811
    kitchen_on(db, publish=True, mode="degen")
    db.add(TsSignal(number=1, coin="ETH", side="LONG", timeframe="15m", strategy=kitchen.STRATEGY, entry_lo=1, entry_hi=2, stop=0.5,
                    target=3, lev_lo=1, lev_hi=3, rr=1, status="ACTIVE", expires_at=utcnow()))
    db.commit()
    md = KitchenMD(BASE + 60_000)
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "BURNT"}
    k = db.scalar(select(TsKitchen).where(TsKitchen.coin == "BTC")).data  # type: ignore[union-attr]
    assert k["reason"].startswith("AI Risk Team: Correlation desk: #001 ETH LONG")
    assert k["risk_team"]["verdict"] == "VETO" and any(c["id"] == "risk_team" for c in k["risk"])
    assert db.scalar(select(TsSignal).where(TsSignal.coin == "BTC")) is None  # nothing proposed


def test_an_approved_dish_carries_the_team_onto_its_signal_and_card(db: Session, setups: dict[str, Any]) -> None:  # noqa: F811
    kitchen_on(db, publish=True, mode="degen")
    md = KitchenMD(BASE + 60_000)
    assert kitchen.tick(db, md, at(md.now_ms))["changed"] == {"BTC": "READY"}
    sig = db.scalar(select(TsSignal).where(TsSignal.coin == "BTC"))
    assert sig and sig.analysis["risk_team"]["verdict"] == "APPROVE"
    sig.status = "ACTIVE"
    text = bot.kitchen_announcement(sig, "WAITING")
    assert text and "🛡️ AI Risk Team: approved by 4 of 5 desks" in text


class FakeRouter:
    """TEST FIXTURE: a model that answers with `memo` (or raises)."""

    def __init__(self, memo: str | None) -> None:
        self.memo, self.calls = memo, 0

    async def complete(self, task: str, messages: Any, response_schema: Any = None, timeout_seconds: Any = None) -> Any:
        self.calls += 1
        assert task == "kiwi.risk_memo" and "Correlation desk" in messages[1].content
        if self.memo is None:
            raise RuntimeError("no model")
        return SimpleNamespace(content='{"memo": "%s"}' % self.memo), SimpleNamespace(model="fake-cro")


def vetoed_setup(db: Session) -> None:
    kitchen_on(db, publish=True, mode="degen")
    db.add(TsSignal(number=1, coin="ETH", side="LONG", timeframe="15m", strategy=kitchen.STRATEGY, entry_lo=1, entry_hi=2, stop=0.5,
                    target=3, lev_lo=1, lev_hi=3, rr=1, status="ACTIVE", expires_at=utcnow()))
    db.commit()
    md = KitchenMD(BASE + 60_000)
    kitchen.tick(db, md, at(md.now_ms))


def memo_events(db: Session) -> list[KbEvent]:
    return [e for e in db.scalars(select(KbEvent).where(KbEvent.source == "kitchen")) if e.title.startswith("🛡️")]


def test_the_cro_memo_is_used_when_grounded_once_per_setup(db: Session, setups: dict[str, Any]) -> None:  # noqa: F811
    vetoed_setup(db)
    r = FakeRouter("The team blocked BTC LONG because #001 ETH LONG is already open, and the backtest shows -0.121R a trade.")
    assert asyncio.run(riskteam.write_memos(db, r)) == {"written": 1, "by_model": 1}
    [e] = memo_events(db)
    assert e.title.startswith("🛡️ BTC risk team vetoed LONG: The team blocked") and e.data["kitchen"]["risk_team"]["memo"]["by"] == "fake-cro"
    row = db.scalar(select(TsKitchen).where(TsKitchen.coin == "BTC"))
    assert row and row.data["risk_team"]["memo"]["text"].startswith("The team blocked")
    assert asyncio.run(riskteam.write_memos(db, r)) == {"written": 0, "by_model": 0} and r.calls == 1  # once per setup


def test_an_invented_number_or_no_model_falls_back_to_the_rules_memo(db: Session, setups: dict[str, Any]) -> None:  # noqa: F811
    vetoed_setup(db)
    assert asyncio.run(riskteam.write_memos(db, FakeRouter("BTC could drop 40% tonight."))) == {"written": 1, "by_model": 0}
    [e] = memo_events(db)
    memo = e.data["kitchen"]["risk_team"]["memo"]
    assert memo["by"] == "rules" and memo["text"].startswith("The risk team blocked BTC LONG. Correlation desk") and "40%" not in memo["text"]


def test_no_model_still_gets_a_memo(db: Session, setups: dict[str, Any]) -> None:  # noqa: F811
    vetoed_setup(db)
    assert asyncio.run(riskteam.write_memos(db, FakeRouter(None)))["written"] == 1
    assert memo_events(db)[0].data["kitchen"]["risk_team"]["memo"]["by"] == "rules"


def test_grounded_only_accepts_numbers_from_the_evidence() -> None:
    ev = "today -3.10R from 2 closed; backtest -0.121R"
    assert riskteam.grounded("Down -3.10R today after 2 losses; backtest -0.121R.", ev)
    assert not riskteam.grounded("Down 5R today.", ev)
    assert riskteam.grounded("No numbers here.", ev)


def test_the_owner_can_switch_the_team_off(db: Session, setups: dict[str, Any]) -> None:  # noqa: F811
    s = kitchen.signals.settings(db)
    s.strategies = {**(s.strategies or {}), "risk_desk": {"enabled": False, "nonsense": 1}}
    db.commit()
    cfg = kitchen.config(s)
    assert cfg["risk_desk"]["enabled"] is False and "nonsense" not in cfg["risk_desk"]
    assert riskteam.check(cfg["risk_desk"], None) == ("skip", "off (owner setting)")
