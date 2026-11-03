"""Signal desk: a signal's track record, real money kept apart from practice, and discussing it on the chart. TEST FIXTURES only."""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.trading import TrPosition
from app.tradingos import execution, performance, signals
from tests.test_kiwi_brain2 import MD, _login, md  # noqa: F401
from tests.test_trading_execution import active_signal
from tests.test_trading_os import kiwi


def closed_signal(db: Session, md: MD, strategy: str, pnl: float, tf: str = "1h") -> Any:  # noqa: F811
    s = active_signal(db, md)
    s.strategy, s.timeframe, s.status, s.model_pnl_pct, s.closed_at = strategy, tf, ("TARGET_HIT" if pnl > 0 else "STOP_HIT"), pnl, utcnow()
    db.commit()
    return s


def position(db: Session, sig: Any, member: Any, mode: str, status: str = "OPEN", size: float = 1.0, entry: float | None = 200.0) -> TrPosition:
    p = TrPosition(account_id=execution.account(db, member).id, signal_id=sig.id, mode=mode, coin=sig.coin, side=sig.side, status=status, size=size,
                   entry_px=entry, leverage=2, stop=sig.stop, tp1=sig.target, tp2=sig.target, max_loss_usd=5, via="enter")
    db.add(p)
    db.commit()
    return p


def test_record_counts_losers_after_costs_and_says_when_too_few(db: Session, md: MD) -> None:  # noqa: F811
    for pnl in (2.0, -1.0, -1.0):
        closed_signal(db, md, "trend_pullback_v1", pnl)
    closed_signal(db, md, "breakout_4h_v1", 5.0, "4h")  # another setup is not this setup's record
    closed_signal(db, md, "test", 9.0)  # the owner's pipeline checks never count
    active_signal(db, md).strategy = "trend_pullback_v1"  # still open: no result yet
    db.commit()
    rec = performance.track_record(db)
    r = rec[("trend_pullback_v1", "1h")]
    assert (r["closed"], r["wins"], r["losses"], r["win_rate"]) == (3, 1, 2, 33.3)
    assert r["avg_pct_after_costs"] < 0  # 0.0 before slippage: costs are charged
    assert r["enough"] is False and "not executed trades" in r["label"]
    assert not any(k[0] == "test" for k in rec)
    assert "too few to judge" in performance.record_line(r)
    assert performance.record_line({**r, "enough": True}).startswith("📊 This setup so far: 1W 2L")
    assert performance.record_line(None) is None


def test_real_money_and_practice_are_counted_apart_and_small_totals_stay_hidden(db: Session, md: MD) -> None:  # noqa: F811
    sig = active_signal(db, md)
    position(db, sig, kiwi(db, 4, "801"), "LIVE")
    position(db, sig, kiwi(db, 4, "802"), "LIVE")
    position(db, sig, kiwi(db, 4, "803"), "PAPER")
    position(db, sig, kiwi(db, 4, "804"), "TESTNET")
    position(db, sig, kiwi(db, 4, "805"), "LIVE", status="FAILED", size=0)  # never filled
    n = signals.participation(db, sig)
    assert (n["live"], n["practice"]) == (2, 2) and "live_open_usd" not in n  # two holders: a total would nearly show a size
    position(db, sig, kiwi(db, 4, "806"), "LIVE", size=1.26)
    n = signals.participation(db, sig)
    assert n["live"] == 3 and n["live_open_usd"] == 700  # 652 rounded to the nearest 100
    position(db, sig, kiwi(db, 4, "807"), "LIVE", status="CLOSED", size=0)
    n = signals.participation(db, sig)
    assert n["live"] == 4 and n["live_open_usd"] == 700  # closed: was in, no longer open
    assert signals.crowd_line(n) == "👥 4 Kiwis with real money (≈$700 open) · 2 practising"
    assert signals.crowd_line({"practice": 1}) == "👥 1 practising"
    assert signals.crowd_line({"live": 0, "practice": 0}) is None
    assert "2 practising" in signals.card(sig, n)


def test_members_feed_carries_each_signals_own_record(client: Any, db: Session, md: MD) -> None:  # noqa: F811
    closed_signal(db, md, "trend_pullback_v1", 2.0)
    now = active_signal(db, md)
    now.strategy = "trend_pullback_v1"
    other = active_signal(db, md)  # strategy "test" (or a setup with no history): no record shown
    other.strategy = "breakout_4h_v1"
    db.commit()
    items = {x["id"]: x for x in client.get("/api/members/trading-os/signals").json()["items"]}
    assert items[now.id]["record"]["closed"] == 1 and items[other.id]["record"] is None
    assert items[now.id]["participation"]["live"] == 0


def test_discussing_a_signal_grounds_the_model_and_works_without_it(client: Any, db: Session, md: MD, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    import app.ai.structured as st
    from app.api.routes.kiwibrain import _Ask

    sig = active_signal(db, md)
    _login(client, db, kiwi(db, 4, "811"))
    seen: dict[str, str] = {}

    class R:
        model = "fake"

    async def fake(_router: Any, _task: str, system: str, prompt: str, _schema: Any) -> Any:
        seen.update(system=system, prompt=prompt)
        return _Ask(answer="Stop sits below the pullback [SIG].", cited=["SIG", "NOPE"], sufficient=True,
                    proposal={"kind": "limits", "changes": {"paused": True}, "summary": "pause"}), R(), R()

    monkeypatch.setattr(st, "complete_structured", fake)
    body = {"question": "What would prove this wrong?", "signal_id": sig.id, "history": [{"who": "me", "text": "hi kiwi"}]}
    a = client.post("/api/members/kiwi-brain/ask", json=body).json()
    assert a["cited"] == ["SIG"] and a["proposal"] is None  # invented citations dropped; no settings changes from the desk
    assert "never state a probability" in seen["system"] and "Member: hi kiwi" in seen["prompt"]
    for tag in ("[SIG]", "[REC]", "[CROWD]", "[CHART]"):
        assert tag in seen["prompt"]

    async def offline(*a: Any, **k: Any) -> Any:
        raise ConnectionError("model offline")

    monkeypatch.setattr(st, "complete_structured", offline)
    a = client.post("/api/members/kiwi-brain/ask", json=body).json()
    assert "AI is offline" in a["answer"] and f"Signal #{sig.number:03d}" in a["answer"] and a["model"].startswith("direct")

    sig.published_at = None
    db.commit()
    assert client.post("/api/members/kiwi-brain/ask", json=body).status_code == 404  # only published signals
    assert client.post("/api/members/kiwi-brain/ask", json={**body, "history": [{"who": "me", "text": "x"}] * 7, "signal_id": None}).status_code == 422
