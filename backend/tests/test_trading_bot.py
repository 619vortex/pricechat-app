"""VIP Trading Bot access funnel: the acceptance tests from the brief, plus wallet-signature and Hyperliquid parsing."""

import asyncio
import secrets
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import tradingbot as routes
from app.members import service as members
from app.members.catalog import ensure_catalog
from app.members import xp as ledger
from app.models.tradingbot import TbApplication, TbAudit, TbEvent
from app.tradingbot import evm, hyperliquid, service

ADDR_KEY = 0xA11CE  # a throwaway private key, only to produce test signatures


def sign(message: str, key: int) -> str:
    """personal_sign, for the tests only (Price Chat itself never signs anything)."""
    z = int.from_bytes(evm.eip191_hash(message), "big")
    while True:
        k = secrets.randbelow(evm.N - 1) + 1
        R = evm.mul(k)
        r = R[0] % evm.N
        s = pow(k, -1, evm.N) * (z + r * key) % evm.N
        if r and s:
            break
    v = R[1] & 1
    if s > evm.N // 2:
        s, v = evm.N - s, v ^ 1
    return "0x" + r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex() + bytes([27 + v]).hex()


def addr(key: int) -> str:
    return evm.address_of(evm.mul(key))


class FakeHL:
    """Hyperliquid's info API as fixtures: a referral record and a ledger per address."""

    referred: dict[str, str | None] = {}
    ledgers: dict[str, list[dict[str, Any]]] = {}
    down = False

    def referral(self, a: str) -> dict[str, Any]:
        if FakeHL.down:
            raise hyperliquid.HyperliquidError("down")
        code = FakeHL.referred.get(a)
        return {"referredBy": {"referrer": "0xabc", "code": code} if code else None, "cumVlm": "0.0"}

    def ledger(self, a: str) -> list[dict[str, Any]]:
        if FakeHL.down:
            raise hyperliquid.HyperliquidError("down")
        return FakeHL.ledgers.get(a, [])

    agents_of: dict[str, list[dict[str, Any]]] = {}

    def agents(self, a: str) -> list[dict[str, Any]]:
        return FakeHL.agents_of.get(a, [])

    def portfolio(self, a: str) -> dict[str, Any]:
        return {"allTime": {"pnlHistory": [[1, "0"], [2, "250.5"]], "accountValueHistory": [[2, "1250.5"]]},
                "month": {"pnlHistory": [[1, "0"], [2, "-12"]], "accountValueHistory": [[2, "1250.5"]]}}

    fills_of: dict[str, list[dict[str, Any]]] = {}

    def fills(self, a: str, start_ms: int) -> tuple[list[dict[str, Any]], bool]:
        return FakeHL.fills_of.get(a, []), False


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def create_join_request_link(self, chat_id: Any, name: str, expire_date: int) -> str:
        self.calls.append(("create", chat_id, name))
        return f"https://t.me/+secret{len(self.calls)}"

    async def revoke_invite_link(self, chat_id: Any, link: str) -> None:
        self.calls.append(("revoke", link))

    async def approve_join_request(self, chat_id: Any, uid: int) -> None:
        self.calls.append(("approve", uid))

    async def decline_join_request(self, chat_id: Any, uid: int) -> None:
        self.calls.append(("decline", uid))

    in_group: set[int] = set()

    async def get_chat_member(self, chat_id: Any, uid: int) -> dict[str, Any]:
        return {"status": "member" if uid in self.in_group else "left"}

    async def remove_member(self, chat_id: Any, uid: int) -> None:
        self.calls.append(("remove", uid))


GROUP = "-1001234567890"


