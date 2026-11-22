"""Owner alerts: a private Telegram message to the owner when something important breaks (not only a line in the logs).

- Where: Admin → Settings → Background jobs → Owner alerts (the owner's numeric Telegram user id; the owner presses
  Start in a private chat with the bot once). Unset or off: the alert is only logged.
- Each alert has a key; the same key goes out at most once per 6 h (stored in `owner_alerts`, so restarts don't spam).
- A problem that was reported stays "open" until it is fixed; the fix sends one "recovered" message (no cooldown: it
  always pairs with an alert the owner already got).
- Sources: a background job failing 3 runs in a row (the nightly backup and the weekly restore test: their first failure),
  the newest backup older than 26 h or the bucket unreadable (the hourly `system.backup_check` job), and a Brand Manager TikTok
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
from app.db.singleton import get_or_create
from app.models.alerts import OwnerAlert, OwnerAlertSettings

logger = logging.getLogger(__name__)

COOLDOWN = timedelta(hours=6)
FAIL_AFTER = 3  # failed runs in a row before the owner hears about a job
DAILY = timedelta(hours=24)
REMIND_JOB = {"system.restore_test": DAILY, "system.backup_check": DAILY}  # weekly / hourly checks: a reminder a day, not every 6 h
FAIL_AFTER_JOB = {"system.backup": 1, "system.restore_test": 1, "system.backup_check": 1}  # daily / weekly / backup safety: the first failure matters
TIKTOK_BAD = ("expired", "revoked", "disconnected")
from app.vpn.service import OFFLINE_AFTER as VPN_OFFLINE_AFTER  # noqa: E402  (same count as the server going offline)
VPN_FULL_AT, VPN_FULL_CLEAR = 0.9, 0.8  # "nearly full" once past 90% of capacity, re-armed only below 80%
VPN_TRAFFIC_WARN, VPN_TRAFFIC_OVER = 0.8, 1.0  # of a server's monthly allowance; both re-arm when a new month starts
VPN_FLEET_BUSY, VPN_FLEET_CLEAR = 0.8, 0.7  # devices on public servers / their capacity: "add a server", re-armed below 70%
VPN_MIN_HEALTHY = 2  # public servers that must be healthy while the VPN is open, so one failure still leaves a fallback


def settings_row(db: Session) -> OwnerAlertSettings:
    return get_or_create(db, OwnerAlertSettings, lambda: OwnerAlertSettings(id=1))


# --- linking the owner's chat from Admin (2026-11-18: owner alerts were "logged only" for days: no chat id was ever set) ------------
OWNER_LINK_PREFIX = "ow_"
OWNER_LINK_KEY = "owner_link"
OWNER_LINK_TTL = timedelta(minutes=30)


def _hash(code: str) -> str:
    import hashlib

    return hashlib.sha256(code.encode()).hexdigest()


def start_owner_link(db: Session, now: datetime | None = None) -> tuple[str, datetime]:
    """A one-time code for the bot's deep link (t.me/<bot>?start=ow_<code>): only its hash is kept, it expires in 30 minutes."""
    import secrets

    now = now or utcnow()
    code = secrets.token_hex(8)
    row = db.get(OwnerAlert, OWNER_LINK_KEY) or OwnerAlert(key=OWNER_LINK_KEY)
    row.last_text, row.last_sent_at, row.open = _hash(code), now, True
    db.merge(row)
    db.commit()
    return code, now + OWNER_LINK_TTL


def claim_owner_link(db: Session, code: str, user_id: int, now: datetime | None = None) -> bool:
    """The person who pressed Start with a valid, unexpired code becomes the alerts' chat. The code works once."""
    now = now or utcnow()
    row = db.get(OwnerAlert, OWNER_LINK_KEY)
    if row is None or not row.open or not row.last_sent_at or not code or row.last_text != _hash(code):
        return False
    if now - _aware(row.last_sent_at) > OWNER_LINK_TTL:
        return False
    s = settings_row(db)
    s.telegram_chat, s.enabled = str(int(user_id)), True
    row.open, row.last_text = False, ""
    db.commit()
    return True  # the kept alerts go out on the next alert or the next watchdog pass (flush_pending)


def owner_chat(db: Session) -> str | None:
    row = db.get(OwnerAlertSettings, 1)
    return (row.telegram_chat.strip() or None) if row is not None and row.enabled else None


