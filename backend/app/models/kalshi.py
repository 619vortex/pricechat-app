"""Kalshi: the owner's bot reports (C:\\AI\\kalshi-bot, runs on the owner's machine) and members' read-only API links.

The bot holds its own Kalshi key; it pushes reports with its own bearer token (only the token's SHA-256 is stored here).
Members (Earn › Kalshi) link their own Kalshi API key: kept sealed (app.security.secrets) and used only for read-only
portfolio calls. Nothing here places, changes or cancels an order.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, false
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
    members_open: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # Earn › Kalshi for all


class KalshiReport(IdMixin, Base):
    """A report from the bot: kind "status" (balance, positions, fee curve, kill switch) or "discovery" (sweep stats)."""

    __tablename__ = "kalshi_reports"

    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    env: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class KalshiLink(IdMixin, Base):
    """A member's Kalshi API key (Earn › Kalshi). The private key is sealed; only portfolio reads use it."""

    __tablename__ = "kalshi_links"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)  # production | demo
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    key_enc: Mapped[str] = mapped_column(Text, nullable=False)  # sealed PEM (enc() from tradingos/exchange.py; registered in rekey.py)
    key_type: Mapped[str] = mapped_column(String(16), nullable=False)  # ed25519 | rsa
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
