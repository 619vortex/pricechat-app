"""Search heads, link previews and sitemaps (app.seo). TEST FIXTURES only: catalogue cards, made-up members, Vault listings."""

import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import service as members
from app.models.pokemon import PokemonCard
from app.models.portfolio import PokemonHolding
from app.models.vault import VaultListing, VaultSource
from app.pokemon import catalog
from app.portfolio import service as portfolio
from app.seo import heads, sitemaps

GOLDEN = Path(__file__).resolve().parents[2] / "frontend" / "src" / "website" / "seoHeads.golden.json"


# --- the same strings as the website (frontend/src/website/seo.ts checks the same file) -----------------------------------


@pytest.fixture(scope="module")
def golden() -> dict:
    if not GOLDEN.exists():
        pytest.skip("the frontend folder isn't next to the backend")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_heads_match_the_website(golden: dict) -> None:
    base = golden["base"]
    assert {p: {"title": t, "description": d} for p, (t, d) in heads.PAGES.items()} == golden["pages"]
    assert {p: heads.is_noindex(p) for p in golden["noindex"]} == golden["noindex"]
    for c in golden["cards"]:
        assert heads.card_head(c["detail"], c["grade"]) == c["head"]
        assert heads.absolutize(heads.card_json_ld(c["detail"], c["grade"]), base) == c["jsonLd"]
    ls = golden["landing"]
    for c in ls["cases"]:
        assert heads.landing_head(parse_qsl(c["search"], keep_blank_values=True), ls["sets"], ls["years"]) == c["head"], c["search"]
    for c in golden["vault"]:
        assert heads.vault_head(c["card"]) == c["head"]
        assert heads.absolutize(heads.vault_json_ld(c["card"]), base) == c["jsonLd"]
    for c in golden["portfolios"]:
        assert heads.portfolio_head(c["portfolio"]) == c["head"]
        assert heads.absolutize(heads.portfolio_json_ld(c["portfolio"]["username"]), base) == c["jsonLd"]
    for c in golden["profiles"]:
        assert heads.profile_head(c["profile"]) == c["head"]


def test_browser_number_formats() -> None:
    assert heads.usd(1250) == "$12.50" and heads.usd(99_999) == "$999.99" and heads.usd(100_050) == "$1,001" and heads.usd(42_000_000) == "$420,000"
    assert heads.money(1_234_500, "JPY") == "¥12,345" and heads.money(123_456, "EUR") == "€1,234.56" and heads.money(500, "CHF") == "CHF 5.00"
    assert heads.num(1234567) == "1,234,567" and heads.encode_component("Team Rocket (Rocket Gang) & Co/2") == "Team%20Rocket%20(Rocket%20Gang)%20%26%20Co%2F2"


# --- fixtures ------------------------------------------------------------------------------------------------------------


def _charizard(db: Session) -> PokemonCard:
    catalog.ensure_catalog(db)
    c = db.scalar(select(PokemonCard).where(PokemonCard.name == "Charizard", PokemonCard.edition == "1st Edition", PokemonCard.set_name == "Base Set"))
    assert c is not None
    return c


def _member(db: Session, name: str, public_profile: bool = False):  # type: ignore[no-untyped-def]
    m = members.create_member(db)
    m.username, m.username_key, m.public_profile = name, name.lower(), public_profile
    db.commit()
    return m


def _collector(db: Session, name: str, card: PokemonCard, *, visibility: str, review: str = "ok", public_cards: bool = True) -> None:
    m = _member(db, name)
    p = portfolio.get_portfolio(db, m, create=True)
    p.visibility, p.review_status, p.bio, p.last_activity_at = visibility, review, f"{name} collects", utcnow()
    db.add(PokemonHolding(portfolio_id=p.id, card_id=card.id, kind="raw", quantity=2, is_public=public_cards))
    db.commit()


def _listing(db: Session, i: int, *, review: str = "approved", eligible: bool = True, availability: str = "available") -> VaultListing:
    src = db.scalar(select(VaultSource).where(VaultSource.key == "test_seller"))
    if src is None:
        src = VaultSource(kind="seller_catalog", key="test_seller", name="Test seller", enabled=True)
        db.add(src)
        db.flush()
    r = VaultListing(source_id=src.id, external_id=f"x{i}", title=f"[NM] Lugia Pokemon Card Japanese Neo Genesis Holo #{i}", price_cents=45_000 + i,
                     currency="USD", images=[f"https://img.test/{i}.jpg"], availability=availability, first_seen_at=utcnow(), checked_at=utcnow(),
                     eligible=eligible, review=review, pokemon="Lugia", set_name="Neo Genesis", year=2000, language="Japanese", condition="Ungraded")
    db.add(r)
    db.commit()
    return r


