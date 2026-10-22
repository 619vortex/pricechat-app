"""Pack Giveaway (just for fun). TEST FIXTURES only: a small fake Base Set."""

from collections import Counter

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.members import packs, service
from app.members.catalog import ensure_catalog
from app.models.members import Member, MemberStreak, ReputationEvent
from app.models.pokemon import PokemonCard


def _set(db: Session) -> None:
    """TEST FIXTURE cards: a tiny Base Set with every tier."""
    packs._pools.clear()
    n = 0
    for rarity, count in (("Common", 6), ("Uncommon", 4), ("Rare", 3), ("Rare Holo", 2), ("Rare Secret", 1)):
        for _ in range(count):
            n += 1
            db.add(PokemonCard(identity_key=f"test-pack-{n}", name="Charizard" if rarity == "Rare Holo" and n % 2 else f"Mon{n}", pokemon_name="Mon", set_name="Base Set",
                               card_number=f"{n}/102", year=1999, language="English", edition="Unlimited", variant="", rarity=rarity,
                               image_url=f"https://img.test/{n}.png", era="vintage"))
    db.commit()


def _member(db: Session, client: TestClient) -> Member:
    from datetime import timedelta

    from app.db.base import utcnow

    ensure_catalog(db)
    m = service.create_member(db)
    m.username, m.username_key = "packfan", "packfan"
    m.created_at = utcnow() - timedelta(days=5)
    db.commit()
    client.cookies.set("pc_session", service.new_session(db, m, 30))
    return m


def test_free_unlimited_packs_xp_for_three_a_day_streak_binder_and_level_up_packs(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _set(db)
    shelf = client.get("/api/members/packs").json()
    assert shelf["signed_in"] is False and {p["key"] for p in shelf["packs"]} >= {"base", "rocket", "destiny"} and len(shelf["packs"]) == 12
    assert all(p["year"] <= 2003 for p in shelf["packs"])  # original vintage packs only
    assert client.post("/api/members/packs/base/open").status_code == 401  # signed out: browse only
    m = _member(db, client)
    monkeypatch.setattr(packs, "COOLDOWN", __import__("datetime").timedelta(0))
    seen, xp = [], []
    for i in range(4):  # free and unlimited for now
        r = client.post("/api/members/packs/base/open")
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["cards"]) == 5 and d["cards"][0]["tier"] == "common" and d["cards"][-1]["tier"] in ("rare", "holo", "ultra")
        seen += [c["id"] for c in d["cards"]]
        xp.append(d["xp"])
    assert xp == [5, 5, 5, 5]  # +5 XP a pack (up to 100 XP a day)
    assert client.get("/api/members/packs").json()["xp_today"] == 20
    assert db.scalar(select(MemberStreak.current).where(MemberStreak.member_id == m.id)) == 1  # opening packs is a daily task
    b = client.get("/api/members/packs/binder").json()
    assert b["pulls"] == 20 and b["unique"] == len(set(seen))
    assert client.post("/api/members/packs/nope/open").status_code == 400

    # one boosted Level-up pack per Kiwi Passport level reached
    assert client.post("/api/members/packs/base/open?bonus=true").status_code == 429  # level 1: none yet
    from app.members import xp as ledger

    ledger.adjust(db, m, 200, actor="test", reason="reach rank 3", ref="t")  # real XP (packs now pay XP, which recalculates rank)
    assert m.rank_level == 3
    assert client.get("/api/members/packs").json()["level_packs"] == 2
    for _ in range(2):
        d = client.post("/api/members/packs/base/open?bonus=true").json()
        assert d["bonus"] and d["cards"][-1]["tier"] in ("holo", "ultra")  # guaranteed holo or better
    assert client.get("/api/members/packs").json()["level_packs"] == 0
    assert client.post("/api/members/packs/base/open?bonus=true").status_code == 429


def test_cooldown_stops_hammering(client: TestClient, db: Session) -> None:
    _set(db)
    _member(db, client)
    assert client.post("/api/members/packs/base/open").status_code == 200
    r = client.post("/api/members/packs/base/open")
    assert r.status_code == 429 and "One pack at a time" in r.json()["detail"]


def test_tiers_and_hit_odds(db: Session) -> None:
    assert packs.tier_of("Rare Holo", "1st Edition") == "ultra" and packs.tier_of("Rare Holo", "Shadowless") == "ultra"  # the vintage chase
    assert packs.tier_of("Rare Holo", "Unlimited") == "holo" and packs.tier_of("Rare", "1st Edition") == "rare"
    assert [packs.tier_of(r) for r in ("Common", "Uncommon", "Rare", "Rare Holo", "Double Rare", "Rare Holo V", "Rare Shining",
                                       "Special Illustration Rare", "Rare Secret", "Hyper Rare", "Rare Rainbow", None)] == [
        "common", "uncommon", "rare", "holo", "holo", "holo", "ultra", "ultra", "ultra", "ultra", "ultra", "common"]  # no rarity (energy): common
    assert packs.tier_of("Rare Holo", "Unlimited", "Reverse Holo") == "ultra" and packs.tier_of("Common", "Unlimited", "Reverse Holo") == "common"
    _set(db)
    hits = Counter(packs.draw(db, "base")[1][-1][1] for _ in range(3000))
    assert 0.04 < hits["ultra"] / 3000 < 0.12 and 0.2 < hits["holo"] / 3000 < 0.34 and hits["rare"] > hits["holo"]
    packs._pools.clear()
    assert db.scalar(select(PokemonCard.id).where(PokemonCard.set_name == "Team Rocket")) is None
    try:
        packs.draw(db, "rocket")
        raise AssertionError("an empty pack must not draw")
    except ValueError:
        pass


