"""Kiwi VPN is a paid product, not a rank perk: its entitlement no longer unlocks by rank (vpn_settings.min_rank stays, unused)

Revision ID: 0096_kiwi_vpn_not_rank_based
Revises: 0095_tr_positions_signal_index
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0096_kiwi_vpn_not_rank_based'
down_revision: str | None = '0095_tr_positions_signal_index'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE entitlements SET required_rank = NULL, "
        "description = 'A private WireGuard VPN (up to 3 devices), $9.99 a month. Not unlocked by rank.' WHERE key = 'kiwi_vpn'"))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE entitlements SET required_rank = 5, "
        "description = 'A private WireGuard VPN (up to 3 devices). The rank follows Admin \u203a Kiwi VPN''s minimum rank.' WHERE key = 'kiwi_vpn'"))
