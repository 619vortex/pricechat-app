"""Kiwi Passport rank rewards: every rank boundary, members already past a rank, XP never touched by the reward system, the
server-side gates (beginner content, profile customization, Earn at Scout, Vortex Realm at Warrior, Knight, Royal), duplicate
claims, the admin workflow and its audit log, owner-only admin routes and wallet / account disconnect. No network."""


import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import rewards, service
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.members import Member, ReputationEvent
from app.models.rewards import RewardAudit, RewardClaim
from app.models.tradingbot import TbWallet
from app.models.workers import WkRole
from tests.test_production import prod  # noqa: F401  (fixture)

RANKS = ["egg", "chick", "kiwi", "scout", "warrior", "knight", "royal", "golden", "legend", "mythic"]


def member_at(db: Session, score: int, name: str = "kiwi_member") -> Member:
    """A member whose Kiwi Rank Score is exactly `score` (the ledger is the only way XP gets there)."""
    ensure_catalog(db)
    m = service.create_member(db)
    m.username, m.username_key = name, name.lower()
    db.commit()
    delta = score - m.xp_total
    if delta:
        ledger.adjust(db, m, delta, actor="test", reason="set rank", ref=f"set-{name}")
    db.refresh(m)
    assert m.rank_score == score
    return m


def min_xp(db: Session, key: str) -> int:
    ensure_catalog(db)
    return next(r.min_xp for r in ledger.ranks(db) if r.key == key)


def signed_in(client: TestClient, db: Session, m: Member) -> TestClient:
    client.cookies.set("pc_session", service.new_session(db, m, 7))
    return client


# --- catalog, boundaries, existing members --------------------------------------------------------------------------


def test_rank_order_and_thresholds_are_preserved(db: Session) -> None:
    ensure_catalog(db)
    rs = ledger.ranks(db)
    assert [r.key for r in rs] == RANKS
    assert [r.min_xp for r in rs] == [0, 50, 150, 400, 900, 1800, 3500, 6000, 10000, 16000]
    rows = rewards.reward_rows(db)
    assert rows["scout"].title == "UNLOCK EARN — Your Kiwi Trading Bot"
    assert rows["egg"].description == "Hatch your Kiwi to unlock your first exclusive content."
    for k in ("golden", "legend", "mythic"):
        assert rows[k].kind == "tba" and rows[k].title == "Exclusive reward — To Be Announced"


@pytest.mark.parametrize("key", RANKS[1:])
def test_every_rank_boundary(db: Session, key: str) -> None:
    need = min_xp(db, key)
    below, at = member_at(db, need - 1, "below"), member_at(db, need, "at")
    assert not rewards.unlocked(db, below, key)
    assert rewards.unlocked(db, at, key) == (rewards.reward_rows(db)[key].kind not in ("none", "tba"))  # TBA: nothing to unlock
    cards = {c["rank"]["key"]: c for c in rewards.overview(db, at)["ranks"]}
    assert cards[key]["reached"] and cards[key]["current"]
    assert not {c["rank"]["key"]: c for c in rewards.overview(db, below)["ranks"]}[key]["reached"]


def test_members_already_past_a_rank_qualify_and_their_xp_is_untouched(db: Session) -> None:
    m = member_at(db, 5000, "veteran")  # Royal Kiwi before the reward system existed
    before = (m.xp_total, m.rank_score, m.rank_level, db.query(ReputationEvent).filter_by(member_id=m.id).count())
    out = rewards.overview(db, m)
    unlocked = {c["rank"]["key"] for c in out["ranks"] if c["unlocked"]}
    assert unlocked == {"chick", "kiwi", "scout", "warrior", "knight", "royal"}  # egg has no reward; golden+ not reached
    db.expire_all()
    m = db.get(Member, m.id)
    assert (m.xp_total, m.rank_score, m.rank_level, db.query(ReputationEvent).filter_by(member_id=m.id).count()) == before


def test_disabling_a_reward_locks_it_on_the_server(db: Session, client: TestClient) -> None:
    m = member_at(db, min_xp(db, "chick"), "chick")
    rewards.update_reward(db, "dashboard", "chick", {"enabled": False})
    assert signed_in(client, db, m).get("/api/members/rewards/beginner").status_code == 403


