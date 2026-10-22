"""The owner's dashboard sessions (admin login). Only a hash of each session token is stored."""

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class AdminSession(IdMixin, TimestampMixin, Base):
    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # sha256 of the cookie value
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