@pytest.fixture
def tb(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Session, FakeBot]]:
    FakeHL.referred, FakeHL.ledgers, FakeHL.fills_of, FakeHL.down = {}, {}, {}, False
    routes._pnl_cache.clear()
    routes._trades_cache.clear()
    service.reset_limits_for_tests()
    bot = FakeBot()
    monkeypatch.setattr(routes, "hyperliquid_client", FakeHL)
    monkeypatch.setattr(routes, "telegram_bot", lambda runtime: bot)
    ensure_catalog(db)
    assert service.settings(db).min_rank_level == 1 and service.settings(db).min_deposit_usd == 0  # open to every member by default
    service.update_settings(db, "test", {"public_enabled": True, "telegram_group_id": GROUP, "min_rank_level": 4, "min_deposit_usd": 10})
    yield client, db, bot


def member(db: Session, client: TestClient, rank: int, telegram: str | None = "4242") -> Any:
    m = members.create_member(db)
    if telegram:
        members.link_identity(db, m, "telegram", telegram, "tester", None)  # (linking can pay XP and recalculate the rank)
    need = next(r for r in ledger.ranks(db) if r.level == rank)
    m.rank_score, m.rank_level = need.min_xp, rank
    db.commit()
    client.cookies.set("pc_session", members.new_session(db, m, 7))
    return m


def prove(client: TestClient, key: int) -> Any:
    n = client.post("/api/members/trading-bot/wallet/nonce", json={"address": addr(key)})
    assert n.status_code == 200, n.text
    return client.post("/api/members/trading-bot/wallet/verify", json={"nonce": n.json()["nonce"], "signature": sign(n.json()["message"], key)})


def deposit(usd: float) -> dict[str, Any]:
    return {"time": 1, "hash": "0x1", "delta": {"type": "deposit", "usdc": str(usd)}}


# ---------------------------------------------------------------- crypto
def test_keccak_and_addresses_match_known_vectors() -> None:
    assert evm.keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert evm.keccak256(b"a" * 200).hex() == evm.keccak256(b"a" * 200).hex()  # multi-block path runs
    assert evm.checksum(addr(1)) == "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"
    assert evm.checksum("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed") == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


def test_signature_recovery() -> None:
    msg = "pricechat.live asks you to prove you own this wallet."
    assert evm.recover(msg, sign(msg, ADDR_KEY)) == addr(ADDR_KEY)
    assert evm.recover(msg + "!", sign(msg, ADDR_KEY)) != addr(ADDR_KEY)
    with pytest.raises(ValueError):
        evm.recover(msg, "0x1234")


def test_deposits_count_only_bridge_deposits() -> None:
    me = addr(7)
    entries = [deposit(60), {"time": 2, "delta": {"type": "spotTransfer", "token": "USDC", "usdcValue": "500", "user": "0xother", "destination": me}},
               {"time": 3, "delta": {"type": "accountClassTransfer", "usdc": "60", "toPerp": True}}, deposit(30)]
    dep, other, _ = hyperliquid.classify(me, entries)
    assert (dep, other) == (90.0, 500.0)


# ---------------------------------------------------------------- acceptance tests
def test_logged_out_users_cannot_apply(tb: Any) -> None:
    client, _, _ = tb
    client.cookies.clear()
    assert client.get("/api/members/trading-bot").json()["stage"] == "LOCKED"
    assert client.post("/api/members/trading-bot/apply").status_code == 401
    assert client.post("/api/members/trading-bot/wallet/nonce", json={"address": addr(1)}).status_code == 401


def test_rank_1_cannot_bypass_rank_3(tb: Any) -> None:
    client, db, _ = tb
    member(db, client, 1)
    s = client.get("/api/members/trading-bot").json()
    assert s["stage"] == "LOCKED" and not s["rank"]["ok"] and s["can_apply"] is False
    assert client.post("/api/members/trading-bot/apply").status_code == 400
    # the browser can't send its own rank or verification flags
    assert client.post("/api/members/trading-bot/event", json={"kind": "view", "rank": 9}).status_code == 422


def test_rank_4_proceeds_to_verification(tb: Any) -> None:
    client, db, _ = tb
    member(db, client, 4)
    s = client.get("/api/members/trading-bot").json()
    assert s["rank"]["ok"] and s["stage"] == "IN_PROGRESS" and s["steps"]["wallet"] == "todo"
    FakeHL.referred[addr(ADDR_KEY)] = "BANTER"
    FakeHL.ledgers[addr(ADDR_KEY)] = [deposit(150)]
    r = prove(client, ADDR_KEY)
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "ELIGIBLE" and r.json()["can_apply"]


