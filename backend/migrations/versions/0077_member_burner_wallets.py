"""Kiwi Passport burner wallets: address + the browser-encrypted recovery phrase (ciphertext only)

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-21 18:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0077'
down_revision: str | None = '0076'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "member_burner_wallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address", sa.String(42), nullable=False),
        sa.Column("vault", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("member_id", name="uq_member_burner_wallets_member"),
    )


def downgrade() -> None:
    op.drop_table("member_burner_wallets")
