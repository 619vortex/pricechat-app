"""Search engines and link previews for the public website (a single-page app, so its own HTML is the same everywhere).

- /api/website/seo/sitemap.xml: a sitemap index (the website serves it at /sitemap.xml) listing the files below, each with
  the date its newest address last changed. /api/website/seo/sitemaps/<kind>-<n>.xml: one file (app.seo.sitemaps says
  what each kind lists and what is never listed).
- /api/website/seo/og/<website path>: a small HTML page with that page's own title, description, picture and JSON-LD,
  served only to link-preview bots (Telegram, X, WhatsApp, Discord...: the website's vercel.json picks them by user agent).
  The strings are the ones the page itself sets in the browser (app.seo.heads mirrors frontend/src/website/seo.ts), from
  what the page shows a visitor: the market's public (cleaned) numbers, a profile or portfolio only when its member made
  it public. Private, owner-only and unknown addresses get the site's general preview with noindex (meta + X-Robots-Tag).
Public (admin_auth.PUBLIC_PREFIXES) and cached at the edge for an hour (edge_cache.SEO_PREFIX)."""

from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response

from app.api.deps import DbSession, Runtime
from app.seo import heads, sitemaps

router = APIRouter(tags=["seo"])

PAGES = heads.PAGES  # kept for older imports
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def _xml(body: str) -> Response:
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n' + body, media_type="application/xml")


def _cache(request: Request) -> dict[str, Any]:
    """The sitemaps' hourly cache, kept on the app (one per process)."""
    c = getattr(request.app.state, "seo_sitemaps", None)
    if c is None:
        c = request.app.state.seo_sitemaps = {}
    return c


def _lastmod(d: Any) -> str:
    return f"<lastmod>{d.isoformat()}</lastmod>" if d else ""


@router.get("/website/seo/sitemap.xml")
def sitemap_index(request: Request, db: DbSession, runtime: Runtime) -> Response:
    base = runtime.settings.members_public_url.rstrip("/")
    rows = "".join(f"<sitemap><loc>{escape(f'{base}/sitemaps/{n}.xml')}</loc>{_lastmod(d)}</sitemap>" for n, d in sitemaps.index(db, _cache(request)))
    return _xml(f"<sitemapindex {NS}>{rows}</sitemapindex>")


@router.get("/website/seo/sitemaps/{name}.xml")
def sitemap_part(name: str, request: Request, db: DbSession, runtime: Runtime) -> Response:
    base = runtime.settings.members_public_url.rstrip("/")
    found = sitemaps.part(db, name, _cache(request))
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such sitemap")
    rows = "".join(f"<url><loc>{escape(f'{base}/{p}' if p else base + '/')}</loc>{_lastmod(d)}</url>" for p, d in found)
    return _xml(f"<urlset {NS}>{rows}</urlset>")


def _num(s: str) -> bool:
    """A plain id: ASCII digits only ("²".isdigit() is True and int() of it fails), small enough for the database."""
    return s.isascii() and s.isdigit() and len(s) <= 12


Found = dict[str, Any]  # title, description, image?, json_ld?, path? (the canonical address when it isn't the one asked for)


def _card(db: Any, card_id: int, grade: str | None) -> Found | None:
    """The card page's head, from the same cleaned data the public card page shows."""
    from app.models.pokemon import PokemonCard
    from app.pokemon import public as market_public
    from app.pokemon import service

    c = db.get(PokemonCard, card_id)
    if c is None:
        return None
    d = market_public.sanitize(service.card_detail(db, c), f"/api/pokemon/cards/{card_id}")
    g = heads.card_grade(d, grade)
    return {**heads.card_head(d, g), "json_ld": heads.card_json_ld(d, g), "path": f"/pokemon/cards/{card_id}"}


def _landing(db: Any, params: list[tuple[str, str]]) -> Found | None:
    from app.pokemon import service

    if not params:
        return None
    opts = service.filter_options(db)
    return heads.landing_head(params, set(opts["sets"]), set(opts["years"]))


