"""Background jobs: Telegram ingestion and the community intelligence pipeline.

    telegram.ingest ─▶ community.process ─▶ community.topics ─▶ summaries ─▶ memories / opportunities

Each job is independent and idempotent: it works on whatever the previous stage
produced, so a failure in one stage never corrupts another, and every stage can
be re-run safely.
"""

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select

from app.community.extraction import extract_memories
from app.services.usage import bump as usage_bump
from app.community.merging import suggest_merges
from app.community.ranking import rank_opportunities
from app.connectors.state import get_connector_state
from app.connectors.x.errors import XAuthError
from app.db.base import utcnow
from app.models.enums import ActivityLevel, Platform
from app.security.credentials import x_credentials
from app.services.agent_service import build_retriever
from app.social.approvals import expire_pending
from app.social.cross_signals import detect_cross_signals
from app.social.x_ingest import ingest_x
from app.analytics.snapshots import TELEGRAM_SNAPSHOT_SECONDS
from app.social.x_opportunities import detect_x_opportunities
from app.community.opportunities import detect_opportunities
from app.community.processing import process_pending
from app.community.questions import canonicalise_questions, refresh_question_stats
from app.community.summaries import run_daily_summaries, run_recent_summaries
from app.community.topics import name_topics, refresh_topic_stats
from app.community.tuning import CommunityTuning
from app.knowledge.service import KnowledgeService
from app.models.community import CommunityTopic
from app.models.enums import TopicStatus
from app.models.telegram import TelegramMessage, TelegramSource
from app.scheduler.scheduler import JobContext, JobSkipped, JobSpec
from app.services.activity import ActivityLogger
from app.services.agent_service import get_agent_config
from app.services.runtime import AppRuntime
from app.telegram.errors import TelegramError
from app.models.members import LinkedIdentity
from app.telegram.ingestion import TelegramIngestor, get_bot_state
from app.telegram.service import make_client, record_error, record_recovery

logger = logging.getLogger(__name__)


def _active_topic_ids(db) -> set[int]:  # type: ignore[no-untyped-def]
    return set(db.scalars(select(CommunityTopic.id).where(CommunityTopic.status == TopicStatus.ACTIVE)))


