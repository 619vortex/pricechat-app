"""Pokémon Portfolio. TEST FIXTURES only (test database; fake Scrydex / Anthropic transports; generated images)."""

import io
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import service as members
from app.members.catalog import ensure_catalog
from app.models.members import Member, ReputationEvent
from app.models.pokemon import PokemonCard, PokemonProviderValuation
from app.models.portfolio import PokemonHolding, PokemonHoldingEvent, PokemonPortfolio, PokemonPortfolioSnapshot
from app.pokemon import catalog
from app.portfolio import service as portfolio


def _card(db: Session, name: str = "Charizard", edition: str = "1st Edition", set_name: str = "Base Set") -> PokemonCard:
    catalog.ensure_catalog(db)
    c = db.scalar(select(PokemonCard).where(PokemonCard.name == name, PokemonCard.edition == edition, PokemonCard.set_name == set_name))
    assert c is not None
    return c


def _price(db: Session, c: PokemonCard, company: str, grade: str, dollars: float) -> None:
    """TEST FIXTURE: a current Scrydex valuation."""
    db.add(PokemonProviderValuation(provider="scrydex", card_id=c.id, provider_card_id="x", provider_variant="v", grading_company=company,
                                    grade=grade, day=utcnow().date(), kind="current", market_cents=int(dollars * 100), currency="USD",
                                    fetched_at=utcnow()))
    db.commit()


def _member(db: Session, name: str, client: TestClient | None = None, age: timedelta = timedelta(days=3)) -> Member:
    ensure_catalog(db)
    m = members.create_member(db)
    m.username, m.username_key, m.username_chosen = name, name.lower(), True
    m.created_at = utcnow() - age
    db.commit()
    if client is not None:
        client.cookies.set("pc_session", members.new_session(db, m, 30))
    return m


def _jpeg(w: int = 1200, h: int = 1680, exif: bool = True) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (w, h), (200, 60, 40))
    out = io.BytesIO()
    kw = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretPhoneMaker"  # Make
        ex[0x0131] = "SecretSoftware 1.0"  # Software
        kw["exif"] = ex.tobytes()
    img.save(out, "JPEG", **kw)
    return out.getvalue()


def test_grade_exact_values_honest_raw_ranges_and_totals(client: TestClient, db: Session) -> None:
    zard, unl = _card(db), _card(db, edition="Unlimited")
    _price(db, zard, "PSA", "10", 400_000)
    _price(db, zard, "PSA", "9", 60_000)
    for cond, usd in (("NM", 9_000), ("LP", 6_000), ("MP", 4_000), ("HP", 2_500)):
        _price(db, zard, "RAW", cond, usd)
    _member(db, "ash", client)
    assert client.get("/api/members/portfolio").json()["summary"]["value"] == 0  # created on first visit, empty

    nine = client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "graded", "grading_company": "PSA", "grade": "9"}).json()
    assert nine["value"]["each"] == 60_000_00 and nine["value"]["source"] == "scrydex" and nine["label"] == "PSA 9"  # never the PSA 10 price
    raw = client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "raw", "quantity": 2}).json()
    assert raw["label"] == "Raw" and raw["grading_company"] is None and raw["grade"] is None  # never an official grade
    assert (raw["value"]["each"], raw["value"]["low"], raw["value"]["high"]) == (6_000_00, 4_000_00, 9_000_00)  # LP point, MP..NM range
    assert raw["value"]["confidence"] == "low" and "not assessed" in raw["value"]["note"] and "Not professionally graded" in raw["value"]["note"]
    assessed = client.patch(f"/api/members/portfolio/holdings/{raw['id']}", json={"condition_low": 7, "condition_high": 8}).json()
    assert (assessed["value"]["each"], assessed["value"]["low"], assessed["value"]["high"]) == (9_000_00, 9_000_00, 9_000_00) and assessed["label"] == "Raw · ~7-8 eq."
    unpriced = client.post("/api/members/portfolio/holdings", json={"card_id": unl.id, "kind": "raw"}).json()
    assert unpriced["value"]["each"] is None and unpriced["value"]["note"].startswith("Price unavailable")  # never invented

    d = client.get("/api/members/portfolio").json()
    s = d["summary"]
    assert s["value"] == 60_000_00 + 2 * 9_000_00 and (s["unique_cards"], s["copies"], s["graded"], s["raw"]) == (2, 4, 1, 3)
    assert s["unpriced_holdings"] == 1 and s["most_valuable"]["label"] == "PSA 9"
    assert {g["key"] for g in d["composition"]["grade"]} == {"PSA 9", "Raw"}
    assert s["changes"]["30d"]["value"] is None and "history" in s["changes"]["30d"]["reason"]  # no invented history

    gone = client.delete(f"/api/members/portfolio/holdings/{nine['id']}")
    assert gone.status_code == 200, gone.text
    assert client.get("/api/members/portfolio").json()["summary"]["value"] == 2 * 9_000_00
    dup = client.post(f"/api/members/portfolio/holdings/{raw['id']}/duplicate").json()
    assert dup["id"] != raw["id"] and dup["quantity"] == 1
    assert client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "graded", "grading_company": "PSA", "grade": "11"}).status_code == 400