def _vault(db: Any, settings: Any, listing_id: int) -> Found | None:
    from app.members.service import MemberError
    from app.vault import service

    try:
        c = service.detail(db, settings, listing_id, None)  # approved, eligible, from an enabled source; else MemberError
    except MemberError:
        return None
    return {**heads.vault_head(c), "json_ld": heads.vault_json_ld(c), "path": f"/pokemon/vault/{listing_id}"}


def _portfolio(db: Any, username: str) -> Found | None:
    from app.members.service import MemberError
    from app.portfolio import service

    try:
        p = service.public_portfolio(db, username, None, limit=1)  # MemberError unless public (no viewer: never the owner's view)
    except MemberError:
        return None
    if p["visibility"] != "public":
        return None
    return {**heads.portfolio_head(p), "json_ld": heads.portfolio_json_ld(p["username"]),
            "path": f"/pokemon/portfolio/u/{heads.encode_component(p['username'])}"}


def _profile(db: Any, username: str) -> Found | None:
    from app.members import insights

    p = insights.public_profile(db, username)  # None unless the member made the profile public
    if p is None:
        return None
    return {**heads.profile_head(p), "path": f"/u/{heads.encode_component(p['username'])}"}


@router.get("/website/seo/og/{path:path}")
def link_preview(path: str, request: Request, db: DbSession, runtime: Runtime) -> HTMLResponse:
    base = runtime.settings.members_public_url.rstrip("/")
    path = path.strip("/")
    parts = path.split("/")
    found: Found | None = None
    if len(parts) == 3 and parts[:2] == ["pokemon", "cards"] and _num(parts[2]):
        found = _card(db, int(parts[2]), request.query_params.get("grade"))
    elif len(parts) == 3 and parts[:2] == ["pokemon", "vault"] and _num(parts[2]):
        found = _vault(db, runtime.settings, int(parts[2]))
    elif len(parts) == 4 and parts[:3] == ["pokemon", "portfolio", "u"]:
        found = _portfolio(db, parts[3][:32])
    elif len(parts) == 2 and parts[0] == "u" or len(parts) == 3 and parts[:2] == ["website", "u"]:
        found = _profile(db, parts[-1][:32])
    elif path == "pokemon":
        found = _landing(db, list(request.query_params.multi_items()))
    if found is None and path in heads.PAGES:
        title, desc = heads.PAGES[path]
        found = {"title": title, "description": desc}
    # a private portfolio / profile, an owner-only page or an unknown address: the site's general card, never indexed
    noindex = found is None or heads.is_noindex(path)
    if found is None:  # the general card points at the home page: a private name isn't repeated back
        title, desc = heads.PAGES[""]
        found = {"title": title, "description": desc, "path": "/"}
    image = found.get("image") or f"{base}/og.jpg"
    url = base + (found.get("path") or (f"/{path}" if path else "/"))
    big = image.endswith("/og.jpg")  # the wide site picture; a card scan is portrait, so a small preview shows all of it
    t, d, u, i = escape(found["title"]), escape(found["description"]), escape(url), escape(image)
    robots = '<meta name="robots" content="noindex">' if noindex else ""
    ld = "" if noindex else heads.json_ld_script(found.get("json_ld") or [], base)
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{t}</title>
<meta name="description" content="{d}">{robots}<link rel="canonical" href="{u}">
<meta property="og:type" content="website"><meta property="og:site_name" content="Price Chat"><meta property="og:url" content="{u}">
<meta property="og:title" content="{t}"><meta property="og:description" content="{d}"><meta property="og:image" content="{i}">
<meta name="twitter:card" content="{'summary_large_image' if big else 'summary'}"><meta name="twitter:site" content="@pricechat">
<meta name="twitter:title" content="{t}"><meta name="twitter:description" content="{d}"><meta name="twitter:image" content="{i}">
{ld}<meta http-equiv="refresh" content="0;url={u}"></head><body><a href="{u}">{t}</a><p>{d}</p></body></html>"""
    return HTMLResponse(html, headers={"X-Robots-Tag": "noindex"} if noindex else None)
