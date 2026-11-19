"""Editorial intelligence (owner, 2026-11-19): the owner's one-click story feedback and the persistent story memory across every lane.
Create-only, no data change.

Revision ID: 0118_nd_editor
Revises: 0117_nd_validation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0118_nd_editor'
down_revision: str | None = '0117_nd_validation'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nd_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("target", sa.String(length=40), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("target", "verdict", name="uq_nd_feedback_target_verdict"),
    )
    op.create_index("ix_nd_feedback_created", "nd_feedback", ["created_at"])
    op.create_table(
        "nd_storylines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("family", sa.String(length=24), nullable=False),
        sa.Column("anchors", sa.JSON(), nullable=False),
        sa.Column("regions", sa.JSON(), nullable=False),
        sa.Column("states", sa.JSON(), nullable=False),
        sa.Column("told", sa.JSON(), nullable=False),
        sa.Column("sightings", sa.JSON(), nullable=False),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("told_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lead_by", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nd_storylines_last", "nd_storylines", ["last_at"])


def downgrade() -> None:
    op.drop_index("ix_nd_storylines_last", table_name="nd_storylines")
    op.drop_table("nd_storylines")
    op.drop_index("ix_nd_feedback_created", table_name="nd_feedback")
    op.drop_table("nd_feedback")
