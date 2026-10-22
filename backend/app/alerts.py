"""Owner alerts: a private Telegram message to the owner when something important breaks (not only a line in the logs).

- Where: Admin → Settings → Background jobs → Owner alerts (the owner's numeric Telegram user id; the owner presses
  Start in a private chat with the bot once). Unset or off: the alert is only logged.
- Each alert has a key; the same key goes out at most once per 6 h (stored in `owner_alerts`, so restarts don't spam).
- A problem that was reported stays "open" until it is fixed; the fix sends one "recovered" message (no cooldown: it
  always pairs with an alert the owner already got).
- Sources: a background job failing 3 runs in a row (the nightly backup: its first failure), and a Brand Manager TikTok
  account that is expired / revoked / disconnected while AUTO is on (the `system.alerts` job).
- Sending can never break the caller: a Telegram error is logged and swallowed. Messages never carry secrets (job
  errors are already redacted by the scheduler).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.db.base import utcnow
from app.models.alerts import OwnerAlert, OwnerAlertSettings

logger = logging.getLogger(__name__)

COOLDOWN = timedelta(hours=6)
FAIL_AFTER = 3  # failed runs in a row before the owner hears about a job
FAIL_AFTER_JOB = {"system.backup": 1}  # the nightly backup runs once a day: its first failure already matters
TIKTOK_BAD = ("expired", "revoked", "disconnected")


def settings_row(db: Session) -> OwnerAlertSettings:
    row = db.get(OwnerAlertSettings, 1)
    if row is None:
        row = OwnerAlertSettings(id=1)
        db.add(row)
        db.flush()
    return row


def owner_chat(db: Session) -> str | None:
    row = db.get(OwnerAlertSettings, 1)
    return (row.telegram_chat.strip() or None) if row is not None and row.enabled else None


def _when(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M UTC")


def _first_line(error: str | None, limit: int = 200) -> str:
    line = (error or "").strip().splitlines()[0] if (error or "").strip() else "unknown error"
    return line if len(line) <= limit else line[: limit - 1] + "…"


async def send_text(settings: Settings, chat: str, text: str, transport: Any = None) -> None:
    from app.telegram.service import make_client

    client = make_client(settings, transport)  # TelegramNotConfiguredError without a bot token
    try:
        await client.send_message(int(chat) if chat.lstrip("-").isdigit() else chat, text)
    finally:
        await client.aclose()


async def notify_owner(db: Session, key: str, text: str, *, settings: Settings | None = None, transport: Any = None,
                       now: datetime | None = None, cooldown: bool = True, open_: bool | None = None) -> bool:
    """DM the owner `text` unless `key` was sent within 6 h. True when it went out. Never raises.
    `open_`: mark the problem behind `key` open (True) / fixed (False) once sent."""
    now = now or utcnow()
    try:
        row = db.get(OwnerAlert, key)
        if cooldown and row is not None and row.last_sent_at is not None and now - _aware(row.last_sent_at) < COOLDOWN:
            return False
        chat = owner_chat(db)
        if chat is None:
            logger.warning("Owner alert (no owner Telegram chat set, logged only) [%s]: %s", key, text)
            return False
        try:
            await send_text(settings or get_settings(), chat, text, transport)
        except Exception as exc:  # noqa: BLE001 - Telegram down / no bot token / owner never pressed Start: log only
            logger.warning("Owner alert [%s] not sent (%s: %s): %s", key, type(exc).__name__, str(exc)[:200], text)
            return False
        if row is None:
            row = OwnerAlert(key=key)
            db.add(row)
        row.last_sent_at, row.sent_count, row.last_text = now, (row.sent_count or 0) + 1, text[:2000]
        if open_ is not None:
            row.open = open_
        db.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - an alert must never break the job / scheduler that raised it
        db.rollback()
        logger.warning("Owner alert [%s] bookkeeping failed (%s): %s", key, type(exc).__name__, text)
        return False


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_open(db: Session, key: str) -> bool:
    row = db.get(OwnerAlert, key)
    return bool(row is not None and row.open)


def _close(db: Session, key: str) -> None:
    row = db.get(OwnerAlert, key)
    if row is not None and row.open:
        row.open = False
        db.commit()


# --- background jobs (called by the scheduler after it records each run) --------------------------------------------------


async def job_outcome(db: Session, name: str, label: str, status: str, error: str | None, previous: int, failures: int, *,
                      settings: Settings | None = None, transport: Any = None, now: datetime | None = None) -> bool:
    """`previous` / `failures`: failed runs in a row before / after this run. Alerts at the job's threshold (and again
    every 6 h while it keeps failing); one 'recovered' message when a reported job succeeds again."""
    key = f"job:{name}"
    if status == "failed" and failures >= FAIL_AFTER_JOB.get(name, FAIL_AFTER):
        now = now or utcnow()
        title = "Nightly database backup FAILED" if name == "system.backup" else "Background job failing"
        runs = "1 run" if failures == 1 else f"{failures} runs in a row"
        text = f"Price Chat alert: {title}\nJob: {label} ({name})\nFailed: {runs}\nError: {_first_line(error)}\nTime: {_when(now)}"
        return await notify_owner(db, key, text, settings=settings, transport=transport, now=now, open_=True)
    if status == "success" and _is_open(db, key):  # not only previous > 0: a skipped run in between resets the streak
        now = now or utcnow()
        after = f" after {previous} failed run{'s' if previous != 1 else ''}" if previous else ""
        text = f"Price Chat: recovered\nJob: {label} ({name}) is working again{after}.\nTime: {_when(now)}"
        sent = await notify_owner(db, f"{key}:recovered", text, settings=settings, transport=transport, now=now, cooldown=False)
        if sent:
            _close(db, key)
        return sent
    return False


# --- Brand Manager: TikTok sign-in lost while AUTO is on (the system.alerts job) -----------------------------------------


async def check_brand_accounts(db: Session, *, settings: Settings | None = None, transport: Any = None, now: datetime | None = None) -> dict[str, int]:
    from app.models.brand_manager import BmAccount, BmAutomation, BmBrand

    now = now or utcnow()
    sent = recovered = 0
    rows = db.execute(select(BmAutomation, BmBrand.name).join(BmBrand, BmBrand.id == BmAutomation.brand_id)).all()
    for auto, brand_name in rows:
        acc = db.scalar(select(BmAccount).where(BmAccount.brand_id == auto.brand_id, BmAccount.platform == "tiktok"))
        status = acc.status if acc is not None else "disconnected"
        key = f"tiktok:{auto.brand_id}"
        if auto.running and status in TIKTOK_BAD:
            text = (f"Price Chat alert: TikTok sign-in {status} while AUTO is on\nBrand: {brand_name}\n"
                    f"Videos can't be sent to TikTok until it is reconnected (Brand Manager › TikTok).\nTime: {_when(now)}")
            sent += await notify_owner(db, key, text, settings=settings, transport=transport, now=now, open_=True)
        elif _is_open(db, key):
            if status == "connected":
                text = f"Price Chat: recovered\nBrand: {brand_name}: TikTok is connected again.\nTime: {_when(now)}"
                if await notify_owner(db, f"{key}:recovered", text, settings=settings, transport=transport, now=now, cooldown=False):
                    recovered += 1
                    _close(db, key)
            elif not auto.running:
                _close(db, key)  # AUTO was stopped: nothing is waiting on TikTok any more
    return {"tiktok_alerts": sent, "recovered": recovered}


# --- admin -------------------------------------------------------------------------------------------------------------


def settings_out(db: Session) -> dict[str, Any]:
    row = settings_row(db)
    recent = db.scalars(select(OwnerAlert).where(OwnerAlert.last_sent_at.is_not(None)).order_by(OwnerAlert.last_sent_at.desc()).limit(10))
    return {"enabled": row.enabled, "telegram_chat": row.telegram_chat,
            "recent": [{"key": a.key, "last_sent_at": a.last_sent_at, "sent_count": a.sent_count, "open": a.open,
                        "text": a.last_text} for a in recent]}
