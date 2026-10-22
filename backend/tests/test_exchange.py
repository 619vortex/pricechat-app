"""Pokémon Paper Exchange (paper trading only). TEST FIXTURES only: a fake Charizard PSA 10 with fake sales and prices."""

import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.session import SessionLocal
from app.exchange import admin, engine, ledger, maker, oracle, views
from app.exchange.ledger import USDT, ExchangeError, account_for, balances, ensure_markets, reconcile
from app.members import service
from app.members.catalog import ensure_catalog
from app.models.exchange import ExMarket, ExOrder, ExTrade
from app.models.members import Member
from app.models.pokemon import PokemonCard, PokemonProviderValuation, PokemonSale

SYM = "CHARUNL"


def _card(db: Session, sales=(35_000, 34_000, 36_000, 35_500, 34_500), scrydex: int | None = 35_000, scry_age_days: int = 1,
          sale_age_days: int = 3) -> PokemonCard:
    """TEST FIXTURE: the Base Set Unlimited Charizard PSA 10 market's card, its sales and a Scrydex estimate (USD)."""
    c = PokemonCard(identity_key="test-ex-charizard", name="Charizard", pokemon_name="Charizard", set_name="Base Set", card_number="4/102",
                    year=1999, language="English", edition="Unlimited", variant="", rarity="Rare Holo", image_url="https://img.test/c.png", era="vintage")
    db.add(c)
    db.flush()
    now = utcnow()
    for i, usd in enumerate(sales):
        db.add(PokemonSale(card_id=c.id, grading_company="PSA", grade="10", sale_price_cents=usd * 100, sale_price_usd_cents=usd * 100, fingerprint=f"t{i}-{usd}",
                           sale_date=now - timedelta(days=sale_age_days + i * 5),
                           verified=True, excluded=False, source="ebay", source_listing_id=f"t{i}", title="TEST", currency="USD"))
    if scrydex:
        db.add(PokemonProviderValuation(provider="scrydex", card_id=c.id, provider_card_id="x", provider_variant="v", grading_company="PSA", grade="10",
                                        day=(now - timedelta(days=scry_age_days)).date(), kind="current", market_cents=scrydex * 100, currency="USD", fetched_at=now))
    db.commit()
    return c


def _market(db: Session) -> ExMarket:
    ensure_markets(db)
    m = db.scalar(select(ExMarket).where(ExMarket.symbol == SYM))
    assert m is not None
    oracle.refresh(db, m)
    db.commit()
    return m


def _member(db: Session, name: str) -> tuple[Member, str]:
    ensure_catalog(db)
    m = service.create_member(db)
    m.username, m.username_key = name, name
    db.commit()
    return m, service.new_session(db, m, 30)


def _acct(db: Session, name: str):  # type: ignore[no-untyped-def]
    m, _ = _member(db, name)
    return account_for(db, m)


def _give(db: Session, acct_id: int, qty: int) -> None:
    """TEST: move contracts from the treasury to an account (a balanced journal)."""
    t = ledger.system(db, "treasury")
    ledger.post(db, "seed", f"test:give:{acct_id}:{qty}:{utcnow().timestamp()}", "test", [ledger.Leg(t.id, SYM, -qty), ledger.Leg(acct_id, SYM, qty)],
                treasury_may_go_negative=True)
    db.commit()


def test_oracle_rejects_outliers_and_combines_sources(db: Session) -> None:
    _card(db, sales=(35_000, 34_000, 36_000, 330, 35_500, 34_500))  # a misidentified $330 "sale"
    m = _market(db)
    snap = db.scalar(select(func.max(ExMarket.ref_contract_cents)))
    assert m.ref_status == "ok" and m.ref_confidence in ("high", "medium")
    assert 3_450 <= snap <= 3_550  # ~ $35,000 / 1000 = 35.00 paper USDT, the $330 rejected
    ev = oracle.evaluate(db, m)
    assert [r["usd"] for r in ev["provenance"]["rejected"]] == [330.0] and ev["review"] is True


def test_oracle_stale_and_insufficient_halt_quoting_and_trading(db: Session) -> None:
    _card(db, sales=(), scrydex=35_000, scry_age_days=30)  # only an old Scrydex value
    m = _market(db)
    assert m.ref_status == "stale"
    assert maker.requote(db, m)["quoting"] is False  # the simulated maker stops quoting
    db.execute(PokemonProviderValuation.__table__.delete())
    db.commit()
    oracle.refresh(db, m)
    db.commit()
    assert m.ref_status == "insufficient"
    a = _acct(db, "trader")
    with pytest.raises(ExchangeError, match="halted"):
        engine.place(db, a, m, "buy", "limit", 1, 3_500, "halted1")


