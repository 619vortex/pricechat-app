import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.enums import ActivityLevel, Platform

logger = logging.getLogger("pricechat.activity")

_PY_LEVELS = {
    ActivityLevel.INFO: logging.INFO,
    ActivityLevel.AUDIT: logging.INFO,
    ActivityLevel.WARNING: logging.WARNING,
    ActivityLevel.ERROR: logging.ERROR,
}

# Any details key that looks like a credential is masked before it is stored or printed.
_SENSITIVE_KEY = re.compile(r"(api[_-]?key|token$|secret|password|passwd|authorization|credential|cookie)", re.I)
REDACTED = "[redacted]"
# Secret-shaped values inside free text: Telegram bot tokens and /bot<token>/ URL segments.
_TOKEN_VALUE = re.compile(r"\b\d{5,15}:[A-Za-z0-9_-]{30,}")
_BOT_URL = re.compile(r"/bot[^/\s]+")


def redact_text(text: str) -> str:
    return _BOT_URL.sub("/bot" + REDACTED, _TOKEN_VALUE.sub(REDACTED, text))


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: REDACTED if _SENSITIVE_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


# event -> (category, plain-English label) for the Activity page.
EVENT_CATALOG: dict[str, tuple[str, str]] = {
    "ai_inference": ("ai", "AI answered"),
    "ai_inference_failed": ("ai", "AI could not answer"),
    "model_test": ("ai", "Model test"),
    "provider_failure": ("ai", "AI went offline"),
    "provider_recovery": ("ai", "AI back online"),
    "memory_retrieval": ("memory", "Memories recalled"),
    "memory_created": ("memory", "Knowledge added"),
    "memory_updated": ("memory", "Knowledge updated"),
    "memory_deleted": ("memory", "Knowledge removed"),
    "memory_indexed": ("memory", "Knowledge indexed for search"),
    "agent_task": ("task", "Agent task"),
    "agent_config_updated": ("config", "Agent settings changed"),
    "brand_profile_updated": ("config", "Brand profile changed"),
    "goal_created": ("config", "Goal added"),
    "goal_updated": ("config", "Goal changed"),
    "goal_deleted": ("config", "Goal removed"),
    "model_config_updated": ("config", "Model settings changed"),
    "safety_policy_updated": ("safety", "Safety policy changed"),
    "action_limit_updated": ("safety", "Action limit changed"),
    "kill_switch": ("safety", "Kill switch"),
    "safety_check": ("safety", "Safety check"),
    "content_created": ("content", "Content created"),
    "content_updated": ("content", "Content updated"),
    "content_deleted": ("content", "Content deleted"),
    "system_started": ("system", "System started"),
    "memory_reviewed": ("memory", "Memory reviewed"),
    "community_memories_proposed": ("memory", "Community memories proposed"),
    "telegram_ingested": ("community", "Telegram messages received"),
    "telegram_error": ("community", "Telegram problem"),
    "telegram_recovered": ("community", "Telegram reconnected"),
    "telegram_source_added": ("community", "Telegram chat authorised"),
    "telegram_source_updated": ("community", "Telegram chat changed"),
    "telegram_source_deleted": ("community", "Telegram chat removed"),
    "telegram_data_deleted": ("community", "Telegram data deleted"),
    "community_summary": ("community", "Community summary"),
    "community_processing_degraded": ("community", "Processing used fallback rules"),
    "content_opportunities_detected": ("content", "Content opportunities found"),
    "opportunity_reviewed": ("content", "Opportunity reviewed"),
    "content_drafted": ("content", "Content drafted"),
    "job_failed": ("system", "Background job failed"),
    "telegram_messages_processed": ("community", "Telegram messages processed"),
    "telegram_account_configured": ("community", "Telegram API keys saved"),
    "telegram_account_connected": ("community", "Telegram account connected"),
    "telegram_account_disconnected": ("community", "Telegram account logged out"),
    "telegram_history_imported": ("community", "Telegram history imported"),
    "topics_detected": ("community", "Topics detected"),
    "merge_suggested": ("community", "Topic merge suggested"),
    "x_ingested": ("social", "X posts fetched"),
    "x_ingest_limited": ("social", "X reading limited"),
    "x_error": ("social", "X problem"),
    "x_posts_analysed": ("social", "X posts analysed"),
    "x_opportunities_detected": ("social", "X opportunities found"),
    "x_connection_tested": ("social", "X connection tested"),
    "x_credentials_saved": ("social", "X keys saved"),
    "x_disconnected": ("social", "X disconnected"),
    "x_reading_toggled": ("social", "X reading paused/resumed"),
    "x_rule_added": ("social", "X topic monitored"),
    "x_watch_added": ("social", "X account watched"),
    "cross_signals_detected": ("social", "Cross-platform signal"),
    "youtube_connected": ("social", "YouTube channel connected"),
    "youtube_oauth_client_saved": ("social", "YouTube sign-in client saved"),
    "youtube_oauth_connected": ("social", "Signed in to YouTube"),
    "youtube_tested": ("social", "YouTube connection tested"),
    "youtube_disconnected": ("social", "YouTube disconnected"),
    "youtube_new_content": ("social", "New YouTube content"),
    "youtube_content_unavailable": ("social", "YouTube content no longer public"),
    "youtube_sync_failed": ("social", "YouTube sync problem"),
    "content_item_updated": ("content", "Website content changed"),
    "website_settings_updated": ("content", "Website settings changed"),
    "member_admin_action": ("community", "Community member change"),
    "x_action_executed": ("safety", "X action executed"),
    "x_action_dry_run": ("safety", "Dry run: would execute"),
    "x_action_failed": ("safety", "X action failed"),
    "x_action_blocked": ("safety", "X action blocked"),
    "social_actions_toggled": ("safety", "Social actions active/paused"),
    "dry_run_changed": ("safety", "Dry run changed"),
    "ai_analysis_started": ("ai", "AI analysis started"),
    "ai_analysis_completed": ("ai", "AI analysis completed"),
    "ai_analysis_timeout": ("ai", "AI analysis timed out"),
    "ai_analysis_failed": ("ai", "AI analysis failed"),
    "job_recovered": ("system", "Background job recovered"),
    "job_updated": ("config", "Background job changed"),
    "model_route_updated": ("config", "Model routing changed"),
}
CATEGORIES = ("ai", "memory", "community", "social", "task", "config", "safety", "content", "system")


