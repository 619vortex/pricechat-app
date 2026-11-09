"""Every public page's head: title, description, picture, robots and JSON-LD.

The website builds the SAME strings in the browser (frontend/src/website/seo.ts) for visitors and Google; this module builds
them for link-preview bots (Telegram, X, WhatsApp, Discord...), which never run the app (frontend/vercel.json sends them to
/api/website/seo/og/...). Both test suites compare their builders with frontend/src/website/seoHeads.golden.json, so the
two cannot drift apart silently: change seo.ts, this file and the golden file together.

Formatting follows the browser's en-US Intl output exactly (usd, num, money). Builders take the same public, cleaned
dicts the pages receive from the API (app.pokemon.public.sanitize, the Vault's public detail, the public portfolio), so a
preview never shows more than the page does. JSON-LD is built with site paths and made absolute with the site's address.
"""

import json
import re
from typing import Any
from urllib.parse import quote

SITE = "Price Chat"
DASH = "—"

# path (no leading "/") -> (title, description). Titles are complete. Mirrors PAGE_HEADS in seo.ts.
PAGES: dict[str, tuple[str, str]] = {
    "": ("Price Chat · Everything has a price.", "Markets. Money. Culture. Everything has a price. Short stories and conversations from Price Chat."),
    "content": ("All content · Price Chat", "Every Price Chat video and Short, newest first."),
    "join": ("Join Price Chat", "Sign in with Telegram, X or Google. Earn XP. Level up."),
    "leaderboard": ("Kiwi Leaderboard · Price Chat", "Price Chat members by Kiwi XP and invites."),
    "giveaway": ("Open Packs · Price Chat", "Open free virtual Pokémon packs for fun and earn Kiwi XP. Virtual cards only: no money, no real cards."),
    "earn": ("Earn: Kiwi Trading Bot · Price Chat", "Free for every Kiwi Passport member: connect Hyperliquid and start in paper trading."),
    "earn/brain": ("Kiwi Brain · Price Chat", "How the Kiwi trading bot reads the market, signal by signal, live."),
    "earn/lunch": ("Earn: Lunch Bot · Price Chat", "Lunch Bot paper-trades pump.fun launches at real on-chain prices."),
    "earn/funding": ("Earn: Funding Arb · Price Chat", "Live perp funding rates across Hyperliquid, Lighter, Orderly and Backpack, and the biggest gaps between them."),
    "earn/kalshi": ("Earn: Kalshi · Price Chat", "Watch Kiwi's Kalshi bot scan every market against Kalshi's fees, and follow your own Kalshi positions marked at the current bid after Kalshi's fees."),
    "learn": ("Beginner content · Price Chat", "Exclusive beginner lessons for Kiwi Chicks and above."),
    "vpn": ("Kiwi VPN · Price Chat", "A WireGuard VPN from Price Chat. We don't log the sites you visit. Start with a 30-day free trial."),
    "vpn/privacy": ("Kiwi VPN Privacy · Price Chat", "What Kiwi VPN and its browser extension store, what they don't, and why they need their permissions."),
    "privacy": ("Privacy · Price Chat", "How Price Chat handles member data, connected accounts and Kiwi XP."),
    "terms": ("Terms · Price Chat", "The rules of the Price Chat community and Kiwi XP."),
    "disclaimer": ("Disclaimer · Price Chat", "Price Chat content is for information and entertainment."),
    "contact": ("Contact · Price Chat", "How to reach Price Chat."),
    "pokemon": ("Pokémon Market · Price Chat", "Vintage Pokémon card prices, market caps and verified sales, and the Price Chat Vault of vintage Japanese cards."),
    "pokemon/trending": ("Trending Pokémon Cards · Price Chat", "The vintage Pokémon cards collectors are talking about and trading right now, with prices, 30-day moves and the reason each one is trending."),
    "pokemon/gainers": ("Pokémon Card Top Gainers · Price Chat", "The vintage Pokémon cards whose estimated price rose the most over the last 30 and 90 days, by grade."),
    "pokemon/losers": ("Pokémon Card Top Losers · Price Chat", "The vintage Pokémon cards whose estimated price fell the most over the last 30 and 90 days, by grade."),
    "pokemon/recent": ("Recently Sold Pokémon Cards · Price Chat", "Completed, verified sales of vintage Pokémon cards, newest first: date, grade, price and where each card sold."),
    "pokemon/years": ("Pokémon Card Market Cap by Year · Price Chat", "The estimated market cap of Pokémon cards by release year, its share of the whole market, 30- and 90-day moves and each year's top cards."),
    "pokemon/sets": ("Pokémon Card Sets by Market Cap · Price Chat", "Every tracked Pokémon card set ranked by estimated market cap, with 30-day moves, verified sales volume, population and its most valuable cards."),
    "pokemon/grades": ("Pokémon Card Market by Grade · Price Chat", "PSA, BGS and CGC grades side by side: cards priced, market cap, verified sales volume, known population and the most valuable card at each grade."),
    "pokemon/vault": ("Price Chat Vault · Vintage Japanese Pokémon Cards", "Hand-picked 1996–2002 Japanese Pokémon cards (Base, Jungle, Fossil, Rocket Gang, Gym, Neo, e-Card) with the seller's real photos and prices."),
    "pokemon/portfolios": ("Explore Pokémon Card Collectors · Price Chat", "Public Pokémon card portfolios from the Price Chat community: top collections, most cards, most valuable cards and featured collectors."),
}