# --- finding the owner's chat by itself (2026-11-20: every owner alert since the feature shipped was "logged only": the owner never pressed
# Admin > Settings > Owner alerts > Link my Telegram, so a missed News Desk post on 2026-11-20 04:00 UTC told nobody) -------------------------
DISCOVER_KEY = "owner_discover"  # the OwnerAlert row that throttles the attempts (last_sent_at) and keeps the last outcome (last_text)
DISCOVER_EVERY = timedelta(minutes=30)
CONNECTED_KEY = "owner:connected"


def owner_candidates_chats(db: Session) -> list[str]:
    """Price Chat's own Telegram chats: the main community chat, Bot Chat (the signal group) and the news channel."""
    out: list[str] = []
    try:
        from app.tradingos import signals

        st = signals.settings(db)
        out += [str(st.main_chat or "").strip(), str(st.publish_chat or "").strip()]
    except Exception:  # noqa: BLE001 - a missing table on an old database: the channel alone
        db.rollback()
    try:
        from app.members.telegram_missions import channel_handle

        out.append(channel_handle(db) or "")
    except Exception:  # noqa: BLE001
        db.rollback()
    seen: list[str] = []
    for c in out:
        if c and c.lower() not in [x.lower() for x in seen]:
            seen.append(c)
    return seen


async def discover_owner_chat(db: Session, *, settings: Settings | None = None, transport: Any = None, now: datetime | None = None,
                              force: bool = False) -> str | None:
    """No owner chat is set: the person who CREATED Price Chat's own Telegram chats is the owner. When every one of those chats that names
    a creator names the same person, and the bot can message them (they pressed Start with the bot once), their private chat becomes the
    alerts' chat and a confirmation is sent there. Never overrides a chat set in Admin, never turns alerts back on after the owner turned
    them off, at most every DISCOVER_EVERY (force: now). Returns the chat, or None. Never raises."""
    now = now or utcnow()
    s = settings_row(db)
    if s.telegram_chat.strip() or not s.enabled:
        return owner_chat(db)
    row = db.get(OwnerAlert, DISCOVER_KEY)
    if not force and row is not None and row.last_sent_at is not None and now - _aware(row.last_sent_at) < DISCOVER_EVERY:
        return None
    if row is None:
        row = OwnerAlert(key=DISCOVER_KEY)
        db.add(row)
    row.last_sent_at = now
    db.commit()

    def outcome(text: str, level: int = logging.WARNING) -> None:
        row.last_text = text[:2000]
        db.commit()
        logger.log(level, "owner alerts: %s", text)

    from app.telegram.errors import TelegramError
    from app.telegram.service import make_client

    try:
        client = make_client(settings or get_settings(), transport)
    except Exception as e:  # noqa: BLE001 - no bot token
        outcome(f"can't look for the owner: {type(e).__name__}")
        return None
    try:
        creators: dict[str, dict[str, Any]] = {}
        for chat in owner_candidates_chats(db):
            try:
                admins = await client.get_chat_administrators(chat)
            except TelegramError as e:
                logger.info("owner alerts: %s: no administrator list (%s)", chat, str(e)[:120])
                continue
            for a in admins:
                user = a.get("user") or {}
                if a.get("status") == "creator" and not user.get("is_bot") and user.get("id"):
                    creators[chat] = user
        ids = {int(u["id"]) for u in creators.values()}
        if len(ids) != 1:
            outcome(f"can't tell who the owner is: the chats' creators are {sorted(ids) or 'not visible to the bot'} "
                    f"({', '.join(creators) or 'no chat answered'}). Link it in Admin > Settings > Owner alerts > Link my Telegram.")
            return None
        user = next(iter(creators.values()))
        uid = int(user["id"])
        who = f"@{user['username']}" if user.get("username") else str(user.get("first_name") or uid)
        where = ", ".join(creators)
        text = (f"Price Chat: owner alerts are connected to this chat.\nYou created {where}, so this private chat now gets the owner alerts "
                "that until now were only written to the server log: a News Desk post that was missed or not delivered to Telegram or X, a "
                "background job failing, a backup problem, a LIVE position without a stop-loss.\nTo use another chat or turn them off: Admin > "
                f"Settings > Owner alerts.\nTime: {_when(now)}")
        try:
            await client.send_message(uid, text)
        except TelegramError as e:
            outcome(f"the owner looks like {who} (id {uid}, creator of {where}), but the bot can't message them yet ({str(e)[:120]}): "
                    "press Start in a private chat with the bot, or Admin > Settings > Owner alerts > Link my Telegram.")
            return None
        s.telegram_chat, s.enabled = str(uid), True
        done = db.get(OwnerAlert, CONNECTED_KEY) or OwnerAlert(key=CONNECTED_KEY)
        done.last_sent_at, done.sent_count, done.last_text = now, (done.sent_count or 0) + 1, text[:2000]
        db.merge(done)
        outcome(f"linked to {who} (id {uid}), the creator of {where}; confirmation sent", logging.INFO)
        return str(uid)
    except Exception as e:  # noqa: BLE001 - finding the owner never breaks the alert that asked
        db.rollback()
        logger.warning("owner alerts: looking for the owner failed (%s)", type(e).__name__)
        return None
    finally:
        await client.aclose()


