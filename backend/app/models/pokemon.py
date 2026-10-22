"""Pokémon Vintage Market Cap: a market-data catalog for Pokémon cards.

Identity: one row per DISTINCT collectible (set + number + language + edition + variant). 1st Edition and Unlimited
Base Set Charizard are different assets. Grades are never mixed: every price, population, sale and snapshot names
its grading company and grade (RAW conditions included).

Money is stored in integer cents (USD conversion kept beside the original currency). Nothing here is ever filled
with made-up values: no price means None, shown as "—".
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, UTCDateTime


class PokemonCard(IdMixin, TimestampMixin, Base):
    __tablename__ = "pokemon_cards"
    __table_args__ = (UniqueConstraint("identity_key"),)

    identity_key: Mapped[str] = mapped_column(String(160), nullable=False)  # set|number|language|edition|variant, lower-case
    name: Mapped[str] = mapped_column(String(128), nullable=False)  # e.g. "Charizard"
    pokemon_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    set_name: Mapped[str] = mapped_column(String(128), nullable=False)
    set_code: Mapped[str | None] = mapped_column(String(32))  # e.g. the Pokémon TCG API set id ("base1")
    card_number: Mapped[str | None] = mapped_column(String(16))  # "4/102"; None when the card has none printed
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="English")
    edition: Mapped[str] = mapped_column(String(32), nullable=False, default="Unlimited")  # 1st Edition | Shadowless | Unlimited | —
    rarity: Mapped[str | None] = mapped_column(String(48))
    variant: Mapped[str] = mapped_column(String(48), nullable=False, default="")  # e.g. "No Rarity", "Gold Star"
    holo_type: Mapped[str | None] = mapped_column(String(32))  # Holo | Reverse Holo | Non-Holo | Gold Star ...
    era: Mapped[str] = mapped_column(String(16), nullable=False, default="vintage")  # vintage | modern
    is_promo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(512))
    image_source: Mapped[str | None] = mapped_column(String(32))  # which provider the image came from
    release_date: Mapped[date | None] = mapped_column(Date)
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)  # {"pokemontcg": "base1-4", ...}
    search_text: Mapped[str] = mapped_column(Text, default="", nullable=False)  # normalised words for search


class PokemonSale(IdMixin, TimestampMixin, Base):
    """One verified-or-not transaction. Duplicates are refused by (source, source_listing_id) or, when a source has no
    listing id, by a fingerprint of card + grade + price + date + source."""

    __tablename__ = "pokemon_sales"
    __table_args__ = (UniqueConstraint("fingerprint"), Index("ix_pokemon_sales_card_grade_date", "card_id", "grading_company", "grade", "sale_date"),
                      Index("ix_pokemon_sales_grade_date", "grading_company", "grade", "sale_date"), Index("ix_pokemon_sales_date", "sale_date"))

    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    grading_company: Mapped[str] = mapped_column(String(8), nullable=False)  # PSA | BGS | CGC | RAW
    grade: Mapped[str] = mapped_column(String(8), nullable=False)  # "10", "9.5", or a raw condition ("NM")
    cert_number: Mapped[str | None] = mapped_column(String(32))
    sale_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)  # in `currency`
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    sale_price_usd_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    sale_date: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # ebay | goldin | heritage | pwcc | pricecharting | manual ...
    source_listing_id: Mapped[str | None] = mapped_column(String(128))
    auction_house: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(512))
    sale_type: Mapped[str | None] = mapped_column(String(24))  # auction | buy_it_now | best_offer | fixed | private
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    verified_by: Mapped[str | None] = mapped_column(String(16))  # manual (admin confirmed the import) | source (the source reports it as sold)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # outlier / shill, kept for audit
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    fx_rate: Mapped[float | None] = mapped_column(Float)  # USD per unit of `currency` used for sale_price_usd_cents
    fx_source: Mapped[str | None] = mapped_column(String(32))  # usd | ecb (Frankfurter) | admin
    title: Mapped[str | None] = mapped_column(Text)  # the original listing / lot title, when there was one
    match_confidence: Mapped[float | None] = mapped_column(Float)  # how surely the row was matched to this card (0-1)


class PokemonPopulation(IdMixin, TimestampMixin, Base):
    """How many copies a grading company has graded at one grade, as reported by a source on a date."""

    __tablename__ = "pokemon_populations"
    __table_args__ = (UniqueConstraint("card_id", "grading_company", "grade", "source"),)

    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    grading_company: Mapped[str] = mapped_column(String(8), nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PokemonPriceSnapshot(IdMixin, TimestampMixin, Base):
    """Price Chat's own history: the engine's estimate for one card + grade at one moment (charts, 24h / 7d moves)."""

    __tablename__ = "pokemon_price_snapshots"
    __table_args__ = (Index("ix_pokemon_snapshots_card_grade_time", "card_id", "grading_company", "grade", "taken_at"),)

    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    grading_company: Mapped[str] = mapped_column(String(8), nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    market_price_cents: Mapped[int | None] = mapped_column(Integer)
    population: Mapped[int | None] = mapped_column(Integer)
    market_cap_cents: Mapped[int | None] = mapped_column(Integer)  # ESTIMATED: market price x known population
    sales_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # sales in the 30 days before
    sales_volume_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confidence: Mapped[str | None] = mapped_column(String(8))  # HIGH | MEDIUM | LOW at the time
    taken_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)


class PokemonWatch(IdMixin, TimestampMixin, Base):
    """A starred card on the owner's watchlist."""

    __tablename__ = "pokemon_watchlist"
    __table_args__ = (UniqueConstraint("owner", "card_id"),)

    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="admin")
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False)


