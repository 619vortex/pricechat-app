"""Price Chat Vault (docs/VAULT.md): a curated storefront of real vintage Japanese Pokémon cards from approved sellers.

Catalog (live today):
- `VaultSource`: one seller feed: an eBay seller read through the official Browse API, or a seller-provided catalog.
- `VaultListing`: one listing from a source, exactly as the source states it (title, price, photos, condition,
  availability). The rule-based classifier decides whether it is vintage Japanese Pokémon; an admin approves what
  becomes visible. Nothing is ever invented: a field the source doesn't give stays empty.
- `VaultRequest`: a member's REQUEST TO BUY. Not an order, not a payment, not a reservation.

Physical-card vault (architecture only: nothing is minted or claimed as owned before verified physical acquisition):
- `VaultCard`: one physical card Price Chat acquired, with its own unique id, acquisition, custody and NFT status.
- `VaultCardPhoto`, `VaultConditionRecord`, `VaultRedemption`, `VaultEvent` (append-only audit of every change).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class VaultSource(IdMixin, TimestampMixin, Base):
    """A seller feed. kind: ebay_seller (Browse API, `key` = the eBay username) | seller_catalog (data the seller gave us)."""

    __tablename__ = "vault_sources"
    __table_args__ = (UniqueConstraint("kind", "key"),)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    url: Mapped[str | None] = mapped_column(String(300))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_status: Mapped[str] = mapped_column(String(16), default="never", nullable=False)  # never | ok | partial | error | not_connected
    last_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_counts: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    calls_day: Mapped[str | None] = mapped_column(String(10))  # UTC date the counter below belongs to
    calls_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class VaultListing(IdMixin, TimestampMixin, Base):
    """One listing from a source. Prices are integer cents in the source's currency."""

    __tablename__ = "vault_listings"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    source_id: Mapped[int] = mapped_column(ForeignKey("vault_sources.id", ondelete="CASCADE"), index=True, nullable=False)
    external_id: Mapped[str] = mapped_column(String(80), nullable=False)  # eBay item id (v1|…|0) or the seller's SKU
    legacy_id: Mapped[str | None] = mapped_column(String(32))  # eBay's numeric item number
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[str | None] = mapped_column(String(500))  # the listing on the source (eBay item page)
    price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    shipping_cents: Mapped[int | None] = mapped_column(Integer)  # None = the source didn't say
    condition: Mapped[str | None] = mapped_column(String(80))  # the source's own condition text ("Ungraded", "Used"…)
    condition_detail: Mapped[str | None] = mapped_column(Text)  # the seller's condition description, verbatim
    images: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # source-hosted photo URLs
    seller_username: Mapped[str | None] = mapped_column(String(64))
    seller_feedback_pct: Mapped[str | None] = mapped_column(String(8))
    seller_feedback_score: Mapped[int | None] = mapped_column(Integer)
    buying_options: Mapped[str | None] = mapped_column(String(80))
    aspects: Mapped[dict[str, str] | None] = mapped_column(JSON)  # item specifics (Set, Language, Year Manufactured…)
    # available | sold_out | ended | unknown. `checked_at` = when the source last confirmed price + availability.
    availability: Mapped[str] = mapped_column(String(12), default="unknown", nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    details_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # item specifics fetched (one getItem per listing)
    # classifier (app/vault/classify.py): eligible | excluded, and why
    eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    exclude_reason: Mapped[str | None] = mapped_column(String(120))
    pokemon: Mapped[str | None] = mapped_column(String(60))
    set_name: Mapped[str | None] = mapped_column(String(80))
    year: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(16))
    graded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # admin curation: pending | approved | hidden. Only approved + eligible listings are public.
    review: Mapped[str] = mapped_column(String(10), default="pending", nullable=False, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    # admin corrections of the classifier (shown instead of the parsed value; the source's own text is never edited)
    pokemon_override: Mapped[str | None] = mapped_column(String(60))
    set_override: Mapped[str | None] = mapped_column(String(80))
    year_override: Mapped[int | None] = mapped_column(Integer)


class VaultRequest(IdMixin, TimestampMixin, Base):
    """REQUEST TO BUY: a member asks Price Chat to try to get this card. No payment, no reservation, no promise."""

    __tablename__ = "vault_requests"

    listing_id: Mapped[int] = mapped_column(ForeignKey("vault_listings.id", ondelete="CASCADE"), index=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    contact: Mapped[str] = mapped_column(String(120), default="", nullable=False)  # how the member wants to be reached
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # the listing as the member saw it
    price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(12), default="new", nullable=False)  # new | contacted | closed | declined | withdrawn
    admin_note: Mapped[str] = mapped_column(Text, default="", nullable=False)


class VaultCard(IdMixin, TimestampMixin, Base):
    """One PHYSICAL card. Created only when Price Chat starts acquiring a real card; `uid` is its permanent identity.

    acquisition: purchase_pending | purchased | in_transit | received | verified | rejected
    custody:     none | in_custody | redemption_pending | shipped | delivered
    nft:         not_eligible | eligible | mint_pending | minted | burned
    An NFT can only be requested once acquisition is `verified` AND custody is `in_custody` (app/vault/cards.py)."""

    __tablename__ = "vault_cards"
    __table_args__ = (UniqueConstraint("uid"),)

    uid: Mapped[str] = mapped_column(String(20), nullable=False)  # PCV-XXXX-XXXX
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("vault_listings.id", ondelete="SET NULL"))
    request_id: Mapped[int | None] = mapped_column(ForeignKey("vault_requests.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    pokemon: Mapped[str | None] = mapped_column(String(60))
    set_name: Mapped[str | None] = mapped_column(String(80))
    year: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(16), default="Japanese", nullable=False)
    acquisition_status: Mapped[str] = mapped_column(String(16), default="purchase_pending", nullable=False)
    acquisition_cost_cents: Mapped[int | None] = mapped_column(Integer)
    acquisition_currency: Mapped[str | None] = mapped_column(String(3))
    acquisition_ref: Mapped[str | None] = mapped_column(String(80))  # e.g. the eBay order number (private)
    acquired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    custody_status: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    custody_location: Mapped[str | None] = mapped_column(String(120))  # private
    nft_status: Mapped[str] = mapped_column(String(14), default="not_eligible", nullable=False)
    nft_chain: Mapped[str | None] = mapped_column(String(16))
    nft_contract: Mapped[str | None] = mapped_column(String(64))
    nft_token_id: Mapped[str | None] = mapped_column(String(80))
    mint_tx: Mapped[str | None] = mapped_column(String(80))
    owner_member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), index=True)
    owner_wallet: Mapped[str | None] = mapped_column(String(64))  # a wallet the owner proved (market_wallets)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class VaultCardPhoto(IdMixin, TimestampMixin, Base):
    """Our own photographs of the physical card (front, back, details), taken at intake."""

    __tablename__ = "vault_card_photos"

    card_id: Mapped[int] = mapped_column(ForeignKey("vault_cards.id", ondelete="CASCADE"), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(12), nullable=False)  # front | back | detail
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    taken_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class VaultConditionRecord(IdMixin, TimestampMixin, Base):
    """A condition inspection of the physical card (ours, never the seller's claim)."""

    __tablename__ = "vault_condition_records"

    card_id: Mapped[int] = mapped_column(ForeignKey("vault_cards.id", ondelete="CASCADE"), index=True, nullable=False)
    inspector: Mapped[str] = mapped_column(String(60), nullable=False)
    grade: Mapped[str] = mapped_column(String(12), nullable=False)  # NM | LP | MP | HP | DMG
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    matches_listing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class VaultRedemption(IdMixin, TimestampMixin, Base):
    """The holder asks for the physical card to be shipped. Shipping burns or locks the NFT first (cards.py)."""

    __tablename__ = "vault_redemptions"

    card_id: Mapped[int] = mapped_column(ForeignKey("vault_cards.id", ondelete="CASCADE"), index=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    address: Mapped[dict[str, str] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="requested", nullable=False)  # requested | packing | shipped | delivered | cancelled
    carrier: Mapped[str | None] = mapped_column(String(40))
    tracking: Mapped[str | None] = mapped_column(String(80))
    shipped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class VaultEvent(IdMixin, Base):
    """Append-only history of a physical card: every status change, who made it and why."""

    __tablename__ = "vault_events"

    card_id: Mapped[int] = mapped_column(ForeignKey("vault_cards.id", ondelete="CASCADE"), index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    field: Mapped[str] = mapped_column(String(20), nullable=False)  # acquisition | custody | nft | owner | note
    old: Mapped[str | None] = mapped_column(String(40))
    new: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
