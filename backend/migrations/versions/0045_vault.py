"""Price Chat Vault: seller sources, curated listings, requests to buy, and the physical-card vault (architecture)

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-11 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0045'
down_revision: str | None = '0044'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "vault_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("url", sa.String(length=300), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=False),
        sa.Column("last_message", sa.Text(), nullable=False),
        sa.Column("last_counts", sa.JSON(), nullable=True),
        sa.Column("calls_day", sa.String(length=10), nullable=True),
        sa.Column("calls_today", sa.Integer(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "key"),
    )
    op.create_table(
        "vault_listings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("vault_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(length=80), nullable=False),
        sa.Column("legacy_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("shipping_cents", sa.Integer(), nullable=True),
        sa.Column("condition", sa.String(length=80), nullable=True),
        sa.Column("condition_detail", sa.Text(), nullable=True),
        sa.Column("images", sa.JSON(), nullable=False),
        sa.Column("seller_username", sa.String(length=64), nullable=True),
        sa.Column("seller_feedback_pct", sa.String(length=8), nullable=True),
        sa.Column("seller_feedback_score", sa.Integer(), nullable=True),
        sa.Column("buying_options", sa.String(length=80), nullable=True),
        sa.Column("aspects", sa.JSON(), nullable=True),
        sa.Column("availability", sa.String(length=12), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("exclude_reason", sa.String(length=120), nullable=True),
        sa.Column("pokemon", sa.String(length=60), nullable=True),
        sa.Column("set_name", sa.String(length=80), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("graded", sa.Boolean(), nullable=False),
        sa.Column("review", sa.String(length=10), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("featured", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("pokemon_override", sa.String(length=60), nullable=True),
        sa.Column("set_override", sa.String(length=80), nullable=True),
        sa.Column("year_override", sa.Integer(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id"),
    )
    op.create_index("ix_vault_listings_source_id", "vault_listings", ["source_id"])
    op.create_index("ix_vault_listings_checked_at", "vault_listings", ["checked_at"])
    op.create_index("ix_vault_listings_eligible", "vault_listings", ["eligible"])
    op.create_index("ix_vault_listings_review", "vault_listings", ["review"])
    op.create_table(
        "vault_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("vault_listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("admin_note", sa.Text(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vault_requests_listing_id", "vault_requests", ["listing_id"])
    op.create_index("ix_vault_requests_member_id", "vault_requests", ["member_id"])
    op.create_table(
        "vault_cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.String(length=20), nullable=False),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("vault_listings.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("vault_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("pokemon", sa.String(length=60), nullable=True),
        sa.Column("set_name", sa.String(length=80), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("acquisition_status", sa.String(length=16), nullable=False),
        sa.Column("acquisition_cost_cents", sa.Integer(), nullable=True),
        sa.Column("acquisition_currency", sa.String(length=3), nullable=True),
        sa.Column("acquisition_ref", sa.String(length=80), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("custody_status", sa.String(length=20), nullable=False),
        sa.Column("custody_location", sa.String(length=120), nullable=True),
        sa.Column("nft_status", sa.String(length=14), nullable=False),
        sa.Column("nft_chain", sa.String(length=16), nullable=True),
        sa.Column("nft_contract", sa.String(length=64), nullable=True),
        sa.Column("nft_token_id", sa.String(length=80), nullable=True),
        sa.Column("mint_tx", sa.String(length=80), nullable=True),
        sa.Column("owner_member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="SET NULL"), nullable=True),
        sa.Column("owner_wallet", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid"),
    )
    op.create_index("ix_vault_cards_owner_member_id", "vault_cards", ["owner_member_id"])
    op.create_table(
        "vault_card_photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("vault_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("side", sa.String(length=12), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vault_card_photos_card_id", "vault_card_photos", ["card_id"])
    op.create_table(
        "vault_condition_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("vault_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("inspector", sa.String(length=60), nullable=False),
        sa.Column("grade", sa.String(length=12), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("matches_listing", sa.Boolean(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vault_condition_records_card_id", "vault_condition_records", ["card_id"])
    op.create_table(
        "vault_redemptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("vault_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("carrier", sa.String(length=40), nullable=True),
        sa.Column("tracking", sa.String(length=80), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vault_redemptions_card_id", "vault_redemptions", ["card_id"])
    op.create_index("ix_vault_redemptions_member_id", "vault_redemptions", ["member_id"])
    op.create_table(
        "vault_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("vault_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("field", sa.String(length=20), nullable=False),
        sa.Column("old", sa.String(length=40), nullable=True),
        sa.Column("new", sa.String(length=40), nullable=True),
        sa.Column("actor", sa.String(length=60), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vault_events_card_id", "vault_events", ["card_id"])


def downgrade() -> None:
    for t in ("vault_events", "vault_redemptions", "vault_condition_records", "vault_card_photos", "vault_cards", "vault_requests",
              "vault_listings", "vault_sources"):
        op.drop_table(t)