def test_damping_caps_one_jump(db: Session) -> None:
    c = _card(db)
    m = _market(db)
    before = m.ref_contract_cents
    db.add(PokemonProviderValuation(provider="scrydex", card_id=c.id, provider_card_id="x", provider_variant="v2", grading_company="PSA", grade="10",
                                    day=utcnow().date(), kind="current", market_cents=9_000_000, currency="USD", fetched_at=utcnow()))  # $90,000
    db.commit()
    snap = oracle.refresh(db, m)
    db.commit()
    assert m.ref_contract_cents <= before * 115 // 100 and snap.review is True  # one update can't move it more than 15%


def test_wallet_limit_priority_partial_fills_fees_and_pnl(db: Session) -> None:
    _card(db)
    m = _market(db)
    s1, s2, b = _acct(db, "seller1"), _acct(db, "seller2"), _acct(db, "buyer")
    assert balances(db, b.id)[USDT] == (1_000_000, 0)  # 10,000 paper USDT
    _give(db, s1.id, 5)
    _give(db, s2.id, 5)
    engine.place(db, s1, m, "sell", "limit", 3, 3_510, "s1a")  # first at 35.10
    engine.place(db, s2, m, "sell", "limit", 3, 3_510, "s2a")  # second at 35.10 (later: lower priority)
    engine.place(db, s2, m, "sell", "limit", 2, 3_500, "s2b")  # best price 35.00
    r = engine.place(db, b, m, "buy", "limit", 6, 3_510, "b1")
    fills = [(f["price"], f["qty"]) for f in r["fills"]]
    assert fills == [(3_500, 2), (3_510, 3), (3_510, 1)]  # best price first, then time priority at the same price
    orders = {o.client_id: o for o in db.scalars(select(ExOrder))}
    assert orders["s1a"].status == "filled" and orders["s2a"].filled == 1 and orders["s2a"].status == "open"  # partial fill rests
    notional = 3_500 * 2 + 3_510 * 4
    fee = 3_500 * 2 * 10 // 10_000 + 3_510 * 3 * 10 // 10_000 + 3_510 * 1 * 10 // 10_000  # taker 0.10% per fill, rounded down; maker 0%
    assert balances(db, b.id)[USDT] == (1_000_000 - notional - fee, 0)  # the unused hold came back
    assert balances(db, b.id)[SYM] == (6, 0)
    assert balances(db, s1.id)[USDT][0] == 1_000_000 + 3_510 * 3  # maker: no fee
    # P&L: the buyer sells 6 at 36.00 to a bid: realized = proceeds - fee - cost (fees included)
    engine.cancel(db, s2, orders["s2a"].id)  # the rest of that ask would otherwise fill the new bid first (correctly)
    bb = _acct(db, "bidder")
    engine.place(db, bb, m, "buy", "limit", 6, 3_600, "bb1")
    out = engine.place(db, b, m, "sell", "market", 6, None, "bsell")
    t = db.scalar(select(ExTrade).where(ExTrade.sell_order_id == db.scalar(select(ExOrder.id).where(ExOrder.client_id == "bsell"))))
    assert out["fills"] and t.seller_realized_cents == 3_600 * 6 - 3_600 * 6 * 10 // 10_000 - (notional + fee)
    assert reconcile(db)["ok"]


def test_holds_no_overspend_no_shorting_cancel_releases(db: Session) -> None:
    _card(db)
    m = _market(db)
    a = _acct(db, "small")
    with pytest.raises(ExchangeError, match="Not enough paper USDT"):
        engine.place(db, a, m, "buy", "limit", 1000, 5_000, "toobig")  # 50,000 > 10,000
    with pytest.raises(ExchangeError, match="only sell contracts you own"):
        engine.place(db, a, m, "sell", "limit", 1, 3_500, "short")
    r = engine.place(db, a, m, "buy", "limit", 10, 3_400, "rest")
    held = 3_400 * 10 + 3_400 * 10 * 10 // 10_000
    assert balances(db, a.id)[USDT] == (1_000_000 - held, held)
    with pytest.raises(ExchangeError, match="Not enough"):  # what's held can't be spent twice
        engine.place(db, a, m, "buy", "limit", 280, 3_500, "twice")
    engine.cancel(db, a, r["order"]["id"])
    assert balances(db, a.id)[USDT] == (1_000_000, 0)
    with pytest.raises(ExchangeError, match="within 50%"):
        engine.place(db, a, m, "buy", "limit", 1, 100, "fatfinger")
    assert reconcile(db)["ok"]


