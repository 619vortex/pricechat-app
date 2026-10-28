"""Earn › Kalshi member links: the key is checked against Kalshi before it's kept, kept sealed, used only for signed GETs
(the fake Kalshi refuses any other method and verifies every signature), positions are marked at the bid after the
series' own fee, an unknown fee model is shown as unknown, jurisdiction/auth answers stop the read, and the tab is
owner-only until opened."""

import asyncio
import base64
from decimal import Decimal
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.kalshi import member as links
from app.members import service as members
from app.models.kalshi import KalshiLink
from app.models.members import Member
from tests.test_production import prod  # noqa: F401  (fixture)

KEY_ID = "0a472aed-0000-4666-97fe-10e018905fef"  # TEST FIXTURE: not a real key id


def _pem(key: Any) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


class FakeKalshi:
    """GET-only Kalshi with one YES position (standard fee) and one NO position (unknown fee model)."""

    def __init__(self, key: Any, *, status: int = 200, error: str = "") -> None:
        self.pub, self.status, self.error, self.calls = key.public_key(), status, error, []

    def _verify(self, req: httpx.Request) -> None:
        msg = (req.headers["KALSHI-ACCESS-TIMESTAMP"] + req.method + req.url.path).encode()
        sig = base64.b64decode(req.headers["KALSHI-ACCESS-SIGNATURE"])
        if isinstance(self.pub, rsa.RSAPublicKey):
            self.pub.verify(sig, msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        else:
            self.pub.verify(sig, msg)
        assert req.headers["KALSHI-ACCESS-KEY"] == KEY_ID

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.method == "GET", f"only GETs allowed, got {req.method} {req.url.path}"
        self._verify(req)
        p = req.url.path.removeprefix("/trade-api/v2")
        self.calls.append(p)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": "x", "message": self.error}})
        body: dict[str, Any] = {
            "/portfolio/balance": {"balance": 34818, "portfolio_value": 1200},
            "/portfolio/positions": {"cursor": "", "market_positions": [
                {"ticker": "M-YES", "position_fp": "10.00", "market_exposure_dollars": "4.0000", "realized_pnl_dollars": "0", "fees_paid_dollars": "0.17"},
                {"ticker": "M-NO", "position_fp": "-5.00", "market_exposure_dollars": "2.0000"},
                {"ticker": "M-FLAT", "position_fp": "0.00"}]},
            "/portfolio/orders": {"orders": [{"ticker": "M-YES", "side": "yes", "action": "buy", "type": "limit", "yes_price_dollars": "0.3500",
                                              "remaining_count_fp": "3.00"}]},
            "/portfolio/fills": {"fills": []},
            "/portfolio/settlements": {"settlements": [{"ticker": "S", "market_result": "yes", "yes_count_fp": "2.00", "no_count_fp": "0.00",
                                                        "yes_total_cost_dollars": "1.20", "no_total_cost_dollars": "0", "fee_cost": "0.03", "revenue": 200}]},
            "/markets/M-YES": {"market": {"ticker": "M-YES", "title": "Rain in NYC?", "event_ticker": "E1", "yes_bid_dollars": "0.5000",
                                          "yes_ask_dollars": "0.5200", "no_bid_dollars": "0.4800", "status": "active"}},
            "/markets/M-NO": {"market": {"ticker": "M-NO", "title": "Flat-fee market", "event_ticker": "E2", "yes_bid_dollars": "0.3000",
                                         "yes_ask_dollars": "0.3300", "no_bid_dollars": "0.6700", "status": "active"}},
            "/events/E1": {"event": {"event_ticker": "E1", "series_ticker": "S1", "category": "Climate and Weather"}},
            "/events/E2": {"event": {"event_ticker": "E2", "series_ticker": "S2", "category": "Other"}},
            "/series/S1": {"series": {"ticker": "S1", "fee_type": "quadratic", "fee_multiplier": 1}},
            "/series/S2": {"series": {"ticker": "S2", "fee_type": "flat", "fee_multiplier": 1}},
        }.get(p)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, json={"error": {"code": "not_found"}})


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    links._cache.clear()
    links._series.clear()
    links._lifetime.clear()
    monkeypatch.delenv("KALSHI_FEE_COEFFICIENT", raising=False)


def _member(db: Session, client: TestClient, name: str = "kal") -> Member:
    m = members.create_member(db)
    m.username, m.username_key = name, name
    db.commit()
    client.cookies.set("pc_session", members.new_session(db, m, 7))
    return m


