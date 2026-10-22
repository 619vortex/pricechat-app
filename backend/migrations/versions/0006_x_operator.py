"""x operator v1: dry run, interaction counters, X voice, action targets; supervised X write defaults

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-03 08:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Conservative safety ceilings (not targets): action -> (per hour, per day)
X_VOICE = ["curious", "knowledgeable", "direct", "informal", "human", "occasionally witty", "interested in value and price"]
X_AVOID = ["corporate language", "AI-sounding phrases", "fake enthusiasm", "constant promotion", "engagement bait",
           "excessive emojis", "excessive hashtags", "price predictions presented as facts"]
X_LIMITS = {"post": (1, 3), "reply": (3, 10), "like": (5, 20), "repost": (2, 5), "follow": (2, 5)}


def upgrade() -> None:
    with op.batch_alter_table('safety_policies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dry_run', sa.Boolean(), server_default=sa.true(), nullable=False))
    with op.batch_alter_table('social_relationships', schema=None) as batch_op:
        batch_op.add_column(sa.Column('our_like_count', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('our_repost_count', sa.Integer(), server_default='0', nullable=False))
    with op.batch_alter_table('brand_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('x_voice', sa.JSON(), server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('x_avoid', sa.JSON(), server_default='[]', nullable=False))
    with op.batch_alter_table('social_actions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('target_account', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('external_id', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_social_actions_target_account', ['target_account'], unique=False)

    # Supervised X operation: every X write action becomes "approval required" with conservative ceilings.
    # Dry run stays ON (server default above): nothing reaches X until the user turns it off.
    conn = op.get_bind()
    for action, (per_hour, per_day) in X_LIMITS.items():
        conn.execute(
            sa.text("UPDATE action_limits SET enabled = 1, mode = 'approval', max_per_hour = :h, max_per_day = :d "
                    "WHERE platform = 'x' AND action = :a"),
            {"h": per_hour, "d": per_day, "a": action},
        )
    conn.execute(sa.text("UPDATE safety_policies SET outbound_enabled = 1, dry_run = 1"))
    # Initial X voice (editable on AI Brain › Brand profile).
    import json

    conn.execute(sa.text("UPDATE brand_profiles SET x_voice = :v, x_avoid = :a"), {"v": json.dumps(X_VOICE), "a": json.dumps(X_AVOID)})


def downgrade() -> None:
    with op.batch_alter_table('social_actions', schema=None) as batch_op:
        batch_op.drop_index('ix_social_actions_target_account')
        batch_op.drop_column('external_id')
        batch_op.drop_column('target_account')
    with op.batch_alter_table('brand_profiles', schema=None) as batch_op:
        batch_op.drop_column('x_avoid')
        batch_op.drop_column('x_voice')
    with op.batch_alter_table('social_relationships', schema=None) as batch_op:
        batch_op.drop_column('our_repost_count')
        batch_op.drop_column('our_like_count')
    with op.batch_alter_table('safety_policies', schema=None) as batch_op:
        batch_op.drop_column('dry_run')