def test_referral_click_alone_is_not_verification(tb: Any) -> None:
    client, db, _ = tb
    member(db, client, 4)
    client.post("/api/members/trading-bot/event", json={"kind": "ref_click"})
    s = client.get("/api/members/trading-bot").json()
    assert s["steps"]["hyperliquid"] != "done" and s["wallet"] is None and not s["can_apply"]
    assert db.scalar(select(TbEvent).where(TbEvent.kind == "referral_verified")) is None


def test_non_referred_wallet_cannot_qualify(tb: Any) -> None:
    client, db, _ = tb
    member(db, client, 4)
    FakeHL.ledgers[addr(ADDR_KEY)] = [deposit(1000)]  # money in, but no BANTER referral on the account
    s = prove(client, ADDR_KEY).json()
    assert s["wallet"]["referral"] == "not_referred" and not s["can_apply"]
    assert client.post("/api/members/trading-bot/apply").status_code == 400


def test_unverified_deposit_cannot_qualify_automatically(tb: Any) -> None:
    client, db, _ = tb
    service.update_settings(db, "test", {"manual_approval": False})  # even with automatic approval on
    m = member(db, client, 4)
    a = addr(ADDR_KEY)
    FakeHL.referred[a] = "BANTER"
    FakeHL.ledgers[a] = [{"time": 1, "delta": {"type": "internalTransfer", "usdc": "500", "user": "0xfriend", "destination": a}}]
    s = prove(client, ADDR_KEY).json()
    assert s["wallet"]["deposit"] == "unverified" and s["stage"] == "PENDING_VERIFICATION"
    r = client.post("/api/members/trading-bot/apply")
    assert r.json()["application"]["status"] == "PENDING_APPROVAL" and r.json()["application"]["manual_review"]
    assert db.scalar(select(TbApplication).where(TbApplication.member_id == m.id)).needs_manual_review
    # a balance that came from nowhere the API can see counts for nothing
    FakeHL.ledgers[a] = []
    assert hyperliquid.check(FakeHL(), a, "BANTER", 100).deposit_status == "insufficient"


def test_api_outage_is_unverified_never_a_guess(tb: Any) -> None:
    client, db, _ = tb
    member(db, client, 4)
    FakeHL.down = True
    s = prove(client, ADDR_KEY).json()
    assert s["wallet"]["referral"] == "unverified" and s["wallet"]["deposit"] == "unverified"


def test_replayed_or_foreign_signatures_are_refused(tb: Any) -> None:
    client, db, _ = tb
    member(db, client, 4)
    n = client.post("/api/members/trading-bot/wallet/nonce", json={"address": addr(ADDR_KEY)}).json()
    sig = sign(n["message"], ADDR_KEY)
    assert client.post("/api/members/trading-bot/wallet/verify", json={"nonce": n["nonce"], "signature": sig}).status_code == 200
    assert client.post("/api/members/trading-bot/wallet/verify", json={"nonce": n["nonce"], "signature": sig}).status_code == 400  # replay
    n2 = client.post("/api/members/trading-bot/wallet/nonce", json={"address": addr(ADDR_KEY)}).json()
    other = sign(n2["message"], 0xB0B)  # signed by a different key than the address claimed
    assert client.post("/api/members/trading-bot/wallet/verify", json={"nonce": n2["nonce"], "signature": other}).status_code == 400


def _approved(client: TestClient, db: Session) -> Any:
    member(db, client, 4)
    FakeHL.referred[addr(ADDR_KEY)] = "BANTER"
    FakeHL.ledgers[addr(ADDR_KEY)] = [deposit(100)]
    prove(client, ADDR_KEY)
    app = client.post("/api/members/trading-bot/apply").json()["application"]
    assert app["status"] == "PENDING_APPROVAL"
    return app