def _fake(monkeypatch: pytest.MonkeyPatch, fake: FakeKalshi) -> None:
    monkeypatch.setattr(links, "transport", httpx.MockTransport(fake))


def _connect(client: TestClient, pem: str, **over: Any) -> httpx.Response:
    return client.post("/api/members/kalshi/connect", json={"env": "production", "key_id": KEY_ID, "private_key": pem, "confirm": True, **over})


@pytest.mark.parametrize("make", [ed25519.Ed25519PrivateKey.generate, lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)])
def test_connect_checks_the_key_seals_it_and_reads_the_account_live(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, make: Any) -> None:
    key = make()
    fake = FakeKalshi(key)
    _fake(monkeypatch, fake)
    m = _member(db, client)
    links.set_open(db, True)
    r = _connect(client, _pem(key))
    assert r.status_code == 200, r.text
    assert r.json()["balance_usd"] == 348.18 and fake.calls == ["/portfolio/balance"]
    row = db.query(KalshiLink).filter_by(member_id=m.id).one()
    assert "PRIVATE KEY" not in row.key_enc and row.key_type in ("ed25519", "rsa")  # sealed at rest
    a = client.get("/api/members/kalshi/account").json()
    assert a["balance_usd"] == 348.18 and a["positions_total"] == 2
    yes = next(p for p in a["positions"] if p["ticker"] == "M-YES")
    # 10 YES at the 50c bid = $5.00; fee 0.07 x 10 x 0.5 x 0.5 = $0.175 (already on the $0.0001 grid); cost $4.00
    assert (yes["side"], yes["value_now_usd"], yes["exit_fee_usd"], yes["net_if_sold_usd"]) == ("yes", 5.0, 0.175, 4.825)
    assert yes["pnl_if_sold_usd"] == pytest.approx(0.825) and yes["pays_if_right_usd"] == 10 and yes["category"] == "Climate and Weather"
    no = next(p for p in a["positions"] if p["ticker"] == "M-NO")
    assert no["side"] == "no" and no["bid"] == 0.67 and no["exit_fee_usd"] is None and no["fee_model"] == "flat"  # never guessed
    assert a["totals"]["unpriced"] == 1 and a["orders"][0]["price"] == 0.35 and a["settlements"][0]["revenue_usd"] == 2.0
    # 2 YES won $2.00 on $1.20 cost + $0.03 fee
    assert a["lifetime"] == {"realized_pnl_usd": 0.77, "settled": 1, "wins": 1, "losses": 0, "win_rate": 1.0, "complete": True}
    n = len(fake.calls)
    client.get("/api/members/kalshi/account")
    assert len(fake.calls) == n  # cached: tabs polling don't multiply Kalshi calls
    assert client.get("/api/members/kalshi").json()["link"]["key_id"].startswith("0a472aed…")