def _og(client: TestClient, path: str):  # type: ignore[no-untyped-def]
    r = client.get(f"/api/website/seo/og/{path}")
    assert r.status_code == 200
    return r


def _title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html)
    assert m
    return unescape(m.group(1))


# --- link previews -------------------------------------------------------------------------------------------------------


def test_card_preview_is_the_card_pages_head(client: TestClient, db: Session) -> None:
    card = _charizard(db)
    r = _og(client, f"pokemon/cards/{card.id}")
    assert _title(r.text) == f"Charizard 1st Edition · 1999 Base Set {card.card_number} · PSA 10 Price · Price Chat"
    assert "x-robots-tag" not in r.headers and "noindex" not in r.text
    ld = [json.loads(x) for x in re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text)]
    assert [b["@type"] for b in ld] == ["Product", "BreadcrumbList"] and "offers" not in ld[0] and "aggregateRating" not in ld[0]
    assert ld[0]["url"].endswith(f"/pokemon/cards/{card.id}") and ld[1]["itemListElement"][1]["item"].endswith("/pokemon?set=Base%20Set")
    assert "PSA 9 Price" in _title(_og(client, f"pokemon/cards/{card.id}?grade=PSA%209").text)
    junk = _og(client, f"pokemon/cards/{card.id}?grade=%3Cb%3Ehi").text  # a made-up grade is never echoed
    assert "PSA 10 Price" in _title(junk) and "hi" not in _title(junk)
    canonical = re.search(r'rel="canonical" href="([^"]+)"', _og(client, f"pokemon/cards/{card.id}?grade=PSA%209&tab=sales").text)
    assert canonical and canonical.group(1).endswith(f"/pokemon/cards/{card.id}")  # one canonical address per card


def test_set_and_year_landing_previews(client: TestClient, db: Session) -> None:
    catalog.ensure_catalog(db)
    r = _og(client, "pokemon?set=Base%20Set")
    assert _title(r.text) == "Base Set Pokémon Card Prices · Price Chat" and 'href="http://127.0.0.1:5173/pokemon?set=Base%20Set"' in r.text
    assert _title(_og(client, "pokemon?year=1999").text) == "1999 Pokémon Card Prices · Price Chat"
    assert _title(_og(client, "pokemon?set=Not%20a%20set").text) == "Pokémon Market · Price Chat"
    assert _title(_og(client, "pokemon?set=Base%20Set&grade=PSA%209").text) == "Pokémon Market · Price Chat"


def test_vault_preview_only_for_public_listings(client: TestClient, db: Session) -> None:
    shown, pending = _listing(db, 1), _listing(db, 2, review="pending")
    r = _og(client, f"pokemon/vault/{shown.id}")
    assert _title(r.text) == "Lugia · Neo Genesis · $450.01 · Price Chat Vault" and '"@type": "Offer"' in r.text and "schema.org/InStock" in r.text
    hidden = _og(client, f"pokemon/vault/{pending.id}")
    assert "Lugia" not in hidden.text and hidden.headers["x-robots-tag"] == "noindex"


def test_private_portfolios_and_owner_pages_are_never_previewed_or_indexed(client: TestClient, db: Session) -> None:
    card = _charizard(db)
    _collector(db, "open_ash", card, visibility="public")
    _collector(db, "secret_misty", card, visibility="private")
    r = _og(client, "pokemon/portfolio/u/open_ash")
    assert _title(r.text) == "open_ash's Pokémon Card Collection · Price Chat" and "2 cards, 1 unique, 0 graded. open_ash collects" in r.text
    assert "x-robots-tag" not in r.headers and "BreadcrumbList" in r.text
    p = _og(client, "pokemon/portfolio/u/secret_misty")
    assert "secret_misty" not in p.text and p.headers["x-robots-tag"] == "noindex" and '<meta name="robots" content="noindex">' in p.text
    for path in ("passport", "pokemon/portfolio", "pokemon/portfolio/add", "pokemon/wallet", "pokemon/vault/requests", "u/nobody_here", "nowhere/at/all"):
        assert _og(client, path).headers.get("x-robots-tag") == "noindex", path
    assert "x-robots-tag" not in _og(client, "vpn").headers and _title(_og(client, "vpn").text) == "Kiwi VPN · Price Chat"