def build_jobs(runtime: AppRuntime) -> list[JobSpec]:
    s = runtime.settings
    tuning = CommunityTuning.from_settings(s)

    def then(job: str, delay: int = 5) -> None:
        """Chain the pipeline: new output from one stage brings the next stage forward."""
        if runtime.scheduler is not None:
            runtime.scheduler.request_run(job, delay)

    async def telegram_ingest(ctx: JobContext) -> dict[str, Any]:
        """Bot mode (updates) and/or account mode (authorised groups via the user session)."""
        from app.telegram import account_service

        db = ctx.db
        bot_on = s.telegram_configured
        account_on = account_service.has_session(s) and account_service.configured(db, s)
        if not bot_on and not account_on:
            raise JobSkipped("Telegram is not configured (Community › Connection: add a bot token or log in with a Telegram account)")
        out: dict[str, Any] = {}
        stored = edited = 0
        if bot_on:
            was_error = get_bot_state(db).status == "error"
            client = make_client(s, runtime.telegram.transport)
            try:
                report = await TelegramIngestor(db, client, runtime.telegram).sync(long_poll_seconds=s.telegram_long_poll_seconds,
                                                                                 listen_seconds=s.telegram_listen_seconds)
            except TelegramError as exc:
                record_error(db, exc)
                runtime.telegram.set_status("error", str(exc))
                raise
            finally:
                await client.aclose()
            state = get_bot_state(db)
            runtime.telegram.set_status("connected", "", state.bot_username)
            record_recovery(db, was_error, report.stored)
            usage_bump("telegram", "bot")
            out = report.as_dict()
            stored, edited = report.stored, report.edited
        if account_on:
            acc_report = await account_service.sync(db, s, runtime.telegram_account)
            out["account"] = acc_report.as_dict()
            stored += acc_report.stored
            edited += acc_report.edited
            out["stored"] = stored  # bot + account
        runtime.telegram.authorised_chats = int(
            db.scalar(select(func.count()).select_from(TelegramSource).where(TelegramSource.enabled.is_(True))) or 0
        )
        if stored or edited:
            then("community.process")
        if stored and db.scalar(select(LinkedIdentity.id).where(LinkedIdentity.platform == "telegram").limit(1)):
            then("members.verify", 3)  # a linked member may have just taken part: missions + XP update within seconds
        return out

    async def community_process(ctx: JobContext) -> dict[str, Any]:
        db = ctx.db
        pending = db.scalar(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.processed_at.is_(None)))
        if not pending:
            return {"processed": 0}
        report = await process_pending(db, runtime.router(db), runtime.embedder, tuning)
        if report.notes and report.classifier == "rules":
            ActivityLogger(db).log("community_processing_degraded", report.notes[0])
        if report.processed:
            then("community.topics", 10)
            then("community.process", 5)  # more may be waiting (batches of 200)
        return report.as_dict()

    async def community_topics(ctx: JobContext) -> dict[str, Any]:
        db = ctx.db
        active_before = _active_topic_ids(db)
        topics = refresh_topic_stats(db, tuning, ctx.now)
        questions = refresh_question_stats(db)
        db.commit()  # statistics are saved even if naming below fails
        router = runtime.router(db)
        named = await name_topics(db, router)
        worded = await canonicalise_questions(db, router)
        db.commit()
        newly_active = len(_active_topic_ids(db) - active_before)
        if newly_active or named or worded:
            then("content.opportunities", 30)
            then("topic.merges", 60)
        return {"topics": topics, "questions": questions, "named": named, "questions_reworded": worded, "newly_active": newly_active}

    async def summary_recent(ctx: JobContext) -> dict[str, Any]:
        made = await run_recent_summaries(ctx.db, runtime.router(ctx.db), tuning, s.community_summary_min_messages, ctx.now)
        if made:
            then("community.memories", 15)
        for summary in made:
            ActivityLogger(ctx.db).log(
                "community_summary", f"New community summary: {summary.headline} ({summary.message_count} messages, {summary.model})",
                details={"summary_id": summary.id, "kind": summary.kind.value, "model": summary.model},
            )
        return {"summaries": len(made)}

    async def summary_daily(ctx: JobContext) -> dict[str, Any]:
        made = await run_daily_summaries(ctx.db, runtime.router(ctx.db), tuning, ctx.now)
        if made:
            then("community.memories", 15)
        for summary in made:
            ActivityLogger(ctx.db).log(
                "community_summary", f"Daily community summary: {summary.headline} ({summary.message_count} messages, {summary.model})",
                details={"summary_id": summary.id, "kind": summary.kind.value, "model": summary.model},
            )
        return {"summaries": len(made)}

    async def memories(ctx: JobContext) -> dict[str, Any]:
        agent_id = get_agent_config(ctx.db).id
        report = await extract_memories(ctx.db, runtime.router(ctx.db), agent_id, runtime.embedder)
        if report.created:
            ActivityLogger(ctx.db, agent_id).log(
                "community_memories_proposed",
                f"Learned {report.created} candidate community memor{'y' if report.created == 1 else 'ies'} from Telegram. "
                "They are pending your review in Knowledge › Community brain.",
                details={"memory_ids": report.memory_ids, "duplicates_skipped": report.duplicates},
            )
        return {"summaries": report.summaries, "created": report.created, "duplicates": report.duplicates}

    async def opportunities(ctx: JobContext) -> dict[str, Any]:
        agent_id = get_agent_config(ctx.db).id
        report = await detect_opportunities(ctx.db, runtime.router(ctx.db), agent_id, tuning, now=ctx.now)
        if report.created:
            then("opportunities.rank", 5)
            ActivityLogger(ctx.db, agent_id).log(
                "content_opportunities_detected",
                f"Found {report.created} new content opportunit{'y' if report.created == 1 else 'ies'} in community discussion (ideas only, nothing published)",
                details={"opportunity_ids": report.ids},
            )
        return {"evaluated": report.evaluated, "created": report.created, "set_aside": report.skipped}

    async def reindex(ctx: JobContext) -> dict[str, Any]:
        if runtime.embedder is None:
            raise JobSkipped("No embedding provider configured")
        count = await KnowledgeService(ctx.db, get_agent_config(ctx.db).id, runtime.embedder).reindex()
        return {"indexed": count}

    async def tradingos_scan(ctx: JobContext) -> dict[str, Any]:
        """Trading OS: scan the top Hyperliquid markets with the deterministic rules (most runs end in NO TRADE)."""
        import asyncio

        from app.tradingos import market, signals

        out = await asyncio.to_thread(signals.scan, ctx.db, market.data())
        snaps = await asyncio.to_thread(_brain_cycle, ctx.db)
        return {"checked": out["checked"], "created": out["created"], "skipped": out.get("skipped"), "no_trade": len(out["no_trade"]), **snaps}

    def _brain_cycle(db: Any) -> dict[str, Any]:
        """Kiwi Brain: a market observation for the headline markets each scan (real Hyperliquid numbers with their sources and
        timestamps), then retention. A failed read is recorded as such, never filled in."""
        from app.tradingos import brain, market

        logged = 0
        for coin in ("BTC", "ETH"):
            try:
                snap = brain.market_snapshot(market.data(), coin, "1h")
            except Exception as e:  # noqa: BLE001
                brain.log(db, "system", f"{coin} market data unavailable: {type(e).__name__}", coin=coin, source="hyperliquid:info",
                          data={"error": str(e)[:200]})
                continue
            i, mk = snap["indicators"], snap["market"]
            title = (f"{coin} market data updated · {mk['mark']:g}" + (f" · RSI {i['rsi14']:.1f}" if i["rsi14"] is not None else "")
                     + (f" · trend {i['trend']}" if i["trend"] else "") + (" · STALE" if snap["stale"] else ""))
            brain.log(db, "market", title, coin=coin, source=snap["candles"]["source"], source_at=snap["candles"]["at"],
                      data=brain.jsonable(snap))
            logged += 1
        db.commit()
        pruned = brain.prune(db)
        return {"brain_snapshots": logged, "brain_pruned": pruned}

    async def tradingos_kitchen(ctx: JobContext) -> dict[str, Any]:
        """Kiwi Kitchen: the 15m / 30m strategy's state machine for its markets (closed candles; most runs change nothing)."""
        import asyncio

        from app.tradingos import kitchen, market

        return await asyncio.to_thread(kitchen.tick, ctx.db, market.data())

    async def tradingos_oi(ctx: JobContext) -> dict[str, Any]:
        """Open-interest history: Hyperliquid keeps none, so record the top markets' OI and funding every 15 minutes."""
        import asyncio

        from app.tradingos import market, oi

        return await asyncio.to_thread(oi.record, ctx.db, market.data())

    async def tradingos_structure(ctx: JobContext) -> dict[str, Any]:
        """Market structure record: binned order books (liquidity heatmaps) and OKX's reported liquidations, every 2 minutes."""
        import asyncio

        from app.tradingos import market, structure

        return await asyncio.to_thread(structure.record, ctx.db, structure.HEATMAP_COINS, None, market.data())

    async def tradingos_shadow(ctx: JobContext) -> dict[str, Any]:
        """Shadow trading: replay every finished kitchen decision (taken, rejected or shadow) on the candles that followed."""
        import asyncio

        from app.tradingos import decisions, market

        return await asyncio.to_thread(decisions.resolve, ctx.db, market.data())

    async def tradingos_riskteam(ctx: JobContext) -> dict[str, Any]:
        """Kiwi Risk Team: the AI Chief Risk Officer's memo on each reviewed kitchen setup (the desks already decided)."""
        from app.tradingos import riskteam

        return await riskteam.write_memos(ctx.db, runtime.router(ctx.db))

    async def tradingos_ideas(ctx: JobContext) -> dict[str, Any]:
        """/signal track record: grade the plans /signal showed on the candles that followed (record.py)."""
        import asyncio

        from app.tradingos import market, record

        return await asyncio.to_thread(record.grade_all, ctx.db, market.data())

    async def tradingos_brief(ctx: JobContext) -> dict[str, Any]:
        """The daily brief in the signal group, once a day at the configured UTC hour (bot.daily_brief)."""
        from app.tradingos import bot

        if not s.telegram_configured:
            raise JobSkipped("Telegram is not configured")
        client = make_client(s, runtime.telegram.transport)
        try:
            return {"brief": await bot.daily_brief(ctx.db, client)}
        finally:
            await client.aclose()

    _kiwis_shown: dict[int, int] = {}  # signal id -> the entry count its Telegram card shows

    async def tradingos_alerts(ctx: JobContext) -> dict[str, Any]:
        """Price alerts (/alert, the /signal "Alert me at entry" button): one markets read, crossed alerts fire once by DM."""
        from app.telegram.service import make_client
        from app.tradingos import alerts, market

        if not s.telegram_configured:
            return {"skipped": "telegram not configured"}
        client = make_client(s, runtime.telegram.transport)
        try:
            return await alerts.check(ctx.db, client, market.data())
        finally:
            await client.aclose()

    async def tradingos_track(ctx: JobContext) -> dict[str, Any]:
        """Trading OS: follow open signals on real 1-minute candles, publish approved ones (auto-publish), update cards."""
        import asyncio

        from sqlalchemy import select

        from app.models.tradingos import TsSignal
        from app.telegram.service import make_client
        from app.tradingos import bot, market, signals

        changed = await asyncio.to_thread(signals.track, ctx.db, market.data())
        st = signals.settings(ctx.db)
        to_publish = [] if st.paused else list(ctx.db.scalars(select(TsSignal).where(TsSignal.status == "WAITING", TsSignal.tg_message_id.is_(None))))
        published = 0
        if s.telegram_configured and not bot._menu_set:  # the "/" command menu, once per process
            menu_client = make_client(s, runtime.telegram.transport)
            try:
                await bot.ensure_menu(menu_client)
            except Exception:  # noqa: BLE001 - cosmetic; retried next run
                pass
            finally:
                await menu_client.aclose()
        if (changed or to_publish) and s.telegram_configured:
            client = make_client(s, runtime.telegram.transport)
            try:
                for sig in to_publish:
                    try:
                        await bot.publish(ctx.db, client, sig, "auto-publish")
                        published += 1
                    except Exception:  # noqa: BLE001 - recorded on the signal (tg_error); the next run retries
                        continue
                for sig in changed:
                    await bot.refresh(ctx.db, client, sig)
                    await bot.announce(ctx.db, client, sig, signals.BEFORE.pop(sig.id, ""))  # the open / close notification
            finally:
                await client.aclose()
        ex = await asyncio.to_thread(_trade_cycle, ctx.db)
        if s.telegram_configured:  # ORDER UP / KIWI FINISHED COOKING, privately, once per position change
            client = make_client(s, runtime.telegram.transport)
            try:
                ex["announced"] = await bot.announce_positions(ctx.db, client)
                ex["skips_told"] = await bot.announce_skips(ctx.db, client)  # AUTO didn't take a signal: the member hears why
            finally:
                await client.aclose()
        # "👥 N Kiwis in" on the cards: redraw an open card whenever its entry count changed (website, Telegram or AUTO entries)
        if s.telegram_configured:
            moved = []
            for sig in ctx.db.scalars(select(TsSignal).where(TsSignal.status.in_(signals.OPEN), TsSignal.tg_message_id.is_not(None))):
                n = signals.participation(ctx.db, sig).get("enter", 0)
                if _kiwis_shown.get(sig.id) != n:
                    _kiwis_shown[sig.id] = n
                    moved.append(sig)
            if moved:
                client = make_client(s, runtime.telegram.transport)
                try:
                    for sig in moved:
                        await bot.refresh(ctx.db, client, sig)
                finally:
                    await client.aclose()
        return {"changed": [f"#{x.number:03d} {x.status}" for x in changed], "published": published, **ex}

    def _trade_cycle(db: Any) -> dict[str, Any]:
        """Execution upkeep: AUTO entries on open published signals (once per signal per account, within each member's
        preapproved limits), paper positions on real candles, and testnet/live positions reconciled with the exchange."""
        from sqlalchemy import select

        from app.models.trading import TrAccount, TrPosition
        from app.models.tradingos import TsSignal
        from app.tradingos import execution, signals

        auto = 0
        if not signals.settings(db).paused:
            for sig in db.scalars(select(TsSignal).where(TsSignal.status == "ACTIVE", TsSignal.published_at.is_not(None))).all():
                auto += len(execution.run_auto(db, sig))
        paper = execution.track_paper(db)
        reconciled, errors = 0, 0
        ids = {a for (a,) in db.execute(select(TrPosition.account_id).where(TrPosition.status.in_(execution.TO_RECONCILE),
                                                                         TrPosition.mode != "PAPER")).all()}
        for acc in db.scalars(select(TrAccount).where(TrAccount.id.in_(ids))).all() if ids else []:
            try:
                reconciled += execution.reconcile(db, acc)
            except Exception:  # noqa: BLE001 - one account's exchange hiccup must not stop the others; retried next minute
                db.rollback()
                errors += 1
        return {"auto_entries": auto, "paper_tracked": paper, "reconciled": reconciled, "reconcile_errors": errors}

    async def affiliate_snapshot(ctx: JobContext) -> dict[str, Any]:
        """Hyperliquid affiliate wallet: store the current referral numbers (Hyperliquid keeps no history for us)."""
        import asyncio

        from app.tradingbot import affiliate
        from app.tradingos import market

        row = await asyncio.to_thread(affiliate.snapshot, ctx.db, market.data()._post)
        return {"referrals": row.data.get("n_referrals"), "volume": row.data.get("volume"), "rewarded": row.data.get("rewarded_to_us")}

    async def workers_verify(ctx: JobContext) -> dict[str, Any]:
        """Worker System: resolve and verify submitted Telegram invites, refresh engagement counts."""
        from app.telegram.service import make_client
        from app.workers import service as wk

        if not s.telegram_configured:
            return await wk.verify(ctx.db, None)
        tg = make_client(s, runtime.telegram.transport)
        try:
            return await wk.verify(ctx.db, tg)
        finally:
            await tg.aclose()

    specs = [
        JobSpec("workers.verify", "Worker invites: verification", "Verify Telegram invites submitted by workers and count engagement", 300,
                workers_verify, initial_delay_seconds=150, timeout_seconds=600),
        JobSpec("tradingbot.affiliate", "Hyperliquid affiliate snapshot", "Record the referral wallet's referrals, volume and rewards (hourly)", 3600,
                affiliate_snapshot, initial_delay_seconds=120, timeout_seconds=300),
        JobSpec("tradingos.scan", "Trading OS: market scan", "Scan Hyperliquid markets for signals (deterministic rules)", 900, tradingos_scan,
                initial_delay_seconds=90, timeout_seconds=600),
        JobSpec("tradingos.kitchen", "Trading OS: Kiwi Kitchen (15m / 30m)", "Evaluate the 15m strategy with 30m confirmation and stream its state",
                45, tradingos_kitchen, initial_delay_seconds=75, timeout_seconds=120),
        JobSpec("tradingos.oi", "Trading OS: open-interest history", "Record open interest and funding of the top markets (for charts and /markets weekly)",
                900, tradingos_oi, initial_delay_seconds=45, timeout_seconds=120),
        JobSpec("tradingos.structure", "Trading OS: market structure record",
                "Order-book snapshots for liquidity heatmaps and exchange-reported liquidations (BTC, ETH, SOL)",
                120, tradingos_structure, initial_delay_seconds=50, timeout_seconds=100),
        JobSpec("tradingos.shadow", "Trading OS: shadow outcomes", "What each kitchen decision (taken or rejected) would have done, after costs (hypothetical)",
                300, tradingos_shadow, initial_delay_seconds=120, timeout_seconds=180),
        JobSpec("tradingos.riskteam", "Trading OS: AI Risk Team memos", "The Chief Risk Officer explains each kitchen risk decision (memo only)",
                60, tradingos_riskteam, initial_delay_seconds=100, timeout_seconds=300),
        JobSpec("tradingos.alerts", "Trading OS: price alerts", "Check members' Telegram price alerts against Hyperliquid marks; each fires once, privately",
                60, tradingos_alerts, initial_delay_seconds=70, timeout_seconds=50),
        JobSpec("tradingos.ideas", "Trading OS: /signal track record", "Grade the plans /signal showed on real candles (filled, target, stop, expired)",
                900, tradingos_ideas, initial_delay_seconds=110, timeout_seconds=300),
        JobSpec("tradingos.brief", "Trading OS: daily brief", "Once a day at the set UTC hour: market overview, the most promising setup with its chart and the track record, in the signal group",
                300, tradingos_brief, initial_delay_seconds=130, timeout_seconds=300),
        JobSpec("tradingos.track", "Trading OS: signal tracking", "Follow open signals on real prices and update their Telegram cards", 60,
                tradingos_track, initial_delay_seconds=60, timeout_seconds=300),
        JobSpec("telegram.ingest", "Telegram ingestion", "Fetch new messages from authorised chats (read-only) and answer bot commands; each run keeps listening for TELEGRAM_LISTEN_SECONDS so replies come within about a second", s.job_telegram_ingest_seconds, telegram_ingest, initial_delay_seconds=5, timeout_seconds=s.telegram_listen_seconds + 60),
        JobSpec("community.process", "Conversation processing", "Classify messages, detect questions, group into topics", s.job_community_process_seconds, community_process, initial_delay_seconds=20),
        JobSpec("community.topics", "Topic analysis", "Update topic trends, name topics, word recurring questions", s.job_community_topics_seconds, community_topics, initial_delay_seconds=40),
        JobSpec("community.summary.recent", "Recent activity summary", "Summarise the last few hours of each chat", s.job_summary_recent_seconds, summary_recent, initial_delay_seconds=120),
        JobSpec("community.summary.daily", "Daily summary", "Summarise yesterday for each chat", s.job_summary_daily_seconds, summary_daily, initial_delay_seconds=180),
        JobSpec("community.memories", "Memory extraction", "Propose community memories from summaries (for review)", s.job_memory_extraction_seconds, memories, initial_delay_seconds=240),
        JobSpec("content.opportunities", "Content opportunities", "Turn rising topics and recurring questions into content ideas", s.job_opportunities_seconds, opportunities, initial_delay_seconds=300),
        JobSpec("knowledge.reindex", "Knowledge indexing", "Embed knowledge that is not yet searchable by meaning", s.job_knowledge_reindex_seconds, reindex, initial_delay_seconds=10),
    ]

    # --- Phase 3B/4A ----------------------------------------------------------------------------

    async def x_ingest(ctx: JobContext) -> dict[str, Any]:
        if not x_credentials(s).configured:
            raise JobSkipped("X is not configured (add credentials on X › Connection)")
        state = get_connector_state(ctx.db, Platform.X)
        if state.paused:
            raise JobSkipped("X reads are paused")
        if state.blocked_until and state.blocked_until > ctx.now:
            raise JobSkipped(f"X rate limit / usage cap: waiting until {state.blocked_until:%H:%M} UTC")
        if not state.capabilities:
            raise JobSkipped("Run 'Test connection' on X › Connection first")
        try:
            report = await ingest_x(ctx.db, s, runtime.x, runtime.embedder)
        except XAuthError as exc:
            state = get_connector_state(ctx.db, Platform.X)
            state.status, state.last_error, state.last_error_at = "error", str(exc), utcnow()
            runtime.x.status, runtime.x.detail = "error", str(exc)
            ctx.db.commit()
            ActivityLogger(ctx.db).log("x_error", f"X connection problem: {exc}", level=ActivityLevel.ERROR)
            raise
        if report.new_posts:
            then("x.opportunities", 10)
            then("cross.signals", 20)
            ActivityLogger(ctx.db).log("x_ingested", f"Read {report.new_posts} new X post(s) from your monitoring rules, watchlist, mentions and own posts")
        if report.stopped:
            ActivityLogger(ctx.db).log("x_ingest_limited", f"X reading stopped early: {report.stopped}", level=ActivityLevel.WARNING)
        return report.as_dict()

    async def x_own(ctx: JobContext) -> dict[str, Any]:
        """Only Price Chat's own new X posts (one small incremental read): what Read & Earn shows members. Runs where the
        website's database is (production), even when the full X reading runs on another machine."""
        if not x_credentials(s).configured:
            raise JobSkipped("X is not configured (add credentials on X › Connection)")
        state = get_connector_state(ctx.db, Platform.X)
        if state.paused:
            raise JobSkipped("X reads are paused")
        if state.blocked_until and state.blocked_until > ctx.now:
            raise JobSkipped(f"X rate limit / usage cap: waiting until {state.blocked_until:%H:%M} UTC")
        if not state.capabilities:
            raise JobSkipped("Run 'Test connection' on X › Connection first")
        report = await ingest_x(ctx.db, s, runtime.x, runtime.embedder, only_own=True)
        return report.as_dict()

    async def newsdesk_tick(ctx: JobContext) -> dict[str, Any]:
        from app.newsdesk import engine as desk

        return await desk.tick(ctx.db, s, runtime.x, get_agent_config(ctx.db).id, ctx.now)

    async def newsdesk_metrics(ctx: JobContext) -> dict[str, Any]:
        from app.newsdesk import engine as desk

        return await desk.collect_metrics(ctx.db, s, runtime.x, ctx.now)

    async def x_opps(ctx: JobContext) -> dict[str, Any]:
        agent_id = get_agent_config(ctx.db).id
        report = await detect_x_opportunities(
            ctx.db, runtime.router(ctx.db), agent_id, build_retriever(ctx.db, runtime, agent_id), s,
            runtime.embedder.model if runtime.embedder else None,
        )
        if report.created:
            ActivityLogger(ctx.db, agent_id).log(
                "x_opportunities_detected", f"Found {report.created} X opportunit{'y' if report.created == 1 else 'ies'} (suggestions only; nothing is posted)",
                details={"ids": report.ids},
            )
        return {"screened": report.screened, "relevant": report.relevant, "created": report.created, "set_aside": report.set_aside,
                "model_calls": report.model_calls, "salvaged": report.salvaged, "unscreened": report.unscreened,
                "advice_failed": report.advice_failed}

    async def cross(ctx: JobContext) -> dict[str, Any]:
        report = await detect_cross_signals(ctx.db, runtime.router(ctx.db), runtime.embedder.model if runtime.embedder else None)
        if report.new:
            ActivityLogger(ctx.db).log("cross_signal_detected", f"{report.new} topic(s) are now active in both Telegram and X (informational signal)")
            then("opportunities.rank", 10)
        return {"topics_checked": report.topics_checked, "active": report.active, "new": report.new, "stale": report.stale}

    async def merges(ctx: JobContext) -> dict[str, Any]:
        report = await suggest_merges(ctx.db, runtime.router(ctx.db))
        if report.suggested:
            ActivityLogger(ctx.db).log("topic_merge_suggested", f"{report.suggested} possible topic merge(s) to review on Community › Topics")
        return {"candidates": report.candidates, "suggested": report.suggested}

    async def rank(ctx: JobContext) -> dict[str, Any]:
        return {"ranked": await rank_opportunities(ctx.db, get_agent_config(ctx.db).id, runtime.embedder, ctx.now)}

    async def expire(ctx: JobContext) -> dict[str, Any]:
        return {"expired": expire_pending(ctx.db)}

    async def youtube_sync(ctx: JobContext) -> dict[str, Any]:
        from app.youtube import service as yt

        if yt.mode(ctx.db, s) is None:
            raise JobSkipped("YouTube is not connected (YouTube page)")
        if not get_connector_state(ctx.db, Platform.YOUTUBE).account_external_id:
            raise JobSkipped("Choose the Price Chat YouTube channel first")
        paused = yt.quota_paused_until(ctx.db, ctx.now)
        if paused is not None:  # YouTube refuses every call until its quota resets: wait instead of failing every run
            raise JobSkipped(f"YouTube's daily API quota is used up; syncing resumes after {paused:%H:%M} UTC")
        return (await yt.sync(ctx.db, s, runtime.youtube)).as_dict()

    async def growth_snapshots(ctx: JobContext) -> dict[str, Any]:
        from app.analytics.snapshots import snapshot_telegram

        return await snapshot_telegram(ctx.db, s, runtime)

    async def combot_sync(ctx: JobContext) -> dict[str, Any]:
        from app.members import combot, reputation

        rs = reputation.settings(ctx.db)
        if rs.combot_mode != "api":
            raise JobSkipped("Combot API is not connected (Members › Reputation sources)")
        return await combot.sync(ctx.db, s, runtime.members_transport)

    async def members_verify(ctx: JobContext) -> dict[str, Any]:
        from app.api.routes.website import _links, _settings
        from app.members import missions, verified

        from app.members import referrals, telegram_missions
        from app.telegram.service import make_client

        crew_out: dict[str, Any] = {}
        if s.telegram_configured:  # what the bot can see of the announcement channel decides which missions are offered
            from app.members import crew

            tg = make_client(s, runtime.telegram.transport)
            try:
                await telegram_missions.check_channel(ctx.db, tg)
            except Exception as exc:  # Telegram unavailable: keep the last known state
                ctx.db.rollback()
                logger.warning("Announcement channel check failed: %s", exc)
            try:  # Invite your crew: friends who joined the community through a member's invite (paid after the check period)
                crew_out = await crew.verify(ctx.db, tg)
            except Exception as exc:  # noqa: BLE001
                ctx.db.rollback()
                logger.warning("Crew invite check failed: %s", exc)
            finally:
                await tg.aclose()
        gen = missions.generate(ctx.db, _links(ctx.db, _settings(ctx.db)).get("telegram"))
        out = {"missions": gen, "telegram": verified.telegram_participation(ctx.db), "telegram_messages": verified.telegram_message_xp(ctx.db),
               "x": verified.x_interactions(ctx.db),
               "referrals": referrals.sweep(ctx.db), "crew": crew_out}
        if x_credentials(s).configured:
            from app.social.x_service import make_x_client

            client = make_x_client(ctx.db, s, runtime.x)
            try:
                out["reposts"] = await missions.verify_reposts(ctx.db, client)
            except Exception as exc:  # budget reached, X unavailable...: reported, never fatal for the other checks
                out["reposts"] = {"error": str(exc)[:200]}
            finally:
                await client.aclose()
        return out

    specs += [
        JobSpec("combot.sync", "Combot reputation sync", "Refresh historical Telegram reputation from Combot's official API (if connected)",
                s.job_combot_sync_seconds, combot_sync, initial_delay_seconds=300, timeout_seconds=600),
        JobSpec("members.verify", "Kiwi XP: missions & verified activity",
                "Create missions from new Price Chat content; award XP for linked members' verified activity (authorised Telegram chats, ingested X posts)",
                s.job_members_verify_seconds, members_verify, initial_delay_seconds=120, timeout_seconds=300),
        JobSpec("x.ingest", "X reading", "Read own posts, mentions, monitoring rules and watched accounts (read-only, budgeted)", s.job_x_ingest_seconds, x_ingest, initial_delay_seconds=60, timeout_seconds=300),
        JobSpec("newsdesk.tick", "News Desk", "Read news and prices, verify and queue posts for X, publish what's due (dry run until switched live in X › News Desk)",
                s.job_newsdesk_seconds, newsdesk_tick, initial_delay_seconds=120, timeout_seconds=240),
        JobSpec("newsdesk.metrics", "News Desk: post metrics", "Read impressions, likes and reposts of the last 3 days' posts (X charges per post read)",
                86400, newsdesk_metrics, initial_delay_seconds=1800, timeout_seconds=120),
        JobSpec("x.own", "X: Price Chat's own posts", "Read Price Chat's own new X posts for Read & Earn (one incremental request, budgeted)",
                s.job_x_own_seconds, x_own, initial_delay_seconds=90, timeout_seconds=120),
        JobSpec("x.opportunities", "X opportunities", "Find X posts where Price Chat could add value (suggestions only)", s.job_x_opportunities_seconds, x_opps, initial_delay_seconds=360),
        JobSpec("cross.signals", "Cross-platform signals", "Spot topics active on both Telegram and X", s.job_cross_signals_seconds, cross, initial_delay_seconds=400),
        JobSpec("topic.merges", "Topic merge suggestions", "Suggest topics that may be the same (you decide)", s.job_topic_merge_seconds, merges, initial_delay_seconds=500),
        JobSpec("opportunities.rank", "Opportunity ranking", "Re-rank content opportunities with transparent signals and your feedback", s.job_opportunity_rank_seconds, rank, initial_delay_seconds=90),
        JobSpec("approvals.expire", "Approval expiry", "Expire proposed actions nobody decided on within 48 hours", s.job_approvals_expire_seconds, expire, initial_delay_seconds=30),
        JobSpec("youtube.sync", "YouTube sync", "Find new uploads on the Price Chat YouTube channel and update the website (read-only)",
                s.job_youtube_sync_seconds, youtube_sync, initial_delay_seconds=45, timeout_seconds=180),
        JobSpec("growth.snapshots", "Growth snapshots", "Record Telegram member and message counts for the growth dashboard (free API)",
                TELEGRAM_SNAPSHOT_SECONDS, growth_snapshots, initial_delay_seconds=150, timeout_seconds=120),
    ]

    # --- Pokémon Vintage Market Cap ------------------------------------------------------------------------------
    async def pokemon_snapshot(ctx: JobContext) -> dict[str, Any]:
        import asyncio

        from sqlalchemy import func as sa_func

        from app.models.pokemon import PokemonPopulation, PokemonSale
        from app.pokemon import service as pokemon

        if not (ctx.db.scalar(select(sa_func.count()).select_from(PokemonSale)) or ctx.db.scalar(select(sa_func.count()).select_from(PokemonPopulation))):
            raise JobSkipped("No Pokémon sales or population data yet (Pokémon › Data sources)")
        return {"snapshots": await asyncio.to_thread(pokemon.snapshot, ctx.db, ctx.now)}  # off the event loop: the API keeps answering

    async def pokemon_sync(ctx: JobContext) -> dict[str, Any]:
        """Licensed provider syncs (eBay listings / sold items if granted, PriceCharting reference prices). Each provider on its own."""
        from app.pokemon.sync import sync_all
        from app.providers.pokemon import PROVIDERS

        if not any(PROVIDERS[k](s).status()[0] for k in ("ebay", "pricecharting")):
            raise JobSkipped("No eBay or PriceCharting credentials (Pokémon › Data sources). Manual imports work without them.")
        out = await sync_all(ctx.db, s, getattr(runtime, "pokemon_transport", None))
        # a provider that failed must not hide behind the job's "success": name it in the result and the log
        problems = {k: (v.get("error") or v.get("skipped")) for k, v in out.items() if isinstance(v, dict) and (v.get("error") or v.get("skipped"))}
        if problems:
            logger.warning("Pokémon sync: some providers did not sync: %s", "; ".join(f"{k}: {v}" for k, v in problems.items())[:500])
        return {**out, "problems": problems}

    async def pokemon_pulse(ctx: JobContext) -> dict[str, Any]:
        """Market Pulse sidebar: one budget-checked X recent search, only when an admin enabled auto-refresh."""
        from app.pokemon import pulse
        from app.security.credentials import x_credentials
        from app.social.x_service import make_x_client

        if not pulse.config(ctx.db).enabled:
            raise JobSkipped("Market Pulse auto-refresh is off (Pokémon Market › Market Pulse ⚙)")
        if not x_credentials(s).app_only:
            raise JobSkipped("X is not connected")
        client = make_x_client(ctx.db, s, runtime.x)
        try:
            out = await pulse.refresh(ctx.db, client, ctx.now, force=True)
        finally:
            await client.aclose()
        if not out.get("ok"):
            raise JobSkipped(str(out.get("error")))
        return out

    async def pokemon_catalog(ctx: JobContext) -> dict[str, Any]:
        """Whole catalogue from Scrydex, refreshed weekly: each hourly run does the sets that are due (a few dozen at most)."""
        from app.pokemon import bulk
        from app.providers.pokemon import PROVIDERS

        if not PROVIDERS["scrydex"](s).status()[0]:
            raise JobSkipped("Scrydex isn't connected (Pokémon › Data sources)")
        out = await bulk.sync(ctx.db, s, max_sets=s.pokemon_catalog_sets_per_run, transport=getattr(runtime, "pokemon_transport", None),
                              max_requests=s.pokemon_catalog_requests_per_run)
        if not out["summary"]["sets"] and not out["error"]:
            raise JobSkipped("Every set was refreshed in the last 7 days")
        return out["summary"]

    async def pokemon_warm(ctx: JobContext) -> dict[str, Any]:
        """Keep the market's PSA 10 rows (the homepage) computed, so no visitor waits for the ~50k-card build."""
        import asyncio

        from app.pokemon import market

        def warm() -> int:
            rows = market.base_rows(ctx.db, market.DEFAULT_GRADE, ctx.now)
            market.market(ctx.db, market.DEFAULT_GRADE, None, "", 100, 0, ctx.now, view="top100")  # the homepage's default view
            return len(rows)

        # seconds of CPU: in a worker thread, so the event loop (every API request, the job timeout) keeps running meanwhile
        return {"rows": await asyncio.to_thread(warm)}

    async def pokemon_market_expire(ctx: JobContext) -> dict[str, Any]:
        """Card Marketplace: testnet payments waiting for confirmations are checked again (read-only chain calls, only when
        the testnet is on); unpaid reservations past their time go back on sale; expired offers close."""
        from app.market import service as market_service

        rechecked = market_service.recheck_pending(ctx.db, s)
        return {**market_service.expire(ctx.db, ctx.now), "testnet_rechecked": rechecked}

    async def pokemon_exchange(ctx: JobContext) -> dict[str, Any]:
        """Paper Exchange (paper trading only): the reference oracle every 15 min, the simulated market maker re-quotes
        every minute, members' equity snapshot once a day. Light: a few queries per market, no API calls."""
        from sqlalchemy import func, select

        from app.exchange import ledger as ex_ledger, maker as ex_maker, oracle as ex_oracle, views as ex_views
        from app.models.exchange import ExEquitySnapshot, ExMarket

        if not s.exchange_enabled:
            raise JobSkipped("The Paper Exchange is retired (EXCHANGE_ENABLED is off); its data is kept")
        ex_ledger.ensure_markets(ctx.db)
        out: dict[str, Any] = {}
        oldest = ctx.db.scalar(select(func.min(ExMarket.ref_at)))
        if oldest is None or (ctx.now - oldest).total_seconds() >= 900:
            out["oracle"] = ex_oracle.refresh_all(ctx.db, ctx.now)
        out["maker"] = {k: v.get("placed", 0) for k, v in ex_maker.requote_all(ctx.db, ctx.now).items()}
        from app.exchange import perps as ex_perps

        out["perps"] = {k: {"placed": v["placed"], "funding": (v["funding"] or {}).get("rate_ppm"), "unwound": v["unwound"]}
                        for k, v in ex_perps.tick(ctx.db, ctx.now).items()}
        if ctx.db.scalar(select(ExEquitySnapshot.id).where(ExEquitySnapshot.day == ctx.now.date()).limit(1)) is None:
            out["equity_snapshots"] = ex_views.snapshot_equity(ctx.db, ctx.now)
        return out

    async def pokemon_exchange_auto(ctx: JobContext) -> dict[str, Any]:
        """Paper Exchange: the owner's SIMULATED AUTO-TRADER (off by default; labelled SIM). One settings read when off."""
        from app.exchange import auto as ex_auto, maker as ex_maker

        pruned = ex_maker.prune(ctx.db, ctx.now)  # always: the maker's dead quotes must never fill the volume again
        if not s.exchange_enabled:
            return {"retired": True, "pruned_quotes": pruned}
        return {**ex_auto.tick(ctx.db, ctx.now), "pruned_quotes": pruned}

    async def brand_tick(ctx: JobContext) -> dict[str, Any]:
        """Brand Manager: plans, publishes and measures for every brand with AUTO on (light: no AI, no rendering here)."""
        from app.brand_manager import engine as bm_engine

        return await bm_engine.tick(ctx.db, s, transport=getattr(runtime, "bm_transport", None), now=ctx.now)

    async def brand_trends(ctx: JobContext) -> dict[str, Any]:
        """Brand Manager trend scan: counts the words finance and crypto headlines use today vs the past week (no AI)."""
        from app.brand_manager import trends as bm_trends

        return await bm_trends.refresh(ctx.db, ctx.now, transport=getattr(runtime, "bm_transport", None))

    async def lunch_bot(ctx: JobContext) -> dict[str, Any]:
        """Earn › Arbitrage › Lunch Bot: paper trades pump.fun launches at real on-chain prices (no keys, no transactions)."""
        from app.arbitrage import lunch, lunch_stream

        if not s.lunch_bot_enabled:
            lunch_stream.stop()  # its live feed and fast check stop with it
            raise JobSkipped("Lunch Bot is off (LUNCH_BOT_ENABLED)")
        try:
            return await lunch.tick(ctx.db, ctx.now, s.solana_rpc_url, transport=getattr(runtime, "lunch_transport", None), stream=True,
                                    stream_key=s.pumpportal_api_key.get_secret_value() if s.pumpportal_api_key else None)
        except Exception as exc:
            lunch.record_error(exc)
            raise

    async def autodelete(ctx: JobContext) -> dict[str, Any]:
        """Main chat: delete the bot's own messages a few minutes after they were sent (never Bot Chat)."""
        from app.telegram.service import make_client
        from app.tradingos import autodelete as ad

        if not s.telegram_configured:
            raise JobSkipped("Telegram is not configured")
        tg = make_client(s, runtime.telegram.transport)
        try:
            return await ad.run(ctx.db, tg, ctx.now)
        finally:
            await tg.aclose()

    async def vortex_expire(ctx: JobContext) -> dict[str, Any]:
        """Vortex trade checks: placeholders still unanswered after 60 s become the bot's own assessment."""
        from app.telegram.service import make_client
        from app.tradingos import assist

        if not assist.stale(ctx.db, ctx.now):
            return {"expired": 0}
        if not s.telegram_configured:
            raise JobSkipped("Telegram is not configured")
        tg = make_client(s, runtime.telegram.transport)
        try:
            return {"expired": await assist.expire(ctx.db, tg, ctx.now)}
        finally:
            await tg.aclose()

    async def committee_outcomes(ctx: JobContext) -> dict[str, Any]:
        """Kiwi AI Investment Committee: score finished runs at 1h / 4h / 24h / 7d from Hyperliquid candles (read-only)."""
        from app.committee import outcomes

        return await outcomes.resolve(ctx.db, ctx.now)

    async def committee_paper(ctx: JobContext) -> dict[str, Any]:
        """Kiwi AI Investment Committee: book finished LONG / SHORT runs in the PAPER book and replay them on real candles."""
        from app.committee import paper

        return await paper.run_job(ctx.db, ctx.now)

    async def committee_llm_expire(ctx: JobContext) -> dict[str, Any]:
        """Kiwi AI Investment Committee: LLM calls Vortex didn't answer within 90 s are expired (their roles use rules); runs
        stuck in queued / running (10 min, or left by a previous process: so also right after a start) are failed as
        "interrupted by a restart" and their Telegram placeholder says so."""
        from app.committee import orchestrator, remote
        from app.tradingos import committeecmd

        out: dict[str, Any] = {"expired": remote.expire(ctx.db, ctx.now)}
        if orchestrator.stuck(ctx.db, ctx.now):
            client = None
            if s.telegram_configured:
                from app.telegram.service import make_client

                client = make_client(s, runtime.telegram.transport)
            try:
                out["interrupted"] = await committeecmd.recover(ctx.db, client, ctx.now)
            finally:
                if client is not None:
                    await client.aclose()
        return out

    async def funding_alerts(ctx: JobContext) -> dict[str, Any]:
        """Earn › Funding Arb: post wide cross-venue funding spreads (net of estimated fees) to the publish chat. Read-only."""
        from app.arbitrage import funding_alerts as fa
        from app.telegram.service import make_client

        reason = fa.inactive_reason(ctx.db)
        if reason:
            raise JobSkipped(reason)
        if not s.telegram_configured:
            raise JobSkipped("Telegram is not configured")
        tg = make_client(s, runtime.telegram.transport)
        try:
            return await fa.run(ctx.db, ctx.now, tg)
        finally:
            await tg.aclose()

    async def vault_sync(ctx: JobContext) -> dict[str, Any]:
        """Price Chat Vault: each enabled eBay seller's listings through the Browse API (budgeted), so the storefront's
        prices and availability stay within eBay's 6-hour freshness rule."""
        from sqlalchemy import select as _select

        from app.models.vault import VaultSource
        from app.providers.pokemon import PROVIDERS
        from app.vault import service as vault_service
        from app.vault.sync import sync_ebay_seller

        vault_service.ensure_sources(ctx.db, s)
        if not PROVIDERS["ebay"](s).status()[0]:
            raise JobSkipped("eBay isn't connected (Pokémon › Data sources: add the eBay App ID and Cert ID)")
        out = {}
        for src in ctx.db.scalars(_select(VaultSource).where(VaultSource.kind == "ebay_seller", VaultSource.enabled.is_(True))).all():
            r = await sync_ebay_seller(ctx.db, s, src, transport=getattr(runtime, "pokemon_transport", None), now=ctx.now)
            out[src.key] = {k: r.get(k) for k in ("status", "seen", "new", "eligible", "ended", "calls")}
        return out

    async def invites_poll(ctx: JobContext) -> dict[str, Any]:
        """Invite Bot: read new messages in approved groups and private chats (activity, opt-ins, opt-outs). Sends nothing."""
        from app.invites import service as invites_service

        out = await invites_service.poll(ctx.db, s)
        return {**out, "auto_started": invites_service.maybe_auto_send(ctx.db, s)}  # Auto mode: send to whoever just asked

    async def pokemon_portfolios(ctx: JobContext) -> dict[str, Any]:
        """Pokémon Portfolio: fetch current Scrydex prices (incl. raw conditions) for held cards that have none yet (capped
        credits), then re-price every portfolio and record today's snapshot. Batched: bounded memory."""
        from sqlalchemy import select as sel

        from app.models.portfolio import PokemonHolding
        from app.pokemon import scrydex_sync
        from app.portfolio import service as portfolio

        from app.portfolio import prices

        missing = sorted(set(ctx.db.scalars(sel(PokemonHolding.card_id).where(
            PokemonHolding.removed_at.is_(None), (PokemonHolding.value_reason.in_(list(prices.NEEDS_FETCH))) | (PokemonHolding.value_source == "none")))))
        fetched: dict[str, int] = {}
        for cid in missing[:100]:  # at most 100 cards a run; each at most once a day (prices.ensure remembers)
            r = await prices.ensure(ctx.db, s, cid, None, getattr(runtime, "pokemon_transport", None))
            fetched[r] = fetched.get(r, 0) + 1
        return {"fetched": fetched, **portfolio.refresh_all(ctx.db, ctx.now)}

    async def pokemon_top100(ctx: JobContext) -> dict[str, Any]:
        """Reassess Top 100 eligibility after data refreshes; fetch Scrydex history for near-misses (capped credits); snapshot."""
        from app.pokemon import top100
        from app.providers.pokemon import PROVIDERS

        import asyncio

        improved = None
        if PROVIDERS["scrydex"](s).status()[0]:
            improved = await top100.improve(ctx.db, s, transport=getattr(runtime, "pokemon_transport", None))
        return {"improve": improved, **await asyncio.to_thread(top100.record, ctx.db, ctx.now)}  # may rebuild the 50k rows: off the loop

    async def pokemon_sales(ctx: JobContext) -> dict[str, Any]:
        """Weekly: the last 10 days of Scrydex sold listings for the Top 100 and its nearest candidates -> verified sales."""
        from app.pokemon import scrydex_sales
        from app.providers.pokemon import PROVIDERS

        if not PROVIDERS["scrydex"](s).status()[0]:
            raise JobSkipped("Scrydex isn't connected (Pokémon › Data sources)")
        out = await scrydex_sales.sync(ctx.db, s, days=10, transport=getattr(runtime, "pokemon_transport", None), max_requests=s.ebay_cards_per_sync + 50)
        return out["summary"]

    async def system_backup(ctx: JobContext) -> dict[str, Any]:
        import asyncio

        from app.services import db_backup

        url = ctx.db.get_bind().url
        if url.get_backend_name() != "sqlite" or not url.database:
            raise JobSkipped("Only a SQLite database file is backed up this way")
        if not s.portfolio_bucket:
            raise JobSkipped("No storage bucket configured (PORTFOLIO_BUCKET): the backup has nowhere off the volume to go")
        return await asyncio.to_thread(db_backup.run, s, url.database)

    async def system_alerts(ctx: JobContext) -> dict[str, Any]:
        """Owner alerts that aren't a failed job: a Brand Manager TikTok sign-in lost / publishing stalled while AUTO is on (app/alerts.py)."""
        from app import alerts

        return await alerts.check_brand_accounts(ctx.db, settings=s, transport=runtime.telegram.transport, now=ctx.now)

    async def system_retention(ctx: JobContext) -> dict[str, Any]:
        """Delete rows past their retention window from tables that only grow (app/retention.py)."""
        import asyncio

        from app import retention

        return await asyncio.to_thread(retention.run, ctx.db, ctx.now)

    async def vpn_sync(ctx: JobContext) -> dict[str, Any]:
        """Kiwi VPN: each enabled server's agent status, usage, missing / unknown peers, revoked members (app/vpn/service.py)."""
        import asyncio

        from app.models.vpn import VpnServer
        from app.vpn import service as vpn

        from app import alerts

        if not ctx.db.scalar(select(func.count()).select_from(VpnServer).where(VpnServer.enabled.is_(True))):
            raise JobSkipped("No Kiwi VPN servers yet (Admin › Kiwi VPN)")
        out = await asyncio.to_thread(vpn.sync_all, ctx.db, ctx.now)
        # the owner hears once when a server goes offline / comes back / passes 90% of its capacity
        return {**out, **await alerts.check_vpn_servers(ctx.db, settings=s, transport=runtime.telegram.transport, now=ctx.now)}

    specs += [
        JobSpec("vpn.sync", "Kiwi VPN: server sync", "Every 5 min: reads each VPN server's agent (online, handshakes, this month's bytes), re-adds members' missing peers, removes peers nobody owns and revokes devices of members who no longer qualify (rank dropped, banned). No traffic logs",
                300, vpn_sync, initial_delay_seconds=100, timeout_seconds=240),
        JobSpec("system.alerts", "Owner alerts", "Every 10 min: tells the owner on Telegram (Settings › Background jobs › Owner alerts) when a Brand Manager TikTok account is expired or disconnected while AUTO is on, or Brand Manager stops publishing by itself (AUTO blocked or failing, videos past their posting time, failed sends); failing jobs are reported as they happen",
                600, system_alerts, initial_delay_seconds=240, timeout_seconds=60),
        JobSpec("system.backup", "Nightly database backup", "Daily: a consistent copy of the whole database, integrity-checked, gzip-compressed into the private storage bucket (separate from the server's volume); the newest 14 are kept",
                86400, system_backup, initial_delay_seconds=900, timeout_seconds=1800),
        JobSpec("system.retention", "Data retention", "Daily: deletes old rows from tables that only grow (Brand Manager log and metrics, referral clicks, skipped /signal ideas, finished price alerts, activity log, expired member sessions, read notifications, card-scan records, Pokémon sync run logs, Exchange oracle history thinned to a day's last, usage tallies), at most 5,000 per table a run; anything still in use or counted in all-time stats is kept (app/retention.py)",
                86400, system_retention, initial_delay_seconds=1500, timeout_seconds=600),
        JobSpec("pokemon.sales", "Pokémon verified sales (Scrydex sold listings)", "Weekly: completed sales of the Top 100 and nearby cards, each matched to the exact card + grade (~150 credits)",
                7 * 86400, pokemon_sales, initial_delay_seconds=2400, timeout_seconds=900),
        JobSpec("pokemon.top100", "Pokémon Top 100 assessment", "Every 6 h: re-checks every vintage card's PSA 10 data quality, fills missing Scrydex history for the closest candidates (capped credits) and snapshots the Top 100",
                21600, pokemon_top100, initial_delay_seconds=1800, timeout_seconds=900),
        JobSpec("pokemon.portfolios", "Pokémon Portfolio valuations", "Every 6 h: re-prices every portfolio from the market's data, fetches prices for held cards that have none (≤100 credits), records today's value snapshot",
                21600, pokemon_portfolios, initial_delay_seconds=900, timeout_seconds=900),
        JobSpec("pokemon.market_expire", "Card Marketplace expiries", "Every 2 min: releases unpaid reservations and closes expired offers",
                120, pokemon_market_expire, initial_delay_seconds=60, timeout_seconds=120),
        JobSpec("brand.tick", "Brand Manager automation", "Every minute: plans TikTok videos for the next posting slots (topic discovery + research), queues them for the AI workers, sends finished videos to TikTok through the authorized flow, checks TikTok's processing status, collects analytics every 6 h",
                s.bm_tick_seconds, brand_tick, initial_delay_seconds=75, timeout_seconds=170),
        JobSpec("tradingos.lunch_bot", "Earn › Arbitrage › Lunch Bot (paper)", "Every minute: reads pump.fun's newest launches and their bonding curves on Solana, paper-buys launches with early momentum and closes them at graduation, target, stop or timeout at real on-chain prices; every 10 runs measures the curve-to-pool price gap of recent graduations. Never signs or sends a transaction",
                60, lunch_bot, initial_delay_seconds=90, timeout_seconds=55),
        JobSpec("tradingos.autodelete", "Trading OS: main-chat tidy-up", "Every minute: deletes the bot's own messages in the main chat a few minutes after they were sent (Admin › Trading OS; 0 = off). Bot Chat and private chats are never touched",
                60, autodelete, initial_delay_seconds=50, timeout_seconds=50),
        JobSpec("tradingos.vortex_expire", "Trading OS: Vortex trade-check expiry", "Every 20 s: a /help <trade> check Vortex hasn't answered within 60 s gets the bot's own assessment instead (a late answer from Vortex is then ignored)",
                20, vortex_expire, initial_delay_seconds=30, timeout_seconds=18),
        JobSpec("committee.outcomes", "Kiwi AI Investment Committee: outcomes", "Every 15 min: what the market did 1 h / 4 h / 24 h / 7 d after each committee verdict, measured from the first Hyperliquid candle close after it (the public track record). Read-only, never trades",
                900, committee_outcomes, initial_delay_seconds=420, timeout_seconds=300),
        JobSpec("committee.llm_expire", "Kiwi AI Investment Committee: Vortex call expiry", "Every 30 s: committee LLM calls Vortex hasn't answered within 90 s are expired; those roles use the committee's own rules text",
                30, committee_llm_expire, initial_delay_seconds=45, timeout_seconds=25),
        JobSpec("committee.paper", "Kiwi AI Investment Committee: paper book", "Every 5 min: books each finished LONG / SHORT committee run in the committee's PAPER book (risk-gate approved: a paper trade; rejected: kept with a hypothetical result) and replays the trades on real Hyperliquid candles (entry zone, stop first, TP1 half / TP2 rest, fees, slippage, real funding). Simulated only, never trades",
                300, committee_paper, initial_delay_seconds=480, timeout_seconds=240),
        JobSpec("tradingos.funding_alerts", "Earn › Funding Arb alerts", "Every 10 min: when a coin's best cross-venue funding spread, net of estimated taker fees on both legs, clears the threshold (Admin › Trading OS), posts the widest one to the publish chat; 24 h cooldown per coin unless the spread doubles, at most 4 a day, off unless turned on. Read-only, never trades",
                600, funding_alerts, initial_delay_seconds=240, timeout_seconds=120),
        JobSpec("brand.trends", "Brand Manager trend scan", "Every 30 min: reads today's headlines from crypto and finance outlets (CoinDesk, Cointelegraph, Decrypt, The Block, CNBC, MarketWatch, FT, BBC Business) and CoinGecko's most-searched coins, and counts which words are new or rising against the past 7 days, for Content Studio's topics and scripts",
                1800, brand_trends, initial_delay_seconds=120, timeout_seconds=120),
        JobSpec("vault.sync", "Price Chat Vault: eBay seller sync", "Every 3 h: reads each approved seller's Japanese fixed-price listings through eBay's Browse API (within a daily call budget), classifies vintage cards for review, and re-checks approved listings' price and availability",
                s.vault_sync_seconds, vault_sync, initial_delay_seconds=180, timeout_seconds=600),
        JobSpec("pokemon.exchange", "Pokémon Paper Exchange", "Every minute: the simulated market maker re-quotes spot and perps around the reference, perp funding hourly, liquidations and the insurance fund's unwind; the reference oracle every 15 min; members' equity snapshot daily (paper trading only, no API calls)",
                60, pokemon_exchange, initial_delay_seconds=45, timeout_seconds=55),
        JobSpec("pokemon.exchange.auto", "Exchange auto-trader", "Every 15 s while the owner has it on: moves each card's simulated price path and the labelled SIMULATED AUTO-TRADER takes a few of the maker's quotes so charts form (paper trading only; off by default)",
                15, pokemon_exchange_auto, initial_delay_seconds=30, timeout_seconds=14),
        JobSpec("invites.poll", "Invite Bot monitor", "Every 3 min: reads approved groups and private chats for activity, opt-ins and opt-outs; in Auto mode starts sending to people who asked",
                180, invites_poll, initial_delay_seconds=90, timeout_seconds=170),
        JobSpec("pokemon.warm", "Pokémon market cache", "Every 10 min: keeps the market homepage's rows computed (no API calls)",
                600, pokemon_warm, initial_delay_seconds=20, timeout_seconds=300),
        JobSpec("pokemon.catalog", "Pokémon catalogue refresh (Scrydex)", "Hourly: refreshes the sets whose prices are older than 7 days (all English + Japanese cards, ~480 credits a week)",
                s.job_pokemon_catalog_seconds, pokemon_catalog, initial_delay_seconds=1200, timeout_seconds=900),
        JobSpec("pokemon.pulse", "Pokémon Market Pulse (X)", "Every ~15 min when enabled: one budget-checked X search for card-market posts (sidebar only, never prices)",
                s.job_pokemon_pulse_seconds, pokemon_pulse, initial_delay_seconds=300, timeout_seconds=60),
        JobSpec("pokemon.snapshot", "Pokémon price snapshots", "Once a day: each card + grade's market price, confidence, population, est. market cap and volume (Price Chat's own history)",
                s.job_pokemon_snapshot_seconds, pokemon_snapshot, initial_delay_seconds=600, timeout_seconds=300),
        JobSpec("pokemon.sync", "Pokémon data sync", "Import sales / prices / population from connected, licensed Pokémon data providers",
                s.job_pokemon_sync_seconds, pokemon_sync, initial_delay_seconds=900, timeout_seconds=600),
    ]
    for spec in specs:
        spec.describe = JOB_SUMMARIES.get(spec.name)
    return specs