def test_history_separates_market_moves_from_cards_added(client: TestClient, db: Session) -> None:
    zard = _card(db)
    _price(db, zard, "PSA", "9", 1_000)
    m = _member(db, "misty", client)
    client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "graded", "grading_company": "PSA", "grade": "9"})
    p = db.scalar(select(PokemonPortfolio).where(PokemonPortfolio.member_id == m.id))
    then = utcnow() - timedelta(days=31)
    from app.models.portfolio import PokemonHoldingEvent

    for e in db.scalars(select(PokemonHoldingEvent).where(PokemonHoldingEvent.portfolio_id == p.id)):
        e.at = then - timedelta(days=1)  # TEST FIXTURE: the card was added 32 days ago
    db.add(PokemonPortfolioSnapshot(portfolio_id=p.id, day=then.date(), taken_at=then, value_cents=1_000_00, flows_cents=1_000_00, copies=1, priced_copies=1))
    db.commit()  # TEST FIXTURE: a snapshot recorded 31 days ago
    # since then: the market moved +20% and the member added a second copy
    v = db.scalar(select(PokemonProviderValuation).where(PokemonProviderValuation.card_id == zard.id))
    v.market_cents = 1_200_00
    db.commit()
    h = db.scalar(select(PokemonHolding).where(PokemonHolding.portfolio_id == p.id))
    portfolio.revalue(db, p)
    db.commit()
    client.patch(f"/api/members/portfolio/holdings/{h.id}", json={"quantity": 2})
    ch = client.get("/api/members/portfolio").json()["summary"]["changes"]["30d"]
    assert ch["value"] == 2_400_00 - 1_000_00 and ch["flows"] == 1_200_00 and ch["market"] == 200_00 and ch["pct_market"] == 20.0


