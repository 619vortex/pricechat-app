"""Public website settings: which homepage sections are switched on, and the public links.

A single row (id 1). Content itself lives in content_items; copy lives in the frontend (websiteData.ts).
"""

from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class WebsiteSettings(IdMixin, TimestampMixin, Base):
    __tablename__ = "website_settings"

    sections: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict, nullable=False)  # missing key = on
    links: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # telegram/x/youtube URL overrides
    site_url: Mapped[str | None] = mapped_column(String(256))  # public origin for canonical/OpenGraph; NULL = not decided yet