def describe_event(event: str) -> tuple[str, str]:
    return EVENT_CATALOG.get(event, ("system", event.replace("_", " ").capitalize()))


class ActivityLogger:
    """Writes to the activity_logs table and mirrors each entry to the Python log."""

    def __init__(self, db: Session, agent_id: int | None = None) -> None:
        self._db = db
        self._agent_id = agent_id

    def log(
        self,
        event: str,
        message: str,
        *,
        level: ActivityLevel = ActivityLevel.INFO,
        actor: str = "system",
        platform: Platform | None = None,
        action: str | None = None,
        details: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> ActivityLog:
        """commit=False: the entry is saved with the caller's own transaction (used inside running jobs,
        so logging never opens a second write that could wait on the job's own lock)."""
        entry = ActivityLog(
            agent_id=self._agent_id,
            level=level,
            event=event,
            message=redact_text(message),
            actor=actor,
            platform=platform,
            action=action,
            details=redact(details or {}),
        )
        self._db.add(entry)
        if commit:
            self._db.commit()
        logger.log(_PY_LEVELS[level], "[%s] %s: %s", actor, event, entry.message)
        return entry


def recent_activity(
    db: Session,
    limit: int = 50,
    level: ActivityLevel | None = None,
    category: str | None = None,
) -> list[ActivityLog]:
    stmt = select(ActivityLog).order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
    if level is not None:
        stmt = stmt.where(ActivityLog.level == level)
    if category is not None:
        events = [e for e, (c, _) in EVENT_CATALOG.items() if c == category]
        stmt = stmt.where(ActivityLog.event.in_(events))
    return list(db.scalars(stmt.limit(limit)))
