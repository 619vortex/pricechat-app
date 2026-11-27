"""Terminal Intelligence Engine, Phase 0: the shared semantic gate's shadow record (intel_shadow). Additive; nothing reads it to decide.


Revision ID: 0122_intel_shadow_gate
Revises: 0121_intel_channel
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0122_intel_shadow_gate'
down_revision: str | None = '0121_intel_channel'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intel_shadow",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("lane", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("rules_ok", sa.Boolean(), nullable=False),
        sa.Column("semantic_ok", sa.Boolean(), nullable=False),
        sa.Column("agree", sa.Boolean(), nullable=False),
        sa.Column("tier", sa.String(12), nullable=False, server_default=""),
        sa.Column("source", sa.String(16), nullable=False, server_default="rules"),
        sa.Column("seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verdict", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("lane", "ref", name="uq_intel_shadow_lane_ref"),
    )
    op.create_index("ix_intel_shadow_seen", "intel_shadow", ["last_seen"])


def downgrade() -> None:
    op.drop_index("ix_intel_shadow_seen", table_name="intel_shadow")
    op.drop_table("intel_shadow")
