"""Terminal Intelligence Engine channel (2026-11-25): the PC worker's scoped token record, the leased intel job queue and the shared AI
spend ledger. Create-only, no data change; nothing writes to these tables while settings.intel_enabled is False.

Revision ID: 0121_intel_channel
Revises: 0120_trending_text_ok
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0121_intel_channel'
down_revision: str | None = '0120_trending_text_ok'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intel_workers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("info", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_intel_workers_token_hash"),
    )
    op.create_table(
        "intel_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=10), nullable=False),
        sa.Column("route", sa.String(length=8), nullable=False),
        sa.Column("shadow", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("key", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=True),
        sa.Column("worker_id", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("offer_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("result_by", sa.String(length=8), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_intel_jobs_status", "intel_jobs", ["status", "route", "priority"])
    op.create_index("ix_intel_jobs_key", "intel_jobs", ["key"])
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cache_read", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
    )
    op.create_index("ix_ai_usage_day", "ai_usage", ["day"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_day", table_name="ai_usage")
    op.drop_table("ai_usage")
    op.drop_index("ix_intel_jobs_key", table_name="intel_jobs")
    op.drop_index("ix_intel_jobs_status", table_name="intel_jobs")
    op.drop_table("intel_jobs")
    op.drop_table("intel_workers")
