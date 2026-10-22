"""exchange settings (auto-trader switch and state)

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-10 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0042'
down_revision: str | None = '0041'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ex_settings",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("ex_settings")