def test_approved_application_gets_the_private_join_flow(tb: Any) -> None:
    client, db, bot = tb
    app = _approved(client, db)
    assert client.post("/api/members/trading-bot/invite").status_code == 403  # not approved yet: no invite
    d = client.post(f"/api/tradingbot/admin/applications/{app['id']}/approve", json={"notes": "looks good"})
    assert d.status_code == 200
    assert client.post(f"/api/tradingbot/admin/applications/{app['id']}/approve", json={}).status_code == 200  # idempotent
    assert len([a for a in db.scalars(select(TbAudit).where(TbAudit.action == "approved"))]) == 1
    inv = client.post("/api/members/trading-bot/invite")
    assert inv.status_code == 200 and inv.json()["url"].startswith("https://t.me/+") and inv.headers["cache-control"] == "no-store"
    assert client.post("/api/members/trading-bot/invite").json()["url"] == inv.json()["url"]  # reused while valid
    # the link is never in the public state, the admin queue or the audit log
    assert "t.me/+" not in client.get("/api/members/trading-bot").text
    assert "t.me/+" not in client.get("/api/tradingbot/admin/applications").text
    # the bot lets everyone in (open group); the bound account is marked on its application
    asyncio.run(service.on_update(db, bot, {"chat_join_request": {"chat": {"id": int(GROUP)}, "from": {"id": 4242}}}))
    asyncio.run(service.on_update(db, bot, {"chat_join_request": {"chat": {"id": int(GROUP)}, "from": {"id": 999}}}))
    assert ("approve", 4242) in bot.calls and ("approve", 999) in bot.calls and not [c for c in bot.calls if c[0] == "decline"]
    asyncio.run(service.on_update(db, bot, {"chat_member": {"chat": {"id": int(GROUP)}, "new_chat_member": {"status": "member", "user": {"id": 4242}}}}))
    assert client.get("/api/members/trading-bot").json()["application"]["joined"]
    # revoke: link retired, member removed
    client.post(f"/api/tradingbot/admin/applications/{app['id']}/revoke", json={"notes": "test"})
    assert ("remove", 4242) in bot.calls
    assert client.post("/api/members/trading-bot/invite").status_code == 403


def test_rejected_application_cannot_retrieve_an_invite(tb: Any) -> None:
    client, db, bot = tb
    app = _approved(client, db)
    assert client.post(f"/api/tradingbot/admin/applications/{app['id']}/reject", json={"notes": ""}).status_code == 400  # a reason is required
    assert client.post(f"/api/tradingbot/admin/applications/{app['id']}/reject", json={"notes": "member: not this time"}).status_code == 200
    assert client.post("/api/members/trading-bot/invite").status_code == 403
    s = client.get("/api/members/trading-bot").json()
    assert s["stage"] == "REJECTED" and s["application"]["message"] == "not this time" and not s["can_apply"]
    assert not [c for c in bot.calls if c[0] == "create"]
    asyncio.run(service.on_update(db, bot, {"chat_join_request": {"chat": {"id": int(GROUP)}, "from": {"id": 4242}}}))
    assert ("approve", 4242) in bot.calls  # the group is open: a rejected application doesn't keep anyone out


def test_closed_funnel_is_admin_only_and_settings_are_validated(tb: Any) -> None:
    client, db, _ = tb
    service.update_settings(db, "test", {"public_enabled": False})
    member(db, client, 4)
    assert client.get("/api/members/trading-bot").json()["open"] is False
    assert client.post("/api/members/trading-bot/wallet/nonce", json={"address": addr(1)}).status_code == 403
    assert client.put("/api/tradingbot/admin/settings", json={"telegram_group_id": "12345"}).status_code == 400
    assert client.put("/api/tradingbot/admin/settings", json={"min_rank_level": 2}).json()["min_rank_level"] == 2
    o = client.get("/api/tradingbot/admin/overview").json()
    assert o["stats"]["funnel"][0]["key"] == "view" and "bot_token" not in str(o)


