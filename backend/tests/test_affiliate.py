"""Hyperliquid affiliate tracking (admin only): referred accounts matched to Passport members, history from snapshots. A fake
Hyperliquid info API: nothing real is read."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.tradingbot import TbAffiliateSnapshot, TbEvent, TbWallet
from app.tradingbot import affiliate
from app.tradingos import execution
from tests.test_trading_os import kiwi

A, B, C = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40


def fake(volumes: dict[str, float]) -> Any:
    def post(body: dict[str, Any]) -> Any:
        if body["type"] == "referral":
            assert body["user"] == affiliate.WALLET
            return {"referredBy": None, "cumVlm": "10", "unclaimedRewards": "1.5", "claimedRewards": "0.0", "builderRewards": "0.0",
                    "referrerState": {"stage": "ready", "data": {"code": "BANTER", "nReferrals": len(volumes), "referralStates": [
                        {"cumVlm": str(v), "cumRewardedFeesSinceReferred": str(v * 0.0005), "cumFeesRewardedToReferrer": str(v * 0.00005),
                         "timeJoined": 1_790_000_000_000, "user": a} for a, v in volumes.items()]}}, "rewardHistory": []}
        if body["type"] == "clearinghouseState":
            return {"marginSummary": {"accountValue": "100", "totalNtlPos": "50"},
                    "assetPositions": [{"position": {"coin": "BTC", "szi": "0.001", "positionValue": "50", "unrealizedPnl": "1"}}] if body["user"] == A else []}
        if body["type"] == "spotClearinghouseState":
            return {"balances": [{"coin": "USDC", "total": "25"}]}
        raise AssertionError(body)
    return post


def test_referred_accounts_are_matched_to_passport_members(db: Session) -> None:
    affiliate._cache.clear()
    m1, m2, m3, m4 = kiwi(db, 3, "901"), kiwi(db, 3, "902"), kiwi(db, 3, "903"), kiwi(db, 3, "904")
    db.add(TbWallet(member_id=m1.id, address=A, verified_at=utcnow(), referral_status="verified", referral_code_seen="BANTER"))
    execution.account(db, m2).master_address = B  # matched through the trading account instead
    db.add(TbWallet(member_id=m3.id, address="0x" + "d" * 40, verified_at=utcnow(), referral_status="not_referred"))  # known, not under our code
    db.add(TbEvent(kind="ref_click", member_id=m4.id))  # clicked, never verified a wallet
    db.commit()
    r = affiliate.report(db, fake({A: 1000.0, B: 5000.0, C: 200.0}))
    assert r["master"]["code"] == "BANTER" and r["master"]["unclaimed"] == 1.5
    by = {x["address"]: x for x in r["referrals"]}
    assert by[A]["member"]["id"] == m1.id and by[A]["match"] == "wallet" and by[A]["account"]["positions"][0]["coin"] == "BTC"
    assert by[B]["member"]["id"] == m2.id and by[B]["match"] == "trading"
    assert by[C]["member"] is None  # a referral we can't tie to anyone: shown as unmatched, never guessed
    assert [x["address"] for x in r["referrals"]] == [B, A, C]  # by volume
    t = r["totals"]
    assert t["referrals"] == 3 and t["matched"] == 2 and t["volume"] == 6200 and t["active"] == 1 and t["balances"] == 3 * 125
    assert t["rewarded_to_us"] == pytest.approx(6200 * 0.00005)
    assert [x["member"]["id"] for x in r["not_listed"]] == [m3.id] and r["not_listed"][0]["our_check"] == "not_referred"
    assert [x["member"]["id"] for x in r["clicked_no_wallet"]] == [m4.id]


def test_snapshots_build_the_history_and_volume_changes(db: Session) -> None:
    affiliate.snapshot(db, fake({A: 1000.0}))
    old = db.query(TbAffiliateSnapshot).one()
    old.created_at = utcnow() - timedelta(hours=30)
    db.commit()
    affiliate.snapshot(db, fake({A: 1600.0, B: 10.0}))
    h = affiliate.history(db)
    assert [p["n_referrals"] for p in h["points"]] == [1, 2]
    assert h["volume_change"][A]["7d"] == 600 and h["volume_change"][B]["7d"] == 10


def test_the_dashboard_is_owner_only() -> None:
    from app.security.admin_auth import is_public

    for path in ("/api/tradingbot/admin/affiliate", "/api/tradingbot/admin/affiliate/snapshot"):
        assert not is_public("GET", path) and not is_public("POST", path)  # behind the owner login, never a member route
