"""Price Chat Predictions: calls, votes and Telegram voters (pred_questions, pred_votes, pred_tg_users). Additive; off until
PREDICTIONS_MODE is set. XP goes to the existing reputation_events ledger under its own category ("predictor").


Revision ID: 0125_predictions
Revises: 0124_growth_originals
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0125_predictions'
down_revision: str | None = '0124_growth_originals'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "pred_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slot", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("qtype", sa.String(16), nullable=False),
        sa.Column("asset", sa.String(16), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("ref_price", sa.Float(), nullable=True),
        sa.Column("ref_day", sa.Date(), nullable=True),
        sa.Column("settle_day", sa.Date(), nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locks_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settles_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("settle_price", sa.Float(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("void_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tg_chat", sa.String(64), nullable=False, server_default=""),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=True),
        sa.Column("tg_shown", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tg_edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(40), nullable=False, server_default="system"),
        sa.Column("audit", sa.JSON(), nullable=False),
        *_ts(),
        sa.UniqueConstraint("slot"),
    )
    op.create_index("ix_pred_questions_status", "pred_questions", ["status", "settles_at"])
    op.create_table(
        "pred_votes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("pred_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("voter_key", sa.String(40), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("option", sa.String(16), nullable=False),
        sa.Column("via", sa.String(8), nullable=False),
        sa.Column("voted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(12), nullable=False, server_default="counted"),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.UniqueConstraint("question_id", "voter_key"),
    )
    op.create_index("ix_pred_votes_question_id", "pred_votes", ["question_id"])
    op.create_index("ix_pred_votes_member", "pred_votes", ["member_id"])
    op.create_index("ix_pred_votes_tg", "pred_votes", ["tg_user_id"])
    op.create_table(
        "pred_tg_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("in_group", sa.Boolean(), nullable=True),
        sa.Column("group_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dm_ok", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("reminders", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("results_dm", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        *_ts(),
        sa.UniqueConstraint("tg_user_id"),
    )


def downgrade() -> None:
    op.drop_table("pred_tg_users")
    op.drop_index("ix_pred_votes_tg", table_name="pred_votes")
    op.drop_index("ix_pred_votes_member", table_name="pred_votes")
    op.drop_index("ix_pred_votes_question_id", table_name="pred_votes")
    op.drop_table("pred_votes")
    op.drop_index("ix_pred_questions_status", table_name="pred_questions")
    op.drop_table("pred_questions")
