"""Pack Giveaway XP: +5 a pack, up to 100 XP a day, outside the overall automatic-XP cap

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-15 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0052'
down_revision: str | None = '0051'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE xp_rules SET xp = 5, daily_cap = 100, verification_source = 'packs', "
        "description = '+5 XP per free virtual pack opened in the Pack Giveaway, up to 100 XP a day (20 packs). Just for fun: no money, no real cards.' "
        "WHERE event_type = 'pack.open'"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE xp_rules SET xp = 2, daily_cap = 6, verification_source = 'website' WHERE event_type = 'pack.open'"))