def test_private_portfolios_are_invisible_and_public_ones_respect_choices(client: TestClient, db: Session) -> None:
    zard = _card(db)
    _price(db, zard, "PSA", "10", 400_000)
    _member(db, "brock", client)
    h = client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "graded", "grading_company": "PSA", "grade": "10",
                                                              "purchase_price": 300_000_00, "cert_number": "12345678"}).json()
    ph = client.post(f"/api/members/portfolio/holdings/{h['id']}/photos", files={"file": ("c.jpg", _jpeg(), "image/jpeg")}, data={"side": "front"}).json()
    other = TestClient(client.app)
    _member(db, "gary", other)
    for path in ("/api/members/portfolios/u/brock", f"/api/members/portfolio/holdings/{h['id']}", f"/api/members/portfolio/photos/{ph['id']}"):
        assert other.get(path).status_code == 404  # private: indistinguishable from missing
    assert other.patch(f"/api/members/portfolio/holdings/{h['id']}", json={"quantity": 5}).status_code == 404  # never someone else's

    client.patch("/api/members/portfolio", json={"visibility": "public", "show_value": False})
    pub = other.get("/api/members/portfolios/u/brock").json()
    assert pub["value"] is None and "value" not in pub["holdings"][0] and "purchase_price" not in pub["holdings"][0]
    assert "cert_number" not in pub["holdings"][0] and pub["holdings"][0]["verification"] == "self_reported"
    assert other.get(f"/api/members/portfolio/photos/{ph['id']}").status_code == 200  # public card: its photo too
    client.patch("/api/members/portfolio", json={"show_value": True})
    pub = other.get("/api/members/portfolios/u/brock").json()
    assert pub["value"] == 400_000_00 and "purchase_price" not in pub["holdings"][0]  # purchase stays private unless opted in
    assert other.get("/api/members/portfolios/explore?tab=top").json()["rows"][0]["username"] == "brock"
    client.patch(f"/api/members/portfolio/holdings/{h['id']}", json={"is_public": False})
    assert other.get(f"/api/members/portfolio/photos/{ph['id']}").status_code == 404


def test_photos_are_reencoded_without_metadata_and_validated(client: TestClient, db: Session) -> None:
    from PIL import Image

    from app.config.settings import get_settings
    from app.portfolio.photos import Storage

    zard = _card(db)
    _member(db, "dawn", client)
    h = client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "raw"}).json()
    ph = client.post(f"/api/members/portfolio/holdings/{h['id']}/photos", files={"file": ("c.jpg", _jpeg(3000, 4200), "image/jpeg")},
                     data={"side": "front"}).json()
    assert max(ph["width"], ph["height"]) == 1600
    full = client.get(f"/api/members/portfolio/photos/{ph['id']}?size=full").content
    img = Image.open(io.BytesIO(full))
    assert not img.getexif() and b"SecretPhoneMaker" not in full  # metadata (incl. GPS) stripped
    assert client.post(f"/api/members/portfolio/holdings/{h['id']}/photos", files={"file": ("x.jpg", b"<script>alert(1)</script>", "image/jpeg")},
                       data={"side": "back"}).status_code == 400
    assert Storage(get_settings()).available


def _vision(card_id: str, variant: str | None = None, score: float = 1.2, graded: dict | None = None) -> httpx.MockTransport:  # type: ignore[type-arg]
    def handle(r: httpx.Request) -> httpx.Response:
        assert r.url.path == "/vision/v1/cards/identify" and r.headers["x-api-key"] == "sx" and b'name="games"' in r.content
        match = {"score": score, "card": {"id": card_id, "name": "Charizard"}}
        if variant:
            match["variant"] = variant
        analysis = {"type": "graded" if graded else "raw", "game": "pokemon", "language_code": "EN", **({"graded_details": graded} if graded else {})}
        return httpx.Response(200, json={"data": {"analysis": analysis, "matches": [match, {"score": 0.8, "card": {"id": "base4-4", "name": "Charizard"}}]}})
    return httpx.MockTransport(handle)


