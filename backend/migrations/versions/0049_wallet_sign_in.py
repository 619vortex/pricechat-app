"""wallet sign-in: a sign-in nonce belongs to no member yet

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-15 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0049'
down_revision: str | None = '0048'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tb_nonces', schema=None) as batch_op:
        batch_op.alter_column('member_id', existing_type=sa.Integer(), nullable=True)
    # The owner opened the Earn funnel to the public with this release (2026-10-15). Once: later changes are made in
    # Admin → Trading Bot Access as usual.
    op.execute(sa.text("UPDATE tb_settings SET public_enabled = :v").bindparams(v=True))


def downgrade() -> None:
    op.execute("DELETE FROM tb_nonces WHERE member_id IS NULL")
    with op.batch_alter_table('tb_nonces', schema=None) as batch_op:
        batch_op.alter_column('member_id', existing_type=sa.Integer(), nullable=False)