def test_market_orders_ioc_with_price_protection(db: Session) -> None:
    _card(db)
    m = _market(db)
    s, b = _acct(db, "s"), _acct(db, "b")
    _give(db, s.id, 10)
    engine.place(db, s, m, "sell", "limit", 2, 3_500, "a1")
    engine.place(db, s, m, "sell", "limit", 3, 3_900, "a2")  # more than 5% above the best ask: out of reach
    r = engine.place(db, b, m, "buy", "market", 4, None, "mb")
    assert [f["qty"] for f in r["fills"]] == [2] and r["order"]["status"] == "cancelled" and "filled 2 of 4" in r["order"]["reason"]
    assert balances(db, b.id)[USDT][1] == 0  # nothing left on hold
    with pytest.raises(ExchangeError, match="No buyers"):
        engine.place(db, b, m, "sell", "market", 1, None, "nobids")


def test_self_trade_prevention_and_idempotent_retries(db: Session) -> None:
    _card(db)
    m = _market(db)
    a = _acct(db, "solo")
    _give(db, a.id, 5)
    engine.place(db, a, m, "sell", "limit", 2, 3_500, "mine-ask")
    r = engine.place(db, a, m, "buy", "limit", 2, 3_500, "mine-bid")
    assert r["fills"] == [] and db.scalar(select(ExOrder.status).where(ExOrder.client_id == "mine-ask")) == "cancelled"  # never trades with itself
    again = engine.place(db, a, m, "buy", "limit", 2, 3_500, "mine-bid")
    assert again["duplicate"] is True and db.scalar(select(func.count()).select_from(ExOrder).where(ExOrder.client_id == "mine-bid")) == 1
    assert db.scalar(select(func.count()).select_from(ExTrade)) == 0


def test_market_maker_quotes_bounded_inventory_and_own_ledger(db: Session) -> None:
    _card(db)
    m = _market(db)
    q = maker.requote(db, m)
    assert q["quoting"] and q["placed"] == 6  # 3 bids + 3 asks
    book = views.book(db, m)
    ref = m.ref_contract_cents
    assert book["bids"][0]["price"] < ref < book["asks"][0]["price"] and all(l["simulated_qty"] == l["qty"] for l in book["bids"] + book["asks"])
    spread = (book["asks"][0]["price"] - book["bids"][0]["price"]) / ref
    assert 0.008 < spread < 0.013  # ~1%
    mk = ledger.system(db, "maker")
    inv0 = sum(balances(db, mk.id)[SYM])
    # a member buys from the maker; it re-quotes; it can never sell more than it holds
    b = _acct(db, "buyer")
    engine.place(db, b, m, "buy", "market", 30, None, "lift")
    maker.requote(db, m)
    inv1 = sum(balances(db, mk.id)[SYM])
    assert inv1 < inv0 and all(o.held >= 0 for o in db.scalars(select(ExOrder)))
    asks_qty = sum(l["simulated_qty"] for l in views.book(db, m)["asks"])
    assert asks_qty <= inv1
    # the maker's executions are ordinary, recorded trades with its own (seeded, journaled) balances
    assert db.scalar(select(func.count()).select_from(ExTrade).where(ExTrade.seller_account_id == mk.id)) >= 1
    assert reconcile(db)["ok"]


def test_concurrent_orders_never_overspend_or_double_fill(db: Session) -> None:
    _card(db)
    m = _market(db)
    maker.requote(db, m)
    accts = [_acct(db, f"racer{i}") for i in range(6)]
    for a in accts:
        _give(db, a.id, 20)
    mid, aids = m.id, [a.id for a in accts]
    errors: list[str] = []

    def run(i: int) -> None:
        s = SessionLocal()
        try:
            from app.models.exchange import ExAccount

            a = s.get(ExAccount, aids[i % len(aids)])
            mm = s.get(ExMarket, mid)
            for k in range(8):
                side = "buy" if (i + k) % 2 else "sell"
                try:
                    engine.place(s, a, mm, side, "market" if k % 3 == 0 else "limit", 1 + k % 3, None if k % 3 == 0 else (3_520 if side == "buy" else 3_480), f"r{i}-{k}")
                except ExchangeError:
                    pass
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
        finally:
            s.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    db.expire_all()
    r = reconcile(db)
    assert r["ok"], r
    for a in aids:  # never negative
        assert all(av >= 0 and h >= 0 for av, h in balances(db, a).values())
    for o in db.scalars(select(ExOrder)):  # never over-filled
        assert 0 <= o.filled <= o.qty
    traded = db.scalar(select(func.coalesce(func.sum(ExTrade.qty), 0)))
    filled = db.scalar(select(func.coalesce(func.sum(ExOrder.filled), 0)))
    assert filled == 2 * traded  # every fill counted once on each side