def test_scanner_distinguishes_printings_and_never_guesses_the_expensive_one(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "scrydex_api_key", SecretStr("sx"))
    monkeypatch.setattr(runtime.settings, "scrydex_team_id", SecretStr("team"))
    first, shadowless, unl = _card(db), _card(db, edition="Shadowless"), _card(db, edition="Unlimited")
    for c, v in ((first, "firstEditionShadowlessHolofoil"), (shadowless, "shadowlessHolofoil"), (unl, "unlimitedHolofoil")):
        c.external_ids = {**(c.external_ids or {}), "scrydex": "base1-4", "scrydex_variant": v}  # TEST FIXTURE mapping
    db.commit()
    _member(db, "red", client)

    monkeypatch.setattr(runtime, "pokemon_transport", _vision("base1-4"))  # Scrydex not sure of the printing
    out = client.post("/api/members/portfolio/scan", files={"file": ("s.jpg", _jpeg(), "image/jpeg")}).json()
    assert out["needs_choice"] and not any(c["suggested"] for c in out["candidates"])  # none preselected
    assert {c["card"]["edition"] for c in out["candidates"]} == {"1st Edition", "Shadowless", "Unlimited"}

    monkeypatch.setattr(runtime, "pokemon_transport", _vision("base1-4", "unlimitedHolofoil",
                                                              graded={"company": "PSA", "grade_number": 8, "cert": "55555555"}))
    out = client.post("/api/members/portfolio/scan", files={"file": ("s2.jpg", _jpeg(1100, 1540), "image/jpeg")}).json()
    sug = [c for c in out["candidates"] if c["suggested"]]
    assert len(sug) == 1 and sug[0]["card"]["edition"] == "Unlimited" and not out["needs_choice"]  # exact printing named by Scrydex
    assert out["detected"]["graded"] == {"company": "PSA", "grade": "8", "cert": "55555555"}  # proposed, not saved
    again = client.post("/api/members/portfolio/scan", files={"file": ("s2.jpg", _jpeg(1100, 1540), "image/jpeg")}).json()
    assert again["cached"]  # the same photo: no second paid request

    # the slab LABEL names the printing (Scrydex didn't): it's preselected and listed first; a label year far from the card's is flagged
    monkeypatch.setattr(runtime, "pokemon_transport", _vision("base1-4", graded={"company": "PSA", "grade_number": 9, "cert": "1",
                                                                                "title": "1999 POKEMON GAME 1ST EDITION HOLO CHARIZARD", "year": "1999"}))
    out = client.post("/api/members/portfolio/scan", files={"file": ("s4.jpg", _jpeg(1200, 1600), "image/jpeg")}).json()
    assert out["candidates"][0]["card"]["edition"] == "1st Edition" and out["candidates"][0]["suggested"] and out["detected"]["label"]["first"]
    monkeypatch.setattr(runtime, "pokemon_transport", _vision("base1-4", graded={"company": "PSA", "grade_number": 9, "title": "2016 CHARIZARD EVOLUTIONS", "year": "2016"}))
    out = client.post("/api/members/portfolio/scan", files={"file": ("s5.jpg", _jpeg(1300, 1700), "image/jpeg")}).json()
    assert all("2016" in c["warning"] for c in out["candidates"])

    monkeypatch.setattr(runtime.settings, "portfolio_scans_per_day", 4)
    assert client.post("/api/members/portfolio/scan", files={"file": ("s3.jpg", _jpeg(1000, 1400), "image/jpeg")}).status_code == 400  # daily cap
    # confirmed by the member: saved as a scanned card (first-scan XP once)
    client.post("/api/members/portfolio/holdings", json={"card_id": unl.id, "kind": "graded", "grading_company": "PSA", "grade": "8", "source": "scan"})
    client.post("/api/members/portfolio/holdings", json={"card_id": unl.id, "kind": "raw", "source": "scan"})
    me = db.scalar(select(Member).where(Member.username_key == "red"))
    types = [e.event_type for e in db.scalars(select(ReputationEvent).where(ReputationEvent.member_id == me.id))]
    assert types.count("portfolio.first_scan") == 1 and types.count("portfolio.first_card") == 1


