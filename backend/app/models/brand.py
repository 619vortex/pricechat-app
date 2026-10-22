from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class BrandProfile(IdMixin, TimestampMixin, Base):
    """The persistent, editable definition of the brand. One per agent."""

    __tablename__ = "brand_profiles"

    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    brand_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mission: Mapped[str] = mapped_column(Text, default="", nullable=False)
    audience: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tone: Mapped[str] = mapped_column(Text, default="", nullable=False)
    writing_style: Mapped[str] = mapped_column(Text, default="", nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="", nullable=False)

    products_services: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    topics_of_interest: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    topics_to_avoid: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    terminology: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_claims: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    restricted_claims: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    social_objectives: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # X voice: how Price Chat should (and should not) sound on X. Used for replies and posts.
    x_voice: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]", nullable=False)
    x_avoid: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]", nullable=False)


BRAND_TEXT_FIELDS = (
    "brand_name",
    "description",
    "mission",
    "audience",
    "tone",
    "writing_style",
    "personality",
)
BRAND_LIST_FIELDS = (
    "products_services",
    "topics_of_interest",
    "topics_to_avoid",
    "terminology",
    "allowed_claims",
    "restricted_claims",
    "social_objectives",
)