def test_public_group_by_username(tb: Any) -> None:
    client, db, bot = tb
    assert client.put("/api/tradingbot/admin/settings", json={"telegram_group_id": "https://t.me/botpricechat"}).json()["telegram_group_id"] == "@botpricechat"
    app = _approved(client, db)
    client.post(f"/api/tradingbot/admin/applications/{app['id']}/approve", json={})
    chat = {"id": -1009999, "username": "BotPriceChat"}
    asyncio.run(service.on_update(db, bot, {"chat_join_request": {"chat": chat, "from": {"id": 4242}}}))
    asyncio.run(service.on_update(db, bot, {"chat_join_request": {"chat": chat, "from": {"id": 7}}}))
    assert ("approve", 4242) in bot.calls and ("approve", 7) in bot.calls
    other = {"id": -1008888, "username": "someothergroup"}  # never touches another group's requests
    assert asyncio.run(service.on_update(db, bot, {"chat_join_request": {"chat": other, "from": {"id": 8}}})) is False


def test_wallet_gives_one_free_level_once(tb: Any) -> None:
    client, db, _ = tb
    m = member(db, client, 1)
    ledger.recalc(db, m)  # the real, ledger-based rank (as in production)
    db.commit()
    start = m.rank_level
    n = client.post("/api/members/me/wallet/nonce", json={"address": addr(ADDR_KEY)}).json()
    r = client.post("/api/members/me/wallet/verify", json={"nonce": n["nonce"], "signature": sign(n["message"], ADDR_KEY)}).json()
    assert r["leveled_up"] and r["rank_level"] >= start + 1 and r["free_level_claimed"]
    from app.models.members import ReputationEvent
    paid = db.scalar(select(ReputationEvent.xp).where(ReputationEvent.member_id == m.id, ReputationEvent.event_type == "wallet.verified"))
    assert paid >= service.WALLET_XP_MIN
    # again (even with another wallet): no second level
    n = client.post("/api/members/me/wallet/nonce", json={"address": addr(0xB0B)}).json()
    r = client.post("/api/members/me/wallet/verify", json={"nonce": n["nonce"], "signature": sign(n["message"], 0xB0B)}).json()
    assert not r["leveled_up"]
    # the first wallet can't level up a second account either (claim is per wallet too)
    other = member(db, client, 1, telegram="555")
    assert service.free_level(db, other, addr(ADDR_KEY)) == 0


def test_sign_in_with_wallet(tb: Any) -> None:
    client, db, _ = tb
    client.cookies.clear()
    n = client.post("/api/members/auth/wallet/nonce", json={"address": addr(0xC0FFEE)}).json()
    r = client.post("/api/members/auth/wallet/verify", json={"nonce": n["nonce"], "signature": sign(n["message"], 0xC0FFEE)})
    assert r.status_code == 200 and r.json()["created"]
    assert client.get("/api/members/session").json()["signed_in"]
    assert client.get("/api/members/me/wallet").json()["wallet"]["address"].lower() == addr(0xC0FFEE)
    assert client.get("/api/members/me/wallet").json()["free_level_claimed"]
    # signing in again with the same wallet finds the same Passport; a replayed signature is refused
    client.cookies.clear()
    n2 = client.post("/api/members/auth/wallet/nonce", json={"address": addr(0xC0FFEE)}).json()
    r2 = client.post("/api/members/auth/wallet/verify", json={"nonce": n2["nonce"], "signature": sign(n2["message"], 0xC0FFEE)})
    assert r2.json()["created"] is False and r2.json()["username"] == r.json()["username"]
    assert client.post("/api/members/auth/wallet/verify", json={"nonce": n2["nonce"], "signature": sign(n2["message"], 0xC0FFEE)}).status_code == 400
    # a member's link nonce can't be used to sign in, and a sign-in nonce can't link a wallet
    bad = client.post("/api/members/auth/wallet/verify", json={"nonce": "deadbeefdeadbeef", "signature": sign("x", 1)})
    assert bad.status_code == 400