def test_losing_xp_relocks_the_reward(db: Session) -> None:
    m = member_at(db, min_xp(db, "kiwi"), "dropper")
    assert rewards.unlocked(db, m, "kiwi")
    ledger.adjust(db, m, -10, actor="test", reason="reversal", ref="drop")
    assert not rewards.unlocked(db, m, "kiwi")


# --- Kiwi Chick: beginner content ---------------------------------------------------------------------------------


def test_beginner_content_is_gated_and_coming_soon_until_published(db: Session, client: TestClient) -> None:
    egg = member_at(db, 10, "egg_one")
    assert signed_in(client, db, egg).get("/api/members/rewards/beginner").status_code == 403
    chick = member_at(db, min_xp(db, "chick"), "chick_one")
    c = signed_in(client, db, chick)
    assert c.get("/api/members/rewards/beginner").json() == {"items": [], "coming_soon": True}
    draft = c.post("/api/rewards-admin/content", json={"title": "Reading a chart", "video_url": "https://youtu.be/abc", "guide_text": "Step 1"}).json()
    assert c.get("/api/members/rewards/beginner").json()["coming_soon"] is True  # drafts stay hidden
    c.put(f"/api/rewards-admin/content/{draft['id']}", json={"published": True})
    items = c.get("/api/members/rewards/beginner").json()["items"]
    assert [i["title"] for i in items] == ["Reading a chart"] and "published" not in items[0]
    assert c.post("/api/rewards-admin/content", json={"title": "x", "video_url": "javascript:alert(1)"}).status_code == 400


# --- Kiwi: profile customization ----------------------------------------------------------------------------------


def test_display_name_is_rank_gated_validated_and_rate_limited(db: Session, client: TestClient) -> None:
    low = member_at(db, min_xp(db, "kiwi") - 1, "low_kiwi")
    r = signed_in(client, db, low).patch("/api/members/rewards/profile", json={"display_name": "Bob"})
    assert r.status_code == 403
    assert signed_in(client, db, low).patch("/api/members/me", json={"display_name": "Bob"}).status_code == 400  # old path gated too

    m = member_at(db, min_xp(db, "kiwi"), "real_kiwi")
    c = signed_in(client, db, m)
    for bad in ("Adm1n Bob", "Ѕupport", "pricechat_official", "The Moderator", "xADMINx", "M0D", "a", "<script>", "x" * 40):
        assert c.patch("/api/members/rewards/profile", json={"display_name": bad}).status_code == 400, bad
    ok = c.patch("/api/members/rewards/profile", json={"display_name": "  Kiwi​   Bob  "})
    assert ok.status_code == 200 and ok.json()["display_name"] == "Kiwi Bob"  # zero-width removed, spaces collapsed
    again = c.patch("/api/members/rewards/profile", json={"display_name": "Kiwi Bobby"})
    assert again.status_code == 429  # rename cooldown
    db.expire_all()
    m = db.get(Member, m.id)
    assert (m.username, m.display_name) == ("real_kiwi", "Kiwi Bob")  # the username / id / sign-ins never change


def test_display_name_cannot_impersonate_a_worker_or_manager(db: Session, client: TestClient) -> None:
    staff = member_at(db, 20, "kiwiworker_jo")
    db.add(WkRole(member_id=staff.id, role="WORKER", granted_by="test"))
    db.commit()
    m = member_at(db, min_xp(db, "kiwi"), "copycat")
    c = signed_in(client, db, m)
    assert c.patch("/api/members/rewards/profile", json={"display_name": "KiwiW0rker Jo"}).status_code == 400


def test_profile_changes_only_ever_touch_the_signed_in_member(db: Session, client: TestClient) -> None:
    a, b = member_at(db, min_xp(db, "kiwi"), "alpha"), member_at(db, min_xp(db, "kiwi"), "bravo")
    r = signed_in(client, db, a).patch("/api/members/rewards/profile", json={"display_name": "Hacked", "member_id": b.public_id})
    assert r.status_code == 422  # there's no way to name another member: unknown fields are refused
    db.expire_all()
    assert db.get(Member, b.id).display_name_changed_at is None


def test_accent_is_a_kiwi_reward(db: Session, client: TestClient) -> None:
    low = member_at(db, 60, "accent_low")
    assert signed_in(client, db, low).patch("/api/members/rewards/profile", json={"accent": "gold"}).status_code == 403
    m = member_at(db, min_xp(db, "kiwi"), "accent_ok")
    c = signed_in(client, db, m)
    assert c.patch("/api/members/rewards/profile", json={"accent": "gold"}).json()["accent"] == "gold"
    assert c.patch("/api/members/rewards/profile", json={"accent": "rainbow"}).status_code == 400
    assert c.patch("/api/members/rewards/profile", json={"clear_accent": True}).json()["accent"] is None


