"""Marketplace wallets: pure-Python keccak / secp256k1 against published vectors, and proving a wallet by signature.
TEST FIXTURES only: well-known throwaway test keys, never real wallets."""

import hashlib
import hmac

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.market import evm
from tests.test_portfolio import _member

KEY_A = 0x4C0883A69102937D6231471B5DBB6204FE5129617082792AE468D01A3F362318  # eth-account docs test key
KEY_B = 0xB25C7DB31FEED9122727BF0939DC769A96564B2DE4C4726D035B36ECF1E5B364


def _sign(message: str, key: int) -> str:
    """What a wallet does for personal_sign (test only): deterministic k, low-s, v = 27/28."""
    h = evm.personal_hash(message)
    z = int.from_bytes(h, "big")
    k = int.from_bytes(hmac.new(key.to_bytes(32, "big"), h, hashlib.sha256).digest(), "big") % evm.N or 1
    R = evm._mul(k, evm.G)
    assert R is not None
    r = R[0] % evm.N
    s = pow(k, -1, evm.N) * (z + r * key) % evm.N
    v = R[1] & 1
    if s > evm.N // 2:
        s, v = evm.N - s, v ^ 1
    return "0x" + (r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([27 + v])).hex()


def _addr(key: int) -> str:
    p = evm._mul(key, evm.G)
    assert p is not None
    return evm.address_of(p)


def test_primitives_match_published_vectors() -> None:
    assert evm.keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert evm.keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    assert _addr(KEY_A) == "0x2c7536E3605D9C16a7a3D7b1898e529396a65c23"  # eth-account documentation
    assert evm.checksum("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed") == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # EIP-55
    assert evm.personal_hash("I♥SF").hex() == "1476abb745d423bf09273f1afd887d951181d25adc66c4834a70491911b7f750"  # EIP-191
    long = "x" * 500  # several keccak blocks
    assert evm.recover_personal(long, _sign(long, KEY_B)) == _addr(KEY_B)


def test_recovery_rejects_tampering() -> None:
    sig = _sign("hello", KEY_A)
    assert evm.recover_personal("hello", sig) == _addr(KEY_A)
    assert evm.recover_personal("hello!", sig) != _addr(KEY_A)  # another message -> another signer
    raw = bytes.fromhex(sig[2:])
    high_s = raw[:32] + (evm.N - int.from_bytes(raw[32:64], "big")).to_bytes(32, "big") + bytes([raw[64] ^ 1])
    try:
        evm.recover(evm.personal_hash("hello"), high_s)
        raise AssertionError("malleable high-s signature accepted")
    except ValueError:
        pass


def test_link_a_wallet_by_signature(client: TestClient, db: Session) -> None:
    _member(db, "walletowner", client)
    a = _addr(KEY_A)
    c = client.post("/api/members/market/wallets/challenge", json={"address": a.lower()}).json()
    assert c["address"] == a and "@walletowner" in c["message"] and "doesn't send a transaction" in c["message"]
    bad = client.post("/api/members/market/wallets/verify", json={"address": a, "signature": _sign(c["message"], KEY_B)})
    assert bad.status_code == 400 and "different wallet" in bad.text  # signed by someone else
    assert client.post("/api/members/market/wallets/verify", json={"address": a, "signature": _sign(c["message"], KEY_A)}).status_code == 400  # single use
    c = client.post("/api/members/market/wallets/challenge", json={"address": a}).json()
    r = client.post("/api/members/market/wallets/verify", json={"address": a, "signature": _sign(c["message"], KEY_A), "wallet": "OKX Wallet"})
    assert r.status_code == 200, r.text
    w = r.json()["wallets"]
    assert [(x["address"], x["wallet"], x["payout"]) for x in w] == [(a, "OKX Wallet", True)]  # the first wallet receives payouts

    b = _addr(KEY_B)
    c = client.post("/api/members/market/wallets/challenge", json={"address": b}).json()
    w = client.post("/api/members/market/wallets/verify", json={"address": b, "signature": _sign(c["message"], KEY_B), "wallet": "MetaMask"}).json()["wallets"]
    second = next(x for x in w if x["address"] == b)
    w = client.post(f"/api/members/market/wallets/{second['id']}/payout").json()["wallets"]
    assert [x["address"] for x in w if x["payout"]] == [b]
    w = client.delete(f"/api/members/market/wallets/{second['id']}").json()["wallets"]
    assert [(x["address"], x["payout"]) for x in w] == [(a, True)]  # payout moves back to the remaining wallet

    _member(db, "someoneelse", client)  # the same wallet can't be claimed by a second account
    c = client.post("/api/members/market/wallets/challenge", json={"address": a}).json()
    r = client.post("/api/members/market/wallets/verify", json={"address": a, "signature": _sign(c["message"], KEY_A)})
    assert r.status_code == 400 and "another Price Chat account" in r.text
    assert client.get("/api/members/market/wallets").json()["wallets"] == []
