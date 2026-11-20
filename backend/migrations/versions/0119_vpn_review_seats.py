"""Kiwi VPN store-review seats (2026-11-20): every review password sign-in gets its own member (app/vpn/reviewer.py).

Adds members.review_seat. The review account bound so far (vpn_settings.reviewer_member_id) becomes the first seat: its email
is cleared, so no email-code sign-in can reach it, and every session it holds other than the scoped "kr." browser session and
the Kiwi VPN app's "kd." session is deleted here (members.service.member_for_token also refuses them from now on).

Revision ID: 0119_vpn_review_seats
Revises: 0118_nd_editor
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0119_vpn_review_seats'
down_revision: str | None = '0118_nd_editor'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("members", sa.Column("review_seat", sa.Boolean(), nullable=False, server_default=sa.false()))
    conn = op.get_bind()
    bound = conn.execute(sa.text("SELECT reviewer_member_id FROM vpn_settings WHERE id = 1")).scalar()
    if bound is None or conn.execute(sa.text("SELECT id FROM members WHERE id = :id"), {"id": bound}).scalar() is None:
        return
    conn.execute(sa.text("UPDATE members SET review_seat = :yes, email = NULL, email_verified_at = NULL WHERE id = :id"), {"yes": True, "id": bound})
    conn.execute(sa.text("DELETE FROM member_sessions WHERE member_id = :id AND (kind IS NULL OR kind NOT IN ('reviewer', 'desktop'))"), {"id": bound})


def downgrade() -> None:
    # the cleared email and the deleted sessions aren't restored
    with op.batch_alter_table("members") as batch:
        batch.drop_column("review_seat")
