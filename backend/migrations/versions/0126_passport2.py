"""Kiwi Passport 2.0: the member's public identity (bio, title, sections, invite link, invitation answered), badge collections
(family, tier, title, sort) and numbered / featured badges. Additive only: no existing value changes; off until PASSPORT2_MODE is set.


Revision ID: 0126_passport2
Revises: 0125_predictions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0126_passport2'
down_revision: str | None = '0125_predictions'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("members") as t:
        t.add_column(sa.Column("bio", sa.String(80), nullable=True))
        t.add_column(sa.Column("title_badge", sa.String(32), nullable=True))
        t.add_column(sa.Column("profile_sections", sa.JSON(), nullable=True))
        t.add_column(sa.Column("profile_ref_link", sa.Boolean(), server_default=sa.false(), nullable=False))
        t.add_column(sa.Column("profile_invited_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("badges") as t:
        t.add_column(sa.Column("family", sa.String(16), nullable=True))
        t.add_column(sa.Column("tier", sa.String(8), nullable=True))
        t.add_column(sa.Column("title", sa.String(48), nullable=True))
        t.add_column(sa.Column("sort", sa.Integer(), server_default="0", nullable=False))
    with op.batch_alter_table("member_badges") as t:
        t.add_column(sa.Column("serial", sa.Integer(), nullable=True))
        t.add_column(sa.Column("featured_slot", sa.Integer(), nullable=True))
    op.create_index("ux_member_badges_serial", "member_badges", ["badge_id", "serial"], unique=True,
                    sqlite_where=sa.text("serial IS NOT NULL"), postgresql_where=sa.text("serial IS NOT NULL"))
    op.create_index("ux_member_badges_featured", "member_badges", ["member_id", "featured_slot"], unique=True,
                    sqlite_where=sa.text("featured_slot IS NOT NULL"), postgresql_where=sa.text("featured_slot IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ux_member_badges_featured", table_name="member_badges")
    op.drop_index("ux_member_badges_serial", table_name="member_badges")
    with op.batch_alter_table("member_badges") as t:
        t.drop_column("featured_slot")
        t.drop_column("serial")
    with op.batch_alter_table("badges") as t:
        for c in ("sort", "title", "tier", "family"):
            t.drop_column(c)
    with op.batch_alter_table("members") as t:
        for c in ("profile_invited_at", "profile_ref_link", "profile_sections", "title_badge", "bio"):
            t.drop_column(c)