# --- alerts that could not be delivered are kept, never only logged (owner, 2026-11-21: every alert of 2026-11-20/21, 46 "no Telegram post
# for 12 h" among them, was a log line nobody read; when a chat was linked later, none of them reached the owner) ----------------------------
PENDING_PREFIX = "pending:"  # an OwnerAlert row per undelivered key: last_text = the newest text, last_sent_at = when it was last raised,
PENDING_MAX = 25  # sent_count = how many times; delivered as one digest, newest first, at most this many lines


def _queue_pending(db: Session, key: str, text: str, now: datetime, why: str) -> None:
    pkey = (PENDING_PREFIX + key)[:128]
    row = db.get(OwnerAlert, pkey)
    first = row is None
    if row is None:
        row = OwnerAlert(key=pkey, sent_count=0)
        db.add(row)
    row.last_sent_at, row.sent_count, row.last_text, row.open = now, (row.sent_count or 0) + 1, text[:2000], True
    db.commit()
    if first:
        logger.warning("Owner alert kept for delivery (%s) [%s]: %s", why, key, text)
    else:
        logger.info("Owner alert raised again, still kept for delivery (%s) [%s] x%d", why, key, row.sent_count)


def pending_alerts(db: Session) -> list[OwnerAlert]:
    return sorted(db.scalars(select(OwnerAlert).where(OwnerAlert.key.like(PENDING_PREFIX + "%"), OwnerAlert.open.is_(True))),
                  key=lambda r: _aware(r.last_sent_at) if r.last_sent_at else datetime.min.replace(tzinfo=timezone.utc), reverse=True)


