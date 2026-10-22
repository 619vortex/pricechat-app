"""trading os: more signals, posted without waiting (the owner asked for the bot to send signals, 2026-10-17)

- the 15m / 30m Kiwi Kitchen strategy goes live: READY creates real signals instead of shadow ones (intraday_live, real
  money for 15m signals, stays exactly as it was)
- new signals post to the Telegram signal group straight away (auto_publish)
- the 1h scanner looks wider: at least the top 30 markets, down to $25M of daily volume (never narrower than before)
Every switch stays in Admin → Trading → Signals → Settings; the risk / reward filter is untouched.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-17 21:00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0066'
down_revision: str | None = '0065'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, strategies, universe_size, min_volume_usd FROM ts_settings")).fetchall()
    for rid, strategies, universe, volume in rows:
        st = strategies if isinstance(strategies, dict) else json.loads(strategies or "{}")
        st = {**st, "intraday_15m_v1": True}
        stmt = sa.text("UPDATE ts_settings SET strategies = :s, auto_publish = :on, universe_size = :u, min_volume_usd = :v WHERE id = :id")
        bind.execute(stmt.bindparams(sa.bindparam("s", type_=sa.JSON())),  # typed: Postgres won't put a plain string in a JSON column
                     {"s": st, "on": True, "u": max(int(universe or 0), 30), "v": min(float(volume or 50_000_000), 25_000_000), "id": rid})


def downgrade() -> None:
    pass  # owner settings, changeable in the admin; nothing structural to undo