class PokemonSourceState(IdMixin, TimestampMixin, Base):
    """What each data provider last did. Never holds credentials."""

    __tablename__ = "pokemon_sources"

    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # psa | ebay | pricecharting | tcgplayer | beckett | pokemontcg | manual
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    records_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_request_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    requests_today: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    requests_day: Mapped[str | None] = mapped_column(String(10))
    rate_limit: Mapped[str | None] = mapped_column(String(128))  # as the provider reports / documents it


class PokemonListing(IdMixin, TimestampMixin, Base):
    """Something seen on a marketplace: an ACTIVE listing (an asking price, never a sale) or a SOLD item a provider reported,
    before it is accepted as a sale. Low-confidence card matches wait here as "review" (REVIEW NEEDED)."""

    __tablename__ = "pokemon_listings"
    __table_args__ = (UniqueConstraint("provider", "kind", "listing_id"),)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # ebay ...
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # active | sold
    listing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    condition: Mapped[str | None] = mapped_column(String(64))
    seller: Mapped[str | None] = mapped_column(String(128))
    listing_type: Mapped[str | None] = mapped_column(String(32))  # auction | fixed_price | best_offer
    url: Mapped[str | None] = mapped_column(String(512))
    image_url: Mapped[str | None] = mapped_column(String(512))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sold_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    card_id: Mapped[int | None] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="SET NULL"), index=True)
    grading_company: Mapped[str | None] = mapped_column(String(8))
    grade: Mapped[str | None] = mapped_column(String(8))
    match_status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)  # matched | review | rejected | imported
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class PokemonReferencePrice(IdMixin, TimestampMixin, Base):
    """A price a provider publishes (e.g. PriceCharting). Reference only: never a sale, never the source of truth.
    `provider_grade` keeps the provider's own label ("Grade 9" is any grader); company/grade are set only on an exact match."""

    __tablename__ = "pokemon_reference_prices"
    __table_args__ = (UniqueConstraint("provider", "card_id", "provider_grade", "as_of"),)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_product_id: Mapped[str | None] = mapped_column(String(64))
    provider_grade: Mapped[str] = mapped_column(String(32), nullable=False)
    grading_company: Mapped[str | None] = mapped_column(String(8))
    grade: Mapped[str | None] = mapped_column(String(8))
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class PokemonValuationPoint(IdMixin, TimestampMixin, Base):
    """The price history engine's output: the estimate for one card + grade as of the end of a day on which at least one
    verified sale happened (the engine run over the sales known by then). Days without a sale get NO row: charts carry
    the last point forward and say so. Rebuilt from pokemon_sales whenever the sales of that card + grade change."""

    __tablename__ = "pokemon_valuation_points"
    __table_args__ = (UniqueConstraint("card_id", "grading_company", "grade", "day"),
                      Index("ix_pokemon_points_card_grade_day", "card_id", "grading_company", "grade", "day"),
                      Index("ix_pokemon_points_grade_day", "grading_company", "grade", "day"))

    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False)
    grading_company: Mapped[str] = mapped_column(String(8), nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)  # UTC day of the sale event(s)
    market_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[str | None] = mapped_column(String(8))
    sales_used: Mapped[int] = mapped_column(Integer, nullable=False)  # sales the estimate rests on
    sales_on_day: Mapped[int] = mapped_column(Integer, nullable=False)  # verified sales that happened that day
    volume_on_day_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(160), nullable=False)