async def flush_pending(db: Session, *, settings: Settings | None = None, transport: Any = None, now: datetime | None = None,
                        chat: str | None = None, skip: str = "") -> int:
    """Send every kept alert to the owner's chat as one digest (newest first), except `skip` (the key being sent on its own). Returns how
    many were delivered. Never raises."""
    now = now or utcnow()
    try:
        rows = [r for r in pending_alerts(db) if not skip or r.key != (PENDING_PREFIX + skip)[:128]]
        if not rows:
            return 0
        chat = chat or owner_chat(db)
        if chat is None:
            return 0
        lines = []
        for r in rows[:PENDING_MAX]:
            times = f" (x{r.sent_count})" if (r.sent_count or 0) > 1 else ""
            at = _when(_aware(r.last_sent_at)) if r.last_sent_at else "?"
            lines.append(f"- {at}{times}: {(r.last_text or '').strip()[:400]}")
        more = f"\n(+{len(rows) - PENDING_MAX} older)" if len(rows) > PENDING_MAX else ""
        text = (f"Price Chat: {len(rows)} alert(s) that could not reach you when they happened\n" + "\n".join(lines) + more)[:3900]
        await send_text(settings or get_settings(), chat, text, transport)
        for r in rows:
            r.open = False
        db.commit()
        logger.info("Owner alerts: %d kept alert(s) delivered", len(rows))
        return len(rows)
    except Exception as exc:  # noqa: BLE001 - kept for the next try
        db.rollback()
        logger.warning("Owner alerts: kept alerts not delivered yet (%s)", type(exc).__name__)
        return 0


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
                       now: datetime | None = None, cooldown: bool | timedelta = True, open_: bool | None = None) -> bool:
    """DM the owner `text` unless `key` was sent within 6 h (or the `cooldown` given). True when it went out. Never raises.
    `open_`: mark the problem behind `key` open (True) / fixed (False) once sent."""
    now = now or utcnow()
    window = cooldown if isinstance(cooldown, timedelta) else COOLDOWN
    try:
        row = db.get(OwnerAlert, key)
        if cooldown and row is not None and row.last_sent_at is not None and now - _aware(row.last_sent_at) < window:
            return False
        chat = owner_chat(db) or await discover_owner_chat(db, settings=settings, transport=transport, now=now)
        if chat is None:
            _queue_pending(db, key, text, now, "no owner Telegram chat set")
            return False
        mine = db.get(OwnerAlert, (PENDING_PREFIX + key)[:128])
        others = [r for r in pending_alerts(db) if r.key != (PENDING_PREFIX + key)[:128]]
        if others:  # a chat exists now: what could not be delivered before goes first (this key's own kept copy is this message)
            await flush_pending(db, settings=settings, transport=transport, now=now, chat=chat, skip=key)
        try:
            await send_text(settings or get_settings(), chat, text, transport)
        except Exception as exc:  # noqa: BLE001 - Telegram down / no bot token / owner never pressed Start: kept for later
            logger.warning("Owner alert [%s] not sent (%s: %s)", key, type(exc).__name__, str(exc)[:200])
            _queue_pending(db, key, text, now, f"not sent: {type(exc).__name__}")
            return False
        row = db.get(OwnerAlert, key)  # read again: finding the owner's chat committed in between
        if row is None:
            row = OwnerAlert(key=key)
            db.add(row)
        row.last_sent_at, row.sent_count, row.last_text = now, (row.sent_count or 0) + 1, text[:2000]
        if mine is not None and mine.open:  # delivered now, in its newest wording: nothing of it is kept any more
            mine.open = False
        logger.info("Owner alert sent [%s] to the owner's chat", key)
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
        title = {"system.backup": "Nightly database backup FAILED", "system.restore_test": "Weekly backup restore test FAILED"}.get(name, "Background job failing")
        runs = "1 run" if failures == 1 else f"{failures} runs in a row"
        text = f"Price Chat alert: {title}\nJob: {label} ({name})\nFailed: {runs}\nError: {_first_line(error)}\nTime: {_when(now)}"
        return await notify_owner(db, key, text, settings=settings, transport=transport, now=now, open_=True,
                                  cooldown=REMIND_JOB.get(name, True))
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


# --- database backups (the hourly system.backup_check job) -------------------------------------------------------------------

BACKUP_KEY = "backup:stale"


async def check_backup_freshness(db: Session, info: dict[str, Any] | None, error: str | None = None, *, settings: Settings | None = None,
                                 transport: Any = None, now: datetime | None = None) -> dict[str, int]:
    """`info` from db_backup.newest() (None with `error` when the bucket couldn't be read). One message when the newest
    backup is older than 26 h, missing, empty (0 bytes) or the bucket is unreadable, a reminder once a day while it lasts,
    one "recovered" when a fresh copy is there.
    Deduplicated against the two alerts main already had for the same incident: while the system.backup job's own failure
    alert (`job:system.backup`) or the watchdog's "no successful backup for 26 h" (`ops:silence:backup`) is open, the owner
    already knows, so this stays quiet. It speaks when the bucket disagrees with a job that looks fine."""
    now = now or utcnow()
    out = {"backup_stale": 0, "backup_recovered": 0}
    if error is not None or info is None or info.get("stale") or info.get("empty_newer"):
        if _is_open(db, "job:system.backup") or _is_open(db, "ops:silence:backup"):
            return out
        if error is not None or info is None:
            why = f"The backup bucket can't be read: {_first_line(error)}"
        elif info.get("empty_newer"):
            why = f"The newest backup object is empty (0 bytes): {info['empty_newer'][0].rsplit('/', 1)[-1]}."
        elif not info.get("key"):
            why = "There is no backup in the bucket (backups/db/ is empty)."
        else:
            why = f"The newest backup is {info['age_hours']:.0f} h old ({info['key'].rsplit('/', 1)[-1]}); one should land every 24 h."
        text = (f"Price Chat alert: database backups are STALE\n{why}\nCheck the system.backup job (Admin › Settings › Background jobs) "
                f"and the bucket secret.\nTime: {_when(now)}")
        out["backup_stale"] += await notify_owner(db, BACKUP_KEY, text, settings=settings, transport=transport, now=now, open_=True, cooldown=DAILY)
    elif _is_open(db, BACKUP_KEY):
        text = f"Price Chat: recovered\nDatabase backups are fresh again: the newest is {info['age_hours']:.0f} h old.\nTime: {_when(now)}"
        if await notify_owner(db, f"{BACKUP_KEY}:recovered", text, settings=settings, transport=transport, now=now, cooldown=False):
            out["backup_recovered"] += 1
            _close(db, BACKUP_KEY)
    return out


