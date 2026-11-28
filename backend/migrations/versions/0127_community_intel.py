"""Community Intelligence: members' tips (ci_submissions) and their audit trail (ci_events). Additive only; off until
COMMUNITY_INTEL_MODE is set. Nothing here publishes: an editor decides every tip.


Revision ID: 0127_community_intel
Revises: 0126_passport2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0127_community_intel'
down_revision: str | None = '0126_passport2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "ci_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("via", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("title", sa.String(160), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.String(500), nullable=False, server_default=""),
        sa.Column("canonical_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("host", sa.String(120), nullable=False, server_default=""),
        sa.Column("forward_from", sa.String(256), nullable=True),
        sa.Column("forward_key", sa.String(120), nullable=True),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("tickers", sa.JSON(), nullable=False),
        sa.Column("disclosure", sa.String(8), nullable=True),
        sa.Column("credit", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(12), nullable=False, server_default="checking"),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verified_by", sa.String(64), nullable=True),
        sa.Column("dup", sa.JSON(), nullable=False),
        sa.Column("story_key", sa.String(200), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(48), nullable=True),
        sa.Column("editor_note", sa.String(400), nullable=True),
        sa.Column("new_info", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_spotted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("appeal_status", sa.String(8), nullable=True),
        sa.Column("appeal_text", sa.String(400), nullable=True),
        sa.Column("appeal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("appeal_decided_by", sa.String(64), nullable=True),
        sa.Column("appeal_decided_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
    )
    op.create_index("ix_ci_submissions_member", "ci_submissions", ["member_id", "created_at"])
    op.create_index("ix_ci_submissions_status", "ci_submissions", ["status", "created_at"])
    op.create_index("ix_ci_submissions_url", "ci_submissions", ["canonical_url"])
    op.create_index("ix_ci_submissions_fwd", "ci_submissions", ["forward_key"])
    op.create_table(
        "ci_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("ci_submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        *_ts(),
    )
    op.create_index("ix_ci_events_submission", "ci_events", ["submission_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ci_events_submission", table_name="ci_events")
    op.drop_table("ci_events")
    for ix in ("ix_ci_submissions_fwd", "ix_ci_submissions_url", "ix_ci_submissions_status", "ix_ci_submissions_member"):
        op.drop_index(ix, table_name="ci_submissions")
    op.drop_table("ci_submissions")
