"""Telegram account mode: configuration, login, discovery, ingestion, history import, logout.

Security:
- API Hash and the session string are kept in the encrypted secret store (DPAPI); they are never
  returned to the browser, logged, or given to an AI model. Login codes and 2FA passwords are only
  passed straight to Telegram and never stored or logged.
- Only sources on the allow-list (TelegramSource with via="account", enabled) are ever read.
- Private one-to-one chats are never listed or read.
"""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.models.enums import ActivityLevel
from app.models.telegram import TelegramAccount, TelegramMessage, TelegramSource
from app.security import credentials
from app.services import usage
from app.services.activity import ActivityLogger
from app.telegram.account import (
    AccountError,
    AccountGateway,
    DialogInfo,
    FloodWait,
    PasswordNeeded,
    SessionRevoked,
    TelethonGateway,
)
from app.telegram.ingestion import SyncReport, store_message

GatewayFactory = Callable[[int, str, str | None], AccountGateway]
LOGIN_TTL = timedelta(minutes=10)
IMPORT_HOURS = {24, 168, 720}  # last 24 h, 7 days, 30 days
IMPORT_CAP = 5000  # never more than this many messages in one import
LIVE_BATCH = 500  # new messages per source per sync


def default_factory(api_id: int, api_hash: str, session: str | None) -> AccountGateway:
    return TelethonGateway(api_id, api_hash, session)


@dataclass(slots=True)
class PendingLogin:
    gateway: AccountGateway
    phone: str
    phone_code_hash: str
    stage: str = "code"  # code | password | qr
    expires_at: datetime = field(default_factory=lambda: utcnow() + LOGIN_TTL)


@dataclass(slots=True)
class TelegramAccountRuntime:
    factory: GatewayFactory = default_factory
    pending: PendingLogin | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one Telegram session in use at a time
    discovered: list[DialogInfo] = field(default_factory=list)
    flood_until: datetime | None = None  # Telegram's FloodWait: no request before this (each early one extends the wait)


def get_account(db: Session) -> TelegramAccount:
    acc = db.scalar(select(TelegramAccount))
    if acc is None:
        acc = TelegramAccount(status="not_configured")
        db.add(acc)
        db.commit()
    return acc


def _store(settings: Settings):  # type: ignore[no-untyped-def]
    return credentials.secret_store(settings)


def has_session(settings: Settings) -> bool:
    return bool(_store(settings).get("telegram_session"))


def configured(db: Session, settings: Settings) -> bool:
    return bool(get_account(db).api_id and credentials.resolve("telegram_api_hash", settings))


def status(db: Session, settings: Settings, rt: TelegramAccountRuntime) -> dict[str, object]:
    acc = get_account(db)
    st = acc.status
    if not configured(db, settings):
        st = "not_configured"
    elif rt.pending is not None and rt.pending.expires_at > utcnow():
        st = {"password": "password_needed", "qr": "qr_waiting"}.get(rt.pending.stage, "code_sent")
    elif not has_session(settings) and st not in ("error",):
        st = "needs_login"
    info = credentials.info("telegram_api_hash", settings)
    return {
        "configured": configured(db, settings), "status": st, "api_id": acc.api_id,
        "api_hash": {"name": info.name, "configured": info.configured, "source": info.source, "saved_at": info.saved_at},
        "session": "active" if has_session(settings) else "none",
        "user_id": acc.user_id, "username": acc.username, "display_name": acc.display_name, "phone_hint": acc.phone_hint,
        "authorized_at": acc.authorized_at, "last_success_at": acc.last_success_at,
        "last_error": acc.last_error, "last_error_at": acc.last_error_at, "requests_total": acc.requests_total,
    }