# --- Kiwi VPN servers (called by the vpn.sync job after each sync) -----------------------------------------------------------


async def check_vpn_servers(db: Session, *, settings: Settings | None = None, transport: Any = None, now: datetime | None = None) -> dict[str, int]:
    """One message per transition: offline (2 failed syncs in a row) → back online; nearly full (≥ 90% of capacity),
    re-armed once it drops below 80%. A disabled server's open problems are closed quietly."""
    from app.models.vpn import VpnServer
    from app.vpn.service import device_counts, server_traffic

    now = now or utcnow()
    counts = device_counts(db)
    traffic = server_traffic(db, now)
    month = now.strftime("%Y-%m")
    out = {"vpn_offline": 0, "vpn_recovered": 0, "vpn_full": 0, "vpn_traffic": 0}
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
        if v.traffic_included_gb > 0:
            used = traffic.get(v.id, 0)
            share = used / (v.traffic_included_gb * 1e9)
            for level, label in ((VPN_TRAFFIC_OVER, "over its included traffic: extra traffic is billed"), (VPN_TRAFFIC_WARN, "at 80% of its included traffic")):
                if share >= level:
                    key = f"vpn:traffic:{v.id}:{month}:{int(level * 100)}"  # once per server, level and month
                    if not _is_open(db, key):
                        text = (f"Price Chat alert: Kiwi VPN server bandwidth\nServer: {v.name} is {label}.\n"
                                f"This month: {used / 1e9:.0f} GB of {v.traffic_included_gb} GB ({round(share * 100)}%).\n"
                                f"Consider moving users to another server or raising its plan.\nTime: {_when(now)}")
                        out["vpn_traffic"] += await notify_owner(db, key, text, settings=settings, transport=transport, now=now, cooldown=False, open_=True)
                    break
    for k, n in (await _check_vpn_fleet(db, settings=settings, transport=transport, now=now)).items():
        out[k] = out.get(k, 0) + n
    return out


async def _recovered(db: Session, key: str, text: str, settings: Settings | None, transport: Any, now: datetime) -> int:
    """One "recovered" message for an open problem, then closed (kept open if Telegram didn't take it: retried next time)."""
    if not _is_open(db, key):
        return 0
    if await notify_owner(db, f"{key}:recovered", text, settings=settings, transport=transport, now=now, cooldown=False):
        _close(db, key)
        return 1
    if owner_chat(db) is None:
        _close(db, key)  # nowhere to send it: don't hold the problem open forever
    return 0


