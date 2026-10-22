"""Search engines and link previews for the public website (a single-page app, so its own HTML is the same everywhere).

- /api/website/seo/sitemap.xml: a sitemap index (the website serves it at /sitemap.xml), pointing at the page list, the
  card catalogue in parts of 25,000 and the public profiles.
- /api/website/seo/og/<website path>: a small HTML page with that page's own title, description and picture, served
  only to link-preview bots (Telegram, X, WhatsApp, Discord...: the website's vercel.json picks them by user agent).
  It shows only what the page itself shows a visitor: the market's public (cleaned) numbers, a profile only when its
  member made it public. Unknown addresses get the site's general preview.
Public (admin_auth.PUBLIC_PREFIXES) and cached at the edge for an hour (edge_cache.SEO_PREFIX)."""

from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select

from app.api.deps import DbSession, Runtime

router = APIRouter(tags=["seo"])

PART = 25_000  # card addresses per sitemap file (the limit is 50,000)
PAGES = {  # path -> (title, description): the public pages worth indexing (never the Passport or other personal pages)
    "": ("Price Chat · Everything has a price.", "Markets. Money. Culture. Short videos, a live Pokémon card market, and a community where your Kiwi levels up."),
    "pokemon": ("Pokémon Card Market · Price Chat", "Live prices, verified sales and population for 50,000 Pokémon cards, graded and raw."),
    "pokemon/trending": ("Trending Pokémon cards · Price Chat", "The Pokémon cards collectors are watching right now."),
    "pokemon/gainers": ("Top gaining Pokémon cards · Price Chat", "The Pokémon cards whose market price rose the most."),
    "pokemon/losers": ("Top falling Pokémon cards · Price Chat", "The Pokémon cards whose market price fell the most."),
    "pokemon/recent": ("Recent Pokémon card sales · Price Chat", "The latest verified sales of graded Pokémon cards."),
    "pokemon/years": ("Pokémon cards by year · Price Chat", "Every Pokémon card year, from 1996 to today, with market values."),
    "pokemon/sets": ("Pokémon card sets · Price Chat", "Every Pokémon card set with its cards and market values."),
    "pokemon/grades": ("Pokémon card grades · Price Chat", "Pokémon card prices by grading company and grade."),
    "pokemon/vault": ("Price Chat Vault · Japanese vintage Pokémon cards", "Vintage Japanese Pokémon cards from approved sellers."),
    "pokemon/portfolios": ("Pokémon card portfolios · Price Chat", "Collections that members chose to share, valued with market data."),
    "earn": ("Earn: Kiwi Trading Bot · Price Chat", "Free for every Kiwi Passport member: connect Hyperliquid and start in paper trading."),
    "earn/brain": ("Kiwi Brain · Price Chat", "How the Kiwi trading bot reads the market, signal by signal."),
    "leaderboard": ("Leaderboard · Price Chat", "The top Kiwis in the Price Chat community."),
    "giveaway": ("Giveaway · Price Chat", "Price Chat giveaways for the community."),
    "learn": ("Learn · Price Chat", "How Kiwi XP, ranks and rewards work in Price Chat."),
    "join": ("Join Price Chat", "Sign in with Telegram, X or Google. Earn XP. Level up."),
    "content": ("Videos · Price Chat", "Every Price Chat video, newest first."),
    "website/privacy": ("Privacy · Price Chat", "How Price Chat handles your data."),
    "website/terms": ("Terms · Price Chat", "The terms for using Price Chat."),
    "website/disclaimer": ("Disclaimer · Price Chat", "Price Chat is not financial advice."),
    "website/contact": ("Contact · Price Chat", "How to reach Price Chat."),
}


def _xml(body: str) -> Response:
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n' + body, media_type="application/xml")


