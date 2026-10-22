"""rank rewards: the Warrior Kiwi game is now Vortex Realm (the Kiwi Arena is gone, 2026-10-17)

- the Warrior Kiwi reward card says Vortex Realm (only if it still has the old default text: an edited card is kept)
- the Kiwi Arena XP rule is switched off (nothing awards it any more; its history stays in the ledger)
The arena_runs / arena_heroes tables are kept as history.

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-17 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0067'
down_revision: str | None = '0066'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TITLE = "KIWI ARENA"
OLD_DESC = "Grab your sword and survive the monster waves. Defeating monsters earns Kiwi Passport XP (daily limits apply)."
NEW_TITLE = "VORTEX REALM"
NEW_DESC = "A 3D fantasy RPG in your browser: create a hero, fight monsters, take on quests and collect loot. Play it in your Passport."


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE rank_rewards SET title = :t WHERE rank_key = 'warrior' AND title = :old"), {"t": NEW_TITLE, "old": OLD_TITLE})
    bind.execute(sa.text("UPDATE rank_rewards SET description = :d WHERE rank_key = 'warrior' AND description = :old"),
                 {"d": NEW_DESC, "old": OLD_DESC})
    bind.execute(sa.text("UPDATE xp_rules SET enabled = :off WHERE event_type = 'arena.run'"), {"off": False})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE rank_rewards SET title = :t WHERE rank_key = 'warrior' AND title = :new"), {"t": OLD_TITLE, "new": NEW_TITLE})
    bind.execute(sa.text("UPDATE rank_rewards SET description = :d WHERE rank_key = 'warrior' AND description = :new"),
                 {"d": OLD_DESC, "new": NEW_DESC})