async def _check_vpn_fleet(db: Session, *, settings: Settings | None, transport: Any, now: datetime) -> dict[str, int]:
    """The fleet as a whole (app/vpn/health.py): too few healthy public servers while the VPN is open; public capacity
    nearly used; a server's traffic on pace to pass its allowance this month (once per server and month)."""
    from app.vpn import health
    from app.vpn.service import settings as vpn_settings

    rows = health.server_rows(db, now)
    f = health.fleet(rows)
    out = {"vpn_fleet": 0, "vpn_recovered": 0}
    key = "vpn:fleet:redundancy"
    # the same flap guard as the per-server offline alert: a server whose only problem is fewer than OFFLINE_AFTER
    # failed syncs in a row (a blip) still counts as healthy here, so one blip never pages the owner
    blips = sum(1 for r in rows if r["enabled"] and not r["testers_only"] and r["status"] == "degraded"
                and 0 < r["fail_streak"] < VPN_OFFLINE_AFTER and len(r["reasons"]) == 1)
    healthy = f["public_healthy"] + blips
    if vpn_settings(db).enabled and f["public_servers"] and healthy < VPN_MIN_HEALTHY:
        if not _is_open(db, key):
            text = (f"Price Chat alert: Kiwi VPN has {healthy} healthy public server(s)\n"
                    f"{f['public_up']} of {f['public_servers']} public servers are up. If one more fails, members have nowhere to fail over to.\n"
                    f"Check Admin › Kiwi VPN › Health.\nTime: {_when(now)}")
            out["vpn_fleet"] += await notify_owner(db, key, text, settings=settings, transport=transport, now=now, cooldown=False, open_=True)
    elif vpn_settings(db).enabled and f["public_servers"]:
        out["vpn_recovered"] += await _recovered(
            db, key, f"Price Chat: recovered\nKiwi VPN has {healthy} healthy public servers again.\nTime: {_when(now)}", settings, transport, now)
    else:
        _close(db, key)  # closed, or no public server: nothing to fail over between
    key = "vpn:fleet:capacity"
    util = f["utilization"]
    if util is not None and util >= VPN_FLEET_BUSY:
        if not _is_open(db, key):
            text = (f"Price Chat alert: Kiwi VPN capacity\nDevices fill {util * 100:.0f}% of the public servers' capacity "
                    f"({f['devices']} of {f['capacity']}).\nAdd a server or raise capacities.\nTime: {_when(now)}")
            out["vpn_fleet"] += await notify_owner(db, key, text, settings=settings, transport=transport, now=now, cooldown=False, open_=True)
    elif util is None or util < VPN_FLEET_CLEAR:
        out["vpn_recovered"] += await _recovered(
            db, key, f"Price Chat: recovered\nKiwi VPN capacity is back below {VPN_FLEET_CLEAR * 100:.0f}% used.\nTime: {_when(now)}", settings, transport, now)
    month = now.strftime("%Y-%m")
    for r in rows:
        share = r["traffic_projected_share"]
        if r["enabled"] and share is not None and share >= 1:
            key = f"vpn:traffic:{r['id']}:{month}:pace"
            if not _is_open(db, key):
                text = (f"Price Chat alert: Kiwi VPN traffic forecast\nServer: {r['name']} is on pace for {r['traffic_projected_gb']:.0f} GB "
                        f"this month, {share * 100:.0f}% of its {r['traffic_included_gb']} GB allowance ({r['traffic_month_gb']:.0f} GB so far).\n"
                        f"Traffic over the allowance is billed by the provider.\nTime: {_when(now)}")
                out["vpn_fleet"] += await notify_owner(db, key, text, settings=settings, transport=transport, now=now, cooldown=False, open_=True)
    return out


# --- admin -------------------------------------------------------------------------------------------------------------


NOWHERE = "Owner alerts are not being delivered"  # kept (pending:) and sent as one digest once they can be (2026-11-21)


def delivery_problem(db: Session, settings: Settings | None = None) -> str | None:
    """Why an owner alert can't be sent right now (it is kept for later), or None when it would be sent. Read-only."""
    settings = settings or get_settings()
    row = db.get(OwnerAlertSettings, 1)
    if row is None or not row.telegram_chat.strip():
        return f"{NOWHERE}: no owner Telegram chat is set (Admin › Settings › Background jobs › Owner alerts), so they are kept and sent as one digest once a chat is linked or found automatically"
    if not row.enabled:
        return f"{NOWHERE}: owner alerts are turned off (Admin › Settings › Background jobs › Owner alerts): they are kept and sent once alerts are turned back on"
    if not settings.telegram_configured:
        return f"{NOWHERE}: no Telegram bot token on this server, so alerts can't be sent"
    return None


def settings_out(db: Session, settings: Settings | None = None) -> dict[str, Any]:
    row = settings_row(db)
    recent = db.scalars(select(OwnerAlert).where(OwnerAlert.last_sent_at.is_not(None)).order_by(OwnerAlert.last_sent_at.desc()).limit(10))
    problem = delivery_problem(db, settings)
    return {"enabled": row.enabled, "telegram_chat": row.telegram_chat, "delivers": problem is None, "problem": problem,
            "recent": [{"key": a.key, "last_sent_at": a.last_sent_at, "sent_count": a.sent_count, "open": a.open,
                        "text": a.last_text} for a in recent]}
