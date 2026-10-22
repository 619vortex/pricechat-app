"""Kiwi Brain 2.0: rule-by-rule checks, shadow evaluations, health per component, the order-flow recorder, Teach Your Kiwi
(versioned filters applied to AUTO), group announcements, replay and Ask Kiwi's grounding. TEST FIXTURES only."""

import asyncio
import time
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.routing import ScheduledJob
from app.models.trading import TrIntent
from app.models.tradingos import KbEvent, TsSignal
from app.tradingos import bot, brainx, execution, market, signals
from tests.test_trading_execution import active_signal
from tests.test_trading_os import FakeMD, flat, kiwi, uptrend_pullback


class MD(FakeMD):
    """FakeMD plus the raw endpoints the flow recorder and replay read."""

    trades: list[dict[str, Any]] = []

    def mids(self) -> tuple[float, dict[str, float]]:
        return time.time(), {c: cs[-1].c for c, cs in self.series.items()}

    def _post(self, body: dict[str, Any]) -> Any:
        coin = body.get("coin") or body.get("req", {}).get("coin")
        px = self.series[coin][-1].c
        if body["type"] == "l2Book":
            return {"levels": [[{"px": str(px - i), "sz": "2", "n": 1} for i in range(1, 6)], [{"px": str(px + i), "sz": "1", "n": 1} for i in range(1, 6)]]}
        if body["type"] == "recentTrades":
            return self.trades
        if body["type"] == "candleSnapshot":
            return [{"t": c.t, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v} for c in self.series[coin][-150:]]
        raise AssertionError(body)


@pytest.fixture
def md(monkeypatch: pytest.MonkeyPatch) -> MD:
    f = MD({"BTC": uptrend_pullback(), "SOL": uptrend_pullback(), "FLAT": flat()})
    monkeypatch.setattr(market, "_shared", f)
    monkeypatch.setattr(brainx, "RECORDER_ENABLED", False)
    return f


def _login(client: Any, db: Session, m: Any) -> None:
    from app.members import service as members

    client.cookies.set("pc_session", members.new_session(db, m, 30))


def test_every_rule_is_recorded_with_its_value_and_failures_match_the_reasons(md: MD) -> None:
    a = signals.analyze(md, "FLAT")
    assert a.decision == "NO TRADE" and a.checks
    failed = [c for c in a.checks if c["ok"] is False]
    assert failed and all(c["node"] in brainx.NODES and c["value"] for c in a.checks)
    assert len(failed) == len(a.reasons)  # each failed rule is one NO TRADE reason
    ok = signals.analyze(md, "SOL")
    assert ok.decision in ("LONG", "SHORT") and all(c["ok"] is not False for c in ok.checks)


def test_strategies_off_means_shadow_evaluations_logged_and_nothing_created(db: Session, md: MD) -> None:
    s = signals.settings(db)
    s.strategies, s.allowed_coins = {"trend_pullback_v1": False, "breakout_4h_v1": False}, "SOL,FLAT"
    db.commit()
    out = signals.scan(db, md)
    assert out["shadow"] and out["created"] == [] and db.scalar(select(TsSignal.id)) is None  # never a signal
    evs = db.scalars(select(KbEvent).where(KbEvent.kind == "strategy")).all()
    assert len(evs) == 4 and all(e.data["shadow"] and e.data["checks"] and "(shadow)" in e.title for e in evs)
    assert any(e.coin == "SOL" and e.data["decision"] in ("LONG", "SHORT") for e in evs)  # a would-be trade, recorded only
    old = evs[0]
    old.at = utcnow() - timedelta(days=8)
    db.commit()
    from app.tradingos import brain

    assert brain.prune(db) == 1  # shadow evaluations are kept 7 days