def configure(db: Session, settings: Settings, api_id: int, api_hash: str | None) -> None:
    acc = get_account(db)
    changed = acc.api_id != api_id or bool(api_hash)
    if changed and has_session(settings):
        raise AccountError("Log out of the current Telegram session before changing the API ID / API Hash")
    acc.api_id = api_id
    if api_hash:
        _store(settings).set("telegram_api_hash", api_hash)
    acc.status = "needs_login"
    acc.last_error = None
    db.commit()
    ActivityLogger(db).log("telegram_account_configured", "Telegram API ID / API Hash saved (encrypted on this computer)",
                           level=ActivityLevel.AUDIT, actor="dashboard")


def _gateway(db: Session, settings: Settings, rt: TelegramAccountRuntime, session: str | None) -> AccountGateway:
    acc = get_account(db)
    api_hash = credentials.resolve("telegram_api_hash", settings)
    if not acc.api_id or not api_hash:
        raise AccountError("Save the Telegram API ID and API Hash first")
    return rt.factory(acc.api_id, api_hash, session)


def _count(db: Session, n: int = 1) -> None:
    acc = get_account(db)
    acc.requests_total = (acc.requests_total or 0) + n
    usage.bump("telegram", "account", n)


async def start_login(db: Session, settings: Settings, rt: TelegramAccountRuntime, phone: str) -> None:
    if rt.pending is not None:
        await _drop_pending(rt)
    gw = _gateway(db, settings, rt, None)
    await gw.connect()
    try:
        code_hash = await gw.send_code(phone)
    except Exception:
        await gw.disconnect()
        raise
    rt.pending = PendingLogin(gw, phone, code_hash)
    acc = get_account(db)
    acc.phone_hint = f"••••{phone[-2:]}" if len(phone) >= 2 else None
    acc.status = "code_sent"
    _count(db, 1)
    db.commit()


async def start_qr(db: Session, settings: Settings, rt: TelegramAccountRuntime) -> str:
    """Log in by scanning a QR code in the Telegram app (Settings › Devices › Link Desktop Device): no login code.
    Returns the tg://login URL to show as a QR; poll_qr() then checks every couple of seconds."""
    if rt.pending is not None:
        await _drop_pending(rt)
    gw = _gateway(db, settings, rt, None)
    await gw.connect()
    try:
        done, url = await gw.qr_check()
    except Exception:
        await gw.disconnect()
        raise
    rt.pending = PendingLogin(gw, "qr", "", stage="qr")
    if done:  # (can't really happen on a fresh session, but finish cleanly if it does)
        await _finish(db, settings, rt)
        return ""
    acc = get_account(db)
    acc.status = "qr_waiting"
    _count(db, 1)
    db.commit()
    return url or ""


async def poll_qr(db: Session, settings: Settings, rt: TelegramAccountRuntime) -> dict[str, str]:
    """{"status": "waiting", "url": ...} | {"status": "connected"} | {"status": "password_needed"}."""
    p = _require_pending(rt, "qr")
    try:
        done, url = await p.gateway.qr_check()
    except PasswordNeeded:
        p.stage = "password"
        acc = get_account(db)
        acc.status = "password_needed"
        db.commit()
        return {"status": "password_needed"}
    if done:
        await _finish(db, settings, rt)
        return {"status": "connected"}
    return {"status": "waiting", "url": url or ""}


def _require_pending(rt: TelegramAccountRuntime, stage: str) -> PendingLogin:
    p = rt.pending
    if p is None or p.expires_at < utcnow():
        raise AccountError("No login in progress (or it expired): enter your phone number again")
    if p.stage != stage:
        raise AccountError("Telegram is waiting for the two-step verification password" if p.stage == "password" else "Enter the login code first")
    return p


async def submit_code(db: Session, settings: Settings, rt: TelegramAccountRuntime, code: str) -> str:
    """Returns 'connected' or 'password_needed'."""
    p = _require_pending(rt, "code")
    try:
        await p.gateway.sign_in_code(p.phone, code, p.phone_code_hash)
    except PasswordNeeded:
        p.stage = "password"
        acc = get_account(db)
        acc.status = "password_needed"
        db.commit()
        return "password_needed"
    await _finish(db, settings, rt)
    return "connected"


