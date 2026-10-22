"""The public Pokémon Market tab: visitors may read the market (cleaned of licensed / private data); everything else under
/api/pokemon stays behind the owner login."""

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard, PokemonReferencePrice
from app.pokemon import catalog, public


def test_public_paths() -> None:
    ok = ["/api/pokemon/market", "/api/pokemon/top100", "/api/pokemon/cards/1", "/api/pokemon/cards/12/chart", "/api/pokemon/years",
          "/api/pokemon/pulse", "/api/pokemon/cards", "/api/pokemon/sales/recent"]
    private = ["/api/pokemon/sources", "/api/pokemon/review", "/api/pokemon/watchlist", "/api/pokemon/cards/1/ebay", "/api/pokemon/cards/1/provenance",
               "/api/pokemon/quality", "/api/pokemon/top100/quality", "/api/pokemon/sync-logs", "/api/pokemon/tasks/x", "/api/settings/api-keys"]
    assert all(public.is_public_get("GET", p) for p in ok)
    assert not any(public.is_public_get("GET", p) for p in private)
    assert not public.is_public_get("POST", "/api/pokemon/market")


def test_sanitize_removes_pricecharting_and_private_fields() -> None:
    row = {"card": {"id": 1, "psa_certs": ["123"]}, "price_source": "pricecharting", "market_price": 5_000_00, "market_cap": 9,
           "reference": {"price": 1}, "sparkline": {"points": []}}
    out = public.sanitize({"rows": [row, {"price_source": "scrydex", "market_price": 7}], "reference_prices": [1]})
    assert out["rows"][0]["market_price"] is None and out["rows"][0]["market_cap"] is None and out["rows"][0]["price_source"] is None
    assert "reference" not in out["rows"][0] and "psa_certs" not in out["rows"][0]["card"] and "reference_prices" not in out
    assert out["rows"][1]["market_price"] == 7
    pulse = public.sanitize({"status": {"enabled": True, "budget": {"month_usd": 3}, "last_refresh_at": "x"}, "posts": []}, "/api/pokemon/pulse")
    assert pulse["status"] == {"enabled": True, "last_refresh_at": "x"}


def test_visitors_read_the_market_but_nothing_admin(client: TestClient, db: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    from app.security.admin_auth import hash_password

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(hash_password("pw")))
    catalog.ensure_catalog(db)
    card = db.scalar(select(PokemonCard).where(PokemonCard.name == "Charizard", PokemonCard.edition == "1st Edition"))
    db.add(PokemonReferencePrice(provider="pricecharting", card_id=card.id, provider_grade="PSA 10", grading_company="PSA", grade="10",
                                 price_cents=300_000_00, currency="USD", as_of=date.today()))  # TEST FIXTURE
    db.commit()

    m = client.get("/api/pokemon/market?era=all&limit=250")
    assert m.status_code == 200 and m.headers["cache-control"] == "public, max-age=60"
    row = next(r for r in m.json()["rows"] if r["card"]["id"] == card.id)
    assert row["market_price"] is None and "reference" not in row  # PriceCharting-only price: not shown publicly
    detail = client.get(f"/api/pokemon/cards/{card.id}").json()
    assert "reference_prices" not in detail and "active_listings" not in detail
    for path in ("/api/pokemon/sources", "/api/pokemon/review", f"/api/pokemon/cards/{card.id}/ebay", "/api/settings/api-keys"):
        assert client.get(path).status_code == 401
    assert client.post("/api/pokemon/sources/scrydex/sync", json={}).status_code == 401

    client.post("/api/admin/login", json={"username": "admin", "password": "pw"})
    owner = next(r for r in client.get("/api/pokemon/market?era=all&limit=250").json()["rows"] if r["card"]["id"] == card.id)
    assert owner["market_price"] == 300_000_00 and owner["reference"]  # the owner still sees everything


def test_database_backup_is_owner_only(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    from app.security.admin_auth import hash_password

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(hash_password("pw")))
    assert client.get("/api/system/db-backup").status_code == 401  # visitors: never
    client.post("/api/admin/login", json={"username": "admin", "password": "pw"})
    r = client.get("/api/system/db-backup")
    assert r.status_code == 200 and r.content[:16] == b"SQLite format 3\x00"


def test_qa_member_session_is_owner_only_and_removable(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    from app.security.admin_auth import hash_password

    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.settings, "admin_username", "admin")
    monkeypatch.setattr(runtime.settings, "admin_password_hash", SecretStr(hash_password("pw")))
    assert client.post("/api/system/qa-member-session").status_code == 401  # visitors: never
    client.post("/api/admin/login", json={"username": "admin", "password": "pw"})
    s = client.post("/api/system/qa-member-session").json()
    assert s["username"] == "qa_scanner" and len(s["token"]) > 30
    member = TestClient(client.app)
    member.cookies.set("pc_session", s["token"])
    assert member.get("/api/members/session").json()["member"]["username"] == "qa_scanner"
    assert client.delete("/api/system/qa-member").json()["deleted"] is True
    assert member.get("/api/members/session").json()["signed_in"] is False