def test_bad_keys_are_refused_before_anything_is_kept(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    _member(db, client)
    links.set_open(db, True)
    _fake(monkeypatch, FakeKalshi(key))
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    assert "public key" in _connect(client, pub).json()["detail"]
    assert _connect(client, "hello").status_code == 400
    assert _connect(client, _pem(key), key_id="nope").status_code == 400
    assert _connect(client, _pem(key), confirm=False).status_code == 400
    _fake(monkeypatch, FakeKalshi(key, status=401, error="NOT_FOUND"))
    r = _connect(client, _pem(key))
    assert r.status_code == 401 and "refused the key" in r.json()["detail"]
    assert db.query(KalshiLink).count() == 0


def test_a_jurisdiction_answer_stops_and_is_shown(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    _member(db, client)
    links.set_open(db, True)
    _fake(monkeypatch, FakeKalshi(key))
    assert _connect(client, _pem(key)).status_code == 200
    fake = FakeKalshi(key, status=403, error="Trading is not available in your region")
    _fake(monkeypatch, fake)
    r = client.get("/api/members/kalshi/account")
    assert r.status_code == 451 and "can't be used from here" in r.json()["detail"]
    assert db.query(KalshiLink).one().last_error  # shown on the tab


def test_owner_only_until_opened_and_disconnect_forgets_the_key(client: TestClient, db: Session, prod: TestClient,  # noqa: F811
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    _fake(monkeypatch, FakeKalshi(key))
    r = prod.post("/api/members/kalshi/connect", json={"env": "production", "key_id": KEY_ID, "private_key": _pem(key), "confirm": True})
    assert r.status_code == 403  # closed, not the owner
    assert prod.get("/api/members/kalshi").json()["available"] is False
    assert prod.post("/api/kalshi/admin/members", json={"open": True}).status_code == 401  # owner login needed
    _member(db, client)
    links.set_open(db, True)
    assert _connect(client, _pem(key)).status_code == 200
    assert client.delete("/api/members/kalshi").status_code == 200
    assert db.query(KalshiLink).count() == 0
    assert client.get("/api/members/kalshi/account").status_code == 404


def test_exit_fee_rounds_up_to_the_balance_grid_and_follows_the_series() -> None:
    assert links.exit_fee("quadratic", 1, Decimal("0.33"), Decimal(1)) == Decimal("0.0155")  # 0.015477 -> up
    assert links.exit_fee("quadratic_with_maker_fees", "0.5", Decimal("0.5"), Decimal(10)) == Decimal("0.0875")
    assert links.exit_fee("flat", 1, Decimal("0.5"), Decimal(1)) is None
    assert links.exit_fee("quadratic", 1, Decimal("0"), Decimal(5)) == 0


def test_bot_activity_is_public_without_the_owners_account(client: TestClient, db: Session) -> None:
    from app.kalshi import service

    token = service.new_token(db)
    act = {"cycle": 7, "mode": "paper", "equity_usd": 348.18, "deployable_usd": 174.09, "trades": [{"event": "E", "status": "paper"}],
           "recent_trades": [{"ticker": "E-0"}], "counts": {"over_one_dollar": 46}, "closest": [{"event": "KXA", "edge_cents": -0.121}],
           "steps": [{"ms": 0, "stage": "wake", "text": "cycle 7"}, {"ms": 444, "stage": "account", "text": "equity $348.18"},
                     {"ms": 21857, "stage": "scan", "text": "read 13,684 open events"}]}
    r = client.post("/api/kalshi/bot/report", json={"kind": "activity", "payload": act, "bot": {"env": "production"}},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    pub = client.get("/api/members/kalshi/activity").json()
    a = pub["activity"]
    assert pub["owner"] is False and a["live"] is True and a["cycle"] == 7 and a["closest"][0]["event"] == "KXA"
    assert "equity_usd" not in a and "trades" not in a and "recent_trades" not in a
    assert [s["stage"] for s in a["steps"]] == ["wake", "scan"] and "348" not in str(a)  # the owner's balance never leaks
    assert a["history"][-1]["over_one_dollar"] == 46 and a["history"][-1]["trades"] is None
    full = service.activity(db, owner=True)
    assert full["equity_usd"] == 348.18 and full["trades"][0]["status"] == "paper" and full["history"][-1]["trades"] == 1


def test_lifetime_pages_every_settlement_and_counts_matched_pairs() -> None:
    lula = {"ticker": "KXBRPRES-26-LULA", "market_result": "no", "settled_time": "2026-10-26T01:09:02Z", "yes_count_fp": "166.00",
            "no_count_fp": "166.00", "yes_total_cost_dollars": "78.00", "no_total_cost_dollars": "96.96", "fee_cost": "5.73", "revenue": 0}
    win = {"ticker": "W", "market_result": "yes", "settled_time": "2026-10-20T00:00:00Z", "yes_count_fp": "2.00", "no_count_fp": "0.00",
           "yes_total_cost_dollars": "1.20", "no_total_cost_dollars": "0", "fee_cost": "0.03", "revenue": 200}
    pages = {None: {"settlements": [win], "cursor": "p2"}, "p2": {"settlements": [lula], "cursor": ""}}

    class K:
        calls: list[Any] = []

        async def get(self, path: str, **params: Any) -> dict[str, Any]:
            self.calls.append(params.get("cursor"))
            return pages[params.get("cursor")]

    class L:
        id = 1

    k = K()
    rows, complete = asyncio.run(links._all_settled(k, L()))  # type: ignore[arg-type]
    assert k.calls == [None, "p2"] and complete and [r["ticker"] for r in rows] == ["KXBRPRES-26-LULA", "W"]  # newest first
    assert (rows[0]["payout_usd"], rows[0]["pnl_usd"]) == (166.0, -14.69)  # 166 matched pairs paid $1 each when they netted
    assert links.lifetime(rows, complete) == {"realized_pnl_usd": -13.92, "settled": 2, "wins": 1, "losses": 1, "win_rate": 0.5, "complete": True}
    assert links.lifetime([], True)["win_rate"] is None
