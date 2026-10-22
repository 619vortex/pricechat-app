"""What the sitemaps list: every public page a search engine should know, with the date its content last changed.

Kinds (each split into files of PART addresses; the index at /sitemap.xml lists them all):
  pages       the fixed public pages (heads.PAGES), lastmod from the data they show where there is one
  market      the market narrowed to one set / one year (/pokemon?set=…, /pokemon?year=…: their own canonical address)
  cards       every catalogue card at its canonical address (/pokemon/cards/<id>; grades are a ?grade= view of it)
  vault       every Vault card on sale publicly (approved + eligible + available, from an enabled source)
  portfolios  collector portfolios their owner made PUBLIC, not hidden or waiting for a review, with public cards
  profiles    Kiwi Passports their member made public

Never listed: private portfolios or profiles, owner-only pages (heads.NOINDEX), admin, anything unlisted or hidden.
Built at most once an hour per process (`cache`), and kept at the edge for an hour too (edge_cache.SEO_PREFIX).
"""

import math
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.seo import heads

PART = 25_000  # addresses per file (the protocol's limit is 50,000)
TTL = 3600.0
Entry = tuple[str, date | None]  # (site path without the leading "/", last change)


def _day(v: date | datetime | None) -> date | None:
    return v.date() if isinstance(v, datetime) else v


def _latest(*vals: date | datetime | None) -> date | None:
    days = [d for d in (_day(v) for v in vals) if d is not None]
    return max(days) if days else None


def pages(db: Session) -> list[Entry]:
    from app.models.content_items import ContentItem
    from app.models.pokemon import PokemonValuationPoint
    from app.models.vault import VaultListing

    market = db.scalar(select(func.max(PokemonValuationPoint.day)))
    content = db.scalar(select(func.max(ContentItem.published_at)).where(ContentItem.website_visible.is_(True)))
    vault = db.scalar(select(func.max(VaultListing.checked_at)).where(VaultListing.review == "approved", VaultListing.eligible.is_(True)))
    out: list[Entry] = []
    for p in heads.PAGES:
        when = content if p in ("", "content") else vault if p == "pokemon/vault" else market if p.startswith("pokemon") and p != "pokemon/portfolios" else None
        out.append((p, _day(when)))
    return out


def market(db: Session) -> list[Entry]:
    from app.models.pokemon import PokemonCard, PokemonValuationPoint

    out: list[Entry] = []
    for col, path in ((PokemonCard.set_name, lambda v: f"pokemon?set={heads.encode_component(v)}"), (PokemonCard.year, lambda v: f"pokemon?year={v}")):
        changed = dict(db.execute(select(col, func.max(PokemonCard.updated_at)).group_by(col)).all())
        priced = dict(db.execute(select(col, func.max(PokemonValuationPoint.day)).join(PokemonCard, PokemonCard.id == PokemonValuationPoint.card_id)
                                 .group_by(col)).all())
        out += [(path(v), _latest(changed[v], priced.get(v))) for v in sorted(changed) if v not in (None, "")]
    return out


def cards(db: Session) -> list[Entry]:
    from app.models.pokemon import PokemonCard, PokemonValuationPoint

    priced = dict(db.execute(select(PokemonValuationPoint.card_id, func.max(PokemonValuationPoint.day)).group_by(PokemonValuationPoint.card_id)).all())
    return [(f"pokemon/cards/{i}", _latest(u, priced.get(i))) for i, u in db.execute(select(PokemonCard.id, PokemonCard.updated_at).order_by(PokemonCard.id))]


def vault(db: Session) -> list[Entry]:
    from app.db.base import utcnow
    from app.models.vault import VaultListing
    from app.vault.service import _public_q

    q = _public_q(utcnow()).with_only_columns(VaultListing.id, VaultListing.checked_at, VaultListing.updated_at).order_by(VaultListing.id)
    return [(f"pokemon/vault/{i}", _latest(c, u)) for i, c, u in db.execute(q)]


def portfolios(db: Session) -> list[Entry]:
    from app.models.members import Member
    from app.models.portfolio import PokemonHolding, PokemonPortfolio

    with_cards = select(PokemonHolding.portfolio_id).where(PokemonHolding.removed_at.is_(None), PokemonHolding.is_public.is_(True))
    q = (select(Member.username, PokemonPortfolio.last_activity_at, PokemonPortfolio.valued_at, PokemonPortfolio.updated_at)
         .join(Member, Member.id == PokemonPortfolio.member_id)
         .where(PokemonPortfolio.visibility == "public", PokemonPortfolio.review_status.in_(("ok", "featured")), Member.status == "active",
                Member.username.is_not(None), PokemonPortfolio.id.in_(with_cards))
         .order_by(PokemonPortfolio.id))
    return [(f"pokemon/portfolio/u/{heads.encode_component(u)}", _latest(a, v, up)) for u, a, v, up in db.execute(q) if u]


def profiles(db: Session) -> list[Entry]:
    from app.models.members import Member

    q = (select(Member.username, Member.updated_at).where(Member.public_profile.is_(True), Member.status == "active", Member.username.is_not(None))
         .order_by(Member.id))
    return [(f"u/{heads.encode_component(u)}", _day(up)) for u, up in db.execute(q) if u]


KINDS: dict[str, Callable[[Session], list[Entry]]] = {"pages": pages, "market": market, "vault": vault, "portfolios": portfolios,
                                                      "profiles": profiles, "cards": cards}


def entries(db: Session, kind: str, cache: dict[str, Any]) -> list[Entry]:
    hit = cache.get(kind)
    if hit is None or hit[0] < time.monotonic():
        hit = (time.monotonic() + TTL, KINDS[kind](db))
        cache[kind] = hit
    return hit[1]


def index(db: Session, cache: dict[str, Any]) -> list[tuple[str, date | None]]:
    """Every sitemap file, "<kind>-<n>", with the newest change in it. A kind with nothing public has no file."""
    out: list[tuple[str, date | None]] = []
    for kind in KINDS:
        rows = entries(db, kind, cache)
        for k in range(math.ceil(len(rows) / PART)):
            out.append((f"{kind}-{k + 1}", _latest(*(d for _, d in rows[k * PART:(k + 1) * PART]))))
    return out


def part(db: Session, name: str, cache: dict[str, Any]) -> list[Entry] | None:
    """One file's addresses, or None. "pages" / "profiles" (no number) are the first file, as older indexes named them."""
    kind, _, n = name.partition("-")
    if kind not in KINDS or not (n == "" or (n.isascii() and n.isdigit() and len(n) <= 6)):
        return None
    k = int(n or "1") - 1
    rows = entries(db, kind, cache)[k * PART:(k + 1) * PART] if k >= 0 else []
    return rows or None
