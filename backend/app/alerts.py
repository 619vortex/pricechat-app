"""Owner alerts: a private Telegram message to the owner when something important breaks (not only a line in the logs).

- Where: Admin → Settings → Background jobs → Owner alerts (the owner's numeric Telegram user id; the owner presses
  Start in a private chat with the bot once). Unset or off: the alert is only logged.
- Each alert has a key; the same key goes out at most once per 6 h (stored in `owner_alerts`, so restarts don't spam).
- A problem that was reported stays "open" until it is fixed; the fix sends one "recovered" message (no cooldown: it
  always pairs with an alert the owner already got).
- Sources: a background job failing 3 runs in a row (the nightly backup: its first failure), and a Brand Manager TikTok
  account that is expired / revoked / disconnected while AUTO is on, Brand Manager publishing that stopped by itself
  (AUTO blocked or failing, videos past their posting time, sends to TikTok that failed; the `system.alerts` job), and a Kiwi VPN server
  the sync couldn't reach twice in a row / back online / past 90% of its capacity (after each `vpn.sync`).
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
VPN_OFFLINE_AFTER = 2  # syncs in a row that couldn't reach a VPN server's agent
VPN_FULL_AT, VPN_FULL_CLEAR = 0.9, 0.8  # "nearly full" once past 90% of capacity, re-armed only below 80%


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
    out = {"tiktok_alerts": sent, "recovered": recovered}
    for k, n in (await check_brand_publishing(db, settings=settings, transport=transport, now=now)).items():
        out[k] = out.get(k, 0) + n
    return out


BRAND_ERRORS_IN_A_ROW = 3  # failed ticks (one a minute) in the last 15 min before a Brand Manager error is worth a message
OVERDUE = timedelta(hours=1)  # a video still not sent this long after its posting time


async def check_brand_publishing(db: Session, *, settings: Settings | None = None, transport: Any = None, now: datetime | None = None) -> dict[str, int]:
    """Publishing that stops without anyone pressing STOP: AUTO blocked (a setting, storage) or failing every tick, videos
    past their posting time (no worker online, waiting for approval), and sends to TikTok that failed. The TikTok sign-in
    itself has its own alert above. One message per problem (6 h cooldown while it lasts), one when it is fixed."""
    from sqlalchemy import func

    from app.brand_manager.engine import DAILY_POST_CAP, _published_today
    from app.models.brand_manager import BmAccount, BmAutomation, BmBrand, BmEvent, BmVideo

    now = now or utcnow()
    out = {"brand_alerts": 0, "recovered": 0, "send_failed_alerts": 0}
    for auto, b in db.execute(select(BmAutomation, BmBrand).join(BmBrand, BmBrand.id == BmAutomation.brand_id)).all():
        key = f"brand:{b.id}:stalled"
        acc = db.scalar(select(BmAccount).where(BmAccount.brand_id == b.id, BmAccount.platform == "tiktok"))
        tiktok_bad = (acc.status if acc is not None else "disconnected") in TIKTOK_BAD
        problems: list[str] = []
        if auto.running and not tiktok_bad:
            if auto.status == "blocked":
                problems.append(f"AUTO is blocked: {auto.reason}")
            elif auto.status == "error":
                errors = db.scalar(select(func.count(BmEvent.id)).where(BmEvent.brand_id == b.id, BmEvent.level == "error",
                                                                         BmEvent.message.like("Something went wrong%"),
                                                                         BmEvent.at >= now - timedelta(minutes=15))) or 0
                if errors >= BRAND_ERRORS_IN_A_ROW:
                    problems.append(f"AUTO keeps failing: {_first_line(auto.reason)}")
            late = dict(db.execute(select(BmVideo.status, func.count(BmVideo.id)).where(
                BmVideo.brand_id == b.id, BmVideo.slot_at < now - OVERDUE,
                BmVideo.status.in_(("queued", "rendering", "needs_review", "ready", "approved"))).group_by(BmVideo.status)).all())
            waiting = late.get("queued", 0) + late.get("rendering", 0)
            if waiting:
                problems.append(f"{waiting} video{'s' * (waiting != 1)} past the posting time still waiting for an AI worker (is it online? Settings › AI)")
            if late.get("needs_review"):
                problems.append(f"{late['needs_review']} video{'s' * (late['needs_review'] != 1)} past the posting time waiting for your approval (Content Studio)")
            unsent = late.get("ready", 0) + late.get("approved", 0)
            if unsent and _published_today(db, b.id, now) < DAILY_POST_CAP:
                problems.append(f"{unsent} finished video{'s' * (unsent != 1)} past the posting time not sent to TikTok (see Brand Manager › Log)")
        if problems:
            text = (f"Price Chat alert: Brand Manager isn't publishing\nBrand: {b.name}\n" + "\n".join(f"- {p}" for p in problems)
                    + f"\nTime: {_when(now)}")
            out["brand_alerts"] += await notify_owner(db, key, text, settings=settings, transport=transport, now=now, open_=True)
        elif _is_open(db, key):
            if auto.running and not tiktok_bad:
                text = f"Price Chat: recovered\nBrand: {b.name}: Brand Manager is publishing on schedule again.\nTime: {_when(now)}"
                if await notify_owner(db, f"{key}:recovered", text, settings=settings, transport=transport, now=now, cooldown=False):
                    out["recovered"] += 1
                    _close(db, key)
            else:
                _close(db, key)  # AUTO stopped, or the TikTok sign-in alert has taken over
        # sends TikTok refused or whose answer was lost: once per batch of new failures, never the same failure twice
        fkey = f"brand:{b.id}:send_failed"
        row = db.get(OwnerAlert, fkey)
        since = max(now - timedelta(hours=24), _aware(row.last_sent_at) if row is not None and row.last_sent_at else now - timedelta(hours=24))
        failed = [m for m in db.scalars(select(BmEvent.message).where(BmEvent.brand_id == b.id, BmEvent.stage == "publish",
                                                                       BmEvent.level == "error", BmEvent.at > since).order_by(BmEvent.id).limit(20))]
        if failed:
            text = (f"Price Chat alert: {len(failed)}{'+' if len(failed) == 20 else ''} video{'s' * (len(failed) != 1)} not sent to TikTok\n"
                    f"Brand: {b.name}\nLatest: {_first_line(failed[-1], 300)}\nCheck TikTok and Brand Manager › Content Studio.\nTime: {_when(now)}")
            out["send_failed_alerts"] += await notify_owner(db, fkey, text, settings=settings, transport=transport, now=now)
    return out


# --- Kiwi VPN servers (called by the vpn.sync job after each sync) -----------------------------------------------------------


async def check_vpn_servers(db: Session, *, settings: Settings | None = None, transport: Any = None, now: datetime | None = None) -> dict[str, int]:
    """One message per transition: offline (2 failed syncs in a row) → back online; nearly full (≥ 90% of capacity),
    re-armed once it drops below 80%. A disabled server's open problems are closed quietly."""
    from app.models.vpn import VpnServer
    from app.vpn.service import device_counts

    now = now or utcnow()
    counts = device_counts(db)
    out = {"vpn_offline": 0, "vpn_recovered": 0, "vpn_full": 0}
    for v in list(db.scalars(select(VpnServer).order_by(VpnServer.id))):
        off_key, full_key = f"vpn:offline:{v.id}", f"vpn:full:{v.id}"
        if not v.enabled:
            _close(db, off_key)
            _close(db, full_key)
            continue
        if v.fail_streak >= VPN_OFFLINE_AFTER:
            if not _is_open(db, off_key):
                text = (f"Price Chat alert: Kiwi VPN server offline\nServer: {v.name} ({v.region or v.country_code})\n"
                        f"The sync couldn't reach its agent {v.fail_streak} times in a row: {_first_line(v.last_error)}\n"
                        f"Members can't add devices there until it's back.\nTime: {_when(now)}")
                out["vpn_offline"] += await notify_owner(db, off_key, text, settings=settings, transport=transport, now=now,
                                                         cooldown=False, open_=True)
        elif v.online and _is_open(db, off_key):
            text = f"Price Chat: recovered\nKiwi VPN server {v.name} is online again.\nTime: {_when(now)}"
            if await notify_owner(db, f"{off_key}:recovered", text, settings=settings, transport=transport, now=now, cooldown=False):
                out["vpn_recovered"] += 1
                _close(db, off_key)
        n = counts.get(v.id, 0)
        load = n / v.capacity if v.capacity > 0 else 1.0
        if load >= VPN_FULL_AT:
            if not _is_open(db, full_key):
                text = (f"Price Chat alert: Kiwi VPN server nearly full\nServer: {v.name}: {n} of {v.capacity} devices "
                        f"({round(load * 100)}%).\nAdd a server or raise its capacity (Admin › Kiwi VPN).\nTime: {_when(now)}")
                out["vpn_full"] += await notify_owner(db, full_key, text, settings=settings, transport=transport, now=now,
                                                      cooldown=False, open_=True)
        elif load < VPN_FULL_CLEAR:
            _close(db, full_key)
    return out


# --- admin -------------------------------------------------------------------------------------------------------------


def settings_out(db: Session) -> dict[str, Any]:
    row = settings_row(db)
    recent = db.scalars(select(OwnerAlert).where(OwnerAlert.last_sent_at.is_not(None)).order_by(OwnerAlert.last_sent_at.desc()).limit(10))
    return {"enabled": row.enabled, "telegram_chat": row.telegram_chat,
            "recent": [{"key": a.key, "last_sent_at": a.last_sent_at, "sent_count": a.sent_count, "open": a.open,
                        "text": a.last_text} for a in recent]}
