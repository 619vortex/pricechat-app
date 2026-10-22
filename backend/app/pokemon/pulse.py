"""Pokémon Market Pulse: a sidebar of public X posts about the card market (auctions, grading, vintage, record sales).

Uses the EXISTING X integration (credentials, client, budget guard, usage accounting). Cost control: ONE combined
recent-search query per refresh (the six topics OR'ed, plus trusted accounts), newest-first with since_id so a refresh only
pays for posts it has not seen, max_results 10, and every call pre-checked against the X daily / monthly budget.
Recurring refresh is OFF until an admin enables it; a manual refresh has a cooldown.

Posts are context only: they are never sale records and never feed any price, change or market cap.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.pokemon import PokemonPulseConfig, PokemonPulsePost

TOPICS = ('"Pokemon cards" PSA', '"Pokemon TCG" auction', '"PSA 10" Charizard', '"vintage Pokemon" sold', '"Pokemon card" record sale',
          "Pokemon Goldin auction")
EXCLUDE = ("-is:retweet -is:reply -giveaway -giveaways -nft -airdrop -launchpad -onchain -\"on-chain\" -usdc -memecoin -crypto -token "
           "-pfp -gacha lang:en")
MAX_QUERY = 512  # X recent search query length limit on the standard tiers
MAX_RESULTS = 10  # X's minimum per request: the cheapest possible refresh
COOLDOWN = timedelta(minutes=5)
KEEP_DAYS = 14

_SPAM = re.compile(r"\b(giveaway|give away|giving away|to win|enter to win|rt (?:&|and) follow|follow (?:&|and) (?:rt|retweet)|tag \d+ friends|"
                   r"airdrop|nft|crypto|presale|promo code|discount code|use code|dm (?:me|us) (?:for|to)|link in bio|onlyfans|whatsapp)\b", re.I)
# crypto / web3 tie-ins and pack-opening, gacha or play-to-earn promotions: not market information
_CRYPTO = re.compile(r"\b(launchpad|on-?chain|usdc|web3|memecoin|creator fees|pump\.fun|solana|tokeni[sz]ed|backs? (?:your|the) coin)\b", re.I)
_CASHTAG = re.compile(r"(?<![\w$])\$[A-Z]{2,8}\b")  # "$CERT", "$PFP" (a coin ticker); "$5,000" is a price, not a cashtag
_PROMO = re.compile(r"\b(lucky \w+ pack|fragment pack|digital packs?|mystery packs?|diamond pack|packs? opened|what are you pulling|"
                    r"play the game|play,? earn|get real pok[eé]mon cards|earn (?:double )?points|rip(?:s|ping)? app|auto-?sold|insured value|"
                    r"use (?:my|our) (?:link|code)|sign up (?:now|today)|avatar|pfp)\b", re.I)
_GAMING = re.compile(r"\b(pokemon go|pok[eé]mon go|scarlet|violet|legends:? z-?a|legends arceus|nintendo switch|shiny hunt|raid battle|gym leader battle|"
                     r"pokemon unite|tcg ?pocket|tcg live|deck ?list|meta deck|tournament deck|anime episode|pokedex entry|open-?world|"
                     r"you are the pok[eé]mon|play (?:it )?now|early access|wishlist (?:it|now)|steam page)\b", re.I)
_GAME_AD = re.compile(r"open-?world|you are the pok[eé]mon|early access|steam page|wishlist (?:it|now)", re.I)  # a game advert even when it mentions PSA
_MARKET = re.compile(r"\b(psa|bgs|cgc|sgc|graded?|grading|slab|pop(?:ulation)? report|gem mint|auction|bid|bids|sold|sale|sales|record|goldin|heritage|"
                     r"pwcc|fanatics|ebay|price|prices|market|realized|hammer|comps?|value|\$\d)", re.I)
_AUCTION = re.compile(r"\b(auction|auctions|bid|bids|bidding|lot|goldin|heritage|pwcc|fanatics collect|hammer|realized|sold for|ends tonight|"
                      r"closing|premium auction)\b", re.I)
_VINTAGE = re.compile(r"\b(vintage|wotc|wizards of the coast|base set|jungle|fossil|team rocket|gym (?:heroes|challenge)|neo (?:genesis|discovery|"
                      r"revelation|destiny)|legendary collection|expedition|aquapolis|skyridge|e-?card|1st edition|first edition|shadowless|"
                      r"199[6-9]|200[0-3])\b", re.I)
_GRADING = re.compile(r"\b(psa ?10|psa ?9|bgs|cgc|sgc|gem mint|pop(?:ulation)? report|graded|grading)\b", re.I)
_SALE = re.compile(r"\b(sold|sale|record|realized|hammer|sells? for)\b", re.I)


# --- query -----------------------------------------------------------------------------------------------------------


def clean_handle(h: str) -> str | None:
    h = h.strip().lstrip("@")
    return h if re.fullmatch(r"[A-Za-z0-9_]{1,15}", h) else None


def build_query(trusted: list[str]) -> str:
    """One query for everything; trusted accounts are added (with a Pokémon term) while the 512-char limit allows."""
    topics = " OR ".join(f"({t})" for t in TOPICS)
    q = f"({topics}) {EXCLUDE}"
    handles = [h for h in (clean_handle(x) for x in trusted) if h]
    if handles:
        for n in range(len(handles), 0, -1):
            froms = " OR ".join(f"from:{h}" for h in handles[:n])
            candidate = f"(({topics}) OR (({froms}) pokemon)) {EXCLUDE}"
            if len(candidate) <= MAX_QUERY:
                return candidate
    return q


# --- classification --------------------------------------------------------------------------------------------------


def text_key(text: str) -> str:
    t = re.sub(r"https?://\S+|@\w+|#|[^\w\s$]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()[:140]
    return hashlib.sha1(t.encode()).hexdigest()[:24]


def classify(text: str, trusted: bool = False) -> tuple[bool, list[str], float, str | None]:
    """(keep, categories, relevance, why dropped)."""
    if _SPAM.search(text):
        return False, [], 0.0, "spam / giveaway"
    if _CRYPTO.search(text) or _CASHTAG.search(text):
        return False, [], 0.0, "crypto / web3 promotion"
    if _PROMO.search(text):
        return False, [], 0.0, "pack-opening / app promotion"
    if _GAMING.search(text) and (not _GRADING.search(text) or _GAME_AD.search(text)):
        return False, [], 0.0, "video game, not the card market"
    market = len(_MARKET.findall(text))
    if not market and not trusted:
        return False, [], 0.0, "no market information"
    cats = [c for c, rx in (("auctions", _AUCTION), ("vintage", _VINTAGE), ("grading", _GRADING), ("sale", _SALE)) if rx.search(text)]
    score = min(market, 5) + 2 * ("auctions" in cats) + 2 * ("vintage" in cats) + ("sale" in cats) + (3 if trusted else 0)
    return True, cats, float(score), None


def engagement(p: PokemonPulsePost) -> int:
    return p.likes + 2 * p.reposts + p.replies + p.quotes


# --- config ----------------------------------------------------------------------------------------------------------


def config(db: Session) -> PokemonPulseConfig:
    cfg = db.scalar(select(PokemonPulseConfig).order_by(PokemonPulseConfig.id))
    if cfg is None:
        cfg = PokemonPulseConfig(enabled=False, trusted_accounts=[], last_result={})
        db.add(cfg)
        db.commit()
    return cfg


def cooldown_left(cfg: PokemonPulseConfig, now: datetime | None = None) -> int:
    if cfg.last_refresh_at is None:
        return 0
    last = cfg.last_refresh_at if cfg.last_refresh_at.tzinfo else cfg.last_refresh_at.replace(tzinfo=timezone.utc)
    return max(0, int((last + COOLDOWN - (now or utcnow())).total_seconds()))


# --- refresh ---------------------------------------------------------------------------------------------------------


async def refresh(db: Session, client: Any, now: datetime | None = None, force: bool = False, older: bool = False) -> dict[str, Any]:
    """One recent-search request (budget-checked by the client). Stores new relevant posts; returns what it cost.
    `older`: step back in time instead (posts older than the oldest one stored, within X's 7-day recent-search window)."""
    from app.connectors.x.errors import XError

    now = now or utcnow()
    cfg = config(db)
    if not force and cooldown_left(cfg, now):
        return {"ok": False, "error": f"Refreshed recently: try again in {cooldown_left(cfg, now)} s.", "cooldown": cooldown_left(cfg, now)}
    trusted = {h.lower() for h in (clean_handle(x) for x in cfg.trusted_accounts or []) if h}
    query = build_query(sorted(trusted))
    try:
        oldest = db.scalar(select(PokemonPulsePost.post_id).order_by(PokemonPulsePost.created_at_x).limit(1)) if older else None
        body = await client.search_recent(query, MAX_RESULTS, since_id=None if older else cfg.since_id, rich=True, until_id=oldest)
    except XError as exc:
        cfg.last_error = str(exc)  # a refusal (budget, auth, limits) is not a refresh: no cooldown, no "updated" time
        cfg.last_result = {"at": now.isoformat(), "error": str(exc), "query_chars": len(query)}
        db.commit()
        return {"ok": False, "error": str(exc)}
    data = body.get("data") or []
    inc = body.get("includes") or {}
    users = {u["id"]: u for u in inc.get("users") or []}
    media = {m["media_key"]: m for m in inc.get("media") or []}
    kept = dropped = dup = 0
    reasons: dict[str, int] = {}
    newest = cfg.since_id
    for t in data:
        if not older:
            newest = t["id"] if newest is None or int(t["id"]) > int(newest) else newest
        u = users.get(t.get("author_id"), {})
        handle = str(u.get("username") or "unknown")
        ok, cats, score, why = classify(t.get("text", ""), handle.lower() in trusted)
        if not ok:
            dropped += 1
            reasons[why or "?"] = reasons.get(why or "?", 0) + 1
            continue
        key = text_key(t.get("text", ""))
        if db.scalar(select(PokemonPulsePost.id).where((PokemonPulsePost.post_id == t["id"]) | (PokemonPulsePost.text_key == key))):
            dup += 1
            continue
        m = next((media[k] for k in ((t.get("attachments") or {}).get("media_keys") or []) if k in media), None)
        pm = t.get("public_metrics") or {}
        created = datetime.fromisoformat(str(t.get("created_at", now.isoformat())).replace("Z", "+00:00"))
        db.add(PokemonPulsePost(
            post_id=t["id"], author_id=str(t.get("author_id") or ""), username=handle, display_name=str(u.get("name") or handle),
            avatar_url=u.get("profile_image_url"), verified=bool(u.get("verified")), text=t.get("text", ""), created_at_x=created,
            lang=t.get("lang"), url=f"https://x.com/{handle}/status/{t['id']}",
            media_url=(m.get("preview_image_url") or m.get("url")) if m else None,
            likes=int(pm.get("like_count") or 0), reposts=int(pm.get("retweet_count") or 0), replies=int(pm.get("reply_count") or 0),
            quotes=int(pm.get("quote_count") or 0), categories=cats, relevance=score, text_key=key, fetched_at=now))
        db.flush()
        kept += 1
    db.execute(delete(PokemonPulsePost).where(PokemonPulsePost.created_at_x < now - timedelta(days=KEEP_DAYS)))
    from app.connectors.x.client import PRICE_POST_READ, PRICE_USER_READ

    cost = round(len(data) * PRICE_POST_READ + len(users) * PRICE_USER_READ, 4)
    cfg.since_id, cfg.last_refresh_at, cfg.last_error = newest, now, None
    cfg.last_result = {"at": now.isoformat(), "posts_returned": len(data), "users_returned": len(users), "estimated_cost_usd": cost,
                       "kept": kept, "filtered": dropped, "duplicates": dup, "filter_reasons": reasons, "query_chars": len(query)}
    db.commit()
    return {"ok": True, **cfg.last_result}


def refilter(db: Session) -> dict[str, int]:
    """Re-apply the current filters to stored posts (after a filter change). Removes posts that no longer qualify."""
    cfg = config(db)
    trusted = {h.lower() for h in (clean_handle(x) for x in cfg.trusted_accounts or []) if h}
    removed: dict[str, int] = {}
    kept = 0
    for p in list(db.scalars(select(PokemonPulsePost))):
        ok, cats, score, why = classify(p.text, p.username.lower() in trusted)
        if not ok:
            removed[why or "?"] = removed.get(why or "?", 0) + 1
            db.delete(p)
        else:
            p.categories, p.relevance = cats, score
            kept += 1
    db.commit()
    return {"kept": kept, **{f"removed: {k}": v for k, v in removed.items()}}


# --- card linking ----------------------------------------------------------------------------------------------------

_SET_WORDS = {"base set": "Base Set", "jungle": "Jungle", "fossil": "Fossil", "team rocket": "Team Rocket", "neo genesis": "Neo Genesis",
              "neo destiny": "Neo Destiny", "skyridge": "Skyridge", "dragon frontiers": "EX Dragon Frontiers", "deoxys": "EX Deoxys",
              "pop series 5": "POP Series 5"}


def is_pokemon(c: Any) -> bool:
    return str(((getattr(c, "external_ids", None) or {}).get("supertype")) or "Pokémon").lower().startswith("pok")


def linked_cards(text: str, cards: list[Any]) -> list[Any]:
    """Catalog cards a post is about: the Pokémon named in it, narrowed by any set / edition words it also contains.
    Matching is on whole words; a post naming no catalog Pokémon links to nothing."""
    t = text.lower()
    cards = [c for c in cards if is_pokemon(c)]
    names = sorted({c.pokemon_name for c in cards}, key=len, reverse=True)
    hits = [n for n in names if re.search(rf"(?<![\w]){re.escape(n.lower())}(?![\w])", t)]
    if not hits:
        return []
    found = [c for c in cards if c.pokemon_name in hits]
    sets = {v for k, v in _SET_WORDS.items() if k in t}
    if sets:
        narrowed = [c for c in found if c.set_name in sets]
        found = narrowed or found
    if re.search(r"\b(1st|first) ed", t):
        found = [c for c in found if c.edition == "1st Edition"] or found
    return found


# --- read ------------------------------------------------------------------------------------------------------------


_EDITION_RANK = {"1st Edition": 0, "Shadowless": 1, "Unlimited": 2}


def card_chips(text: str, cards: list[Any], limit: int = 3) -> list[Any]:
    """One card per Pokémon mentioned (the 1st Edition print when there is one), in the order the post names them."""
    t = text.lower()
    best: dict[str, Any] = {}
    for c in sorted(linked_cards(text, cards), key=lambda c: (_EDITION_RANK.get(c.edition, 3), c.id)):
        best.setdefault(c.pokemon_name, c)
    return sorted(best.values(), key=lambda c: t.find(c.pokemon_name.lower()))[:limit]


def post_out(p: PokemonPulsePost, trusted: set[str], cards: list[Any] | None = None) -> dict[str, Any]:
    links = card_chips(p.text, cards or [])
    return {"cards": [{"id": c.id, "name": c.name, "set_name": c.set_name, "edition": c.edition, "image_url": c.image_url} for c in links],"id": p.post_id, "username": p.username, "name": p.display_name, "avatar": p.avatar_url, "verified": p.verified,
            "trusted": p.username.lower() in trusted, "text": p.text, "created_at": p.created_at_x.isoformat(), "url": p.url, "media": p.media_url,
            "metrics": {"likes": p.likes, "reposts": p.reposts, "replies": p.replies, "quotes": p.quotes}, "categories": p.categories}


def feed(db: Session, tab: str = "latest", limit: int = 30) -> list[dict[str, Any]]:
    cfg = config(db)
    trusted = {h.lower() for h in (clean_handle(x) for x in cfg.trusted_accounts or []) if h}
    posts = list(db.scalars(select(PokemonPulsePost).order_by(PokemonPulsePost.created_at_x.desc()).limit(500)))
    if tab == "auctions":
        posts = [p for p in posts if "auctions" in (p.categories or [])]
    elif tab == "vintage":
        posts = [p for p in posts if "vintage" in (p.categories or [])]
    elif tab == "popular":
        posts.sort(key=lambda p: (engagement(p) + (50 if p.username.lower() in trusted else 0), p.relevance), reverse=True)
    from app.models.pokemon import PokemonCard

    from app.pokemon.catalog import lite

    cards = [c for c in lite(db) if is_pokemon(c)]
    return [post_out(p, trusted, cards) for p in posts[:limit]]


def status(db: Session, settings: Any, runtime_x: Any) -> dict[str, Any]:
    from app.security.credentials import x_credentials
    from app.social.budget import PlatformBudget

    cfg = config(db)
    budget = PlatformBudget(db, settings)
    today, month, limits = budget.today(), budget.month(), budget.limits()
    budget_left = min(limits["daily_usd"] - today["cost"], limits["monthly_usd"] - month["cost"])
    creds = x_credentials(settings)
    error = cfg.last_error
    if not creds.app_only:
        error = "X is not connected (no bearer token). Connect it on the X / Twitter page."
    elif budget_left <= 0:
        error = (f"X budget reached: ${month['cost']:.2f} of the ${limits['monthly_usd']:.2f} monthly budget used "
                 f"(${today['cost']:.2f} of ${limits['daily_usd']:.2f} today). Raise X_MONTHLY_BUDGET_USD / X_DAILY_BUDGET_USD to read X again.")
    return {"enabled": cfg.enabled, "trusted_accounts": cfg.trusted_accounts or [], "last_refresh_at": cfg.last_refresh_at.isoformat() if cfg.last_refresh_at else None,
            "cooldown_seconds": cooldown_left(cfg), "error": error, "last_result": cfg.last_result or {},
            "budget": {"today_usd": today["cost"], "month_usd": month["cost"], **limits, "left_usd": round(max(0.0, budget_left), 4)},
            "posts_stored": db.query(PokemonPulsePost).count(), "connected": creds.app_only}
