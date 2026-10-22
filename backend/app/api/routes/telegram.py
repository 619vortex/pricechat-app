from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.community_schemas import (
    DeletionOut,
    DiscoveredChatOut,
    JobRunOut,
    TelegramMessageOut,
    TelegramSourceIn,
    TelegramSourceOut,
    TelegramSourceUpdate,
    TelegramStatusOut,
)
from app.api.deps import DbSession, Runtime
from app.api.social_schemas import MessageTraceOut, SecretStatusOut, SetupStep, TelegramSetupOut, TelegramTokenIn, ToggleIn
from app.connectors.state import get_connector_state
from app.models.community import CommunityQuestion, CommunitySummary, CommunityTopic, ContentOpportunity, ProvenanceLink
from app.models.enums import ActivityLevel, Platform
from app.models.memory import Memory
from app.models.routing import ScheduledJob
from app.security import credentials
from app.services.activity import ActivityLogger
from app.telegram.client import TelegramBotClient
from app.telegram.ingestion import get_bot_state
from app.community.tuning import CommunityTuning
from app.models.telegram import TelegramMessage, TelegramSource
from app.services.runtime import AppRuntime
from app.telegram.ingestion import TelegramIngestor
from app.telegram.service import add_source, delete_messages, delete_source, make_client, telegram_status, update_source

router = APIRouter(prefix="/telegram", tags=["telegram"])

SETUP_STEPS = [
    "In Telegram, open @BotFather, send /newbot and follow the prompts. Copy the token it gives you.",
    "In @BotFather send /setprivacy, choose your bot and select Disable, so the bot can read normal group messages "
    "(alternatively make the bot a group admin).",
    "Paste the token on this page and click Save & test (it is encrypted on this computer). Never share it.",
    "Add the bot to each community group you want Price Chat to learn from.",
    "Send any message in the group, wait ~30 seconds, then authorise the chat below (it appears under 'Chats the bot can see').",
]


def status_out(db: DbSession, runtime: AppRuntime) -> TelegramStatusOut:
    st = telegram_status(db, runtime.settings)
    sources = list(db.scalars(select(TelegramSource).order_by(TelegramSource.created_at)))
    discovered = sorted(runtime.telegram.discovered.values(), key=lambda d: d.last_seen_at, reverse=True)
    return TelegramStatusOut(
        configured=st.configured,
        status=st.status,
        bot_username=st.bot_username,
        bot_name=st.bot_name,
        last_sync_at=st.last_sync_at,
        last_success_at=st.last_success_at,
        last_error=st.last_error,
        last_error_at=st.last_error_at,
        messages_ingested=st.messages_ingested,
        ignored_updates=st.ignored_updates,
        sources=[TelegramSourceOut.model_validate(s) for s in sources],
        discovered_chats=[
            DiscoveredChatOut(
                chat_id=d.chat_id, title=d.title, chat_type=d.chat_type, last_seen_at=d.last_seen_at,
                ignored_messages=d.ignored_messages, bot_status=d.bot_status,
            )
            for d in discovered
        ],
        setup_steps=SETUP_STEPS,
    )


@router.get("/status", response_model=TelegramStatusOut)
def get_status(db: DbSession, runtime: Runtime) -> TelegramStatusOut:
    return status_out(db, runtime)


@router.post("/verify", response_model=TelegramStatusOut)
async def verify(db: DbSession, runtime: Runtime) -> TelegramStatusOut:
    """Check the bot token with Telegram (getMe). Telegram errors map to structured responses."""
    client = make_client(runtime.settings, runtime.telegram.transport)
    try:
        state = await TelegramIngestor(db, client, runtime.telegram).verify()
    finally:
        await client.aclose()
    runtime.telegram.set_status(runtime.telegram.status if runtime.telegram.status == "connected" else "disconnected", "", state.bot_username)
    return status_out(db, runtime)


@router.post("/sync", response_model=JobRunOut)
async def sync_now(runtime: Runtime) -> JobRunOut:
    assert runtime.scheduler is not None
    outcome = await runtime.scheduler.run_job("telegram.ingest", trigger="manual")
    return JobRunOut(name=outcome.name, status=outcome.status, result=outcome.result, error=outcome.error)


# --- allow-list ---------------------------------------------------------------------------


