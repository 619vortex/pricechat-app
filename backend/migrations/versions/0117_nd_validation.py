"""News Desk validation records (owner, 2026-11-19): every candidate's editorial decision, the extraordinary-news shadow verdict, each
published picture's audit and the 30-candidate report, kept in production and shown in Terminal Admin. Create-only, no data change.

Revision ID: 0117_nd_validation
Revises: 0116_vpn_trial_guards
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0117_nd_validation'
down_revision: str | None = '0116_vpn_trial_guards'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nd_validation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=False),
        sa.Column("flag", sa.Text(), nullable=False, server_default=""),
        sa.Column("alerted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "ref", name="uq_nd_validation_kind_ref"),
    )
    op.create_index("ix_nd_validation_kind", "nd_validation", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_nd_validation_kind", table_name="nd_validation")
    op.drop_table("nd_validation")
