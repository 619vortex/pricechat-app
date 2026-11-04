"""One-time sign-in codes for the Kiwi VPN desktop app. Only hashes and the verifier's challenge are stored."""

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class MemberDesktopCode(IdMixin, TimestampMixin, Base):
    __tablename__ = "member_desktop_codes"

    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    challenge: Mapped[str] = mapped_column(String(43), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