@router.post("/sources", response_model=TelegramSourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(payload: TelegramSourceIn, db: DbSession, runtime: Runtime) -> TelegramSourceOut:
    try:
        source = await add_source(db, runtime.settings, runtime.telegram, payload.chat_id, payload.display_name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    runtime.telegram.authorised_chats += 1
    return TelegramSourceOut.model_validate(source)


def _source(db: DbSession, source_id: int) -> TelegramSource:
    source = db.get(TelegramSource, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram source not found")
    return source


@router.patch("/sources/{source_id}", response_model=TelegramSourceOut)
def edit_source(source_id: int, payload: TelegramSourceUpdate, db: DbSession) -> TelegramSourceOut:
    return TelegramSourceOut.model_validate(update_source(db, _source(db, source_id), payload.model_dump(exclude_unset=True)))


@router.delete("/sources/{source_id}", response_model=DeletionOut)
def remove_source(source_id: int, db: DbSession, runtime: Runtime) -> DeletionOut:
    """Remove a chat from the allow-list and delete ALL data that came from it."""
    report = delete_source(db, CommunityTuning.from_settings(runtime.settings), _source(db, source_id))
    runtime.telegram.authorised_chats = max(0, runtime.telegram.authorised_chats - 1)
    return DeletionOut(messages=report.messages, summaries=report.summaries, memories=report.memories, opportunities=report.opportunities)


# --- raw messages ----------------------------------------------------------------------------


@router.get("/messages", response_model=list[TelegramMessageOut])
def list_messages(
    db: DbSession,
    source_id: int | None = None,
    topic_id: int | None = None,
    question_id: int | None = None,
    questions_only: bool = False,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[TelegramMessageOut]:
    stmt = select(TelegramMessage)
    if source_id is not None:
        stmt = stmt.where(TelegramMessage.source_id == source_id)
    if topic_id is not None:
        stmt = stmt.where(TelegramMessage.topic_id == topic_id)
    if question_id is not None:
        stmt = stmt.where(TelegramMessage.question_id == question_id)
    if questions_only:
        stmt = stmt.where(TelegramMessage.is_question.is_(True))
    if q:
        stmt = stmt.where(TelegramMessage.text.ilike(f"%{q}%"))
    stmt = stmt.order_by(TelegramMessage.sent_at.desc(), TelegramMessage.id.desc()).limit(limit).offset(offset)
    return [TelegramMessageOut.model_validate(m) for m in db.scalars(stmt)]


@router.delete("/messages/{message_id}", response_model=DeletionOut)
def remove_message(message_id: int, db: DbSession, runtime: Runtime) -> DeletionOut:
    if db.get(TelegramMessage, message_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    report = delete_messages(db, CommunityTuning.from_settings(runtime.settings), [message_id], reason="deleted from the dashboard")
    return DeletionOut(messages=report.messages, summaries=report.summaries, memories=report.memories, opportunities=report.opportunities)


# --- Phase 3B: credentials, setup wizard, pilot controls, provenance trace ----------------------


def _token_status(runtime: AppRuntime) -> SecretStatusOut:
    i = credentials.info("telegram_bot_token", runtime.settings)
    return SecretStatusOut(name=i.name, configured=i.configured, source=i.source, saved_at=i.saved_at)


def _ingestion_enabled(db: DbSession) -> bool:
    row = db.get(ScheduledJob, "telegram.ingest")
    return row.enabled if row is not None else True


@router.get("/credentials", response_model=SecretStatusOut)
def get_credentials(runtime: Runtime) -> SecretStatusOut:
    """Whether a bot token is configured. The token itself is never returned."""
    return _token_status(runtime)


@router.put("/credentials", response_model=TelegramSetupOut)
async def save_credentials(payload: TelegramTokenIn, db: DbSession, runtime: Runtime) -> TelegramSetupOut:
    """Verify the token with Telegram (getMe), then store it encrypted on this computer."""
    if payload.verify:
        client = TelegramBotClient(payload.token, runtime.settings.telegram_api_base, transport=runtime.telegram.transport)
        try:
            await TelegramIngestor(db, client, runtime.telegram).verify()
        finally:
            await client.aclose()
    credentials.secret_store(runtime.settings).set("telegram_bot_token", payload.token)
    runtime.telegram.configured = True
    state = get_bot_state(db)
    state.status = "disconnected" if state.status in ("not_configured", "error") else state.status
    state.last_error = None
    db.commit()
    runtime.telegram.set_status(state.status, "", state.bot_username)
    ActivityLogger(db).log(
        "telegram_credentials_saved",
        "Telegram bot token saved (encrypted on this computer)" + (f" for @{state.bot_username}" if state.bot_username else ""),
        level=ActivityLevel.AUDIT,
        actor="dashboard",
    )
    return setup_out(db, runtime)


@router.delete("/credentials", response_model=TelegramSetupOut)
def delete_credentials(db: DbSession, runtime: Runtime) -> TelegramSetupOut:
    removed = credentials.secret_store(runtime.settings).delete("telegram_bot_token")
    runtime.telegram.configured = runtime.settings.telegram_configured  # a .env token may remain
    if not runtime.telegram.configured:
        runtime.telegram.set_status("not_configured")
        state = get_bot_state(db)
        state.status = "not_configured"
        db.commit()
    if removed:
        ActivityLogger(db).log("telegram_credentials_removed", "Telegram bot token removed", level=ActivityLevel.AUDIT, actor="dashboard")
    return setup_out(db, runtime)


def setup_out(db: DbSession, runtime: AppRuntime) -> TelegramSetupOut:
    token = _token_status(runtime)
    state = get_bot_state(db)
    conn = get_connector_state(db, Platform.TELEGRAM)
    privacy = (conn.capabilities or {}).get("can_read_all_group_messages")
    sources = list(db.scalars(select(TelegramSource)))
    messages = int(db.scalar(select(func.count()).select_from(TelegramMessage)) or 0)
    seen_chats = bool(runtime.telegram.discovered) or bool(sources)
    steps = [
        SetupStep(key="create_bot", title="Create a bot with @BotFather",
                  detail="In Telegram, open @BotFather, send /newbot, choose a name and a username ending in 'bot'. BotFather replies with a token.",
                  done=token.configured or None),
        SetupStep(key="privacy", title="Turn privacy mode off",
                  detail="In @BotFather send /setprivacy, pick your bot, choose Disable. Without this the bot only sees commands and replies to itself.",
                  done=privacy if privacy is not None else None),
        SetupStep(key="token", title="Paste the token into Price Chat",
                  detail="Paste it below and click Save & test. It is encrypted on this computer and never shown again.",
                  done=token.configured and state.bot_username is not None),
        SetupStep(key="add_to_group", title="Add the bot to your community group",
                  detail="In the group: Add members, search for your bot's username, add it. Then send any message in the group.",
                  done=seen_chats or None),
        SetupStep(key="authorise", title="Authorise the chat in Price Chat",
                  detail="Click Refresh chats, then Authorise next to your group. Price Chat never reads a chat you have not authorised.",
                  done=bool(sources)),
        SetupStep(key="receiving", title="Receive messages",
                  detail="New messages appear within about 20 seconds. Messages sent before authorisation are never collected.",
                  done=messages > 0),
    ]
    return TelegramSetupOut(
        token=token,
        bot_username=state.bot_username,
        privacy_disabled=privacy,
        ingestion_enabled=_ingestion_enabled(db),
        steps=steps,
    )


@router.get("/setup", response_model=TelegramSetupOut)
def get_setup(db: DbSession, runtime: Runtime) -> TelegramSetupOut:
    return setup_out(db, runtime)


@router.post("/refresh-chats", response_model=TelegramStatusOut)
async def refresh_chats(db: DbSession, runtime: Runtime) -> TelegramStatusOut:
    """Check Telegram for chats the bot is in. Messages from unauthorised chats are discarded, never stored."""
    assert runtime.scheduler is not None
    await runtime.scheduler.run_job("telegram.ingest", trigger="manual")
    db.expire_all()
    return status_out(db, runtime)


@router.put("/ingestion", response_model=TelegramSetupOut)
def set_ingestion(payload: ToggleIn, db: DbSession, runtime: Runtime) -> TelegramSetupOut:
    """Start or pause ingestion for all authorised chats (pauses the Telegram ingestion job)."""
    row = db.get(ScheduledJob, "telegram.ingest")
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The ingestion job is not registered yet")
    row.enabled = payload.enabled
    db.commit()
    ActivityLogger(db).log(
        "telegram_ingestion_toggled", f"Telegram ingestion {'started' if payload.enabled else 'paused'}",
        level=ActivityLevel.AUDIT, actor="dashboard",
    )
    if payload.enabled and runtime.scheduler is not None:
        runtime.scheduler.request_run("telegram.ingest", 1)
    return setup_out(db, runtime)


@router.get("/messages/{message_id}/trace", response_model=MessageTraceOut)
def trace_message(message_id: int, db: DbSession) -> MessageTraceOut:
    """raw message -> classification -> topic/question -> summaries, memories and opportunities derived from it."""
    m = db.get(TelegramMessage, message_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    source = db.get(TelegramSource, m.source_id)
    links = db.execute(select(ProvenanceLink.derived_kind, ProvenanceLink.derived_id).where(ProvenanceLink.message_id == m.id)).all()
    by_kind: dict[str, list[int]] = {}
    for kind, did in links:
        by_kind.setdefault(kind.value, []).append(did)
    topic = db.get(CommunityTopic, m.topic_id) if m.topic_id else None
    question = db.get(CommunityQuestion, m.question_id) if m.question_id else None
    summaries = [db.get(CommunitySummary, i) for i in by_kind.get("summary", [])]
    memories = [db.get(Memory, i) for i in by_kind.get("memory", [])]
    opp_ids = set(by_kind.get("opportunity", []))
    if topic is not None:
        opp_ids |= set(db.scalars(select(ContentOpportunity.id).where(ContentOpportunity.origin_kind == "topic", ContentOpportunity.origin_id == topic.id)))
    if question is not None:
        opp_ids |= set(db.scalars(select(ContentOpportunity.id).where(ContentOpportunity.origin_kind == "question", ContentOpportunity.origin_id == question.id)))
    opps = [db.get(ContentOpportunity, i) for i in sorted(opp_ids)]
    return MessageTraceOut(
        message=TelegramMessageOut.model_validate(m),
        source_name=source.display_name if source else None,
        topic={"id": topic.id, "name": topic.name, "status": topic.status.value, "trend": topic.trend.value} if topic else None,
        question={"id": question.id, "text": question.text, "ask_count": question.ask_count} if question else None,
        summaries=[{"id": s.id, "kind": s.kind.value, "headline": s.headline, "model": s.model} for s in summaries if s],
        memories=[{"id": x.id, "title": x.title, "content": x.content, "review_status": x.review_status.value} for x in memories if x],
        opportunities=[{"id": o.id, "title": o.title, "status": o.status.value} for o in opps if o],
    )


@router.get("/connection")
def connection(db: DbSession, runtime: Runtime) -> dict[str, object]:
    """Bot and account mode in one view: type, status, identity, sources, today's messages, processing."""
    from app.db.base import utcnow
    from app.models.routing import ScheduledJob
    from app.timeutil import local_day_start
    from app.telegram import account_service

    bot = get_bot_state(db)
    bot_on = runtime.settings.telegram_configured
    acc = account_service.status(db, runtime.settings, runtime.telegram_account)
    account_on = acc["session"] == "active"
    modes = [m for m, on in (("bot", bot_on), ("account", account_on)) if on]
    statuses = []
    if bot_on:
        statuses.append(bot.status)
    if acc["configured"]:
        statuses.append(str(acc["status"]))
    overall = ("error" if "error" in statuses else "connected" if "connected" in statuses
               else "needs_login" if any(s in ("needs_login", "code_sent", "password_needed") for s in statuses)
               else "disconnected" if statuses else "not_configured")
    sources = list(db.scalars(select(TelegramSource)))
    processed = db.get(ScheduledJob, "community.process")
    today = local_day_start(utcnow())
    return {
        "mode": modes, "status": overall,
        "bot": {"configured": bot_on, "status": bot.status if bot_on else "not_configured", "username": bot.bot_username,
                "name": bot.bot_name, "last_success_at": bot.last_success_at, "last_error": bot.last_error},
        "account": acc,
        "sources": {"total": len(sources), "enabled": sum(1 for s in sources if s.enabled),
                    "bot": sum(1 for s in sources if s.via == "bot"), "account": sum(1 for s in sources if s.via == "account")},
        "messages_today": int(db.scalar(select(func.count()).select_from(TelegramMessage).where(TelegramMessage.sent_at >= today)) or 0),
        "last_message_at": db.scalar(select(func.max(TelegramMessage.sent_at))),
        "last_processing_at": processed.last_finished_at if processed else None,
    }
