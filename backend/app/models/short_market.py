"""Short Market (docs/SHORT_MARKET.md): short story videos made on the owner's own machine, downloaded by hand.

- `SmIdea`: a story worth telling, found in the sources Price Chat already reads (news feeds, market data, X, the
  community, the Pokémon market, the fact bank), with the brief (facts, numbers, sources) the story must stick to.
- `SmShort`: one video from idea to finished MP4. It is also its own render job: an AI worker (the same ones Brand
  Manager registers) claims it with a lease, reports progress and uploads the result. Nothing is ever posted anywhere.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class SmIdea(IdMixin, TimestampMixin, Base):
    __tablename__ = "sm_ideas"
    __table_args__ = (UniqueConstraint("key"),)

    key: Mapped[str] = mapped_column(String(64), nullable=False)  # the same story is found once
    category: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    brief: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # headline, facts, numbers, sources
    found_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)  # last time a source still showed it


class SmShort(IdMixin, TimestampMixin, Base):
    __tablename__ = "sm_shorts"

    idea_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    brief: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    style: Mapped[str] = mapped_column(String(24), nullable=False)
    voice: Mapped[str] = mapped_column(String(32), nullable=False)
    seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)  # the length asked for
    quality: Mapped[str] = mapped_column(String(12), default="standard", nullable=False)  # fast | standard | best (the video model's resolution)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True, nullable=False)  # queued | rendering | ready | failed
    stages: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # stage -> {status, at, note}
    script: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    video_key: Mapped[str | None] = mapped_column(String(200))  # object storage keys, never bytes in the database
    thumb_key: Mapped[str | None] = mapped_column(String(200))
    duration: Mapped[float | None] = mapped_column(Float)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(64))
    render_info: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # goes up with every remake
    # the render job
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    run_after: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("bm_workers.id", ondelete="SET NULL"))
    downloaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