# --- Scout: Earn ----------------------------------------------------------------------------------------------------


def test_earn_unlocks_at_scout_and_existing_traders_keep_it(db: Session) -> None:
    from app.tradingbot import service as earn

    s = earn.settings(db)
    assert s.min_rank_level == 1  # open to every member by default (0072); the owner can still require Scout
    s.min_rank_level = 4
    kiwi_rank = member_at(db, min_xp(db, "scout") - 1, "almost_scout")
    assert not earn.rank_info(db, kiwi_rank, s)["ok"]
    assert earn.access_ready(db, kiwi_rank)[1] and "Rank 4" in earn.access_ready(db, kiwi_rank)[1]
    scout = member_at(db, min_xp(db, "scout"), "scout")
    assert earn.rank_info(db, scout, s)["ok"] and rewards.unlocked(db, scout, "scout")
    trader = member_at(db, min_xp(db, "kiwi"), "old_trader")  # connected the bot when Earn opened at rank 3
    ledger.award(db, trader, "earn.bot_connected", "earn:bot", xp=50, source="earn")
    info = earn.rank_info(db, trader, s)
    assert info["ok"] and info["grandfathered"]


# --- Knight: trading reward -------------------------------------------------------------------------------------------


def _wallet(db: Session, m: Member, addr: str = "0x" + "ab" * 20) -> TbWallet:
    w = TbWallet(member_id=m.id, address=addr, verified_at=utcnow())
    db.add(w)
    db.commit()
    return w


def _activate_knight(c: TestClient, budget: int = 5) -> None:
    r = c.put("/api/rewards-admin/settings", json={"knight_status": "ACTIVE", "knight_budget": budget,
                                                    "knight_terms": "Trading credit, once per member.", "knight_regions": "Not in the US."})
    assert r.status_code == 200, r.text


def test_knight_reward_is_pending_activation_until_configured(db: Session, client: TestClient) -> None:
    m = member_at(db, min_xp(db, "knight"), "knight")
    _wallet(db, m)
    c = signed_in(client, db, m)
    assert c.get("/api/members/rewards").json()["knight"]["active"] is False
    assert c.post("/api/members/rewards/knight/claim", json={}).status_code == 409
    assert c.put("/api/rewards-admin/settings", json={"knight_status": "ACTIVE"}).status_code == 400  # terms first
    _activate_knight(c)
    k = c.get("/api/members/rewards").json()["knight"]
    assert k["active"] and k["regions"] == "Not in the US." and k["amount_usd"] == 10


def test_knight_claim_rules_duplicates_and_admin_workflow(db: Session, client: TestClient) -> None:
    low = member_at(db, min_xp(db, "knight") - 1, "squire")
    _wallet(db, low, "0x" + "01" * 20)
    c = signed_in(client, db, low)
    _activate_knight(c, budget=1)
    assert c.post("/api/members/rewards/knight/claim", json={}).status_code == 403

    nowallet = member_at(db, min_xp(db, "knight"), "no_wallet")
    assert signed_in(client, db, nowallet).post("/api/members/rewards/knight/claim", json={}).status_code == 409

    m = member_at(db, min_xp(db, "knight"), "sir_kiwi")
    _wallet(db, m)
    c = signed_in(client, db, m)
    first = c.post("/api/members/rewards/knight/claim", json={"note": "thanks"})
    assert first.status_code == 200 and first.json()["claim"]["status"] == "PENDING"
    assert c.post("/api/members/rewards/knight/claim", json={}).status_code == 409  # once per member
    cid = first.json()["claim"]["id"]
    assert c.post(f"/api/rewards-admin/claims/{cid}/fulfill", json={"reference": "x"}).status_code == 409  # approve first
    assert c.post(f"/api/rewards-admin/claims/{cid}/approve", json={}).json()["claim"]["status"] == "APPROVED"
    assert c.post(f"/api/rewards-admin/claims/{cid}/fulfill", json={}).status_code == 400  # needs a reference
    done = c.post(f"/api/rewards-admin/claims/{cid}/fulfill", json={"reference": "HL transfer 2026-10-17 by owner"})
    assert done.json()["claim"]["status"] == "FULFILLED"
    assert db.query(RewardClaim).filter_by(member_id=m.id).count() == 1
    actions = [a.action for a in db.scalars(select(RewardAudit).order_by(RewardAudit.id))]
    assert {"knight_claimed", "knight_approve", "knight_fulfill"} <= set(actions)

    other = member_at(db, min_xp(db, "knight"), "second_knight")
    _wallet(db, other, "0x" + "cd" * 20)
    oc = signed_in(client, db, other).post("/api/members/rewards/knight/claim", json={}).json()["claim"]["id"]
    assert client.post(f"/api/rewards-admin/claims/{oc}/approve", json={}).status_code == 409  # budget of 1 used


