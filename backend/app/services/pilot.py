"""Pilot readiness: is each part of Price Chat actually working with real data?

Every "ready" here is backed by something that was really tested: a live model-service check,
a verified Telegram bot plus a successful ingestion run, an X capability probe, a successful job.
Nothing is reported ready because it is merely configured.
"""

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors.state import get_connector_state
from app.models.enums import Platform
from app.models.routing import ScheduledJob
from app.models.telegram import TelegramSource
from app.security import credentials
from app.services.runtime import AppRuntime as Runtime
from app.telegram.ingestion import get_bot_state


@dataclass(slots=True)
class Check:
    key: str
    label: str
    status: str  # ready | needs_setup | waiting | problem | disabled | paused | dry_run | supervised
    detail: str
    next_step: str | None = None


def _installed(name: str | None, models: list[str]) -> bool:
    if not name:
        return False
    base = name.split(":")[0]
    return name in models or f"{name}:latest" in models or (":" not in name and any(m.split(":")[0] == base for m in models))


def _job(db: Session, name: str) -> ScheduledJob | None:
    return db.get(ScheduledJob, name)


def _ran_ok(job: ScheduledJob | None) -> bool:
    return bool(job and job.run_count > 0 and (job.last_status == "success" or job.last_finished_at and job.consecutive_failures == 0))


async def model_status(runtime: Runtime) -> dict[str, Any]:
    """Live check of the model service and which tier models are installed (not a model call)."""
    s = runtime.settings
    health = await runtime.llm_health(s.model_tier_fast or runtime.provider.model)
    emb = runtime.embedder.model if runtime.embedder else None
    return {
        "service": health.available,
        "detail": health.detail,
        "fast": {"model": s.model_tier_fast or runtime.provider.model, "installed": _installed(s.model_tier_fast or runtime.provider.model, health.models)},
        "strong": {"model": s.model_tier_strong, "installed": _installed(s.model_tier_strong, health.models)},
        "embeddings": {"model": emb, "installed": _installed(emb, health.models)},
    }


def telegram_check(db: Session, runtime: Runtime) -> Check:
    """READY only when a bot or a logged-in account is actually receiving from an authorised source."""
    from app.telegram import account_service

    bot = get_bot_state(db)
    ingest = _job(db, "telegram.ingest")
    bot_on = bool(credentials.telegram_token(runtime.settings))
    acc = account_service.get_account(db)
    acc_on = account_service.has_session(runtime.settings) and account_service.configured(db, runtime.settings)
    bot_chats = int(db.scalar(select(func.count()).select_from(TelegramSource).where(TelegramSource.enabled.is_(True), TelegramSource.via == "bot")) or 0)
    acc_chats = int(db.scalar(select(func.count()).select_from(TelegramSource).where(TelegramSource.enabled.is_(True), TelegramSource.via == "account")) or 0)
    where = "Community › Connection"
    if not bot_on and not acc_on:
        if account_service.configured(db, runtime.settings):
            return Check("telegram", "Telegram", "needs_setup", "Telegram account credentials saved, but not logged in",
                         f"{where}: enter your phone number and the code Telegram sends you")
        return Check("telegram", "Telegram", "needs_setup", "Not connected", f"{where}: add a bot token, or log in with a Telegram account")
    if bot_on and bot.status == "error":
        return Check("telegram", "Telegram", "problem", f"Telegram API error (bot): {bot.last_error}", f"Check the bot token on {where}")
    if acc_on and acc.status == "error":
        return Check("telegram", "Telegram", "problem", f"Telegram API error (account): {acc.last_error}", f"See {where}")
    if bot_on and not acc_on and not bot.last_success_at and not bot.bot_username:
        return Check("telegram", "Telegram", "needs_setup", "Bot token saved but not verified with Telegram yet", f"Click Test connection on {where}")
    chats = (bot_chats if bot_on else 0) + (acc_chats if acc_on else 0)
    who = " and ".join(x for x, on in ((f"bot @{bot.bot_username}", bot_on), (f"account {acc.display_name or ''}".strip(), acc_on)) if on)
    if chats == 0:
        return Check("telegram", "Telegram", "needs_setup", f"Connected ({who}), but no chat is authorised",
                     f"{where}: refresh the list of groups and click Authorize for Price Chat")
    if not (ingest and ingest.enabled):
        return Check("telegram", "Telegram", "needs_setup", "Ingestion is paused", f"Click Start ingestion on {where}")
    if not _ran_ok(ingest):
        return Check("telegram", "Telegram", "waiting", f"{chats} source(s) authorised; waiting for the first successful ingestion run")
    return Check("telegram", "Telegram", "ready", f"Receiving from {chats} authorised source(s) via {who}")


