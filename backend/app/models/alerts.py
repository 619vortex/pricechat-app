"""Owner alerts (app/alerts.py): where the owner's private Telegram alerts go, and when each alert key was last sent."""

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime


class OwnerAlertSettings(Base):
    """One row. Admin → Settings → Background jobs → Owner alerts."""

    __tablename__ = "owner_alert_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    # the owner's numeric Telegram user id (a private chat with the bot: the owner presses Start once); "" = log only
    telegram_chat: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)


class OwnerAlert(Base):
    """One alert key's cooldown (same key at most once per 6 h) and whether a 'recovered' message is still owed."""

    __tablename__ = "owner_alerts"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_text: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    open: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # a problem was reported, not yet fixed
