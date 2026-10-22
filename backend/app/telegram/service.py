"""Telegram source management, status and data deletion."""

import logging

import httpx
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.community.questions import refresh_question_stats
from app.community.topics import refresh_topic_stats
from app.community.tuning import CommunityTuning
from app.config.settings import Settings
from app.security.credentials import telegram_token
from app.db.base import utcnow
from app.models.community import CommunitySummary, ContentOpportunity, ProvenanceLink
from app.models.enums import ActivityLevel, DerivedKind, OpportunityStatus
from app.models.memory import Memory
from app.models.telegram import TelegramBotState, TelegramMessage, TelegramSource
from app.services.activity import ActivityLogger
from app.telegram.client import TelegramBotClient
from app.telegram.errors import TelegramError, TelegramNotConfiguredError
from app.telegram.ingestion import get_bot_state, looks_like_unsigned_group_id
from app.telegram.state import TelegramRuntime

logger = logging.getLogger(__name__)


def make_client(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> TelegramBotClient:
    token = telegram_token(settings)
    if token is None:
        raise TelegramNotConfiguredError("Telegram is not configured: add the bot token on the Community › Setup page.")
    return TelegramBotClient(token, settings.telegram_api_base, transport=transport)


@dataclass(slots=True)
class TelegramStatus:
    configured: bool
    status: str  # not_configured | connected | error | disconnected
    bot_username: str | None
    bot_name: str | None
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    messages_ingested: int
    ignored_updates: int
    sources: int
    enabled_sources: int


def telegram_status(db: Session, settings: Settings) -> TelegramStatus:
    state = db.scalar(select(TelegramBotState).limit(1))
    sources = list(db.scalars(select(TelegramSource)))
    configured = settings.telegram_configured
    status = "not_configured"
    if configured:
        status = state.status if state and state.status != "not_configured" else "disconnected"
    return TelegramStatus(
        configured=configured,
        status=status,
        bot_username=state.bot_username if state else None,
        bot_name=state.bot_name if state else None,
        last_sync_at=state.last_sync_at if state else None,
        last_success_at=state.last_success_at if state else None,
        last_error=state.last_error if state else None,
        last_error_at=state.last_error_at if state else None,
        messages_ingested=int(db.scalar(select(func.count()).select_from(TelegramMessage)) or 0),
        ignored_updates=state.ignored_updates if state else 0,
        sources=len(sources),
        enabled_sources=sum(1 for s in sources if s.enabled),
    )


def record_error(db: Session, error: TelegramError | Exception) -> None:
    """Persist the latest Telegram error for the dashboard and log transitions to Activity."""
    db.rollback()
    state = get_bot_state(db)
    message = str(error)
    first_failure = state.status != "error"
    state.status = "error"
    state.last_error = message
    state.last_error_at = utcnow()
    db.commit()
    if first_failure:
        ActivityLogger(db).log(
            "telegram_error",
            f"Telegram connection problem: {message}",
            level=ActivityLevel.ERROR,
            details={"error_type": getattr(error, "code", type(error).__name__)},
        )


def record_recovery(db: Session, was_error: bool, stored: int) -> None:
    if was_error:
        ActivityLogger(db).log("telegram_recovered", "Telegram connection is working again")
    if stored:
        ActivityLogger(db).log(
            "telegram_ingested", f"Received {stored} new message{'s' if stored != 1 else ''} from authorised Telegram chats"
        )


# --- allow-list ---------------------------------------------------------------------------


async def add_source(
    db: Session, settings: Settings, runtime: TelegramRuntime, chat_id: int, display_name: str | None, actor: str = "dashboard"
) -> TelegramSource:
    if looks_like_unsigned_group_id(chat_id):
        chat_id = -chat_id  # group ids are negative: "1004310183499" means -1004310183499
    if db.scalar(select(TelegramSource).where(TelegramSource.chat_id == chat_id)) is not None:
        raise ValueError("This chat is already on the allow-list")
    title, chat_type, username = display_name, None, None
    discovered = runtime.discovered.get(chat_id)
    if discovered:
        title = title or discovered.title
        chat_type = discovered.chat_type
    if settings.telegram_configured and not (title and chat_type):
        client = make_client(settings, runtime.transport)
        try:
            info = await client.get_chat(chat_id)
            title = title or info.get("title") or info.get("username") or str(chat_id)
            chat_type = info.get("type")
            username = info.get("username")
        except TelegramError as exc:
            logger.info("Could not look up chat %s: %s", chat_id, exc)
        finally:
            await client.aclose()
    source = TelegramSource(
        chat_id=chat_id,
        display_name=(title or str(chat_id))[:256],
        chat_type=chat_type,
        username=username,
        enabled=True,
        ingestion_enabled=True,
    )
    db.add(source)
    db.commit()
    runtime.forget(chat_id)
    ActivityLogger(db).log(
        "telegram_source_added",
        f'Authorised Telegram chat "{source.display_name}" ({chat_id}) for read-only ingestion',
        level=ActivityLevel.AUDIT,
        actor=actor,
        details={"chat_id": chat_id},
    )
    return source


def update_source(db: Session, source: TelegramSource, changes: dict[str, Any], actor: str = "dashboard") -> TelegramSource:
    for key in ("display_name", "enabled", "ingestion_enabled"):
        if key in changes and changes[key] is not None:
            setattr(source, key, changes[key])
    db.commit()
    ActivityLogger(db).log(
        "telegram_source_updated",
        f'Updated Telegram chat "{source.display_name}": '
        + ", ".join(f"{k} = {v}" for k, v in changes.items() if v is not None),
        level=ActivityLevel.AUDIT,
        actor=actor,
    )
    return source


# --- deletion ----------------------------------------------------------------------------------


@dataclass(slots=True)
class DeletionReport:
    messages: int = 0
    summaries: int = 0
    memories: int = 0
    opportunities: int = 0


def _delete_unsupported_derivations(db: Session, report: DeletionReport, deleted_message_ids: set[int]) -> None:
    """After messages are deleted, remove AI-derived records that no longer have any supporting message."""
    affected: dict[DerivedKind, set[int]] = {}
    for kind, derived_id in db.execute(
        select(ProvenanceLink.derived_kind, ProvenanceLink.derived_id).where(ProvenanceLink.message_id.in_(deleted_message_ids))
    ):
        affected.setdefault(kind, set()).add(derived_id)

    db.execute(delete(ProvenanceLink).where(ProvenanceLink.message_id.in_(deleted_message_ids)))
    db.flush()

    def orphaned(kind: DerivedKind) -> list[int]:
        ids = affected.get(kind, set())
        if not ids:
            return []
        still = set(db.scalars(select(ProvenanceLink.derived_id).where(ProvenanceLink.derived_kind == kind, ProvenanceLink.derived_id.in_(ids))))
        return [i for i in ids if i not in still]

    for mid in orphaned(DerivedKind.MEMORY):
        memory = db.get(Memory, mid)
        if memory is not None:
            db.delete(memory)
            report.memories += 1
    for sid in orphaned(DerivedKind.SUMMARY):
        summary = db.get(CommunitySummary, sid)
        if summary is not None:
            db.delete(summary)
            report.summaries += 1
    for oid in orphaned(DerivedKind.OPPORTUNITY):
        opp = db.get(ContentOpportunity, oid)
        if opp is not None and opp.status not in (OpportunityStatus.APPROVED, OpportunityStatus.DRAFTED):
            db.delete(opp)
            report.opportunities += 1


def delete_messages(db: Session, tuning: CommunityTuning, message_ids: list[int], actor: str = "dashboard", reason: str = "") -> DeletionReport:
    report = DeletionReport()
    messages = list(db.scalars(select(TelegramMessage).where(TelegramMessage.id.in_(message_ids))))
    if not messages:
        return report
    ids = {m.id for m in messages}
    for m in messages:
        source = db.get(TelegramSource, m.source_id)
        if source is not None:
            source.message_count = max(0, source.message_count - 1)
    _delete_unsupported_derivations(db, report, ids)
    db.execute(delete(TelegramMessage).where(TelegramMessage.id.in_(ids)))
    report.messages = len(ids)
    db.flush()
    refresh_topic_stats(db, tuning)
    refresh_question_stats(db)
    db.commit()
    ActivityLogger(db).log(
        "telegram_data_deleted",
        f"Deleted {report.messages} Telegram message(s)"
        + (f" and {report.memories} memories, {report.summaries} summaries, {report.opportunities} opportunities derived only from them" if report.memories or report.summaries or report.opportunities else "")
        + (f" ({reason})" if reason else ""),
        level=ActivityLevel.AUDIT,
        actor=actor,
        details={"messages": report.messages, "memories": report.memories, "summaries": report.summaries, "opportunities": report.opportunities},
    )
    return report


def delete_source(db: Session, tuning: CommunityTuning, source: TelegramSource, actor: str = "dashboard") -> DeletionReport:
    """Remove a chat from the allow-list and delete every piece of data that came from it."""
    name, chat_id = source.display_name, source.chat_id
    ids = list(db.scalars(select(TelegramMessage.id).where(TelegramMessage.source_id == source.id)))
    report = DeletionReport()
    if ids:
        _delete_unsupported_derivations(db, report, set(ids))
    summaries = db.execute(delete(CommunitySummary).where(CommunitySummary.source_id == source.id))
    report.summaries += summaries.rowcount or 0
    report.messages = len(ids)
    db.delete(source)  # cascades to messages and their vectors
    db.flush()
    refresh_topic_stats(db, tuning)
    refresh_question_stats(db)
    db.commit()
    ActivityLogger(db).log(
        "telegram_source_deleted",
        f'Removed Telegram chat "{name}" ({chat_id}) and deleted all its data: {report.messages} messages, '
        f"{report.summaries} summaries, {report.memories} memories, {report.opportunities} opportunities",
        level=ActivityLevel.AUDIT,
        actor=actor,
        details={
            "chat_id": chat_id,
            "messages": report.messages,
            "summaries": report.summaries,
            "memories": report.memories,
            "opportunities": report.opportunities,
        },
    )
    return report