def test_knight_approval_refuses_a_changed_wallet(db: Session, client: TestClient) -> None:
    m = member_at(db, min_xp(db, "knight"), "switcher")
    w = _wallet(db, m)
    c = signed_in(client, db, m)
    _activate_knight(c)
    cid = c.post("/api/members/rewards/knight/claim", json={}).json()["claim"]["id"]
    w.address = "0x" + "ef" * 20  # disconnected and reconnected another wallet
    db.commit()
    assert c.post(f"/api/rewards-admin/claims/{cid}/approve", json={}).status_code == 409
    assert c.post(f"/api/rewards-admin/claims/{cid}/reject", json={"note": "wallet changed"}).status_code == 200
    assert c.post("/api/members/rewards/knight/claim", json={}).status_code == 409  # still only once
    assert c.post(f"/api/rewards-admin/claims/{cid}/reopen", json={}).json()["claim"]["status"] == "PENDING"


# --- Royal: WhatsApp VIP --------------------------------------------------------------------------------------------


def test_royal_vip_request_approval_link_and_revocation(db: Session, client: TestClient) -> None:
    link = "https://chat.whatsapp.com/SecretInvite123"
    knight = member_at(db, min_xp(db, "royal") - 1, "not_royal")
    c = signed_in(client, db, knight)
    assert c.post("/api/members/rewards/royal/request", json={}).status_code == 403
    assert c.put("/api/rewards-admin/settings", json={"vip_invite_url": "https://evil.example/x"}).status_code == 400
    s = c.put("/api/rewards-admin/settings", json={"vip_invite_url": link}).json()
    assert s["vip_invite_set"] and link not in str(s)
    assert link not in c.get("/api/rewards-admin/overview").text and link not in c.get("/api/rewards-admin/history").text

    m = member_at(db, min_xp(db, "royal"), "royal")
    c = signed_in(client, db, m)
    cid = c.post("/api/members/rewards/royal/request", json={}).json()["claim"]["id"]
    assert c.post("/api/members/rewards/royal/request", json={}).status_code == 409
    assert c.get("/api/members/rewards/royal").json()["invite_url"] is None  # pending: no link
    c.post(f"/api/rewards-admin/claims/{cid}/approve", json={})
    assert c.get("/api/members/rewards/royal").json()["invite_url"] == link
    assert link not in c.get("/api/members/rewards").text  # never in the general overview
    c.post(f"/api/rewards-admin/claims/{cid}/revoke", json={"note": "left"})
    assert c.get("/api/members/rewards/royal").json()["invite_url"] is None
    signed_in(client, db, knight)
    assert client.get("/api/members/rewards/royal").status_code == 403


# --- who can reach what -------------------------------------------------------------------------------------------


def test_reward_admin_routes_need_the_owner_login(prod: TestClient) -> None:  # noqa: F811
    for method, path in (("GET", "/api/rewards-admin/overview"), ("PUT", "/api/rewards-admin/settings"),
                         ("POST", "/api/rewards-admin/content"), ("GET", "/api/rewards-admin/claims"),
                         ("POST", "/api/rewards-admin/claims/x/approve"), ("GET", "/api/rewards-admin/history"),
                         ("PUT", "/api/rewards-admin/rewards/knight")):
        r = prod.request(method, path, json={})
        assert r.status_code == 401 and r.json()["code"] == "admin_auth", (method, path)
    assert prod.get("/api/members/rewards").status_code == 200  # the catalog is public
    assert prod.get("/api/members/realm").status_code == 401  # member sign-in
    assert prod.get("/api/members/realm/play/index.pck").status_code == 403  # the game's files too


