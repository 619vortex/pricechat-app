"""Trading bot open to every member (owner's request, 2026-10-19: "remove all restrictions with trading bot")

The required Kiwi rank drops to 1 (every member has it) and the minimum deposit to $0. The private signal group now
approves every join request (app/tradingbot/service.on_update) and LIVE no longer needs the referral (execution.eligibility).
The risk engine is unchanged. Both values stay editable in Admin → Trading Bot; downgrade restores 4 and $10.

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-19 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0072'
down_revision: str | None = '0071'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set(rank: int, deposit: float) -> None:
    op.execute(sa.text("UPDATE tb_settings SET min_rank_level = :r, min_deposit_usd = :d").bindparams(r=rank, d=deposit))
    with op.batch_alter_table("tb_settings") as b:
        b.alter_column("min_rank_level", server_default=str(rank), existing_type=sa.Integer(), existing_nullable=False)
        b.alter_column("min_deposit_usd", server_default=f"{deposit:g}", existing_type=sa.Float(), existing_nullable=False)


def upgrade() -> None:
    _set(1, 0.0)


def downgrade() -> None:
    _set(4, 10.0)