def test_overview_separates_health_components_and_verifies_protection(client: Any, db: Session, md: MD) -> None:
    assert client.get("/api/members/kiwi-brain/overview").status_code == 401
    s = signals.settings(db)
    s.strategies = {}
    for name, every in (("tradingos.scan", 900), ("tradingos.track", 60)):
        j = db.get(ScheduledJob, name) or ScheduledJob(name=name, interval_seconds=every)
        j.enabled, j.last_status, j.last_run_at, j.last_finished_at = True, "success", utcnow(), utcnow()
        j.next_run_at = utcnow() + timedelta(seconds=every)
        db.add(j)
    db.commit()
    sig = active_signal(db, md)
    a, b = kiwi(db, 4, "611"), kiwi(db, 4, "612")
    execution.confirm(db, a, execution.preview(db, a, sig).token)
    _login(client, db, a)
    o = client.get("/api/members/kiwi-brain/overview").json()
    h = o["health"]
    assert h["engine"]["state"] == "shadow" and "Shadow mode" in h["engine"]["reason"] and h["engine"]["next_at"]
    assert h["market_feed"]["state"] == "live" and h["exchange"]["state"] == "paper" and h["monitoring"]["state"] == "ok"
    p = o["positions"][0]
    assert p["strategy"] == "trend_pullback_v1" and p["protection"]["stop"] == "SIMULATED" and p["protected"] and p["loss_at_stop"] > 0
    assert "shadow mode" in p["managed"] and o["exposure"]["loss_at_stop"] == p["loss_at_stop"]
    _login(client, db, b)
    assert client.get("/api/members/kiwi-brain/overview").json()["positions"] == []  # never another member's
    ev = client.get("/api/members/kiwi-brain/evaluate?coin=SOL").json()
    assert set(ev["nodes"]) == set(brainx.NODES) and ev["nodes"]["signal"]["status"] == "pass" and ev["nodes"]["risk"]["status"] == "pass"
    assert ev["nodes"]["risk"]["output"].startswith("approved (dry run") and db.scalar(select(TrIntent.id).where(TrIntent.account_id != 0)) is not None


def test_flow_recorder_returns_only_what_was_recorded(db: Session, md: MD) -> None:
    now = int(time.time() * 1000)
    md.trades = [{"coin": "BTC", "side": "B", "px": "100", "sz": "3", "time": now, "tid": 1},
                 {"coin": "BTC", "side": "A", "px": "100", "sz": "1", "time": now, "tid": 2}]
    brainx._flows.clear()
    f = brainx.flow(db, md, "BTC")
    assert f["samples"] == 1 and f["trades_recorded"] == 2 and f["volume"][-1]["cvd"] == pytest.approx(200.0)  # +300 bought, -100 sold
    assert f["large_trades"][0]["usd"] == 300 and f["imbalance"] is not None and f["liquidations"]["available"] is False
    brainx.sample(md, "BTC")  # the same trades again: counted once
    assert brainx.flow(db, md, "BTC")["trades_recorded"] == 2


def test_teach_your_kiwi_versions_rollback_and_auto_filter(client: Any, db: Session, md: MD) -> None:
    m = kiwi(db, 4, "621")
    with pytest.raises(brainx.ConfigError):
        brainx.validate_config({"strategies": {"trend_pullback_v1": True}, "leverage": 50})  # unknown fields refused
    with pytest.raises(brainx.ConfigError):
        brainx.validate_config({"min_rr": 99})
    _login(client, db, m)
    assert client.put("/api/members/kiwi-brain/config", json={"config": {"sides": "short"}, "note": "shorts only"}).json()["version"] == 1
    assert client.put("/api/members/kiwi-brain/config", json={"config": {"sides": "both", "coins": ["eth"]}}).json()["config"]["coins"] == ["ETH"]
    assert client.post("/api/members/kiwi-brain/config/rollback", json={"version": 1}).json()["config"]["sides"] == "short"  # v3 = v1
    assert [v["version"] for v in client.get("/api/members/kiwi-brain/config").json()["versions"]] == [3, 2, 1]
    sig = active_signal(db, md)  # a LONG
    acc = execution.account(db, m)
    acc.auto_enabled = True
    db.commit()
    assert execution.run_auto(db, sig) == []  # shorts-only config: skipped, said why
    it = db.scalar(select(TrIntent).where(TrIntent.account_id == acc.id))
    assert it.status == "SKIPPED" and "shorts only" in it.plan["skipped"]
    imp = client.post("/api/members/kiwi-brain/config/preview", json={"config": {"sides": "both"}}).json()["impact"]
    assert imp["allowed_before"] == 0 and imp["allowed_after"] == 1


def test_group_announcements_for_open_and_close(db: Session, md: MD) -> None:
    from tests.test_trading_os import FakeTG

    sig = active_signal(db, md)
    sig.tg_chat, sig.tg_message_id, sig.last_price = "-100", 42, sig.entry_hi
    assert "is OPEN" in bot.announcement(sig, "WAITING")
    signals.settle(sig, "STOP_HIT", sig.stop)
    text = bot.announcement(sig, "ACTIVE")
    assert "STOP HIT" in text and "Signal result" in text and "no leverage, after fees" in text
    tg = FakeTG()
    assert asyncio.run(bot.announce(db, tg, sig, "ACTIVE")) and tg.sent[-1][0] == "msg"
    chats = [m[1] for m in tg.sent]
    assert chats == ["-100", "@thepricechat"]  # the signal group AND the main chat, both from one announcement
    assert tg.sent[0][2] in tg.sent[1][2]
    sig.status = "WAITING"
    assert bot.announcement(sig, "WAITING") is None  # no change, no message


