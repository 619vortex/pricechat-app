"""members: passwordless email sign-in (verified email on the member + single-use email codes)

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-09 06:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0025'
down_revision: str | None = '0024'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # additive only: existing members keep every row and value; email stays empty until they verify one
    op.add_column('members', sa.Column('email', sa.String(length=254), nullable=True))
    op.add_column('members', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ux_members_email', 'members', ['email'], unique=True)
    op.create_table('member_email_codes',
    sa.Column('email_hash', sa.String(length=64), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('purpose', sa.String(length=8), nullable=False),
    sa.Column('member_id', sa.Integer(), nullable=True),
    sa.Column('browser_hash', sa.String(length=64), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_member_email_codes_email', 'member_email_codes', ['email_hash', 'created_at'], unique=False)
    op.create_index('ix_member_email_codes_ip', 'member_email_codes', ['ip_hash', 'created_at'], unique=False)
    op.create_index('ix_member_email_codes_created', 'member_email_codes', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_member_email_codes_created', table_name='member_email_codes')
    op.drop_index('ix_member_email_codes_ip', table_name='member_email_codes')
    op.drop_index('ix_member_email_codes_email', table_name='member_email_codes')
    op.drop_table('member_email_codes')
    op.drop_index('ux_members_email', table_name='members')
    op.drop_column('members', 'email_verified_at')
    op.drop_column('members', 'email')
