"""short market: story ideas and short videos

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-13 13:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0047'
down_revision: str | None = '0046'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('sm_ideas',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('topic', sa.String(length=300), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('brief', sa.JSON(), nullable=False),
    sa.Column('found_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sm_ideas')),
    sa.UniqueConstraint('key', name=op.f('uq_sm_ideas_key'))
    )
    with op.batch_alter_table('sm_ideas', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sm_ideas_category'), ['category'], unique=False)
        batch_op.create_index(batch_op.f('ix_sm_ideas_found_at'), ['found_at'], unique=False)

    op.create_table('sm_shorts',
    sa.Column('idea_key', sa.String(length=64), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('topic', sa.String(length=300), nullable=False),
    sa.Column('brief', sa.JSON(), nullable=False),
    sa.Column('style', sa.String(length=24), nullable=False),
    sa.Column('voice', sa.String(length=32), nullable=False),
    sa.Column('seconds', sa.Integer(), nullable=False),
    sa.Column('quality', sa.String(length=12), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('stages', sa.JSON(), nullable=False),
    sa.Column('script', sa.JSON(), nullable=True),
    sa.Column('video_key', sa.String(length=200), nullable=True),
    sa.Column('thumb_key', sa.String(length=200), nullable=True),
    sa.Column('duration', sa.Float(), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('checksum', sa.String(length=64), nullable=True),
    sa.Column('render_info', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('worker_id', sa.Integer(), nullable=True),
    sa.Column('downloaded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['worker_id'], ['bm_workers.id'], name=op.f('fk_sm_shorts_worker_id_bm_workers'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sm_shorts'))
    )
    with op.batch_alter_table('sm_shorts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sm_shorts_idea_key'), ['idea_key'], unique=False)
        batch_op.create_index(batch_op.f('ix_sm_shorts_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('sm_shorts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sm_shorts_status'))
        batch_op.drop_index(batch_op.f('ix_sm_shorts_idea_key'))

    op.drop_table('sm_shorts')
    with op.batch_alter_table('sm_ideas', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sm_ideas_found_at'))
        batch_op.drop_index(batch_op.f('ix_sm_ideas_category'))

    op.drop_table('sm_ideas')
