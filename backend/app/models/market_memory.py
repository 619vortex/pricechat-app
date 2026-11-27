"""Growth Engine G0/G1 tables (docs/growth/2026-11-25-original-intelligence-growth-engine.md).

mm_series: Market Memory, one value per series per UTC day, kept for good (migration 0123). Series keys:
    px:<COIN>:close / px:<COIN>:vol   Hyperliquid perp daily close / USD volume
    fund:<COIN>                       Hyperliquid funding, daily mean of the hourly rate
    oi:<COIN>                         open interest USD, the day's last ts_oi_snapshots row (rolled up before its 35-day pruning)
    liq:<COIN>:long / :short          liquidated USD per side per day (ts_liquidations rollup)
    cb:<PAIR>:close                   Coinbase Exchange daily close (BTC-USD since 2015)
    fng                               alternative.me Fear & Greed
    breadth:*                         computed from the Hyperliquid universe

gr_drafts: the Admin "Originals" approval queue (migration 0124). Approving only marks a draft approved: nothing here publishes.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class MmSeries(IdMixin, Base):
    __tablename__ = "mm_series"
    __table_args__ = (UniqueConstraint("series", "day", name="uq_mm_series_series_day"), Index("ix_mm_series_series", "series"))

    series: Mapped[str] = mapped_column(String(80), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class GrDraft(IdMixin, TimestampMixin, Base):
    __tablename__ = "gr_drafts"
    __table_args__ = (Index("ix_gr_drafts_status", "status", "created_at"), Index("ix_gr_drafts_key", "finding_key"))

    finding_key: Mapped[str] = mapped_column(String(160), nullable=False)
    format: Mapped[str] = mapped_column(String(24), nullable=False)  # extremes | history | breadth | crowded | relative
    detector: Mapped[str] = mapped_column(String(40), default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", nullable=False)  # pending|approved|rejected|regenerating
    text: Mapped[str] = mapped_column(Text, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    writer: Mapped[str] = mapped_column(String(24), default="template", server_default="template", nullable=False)  # template | pc | cloud | owner
    finding: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    chart: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    calculation: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    as_of: Mapped[date | None] = mapped_column(Date)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decided_by: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    reject_reason: Mapped[str] = mapped_column(String(500), default="", server_default="", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    writer_job_id: Mapped[int | None] = mapped_column(Integer)  # the pending growth.write intel job, if any
    image_cache: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # optional: {fmt: {sha256, at}} (bytes aren't stored)
