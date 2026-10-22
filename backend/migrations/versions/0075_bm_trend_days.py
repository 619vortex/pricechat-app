"""Brand Manager trend scan: daily headline counts per trend word

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-21 09:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0075'
down_revision: str | None = '0074'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bm_trend_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("term", sa.String(80), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False, server_default="headline"),
        sa.Column("docs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("examples", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("day", "term", name="uq_bm_trend_days_day_term"),
    )
    op.create_index("ix_bm_trend_days_term", "bm_trend_days", ["term"])


def downgrade() -> None:
    op.drop_index("ix_bm_trend_days_term", table_name="bm_trend_days")
    op.drop_table("bm_trend_days")