class PokemonPopulationHistory(IdMixin, TimestampMixin, Base):
    """Every population report kept over time (pokemon_populations holds only the latest per source).
    One row per card + grade + source + day: used for the price-versus-population chart."""

    __tablename__ = "pokemon_population_history"
    __table_args__ = (UniqueConstraint("card_id", "grading_company", "grade", "source", "day"),
                      Index("ix_pokemon_pophist_card_grade_day", "card_id", "grading_company", "grade", "day"))

    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False)
    grading_company: Mapped[str] = mapped_column(String(8), nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)


class PokemonProviderValuation(IdMixin, TimestampMixin, Base):
    """A market valuation PUBLISHED BY A PROVIDER (e.g. Scrydex) for one card variant at one exact grade, per day.
    Never a sale: kept apart from pokemon_sales and never fed into Price Chat's own sales-based engine. `kind` = "current"
    (today's published price, with low / mid / high and the provider's own trends) or "history" (the provider's daily
    price history). Unknown fields stay NULL."""

    __tablename__ = "pokemon_provider_valuations"
    __table_args__ = (UniqueConstraint("provider", "card_id", "grading_company", "grade", "day"),
                      Index("ix_pokemon_provval_card_grade_day", "card_id", "grading_company", "grade", "day"))

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False)
    provider_card_id: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "base1-4"
    provider_variant: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "firstEditionShadowlessHolofoil"
    grading_company: Mapped[str] = mapped_column(String(8), nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # current | history
    market_cents: Mapped[int | None] = mapped_column(Integer)
    low_cents: Mapped[int | None] = mapped_column(Integer)
    mid_cents: Mapped[int | None] = mapped_column(Integer)
    high_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    trends: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # the provider's own {"days_7": {"percent_change": ...}, ...}
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PokemonSyncLog(IdMixin, TimestampMixin, Base):
    """One provider sync run: what was asked, how many requests it used, what was imported, and every per-card outcome."""

    __tablename__ = "pokemon_sync_logs"

    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | partial | error
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    entries: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text)


class PokemonPulsePost(IdMixin, TimestampMixin, Base):
    """A public X post shown in the Market Pulse sidebar. Context only: NEVER a sale record and never used in any price."""

    __tablename__ = "pokemon_pulse_posts"
    __table_args__ = (UniqueConstraint("post_id"), Index("ix_pokemon_pulse_created", "created_at_x"))

    post_id: Mapped[str] = mapped_column(String(32), nullable=False)
    author_id: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_x: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    lang: Mapped[str | None] = mapped_column(String(8))
    url: Mapped[str] = mapped_column(String(256), nullable=False)
    media_url: Mapped[str | None] = mapped_column(String(512))
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reposts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quotes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    categories: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)  # auctions, vintage, grading, sale
    relevance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    text_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # normalised text: near-duplicates share it
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PokemonPulseConfig(IdMixin, TimestampMixin, Base):
    """Settings + state of the Market Pulse feed (one row). Recurring refresh is OFF until an admin enables it."""

    __tablename__ = "pokemon_pulse_config"

    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    trusted_accounts: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    since_id: Mapped[str | None] = mapped_column(String(32))
    last_refresh_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    last_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)  # posts, users, est. cost, kept, filtered


class PokemonTop100Snapshot(IdMixin, TimestampMixin, Base):
    """The Top 100 as assessed on one day (the day's last assessment): rank, the numbers behind its market cap and the
    data-quality score that made it eligible. Kept forever, so entries / exits can be audited."""

    __tablename__ = "pokemon_top100_snapshots"
    __table_args__ = (UniqueConstraint("day", "card_id"), Index("ix_pokemon_top100_day", "day"))

    day: Mapped[date] = mapped_column(Date, nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    market_cap_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_source: Mapped[str] = mapped_column(String(32), nullable=False)
    population: Mapped[int] = mapped_column(Integer, nullable=False)
    history_points: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)


class PokemonTop100Event(IdMixin, TimestampMixin, Base):
    """A card entering or leaving the Top 100, with why (failed checks, or outranked by a larger eligible cap)."""

    __tablename__ = "pokemon_top100_events"
    __table_args__ = (Index("ix_pokemon_top100_events_at", "at"),)

    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("pokemon_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # entered | left
    rank: Mapped[int | None] = mapped_column(Integer)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
