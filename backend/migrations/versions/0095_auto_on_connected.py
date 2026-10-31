"""Trading: every connected LIVE account follows the bot (AUTO on, no end date), the owner's call 2026-10-31.

Connected = LIVE mode, LIVE enabled and an approved mainnet API wallet. Accounts the member or a safety check paused (STOP NEW
TRADES) stay paused: AUTO only enters once they resume.

Revision ID: 0095_auto_on_connected
Revises: 0094_kalshi_links
"""

from collections.abc import Sequence

from alembic import op


revision: str = '0095_auto_on_connected'
down_revision: str | None = '0094_kalshi_links'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE tr_accounts SET auto_enabled = 1, auto_until = NULL WHERE mode = 'LIVE' AND live_enabled_at IS NOT NULL "
               "AND mainnet_approved_at IS NOT NULL AND mainnet_agent_key IS NOT NULL")


def downgrade() -> None:
    pass  # who had AUTO on before isn't recorded here; members turn it off themselves
