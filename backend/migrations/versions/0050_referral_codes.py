"""referral codes: several accepted codes (the BANTER link's referrer is recorded by Hyperliquid as KLGXFMNI)

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-15 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0050'
down_revision: str | None = '0049'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tb_settings', schema=None) as batch_op:
        batch_op.alter_column('referral_code', existing_type=sa.String(length=32), type_=sa.String(length=64), existing_nullable=False)
    op.execute(sa.text("UPDATE tb_settings SET referral_code = 'BANTER,KLGXFMNI' WHERE referral_code = 'BANTER'"))
    # wallets already checked: Hyperliquid's recorded code KLGXFMNI now counts (from the record we already have; no guessing)
    op.execute(sa.text("UPDATE tb_wallets SET referral_status = 'verified' WHERE referral_status = 'not_referred' "
                       "AND upper(referral_code_seen) IN ('BANTER', 'KLGXFMNI')"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE tb_settings SET referral_code = 'BANTER' WHERE referral_code = 'BANTER,KLGXFMNI'"))
    with op.batch_alter_table('tb_settings', schema=None) as batch_op:
        batch_op.alter_column('referral_code', existing_type=sa.String(length=64), type_=sa.String(length=32), existing_nullable=False)