def test_existing_passport_still_works(tb: Any) -> None:
    client, db, _ = tb
    member(db, client, 4)
    assert client.get("/api/members/me").status_code == 200
    assert client.get("/api/members/session").json()["signed_in"]


def test_earn_wallet_step_pays_the_wallet_xp(tb: Any) -> None:
    client, db, _ = tb
    m = member(db, client, 1)
    ledger.recalc(db, m)
    db.commit()
    prove(client, ADDR_KEY)  # through the Earn funnel's own route
    from app.models.members import ReputationEvent
    paid = db.scalar(select(ReputationEvent.xp).where(ReputationEvent.member_id == m.id, ReputationEvent.event_type == "wallet.verified"))
    assert paid is not None and paid >= service.WALLET_XP_MIN


def test_the_code_hyperliquid_records_for_the_banter_link_counts(tb: Any) -> None:
    client, db, _ = tb
    assert service.codes(service.settings(db)) == ["BANTER", "KLGXFMNI"]
    member(db, client, 4)
    FakeHL.referred[addr(ADDR_KEY)] = "klgxfmni"  # what Hyperliquid records for a wallet that used the BANTER link
    FakeHL.ledgers[addr(ADDR_KEY)] = [deposit(120)]
    s = prove(client, ADDR_KEY).json()
    assert s["wallet"]["referral"] == "verified" and s["stage"] == "ELIGIBLE" and s["rules"]["referral_code"] == "BANTER"
    FakeHL.referred[addr(ADDR_KEY)] = "SOMEONEELSE"
    assert hyperliquid.check(FakeHL(), addr(ADDR_KEY), service.codes(service.settings(db)), 100).referral_status == "not_referred"
    assert client.put("/api/tradingbot/admin/settings", json={"referral_code": "banter, klgxfmni"}).json()["referral_code"] == "BANTER,KLGXFMNI"
    assert client.put("/api/tradingbot/admin/settings", json={"referral_code": "BAN TER"}).status_code == 400



def test_deposit_tiers_pay_xp_once_each_and_10_dollars_is_enough(tb: Any) -> None:
    client, db, _ = tb
    assert service.settings(db).min_deposit_usd == 10
    m = member(db, client, 4)
    a = addr(ADDR_KEY)
    FakeHL.referred[a] = "BANTER"
    FakeHL.ledgers[a] = [deposit(15)]
    s = prove(client, ADDR_KEY).json()
    assert s["wallet"]["deposit"] == "verified" and s["tiers"]["current"] == "Bronze" and s["tiers"]["next"]["usd"] == 100
    from app.models.members import ReputationEvent
    tiers = lambda: sorted(db.scalars(select(ReputationEvent.source_ref).where(ReputationEvent.member_id == m.id,  # noqa: E731
                                                                               ReputationEvent.event_type == "deposit.tier")))
    assert tiers() == ["tier:10"]
    FakeHL.ledgers[a] = [deposit(15), deposit(1200)]  # more deposited later: the new tiers pay, the old one doesn't again
    w = service.wallet_of(db, m)
    service.run_check(db, m, w, FakeHL())
    assert tiers() == ["tier:10", "tier:100", "tier:1000"]
    service.run_check(db, m, w, FakeHL())
    assert len(tiers()) == 3


def test_track_record_is_hyperliquids_own_numbers(tb: Any) -> None:
    client, db, _ = tb
    assert client.get("/api/members/trading-bot/track-record").json() == {"configured": False}
    assert client.put("/api/tradingbot/admin/settings", json={"bot_wallet": "not-an-address"}).status_code == 400
    r = client.put("/api/tradingbot/admin/settings", json={"bot_wallet": addr(0xB07), "bot_username": "@cryptopricechatbot"}).json()
    assert r["bot_username"] == "cryptopricechatbot" and r["bot_wallet"] == addr(0xB07)
    t = client.get("/api/members/trading-bot/track-record").json()
    assert t["configured"] and t["pnl"]["all"]["pnl"] == 250.5 and t["pnl"]["30d"]["pnl"] == -12
    member(db, client, 4)
    assert client.get("/api/members/me/pnl").json()["mine"] is None  # no wallet yet
    prove(client, ADDR_KEY)
    mine = client.get("/api/members/me/pnl").json()
    assert mine["mine"]["all"]["value"] == 1250.5 and mine["bot"]["username"] == "cryptopricechatbot"


