"""Growth Engine G0: Market Memory (mm_series), one value per series per UTC day, kept for good. Additive.


Revision ID: 0123_market_memory
Revises: 0122_intel_shadow_gate
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0123_market_memory'
down_revision: str | None = '0122_intel_shadow_gate'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mm_series",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("series", sa.String(80), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("series", "day", name="uq_mm_series_series_day"),
    )
    op.create_index("ix_mm_series_series", "mm_series", ["series"])


def downgrade() -> None:
    op.drop_index("ix_mm_series_series", table_name="mm_series")
    op.drop_table("mm_series")
