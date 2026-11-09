"""Regression tests for the 2026-11 audit's Kiwi Passport findings (PP-2, PP-3, PP-4, PP-6, SEC-6).

Each test reproduces the audit's exploit (backend/tests/audit_passport_exploits.py on the audit branch) and asserts the
fixed behaviour.
"""

import threading
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import service
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.members import Member, ReputationEvent
from app.tradingbot import service as tb_service

# fixtures reused from the regular suite (imported so pytest registers them here)
from tests.test_trading_bot import addr, prove, sign, tb  # noqa: F401
from tests.test_packs import _set as pack_cards


def _signed_in(db: Session, c: TestClient, age_days: int = 5) -> Member:
    ensure_catalog(db)
    m = service.create_member(db)
    m.created_at = utcnow() - timedelta(days=age_days)
    db.commit()
    c.cookies.set("pc_session", service.new_session(db, m, 30))
    return m


def _wallet_xp(db: Session, member_id: int) -> int | None:
    return db.scalar(select(ReputationEvent.xp).where(ReputationEvent.member_id == member_id, ReputationEvent.event_type == "wallet.verified"))


# --- PP-2: the wallet reward is a flat 100 XP, not "enough to reach your next rank" -----------------------------------


def test_wallet_reward_is_flat_even_before_a_big_rank_gap(tb: Any) -> None:  # noqa: F811
    client, db, _ = tb
    m = _signed_in(db, client)
    ledger.adjust(db, m, 10000 - m.xp_total, actor="test", reason="stand-in for XP earned over time", ref="test-pp2")
    db.refresh(m)
    assert m.rank_level == 9  # Kiwi Legend
    assert prove(client, 0xF00D).status_code == 200
    db.refresh(m)
    assert _wallet_xp(db, m.id) == tb_service.WALLET_XP == 100  # was +6,000 (straight to Mythic)
    assert m.rank_level == 9


def test_knight_plus_wallet_is_not_royal(tb: Any) -> None:  # noqa: F811
    client, db, _ = tb
    m = _signed_in(db, client)
    ledger.adjust(db, m, 1800 - m.xp_total, actor="test", reason="stand-in for XP earned over time", ref="test-pp2-knight")
    db.refresh(m)
    assert m.rank_level == 6  # Knight
    assert prove(client, 0xF00E).status_code == 200
    db.refresh(m)
    assert _wallet_xp(db, m.id) == 100 and m.rank_level == 6  # was +1,700 and Royal (free VPN)


def test_wallet_reward_once_per_wallet_ever_whatever_the_letter_case(tb: Any) -> None:  # noqa: F811
    client, db, _ = tb
    first = _signed_in(db, client)
    a = addr(0xC0FFEE)
    assert tb_service.free_level(db, first, a.lower()) == 100
    db.commit()
    other = service.create_member(db)
    db.commit()
    assert tb_service.free_level(db, other, a.upper().replace("0X", "0x")) == 0  # the same wallet can't reward a second Passport
    assert tb_service.free_level(db, first, addr(0xC0FFEF)) == 0  # nor one Passport twice


# --- PP-4 / SEC-6: the wallet sign-in limit keys on the caller's address and the wallet, not the User-Agent ------------


def test_wallet_sign_in_limit_ignores_a_rotating_user_agent(tb: Any) -> None:  # noqa: F811
    client, db, _ = tb
    client.cookies.clear()
    before = db.scalar(select(func.count()).select_from(Member))
    created, refused = 0, 0
    for i in range(15):  # the limit is 10 per 10 minutes per caller
        headers = {"User-Agent": f"bot-{i}"}
        key = 0xBEEF00 + i
        n = client.post("/api/members/auth/wallet/nonce", json={"address": addr(key)}, headers=headers)
        if n.status_code != 200:
            assert n.status_code == 400 and "Too many tries" in n.text
            refused += 1
            continue
        r = client.post("/api/members/auth/wallet/verify", json={"nonce": n.json()["nonce"], "signature": sign(n.json()["message"], key)},
                        headers=headers)
        assert r.status_code == 200, r.text
        created += int(r.json()["created"])
        client.cookies.clear()
    assert created == 10 and refused == 5  # was 15 Passports, each with 120 XP
    assert db.scalar(select(func.count()).select_from(Member)) == before + 10


