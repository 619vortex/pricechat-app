"""News Desk picture backgrounds rendered on the owner's GPU PC (app/newsdesk/render_queue.py).

Revision ID: 0108_newsdesk_render_jobs
Revises: 0107_vpn_payment_recovery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0108_newsdesk_render_jobs'
down_revision: str | None = '0107_vpn_payment_recovery'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nd_render_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("run_after", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("deadline", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.Integer()),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("image", sa.LargeBinary()),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_nd_render_jobs_event_id", "nd_render_jobs", ["event_id"])
    op.create_index("ix_nd_render_jobs_status", "nd_render_jobs", ["status", "run_after"])


def downgrade() -> None:
    op.drop_index("ix_nd_render_jobs_status", "nd_render_jobs")
    op.drop_index("ix_nd_render_jobs_event_id", "nd_render_jobs")
    op.drop_table("nd_render_jobs")