async def pilot_status(db: Session, runtime: Runtime) -> dict[str, Any]:
    checks: list[Check] = []
    models = await model_status(runtime)

    # AI
    if not models["service"]:
        checks.append(Check("ai", "AI", "problem", f"The model service is not reachable: {models['detail'] or 'offline'}", "Start Ollama"))
    elif not models["fast"]["installed"]:
        checks.append(Check("ai", "AI", "needs_setup", f"Fast model {models['fast']['model']} is not installed", f"ollama pull {models['fast']['model']}"))
    elif models["strong"]["model"] and not models["strong"]["installed"]:
        checks.append(Check("ai", "AI", "ready", f"{models['fast']['model']} ready; strong model {models['strong']['model']} not installed, analysis falls back to the fast model",
                            f"ollama pull {models['strong']['model']}"))
    else:
        checks.append(Check("ai", "AI", "ready", f"{models['fast']['model']} (fast) and {models['strong']['model']} (strong) installed; model service online"))

    # Telegram: bot mode and/or account mode
    checks.append(telegram_check(db, runtime))

    # X
    xs = get_connector_state(db, Platform.X)
    caps = {k: (v or {}).get("status") for k, v in (xs.capabilities or {}).items()}
    if not credentials.x_credentials(runtime.settings).configured:
        checks.append(Check("x", "X", "needs_setup", "No X credentials saved", "X / Twitter › Connection: paste the Bearer Token and click Save & test"))
    elif xs.paused:
        checks.append(Check("x", "X", "needs_setup", "X reading is paused", "X / Twitter › Connection: Resume reading"))
    elif xs.status == "error" and not caps.get("public_read") == "available":
        checks.append(Check("x", "X", "problem", f"X API error: {xs.last_error}", "Fix the keys or X credit, then Test connection"))
    elif not caps:
        checks.append(Check("x", "X", "needs_setup", "Credentials saved but never tested", "X / Twitter › Connection: Test connection"))
    elif caps.get("public_read") != "available":
        why = (xs.capabilities or {}).get("public_read", {}).get("detail") or caps.get("public_read")
        checks.append(Check("x", "X", "problem", f"Public read not working: {why}", "See X / Twitter › Connection"))
    else:
        working = [k for k, v in caps.items() if v == "available" and k != "app_auth"]
        checks.append(Check("x", "X", "ready", f"Connected (read-only): {', '.join(w.replace('_', ' ') for w in working)}"))

    # Memory (embeddings power meaning-based recall, topics and matching)
    if not models["service"] or not models["embeddings"]["installed"]:
        checks.append(Check("memory", "Memory", "needs_setup", f"Embedding model {models['embeddings']['model']} not available", f"ollama pull {models['embeddings']['model'] or 'nomic-embed-text'}"))
    else:
        checks.append(Check("memory", "Memory", "ready", f"Meaning-based memory with {models['embeddings']['model']}; community memories wait for your review"))

    # Opportunities: ready only when an analysis has actually produced opportunities from real data
    # (a run over no data does not count).
    from app.models.community import ContentOpportunity
    from app.models.social_intel import SocialOpportunity

    sources = []
    if db.scalar(select(func.count()).select_from(ContentOpportunity).where(ContentOpportunity.model.is_not(None))):
        sources.append("Telegram")
    if db.scalar(select(func.count()).select_from(SocialOpportunity).where(SocialOpportunity.model.is_not(None))):
        sources.append("X")
    if not sources:
        checks.append(Check("opportunities", "Opportunities", "waiting", "No opportunities have been found in real data yet",
                            "Happens automatically once Telegram or X data arrives"))
    else:
        missing = [x for x in ("Telegram", "X") if x not in sources]
        checks.append(Check("opportunities", "Opportunities", "ready", "Producing opportunities from " + " and ".join(sources)
                            + (f" (none from {missing[0]} yet)" if missing else "")))

    # Write actions: supervised (every action needs your approval); never counted toward pilot readiness.
    from app.services.safety import get_or_create_policy
    from app.services.agent_service import get_agent_config

    policy = get_or_create_policy(db, get_agent_config(db).id)
    xcreds = credentials.x_credentials(runtime.settings)
    write_caps = {k: v for k, v in (xs.capabilities or {}).items() if k.startswith("write_")}
    if not xcreds.user_context:
        checks.append(Check("write_actions", "Write actions", "needs_setup", "Needs X user-context credentials (Access Token and Secret with Read and write permission)",
                            "X / Twitter › Connection: add the Access Token and Access Token Secret, then Test connection"))
    elif write_caps and all((v or {}).get("status") == "unavailable" for v in write_caps.values()):
        checks.append(Check("write_actions", "Write actions", "problem", "X says these credentials are read-only",
                            "In the X Developer Console set app permissions to Read and write, regenerate the Access Token and Secret, paste them again"))
    elif policy.kill_switch or not policy.outbound_enabled:
        checks.append(Check("write_actions", "Write actions", "paused", "Social actions are paused: nothing can be sent", "X / Twitter › Overview: set Social actions to Active"))
    elif policy.dry_run:
        checks.append(Check("write_actions", "Write actions", "dry_run", "Dry run: approved actions go through every check but nothing is sent to X",
                            "Turn dry run off on X / Twitter › Overview when you are ready for the first real action"))
    else:
        checks.append(Check("write_actions", "Write actions", "supervised", "Live and supervised: every action needs your approval (automatic is locked)"))

    ready = all(c.status in ("ready", "disabled") for c in checks if c.key != "write_actions")
    return {"ready": ready, "checks": [asdict(c) for c in checks], "models": models}