def test_a_manager_role_grants_no_reward_admin_powers(db: Session, client: TestClient) -> None:
    mgr = member_at(db, min_xp(db, "knight"), "the_manager")
    db.add(WkRole(member_id=mgr.id, role="MANAGER", granted_by="test"))
    db.commit()
    from app.security import admin_auth

    for path in ("/api/rewards-admin/overview", "/api/rewards-admin/claims/x/approve"):
        assert not admin_auth.is_public("GET", path)  # owner-only whatever the member's roles


def test_disconnecting_an_account_keeps_earned_rewards(db: Session, client: TestClient) -> None:
    m = member_at(db, min_xp(db, "warrior"), "linked")
    service.link_identity(db, m, "telegram", "777", "linked_tg", None)
    service.link_identity(db, m, "google", "g-777", "linked@example.com", None)
    db.commit()
    service.disconnect(db, m, "telegram")
    assert rewards.unlocked(db, m, "warrior")  # earned XP stays; only Combot legacy points would go
    service.link_identity(db, m, "telegram", "777", "linked_tg", None)
    db.commit()
    assert rewards.unlocked(db, m, "warrior")


# --- Vortex Realm (the Warrior Kiwi game) ------------------------------------------------------------------------


@pytest.fixture
def game(tmp_path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A tiny stand-in for the exported web build (backend/realm/*.gz)."""
    import gzip

    from app.members import realm

    for name, body in (("index.html", b"<html>realm</html>"), ("index.pck", b"PCK" * 1000)):
        (tmp_path / f"{name}.gz").write_bytes(gzip.compress(body))
    monkeypatch.setattr(realm, "GAME_DIR", tmp_path)
    return tmp_path


def test_realm_is_warrior_and_up(db: Session, client: TestClient, game) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/members/realm").status_code == 401
    assert client.get("/api/members/realm/play/index.pck").status_code == 403
    c = signed_in(client, db, member_at(db, min_xp(db, "warrior") - 1, "almost"))
    s = c.get("/api/members/realm").json()
    assert s["unlocked"] is False and s["needs"] == {"name": "Warrior Kiwi", "min_xp": 900} and s["available"] is True
    assert c.get("/api/members/realm/play/index.pck").status_code == 403  # the files are gated too, not just the page
    c = signed_in(client, db, member_at(db, min_xp(db, "warrior"), "warrior"))
    assert c.get("/api/members/realm").json()["unlocked"] is True
    r = c.get("/api/members/realm/play/index.pck")
    assert r.status_code == 200 and r.content == b"PCK" * 1000 and r.headers["content-type"] == "application/octet-stream"


def test_realm_files_are_sent_gzipped_and_only_by_name(db: Session, client: TestClient, game) -> None:  # type: ignore[no-untyped-def]
    c = signed_in(client, db, member_at(db, min_xp(db, "royal"), "royal"))
    r = c.get("/api/members/realm/play/index.html", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and r.content == b"<html>realm</html>"  # (the client unzips)
    assert r.headers["cache-control"] == "private, no-cache"
    r = c.get("/api/members/realm/play/index.pck", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers and r.content == b"PCK" * 1000
    # every file revalidates (so a game update reaches players), and an unchanged one is a cheap 304
    assert r.headers["cache-control"] == "private, no-cache" and r.headers["etag"]
    again = c.get("/api/members/realm/play/index.pck", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    for bad in ("..%2Findex.html", "index.py", ".env", "secrets.json", "missing.wasm"):
        assert c.get(f"/api/members/realm/play/{bad}").status_code == 404, bad


def test_a_disabled_reward_locks_the_realm(db: Session, client: TestClient, game) -> None:  # type: ignore[no-untyped-def]
    c = signed_in(client, db, member_at(db, min_xp(db, "knight"), "knight_kiwi"))
    rewards.update_reward(db, "test", "warrior", {"enabled": False})
    assert c.get("/api/members/realm").json()["unlocked"] is False
    assert c.get("/api/members/realm/play/index.pck").status_code == 403


def test_the_owner_can_test_the_realm_without_a_rank(db: Session, client: TestClient, game) -> None:  # type: ignore[no-untyped-def]
    from app.security import admin_auth

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(admin_auth, "current", lambda _db, token: object() if token == "owner" else None)
        client.cookies.set("pc_admin", "owner")
        assert client.get("/api/members/realm").json()["unlocked"] is True
        assert client.get("/api/members/realm/play/index.pck").status_code == 200
        client.cookies.set("pc_admin", "someone-else")
        assert client.get("/api/members/realm").status_code == 401
