"""Indexes for hot queries (scheduled jobs and the busiest member / public routes); mirrored in app/models/perf_indexes.py

Plain CREATE INDEX inside the migration transaction (env.py runs every migration in one transaction, so Postgres'
CREATE INDEX CONCURRENTLY is not available here); each table is read once to build its index.
telegram_messages by author is 0089's ix_telegram_messages_author (author_id, chat_id, sent_at), not repeated here.
ex_orders / perp_orders: the account index gains updated_at, so maker.prune's dead-quote delete is an index range.

Revision ID: 0090_perf_indexes
Revises: 0089_workers_checks
"""

from collections.abc import Sequence

from alembic import op


revision: str = '0090_perf_indexes'
down_revision: str | None = '0089_workers_checks'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = [  # (name, table, columns)
    ("ix_telegram_messages_source_sent", "telegram_messages", ["source_id", "sent_at"]),
    ("ix_social_posts_own_posted", "social_posts", ["platform", "is_own", "posted_at"]),
    ("ix_member_missions_status", "member_missions", ["status"]),
    ("ix_invite_sends_user_method", "invite_sends", ["user_id", "method"]),
    ("ix_ts_signal_ideas_status_resolved", "ts_signal_ideas", ["status", "resolved_at"]),
    ("ix_tb_events_member_kind", "tb_events", ["member_id", "kind"]),
    ("ix_ex_trades_buy_order", "ex_trades", ["buy_order_id"]),
    ("ix_ex_trades_sell_order", "ex_trades", ["sell_order_id"]),
    ("ix_ex_equity_snapshots_day", "ex_equity_snapshots", ["day"]),
    ("ix_pokemon_provider_valuations_grade_day", "pokemon_provider_valuations", ["grading_company", "grade", "day"]),
    ("ix_pokemon_reference_prices_grade", "pokemon_reference_prices", ["provider", "grading_company", "grade", "as_of"]),
    ("ix_pokemon_populations_grade", "pokemon_populations", ["grading_company", "grade", "as_of"]),
    ("ix_pokemon_sync_logs_provider_started", "pokemon_sync_logs", ["provider", "started_at"]),
    ("ix_pokemon_cards_set_name", "pokemon_cards", ["set_name", "name"]),
    ("ix_pokemon_portfolio_comments_parent", "pokemon_portfolio_comments", ["parent_id"]),
    ("ix_market_orders_status_expires", "market_orders", ["status", "expires_at"]),
    ("ix_market_offers_status_expires", "market_offers", ["status", "expires_at"]),
    ("ix_market_payments_status", "market_payments", ["status", "network", "created_at"]),
    ("ix_ts_liquidations_t", "ts_liquidations", ["t"]),
    ("ix_ts_oi_snapshots_at", "ts_oi_snapshots", ["at"]),
    ("ix_ts_book_snapshots_at", "ts_book_snapshots", ["at"]),
    ("ix_ts_decisions_candle_t", "ts_decisions", ["candle_t"]),
    ("ix_tr_intents_signal_account", "tr_intents", ["signal_id", "account_id"]),
    ("ix_tr_intents_status_created", "tr_intents", ["status", "created_at"]),
    ("ix_tr_positions_status_closed", "tr_positions", ["status", "closed_at"]),
    ("ix_bm_events_brand_at", "bm_events", ["brand_id", "at"]),
    ("ix_activity_logs_created_at", "activity_logs", ["created_at"]),
    ("ix_member_sessions_expires_at", "member_sessions", ["expires_at"]),
    ("ix_reputation_events_occurred_at", "reputation_events", ["occurred_at"]),
    ("ix_members_rank_score", "members", ["rank_score", "created_at"]),
]

WIDENED = [("ix_ex_orders_account", "ex_orders"), ("ix_perp_orders_account", "perp_orders")]


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols)
    for name, table in WIDENED:
        op.drop_index(name, table_name=table)
        op.create_index(name, table, ["account_id", "status", "updated_at"])


def downgrade() -> None:
    for name, table in WIDENED:
        op.drop_index(name, table_name=table)
        op.create_index(name, table, ["account_id", "status"])
    for name, table, _cols in reversed(INDEXES):
        op.drop_index(name, table_name=table)