# owner-only or personal addresses (mirrors NOINDEX_PATHS in seo.ts), matched against the path without its leading "/"
NOINDEX = [re.compile(p) for p in (r"^passport$", r"^website/profile$", r"^website/preview$",
                                   r"^pokemon/portfolio(/add|/phone/[^/]+)?$", r"^pokemon/wallet$", r"^pokemon/vault/requests$")]

GRADES = ("PSA 10", "PSA 9", "PSA 8", "PSA 7", "BGS 10", "BGS 9.5", "CGC 10", "RAW NM")  # the card page's grade picker (mx.tsx)


def is_noindex(path: str) -> bool:
    return any(r.match(path.strip("/")) for r in NOINDEX)


# --- the browser's en-US number formats ----------------------------------------------------------------------------------


def num(v: int | None) -> str:
    """toLocaleString("en-US")."""
    return DASH if v is None else f"{v:,}"


def _fixed(cents: int, digits: int) -> str:
    """cents / 100 with `digits` decimals and thousands separators, rounding half away from zero (Intl's halfExpand)."""
    neg, c = cents < 0, abs(cents)
    if digits == 0:
        s = f"{(c + 50) // 100:,}"
    else:
        s = f"{c // 100:,}.{c % 100:02d}"
    return f"-{s}" if neg else s


def usd(cents: int | None) -> str:
    """pages/pokemon/format.ts usd(): "$12.50", and whole dollars from $1,000."""
    if cents is None:
        return DASH
    return "$" + _fixed(cents, 0 if cents >= 100_000 else 2)


_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "CA$", "AUD": "A$", "HKD": "HK$", "CNY": "CN¥", "KRW": "₩",
           "NZD": "NZ$", "INR": "₹", "MXN": "MX$", "TWD": "NT$", "ILS": "₪", "VND": "₫", "BRL": "R$", "PHP": "₱"}
_NO_DECIMALS = {"JPY", "KRW", "VND", "CLP", "ISK", "UGX"}


def money(cents: int | None, currency: str | None) -> str:
    """vault/api.ts money(): Intl.NumberFormat("en-US", {style: "currency", currency})."""
    if cents is None:
        return DASH
    code = (currency or "USD").upper()
    amount = _fixed(cents, 0 if code in _NO_DECIMALS else 2)
    sym = _SYMBOL.get(code)
    if sym is None:
        return f"{code} {amount}"
    return f"-{sym}{amount[1:]}" if amount.startswith("-") else f"{sym}{amount}"


def encode_component(s: str) -> str:
    """encodeURIComponent()."""
    return quote(s, safe="-_.!~*'()")


def _crumbs(items: list[tuple[str, str]]) -> dict[str, Any]:
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": it} for i, (n, it) in enumerate(items)]}


