"""kiwi brain: versioned per-member strategy configs (Teach Your Kiwi)

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-16 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0058'
down_revision: str | None = '0057'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('kb_configs',
    sa.Column('member_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('kb_configs', schema=None) as batch_op:
        batch_op.create_index('ix_kb_configs_member_version', ['member_id', 'version'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('kb_configs', schema=None) as batch_op:
        batch_op.drop_index('ix_kb_configs_member_version')
    op.drop_table('kb_configs')