def test_binder_totals_every_pulled_copy_at_its_market_estimate(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from app.db.base import utcnow
    from app.models.members import MemberBinderCard
    from app.models.pokemon import PokemonProviderValuation

    _set(db)
    m = _member(db, client)
    ids = list(db.scalars(select(PokemonCard.id).where(PokemonCard.set_name == "Base Set").limit(3)))
    for i, cid in enumerate(ids):  # TEST FIXTURE: ungraded prices for two cards (NM/LP/MP), none for the third
        db.add(MemberBinderCard(member_id=m.id, card_id=cid, count=2 if i == 0 else 1, tier="common", pack_key="base", first_at=utcnow() - timedelta(minutes=i)))
        if i < 2:
            for cond, usd in (("NM", 10 + i), ("LP", 8 + i), ("MP", 6 + i)):
                db.add(PokemonProviderValuation(provider="scrydex", card_id=cid, provider_card_id="x", provider_variant="v", grading_company="RAW",
                                                grade=cond, day=utcnow().date(), kind="current", market_cents=usd * 100, currency="USD", fetched_at=utcnow()))
    db.commit()
    b = client.get("/api/members/packs/binder").json()
    vals = {c["id"]: c["value"] for c in b["cards"]}
    assert vals[ids[2]] is None and vals[ids[0]] and vals[ids[1]]  # unknown stays unknown, never $0 made up
    assert b["priced"] == 2 and b["value"] == vals[ids[0]] * 2 + vals[ids[1]]  # every copy counts
    assert client.get("/api/members/me").json()["level_packs"] == 0



def _printed_set(db: Session) -> None:
    """TEST FIXTURE cards: the same tiny Base Set in three print runs (1st Edition, Shadowless, Unlimited)."""
    packs._pools.clear()
    n = 0
    for edition in ("1st Edition", "Shadowless", "Unlimited"):
        for i, rarity in enumerate(["Common"] * 4 + ["Uncommon"] * 3 + ["Rare"] * 2 + ["Rare Holo"] * 2):
            n += 1
            db.add(PokemonCard(identity_key=f"test-print-{n}", name=f"Mon{i}", pokemon_name="Mon", set_name="Base Set", card_number=f"{i + 1}/102",
                               year=1999, language="English", edition=edition, variant="", rarity=rarity, image_url=f"https://img.test/p{n}.png", era="vintage"))
    db.commit()


def test_print_runs_are_rolled_per_pack_and_first_edition_is_rare(db: Session) -> None:
    _printed_set(db)
    by_card = {c.id: c.edition for c in db.scalars(select(PokemonCard))}
    runs = Counter()
    for _ in range(4000):
        run, cards = packs.draw(db, "base")
        runs[run] += 1
        assert {by_card[cid] for cid, _ in cards} == {run}  # all five cards come from the pack's own print run
    assert runs["1st Edition"] / 4000 < 0.025 and runs["Shadowless"] / 4000 < 0.07  # rare, like the real thing
    assert runs["Unlimited"] / 4000 > 0.9
    # in a 1st Edition pack the holo is the chase
    tiers = Counter(t for _ in range(400) for _, t in [packs.draw(db, "base", print_run="1st Edition")[1][-1]])
    assert tiers["ultra"] > 0 and tiers["holo"] == 0
    # Level-up packs get a better (still small) chance of a rare print
    boosted = Counter(packs.draw(db, "base", bonus=True)[0] for _ in range(4000))
    assert boosted["1st Edition"] > runs["1st Edition"]


def test_a_rarer_print_is_never_shown_below_the_commoner_one(db: Session) -> None:
    from app.db.base import utcnow
    from app.models.pokemon import PokemonProviderValuation

    _printed_set(db)
    card = {(c.edition, c.card_number): c for c in db.scalars(select(PokemonCard))}
    first, unl = card[("1st Edition", "10/102")], card[("Unlimited", "10/102")]
    other_first, other_unl = card[("1st Edition", "1/102")], card[("Unlimited", "1/102")]

    def raw(cid: int, usd: int) -> None:  # TEST FIXTURE ungraded prices
        for cond in ("NM", "LP", "MP"):
            db.add(PokemonProviderValuation(provider="scrydex", card_id=cid, provider_card_id="x", provider_variant="v", grading_company="RAW",
                                            grade=cond, day=utcnow().date(), kind="current", market_cents=usd * 100, currency="USD", fetched_at=utcnow()))
    raw(unl.id, 35)  # Unlimited priced, 1st Edition not: shown as "at least" the Unlimited price
    raw(other_first.id, 160)
    raw(other_unl.id, 5)  # both priced and in order: each keeps its own price
    db.commit()
    v = packs.values_for(db, [first, unl, other_first, other_unl])
    assert v[first.id] == (3500, True) and v[unl.id] == (3500, False)
    assert v[other_first.id] == (16000, False) and v[other_unl.id] == (500, False)
    out = packs.card_out(first, "ultra", v[first.id])
    assert out["value"] == 3500 and out["value_floor"] is True


def test_opt_in_adds_pulls_to_the_portfolio_as_private_virtual_holdings(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db.base import utcnow
    from app.models.pokemon import PokemonProviderValuation
    from app.models.portfolio import PokemonHolding, PokemonHoldingEvent, PokemonPortfolio

    _set(db)
    ids = list(db.scalars(select(PokemonCard.id).where(PokemonCard.set_name == "Base Set").limit(2)))
    for cid in ids:  # TEST FIXTURE ungraded prices: $10 each
        for cond in ("NM", "LP", "MP"):
            db.add(PokemonProviderValuation(provider="scrydex", card_id=cid, provider_card_id="x", provider_variant="v", grading_company="RAW",
                                            grade=cond, day=utcnow().date(), kind="current", market_cents=1000, currency="USD", fetched_at=utcnow()))
    db.commit()
    m = _member(db, client)
    monkeypatch.setattr(packs, "COOLDOWN", __import__("datetime").timedelta(0))
    monkeypatch.setattr(packs, "draw", lambda db, key, bonus=False: ("Unlimited", [(ids[0], "common"), (ids[0], "common"), (ids[1], "common"),
                                                                                   (ids[1], "common"), (ids[1], "common")]))
    assert client.post("/api/members/packs/base/open").status_code == 200
    assert client.get("/api/members/packs/binder").json()["in_portfolio"] is False  # off until the member opts in
    assert db.scalar(select(PokemonHolding.id)) is None
    assert client.post("/api/members/packs/portfolio", json={"show": True}).json() == {"in_portfolio": True}
    db.expire_all()
    hs = {h.card_id: h for h in db.scalars(select(PokemonHolding).where(PokemonHolding.removed_at.is_(None)))}
    assert {c: h.quantity for c, h in hs.items()} == {ids[0]: 2, ids[1]: 3}
    assert all(h.source == "giveaway" and h.verification == "virtual" and h.is_public is False and h.kind == "raw" for h in hs.values())
    pf = db.scalar(select(PokemonPortfolio).where(PokemonPortfolio.member_id == m.id))
    assert pf.value_cents and pf.public_value_cents == 0  # in the owner's value and chart; never in a public value / leaderboard
    # a new pack follows into the portfolio
    assert client.post("/api/members/packs/base/open").status_code == 200
    db.expire_all()
    assert sum(h.quantity for h in db.scalars(select(PokemonHolding).where(PokemonHolding.removed_at.is_(None)))) == 10
    # the virtual holdings follow the binder: they can't be edited or removed from the portfolio
    hid = next(iter(hs.values())).id
    from app.members.service import MemberError
    from app.portfolio import service as pfs

    try:
        pfs.remove_holding(db, m, hid)
        raise AssertionError("a virtual card must not be removed from the portfolio side")
    except MemberError:
        db.rollback()
    # opting out takes them out again, recorded as removals (the chart shows a flow, not a market move)
    assert client.post("/api/members/packs/portfolio", json={"show": False}).json() == {"in_portfolio": False}
    db.expire_all()
    assert db.scalar(select(PokemonHolding.id).where(PokemonHolding.removed_at.is_(None))) is None
    kinds = [e.kind for e in db.scalars(select(PokemonHoldingEvent))]
    assert "add" in kinds and "remove" in kinds
    client.cookies.clear()
    assert client.post("/api/members/packs/portfolio", json={"show": True}).status_code == 401


def test_pack_xp_is_5_a_pack_up_to_100_a_day(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _set(db)
    m = _member(db, client)
    monkeypatch.setattr(packs, "COOLDOWN", __import__("datetime").timedelta(0))
    got = [client.post("/api/members/packs/base/open").json()["xp"] for _ in range(21)]
    assert got[:20] == [5] * 20 and got[20] == 0  # 20 packs x 5 = the 100 XP daily cap; the 21st is just for fun
    s = client.get("/api/members/packs").json()
    assert s["xp_today"] == 100 and s["xp_cap"] == 100 and s["xp_packs_left"] == 0
    db.refresh(m)
    assert m.xp_total >= 100  # not stopped by the overall 60 XP/day limit on automatic XP
