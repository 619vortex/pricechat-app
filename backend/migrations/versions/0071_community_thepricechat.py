"""The community Telegram group was renamed @cryptopricechat -> @thepricechat (same group, same chat id; the old name
is free now, so anything that addresses the group by its old @name stops working)

Updates the saved settings that still point at the old name, and only those (a value the owner changed to something
else is left alone): the signal bot's main chat, Kiwi Crew's chat, the Worker System's community chat, the website's
Telegram link, the community source's username and the Telegram mission links. Downgrade restores the old name.

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-19 18:30:00
"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0071'
down_revision: str | None = '0070'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD, NEW = "cryptopricechat", "thepricechat"
HANDLES = [("ts_settings", "main_chat"), ("member_program_settings", "crew_chat"), ("wk_settings", "community_chat")]
JSONS = [("website_settings", "links"), ("missions", "target")]


def _swap_text(v: str, old: str, new: str) -> str:
    return re.sub(rf"(t\.me/|@){re.escape(old)}\b", lambda m: m.group(1) + new, v, flags=re.I)


def _run(old: str, new: str) -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())
    for table, col in HANDLES:
        if table in tables:
            conn.execute(sa.text(f"UPDATE {table} SET {col} = :new WHERE lower({col}) = :old"), {"new": f"@{new}", "old": f"@{old}"})
    if "telegram_sources" in tables:
        conn.execute(sa.text("UPDATE telegram_sources SET username = :new WHERE lower(username) = :old"), {"new": new, "old": old})
    for table, col in JSONS:
        if table not in tables:
            continue
        for rid, raw in conn.execute(sa.text(f"SELECT id, {col} FROM {table}")).fetchall():
            text = raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else ""
            if old not in text.lower():
                continue
            data = json.loads(text) if text else {}
            fixed = json.loads(_swap_text(json.dumps(data), old, new))
            if fixed != data:
                conn.execute(sa.text(f"UPDATE {table} SET {col} = :v WHERE id = :id").bindparams(sa.bindparam("v", type_=sa.JSON)),
                             {"v": fixed, "id": rid})


def upgrade() -> None:
    _run(OLD, NEW)
    with op.batch_alter_table("ts_settings") as b:
        b.alter_column("main_chat", server_default="@thepricechat", existing_type=sa.String(64), existing_nullable=False)
    with op.batch_alter_table("member_program_settings") as b:
        b.alter_column("crew_chat", server_default="@thepricechat", existing_type=sa.String(64), existing_nullable=False)
    with op.batch_alter_table("wk_settings") as b:
        b.alter_column("community_chat", server_default="@thepricechat", existing_type=sa.String(64), existing_nullable=False)


def downgrade() -> None:
    _run(NEW, OLD)
    with op.batch_alter_table("ts_settings") as b:
        b.alter_column("main_chat", server_default="@cryptopricechat", existing_type=sa.String(64), existing_nullable=False)
    with op.batch_alter_table("member_program_settings") as b:
        b.alter_column("crew_chat", server_default="@cryptopricechat", existing_type=sa.String(64), existing_nullable=False)
    with op.batch_alter_table("wk_settings") as b:
        b.alter_column("community_chat", server_default="@cryptopricechat", existing_type=sa.String(64), existing_nullable=False)