async def submit_password(db: Session, settings: Settings, rt: TelegramAccountRuntime, password: str) -> str:
    p = _require_pending(rt, "password")
    await p.gateway.sign_in_password(password)
    await _finish(db, settings, rt)
    return "connected"


async def _finish(db: Session, settings: Settings, rt: TelegramAccountRuntime) -> None:
    p = rt.pending
    assert p is not None
    me = await p.gateway.me()
    _store(settings).set("telegram_session", p.gateway.session_string())
    acc = get_account(db)
    acc.user_id, acc.username, acc.display_name = me.user_id, me.username, me.display_name
    acc.phone_hint = me.phone_hint or acc.phone_hint
    acc.status, acc.authorized_at, acc.last_success_at, acc.last_error = "connected", utcnow(), utcnow(), None
    _count(db, 2)
    db.commit()
    await _drop_pending(rt)
    ActivityLogger(db).log("telegram_account_connected", f"Telegram account connected as {me.display_name}"
                           + (f" (@{me.username})" if me.username else ""), level=ActivityLevel.AUDIT, actor="dashboard")


async def _drop_pending(rt: TelegramAccountRuntime) -> None:
    p, rt.pending = rt.pending, None
    if p is not None:
        try:
            await p.gateway.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def cancel_login(rt: TelegramAccountRuntime) -> None:
    await _drop_pending(rt)


@asynccontextmanager
async def session(db: Session, settings: Settings, rt: TelegramAccountRuntime):  # type: ignore[no-untyped-def]
    """A connected, logged-in gateway from the saved session (one at a time)."""
    saved = _store(settings).get("telegram_session")
    if not saved:
        raise AccountError("No Telegram session: log in first")
    async with rt.lock:
        if rt.flood_until is not None and rt.flood_until > utcnow():
            raise FloodWait(int((rt.flood_until - utcnow()).total_seconds()) + 1)
        gw = _gateway(db, settings, rt, saved)
        try:
            await gw.connect()
            if not await gw.is_authorized():
                raise SessionRevoked("The Telegram session is no longer valid; log in again")
            yield gw
            acc = get_account(db)
            acc.last_success_at, acc.status, acc.last_error = utcnow(), "connected", None
        except SessionRevoked as exc:
            _store(settings).delete("telegram_session")
            _record_error(db, exc, "needs_login")
            raise
        except AccountError as exc:
            if isinstance(exc, FloodWait):
                rt.flood_until = utcnow() + timedelta(seconds=exc.seconds)
            _record_error(db, exc, "error" if not isinstance(exc, FloodWait) else get_account(db).status)
            raise
        finally:
            await gw.disconnect()


def _record_error(db: Session, exc: Exception, new_status: str) -> None:
    acc = get_account(db)
    acc.status, acc.last_error, acc.last_error_at = new_status, str(exc), utcnow()


async def discover(db: Session, settings: Settings, rt: TelegramAccountRuntime) -> list[DialogInfo]:
    async with session(db, settings, rt) as gw:
        dialogs = await gw.dialogs()
        _count(db)
    rt.discovered = dialogs
    db.commit()
    return dialogs


def _dialog(src: TelegramSource) -> DialogInfo:
    return DialogInfo(src.chat_id, src.display_name, src.chat_type or "supergroup", src.username, None)


def _newest_id(db: Session, chat_id: int) -> int:
    return int(db.scalar(select(func.max(TelegramMessage.message_id)).where(TelegramMessage.chat_id == chat_id)) or 0)


