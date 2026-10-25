"""Kalshi bot (C:\\AI\\kalshi-bot, runs on the owner's machine): what it reports, read-only.

The bot holds its Kalshi key; Price Chat never does. It pushes reports with its own bearer token (only the token's SHA-256
is stored here), and the admin page shows the latest of each kind.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, UTCDateTime


class KalshiBot(Base):
    """One row: the connection. token_hash empty = not connected."""

    __tablename__ = "kalshi_bot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    token_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_bot: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # {"version", "env", "host"} of the last report


class KalshiReport(IdMixin, Base):
    """A report from the bot: kind "status" (balance, positions, fee curve, kill switch) or "discovery" (sweep stats)."""

    __tablename__ = "kalshi_reports"

    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    env: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
