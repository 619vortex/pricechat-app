"""Card Marketplace. TEST FIXTURES only (test database, generated images, simulated USDT payments)."""

import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.market import compliance, service
from app.models.market import MarketItem, MarketLedgerEntry, MarketListing, MarketOrder, MarketTransfer
from app.models.members import Member
from app.models.portfolio import PokemonHolding
from tests.test_portfolio import _card, _member, _price


def _img(seed: int) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (1000, 1400), (seed % 255, (seed * 7) % 255, (seed * 13) % 255)).save(out, "JPEG")
    return out.getvalue()


def _as(client: TestClient, db: Session, m: Member) -> None:
    from app.members import service as members

    client.cookies.set("pc_session", members.new_session(db, m, 30))


def _graded(client: TestClient, company: str = "PSA", grade: str = "9", card_name: str = "Charizard") -> dict:
    c = client.app.state  # noqa: F841 - keeps the client warm
    return client.post("/api/members/portfolio/holdings", json={"card_id": _CARD[card_name], "kind": "graded", "grading_company": company,
                                                                "grade": grade}).json()


_CARD: dict[str, int] = {}


@pytest.fixture
def setup(client: TestClient, db: Session, monkeypatch):  # type: ignore[no-untyped-def]
    zard = _card(db)
    _CARD["Charizard"] = zard.id
    _price(db, zard, "PSA", "9", 1_000)
    s = client.app.state.runtime.settings  # type: ignore[attr-defined]
    monkeypatch.setattr(s, "market_vault_partners", "")
    monkeypatch.setattr(s, "market_mode", "simulation")
    service._PROVIDERS.clear()
    return s


def _draft(client: TestClient, holding_id: int, cert: str = "12345678", price: float = 900) -> dict:
    r = client.post("/api/members/market/listings", json={"holding_id": holding_id, "grading_company": "PSA", "grade": "9", "cert_number": cert,
                                                          "price": price, "ownership_evidence": "Bought at a card show in 2021, receipt available."})
    assert r.status_code == 201, r.text
    return r.json()


