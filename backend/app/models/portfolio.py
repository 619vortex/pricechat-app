"""Pokémon Portfolio: a member's collection of exact card printings (raw or professionally graded), its valuations and
history, photos, and the lightweight community around public portfolios.

Every holding points at the canonical catalogue card (pokemon_cards.id), never at a name. Values are ESTIMATES from the
market's own data (graded: the exact grade's price; raw: real ungraded-condition prices), each with its source, time and
confidence. Portfolio values are never added to the market's capitalisation and ownership is never trading volume.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class PokemonPortfolio(IdMixin, TimestampMixin, Base):
    """One per member (the Kiwi Passport identity). Private until the owner makes it public."""

    __tablename__ = "pokemon_portfolios"

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), unique=True, nullable=False)
    visibility: Mapped[str] = mapped_column(String(8), default="private", server_default="private", nullable=False)  # private | public
    show_value: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)  # value shown when public
    show_purchase: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # purchase prices public
    comments_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    bio: Mapped[str] = mapped_column(String(280), default="", server_default="", nullable=False)
    featured: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)  # holding ids, owner's choice (max 6)
    # cached totals (the valuation service keeps them current; pages never price every card on load)
    value_cents: Mapped[int | None] = mapped_column(BigInteger)
    public_value_cents: Mapped[int | None] = mapped_column(BigInteger)  # public holdings only
    priced_holdings: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    unpriced_holdings: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    valued_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # leaderboard protection: an unusually valuable or fast-growing self-reported collection waits for a review
    review_status: Mapped[str] = mapped_column(String(12), default="ok", server_default="ok", nullable=False)  # ok | review | hidden
    review_note: Mapped[str | None] = mapped_column(Text)
    last_activity_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)


class PokemonHolding(IdMixin, TimestampMixin, Base):
    """A card in a portfolio: exact printing, raw or graded, quantity. Removing a holding keeps the row (removed_at) so
    the portfolio's history can tell market moves from cards added or removed."""

    __tablename__ = "pokemon_holdings"
    __table_args__ = (Index("ix_pokemon_holdings_portfolio", "portfolio_id", "removed_at"), Index("ix_pokemon_holdings_card", "card_id"))

    portfolio_id: Mapped[int] = mapped_column(ForeignKey("pokemon_portfolios.id", ondelete="CASCADE"), nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # raw | graded
    grading_company: Mapped[str | None] = mapped_column(String(8))  # PSA | BGS | CGC (graded only)
    grade: Mapped[str | None] = mapped_column(String(8))  # official numeric grade (graded only)
    cert_number: Mapped[str | None] = mapped_column(String(32))  # as entered: NOT proof of ownership or authenticity
    cert_status: Mapped[str] = mapped_column(String(12), default="unchecked", server_default="unchecked", nullable=False)  # unchecked | found | mismatch | not_found
    # raw only: estimated condition as a numeric "grade-equivalent" range (never an official grade), and where it came from
    condition_low: Mapped[float | None] = mapped_column(Float)
    condition_high: Mapped[float | None] = mapped_column(Float)
    condition_source: Mapped[str | None] = mapped_column(String(12))  # owner | ai
    condition_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # the AI assistant's observations
    community_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    purchase_price_cents: Mapped[int | None] = mapped_column(BigInteger)  # private unless the owner opts in
    purchase_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(String(500), default="", server_default="", nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)  # within a public portfolio
    source: Mapped[str] = mapped_column(String(8), default="manual", server_default="manual", nullable=False)  # manual | scan
    verification: Mapped[str] = mapped_column(String(16), default="self_reported", server_default="self_reported", nullable=False)
    # latest valuation (per copy): point estimate and range, with source / confidence / time
    value_cents: Mapped[int | None] = mapped_column(BigInteger)
    value_low_cents: Mapped[int | None] = mapped_column(BigInteger)
    value_high_cents: Mapped[int | None] = mapped_column(BigInteger)
    value_source: Mapped[str | None] = mapped_column(String(24))  # sales | scrydex | scrydex_raw | none
    value_confidence: Mapped[str | None] = mapped_column(String(8))  # high | medium | low
    value_note: Mapped[str | None] = mapped_column(Text)
    # why the value is what it is (valuation.REASONS): priced | comparable | grade_equivalent | grade_price_unavailable | ...
    value_reason: Mapped[str | None] = mapped_column(String(32))
    prices_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # last on-demand provider fetch for this card
    valued_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class PokemonHoldingEvent(IdMixin, Base):
    """What changed in a portfolio and when, with the value at that moment: the basis for "market move" vs "cards added"."""

    __tablename__ = "pokemon_holding_events"
    __table_args__ = (Index("ix_pokemon_holding_events_portfolio_at", "portfolio_id", "at"),)

    portfolio_id: Mapped[int] = mapped_column(ForeignKey("pokemon_portfolios.id", ondelete="CASCADE"), nullable=False)
    holding_id: Mapped[int] = mapped_column(ForeignKey("pokemon_holdings.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # add | remove | quantity | grade
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)  # copies added (+) or removed (-)
    value_delta_cents: Mapped[int | None] = mapped_column(BigInteger)  # estimated value added / removed at that moment (None: unpriced)


class PokemonHoldingPhoto(IdMixin, TimestampMixin, Base):
    """An owner's photo in the private bucket (never on the app's disk). Served only through the API, after access checks."""

    __tablename__ = "pokemon_holding_photos"

    holding_id: Mapped[int] = mapped_column(ForeignKey("pokemon_holdings.id", ondelete="CASCADE"), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # front | back | closeup
    key: Mapped[str] = mapped_column(String(160), nullable=False)  # object key of the processed image
    thumb_key: Mapped[str] = mapped_column(String(160), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class PokemonPortfolioSnapshot(IdMixin, Base):
    """The portfolio's estimated value at the end of a day, recorded on that day (never back-filled). `flows_cents` is the
    value of copies added minus removed since the previous snapshot, so value change = market move + flows."""

    __tablename__ = "pokemon_portfolio_snapshots"
    __table_args__ = (UniqueConstraint("portfolio_id", "day"),)

    portfolio_id: Mapped[int] = mapped_column(ForeignKey("pokemon_portfolios.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    taken_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    value_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    flows_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    copies: Mapped[int] = mapped_column(Integer, nullable=False)
    priced_copies: Mapped[int] = mapped_column(Integer, nullable=False)


class PokemonPortfolioComment(IdMixin, TimestampMixin, Base):
    __tablename__ = "pokemon_portfolio_comments"
    __table_args__ = (Index("ix_pokemon_portfolio_comments_portfolio", "portfolio_id", "created_at"),)

    portfolio_id: Mapped[int] = mapped_column(ForeignKey("pokemon_portfolios.id", ondelete="CASCADE"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("pokemon_portfolio_comments.id", ondelete="CASCADE"))  # one level deep
    holding_id: Mapped[int | None] = mapped_column(ForeignKey("pokemon_holdings.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="visible", server_default="visible", nullable=False)  # visible | deleted | hidden


class PokemonConditionVote(IdMixin, TimestampMixin, Base):
    """A collector's opinion of a raw card's condition (a grade-equivalent number). One per member per holding. Never an
    official grade; shown only as a distribution once enough independent members answered."""

    __tablename__ = "pokemon_condition_votes"
    __table_args__ = (UniqueConstraint("holding_id", "member_id"),)

    holding_id: Mapped[int] = mapped_column(ForeignKey("pokemon_holdings.id", ondelete="CASCADE"), index=True, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    estimate: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-10
    note: Mapped[str] = mapped_column(String(280), default="", server_default="", nullable=False)


class PokemonPortfolioReport(IdMixin, TimestampMixin, Base):
    __tablename__ = "pokemon_portfolio_reports"

    reporter_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    target: Mapped[str] = mapped_column(String(10), nullable=False)  # comment | portfolio | holding
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(280), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open", nullable=False)  # open | resolved | dismissed


class PokemonScan(IdMixin, Base):
    """One card-recognition request (rate limits, cost tracking, and the candidates shown). The photo itself is not kept."""

    __tablename__ = "pokemon_scans"
    __table_args__ = (Index("ix_pokemon_scans_member_at", "member_id", "at"), Index("ix_pokemon_scans_at", "at"))

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), default="identify", nullable=False)  # identify | condition
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # ok | no_match | error
    credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
