"""Price Chat Community Intelligence (migration 0127): members' tips (news, research, market anomalies, discoveries) from Telegram or
the Passport. An editor decides every one; nothing a member sends is ever published automatically (app/community_intel).

ci_submissions: one tip, its automatic checks (source, corroboration, figures, flags), its duplicate verdict, the editor's decision,
                the appeal, and what the Terminal later saw (the story it joined, whether it was published, first spotted).
ci_events:      the audit trail: every status change, decision and appeal, with who did it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class CiSubmission(IdMixin, TimestampMixin, Base):
    __tablename__ = "ci_submissions"
    __table_args__ = (Index("ix_ci_submissions_member", "member_id", "created_at"), Index("ix_ci_submissions_status", "status", "created_at"),
                      Index("ix_ci_submissions_url", "canonical_url"), Index("ix_ci_submissions_fwd", "forward_key"))

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    via: Mapped[str] = mapped_column(String(8), nullable=False)  # telegram | web
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # news | research | anomaly | discovery
    title: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)  # the member's own words (never copied third-party text)
    url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)  # tracking parameters and fragments removed
    host: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    forward_from: Mapped[str | None] = mapped_column(String(256))  # a forwarded Telegram message: who it came from
    forward_key: Mapped[str | None] = mapped_column(String(120))  # "<chat username>/<message id>" of a forwarded post (duplicates)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger)  # where to send the receipt and the decision
    tickers: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    disclosure: Mapped[str | None] = mapped_column(String(8))  # None: not answered yet (never reviewed until answered) | none | holds
    credit: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # "Spotted by" credit if a story is published
    status: Mapped[str] = mapped_column(String(12), default="checking", nullable=False)
    # checking | review | needs_info | accepted | merged | duplicate | rejected | withdrawn
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # source, evidence, claim, figures, flags
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # independently confirmed (sources or live data)
    verified_by: Mapped[str | None] = mapped_column(String(64))  # outlets / "live data" / "editor checked the data"
    dup: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # {kind: new|tip|story|covered, ref, ...}
    story_key: Mapped[str | None] = mapped_column(String(200))  # the Trending topic it joined (tt_topics.key)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reason: Mapped[str | None] = mapped_column(String(48))  # rejection reason key (rules.REASONS)
    editor_note: Mapped[str | None] = mapped_column(String(400))  # shown to the member
    new_info: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # a merge that added something the story lacked
    first_spotted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # a story built on it went out through the normal desk
    appeal_status: Mapped[str | None] = mapped_column(String(8))  # None | open | upheld | denied
    appeal_text: Mapped[str | None] = mapped_column(String(400))
    appeal_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    appeal_decided_by: Mapped[str | None] = mapped_column(String(64))
    appeal_decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CiEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "ci_events"
    __table_args__ = (Index("ix_ci_events_submission", "submission_id", "created_at"),)

    submission_id: Mapped[int] = mapped_column(ForeignKey("ci_submissions.id", ondelete="CASCADE"), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)  # system | member:<id> | the editor's name
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