# --- sitemaps ------------------------------------------------------------------------------------------------------------


def _locs(client: TestClient, name: str) -> list[str]:
    r = client.get(f"/api/website/seo/sitemaps/{name}.xml")
    assert r.status_code == 200, name
    return re.findall(r"<loc>(.*?)</loc>", r.text)


def test_sitemaps_cover_every_public_page_and_nothing_private(client: TestClient, db: Session) -> None:
    card = _charizard(db)
    _collector(db, "open_ash", card, visibility="public")
    _collector(db, "secret_misty", card, visibility="private")
    _collector(db, "hidden_brock", card, visibility="public", review="hidden")
    _collector(db, "waiting_gary", card, visibility="public", review="review")
    _collector(db, "empty_oak", card, visibility="public", public_cards=False)
    _member(db, "kiwi_fan", public_profile=True)
    _member(db, "quiet_one", public_profile=False)
    shown = [_listing(db, 1), _listing(db, 2)]
    hidden = [_listing(db, 3, review="pending"), _listing(db, 4, review="hidden"), _listing(db, 5, eligible=False), _listing(db, 6, availability="sold_out")]

    index = client.get("/api/website/seo/sitemap.xml")
    assert index.status_code == 200 and "s-maxage=3600" in index.headers["cache-control"]
    files = re.findall(r"/sitemaps/([\w-]+)\.xml", index.text)
    assert files[:5] == ["pages-1", "market-1", "vault-1", "portfolios-1", "profiles-1"] and "cards-1" in files
    assert index.text.count("<lastmod>") == len(files)

    pages = _locs(client, "pages-1")
    for p in ("/", "/pokemon", "/pokemon/sets", "/pokemon/years", "/pokemon/vault", "/pokemon/portfolios", "/vpn", "/earn", "/earn/brain", "/learn"):
        assert any(x.endswith(p) and x.count("/") == 2 + p.count("/") for x in pages), p
    assert not any("passport" in x or "admin" in x or "wallet" in x or "/pokemon/portfolio/" in x for x in pages)
    assert _locs(client, "pages") == pages  # the older name still answers

    market = _locs(client, "market-1")
    assert "http://127.0.0.1:5173/pokemon?set=Base%20Set" in market and "http://127.0.0.1:5173/pokemon?year=1999" in market
    assert all("&" not in x or "&amp;" in x for x in market)

    vault = _locs(client, "vault-1")
    assert vault == [f"http://127.0.0.1:5173/pokemon/vault/{r.id}" for r in shown] and not any(str(r.id) in v.rsplit("/", 1)[1] for r in hidden for v in vault)

    assert _locs(client, "portfolios-1") == ["http://127.0.0.1:5173/pokemon/portfolio/u/open_ash"]  # never private, hidden, waiting or empty
    profiles = _locs(client, "profiles-1")
    assert "http://127.0.0.1:5173/u/kiwi_fan" in profiles and not any("quiet_one" in x or "secret_misty" in x for x in profiles)

    n_cards = db.scalar(select(PokemonCard.id).order_by(PokemonCard.id.desc()).limit(1))
    cards = _locs(client, "cards-1")
    assert f"http://127.0.0.1:5173/pokemon/cards/{card.id}" in cards and len(cards) == len(set(cards)) and n_cards
    assert "<lastmod>" in client.get("/api/website/seo/sitemaps/cards-1.xml").text
    for bad in ("cards-99", "secrets-1", "cards-0", "cards-x", "portfolios-²"):
        assert client.get(f"/api/website/seo/sitemaps/{bad}.xml").status_code == 404, bad


def test_sitemaps_split_and_are_built_once_an_hour(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog.ensure_catalog(db)
    total = len(db.scalars(select(PokemonCard.id)).all())
    monkeypatch.setattr(sitemaps, "PART", 7)
    calls = {"n": 0}
    real = sitemaps.KINDS["cards"]

    def counting(s: Session) -> list:
        calls["n"] += 1
        return real(s)

    monkeypatch.setitem(sitemaps.KINDS, "cards", counting)
    files = re.findall(r"/sitemaps/(cards-\d+)\.xml", client.get("/api/website/seo/sitemap.xml").text)
    assert len(files) == -(-total // 7)
    got = [x for f in files for x in _locs(client, f)]
    assert len(got) == total and len(set(got)) == total
    assert calls["n"] == 1  # the index and every file came from one build
