"""invite bot one-click tool: personal invitations, skips and reply tracking

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-09 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0033'
down_revision: str | None = '0032'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # additive: existing invitations are untouched
    with op.batch_alter_table('invite_sends') as b:
        b.alter_column('method', existing_type=sa.String(length=6), type_=sa.String(length=8), existing_nullable=False)
        b.add_column(sa.Column('replied_at', sa.DateTime(timezone=True), nullable=True))
        b.create_index('ux_invite_one_personal_per_user', ['user_id'], unique=True, sqlite_where=sa.text("method = 'personal'"),
                       postgresql_where=sa.text("method = 'personal'"))


def downgrade() -> None:
    with op.batch_alter_table('invite_sends') as b:
        b.drop_index('ux_invite_one_personal_per_user')
        b.drop_column('replied_at')
        b.alter_column('method', existing_type=sa.String(length=8), type_=sa.String(length=6), existing_nullable=False)