def test_ai_condition_needs_both_sides_and_returns_a_capped_range(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "anthropic_api_key", SecretStr("ak"))
    zard = _card(db)
    for cond, usd in (("NM", 9_000), ("LP", 6_000), ("MP", 4_000)):
        _price(db, zard, "RAW", cond, usd)
    _member(db, "iris", client)
    h = client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "raw"}).json()
    client.post(f"/api/members/portfolio/holdings/{h['id']}/photos", files={"file": ("f.jpg", _jpeg(), "image/jpeg")}, data={"side": "front"})
    need = client.post(f"/api/members/portfolio/holdings/{h['id']}/condition").json()
    assert need["status"] == "need_photos" and need["missing"] == ["back"]  # no call, no cost

    def handle(r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        assert r.headers["x-api-key"] == "ak" and body["model"] == "claude-sonnet-5-5" and sum(1 for b in body["messages"][0]["content"] if b["type"] == "image") == 2
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({
            "enough_photos": True, "condition_low": 5, "condition_high": 10, "confidence": "medium", "centering": "60/40 left-right",
            "corners": "light whitening on two corners", "edges": "minor wear", "surface": "a faint scratch", "issues": ["whitening"],
            "more_photos": ["close-up of the top left corner"]})}], "usage": {"input_tokens": 3000, "output_tokens": 200}})

    monkeypatch.setattr(runtime, "members_transport", httpx.MockTransport(handle))
    client.post(f"/api/members/portfolio/holdings/{h['id']}/photos", files={"file": ("b.jpg", _jpeg(), "image/jpeg")}, data={"side": "back"})
    out = client.post(f"/api/members/portfolio/holdings/{h['id']}/condition").json()
    rep = out["report"]
    assert (rep["low"], rep["high"]) == (5.0, 9.0) and "not a professional grade" in rep["label"]  # 10 is never claimed from photos
    assert out["holding"]["label"] == "Raw · ~5-9 eq." and out["holding"]["condition"]["source"] == "ai"
    assert out["holding"]["value"]["low"] == 6_000_00 and out["holding"]["value"]["high"] == 9_000_00
    again = client.post(f"/api/members/portfolio/holdings/{h['id']}/condition").json()
    assert again["cached"]  # same photos: no second paid call


def test_comments_votes_and_moderation(client: TestClient, db: Session) -> None:
    zard = _card(db)
    owner = _member(db, "cynthia", client)
    h = client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "raw"}).json()
    client.patch(f"/api/members/portfolio/holdings/{h['id']}", json={"community_review": True})
    client.patch("/api/members/portfolio", json={"visibility": "public"})
    fan = TestClient(client.app)
    _member(db, "lucas", fan)
    c = fan.post("/api/members/portfolios/u/cynthia/comments", json={"body": "Beautiful Zard!"}).json()
    reply = client.post("/api/members/portfolios/u/cynthia/comments", json={"body": "Thanks!", "parent_id": c["id"]}).json()
    deeper = fan.post("/api/members/portfolios/u/cynthia/comments", json={"body": "np", "parent_id": reply["id"]}).json()
    assert deeper["parent_id"] == c["id"]  # one level deep
    assert fan.post("/api/members/portfolios/u/cynthia/comments", json={"body": "buy at scam.xyz"}).status_code == 400  # no links
    assert fan.post("/api/members/portfolios/u/cynthia/comments", json={"body": "more"}).status_code == 201
    assert fan.post("/api/members/portfolios/u/cynthia/comments", json={"body": "even more"}).status_code == 400  # 3 a minute
    newbie = TestClient(client.app)
    _member(db, "fresh", newbie, age=timedelta(minutes=5))
    assert newbie.post("/api/members/portfolios/u/cynthia/comments", json={"body": "hi"}).status_code == 400
    assert client.delete(f"/api/members/portfolio-comments/{c['id']}").status_code == 200  # the owner moderates their portfolio
    thread = next(t for t in fan.get("/api/members/portfolios/u/cynthia/comments").json()["comments"] if t["id"] == c["id"])
    assert thread["deleted"] and thread["body"] is None and len(thread["replies"]) == 2
    assert fan.post("/api/members/portfolio-reports", json={"target": "comment", "target_id": reply["id"], "reason": "spam"}).status_code == 201

    assert client.post(f"/api/members/portfolio/holdings/{h['id']}/condition-vote", json={"estimate": 9}).status_code == 400  # not the owner
    first = fan.post(f"/api/members/portfolio/holdings/{h['id']}/condition-vote", json={"estimate": 6}).json()
    assert first["responses"] == 1 and "distribution" not in first  # too few to show
    for i, est in enumerate((5, 6, 7, 6)):
        v = TestClient(client.app)
        _member(db, f"voter{i}", v)
        last = v.post(f"/api/members/portfolio/holdings/{h['id']}/condition-vote", json={"estimate": est}).json()
    fan.post(f"/api/members/portfolio/holdings/{h['id']}/condition-vote", json={"estimate": 7})  # changes, never a second vote
    assert last["responses"] == 5
    detail = fan.get(f"/api/members/portfolio/holdings/{h['id']}").json()
    dist = {d["estimate"]: d["share"] for d in detail["community"]["distribution"]}
    assert dist == {5: 20, 6: 40, 7: 40} and "not an official grade" in detail["community"]["label"]
    assert owner.id