def absolutize(v: Any, origin: str) -> Any:
    if isinstance(v, str):
        return origin.rstrip("/") + v if v.startswith("/") else v
    if isinstance(v, list):
        return [absolutize(x, origin) for x in v]
    if isinstance(v, dict):
        return {k: absolutize(x, origin) for k, x in v.items()}
    return v


def json_ld_script(blocks: list[dict[str, Any]], origin: str) -> str:
    return "".join('<script type="application/ld+json">' + json.dumps(absolutize(b, origin), ensure_ascii=False).replace("<", "\\u003c")
                   + "</script>" for b in blocks)


# --- card page -----------------------------------------------------------------------------------------------------------


def _card_ident(d: dict[str, Any]) -> tuple[str, str]:
    c = d["card"]
    name = f"{c['name']}{' ' + c['edition'] if c['edition'] != DASH else ''}"
    ident = f"{c['year']} {'' if c['language'] == 'English' else c['language'] + ' '}{c['set_name']}{' ' + c['card_number'] if c.get('card_number') else ''}"
    return name, ident


def _valuation(d: dict[str, Any], grade: str) -> dict[str, Any] | None:
    return next((v for v in [*d.get("valuations", []), *d.get("other_grades", [])] if v.get("label") == grade), None)


def card_grade(d: dict[str, Any], asked: str | None) -> str:
    """The page's grade (?grade=, PSA 10 by default), only from the picker or the card's own grades (never echoed text)."""
    known = {*GRADES, *(v.get("label") for v in [*d.get("valuations", []), *d.get("other_grades", [])])}
    return asked if asked and asked in known else "PSA 10"


def card_head(d: dict[str, Any], grade: str) -> dict[str, Any]:
    name, ident = _card_ident(d)
    v = _valuation(d, grade) or {}
    facts = ", ".join(x for x in (f"estimated {grade} price {usd(v['market_price'])}" if v.get("market_price") is not None else None,
                                  f"{grade} population {num(v['population'])}" if v.get("population") is not None else None) if x)
    return {"title": f"{name} · {ident} · {grade} Price · {SITE}",
            "description": f"{name} ({ident}){': ' + facts if facts else ''}. Verified sales, price history, population and every grade on the Price Chat Pokémon Market.",
            "image": d["card"].get("image_url")}


def card_json_ld(d: dict[str, Any], grade: str) -> list[dict[str, Any]]:
    """A Product (the printing) with its known facts, no offers (the page shows estimates and past sales, nothing for sale)."""
    c = d["card"]
    name, ident = _card_ident(d)
    v = _valuation(d, grade) or {}
    props: list[dict[str, Any]] = []
    if v.get("market_price") is not None:
        props.append({"@type": "PropertyValue", "name": f"Estimated {grade} price (USD)", "value": v["market_price"] / 100})
    if v.get("population") is not None:
        props.append({"@type": "PropertyValue", "name": f"{grade} population", "value": v["population"]})
    product: dict[str, Any] = {"@context": "https://schema.org", "@type": "Product", "name": f"{name} ({ident})", "url": f"/pokemon/cards/{c['id']}",
                               "category": "Pokémon trading card", "brand": {"@type": "Brand", "name": "Pokémon"},
                               "description": card_head(d, grade)["description"]}
    if c.get("image_url"):
        product["image"] = c["image_url"]
    if c.get("card_number"):
        product["mpn"] = c["card_number"]
    if props:
        product["additionalProperty"] = props
    return [product, _crumbs([("Markets", "/pokemon"), (c["set_name"], f"/pokemon?set={encode_component(c['set_name'])}"),
                              (f"{c['name']} {grade}", f"/pokemon/cards/{c['id']}")])]


# --- market home narrowed to one set / one year ----------------------------------------------------------------------------


