import pytest
"""The owner's SIMULATED AUTO-TRADER (paper trading only). TEST FIXTURES only: the fake Charizard market from test_exchange."""

import random

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.exchange import auto, perps, views
from app.exchange.ledger import reconcile, system
from app.models.exchange import ExTrade, PerpTrade
from tests.test_exchange import _card, _market


def _setup(db: Session):  # type: ignore[no-untyped-def]
    _card(db)
    m = _market(db)
    perps.ensure_perps(db)
    return m


def test_off_by_default_does_nothing(db: Session) -> None:
    _setup(db)
    assert auto.tick(db) == {"enabled": False}
    assert (db.scalar(select(func.count()).select_from(ExTrade)) or 0) == 0


def test_on_it_trades_labelled_sim_within_the_swing_and_everything_balances(db: Session) -> None:
    m = _setup(db)
    auto.set_config(db, {"enabled": True, "activity": 12, "swing_pct": 2})
    rng = random.Random(7)
    for _ in range(25):
        auto.tick(db, rng=rng)
    bot = system(db, "auto").id
    spot = db.scalars(select(ExTrade).where(ExTrade.market_id == m.id)).all()
    assert len(spot) > 5 and all(bot in (t.buyer_account_id, t.seller_account_id) for t in spot)
    ref = m.ref_contract_cents
    assert all(abs(t.price_cents - ref) <= ref * 0.035 for t in spot)  # path within 2% + the maker's spread and levels
    assert all(t["simulated"] for t in views.trades(db, m))  # shown as SIM
    assert (db.scalar(select(func.count()).select_from(PerpTrade)) or 0) > 5
    assert views.market_out(db, m)["auto"] is True
    db.expire_all()
    assert reconcile(db)["ok"] and perps.check_invariants(db)["ok"]
    st = auto.status(db)
    assert st["trades_last_hour"]["spot"] > 0
    assert all(abs(x["path_bps"]) <= 200 for x in st["markets"])
    # switched off: the maker goes back to quoting around the real reference
    auto.set_config(db, {"enabled": False})
    assert auto.center(db, m.symbol, ref) == ref


def test_prune_removes_only_old_unfilled_system_quotes(db: Session) -> None:
    from datetime import timedelta

    from app.db.base import utcnow
    from app.exchange import maker
    from app.models.exchange import ExOrder, PerpOrder

    m = _setup(db)
    auto.set_config(db, {"enabled": True, "activity": 12})
    rng = random.Random(3)
    for _ in range(10):
        auto.tick(db, rng=rng)
    count = lambda q: db.scalar(select(func.count()).select_from(q.subquery())) or 0  # noqa: E731
    dead = lambda M: select(M.id).where(M.status == "cancelled", M.filled == 0)  # noqa: E731
    assert count(dead(ExOrder)) > 0 and count(dead(PerpOrder)) > 0
    trades, open_q = count(select(ExTrade.id)), count(select(ExOrder.id).where(ExOrder.status == "open"))
    assert maker.prune(db) == 0  # less than an hour old: kept
    assert maker.prune(db, utcnow() + timedelta(hours=2)) > 0
    assert count(dead(ExOrder)) == 0 and count(dead(PerpOrder)) == 0
    assert count(select(ExTrade.id)) == trades and count(select(ExOrder.id).where(ExOrder.status == "open")) == open_q
    db.expire_all()
    assert reconcile(db)["ok"] and perps.check_invariants(db)["ok"]
    auto.tick(db, rng=rng)  # still trades afterwards
    assert m.id


def test_bot_is_never_ranked(client: TestClient, db: Session) -> None:
    _setup(db)
    auto.set_config(db, {"enabled": True, "activity": 12})
    for _ in range(5):
        auto.tick(db, rng=random.Random(1))
    board = client.get("/api/members/exchange/leaderboard?board=equity").json()
    assert all("AUTO" not in str(r.get("username", "")) for r in board["rows"])


def test_admin_api(client: TestClient, db: Session) -> None:
    _setup(db)
    r = client.put("/api/exchange/admin/auto", json={"enabled": True, "activity": 99})
    assert r.status_code == 422  # out of range
    r = client.put("/api/exchange/admin/auto", json={"enabled": True, "activity": 6, "swing_pct": 1.5})
    assert r.status_code == 200 and r.json()["config"]["enabled"] is True and r.json()["config"]["activity"] == 6
    assert client.post("/api/exchange/admin/auto/tick").json()["enabled"] is True
    assert client.get("/api/exchange/admin/auto").json()["markets"][0]["symbol"]


@pytest.fixture(autouse=True)
def _exchange_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Paper Exchange is retired on the website (exchange_enabled off); its engine is still tested with it on."""
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "exchange_enabled", True)