def test_wallet_sign_in_limit_per_wallet_across_addresses(tb: Any) -> None:  # noqa: F811
    client, _db, _ = tb
    client.cookies.clear()
    a = addr(0xABCDEF)
    codes = [client.post("/api/members/auth/wallet/nonce", json={"address": a}, headers={"X-Forwarded-For": f"203.0.113.{i + 1}"}).status_code
             for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 400  # a pool of network addresses doesn't buy one wallet unlimited tries


# --- PP-3: Pack Giveaway XP no longer eats the 60/day overall cap -----------------------------------------------------


def test_pack_xp_does_not_crowd_out_the_daily_visit_or_read_and_earn(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.members import packs

    pack_cards(db)
    monkeypatch.setattr(packs, "COOLDOWN", timedelta(0))
    m = _signed_in(db, client)  # +20 "Joined" (website)
    for _ in range(8):  # +40 pack XP: used to bring the day's automatic XP to the 60 cap
        assert client.post("/api/members/packs/base/open").json()["xp"] == 5
    client.get("/api/members/session")  # the daily visit
    assert db.scalar(select(ReputationEvent.id).where(ReputationEvent.member_id == m.id, ReputationEvent.event_type == "website.login"))
    r = ledger.award(db, m, "website.x_post_read", "x-read:1")
    assert r.awarded  # was refused "overall_cap"
    for _ in range(12):  # packs keep their own 100/day (20 packs)
        assert client.post("/api/members/packs/base/open").json()["xp"] == 5
    assert client.post("/api/members/packs/base/open").json()["xp"] == 0


def test_pack_xp_is_left_out_of_the_batch_totals_too(db: Session) -> None:
    ensure_catalog(db)
    m = service.create_member(db)
    m.created_at = utcnow() - timedelta(days=5)
    db.commit()
    day = ledger.day_of(utcnow())
    for i in range(8):
        assert ledger.award(db, m, "pack.open", f"pack:t{i}", source="website").awarded
    with ledger.batch(db):
        assert ledger.award(db, m, "website.x_post_read", "x-read:batch", commit=False).awarded
    db.commit()
    assert ledger._paid_today(db, m.id, day, sources=ledger.AUTOMATIC_SOURCES) == 20 + 1  # "Joined" + the read; no pack XP


# --- PP-6: daily caps hold under parallel requests ---------------------------------------------------------------------


def _race(db: Session, event_type: str, n: int = 30, prepaid: int = 0) -> tuple[Member, int, int]:
    from app.db.session import SessionLocal

    ensure_catalog(db)
    m = service.create_member(db)
    m.created_at = utcnow() - timedelta(days=5)
    db.commit()
    for i in range(prepaid):
        assert ledger.award(db, m, event_type, f"before-{i}").awarded
    barrier = threading.Barrier(n)
    results: list[bool] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        with SessionLocal() as s:
            mem = s.get(Member, m.id)
            ledger.get_rule(s, event_type)
            barrier.wait(timeout=120)  # (n stays under the 40-connection pool, or the barrier could never fill)
            try:
                r = ledger.award(s, mem, event_type, f"race-{i}")
                ok = r.awarded
            except Exception as e:  # noqa: BLE001 - would be "database is locked": must not happen
                ok = False
                with lock:
                    errors.append(repr(e))
            with lock:
                results.append(ok)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]
    db.expire_all()
    total = db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == m.id, ReputationEvent.event_type == event_type)) or 0
    return m, int(total), sum(results)


def test_parallel_awards_respect_a_daily_cap(db: Session) -> None:
    ensure_catalog(db)
    rule = ledger.get_rule(db, "website.x_post_read")
    assert rule.daily_cap == 10
    _m, total, awards = _race(db, "website.x_post_read")
    assert total == rule.daily_cap and awards == rule.daily_cap  # was 30 XP against a cap of 10


def test_parallel_awards_respect_the_overall_cap(db: Session) -> None:
    ensure_catalog(db)
    rule = ledger.get_rule(db, "website.x_post_read")
    rule.daily_cap = None  # only the overall 60/day cap is left
    db.commit()
    m, _total, _awards = _race(db, "website.x_post_read", n=30, prepaid=30)  # "Joined" 20 + 30 paid: 10 left of the 60
    day = ledger.day_of(utcnow())
    assert ledger._paid_today(db, m.id, day, sources=ledger.AUTOMATIC_SOURCES) == ledger.DAILY_XP_CAP