def test_selling_needs_verification_and_confirmed_custody_before_anything_is_public(client: TestClient, db: Session, setup, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seller = _member(db, "seller1", client)
    h = _graded(client)
    r = client.post("/api/members/market/listings", json={"holding_id": h["id"], "grading_company": "PSA", "grade": "9", "cert_number": "12345678"})
    assert r.status_code == 400 and "verified sellers" in r.text  # only verified sellers list
    v = client.post("/api/members/market/seller/verification", json={"country": "AE", "about": "Collector since 2015, cards bought at shows."})
    assert v.json()["status"] == "pending" and client.get("/api/members/market/seller").json()["can_list"] is False
    from app.market import admin as market_admin

    market_admin.set_seller(db, "seller1", status="verified")
    raw = client.post("/api/members/portfolio/holdings", json={"card_id": _CARD["Charizard"], "kind": "raw"}).json()
    r = client.post("/api/members/market/listings", json={"holding_id": raw["id"], "grading_company": "RAW"})
    assert r.status_code == 400 and "Estimate condition" in r.text  # a raw card needs its AI condition scan first
    d = _draft(client, h["id"])
    assert d["status"] == "draft" and d["cert"] == "12345678"  # the seller sees their own cert in full
    assert client.post(f"/api/members/market/listings/{d['id']}/submit").status_code == 400  # photos missing
    for i, side in enumerate(("front", "back", "label")):
        assert client.post(f"/api/members/market/listings/{d['id']}/photos", data={"side": side},
                           files={"file": ("p.jpg", _img(i + 1), "image/jpeg")}).status_code == 201
    sub = client.post(f"/api/members/market/listings/{d['id']}/submit").json()
    assert sub["status"] == "awaiting_verification"

    other = _member(db, "lurker", client)
    assert client.get(f"/api/members/market/listings/{d['id']}").status_code == 404  # not public yet
    market_admin.set_seller(db, "lurker", status="verified")
    h2 = _graded(client)
    dup = client.post("/api/members/market/listings", json={"holding_id": h2["id"], "grading_company": "PSA", "grade": "9", "cert_number": "12345678"})
    assert dup.status_code == 400 and "already registered" in dup.json()["detail"]  # duplicate cert from another account

    assert client.post(f"/api/market/admin/listings/{d['id']}/verify", json={"approve": True, "note": "label matches catalogue"}).json()["status"] == "awaiting_custody"
    item_id = db.scalar(select(MarketListing.item_id).where(MarketListing.id == d["id"]))
    # no vault partner contracted: custody can't be confirmed, so the card can't go live
    r = client.post(f"/api/market/admin/items/{item_id}/custody", json={"to": "received", "partner": "acme-vault"})
    assert r.status_code == 400 and "no vault" in r.json()["detail"].lower()
    monkeypatch.setattr(setup, "market_vault_partners", "acme-vault")
    assert client.post(f"/api/market/admin/items/{item_id}/custody", json={"to": "received", "partner": "acme-vault"}).status_code == 200
    bad = client.post(f"/api/market/admin/items/{item_id}/custody", json={"to": "vaulted", "partner": "acme-vault", "inventory_id": "AV-1",
                                                                          "location": "A1", "reason": "intake done", "confirm": "nope"})
    assert bad.status_code == 400  # ownership-changing step needs the target typed back
    ok = client.post(f"/api/market/admin/items/{item_id}/custody", json={"to": "vaulted", "partner": "acme-vault", "inventory_id": "AV-1",
                                                                         "location": "A1", "reason": "intake done", "confirm": f"ITEM-{item_id}"})
    assert ok.json()["custody"] == "vaulted"
    _as(client, db, seller)
    live = client.post(f"/api/members/market/listings/{d['id']}/publish").json()
    assert live["status"] == "active" and live["custody"]["vaulted"] and live["custody"]["insurance"] == "none"  # never claimed insured

    _as(client, db, other)
    pub = client.get(f"/api/members/market/listings/{d['id']}").json()
    assert pub["cert"] == "••••5678" and "ownership_evidence" not in pub and pub["estimate"]["value"] == 1_000_00 and pub["diff_pct"] == -10.0
    found = client.get("/api/members/market/listings", params={"q": "charizard", "company": "PSA", "max_price": 950}).json()
    assert [x["id"] for x in found["items"]] == [d["id"]]
    assert client.get("/api/members/market/listings", params={"min_discount": 20}).json()["items"] == []
    # real (non-sandbox) listings can't be bought while settlement isn't live
    buy = client.post(f"/api/members/market/listings/{d['id']}/buy", json={"network": "tron"}, headers={"Idempotency-Key": "k-real-1"})
    assert buy.status_code == 400 and "simulation" in buy.json()["detail"]
    assert client.get("/api/members/market/home").json()["stats"]["volume"] == 0


def test_raw_card_listing_ai_condition_member_grades_and_comments(client: TestClient, db: Session, setup, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.market import admin as market_admin

    seller = _member(db, "rawseller", client)
    market_admin.set_seller(db, "rawseller", status="verified")
    h = client.post("/api/members/portfolio/holdings", json={"card_id": _CARD["Charizard"], "kind": "raw"}).json()
    sellable = client.get("/api/members/market/sell/holdings").json()["holdings"]
    assert [x["condition"]["scanned"] for x in sellable if x["holding_id"] == h["id"]] == [False]
    row = db.get(PokemonHolding, h["id"])  # what the AI condition assistant records after reviewing front + back photos
    row.condition_low, row.condition_high, row.condition_source = 6.5, 7.5, "ai"
    row.condition_report = {"enough_photos": True, "low": 6.5, "high": 7.5, "confidence": "medium", "corners": "light wear top-left",
                            "issues": ["light whitening on back edges"], "label": "AI estimate: not a professional grade, not authentication"}
    db.commit()
    r = client.post("/api/members/market/listings", json={"holding_id": h["id"], "grading_company": "RAW", "price": 120,
                                                          "ownership_evidence": "Pulled from a booster pack in 2023, kept in a sleeve."})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["kind"] == "raw" and d["grade"] == "LP" and d["cert"] is None and d["condition"]["ai"]["corners"] == "light wear top-left"
    for i, side in enumerate(("front", "back")):  # no slab label for a raw card
        client.post(f"/api/members/market/listings/{d['id']}/photos", data={"side": side}, files={"file": ("p.jpg", _img(i + 20), "image/jpeg")})
    assert client.post(f"/api/members/market/listings/{d['id']}/submit").json()["status"] == "awaiting_verification"
    client.post(f"/api/market/admin/listings/{d['id']}/verify", json={"approve": True, "note": "photos match"})
    monkeypatch.setattr(setup, "market_vault_partners", "acme-vault")
    item_id = db.scalar(select(MarketListing.item_id).where(MarketListing.id == d["id"]))
    client.post(f"/api/market/admin/items/{item_id}/custody", json={"to": "received", "partner": "acme-vault"})
    client.post(f"/api/market/admin/items/{item_id}/custody", json={"to": "vaulted", "partner": "acme-vault", "inventory_id": "AV-R1",
                                                                    "location": "B2", "reason": "intake done", "confirm": f"ITEM-{item_id}"})
    assert client.post(f"/api/members/market/listings/{d['id']}/publish").json()["status"] == "active"
    assert client.post(f"/api/members/market/listings/{d['id']}/condition-vote", json={"estimate": 9}).status_code == 400  # not the seller

    _member(db, "grader", client)
    assert client.post(f"/api/members/market/listings/{d['id']}/condition-vote", json={"estimate": 6, "note": "back edges"}).json()["count"] == 1
    assert client.post(f"/api/members/market/listings/{d['id']}/condition-vote", json={"estimate": 7}).json()["average"] == 7.0  # changeable
    c = client.post(f"/api/members/market/listings/{d['id']}/comments", json={"body": "Any photos of the back corners?"}).json()["comments"]
    reply = client.post(f"/api/members/market/listings/{d['id']}/comments", json={"body": "Sure", "parent_id": c[0]["id"]}).json()["comments"]
    assert client.post(f"/api/members/market/listings/{d['id']}/comments", json={"body": "nested", "parent_id": reply[1]["id"]}).status_code == 400
    pub = client.get(f"/api/members/market/listings/{d['id']}").json()
    assert pub["condition"]["community"]["mine"]["estimate"] == 7 and pub["condition"]["label"] == "LP"
    assert [x["id"] for x in client.get("/api/members/market/listings", params={"kind": "raw"}).json()["items"]] == [d["id"]]
    assert client.get("/api/members/market/listings", params={"kind": "graded"}).json()["items"] == []
    assert client.delete(f"/api/members/market/comments/{c[0]['id']}").json()["ok"]
    assert client.get(f"/api/members/market/listings/{d['id']}/comments").json()["comments"][0]["deleted"] is True
    assert seller.id


def _sandbox(client: TestClient, db: Session, seller: str, price: float = 500) -> int:
    r = client.post("/api/market/admin/sandbox/listings", json={"username": seller, "card_id": _CARD["Charizard"], "grading_company": "PSA",
                                                                "grade": "9", "price": price})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_simulated_trade_idempotent_no_double_sale_verified_payment_ledger_and_portfolios(client: TestClient, db: Session, setup) -> None:  # type: ignore[no-untyped-def]
    seller = _member(db, "simseller")
    buyer = _member(db, "simbuyer", client)
    rival = _member(db, "rival")
    outsider = _member(db, "outsider")
    lid = _sandbox(client, db, "simseller")
    for name in ("simbuyer", "rival"):
        client.post("/api/market/admin/sellers", json={"username": name, "sandbox": True})
    _as(client, db, outsider)
    assert client.get(f"/api/members/market/listings/{lid}").status_code == 404  # sandbox is invisible to the public

    _as(client, db, buyer)
    o1 = client.post(f"/api/members/market/listings/{lid}/buy", json={"network": "tron"}, headers={"Idempotency-Key": "click-0001"}).json()
    again = client.post(f"/api/members/market/listings/{lid}/buy", json={"network": "tron"}, headers={"Idempotency-Key": "click-0001"}).json()
    assert o1["id"] == again["id"] and o1["status"] == "awaiting_payment" and o1["amount_units"] == "500000000"  # 500 USDT, 6 decimals
    assert o1["token"] == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    _as(client, db, rival)
    assert client.post(f"/api/members/market/listings/{lid}/buy", json={"network": "solana"}, headers={"Idempotency-Key": "click-0002"}).status_code == 409

    _as(client, db, buyer)
    for outcome in ("underpaid", "wrong_token", "wrong_address", "wrong_network"):
        r = client.post(f"/api/members/market/orders/{o1['id']}/simulate-payment", json={"outcome": outcome}).json()
        assert r["status"] == "awaiting_payment" and r["payments"][-1]["status"] == "rejected"  # never settled on a bad transfer
    pend = client.post(f"/api/members/market/orders/{o1['id']}/simulate-payment", json={"outcome": "unconfirmed"}).json()
    assert pend["status"] == "awaiting_payment" and pend["payments"][-1]["status"] == "pending"
    done = client.post(f"/api/members/market/orders/{o1['id']}/simulate-payment", json={"outcome": "exact"}).json()
    assert done["status"] == "completed"
    tx = done["payments"][-1]["tx"]
    with pytest.raises(service.Conflict):  # replay: the same transaction can't pay twice
        service.confirm_payment(db, setup, o1["id"], "tron", tx)

    item = db.scalar(select(MarketItem).join(MarketListing, MarketListing.item_id == MarketItem.id).where(MarketListing.id == lid))
    assert item.owner_member_id == buyer.id and db.get(MarketListing, lid).status == "sold"
    kinds = dict(db.execute(select(MarketLedgerEntry.kind, MarketLedgerEntry.amount_cents).where(MarketLedgerEntry.order_id == o1["id"])).all())
    assert kinds["buyer_paid"] == 500_00 and kinds["platform_fee"] == 25_00 and kinds["seller_payable"] == 475_00 and kinds["seller_paid"] == 475_00
    assert [t.kind for t in db.scalars(select(MarketTransfer).where(MarketTransfer.item_id == item.id).order_by(MarketTransfer.id))] == ["intake", "sale"]
    h = db.scalar(select(PokemonHolding).where(PokemonHolding.id == item.origin_holding_id))
    assert h.verification == "sandbox" and h.purchase_price_cents == 500_00 and h.grade == "9" and h.source == "market"
    assert service.settle(db, setup, o1["id"]).status == "completed"  # idempotent
    assert db.scalar(select(func.count()).select_from(MarketLedgerEntry).where(MarketLedgerEntry.order_id == o1["id"])) == 4
    assert client.get("/api/members/market/home").json()["stats"]["volume"] == 0  # simulated trades are never volume
    assert client.get("/api/market/admin/reconcile").json()["ok"]
    assert seller.id != buyer.id


def test_one_live_order_per_listing_is_enforced_by_the_database(client: TestClient, db: Session, setup) -> None:  # type: ignore[no-untyped-def]
    _member(db, "dbseller")
    b = _member(db, "dbbuyer")
    lid = _sandbox(client, db, "dbseller")
    l = db.get(MarketListing, lid)
    for i in range(2):  # a race that slipped past the status check still can't create two live orders
        db.add(MarketOrder(ref=f"R{i}", idempotency_key=f"race-{i}", listing_id=lid, item_id=l.item_id, buyer_id=b.id, seller_id=l.seller_id,
                           price_cents=1, total_cents=1, proceeds_cents=1, network="tron", pay_to="x", amount_units="1", expires_at=utcnow(), is_sandbox=True))
        if i == 1:
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
        else:
            db.flush()


def test_expiry_late_payment_refund_offers_and_cancel(client: TestClient, db: Session, setup) -> None:  # type: ignore[no-untyped-def]
    _member(db, "offseller")
    buyer = _member(db, "offbuyer", client)
    client.post("/api/market/admin/sellers", json={"username": "offbuyer", "sandbox": True})
    lid = _sandbox(client, db, "offseller", 300)
    o = client.post(f"/api/members/market/listings/{lid}/buy", json={"network": "solana"}, headers={"Idempotency-Key": "exp-00001"}).json()
    order = db.get(MarketOrder, o["id"])
    order.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert service.expire(db)["orders_expired"] == 1 and db.get(MarketListing, lid).status == "active"  # back on sale
    late = client.post(f"/api/members/market/orders/{o['id']}/simulate-payment", json={"outcome": "exact"}).json()
    assert late["status"] == "refund_pending"  # paid after expiry: the money must go back, the card isn't sold
    assert client.post(f"/api/market/admin/orders/{o['id']}/refund", json={"reason": "late payment", "confirm": "wrong"}).status_code == 400
    ok = client.post(f"/api/market/admin/orders/{o['id']}/refund", json={"reason": "late payment", "confirm": f"ORDER-{o['ref']}"}).json()
    assert ok["status"] == "refunded"

    off = client.post(f"/api/members/market/listings/{lid}/offers", json={"amount": 250}).json()
    assert client.post(f"/api/members/market/listings/{lid}/offers", json={"amount": 400}).status_code == 400  # at/above ask: use Buy Now
    seller = db.scalar(select(Member).where(Member.username_key == "offseller"))
    _as(client, db, seller)
    from app.models.market import MarketOffer

    stale = db.get(MarketOffer, off["id"])
    stale.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.post(f"/api/members/market/offers/{off['id']}/decide", json={"accept": True}).status_code == 409  # expired offers never execute
    _as(client, db, buyer)
    off2 = client.post(f"/api/members/market/listings/{lid}/offers", json={"amount": 260}).json()
    _as(client, db, seller)
    acc = client.post(f"/api/members/market/offers/{off2['id']}/decide", json={"accept": True, "network": "tron"}).json()
    assert acc["status"] == "accepted" and db.get(MarketListing, lid).status == "reserved"
    assert client.post(f"/api/members/market/listings/{lid}/cancel").status_code == 409  # reserved: the seller can't pull it
    _as(client, db, buyer)
    cancelled = client.post(f"/api/members/market/orders/{acc['order_id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and db.get(MarketListing, lid).status == "active"


def test_redemption_locks_the_card_and_retires_the_claim_only_on_delivery(client: TestClient, db: Session, setup, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from app.config.settings import get_settings
    from app.security.secrets import dpapi_available

    if not dpapi_available():  # Linux CI / production: the address is sealed with APP_ENCRYPTION_KEY (Windows dev uses DPAPI)
        monkeypatch.setattr(get_settings(), "app_encryption_key", SecretStr(Fernet.generate_key().decode()))
    owner = _member(db, "holder", client)
    lid = _sandbox(client, db, "holder", 200)
    assert client.post(f"/api/members/market/listings/{lid}/cancel").json()["status"] == "cancelled"
    item_id = db.get(MarketListing, lid).item_id
    vault = client.get("/api/members/market/my/vault").json()["items"]
    assert vault[0]["can_redeem"]
    q = client.get("/api/members/market/redemption-quote", params={"item_id": item_id, "country": "AE"}).json()
    assert q["fees"]["total"] == q["fees"]["shipping"] + q["fees"]["handling"] + q["fees"]["insurance"]
    addr = {"name": "Ash K", "line1": "1 Pallet Road", "city": "Dubai", "postcode": "00000", "country": "AE"}
    assert client.post("/api/members/market/redemptions", json={"item_id": item_id, "address": {**addr, "country": "KP"}}).status_code == 400
    r = client.post("/api/members/market/redemptions", json={"item_id": item_id, "address": addr}).json()
    assert r["status"] == "requested" and "address" not in r
    assert db.get(MarketItem, item_id).custody_status == "release_requested"
    redemption = db.scalar(text("select address_enc from market_redemptions where id = :i"), {"i": r["id"]})
    assert "Pallet" not in redemption  # stored encrypted
    for step, extra in (("approved", {}), ("released", {"reason": "vault released", "confirm": f"REDEMPTION-{r['id']}"}),
                        ("shipped", {"carrier": "DHL", "tracking": "JD0001"})):
        assert client.post(f"/api/market/admin/redemptions/{r['id']}", json={"to": step, **extra}).status_code == 200
    assert db.get(MarketItem, item_id).custody_status == "released"  # not yet redeemed: only on delivery
    assert client.post(f"/api/market/admin/redemptions/{r['id']}", json={"to": "delivered"}).status_code == 400  # needs confirmation
    client.post(f"/api/market/admin/redemptions/{r['id']}", json={"to": "delivered", "reason": "signed for", "confirm": f"REDEMPTION-{r['id']}"})
    db.expire_all()
    assert db.get(MarketItem, item_id).custody_status == "redeemed"
    with pytest.raises(service.MemberError):
        service.request_redemption(db, setup, owner, item_id, addr)
    assert owner.id


def test_live_mode_is_refused_until_every_gate_passes(client: TestClient, db: Session, setup, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(setup, "market_mode", "live")
    assert compliance.mode(setup) == "simulation"
    for k, v in (("market_live_approved", True), ("market_payment_partner", "p"), ("market_vault_partners", "v"), ("market_kyc_provider", "k")):
        monkeypatch.setattr(setup, k, v)
    assert compliance.mode(setup) == "simulation"  # still SQLite: no live ledger
    gates = {g["gate"]: g["ok"] for g in client.get("/api/market/admin/overview").json()["gates"]}
    assert gates["durable_database"] is False and gates["owner_approval"] is True


def test_qa_member_cleanup_removes_its_marketplace_rows(client: TestClient, db: Session, setup) -> None:  # type: ignore[no-untyped-def]
    from app.api.routes.system import QA_USERNAME

    qa_id = _member(db, QA_USERNAME, client).id
    _sandbox(client, db, QA_USERNAME, 100)
    h = _graded(client)
    _draft(client, h["id"], cert="55554444")
    assert client.delete("/api/system/qa-member").json()["deleted"]
    assert db.scalar(select(func.count()).select_from(MarketItem)) == 0 and db.scalar(select(func.count()).select_from(MarketListing)) == 0
    db.expire_all()
    assert db.scalar(select(Member.id).where(Member.id == qa_id)) is None
