"""Growth Engine G1: the Admin "Originals" approval queue (gr_drafts). Additive. Approving a draft only marks it approved: nothing publishes.


Revision ID: 0124_growth_originals
Revises: 0123_market_memory
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0124_growth_originals'
down_revision: str | None = '0123_market_memory'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gr_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("finding_key", sa.String(160), nullable=False),
        sa.Column("format", sa.String(24), nullable=False),
        sa.Column("detector", sa.String(40), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("writer", sa.String(24), nullable=False, server_default="template"),
        sa.Column("finding", sa.JSON(), nullable=False),
        sa.Column("chart", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("calculation", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("reject_reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("writer_job_id", sa.Integer(), nullable=True),
        sa.Column("image_cache", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_gr_drafts_status", "gr_drafts", ["status", "created_at"])
    op.create_index("ix_gr_drafts_key", "gr_drafts", ["finding_key"])


def downgrade() -> None:
    op.drop_index("ix_gr_drafts_key", table_name="gr_drafts")
    op.drop_index("ix_gr_drafts_status", table_name="gr_drafts")
    op.drop_table("gr_drafts")