async def sync(db: Session, settings: Settings, rt: TelegramAccountRuntime) -> SyncReport:
    """Read new messages from every enabled, account-mode source on the allow-list."""
    report = SyncReport()
    sources = list(db.scalars(select(TelegramSource).where(TelegramSource.via == "account", TelegramSource.enabled.is_(True),
                                                            TelegramSource.ingestion_enabled.is_(True))))
    if not sources:
        return report
    fetched: list[tuple[TelegramSource, list]] = []
    async with session(db, settings, rt) as gw:
        for src in sources:
            newest = _newest_id(db, src.chat_id)
            # "Start from now": a newly authorised source only gets messages sent after it was authorised.
            since = None if newest else src.created_at
            try:
                msgs = await gw.messages(_dialog(src), min_id=newest, since=since, limit=LIVE_BATCH)
                fetched.append((src, msgs))
                src.last_error = None
            except FloodWait:
                raise
            except AccountError as exc:
                src.last_error, src.last_error_at = str(exc), utcnow()
            _count(db)
    # Network first, then one short write: no database transaction is held while waiting on Telegram.
    now = utcnow()
    for src, msgs in fetched:
        for m in msgs:
            store_message(db, src, m, report)
        src.last_sync_at = now
    db.commit()
    return report


async def estimate(db: Session, settings: Settings, rt: TelegramAccountRuntime, src: TelegramSource, hours: int) -> int | None:
    if hours not in IMPORT_HOURS:
        raise AccountError("Choose 24 hours, 7 days or 30 days")
    async with session(db, settings, rt) as gw:
        n = await gw.estimate_since(_dialog(src), utcnow() - timedelta(hours=hours))
        _count(db, 2)
    db.commit()
    return n


async def import_history(db: Session, settings: Settings, rt: TelegramAccountRuntime, src: TelegramSource, hours: int) -> dict[str, object]:
    """Import recent history for one authorised source, bounded by time and IMPORT_CAP messages."""
    if hours not in IMPORT_HOURS:
        raise AccountError("Choose 24 hours, 7 days or 30 days")
    if src.via != "account" or not src.enabled:
        raise AccountError("History import is only available for enabled sources read through the Telegram account")
    since = utcnow() - timedelta(hours=hours)
    async with session(db, settings, rt) as gw:
        msgs = await gw.messages(_dialog(src), min_id=0, since=since, limit=IMPORT_CAP)
        _count(db, max(1, len(msgs) // 100 + 1))
    report = SyncReport()
    for m in msgs:
        store_message(db, src, m, report)
    src.last_sync_at = utcnow()
    db.commit()
    ActivityLogger(db).log("telegram_history_imported", f"Imported {report.stored} messages from the last {hours // 24 or 1} "
                           f"{'day' if hours <= 24 else 'days'} of '{src.display_name}' ({report.duplicates} already stored)",
                           level=ActivityLevel.AUDIT, actor="dashboard")
    return {"stored": report.stored, "duplicates": report.duplicates, "capped": len(msgs) >= IMPORT_CAP, "cap": IMPORT_CAP}


async def logout(db: Session, settings: Settings, rt: TelegramAccountRuntime) -> None:
    """End the Telegram session on Telegram's side too (it disappears from Active Sessions), then delete it locally."""
    await _drop_pending(rt)
    saved = _store(settings).get("telegram_session")
    if saved:
        try:
            async with rt.lock:
                gw = _gateway(db, settings, rt, saved)
                await gw.connect()
                try:
                    await gw.log_out()
                finally:
                    await gw.disconnect()
        except Exception:  # noqa: BLE001 - even if Telegram can't be reached, the local session is deleted
            pass
    _store(settings).delete("telegram_session")
    acc = get_account(db)
    acc.status = "needs_login" if configured(db, settings) else "not_configured"
    acc.user_id = acc.username = acc.display_name = None
    acc.authorized_at = None
    rt.discovered = []
    db.commit()
    ActivityLogger(db).log("telegram_account_disconnected", "Telegram account session logged out and deleted from this computer",
                           level=ActivityLevel.AUDIT, actor="dashboard")


def forget_credentials(db: Session, settings: Settings) -> None:
    if has_session(settings):
        raise AccountError("Log out first")
    _store(settings).delete("telegram_api_hash")
    acc = get_account(db)
    acc.api_id, acc.status, acc.phone_hint = None, "not_configured", None
    db.commit()
