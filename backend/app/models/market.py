"""Card Marketplace (docs/MARKETPLACE.md): graded Pokémon cards bought and sold for USDT.

One `MarketItem` per physical slab (unique company+cert, unique vault inventory id). A portfolio holding never becomes a
listing by itself: a listing passes verification and confirmed custody before it can go live. Ownership is an internal
ledger (`MarketTransfer`), money is accounted in `MarketLedgerEntry` with one idempotency key per entry, and every
state change is a compare-and-swap on `version` guarded by partial unique indexes (one open listing per item, one live
order per listing). Sandbox rows (simulation) are never shown publicly and never count as marketplace volume.

Amounts are integer USDT cents (1 USDT = 100); on-chain amounts are stored as base-unit strings (USDT has 6 decimals).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime

OPEN_LISTING = ("draft", "awaiting_verification", "awaiting_custody", "verified", "active", "reserved")
LIVE_ORDER = ("awaiting_payment", "paid", "settling")
_open = "status IN ('draft','awaiting_verification','awaiting_custody','verified','active','reserved')"
_live = "status IN ('awaiting_payment','paid','settling')"


class MarketSeller(IdMixin, TimestampMixin, Base):
    """A member's marketplace standing. Level 0 = unverified, 1 = identity checked, 2 = enhanced (high-value listings)."""

    __tablename__ = "market_sellers"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="unverified", nullable=False)  # unverified | pending | verified | suspended
    level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    country: Mapped[str | None] = mapped_column(String(2))  # self-declared until a KYC provider confirms it
    kyc_ref: Mapped[str | None] = mapped_column(String(64))  # the KYC provider's reference, never documents
    sandbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # may use the trading simulation
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class MarketItem(IdMixin, TimestampMixin, Base):
    """One physical graded slab. Its owner is the marketplace's record of title (Option A ledger)."""

    __tablename__ = "market_items"
    __table_args__ = (UniqueConstraint("cert_key"), UniqueConstraint("vault_inventory_id"))

    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="RESTRICT"), index=True, nullable=False)
    grading_company: Mapped[str] = mapped_column(String(8), nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    cert_number: Mapped[str] = mapped_column(String(32), nullable=False)
    cert_key: Mapped[str] = mapped_column(String(48), nullable=False)  # COMPANY:cert (sandbox: SBX:...) -> duplicate-cert detection
    owner_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), index=True, nullable=False)
    origin_holding_id: Mapped[int | None] = mapped_column(ForeignKey("pokemon_holdings.id", ondelete="SET NULL"))
    verification_status: Mapped[str] = mapped_column(String(12), default="unverified", nullable=False)  # unverified | pending | verified | rejected
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # self_custody | submitted | received | vaulted | release_requested | released | redeemed
    custody_status: Mapped[str] = mapped_column(String(20), default="self_custody", nullable=False)
    vault_partner: Mapped[str | None] = mapped_column(String(48))
    vault_inventory_id: Mapped[str | None] = mapped_column(String(64))
    storage_location: Mapped[str | None] = mapped_column(String(120))  # private: admin / vault only
    custody_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    insurance_status: Mapped[str] = mapped_column(String(12), default="none", nullable=False)  # none | confirmed
    insurance_note: Mapped[str] = mapped_column(Text, default="", nullable=False)  # coverage + limitations, as the partner states
    redeemed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # RAW cards (grading_company "RAW", grade NM/LP/MP/HP/DMG, no cert): the AI condition estimate the listing was made with
    condition_low: Mapped[float | None] = mapped_column(Float)
    condition_high: Mapped[float | None] = mapped_column(Float)
    condition_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class MarketConditionVote(IdMixin, TimestampMixin, Base):
    """A member's own condition grade (1-10) for a RAW listing: one per member, changeable. Never the seller's own."""

    __tablename__ = "market_condition_votes"
    __table_args__ = (UniqueConstraint("listing_id", "member_id"),)

    listing_id: Mapped[int] = mapped_column(ForeignKey("market_listings.id", ondelete="CASCADE"), index=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    estimate: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-10
    note: Mapped[str] = mapped_column(String(280), default="", nullable=False)


class MarketComment(IdMixin, TimestampMixin, Base):
    """Public questions and remarks on a listing (one reply level). Soft-deleted by the author or an admin."""

    __tablename__ = "market_comments"

    listing_id: Mapped[int] = mapped_column(ForeignKey("market_listings.id", ondelete="CASCADE"), index=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("market_comments.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class MarketPhoto(IdMixin, TimestampMixin, Base):
    """A photo of the slab in the private bucket (front, back, label; vault intake photos are marked as such)."""

    __tablename__ = "market_photos"

    item_id: Mapped[int] = mapped_column(ForeignKey("market_items.id", ondelete="CASCADE"), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # front | back | label | vault
    source: Mapped[str] = mapped_column(String(8), default="seller", nullable=False)  # seller | vault | sandbox
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    thumb_key: Mapped[str] = mapped_column(String(160), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)  # the same photo on two listings is a fraud flag


class MarketListing(IdMixin, TimestampMixin, Base):
    __tablename__ = "market_listings"
    __table_args__ = (
        Index("ux_market_listing_open_item", "item_id", unique=True, sqlite_where=text(_open), postgresql_where=text(_open)),
        Index("ix_market_listings_browse", "status", "is_sandbox", "price_cents"),
    )

    item_id: Mapped[int] = mapped_column(ForeignKey("market_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    seller_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    price_cents: Mapped[int | None] = mapped_column(BigInteger)  # USDT cents
    ownership_evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)  # private: seller + admin
    seller_note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    review_note: Mapped[str] = mapped_column(Text, default="", nullable=False)  # admin's reason (shown to the seller on rejection)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # automatic risk flags
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    listed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    sold_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class MarketListingEvent(IdMixin, Base):
    """Listing history (shown publicly without private fields)."""

    __tablename__ = "market_listing_events"

    listing_id: Mapped[int] = mapped_column(ForeignKey("market_listings.id", ondelete="CASCADE"), index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)  # seller | buyer | admin | vault | system
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    price_cents: Mapped[int | None] = mapped_column(BigInteger)


class MarketWatch(IdMixin, TimestampMixin, Base):
    __tablename__ = "market_watch"
    __table_args__ = (UniqueConstraint("member_id", "listing_id"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    listing_id: Mapped[int] = mapped_column(ForeignKey("market_listings.id", ondelete="CASCADE"), index=True, nullable=False)


class MarketOffer(IdMixin, TimestampMixin, Base):
    __tablename__ = "market_offers"

    listing_id: Mapped[int] = mapped_column(ForeignKey("market_listings.id", ondelete="CASCADE"), index=True, nullable=False)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)  # pending | accepted | rejected | cancelled | expired
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    order_id: Mapped[int | None] = mapped_column(Integer)


class MarketOrder(IdMixin, TimestampMixin, Base):
    __tablename__ = "market_orders"
    __table_args__ = (
        Index("ux_market_order_live_listing", "listing_id", unique=True, sqlite_where=text(_live), postgresql_where=text(_live)),
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("ref"),
    )

    ref: Mapped[str] = mapped_column(String(16), nullable=False)  # public order reference (also the payment memo)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)  # buyer:<client key>: a retried click is the same order
    listing_id: Mapped[int] = mapped_column(ForeignKey("market_listings.id", ondelete="RESTRICT"), index=True, nullable=False)
    item_id: Mapped[int] = mapped_column(ForeignKey("market_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), index=True, nullable=False)
    seller_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), index=True, nullable=False)
    offer_id: Mapped[int | None] = mapped_column(Integer)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buyer_fee_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    seller_fee_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)  # what the buyer pays
    proceeds_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)  # what the seller receives
    network: Mapped[str] = mapped_column(String(12), nullable=False)  # tron | solana | ethereum
    pay_to: Mapped[str] = mapped_column(String(80), nullable=False)  # per-order deposit address (partner-issued; simulated today)
    amount_units: Mapped[str] = mapped_column(String(40), nullable=False)  # exact base units expected
    status: Mapped[str] = mapped_column(String(16), default="awaiting_payment", nullable=False)
    status_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class MarketPayment(IdMixin, TimestampMixin, Base):
    """An on-chain (or simulated) transfer, independently verified. A transaction can pay at most one order."""

    __tablename__ = "market_payments"
    __table_args__ = (UniqueConstraint("network", "tx_ref"),)

    order_id: Mapped[int] = mapped_column(ForeignKey("market_orders.id", ondelete="RESTRICT"), index=True, nullable=False)
    network: Mapped[str] = mapped_column(String(12), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False)  # the verified USDT contract / mint
    tx_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    from_address: Mapped[str | None] = mapped_column(String(80))
    to_address: Mapped[str] = mapped_column(String(80), nullable=False)
    amount_units: Mapped[str] = mapped_column(String(40), nullable=False)
    confirmations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # confirmed | rejected | pending
    problem: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class MarketLedgerEntry(IdMixin, Base):
    """Money accounting (double-entry style amounts per order). `key` makes every entry idempotent."""

    __tablename__ = "market_ledger"
    __table_args__ = (UniqueConstraint("key"),)

    key: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. order:12:buyer_paid
    order_id: Mapped[int | None] = mapped_column(ForeignKey("market_orders.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # buyer_paid | platform_fee | seller_payable | seller_paid | refund_due | refunded
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    member_id: Mapped[int | None] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MarketTransfer(IdMixin, Base):
    """The ownership ledger: who held the item from when, and why."""

    __tablename__ = "market_transfers"

    item_id: Mapped[int] = mapped_column(ForeignKey("market_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    from_member_id: Mapped[int | None] = mapped_column(Integer)
    to_member_id: Mapped[int | None] = mapped_column(Integer)
    order_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # intake | sale | redeemed | admin
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MarketRedemption(IdMixin, TimestampMixin, Base):
    __tablename__ = "market_redemptions"
    __table_args__ = (
        Index("ux_market_redemption_open_item", "item_id", unique=True,
              sqlite_where=text("status IN ('requested','approved','released','shipped')"),
              postgresql_where=text("status IN ('requested','approved','released','shipped')")),
    )

    item_id: Mapped[int] = mapped_column(ForeignKey("market_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), index=True, nullable=False)
    # requested | approved | released | shipped | delivered | returned | lost | cancelled
    status: Mapped[str] = mapped_column(String(12), default="requested", nullable=False)
    address_enc: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted (Fernet); never returned to anyone but the owner/admin
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    fees: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    carrier: Mapped[str | None] = mapped_column(String(32))
    tracking: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)


class MarketDispute(IdMixin, TimestampMixin, Base):
    __tablename__ = "market_disputes"

    order_id: Mapped[int | None] = mapped_column(Integer, index=True)
    redemption_id: Mapped[int | None] = mapped_column(Integer, index=True)
    opened_by: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="open", nullable=False)  # open | resolved | rejected
    resolution: Mapped[str] = mapped_column(Text, default="", nullable=False)


class MarketReport(IdMixin, TimestampMixin, Base):
    """Fraud / suspicious-listing flags: from members, or automatic (duplicate cert, reused photo, price far below market)."""

    __tablename__ = "market_reports"

    listing_id: Mapped[int] = mapped_column(ForeignKey("market_listings.id", ondelete="CASCADE"), index=True, nullable=False)
    reporter_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    auto: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="open", nullable=False)  # open | dismissed | actioned


class MarketAudit(IdMixin, Base):
    """Every sensitive action (admin and system), append-only."""

    __tablename__ = "market_audit"

    at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    actor: Mapped[str] = mapped_column(String(40), nullable=False)  # admin | member:<id> | system
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    target: Mapped[str] = mapped_column(String(40), nullable=False)  # listing:12, order:3 ...
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MarketWallet(IdMixin, TimestampMixin, Base):
    """A crypto wallet a member PROVED is theirs by signing a one-time message (EIP-191). Only the public address is
    stored: never a key or seed phrase. One address belongs to one member. Seller payouts go to the `payout` wallet."""

    __tablename__ = "market_wallets"
    __table_args__ = (UniqueConstraint("chain", "address"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(12), nullable=False)  # evm (Ethereum, BNB Chain… same address everywhere)
    address: Mapped[str] = mapped_column(String(64), nullable=False)  # EIP-55 checksummed
    wallet: Mapped[str] = mapped_column(String(40), default="", nullable=False)  # what the member used: "OKX Wallet", "MetaMask"…
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    payout: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)


class MarketWalletChallenge(IdMixin, Base):
    """The one-time message a member signs to prove a wallet. Single use, expires after a few minutes."""

    __tablename__ = "market_wallet_challenges"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
