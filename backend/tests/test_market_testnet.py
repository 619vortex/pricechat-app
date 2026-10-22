"""Testnet payments: a buyer's verified wallet pays a sandbox order with test USDC on Sepolia; the server reads the
transfer from the chain (here a FAKE JSON-RPC node) and settles only an exact, confirmed transfer from that wallet.
TEST FIXTURES only: synthetic transactions, well-known throwaway test keys, no real network."""

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.market import admin as market_admin, payments, service
from tests.test_market import _CARD, _sandbox, setup  # noqa: F401 - pytest fixture
from tests.test_market_wallets import KEY_A, KEY_B, _addr, _sign
from tests.test_portfolio import _member

RECEIVE = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
TOKEN = str(payments.NETWORKS["sepolia"]["token"]).lower()


class FakeChain:
    def __init__(self) -> None:
        self.head = 100
        self.txs: dict[str, dict] = {}

    def send(self, tx: str, frm: str, to: str, amount: int, block: int | None, token: str = TOKEN) -> None:
        self.txs[tx] = {"status": "0x1", "blockNumber": hex(block) if block else None, "logs": [{
            "address": token, "data": hex(amount),
            "topics": [payments.TRANSFER_TOPIC, "0x" + "0" * 24 + frm.lower()[2:], "0x" + "0" * 24 + to.lower()[2:]]}]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        m, p = body["method"], body["params"]
        if m == "eth_getTransactionReceipt":
            r = self.txs.get(p[0])
            result = r if r and r["blockNumber"] else None  # not mined yet -> null
        elif m == "eth_blockNumber":
            result = hex(self.head)
        else:  # eth_getBlockByNumber
            result = {"timestamp": hex(4_102_444_800)}  # 2100-01-01: always after the order was created
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


def _link(client: TestClient, key: int) -> str:
    a = _addr(key)
    c = client.post("/api/members/market/wallets/challenge", json={"address": a}).json()
    assert client.post("/api/members/market/wallets/verify", json={"address": a, "signature": _sign(c["message"], key)}).status_code == 200
    return a


@pytest.fixture
def chain(setup, monkeypatch):  # type: ignore[no-untyped-def]  # noqa: F811
    monkeypatch.setattr(setup, "market_testnet_receive", RECEIVE)
    fake = FakeChain()
    service._PROVIDERS["testnet"] = payments.EvmRpcProvider("https://rpc.test", RECEIVE, transport=httpx.MockTransport(fake.handler))
    yield fake
    service._PROVIDERS.clear()


def test_testnet_only_when_configured(client: TestClient, db: Session, setup) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    nets = [n["id"] for n in client.get("/api/members/market/status").json()["networks"]]
    assert "sepolia" not in nets  # no receiving test wallet configured: no testnet


def test_wallet_pays_a_sandbox_order_on_testnet_and_only_an_exact_confirmed_transfer_settles(client: TestClient, db: Session, chain) -> None:  # type: ignore[no-untyped-def]
    assert "sepolia" in [n["id"] for n in client.get("/api/members/market/status").json()["networks"]]
    _member(db, "tseller", client)
    lid = _sandbox(client, db, "tseller", 250)
    _member(db, "tbuyer", client)
    market_admin.set_seller(db, "tbuyer", sandbox=True)
    r = client.post(f"/api/members/market/listings/{lid}/buy", json={"network": "sepolia"}, headers={"Idempotency-Key": "testnet-key-1"})
    assert r.status_code == 400 and "verify a wallet" in r.text  # payments must come from a verified wallet
    mine = _link(client, KEY_A)
    o = client.post(f"/api/members/market/listings/{lid}/buy", json={"network": "sepolia"}, headers={"Idempotency-Key": "testnet-key-2"}).json()
    assert o["testnet"] is True and o["chain_id"] == 11155111 and o["pay_to"] == RECEIVE
    exact = int(o["amount_units"])
    assert 250_000_000 < exact < 250_010_000  # 250 test USDC + this order's unique sub-cent tail

    stranger = _addr(KEY_B)
    chain.send("0x" + "b" * 64, stranger, RECEIVE, exact, 90)  # right amount, but not from the buyer's verified wallet
    r = client.post(f"/api/members/market/orders/{o['id']}/tx", json={"tx": "0x" + "b" * 64}).json()
    assert r["status"] == "awaiting_payment" and r["payments"][-1]["status"] == "rejected" and "isn't verified" in r["payments"][-1]["problem"]

    chain.send("0x" + "c" * 64, mine, RECEIVE, exact + 1, 90)  # one base unit off: another order's tail, not this one
    r = client.post(f"/api/members/market/orders/{o['id']}/tx", json={"tx": "0x" + "c" * 64}).json()
    assert r["payments"][-1]["status"] == "rejected"

    tx = "0x" + "a" * 64
    chain.send(tx, mine, RECEIVE, exact, None)  # sent, not mined yet
    r = client.post(f"/api/members/market/orders/{o['id']}/tx", json={"tx": tx.upper().replace("0X", "0x")}).json()
    assert r["status"] == "awaiting_payment" and r["payments"][-1]["status"] == "pending"
    chain.send(tx, mine, RECEIVE, exact, 99)  # mined: 2 confirmations, 3 needed
    assert service.recheck_pending(db, client.app.state.runtime.settings) == 1  # type: ignore[attr-defined]
    assert client.get(f"/api/members/market/orders/{o['id']}").json()["payments"][-1]["confirmations"] == 2
    chain.head = 101
    r = client.post(f"/api/members/market/orders/{o['id']}/tx", json={"tx": tx}).json()  # the buyer's page asks again
    assert r["status"] == "completed", r
    assert r["payments"][-1]["explorer"] == f"https://sepolia.etherscan.io/tx/{tx}"

    lid2 = _sandbox(client, db, "tseller", 250)
    o2 = client.post(f"/api/members/market/listings/{lid2}/buy", json={"network": "sepolia"}, headers={"Idempotency-Key": "testnet-key-3"}).json()
    assert int(o2["amount_units"]) != exact or o2["id"] != o["id"]
    replay = client.post(f"/api/members/market/orders/{o2['id']}/tx", json={"tx": tx})
    assert replay.status_code == 409  # one transaction settles at most one order


def test_testnet_never_buys_a_real_card(client: TestClient, db: Session, chain) -> None:  # type: ignore[no-untyped-def]
    from app.members.service import MemberError
    from app.models.market import MarketListing

    member = _member(db, "realbuyer", client)
    _link(client, KEY_A)
    real = MarketListing(item_id=0, seller_id=0, status="active", price_cents=100, is_sandbox=False)  # not saved: only the rule is checked
    with pytest.raises(MemberError, match="only for sandbox"):
        service._new_order(db, client.app.state.runtime.settings, member, real, 100, "sepolia", "x")  # type: ignore[attr-defined]