def test_candles_only_from_trades_and_admin_reset(db: Session) -> None:
    _card(db)
    m = _market(db)
    assert views.candles(db, m, "1m")["candles"] == []  # no trades: no candles (nothing invented)
    s, b = _acct(db, "cs"), _acct(db, "cb")
    _give(db, s.id, 5)
    engine.place(db, s, m, "sell", "limit", 2, 3_500, "c1")
    engine.place(db, s, m, "sell", "limit", 2, 3_520, "c2")
    engine.place(db, b, m, "buy", "limit", 4, 3_520, "c3")
    c = views.candles(db, m, "1h")["candles"]
    assert len(c) == 1 and c[0]["o"] == 3_500 and c[0]["h"] == 3_520 and c[0]["c"] == 3_520 and c[0]["v"] == 4
    res = admin.reset_account(db, b.id, "test-admin")
    assert res["usdt"] == 1_000_000 and balances(db, b.id)[USDT] == (1_000_000, 0) and balances(db, b.id).get(SYM, (0, 0)) == (0, 0)
    assert reconcile(db)["ok"]


def test_api_paper_trading_end_to_end_and_leaderboard_rules(client: TestClient, db: Session) -> None:
    _card(db)
    m = _market(db)
    maker.requote(db, m)
    member, tok = _member(db, "apitrader")
    listing = client.get("/api/members/exchange/markets").json()
    assert listing["paper_trading"] is True and any(x["symbol"] == SYM for x in listing["markets"])
    assert client.post("/api/members/exchange/orders", json={"symbol": SYM, "side": "buy", "type": "market", "qty": 1, "client_id": "anon-123"}).status_code == 401
    client.cookies.set("pc_session", tok)
    me = client.get("/api/members/exchange/me").json()
    assert me["usdt"]["available"] == 1_000_000 and me["equity"] == 1_000_000
    for i in range(3):
        r = client.post("/api/members/exchange/orders", json={"symbol": SYM, "side": "buy", "type": "market", "qty": 1, "client_id": f"api-buy-{i}"})
        assert r.status_code == 200 and r.json()["fills"], r.text
    me = client.get("/api/members/exchange/me").json()
    assert me["positions"][0]["qty"] == 3 and me["achievements"][0]["earned"] is True
    lb = client.get("/api/members/exchange/leaderboard?board=equity").json()
    assert [r["username"] for r in lb["rows"]] == ["apitrader"]  # the simulated maker is never ranked
    assert any("REAL reference price" in r for r in lb["rules"])
    detail = client.get(f"/api/members/exchange/markets/{SYM}").json()
    assert detail["book"]["asks"] and detail["trades"][0]["simulated"] is True
    assert client.get(f"/api/members/exchange/markets/{SYM}/candles?interval=1m").json()["candles"]
    assert client.get("/api/exchange/admin/overview").status_code in (200, 401)  # owner-only (the admin gate decides)


def test_deleting_a_member_keeps_the_ledger_whole(db: Session) -> None:
    _card(db)
    m = _market(db)
    maker.requote(db, m)
    member, _ = _member(db, "leaver")
    a = account_for(db, member)
    engine.place(db, a, m, "buy", "market", 2, None, "leave-buy")
    engine.place(db, a, m, "buy", "limit", 1, 3_300, "leave-rest")
    service.delete_member(db, member)
    db.expire_all()
    from app.models.exchange import ExAccount

    acct = db.get(ExAccount, a.id)
    assert acct is not None and acct.member_id is None and acct.name == "former member"
    assert db.scalar(select(ExOrder.status).where(ExOrder.client_id == "leave-rest")) == "cancelled"
    assert reconcile(db)["ok"]
    assert all(r["username"] != "leaver" for r in views.leaderboard(db)["rows"])


@pytest.fixture(autouse=True)
def _exchange_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Paper Exchange is retired on the website (exchange_enabled off); its engine is still tested with it on."""
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "exchange_enabled", True)