def test_unusually_valuable_public_collections_wait_for_review(client: TestClient, db: Session) -> None:
    zard = _card(db)
    _price(db, zard, "PSA", "10", 400_000)
    _member(db, "whale", client)
    client.patch("/api/members/portfolio", json={"visibility": "public"})
    client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "graded", "grading_company": "PSA", "grade": "10", "quantity": 5})
    assert client.get("/api/members/portfolio").json()["portfolio"]["review_status"] == "review"
    assert client.get("/api/members/portfolios/explore?tab=top").json()["rows"] == []  # not ranked until a person looked
    assert client.get("/api/members/portfolios/u/whale").status_code == 200  # still visible on its own page


def test_refresh_job_is_bounded_and_snapshots_once_a_day(client: TestClient, db: Session) -> None:
    zard = _card(db)
    _price(db, zard, "PSA", "9", 1_000)
    _member(db, "oak", client)
    client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "graded", "grading_company": "PSA", "grade": "9"})
    a = portfolio.refresh_all(db)
    b = portfolio.refresh_all(db)
    assert a["snapshots"] == b["snapshots"] == 1 and db.query(PokemonPortfolioSnapshot).count() == 1


@pytest.mark.parametrize("path", ["/api/members/portfolio", "/api/members/portfolio/search?q=charizard"])
def test_portfolio_needs_a_signed_in_member(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_quote_shows_the_exact_estimate_before_adding(client: TestClient, db: Session) -> None:
    zard = _card(db)
    _price(db, zard, "PSA", "9", 60_000)
    _price(db, zard, "BGS", "9.5", 90_000)
    for cond, usd in (("NM", 9_000), ("LP", 6_000), ("MP", 4_000)):
        _price(db, zard, "RAW", cond, usd)
    _member(db, "quoter", client)
    q = lambda **p: client.get("/api/members/portfolio/quote", params={"card_id": zard.id, **p}).json()  # noqa: E731
    assert q(kind="graded", company="PSA", grade="9")["value"] == 60_000_00
    assert q(kind="graded", company="BGS", grade="9.5")["value"] == 90_000_00
    cgc = q(kind="graded", company="CGC", grade="10")  # no CGC 10 price: a labelled floor from the nearest LOWER grade, never a higher one
    assert (cgc["value"], cgc["source"], cgc["reason"], cgc["confidence"]) == (60_000_00, "comparable", "comparable", "low") and "PSA 9" in cgc["note"]
    raw = q(kind="raw")
    assert (raw["value"], raw["low"], raw["high"]) == (6_000_00, 4_000_00, 9_000_00) and raw["source"] == "scrydex_raw"
    assert db.query(PokemonHolding).count() == 0  # a quote saves nothing
    assert client.get("/api/members/portfolio/quote", params={"card_id": zard.id, "kind": "graded", "company": "PSA", "grade": "11"}).status_code == 400


def test_big_phone_photos_are_decoded_small() -> None:
    from app.portfolio import photos

    full, thumb, w, h, _ = photos.process(_jpeg(4032, 3024, exif=False))  # a 12 MP phone photo
    assert max(w, h) == 1600 and len(full) < 600_000 and len(thumb) < 120_000


def test_qr_handoff_signs_the_phone_in_once(client: TestClient, db: Session) -> None:
    from app.models.members import MemberAuthRequest

    me = _member(db, "desk_user", client)
    h = client.post("/api/members/portfolio/handoff").json()
    assert h["url"].startswith("http") and "/pokemon/portfolio/phone/" in h["url"] and h["minutes"] == 10
    token = h["url"].rsplit("/", 1)[1]
    assert client.get(f"/api/members/portfolio/handoff/{h['id']}").json()["status"] == "pending"
    assert token not in str([r.state_hash for r in db.scalars(select(MemberAuthRequest))])  # only a hash is stored

    phone = TestClient(client.app, headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)"})
    assert phone.get("/api/members/session").json()["signed_in"] is False
    r = phone.post("/api/members/portfolio/handoff/claim", json={"token": token})
    assert r.status_code == 200 and r.json()["username"] == "desk_user"
    assert phone.get("/api/members/session").json()["member"]["username"] == "desk_user"  # the phone is signed in as them
    st = client.get(f"/api/members/portfolio/handoff/{h['id']}").json()
    assert st["status"] == "connected" and st["device"] == "iPhone"
    assert TestClient(client.app).post("/api/members/portfolio/handoff/claim", json={"token": token}).status_code == 400  # single use

    old = client.post("/api/members/portfolio/handoff").json()
    new = client.post("/api/members/portfolio/handoff").json()
    assert TestClient(client.app).post("/api/members/portfolio/handoff/claim", json={"token": old["url"].rsplit("/", 1)[1]}).status_code == 400  # replaced
    req = db.get(MemberAuthRequest, new["id"])
    req.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert TestClient(client.app).post("/api/members/portfolio/handoff/claim", json={"token": new["url"].rsplit("/", 1)[1]}).status_code == 400  # expired
    other = TestClient(client.app)
    _member(db, "someone_else", other)
    assert other.get(f"/api/members/portfolio/handoff/{h['id']}").status_code == 404  # not theirs
    assert me.id


def test_the_two_real_holdings_bgs_7_5_and_raw_get_defensible_values_or_honest_reasons(client: TestClient, db: Session) -> None:
    """Mirrors production: a BGS 7.5 Legendary Collection Eevee (only PSA 7/8/9/10 + CGC 10 priced) and a raw Challenge! with no
    ungraded prices and cheap graded prices."""
    eevee, challenge = _card(db, "Charizard"), _card(db, "Blastoise")  # TEST FIXTURES standing in for the two printings
    for g, usd in (("7", 10.00), ("8", 20.81), ("9", 42.93), ("10", 182.31)):
        _price(db, eevee, "PSA", g, usd)
    for g, usd in (("7", 8.00), ("8", 22.45), ("9", 44.19)):
        _price(db, challenge, "PSA", g, usd)
    _member(db, "collector", client)
    bgs = client.post("/api/members/portfolio/holdings", json={"card_id": eevee.id, "kind": "graded", "grading_company": "BGS", "grade": "7.5"}).json()
    v = bgs["value"]
    assert (v["each"], v["low"], v["high"], v["source"], v["confidence"]) == (1_000, 1_000, 2_081, "comparable", "low")  # PSA 7..8, never PSA 10
    assert "PSA 7 and PSA 8" in v["note"]
    raw = client.post("/api/members/portfolio/holdings", json={"card_id": challenge.id, "kind": "raw"}).json()
    assert raw["value"]["each"] is None and "cost of grading" in raw["value"]["note"]  # PSA prices barely above grading cost: honest, unpriced
    d = client.get("/api/members/portfolio").json()
    assert d["summary"]["value"] == 1_000 and d["summary"]["unpriced_holdings"] == 1


def test_edit_identification_keeps_photos_and_history_and_diagnostics_explain_unpriced(client: TestClient, db: Session) -> None:
    normal, reverse = _card(db, "Charizard"), _card(db, "Blastoise")  # TEST FIXTURES: a wrongly chosen printing and the right one
    _price(db, reverse, "BGS", "7.5", 30.00)
    _member(db, "fixer", client)
    h = client.post("/api/members/portfolio/holdings", json={"card_id": normal.id, "kind": "graded", "grading_company": "BGS", "grade": "7.5"}).json()
    assert h["value"]["each"] is None and h["value"]["reason"] == "grade_price_unavailable" and h["value"]["reason_label"]
    assert client.post(f"/api/members/portfolio/holdings/{h['id']}/photos", files={"file": ("f.jpg", _jpeg(), "image/jpeg")}, data={"side": "front"}).status_code in (200, 201)
    d = client.get("/api/pokemon/portfolios/diagnostics").json()
    assert d["holdings"] >= 1 and d["unpriced"] >= 1 and any(r["reason"] == "grade_price_unavailable" for r in d["by_reason"])

    fixed = client.patch(f"/api/members/portfolio/holdings/{h['id']}", json={"card_id": reverse.id}).json()
    assert fixed["id"] == h["id"] and fixed["card"]["id"] == reverse.id and len(fixed["photos"]) == 1  # same holding, photo kept
    assert fixed["value"]["each"] == 3_000 and fixed["value"]["reason"] == "priced"
    ev = db.scalars(select(PokemonHoldingEvent).where(PokemonHoldingEvent.holding_id == h["id"]).order_by(PokemonHoldingEvent.id)).all()
    assert [e.kind for e in ev][-1] == "identity" and ev[-1].value_delta_cents == 3_000  # a flow, not a market move
    dash = client.get("/api/members/portfolio").json()
    assert dash["summary"]["value"] == 3_000 and dash["top"][0]["share"] == 100.0 and dash["composition"]["kind"][0]["key"] == "Graded"
    assert client.patch(f"/api/members/portfolio/holdings/{h['id']}", json={"card_id": 99_999_999}).status_code == 400


def test_regrade_and_quantity_in_one_edit_records_the_flow_once(client: TestClient, db: Session) -> None:
    """A PATCH that changes the quantity AND the grade: every recorded flow adds up to exactly what is now held (the
    quantity's part was counted twice before, so the history showed a market loss that never happened)."""
    zard = _card(db)
    _price(db, zard, "PSA", "9", 1_000)
    _price(db, zard, "PSA", "10", 3_000)
    m = _member(db, "brock", client)
    h = client.post("/api/members/portfolio/holdings", json={"card_id": zard.id, "kind": "graded", "grading_company": "PSA", "grade": "9"}).json()
    r = client.patch(f"/api/members/portfolio/holdings/{h['id']}", json={"quantity": 2, "grade": "10"})
    assert r.status_code == 200, r.text
    p = db.scalar(select(PokemonPortfolio).where(PokemonPortfolio.member_id == m.id))
    db.refresh(p)
    flows = sum(e.value_delta_cents or 0 for e in db.scalars(select(PokemonHoldingEvent).where(PokemonHoldingEvent.portfolio_id == p.id)))
    assert p.value_cents == 6_000_00 and flows == 6_000_00


def test_raw_condition_without_prices_says_why_it_is_unpriced(db: Session) -> None:
    from app.portfolio import valuation

    zard = _card(db)
    _price(db, zard, "RAW", "NM", 500)  # raw prices only for Near Mint
    h = PokemonHolding(card_id=zard.id, kind="raw", quantity=1, condition_low=2.0, condition_high=3.0)
    v = valuation.Pricer(db, [h]).value(h)
    assert v.cents is None and v.reason == "raw_prices_unavailable"
