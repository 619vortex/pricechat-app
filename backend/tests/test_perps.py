"""Perpetuals (paper trading only). TEST FIXTURES only: the fake Charizard PSA 10 market from test_exchange."""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.exchange import admin, perps
from app.exchange.ledger import USDT, ExchangeError, account_for, balances, reconcile, system
from app.models.exchange import ExMarket, PerpLiquidation, PerpMarket, PerpOrder, PerpPosition, PerpTrade
from tests.test_exchange import _acct, _card, _market, _member


def _setup(db: Session) -> tuple[ExMarket, PerpMarket]:
    _card(db)
    ex = _market(db)  # reference ~ 35.00 per contract
    pms = perps.ensure_perps(db)
    pm = next(p for p in pms if p.ex_market_id == ex.id)
    perps.requote(db, pm)
    return ex, pm


def _ok(db: Session) -> None:
    db.expire_all()
    r = reconcile(db)
    assert r["ok"], r
    inv = perps.check_invariants(db)
    assert inv["ok"], inv


def _pos(db: Session, acct_id: int, pm: PerpMarket) -> PerpPosition:
    db.expire_all()
    return db.scalar(select(PerpPosition).where(PerpPosition.account_id == acct_id, PerpPosition.market_id == pm.id))


def test_long_with_leverage_then_close_realizes_pnl(db: Session) -> None:
    ex, pm = _setup(db)
    a = _acct(db, "longer")
    r = perps.place(db, a, pm, "buy", "market", 10, None, 10, "l1")
    assert r["fills"]
    p = _pos(db, a.id, pm)
    assert p.size == 10 and p.leverage == 10
    notional = sum(f["price"] * f["qty"] for f in r["fills"])
    assert abs(p.margin_cents - notional // 10) <= 10  # 10x: a tenth of the notional is margin
    assert balances(db, a.id)[USDT][1] == 0  # no leftover hold
    _ok(db)
    out = perps.close_position(db, a, pm, "close1")
    assert out["fills"] and _pos(db, a.id, pm).size == 0
    p = _pos(db, a.id, pm)
    assert p.margin_cents == 0 and p.realized_cents < 0  # bought the ask, sold the bid: the spread is a small loss
    assert sum(balances(db, a.id)[USDT]) > 1_000_000 - 2_000  # lost only the spread and fees
    _ok(db)


def test_short_needs_no_inventory_and_reduce_only_rules(db: Session) -> None:
    ex, pm = _setup(db)
    a = _acct(db, "shorter")
    perps.place(db, a, pm, "sell", "market", 5, None, 5, "s1")
    assert _pos(db, a.id, pm).size == -5  # shorting a perp needs margin, not contracts
    with pytest.raises(ExchangeError, match="Nothing to close"):
        perps.place(db, a, pm, "sell", "market", 1, None, 5, "s2", reduce_only=True)
    r = perps.place(db, a, pm, "buy", "market", 50, None, 5, "s3", reduce_only=True)  # capped to what's open
    assert sum(f["qty"] for f in r["fills"]) == 5 and _pos(db, a.id, pm).size == 0
    with pytest.raises(ExchangeError, match="Leverage is 1x to 10x"):
        perps.place(db, a, pm, "buy", "market", 1, None, 25, "s4")
    with pytest.raises(ExchangeError, match="Not enough paper USDT"):
        perps.place(db, a, pm, "buy", "limit", 1000, 3_600, 1, "s5")  # 36,000 of margin at 1x > 10,000
    _ok(db)


def test_flip_from_long_to_short_in_one_order(db: Session) -> None:
    ex, pm = _setup(db)
    a = _acct(db, "flipper")
    perps.place(db, a, pm, "buy", "market", 4, None, 4, "f1")
    perps.place(db, a, pm, "sell", "market", 7, None, 4, "f2")
    p = _pos(db, a.id, pm)
    assert p.size == -3 and p.margin_cents > 0 and p.realized_cents != 0
    _ok(db)


def test_hourly_funding_longs_pay_shorts_when_the_perp_trades_above_the_index(db: Session) -> None:
    ex, pm = _setup(db)
    lg, sh = _acct(db, "fundlong"), _acct(db, "fundshort")
    mk_acct = system(db, "maker")
    perps._cancel_all(db, mk_acct.id, pm, "test", __import__("app.db.base", fromlist=["utcnow"]).utcnow())  # TEST: an empty book
    db.commit()
    perps.place(db, sh, pm, "sell", "limit", 10, 3_640, 5, "fs1")  # ~4% above the index
    perps.place(db, lg, pm, "buy", "limit", 10, 3_640, 5, "fl1")
    perps.place(db, sh, pm, "sell", "limit", 1, 3_650, 5, "fs2")  # keep a book above the index so the mark stays high
    perps.place(db, lg, pm, "buy", "limit", 1, 3_630, 5, "fl2")
    m_long, m_short = _pos(db, lg.id, pm).margin_cents, _pos(db, sh.id, pm).margin_cents
    res = perps.fund(db, pm, force=True)
    assert res["rate_ppm"] > 0  # perp above the index: longs pay
    assert _pos(db, lg.id, pm).margin_cents < m_long and _pos(db, sh.id, pm).margin_cents > m_short
    assert _pos(db, lg.id, pm).funding_cents < 0 < _pos(db, sh.id, pm).funding_cents
    assert perps.fund(db, pm) is None  # not again within the hour
    _ok(db)


def test_mark_is_clamped_to_the_index_so_a_thin_book_cant_force_liquidations(db: Session) -> None:
    ex, pm = _setup(db)
    perps._cancel_all(db, system(db, "maker").id, pm, "test", __import__("app.db.base", fromlist=["utcnow"]).utcnow())
    db.commit()
    a = _acct(db, "pusher")
    perps.place(db, a, pm, "sell", "limit", 1, 2_000, 1, "p1")  # an absurd ask, far under the index
    perps.place(db, a, pm, "buy", "limit", 1, 1_900, 1, "p2")
    idx = ex.ref_contract_cents
    assert perps.mark(db, pm) >= idx * 95 // 100  # the mark can't leave the ±5% band around the real-card index


def test_liquidation_hands_the_position_to_the_insurance_fund(db: Session) -> None:
    ex, pm = _setup(db)
    a = _acct(db, "degen")
    perps.place(db, a, pm, "buy", "market", 20, None, 10, "d1")
    entry = _pos(db, a.id, pm).entry_cents
    lp = perps.liq_price(_pos(db, a.id, pm))
    assert lp is not None and lp < entry and lp > entry * 88 // 100  # 10x: liquidated after roughly a 5-6% drop
    # the real-card index falls 6%: the maker re-quotes lower, the mark follows (within the band), the long is liquidated
    ex.ref_contract_cents = ex.ref_contract_cents * 94 // 100
    db.commit()
    perps.requote(db, pm)
    p = _pos(db, a.id, pm)
    assert p.size == 0 and p.liquidations == 1
    ins = system(db, "insurance")
    assert _pos(db, ins.id, pm).size == 20  # the fund now holds it
    liq = db.scalar(select(PerpLiquidation).where(PerpLiquidation.account_id == a.id))
    assert liq is not None and liq.size == 20
    assert db.scalar(select(PerpTrade.kind).where(PerpTrade.seller_account_id == a.id).order_by(PerpTrade.id.desc())) == "liquidation"
    assert balances(db, a.id)[USDT][0] >= 0  # never below zero
    _ok(db)
    # the fund unwinds into the book, a chunk at a time
    u = perps.unwind_insurance(db, pm)
    assert u and u["filled"] > 0 and _pos(db, ins.id, pm).size < 20
    _ok(db)


def test_bankruptcy_beyond_the_margin_is_covered_by_the_insurance_fund(db: Session) -> None:
    ex, pm = _setup(db)
    a = _acct(db, "bust")
    perps.place(db, a, pm, "buy", "market", 20, None, 10, "b1")
    before = sum(balances(db, a.id)[USDT])
    ex.ref_contract_cents = ex.ref_contract_cents * 80 // 100  # a 20% gap: far past the margin
    db.commit()
    perps.requote(db, pm)
    assert _pos(db, a.id, pm).size == 0
    assert sum(balances(db, a.id)[USDT]) == before  # lost the margin only, never more: the wallet outside it is untouched
    liq = db.scalar(select(PerpLiquidation).where(PerpLiquidation.account_id == a.id))
    assert liq.insurance_cents > 0
    _ok(db)


def test_halted_index_allows_only_closing(db: Session) -> None:
    ex, pm = _setup(db)
    a = _acct(db, "halt")
    perps.place(db, a, pm, "buy", "market", 2, None, 2, "h1")
    ex.ref_status = "insufficient"
    db.commit()
    with pytest.raises(ExchangeError, match="Only closing orders"):
        perps.place(db, a, pm, "buy", "market", 1, None, 2, "h2")
    assert perps.requote(db, pm)["quoting"] is False


def test_admin_reset_closes_perps_and_keeps_everything_balanced(db: Session) -> None:
    ex, pm = _setup(db)
    a = _acct(db, "resetme")
    perps.place(db, a, pm, "sell", "market", 6, None, 3, "r1")
    perps.place(db, a, pm, "buy", "limit", 2, 3_300, 3, "r2")
    admin.reset_account(db, a.id, "test-admin")
    assert _pos(db, a.id, pm).size == 0 and balances(db, a.id)[USDT] == (1_000_000, 0)
    assert db.scalar(select(PerpOrder.status).where(PerpOrder.client_id == "r2")) == "cancelled"
    _ok(db)


def test_concurrent_perp_orders_stay_balanced(db: Session) -> None:
    ex, pm = _setup(db)
    ids = [_acct(db, f"pc{i}").id for i in range(6)]
    pid = pm.id
    errors: list[str] = []

    def run(i: int) -> None:
        s = SessionLocal()
        try:
            from app.models.exchange import ExAccount

            a, p = s.get(ExAccount, ids[i % 6]), s.get(PerpMarket, pid)
            for k in range(8):
                side = "buy" if (i + k) % 2 else "sell"
                try:
                    perps.place(s, a, p, side, "market" if k % 2 else "limit", 1 + k % 4, None if k % 2 else (3_520 if side == "buy" else 3_480),
                                1 + (i + k) % 10, f"pc{i}-{k}")
                except ExchangeError:
                    pass
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
        finally:
            s.close()

    ts = [threading.Thread(target=run, args=(i,)) for i in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
    _ok(db)
    for o in db.scalars(select(PerpOrder)):
        assert 0 <= o.filled <= o.qty and o.held >= 0


def test_api_perps_end_to_end(client: TestClient, db: Session) -> None:
    ex, pm = _setup(db)
    member, tok = _member(db, "perpapi")
    lst = client.get("/api/members/exchange/perps").json()
    assert lst["paper_trading"] and lst["markets"][0]["symbol"].endswith("-PERP") and lst["markets"][0]["max_leverage"] == 10
    client.cookies.set("pc_session", tok)
    r = client.post("/api/members/exchange/perps/orders", json={"symbol": pm.symbol, "side": "buy", "type": "market", "qty": 3, "leverage": 5,
                                                              "client_id": "api-perp-1"})
    assert r.status_code == 200 and r.json()["fills"], r.text
    me = client.get("/api/members/exchange/me").json()
    pos = me["perps"]["positions"][0]
    assert pos["side"] == "long" and pos["size"] == 3 and pos["leverage"] == 5 and pos["liq_price"] < pos["entry"]
    assert client.post(f"/api/members/exchange/perps/{pm.symbol}/close", json={"client_id": "api-close-1"}).status_code == 200
    d = client.get(f"/api/members/exchange/perps/{pm.symbol}").json()
    assert d["market"]["funding"]["every_minutes"] == 60 and d["book"]["bids"]
    acct = account_for(db, db.get(__import__("app.models.members", fromlist=["Member"]).Member, member.id))
    assert acct is not None
    _ok(db)


@pytest.fixture(autouse=True)
def _exchange_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Paper Exchange is retired on the website (exchange_enabled off); its engine is still tested with it on."""
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "exchange_enabled", True)
