"""Indexes for hot queries (migration 0090_perf_indexes), kept in one place with the query each one serves.

Each is defined against its model's table here rather than in the model's __table_args__, so the list reads as the
audit it came from. tests/test_migrations.py keeps these and the migration identical.
"""

from sqlalchemy import Index

from app.models.activity import ActivityLog
from app.models.brand_manager import BmEvent
from app.models.exchange import ExEquitySnapshot, ExTrade
from app.models.invites import InviteSend
from app.models.market import MarketOffer, MarketOrder, MarketPayment
from app.models.members import Member, MemberMission, MemberSession, ReputationEvent
from app.models.pokemon import PokemonCard, PokemonPopulation, PokemonProviderValuation, PokemonReferencePrice, PokemonSyncLog
from app.models.portfolio import PokemonPortfolioComment
from app.models.social import SocialPost
from app.models.telegram import TelegramMessage
from app.models.trading import TrIntent, TrPosition
from app.models.tradingbot import TbEvent
from app.models.tradingos import TsBookSnapshot, TsDecision, TsLiquidation, TsOiSnapshot, TsSignalIdea

PERF_INDEXES: tuple[Index, ...] = (
    # members.verify (verified XP, discussion missions), /members/me Telegram activity, crew /start lookups
    Index("ix_telegram_messages_author_sent", TelegramMessage.author_id, TelegramMessage.sent_at),
    # trading bot page: the bot's latest signal posts in a source
    Index("ix_telegram_messages_source_sent", TelegramMessage.source_id, TelegramMessage.sent_at),
    # Read & Earn / X feed / missions: Price Chat's own X posts by date (x.own, members.verify, /members/me/x-feed)
    Index("ix_social_posts_own_posted", SocialPost.platform, SocialPost.is_own, SocialPost.posted_at),
    # missions.expire (every members.verify): pending claims
    Index("ix_member_missions_status", MemberMission.status),
    # invites.poll: "did we already contact this person" per private message, and queue()'s invited subquery
    Index("ix_invite_sends_user_method", InviteSend.user_id, InviteSend.method),
    # /signal track-record line: graded ideas, newest resolved first
    Index("ix_ts_signal_ideas_status_resolved", TsSignalIdea.status, TsSignalIdea.resolved_at),
    # trading bot: once-per-member events
    Index("ix_tb_events_member_kind", TbEvent.member_id, TbEvent.kind),
    # ex_orders deletes (maker.prune every 15 s) check these FKs per deleted order: without them, a scan of ex_trades each
    Index("ix_ex_trades_buy_order", ExTrade.buy_order_id),
    Index("ix_ex_trades_sell_order", ExTrade.sell_order_id),
    # pokemon.exchange (every minute): "is today's equity snapshot taken"
    Index("ix_ex_equity_snapshots_day", ExEquitySnapshot.day),
    # pokemon.warm / top100 / market rows: catalogue-wide reads by grade (no card filter)
    Index("ix_pokemon_provider_valuations_grade_day", PokemonProviderValuation.grading_company, PokemonProviderValuation.grade,
          PokemonProviderValuation.day),
    Index("ix_pokemon_reference_prices_grade", PokemonReferencePrice.provider, PokemonReferencePrice.grading_company,
          PokemonReferencePrice.grade, PokemonReferencePrice.as_of),
    Index("ix_pokemon_populations_grade", PokemonPopulation.grading_company, PokemonPopulation.grade, PokemonPopulation.as_of),
    # pokemon.catalog (hourly): sets refreshed by recent runs; retention of old run logs
    Index("ix_pokemon_sync_logs_provider_started", PokemonSyncLog.provider, PokemonSyncLog.started_at),
    # /members/packs: each pack's cover card
    Index("ix_pokemon_cards_set_name", PokemonCard.set_name, PokemonCard.name),
    # public portfolio comments: replies of the shown comments
    Index("ix_pokemon_portfolio_comments_parent", PokemonPortfolioComment.parent_id),
    # pokemon.market_expire (every 2 min): unpaid reservations, expired offers, pending payments
    Index("ix_market_orders_status_expires", MarketOrder.status, MarketOrder.expires_at),
    Index("ix_market_offers_status_expires", MarketOffer.status, MarketOffer.expires_at),
    Index("ix_market_payments_status", MarketPayment.status, MarketPayment.network, MarketPayment.created_at),
    # tradingos.structure / oi / shadow prunes and reads by time across coins
    Index("ix_ts_liquidations_t", TsLiquidation.t),
    Index("ix_ts_oi_snapshots_at", TsOiSnapshot.at),
    Index("ix_ts_book_snapshots_at", TsBookSnapshot.at),
    Index("ix_ts_decisions_candle_t", TsDecision.candle_t),
    # tradingos.track (every minute): auto-trading intents per signal + account, skip announcements, position announcements
    Index("ix_tr_intents_signal_account", TrIntent.signal_id, TrIntent.account_id),
    Index("ix_tr_intents_status_created", TrIntent.status, TrIntent.created_at),
    Index("ix_tr_positions_status_closed", TrPosition.status, TrPosition.closed_at),
    # brand.tick: a brand's recent log lines (daily caps look back 24 h)
    Index("ix_bm_events_brand_at", BmEvent.brand_id, BmEvent.at),
    # system.retention (and the dashboard's recent feed)
    Index("ix_activity_logs_created_at", ActivityLog.created_at),
    Index("ix_member_sessions_expires_at", MemberSession.expires_at),
    # weekly / monthly leaderboards (every signed-in /members/leaderboard and /members/me)
    Index("ix_reputation_events_occurred_at", ReputationEvent.occurred_at),
    # all-time leaderboard order and "your position" count
    Index("ix_members_rank_score", Member.rank_score, Member.created_at),
)
