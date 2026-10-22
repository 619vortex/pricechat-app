"""Vortex trade checks (/help <trade>): the checks, each turn's job for Vortex, and when Vortex was last seen

- ts_assist_convos: one check (the parsed trade + its FACTS); replies to its answers are follow-ups for 30 minutes
- ts_assist_jobs: one turn (the bot's message, the job Vortex claims, the fallback text, the answer)
- ts_assist_state: one row, Vortex's last long-poll

Revision ID: 0088_vortex_assist
Revises: 0087_vpn_adblock
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0088_vortex_assist'
down_revision: str | None = '0087_vpn_adblock'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ts_assist_convos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("trade", sa.JSON(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("followups", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ts_assist_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(24), nullable=False, unique=True),
        sa.Column("convo_id", sa.Integer(), sa.ForeignKey("ts_assist_convos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("reply_to", sa.BigInteger()),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("fallback", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("claims", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ts_assist_jobs_status", "ts_assist_jobs", ["status"])
    op.create_index("ix_ts_assist_jobs_msg", "ts_assist_jobs", ["chat_id", "message_id"])
    op.create_index("ix_ts_assist_jobs_user", "ts_assist_jobs", ["tg_user_id", "created_at"])
    op.create_table(
        "ts_assist_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("ts_assist_state")
    op.drop_index("ix_ts_assist_jobs_user", table_name="ts_assist_jobs")
    op.drop_index("ix_ts_assist_jobs_msg", table_name="ts_assist_jobs")
    op.drop_index("ix_ts_assist_jobs_status", table_name="ts_assist_jobs")
    op.drop_table("ts_assist_jobs")
    op.drop_table("ts_assist_convos")