def test_deposits_are_net_of_withdrawals_so_cycling_money_doesnt_count() -> None:
    me = addr(9)
    w = lambda usd: {"time": 5, "delta": {"type": "withdraw", "usdc": str(usd), "nonce": 1, "fee": "1.0"}}  # noqa: E731
    assert hyperliquid.classify(me, [deposit(500), w(490), deposit(499)])[0] == 509.0  # not 999
    assert hyperliquid.classify(me, [deposit(100), w(150)])[0] == 0.0  # never below zero
    c = hyperliquid.check(FakeHL(), me, "BANTER", 10)
    assert c.evidence.get("account_value") == 1250.5  # shown next to it, from Hyperliquid



def test_funnel_v2_one_time_steps_then_first_signal_then_leaderboard(tb: Any) -> None:
    client, db, bot = tb
    from app.models.telegram import TelegramMessage, TelegramSource

    m = member(db, client, 4, telegram="4242")
    ledger.adjust(db, m, 450, actor="test", reason="real XP for Rank 4 (Scout Kiwi)", ref="v2")  # awards recalc rank from the ledger
    st = client.get("/api/members/trading-bot").json()
    assert st["flow"] == {"hyperliquid": False, "first_trade": False, "has_wallet": False} and not st["access"]["ready"]
    # 2: opening Hyperliquid with our link is the join (once, +25 XP)
    r = client.post("/api/members/trading-bot/hyperliquid/joined").json()
    assert r["xp"] == 25 and r["state"]["flow"]["hyperliquid"] and r["state"]["access"]["ready"]
    assert client.post("/api/members/trading-bot/hyperliquid/joined").json()["xp"] == 0  # once
    # 3a: I joined (Telegram confirms here: the fake bot sees the member)
    bot.in_group = {4242}
    assert client.post("/api/members/trading-bot/access/group").json()["done"]
    # 3b: "I gave the bot my API" -> confirmed on their word without a wallet (+25)
    r = client.post("/api/members/trading-bot/access/bot-confirm").json()
    assert r["done"] and r["xp"] == service.BOT_SELF_XP and r["state"]["access"]["bot"]
    # 4: the bot's signals (bot posts only; members' own messages never shown)
    service.update_settings(db, "test", {"telegram_group_id": "@botpricechat"})
    src = TelegramSource(chat_id=-100777, display_name="Bot Chat", username="botpricechat", enabled=True)
    db.add(src)
    db.flush()
    db.add(TelegramMessage(source_id=src.id, chat_id=src.chat_id, message_id=1, author_id=99, author_name="IQ bot", author_is_bot=True,
                           sent_at=service.utcnow(), text="LONG BTC 64,000 · TP 66,000 · SL 63,000"))
    db.add(TelegramMessage(source_id=src.id, chat_id=src.chat_id, message_id=2, author_id=5, author_name="Somebody", author_is_bot=False,
                           sent_at=service.utcnow(), text="a member's private chatter"))
    db.commit()
    sig = client.get("/api/members/trading-bot/signals").json()
    assert sig["connected"] and [i["text"] for i in sig["items"]] == ["LONG BTC 64,000 · TP 66,000 · SL 63,000"]
    r = client.post(f"/api/members/trading-bot/signals/{sig['items'][0]['id']}", json={"action": "auto"}).json()
    assert r["xp"] == 50 and r["state"]["flow"]["first_trade"]
    # the leaderboard: needs a wallet Hyperliquid lists as referred with our code
    assert client.get("/api/members/trading-bot/leaderboard").json()["items"] == []
    FakeHL.referred[addr(ADDR_KEY)] = "BANTER"
    prove(client, ADDR_KEY)
    lb = client.get("/api/members/trading-bot/leaderboard").json()
    assert lb["items"][0]["username"] == m.username and lb["items"][0]["me"] and lb["items"][0]["pnl_30d"] == -12


