"""Audit 2026-11-08, website findings: UX-2 (absolute og:image on card link previews) and UX-6 (no price promised for Kiwi VPN in
the search / preview description, because payments can be off)."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon import PokemonCard
from app.pokemon import catalog
from app.seo import heads


def _meta(html: str, prop: str) -> str:
    m = re.search(rf'property="{prop}" content="([^"]*)"', html)
    assert m, prop
    return m.group(1)


def test_card_preview_image_is_an_absolute_address(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pokemon import service

    catalog.ensure_catalog(db)
    card = db.scalar(select(PokemonCard).order_by(PokemonCard.id))
    monkeypatch.setattr(service, "_picture", lambda c: "/cards/u/b5292d9dcf0b2022.webp")  # what production stores: a site path
    html = client.get(f"/api/website/seo/og/pokemon/cards/{card.id}").text
    base = client.app.state.runtime.settings.members_public_url.rstrip("/")  # type: ignore[attr-defined]
    assert _meta(html, "og:image") == f"{base}/cards/u/b5292d9dcf0b2022.webp"
    assert re.search(r'name="twitter:image" content="https?://', html)


def test_card_preview_keeps_an_already_absolute_image(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pokemon import service

    catalog.ensure_catalog(db)
    card = db.scalar(select(PokemonCard).order_by(PokemonCard.id))
    monkeypatch.setattr(service, "_picture", lambda c: "https://images.example.com/c.png")
    assert _meta(client.get(f"/api/website/seo/og/pokemon/cards/{card.id}").text, "og:image") == "https://images.example.com/c.png"


def _scout_card(db: Session, member) -> dict:  # type: ignore[no-untyped-def]
    from app.members import rewards

    return next(c for c in rewards.overview(db, member)["ranks"] if c["rank"]["key"] == "scout")


def test_scout_card_does_not_claim_to_unlock_earn_while_earn_is_open_to_everyone(db: Session) -> None:
    """UX-8: Earn's own rule (min_rank_level 1, the default) lets every member in, so the rank ladder can't say Scout unlocks it."""
    from app.members import service
    from app.members.catalog import ensure_catalog
    from app.tradingbot import service as earn

    ensure_catalog(db)
    m = service.create_member(db)
    db.commit()
    assert earn.settings(db).min_rank_level == 1
    card = _scout_card(db, m)
    assert "UNLOCK" not in card["reward"]["title"] and card["reward"]["open_to_all"] is True and card["unlocked"] is True
    assert "every Kiwi Passport member" in card["reward"]["description"]
    assert _scout_card(db, None)["reward"]["open_to_all"] is True  # the signed-out catalog says the same
    earn.settings(db).min_rank_level = 4  # the owner gates Earn at Scout again: the original reward comes back
    card = _scout_card(db, m)
    assert card["reward"]["title"] == "UNLOCK EARN — Your Kiwi Trading Bot" and "open_to_all" not in card["reward"] and card["unlocked"] is False


def test_vpn_description_promises_no_price_while_payments_can_be_off() -> None:
    assert "$" not in heads.PAGES["vpn"][1] and "free trial" in heads.PAGES["vpn"][1]


def test_legal_pages_have_their_own_addresses(client: TestClient) -> None:
    """UX-17: /privacy, /terms, /disclaimer and /contact are the legal pages' addresses (vercel.json 301s /website/<page>)."""
    for k in ("privacy", "terms", "disclaimer", "contact"):
        assert k in heads.PAGES and f"website/{k}" not in heads.PAGES
        r = client.get(f"/api/website/seo/og/{k}")
        assert "x-robots-tag" not in r.headers and 'rel="canonical" href="' in r.text and f'/{k}"' in r.text
    pages = client.get("/api/website/seo/sitemaps/pages.xml").text
    assert "/privacy</loc>" in pages and "/website/privacy" not in pages
