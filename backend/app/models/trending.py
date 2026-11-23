"""Trending Topics (app/trending): Admin › X › Trending Topics. What the sources are talking about, how hot each topic is, and the tweets
suggested for it. Nothing here posts on its own unless the owner switches Automatic on (and, separately, Live)."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, Index, Integer, String, Text, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class TtSettings(Base):
    """One row. mode approval: every tweet waits for the owner. automatic: a genuinely hot topic's tweet goes out by itself, only while
    auto_confirmed_at is set (the owner approved automatic on 2026-11-14, so a new install starts confirmed; switching back to approval
    clears it and turning it on again needs a confirmation in Admin). live: follow the News Desk's X mode (posts go to X only while the
    News Desk is live); False forces a dry run whatever the News Desk does. kill_switch: Trending's own stop for its posts."""

    __tablename__ = "tt_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # scanning the sources
    mode: Mapped[str] = mapped_column(String(10), default="automatic", server_default="automatic", nullable=False)  # approval | automatic
    auto_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    live: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    hot_score: Mapped[float] = mapped_column(Float, default=75.0, server_default="75", nullable=False)  # automatic publishes at or above
    suggest_score: Mapped[float] = mapped_column(Float, default=45.0, server_default="45", nullable=False)  # a tweet is written at or above
    max_per_day: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    gap_minutes: Mapped[int] = mapped_column(Integer, default=180, server_default="180", nullable=False)  # between two trend posts
    topic_cooldown_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24", nullable=False)
    newsdesk_gap_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30", nullable=False)  # clear of News Desk posts and slots
    shared_daily_cap: Mapped[int] = mapped_column(Integer, default=20, server_default="20", nullable=False)  # News Desk + Trending on X, 24 h
    categories: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sources: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # which sources are read, and their options
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # source health, last scan, quota use, learned weights


class TtSignal(IdMixin, TimestampMixin, Base):
    """One item a source returned (a headline, a Google trend, a Reddit thread, an X trend), with its engagement over time."""

    __tablename__ = "tt_signals"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_tt_signals_source_external"), Index("ix_tt_signals_last_seen", "last_seen"))

    source: Mapped[str] = mapped_column(String(16), nullable=False)  # news | google | reddit | x_trends | x_accounts
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    outlet: Mapped[str] = mapped_column(String(80), default="", nullable=False)  # who said it: BBC, r/CryptoCurrency, Google Trends
    reliable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # a news outlet: may back a factual claim
    category: Mapped[str | None] = mapped_column(String(16))
    engagement: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    history: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # [[iso, engagement], ...] newest last, at most 12
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class TtTopic(IdMixin, TimestampMixin, Base):
    """A cluster of signals about one thing, scored on every scan."""

    __tablename__ = "tt_topics"
    __table_args__ = (Index("ix_tt_topics_status", "status", "score"),)

    key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)  # the lead reliable headline, else the trend's own words
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="active", nullable=False)  # active | cooling | posted | dismissed
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    peak_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    parts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # velocity, engagement, freshness, relevance, discussion, learned
    signal_ids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # [{source, outlet, title, url, at, engagement, reliable}]
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # engagement totals per source, signal count
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TtPost(IdMixin, TimestampMixin, Base):
    """A tweet written for a topic. pending: waiting for the owner. queued: approved (or automatic), going out when the spacing allows.
    held: it failed a gate (never sent). dry_run: what would have gone out. published / failed / uncertain: X's answer."""

    __tablename__ = "tt_posts"
    __table_args__ = (Index("ix_tt_posts_status", "status", "created_at"), Index("ix_tt_posts_topic", "topic_id"))

    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    original_text: Mapped[str | None] = mapped_column(Text)
    angle: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    writer: Mapped[str] = mapped_column(String(10), default="rules", nullable=False)  # rules | model | owner
    fingerprint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # the reliable items the facts come from
    source_text: Mapped[str] = mapped_column(Text, default="", nullable=False)  # what those sources said: the gates compare against it
    checks: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    hold_reason: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)  # why a queued post is still waiting
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    x_id: Mapped[str | None] = mapped_column(String(32))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metrics_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # owner, 2026-11-23: a tweet goes with a licensed picture or a link; the owner alone may send it as words only ("Post without picture")
    text_ok: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