def test_replay_and_ask_kiwi_grounding(client: Any, db: Session, md: MD, monkeypatch: pytest.MonkeyPatch) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "631")
    execution.confirm(db, m, execution.preview(db, m, sig).token)
    _login(client, db, m)
    r = client.get(f"/api/members/kiwi-brain/replay?signal_id={sig.id}").json()
    assert r["candles"] and r["report"]["entry"] and any(e["kind"] == "risk" for e in r["events"])

    async def fake(router: Any, task: str, system: str, user: str, cls: Any, retries: int = 1) -> Any:
        assert task == "kiwi.ask" and "[S]" in user and "[MBTC]" in user and "ONLY the evidence" in system
        out = cls(answer="Your stop is set [P1] and I made up [E999999].", cited=["P1", "E999999", "nope"], sufficient=True,
                  proposal={"kind": "config", "changes": {"sides": "long"}, "summary": "longs only"})

        class R:
            model = "fake"
        return out, R(), R()

    import app.ai.structured as st

    monkeypatch.setattr(st, "complete_structured", fake)
    a = client.post("/api/members/kiwi-brain/ask", json={"question": "what is my risk?"}).json()
    assert a["cited"] == ["P1"]  # ids that aren't in the evidence are dropped
    assert a["proposal"]["kind"] == "config" and a["proposal"]["changes"] == {"sides": "long"}  # proposed, not applied
    assert client.get("/api/members/kiwi-brain/config").json()["version"] == 0


def test_ask_kiwi_without_an_ai_model_answers_common_questions_from_records(client: Any, db: Session, md: MD, monkeypatch: pytest.MonkeyPatch) -> None:
    sig = active_signal(db, md)
    m = kiwi(db, 4, "641")
    execution.confirm(db, m, execution.preview(db, m, sig).token)
    _login(client, db, m)

    async def offline(*a: Any, **k: Any) -> Any:
        raise ConnectionError("model offline")

    import app.ai.structured as st

    monkeypatch.setattr(st, "complete_structured", offline)
    r = client.post("/api/members/kiwi-brain/ask", json={"question": "Why didn't you short FLAT?"}).json()
    assert "NO TRADE" in r["answer"] and "Failing" in r["answer"] and r["model"].startswith("direct")
    r = client.post("/api/members/kiwi-brain/ask", json={"question": "what is my current risk exposure?"}).json()
    assert "If every stop is hit" in r["answer"] and any(c.startswith("P") for c in r["cited"])
    res = client.post("/api/members/kiwi-brain/ask", json={"question": "write me a poem"})
    assert res.status_code == 503 and "Without it Kiwi can answer" in res.json()["detail"]  # never a made-up answer


def test_main_chat_notices_are_independent_and_can_be_switched_off(db: Session, md: MD) -> None:
    from tests.test_trading_os import FakeTG

    sig = active_signal(db, md)
    sig.tg_chat, sig.tg_message_id, sig.last_price = "@botpricechat", 42, sig.entry_hi

    class MainFails(FakeTG):
        async def send_message(self, chat_id: Any, text: str, buttons: Any = None, html: bool = False, reply_to: Any = None) -> dict[str, Any]:
            if chat_id == "@thepricechat":
                raise RuntimeError("bot can't post there")
            return await super().send_message(chat_id, text, buttons, html, reply_to)

    tg = MainFails()
    assert asyncio.run(bot.announce(db, tg, sig, "WAITING"))  # the signal group still gets it
    assert [m[1] for m in tg.sent] == ["@botpricechat"]
    ok = FakeTG()
    asyncio.run(bot.announce(db, ok, sig, "WAITING"))
    assert "Signal card: https://t.me/botpricechat/42" in ok.sent[1][2]  # the main chat links back to the card
    signals.settings(db).main_chat = ""
    db.commit()
    off = FakeTG()
    asyncio.run(bot.announce(db, off, sig, "WAITING"))
    assert [m[1] for m in off.sent] == ["@botpricechat"]
    signals.settings(db).main_chat = "@thepricechat"
    sig.status, sig.published_at = "EXPIRED", sig.published_at or utcnow()
    exp = FakeTG()
    asyncio.run(bot.announce(db, exp, sig, "WAITING"))
    assert [m[1] for m in exp.sent] == ["@botpricechat"]  # expired / burnt stays in the signal group