def _urlset(base: str, paths: list[str]) -> Response:
    rows = "".join(f"<url><loc>{escape(f'{base}/{p}' if p else base + '/')}</loc></url>" for p in paths)
    return _xml(f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>')


@router.get("/website/seo/sitemap.xml")
def sitemap_index(db: DbSession, runtime: Runtime) -> Response:
    from app.models.pokemon import PokemonCard

    base = runtime.settings.members_public_url
    parts = -(-(db.scalar(select(func.count(PokemonCard.id))) or 0) // PART)
    names = ["pages", "profiles"] + [f"cards-{k + 1}" for k in range(parts)]
    rows = "".join(f"<sitemap><loc>{base}/sitemaps/{n}.xml</loc></sitemap>" for n in names)
    return _xml(f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</sitemapindex>')


@router.get("/website/seo/sitemaps/{name}.xml")
def sitemap_part(name: str, db: DbSession, runtime: Runtime) -> Response:
    from app.models.members import Member
    from app.models.pokemon import PokemonCard

    base = runtime.settings.members_public_url
    if name == "pages":
        return _urlset(base, list(PAGES))
    if name == "profiles":
        users = db.scalars(select(Member.username).where(Member.public_profile.is_(True), Member.status == "active",
                                                         Member.username.is_not(None)).order_by(Member.id)).all()
        return _urlset(base, [f"u/{u}" for u in users])
    if name.startswith("cards-") and name[6:].isdigit() and int(name[6:]) >= 1:
        k = int(name[6:]) - 1
        ids = db.scalars(select(PokemonCard.id).order_by(PokemonCard.id).offset(k * PART).limit(PART)).all()
        if ids:
            return _urlset(base, [f"pokemon/cards/{i}" for i in ids])
    raise HTTPException(status.HTTP_404_NOT_FOUND, "No such sitemap")


def _money(cents: int) -> str:
    return f"${cents / 100:,.0f}" if cents >= 100_000 else f"${cents / 100:,.2f}"


def _card(db: Any, card_id: int) -> tuple[str, str, str | None] | None:
    """The card page's title, description and picture, from the same cleaned data the public card page shows."""
    from app.models.pokemon import PokemonCard
    from app.pokemon import public as market_public
    from app.pokemon import service

    c = db.get(PokemonCard, card_id)
    if c is None:
        return None
    d = market_public.sanitize(service.card_detail(db, c), f"/api/pokemon/cards/{card_id}")
    card, head = d["card"], d.get("headline") or {}
    where = " · ".join(x for x in (card["set_name"], f"#{card['card_number']}" if card.get("card_number") else "", str(card["year"]),
                                    card["edition"] if card.get("edition") not in (None, "", "—", "Unlimited") else "") if x)
    title = f"{card['name']} · {where}"
    price = head.get("market_price")
    desc = (f"{head.get('label') or 'Market'} market price {_money(price)}. " if isinstance(price, int) and price > 0 else "") \
        + "Live prices, verified sales and population on Price Chat."
    return title, desc, card.get("image_url")


def _profile(db: Any, username: str) -> tuple[str, str, str | None] | None:
    from app.members import insights

    p = insights.public_profile(db, username)  # None unless the member made the profile public
    if p is None:
        return None
    joined = p["joined_at"][:7]
    desc = f"{p['rank']['name']} · Kiwi Rank {p['rank']['level']}" + (f" · {len(p['badges'])} badges" if p["badges"] else "") \
        + f" · in Price Chat since {joined}."
    return f"{p['username']} on Price Chat", desc, None


@router.get("/website/seo/og/{path:path}")
def link_preview(path: str, db: DbSession, runtime: Runtime) -> HTMLResponse:
    base = runtime.settings.members_public_url
    path = path.strip("/")
    parts = path.split("/")
    found: tuple[str, str, str | None] | None = None
    if len(parts) == 3 and parts[:2] == ["pokemon", "cards"] and parts[2].isdigit():
        found = _card(db, int(parts[2]))
    elif len(parts) == 2 and parts[0] == "u" or len(parts) == 3 and parts[:2] == ["website", "u"]:
        found = _profile(db, parts[-1][:32])
    if found is None:
        found = (*PAGES.get(path, PAGES[""]), None)
    title, desc, image = found
    image = image or f"{base}/og.jpg"
    url = f"{base}/{path}" if path else base + "/"
    big = image.endswith("/og.jpg")  # the wide site picture; a card scan is portrait, so a small preview shows all of it
    t, d, u, i = escape(title), escape(desc), escape(url), escape(image)
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{t}</title>
<meta name="description" content="{d}"><link rel="canonical" href="{u}">
<meta property="og:type" content="website"><meta property="og:site_name" content="Price Chat"><meta property="og:url" content="{u}">
<meta property="og:title" content="{t}"><meta property="og:description" content="{d}"><meta property="og:image" content="{i}">
<meta name="twitter:card" content="{'summary_large_image' if big else 'summary'}"><meta name="twitter:site" content="@pricechat">
<meta name="twitter:title" content="{t}"><meta name="twitter:description" content="{d}"><meta name="twitter:image" content="{i}">
<meta http-equiv="refresh" content="0;url={u}"></head><body><a href="{u}">{t}</a><p>{d}</p></body></html>"""
    return HTMLResponse(html)