# --- plain-English activity lines for finished jobs (only when something actually happened) -------------
# Jobs that already log their own result (Telegram/X reading, summaries, memories, opportunities) are not listed.


def _n(result: dict[str, Any], key: str) -> int:
    value = result.get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _x_opps(r: dict[str, Any]) -> tuple[str, str] | None:
    if not _n(r, "screened"):
        return None
    return "x_posts_analysed", (
        f"Screened {_plural(_n(r, 'screened'), 'X post')}: {_n(r, 'relevant')} relevant, "
        f"{_plural(_n(r, 'created'), 'new opportunity', 'new opportunities')}"
    )


JOB_SUMMARIES: dict[str, Callable[[dict[str, Any]], tuple[str, str] | None]] = {
    "community.process": lambda r: (
        "telegram_messages_processed",
        f"Processed {_plural(_n(r, 'processed'), 'Telegram message')} ({_plural(_n(r, 'questions'), 'question')}, {_plural(_n(r, 'new_topics'), 'new topic')})",
    ) if _n(r, "processed") else None,
    "community.topics": lambda r: ("topics_detected", f"{_plural(_n(r, 'newly_active'), 'topic')} became active") if _n(r, "newly_active") else None,
    "x.opportunities": _x_opps,
    "cross.signals": lambda r: ("cross_signals_detected", f"Detected {_plural(_n(r, 'new'), 'new cross-platform signal')}") if _n(r, "new") else None,
    "topic.merges": lambda r: ("merge_suggested", f"Suggested {_plural(_n(r, 'suggested'), 'possible topic merge')}") if _n(r, "suggested") else None,
    "system.backup": lambda r: ("database_backed_up", r["summary"]) if r.get("summary") else None,
}
