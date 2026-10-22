"""Content Studio v4: each Brand Manager video keeps its media library (the authentic footage and photos the worker found or
the owner added, each with its rights record) and its editable timeline (shots, graphics, captions, narration, music)

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0073'
down_revision: str | None = '0072'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _engine(old: str, new: str) -> None:
    """Brands that saved the old default renderer move to the new one (an explicit "classic" choice is kept)."""
    import json

    bind = op.get_bind()
    for bid, raw in bind.execute(sa.text("SELECT id, strategy FROM bm_brands")).fetchall():
        st = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        cr = st.get("creative") or {}
        if cr.get("engine") == old:
            st["creative"] = {**cr, "engine": new}
            bind.execute(sa.text("UPDATE bm_brands SET strategy = CAST(:s AS JSON) WHERE id = :i").bindparams(s=json.dumps(st), i=bid))


def upgrade() -> None:
    op.add_column("bm_videos", sa.Column("media", sa.JSON(), nullable=True))
    op.add_column("bm_videos", sa.Column("timeline", sa.JSON(), nullable=True))
    _engine("motion", "editorial")


def downgrade() -> None:
    _engine("editorial", "motion")
    op.drop_column("bm_videos", "timeline")
    op.drop_column("bm_videos", "media")