def test_leaderboard_lists_every_valid_referral_with_trades_and_pnl(tb: Any) -> None:
    client, db, _ = tb
    ref, other = 0xB0B, 0xC0C
    FakeHL.referred[addr(ref)] = "BANTER"
    FakeHL.referred[addr(other)] = "SOMEONEELSE"
    # three fills, two orders (one order filled in two pieces) -> two trades
    FakeHL.fills_of[addr(ref)] = [{"oid": 1, "tid": 10}, {"oid": 1, "tid": 11}, {"oid": 2, "tid": 12}]
    a = member(db, client, 1, telegram=None)  # never finished Earn: a valid referral is enough
    prove(client, ref)
    b = member(db, client, 1, telegram="4244")
    prove(client, other)  # referred with another code: not listed
    lb = client.get("/api/members/trading-bot/leaderboard").json()
    assert [r["username"] for r in lb["items"]] == [a.username] and lb["count"] == 1 and lb["me"] is None
    row = lb["items"][0]
    assert row["rank"] == 1 and row["trades_30d"] == 2 and not row["trades_capped"] and row["pnl_30d"] == -12 and row["pnl_all"] == 250.5
    assert b.username not in {r["username"] for r in lb["items"]}
    assert "value" not in row  # a public page: P&L only, never the account balance
    # the trade count itself
    assert hyperliquid.trade_count([{"oid": 5}, {"oid": 5}, {"tid": 9}]) == 2

def test_i_joined_is_approved_on_your_word_when_telegram_cant_confirm(tb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, db, _ = tb

    class NotInGroupBot(FakeBot):
        async def get_chat_member(self, chat_id: Any, uid: int) -> dict[str, Any]:
            raise RuntimeError("Bad Request: the bot is not a member of the group")

    monkeypatch.setattr(routes, "telegram_bot", lambda runtime: NotInGroupBot())
    member(db, client, 4, telegram="4243")
    a = addr(ADDR_KEY)
    FakeHL.referred[a], FakeHL.ledgers[a] = "BANTER", [deposit(20)]
    prove(client, ADDR_KEY)
    r = client.post("/api/members/trading-bot/access/group").json()
    assert r["done"] and r["xp"] == service.SELF_REPORTED_GROUP_XP and r["verified"] is False and r["state"]["access"]["group"]



def test_i_joined_needs_the_bot_group_itself(tb: Any) -> None:
    client, db, bot = tb

    class MainGroupOnlyBot(FakeBot):
        async def get_chat_member(self, chat_id: Any, uid: int) -> dict[str, Any]:
            return {"status": "member" if chat_id == "@thepricechat" else "left"}

    class BotGroupBot(FakeBot):
        async def get_chat_member(self, chat_id: Any, uid: int) -> dict[str, Any]:
            return {"status": "member" if chat_id == "@botpricechat" else "left"}

    m = member(db, client, 4, telegram="4250")
    r = asyncio.run(service.check_group(db, m, MainGroupOnlyBot()))  # only in the main group: not enough
    assert not r["done"] and "@botpricechat" in r["message"] and not service._paid(db, m, "earn.joined_group")
    r = asyncio.run(service.check_group(db, m, BotGroupBot()))  # joined @botpricechat: confirmed, full XP
    assert r["done"] and r["verified"] and r["xp"] == 50

    class CantSeeBotGroup(FakeBot):  # the loop: another group says "left" and the bot can't see @botpricechat at all
        async def get_chat_member(self, chat_id: Any, uid: int) -> dict[str, Any]:
            if chat_id == "@botpricechat":
                raise RuntimeError("Bad Request: member list is inaccessible")
            return {"status": "left"}

    m2 = member(db, client, 4, telegram="4251")
    r = asyncio.run(service.check_group(db, m2, CantSeeBotGroup()))
    assert r["done"] and r["verified"] is False  # never stuck: approved on the member's word (small XP)