def landing_head(params: list[tuple[str, str]], sets: list[str] | set[str], years: list[int] | set[int]) -> dict[str, str] | None:
    """`params` = the query string's (key, value) pairs in order. A landing page only when the address holds just that one
    filter and the market lists the value (seo.ts landingHead)."""
    keys = {k for k, _ in params}
    if len(keys) != 1:
        return None
    key = next(iter(keys))
    value = next((v for k, v in params if k == key), "")  # URLSearchParams.get(): the first one
    if key == "set" and value and value in sets:
        return {"title": f"{value} Pokémon Card Prices · {SITE}", "path": f"/pokemon?set={encode_component(value)}",
                "description": f"Every {value} Pokémon card ranked by estimated market value, with verified sales, population and 30-day moves."}
    if key == "year" and re.fullmatch(r"\d{4}", value) and int(value) in years:
        return {"title": f"{value} Pokémon Card Prices · {SITE}", "path": f"/pokemon?year={value}",
                "description": f"Every Pokémon card released in {value} ranked by estimated market value, with verified sales, population and 30-day moves."}
    return None


# --- Vault card ------------------------------------------------------------------------------------------------------------


def vault_card_name(c: dict[str, Any]) -> str:
    if c.get("pokemon") is not None:
        return str(c["pokemon"])
    rest = re.sub(r"^\s*\[[^\]]*\]\s*", "", c["title"], count=1)
    return re.split(r" Pok[eé]mon", rest, flags=re.IGNORECASE)[0][:60]


def vault_head(c: dict[str, Any]) -> dict[str, Any]:
    imgs = c.get("images") or []
    return {"title": f"{vault_card_name(c)}{' · ' + c['set_name'] if c.get('set_name') else ''}"
                     f"{' · ' + money(c['price'], c.get('currency')) if c.get('price') is not None else ''} · Price Chat Vault",
            "description": f"{c['title']}. {'Ungraded' if c.get('condition') is None else c['condition']} {str(c['year']) + ' ' if c.get('year') else ''}Japanese Pokémon card in the "
                           "Price Chat Vault: the seller's own photos and price, re-checked every few hours.",
            "image": imgs[0] if imgs else None}


_AVAILABILITY = {"available": "https://schema.org/InStock", "sold_out": "https://schema.org/SoldOut", "ended": "https://schema.org/Discontinued"}


def vault_json_ld(c: dict[str, Any]) -> list[dict[str, Any]]:
    """The seller's real listing: a Product with its Offer (the seller's own price and availability, as last checked)."""
    name = vault_card_name(c)
    product: dict[str, Any] = {"@context": "https://schema.org", "@type": "Product", "name": name, "description": c["title"],
                               "url": f"/pokemon/vault/{c['id']}", "category": "Pokémon trading card"}
    if c.get("images"):
        product["image"] = list(c["images"][:4])
    if c.get("price") is not None:
        offer: dict[str, Any] = {"@type": "Offer", "price": f"{c['price'] / 100:.2f}", "priceCurrency": c.get("currency") or "USD",
                                 "url": c.get("url") or f"/pokemon/vault/{c['id']}"}
        if c.get("availability") in _AVAILABILITY:
            offer["availability"] = _AVAILABILITY[c["availability"]]
        product["offers"] = offer
    return [product, _crumbs([("Markets", "/pokemon"), ("Vault", "/pokemon/vault"), (name, f"/pokemon/vault/{c['id']}")])]


# --- collectors and members ------------------------------------------------------------------------------------------------


def portfolio_head(p: dict[str, Any]) -> dict[str, str]:
    return {"title": f"{p['username']}'s Pokémon Card Collection · {SITE}",
            "description": f"{p['username']}'s Pokémon portfolio on Price Chat: {num(p['cards'])} cards, {num(p['unique_cards'])} unique, "
                           f"{num(p['graded'])} graded.{' ' + p['bio'] if p.get('bio') else ''}"}


def portfolio_json_ld(username: str) -> list[dict[str, Any]]:
    return [_crumbs([("Markets", "/pokemon"), ("Collectors", "/pokemon/portfolios"), (username, f"/pokemon/portfolio/u/{encode_component(username)}")])]


def profile_head(p: dict[str, Any]) -> dict[str, str]:
    badges = len(p.get("badges") or [])
    return {"title": f"{p['username']} · Kiwi Passport · {SITE}",
            "description": f"{p['rank']['name']} · Kiwi Rank {p['rank']['level']}{f' · {badges} badges' if badges else ''} · in Price Chat since {p['joined_at'][:7]}."}
