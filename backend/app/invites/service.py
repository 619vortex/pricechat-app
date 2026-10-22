"""Invite Bot logic. Rules (enforced here, shown in the dashboard):

  - private invitations ONLY to people who explicitly asked (messaged the account, or wrote an opt-in keyword in an
    approved group); at most one ever per person across all accounts, from the account they contacted;
  - never again after a "no"/"stop", a block, or a failed delivery;
  - group posts ONLY in groups marked "admins allow posts", at most once per GROUP_POST_INTERVAL_DAYS;
  - at most `daily_limit` (≤ 20) invitations per account per day;
  - any Telegram rate limit / spam warning stops the whole run and pauses that account; while any account is paused no
    account sends (never rotating accounts around a restriction).
"""

import asyncio
import base64
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.invites import gateway as gw
from app.models.invites import InviteAccount, InviteConsent, InviteGroup, InviteMember, InviteSend, InviteSettings
from app.security import credentials
from app.security.secrets import protect, unprotect

log = logging.getLogger(__name__)
DEFAULT_DM = ("Hey bro! 👋 We just launched Crypto Price Chat, a new community for crypto discussions and market updates. "
              "You're welcome to join: {link}")
DEFAULT_GROUP = "👋 Crypto Price Chat just launched: a community for crypto discussions and market updates. Everyone's welcome: {link}"
HASH_SECRET = "invite_api_hash"
LOGIN_TTL = timedelta(minutes=10)
MIN_GAP, MAX_GAP = 10, 3600  # the dashboard's interval bar: 1 invitation every 10 s .. every 1 h


class InviteError(ValueError):
    pass


@dataclass
class Pending:
    gateway: Any
    phone: str
    code_hash: str
    stage: str = "code"
    expires_at: datetime = field(default_factory=lambda: utcnow() + LOGIN_TTL)


@dataclass
class Runtime:
    factory: Callable[..., Any] = gw.default_factory
    pending: dict[str, Pending] = field(default_factory=dict)
    locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    run: dict[str, Any] = field(default_factory=lambda: {"state": "idle"})
    stop: bool = False
    pace: tuple[float, float] | None = None  # tests only: overrides the configured gap

    def lock(self, account_id: int) -> asyncio.Lock:
        return self.locks.setdefault(account_id, asyncio.Lock())


RT = Runtime()


# --- settings ---------------------------------------------------------------------------------------------------------


def settings_row(db: Session) -> InviteSettings:
    s = db.scalar(select(InviteSettings))
    if s is None:
        s = InviteSettings(dm_message=DEFAULT_DM, group_message=DEFAULT_GROUP)
        db.add(s)
        db.commit()
    return s


def words(raw: str) -> list[str]:
    return [w.strip().lower() for w in raw.split(",") if w.strip()]


def text_for(s: InviteSettings, kind: str) -> str:
    msg = s.dm_message if kind == "dm" else s.group_message
    return msg.replace("{link}", s.invite_link) if "{link}" in msg else f"{msg.rstrip()} {s.invite_link}"  # the link is always included


def api(db: Session, settings: Settings) -> tuple[int, str]:
    s = settings_row(db)
    h = credentials.secret_store(settings).get(HASH_SECRET)
    if not s.api_id or not h:
        raise InviteError("Save the Telegram API ID and API hash first (from my.telegram.org › API development tools).")
    return s.api_id, h


def update_settings(db: Session, settings: Settings, **v: Any) -> InviteSettings:
    s = settings_row(db)
    if v.get("api_hash"):
        credentials.secret_store(settings).set(HASH_SECRET, v.pop("api_hash").strip())
    v.pop("api_hash", None)
    if "daily_limit" in v and v["daily_limit"] is not None and not 1 <= v["daily_limit"] <= 20:
        raise InviteError("The daily limit must be between 1 and 20 per account.")
    if v.get("dm_interval_seconds") is not None and not MIN_GAP <= v["dm_interval_seconds"] <= MAX_GAP:
        raise InviteError("The interval must be between 10 seconds and 1 hour.")
    for k, val in v.items():
        if val is not None:
            setattr(s, k, val)
    db.commit()
    return s


def _enc(session: str) -> str:
    return base64.b64encode(protect(session.encode())).decode()


def _dec(blob: str) -> str:
    return unprotect(base64.b64decode(blob)).decode()


# --- accounts ----------------------------------------------------------------------------------------------------------


async def start_login(db: Session, settings: Settings, name: str, phone: str) -> None:
    name = name.strip()[:40]
    if not name:
        raise InviteError("Give the account a short name.")
    api_id, api_hash = api(db, settings)
    old = RT.pending.pop(name, None)
    if old:
        await old.gateway.stop()
    g = RT.factory(api_id, api_hash)
    await g.start()
    code_hash = await g.send_code(phone.strip())
    RT.pending[name] = Pending(g, phone.strip(), code_hash)


def _pending(name: str, stage: str) -> Pending:
    p = RT.pending.get(name)
    if p is None or p.expires_at < utcnow():
        raise InviteError("No login in progress for this account (or it expired). Start again.")
    if p.stage != stage:
        raise InviteError("Enter the two-step verification password." if p.stage == "password" else "Enter the login code first.")
    return p


async def submit_code(db: Session, name: str, code: str) -> str:
    p = _pending(name, "code")
    try:
        await p.gateway.sign_in(p.phone, code.strip(), p.code_hash)
    except gw.NeedPassword:
        p.stage = "password"
        return "password_needed"
    await _finish(db, name, p)
    return "connected"


async def submit_password(db: Session, name: str, password: str) -> str:
    p = _pending(name, "password")
    await p.gateway.sign_in_password(password)
    await _finish(db, name, p)
    return "connected"


async def _finish(db: Session, name: str, p: Pending) -> None:
    uid, username = await p.gateway.me()
    session = p.gateway.session()
    await p.gateway.stop()
    RT.pending.pop(name, None)
    a = db.scalar(select(InviteAccount).where(InviteAccount.name == name))
    if a is None:
        a = InviteAccount(name=name)
        db.add(a)
    a.phone_hint, a.tg_user_id, a.tg_username = p.phone[-2:], uid, username
    a.session_enc, a.status, a.last_error = _enc(session), "connected", None
    db.commit()


async def cancel_login(name: str) -> None:
    p = RT.pending.pop(name, None)
    if p:
        await p.gateway.stop()


def disconnect(db: Session, account_id: int) -> None:
    a = db.get(InviteAccount, account_id)
    if a is None:
        raise InviteError("Not found.")
    a.session_enc, a.status = None, "disconnected"  # credentials deleted; history and consents kept
    db.commit()


async def _open(db: Session, settings: Settings, a: InviteAccount) -> Any:
    if not a.session_enc:
        raise InviteError(f"{a.name} isn't connected.")
    api_id, api_hash = api(db, settings)
    g = RT.factory(api_id, api_hash, _dec(a.session_enc))
    if not await g.start():
        await g.stop()
        a.status, a.last_error = "error", "Session no longer authorised: log in again."
        db.commit()
        raise InviteError(f"{a.name}: the Telegram session is no longer authorised. Log in again.")
    return g


async def account_groups(db: Session, settings: Settings, account_id: int) -> list[dict[str, Any]]:
    """Groups this account is ALREADY a member of (it never joins anything)."""
    a = db.get(InviteAccount, account_id)
    if a is None:
        raise InviteError("Not found.")
    async with RT.lock(a.id):
        g = await _open(db, settings, a)
        try:
            found = await g.groups()
        finally:
            await g.stop()
    approved = {x.chat_id for x in db.scalars(select(InviteGroup).where(InviteGroup.account_id == a.id))}
    return [{"chat_id": x.chat_id, "title": x.title, "username": x.username, "approved": x.chat_id in approved} for x in found]


def approve_group(db: Session, account_id: int, chat_id: int, title: str, username: str | None, allow_posts: bool) -> InviteGroup:
    g = db.scalar(select(InviteGroup).where(InviteGroup.account_id == account_id, InviteGroup.chat_id == chat_id))
    if g is None:
        g = InviteGroup(account_id=account_id, chat_id=chat_id, title=title[:200], username=username)
        db.add(g)
    g.allow_posts = allow_posts
    db.commit()
    return g


# --- consent & activity -----------------------------------------------------------------------------------------------


def seen(db: Session, user_id: int, username: str | None, group_id: int, at: datetime | None = None) -> None:
    m = db.get(InviteMember, user_id)
    if m is None:
        db.add(InviteMember(user_id=user_id, username=username, group_id=group_id, last_active_at=at or utcnow()))
        db.flush()  # autoflush is off: the same person writing twice in one batch must update, not insert again
    else:
        m.username, m.group_id, m.last_active_at = username, group_id, max(m.last_active_at, at or utcnow())


def consent(db: Session, user_id: int, account_id: int, username: str | None, kind: str) -> bool:
    if user_id in gw.SERVICE_IDS:
        return False  # Telegram's own service accounts never "ask" for anything
    if db.get(InviteConsent, user_id) is not None:
        return False  # already asked, already invited, or said no: never re-opened
    db.add(InviteConsent(user_id=user_id, account_id=account_id, username=username, kind=kind, given_at=utcnow()))
    db.flush()
    return True


def opt_out(db: Session, user_id: int, reason: str, account_id: int) -> None:
    c = db.get(InviteConsent, user_id)
    if c is None:
        db.add(InviteConsent(user_id=user_id, account_id=account_id, kind="none", given_at=utcnow(), revoked_at=utcnow(), revoked_reason=reason[:100]))
    elif c.revoked_at is None:
        c.revoked_at, c.revoked_reason = utcnow(), reason[:100]
    db.flush()


def is_opt_out(s: InviteSettings, text: str) -> bool:
    t = text.strip().lower()
    return t in words(s.opt_out_words) or t.startswith("/stop")


def has_keyword(s: InviteSettings, text: str) -> bool:
    t = text.lower()
    return any(k in t for k in words(s.opt_in_keywords))


async def poll(db: Session, settings: Settings) -> dict[str, int]:
    """Scheduled (every few minutes): new messages in approved groups (activity + opt-in keywords) and new private
    messages (consent / opt-out). Reads only; sends nothing."""
    s = settings_row(db)
    out = {"accounts": 0, "messages": 0, "opt_ins": 0, "opt_outs": 0}
    if not s.monitoring:
        return out
    try:
        api(db, settings)
    except InviteError:
        return out
    for a in db.scalars(select(InviteAccount).where(InviteAccount.status == "connected", InviteAccount.session_enc.is_not(None))).all():
        lock = RT.lock(a.id)
        if lock.locked():  # a send is using this session: try next time
            continue
        async with lock:
            try:
                g = await _open(db, settings, a)
            except InviteError as exc:
                log.warning("%s", exc)
                continue
            try:
                since = a.last_poll_at or (utcnow() - timedelta(hours=1))
                started = utcnow()
                for grp in db.scalars(select(InviteGroup).where(InviteGroup.account_id == a.id)).all():
                    first = grp.last_message_id == 0
                    msgs = await g.group_messages(grp.chat_id, grp.last_message_id, 50 if first else 300)
                    for m in sorted(msgs, key=lambda m: m.id):
                        grp.last_message_id = max(grp.last_message_id, m.id)
                        if m.is_bot:
                            continue
                        seen(db, m.sender_id, m.username, grp.id, m.at)
                        out["messages"] += 1
                        if not first and has_keyword(s, m.text) and consent(db, m.sender_id, a.id, m.username, "keyword"):
                            out["opt_ins"] += 1  # only keywords written after the group was approved count
                    db.commit()  # this group's batch is saved before the next Telegram read: no write held open across it
                for m in sorted(await g.private_messages(since, 100), key=lambda m: m.at):
                    contacted = db.scalar(select(InviteSend).where(InviteSend.user_id == m.sender_id, InviteSend.method.in_(("dm", "personal")),
                                                                   InviteSend.status.in_(("sent", "opened"))).order_by(InviteSend.id.desc()))
                    if contacted is not None and contacted.replied_at is None and m.at >= contacted.at:
                        contacted.replied_at = m.at  # reply tracking
                    if is_opt_out(s, m.text):
                        opt_out(db, m.sender_id, "said no / stop", a.id)
                        out["opt_outs"] += 1
                    elif contacted is None and consent(db, m.sender_id, a.id, m.username, "dm"):
                        out["opt_ins"] += 1
                a.last_poll_at, a.last_error = started, None
                out["accounts"] += 1
            except Exception as exc:  # noqa: BLE001 - one account's problem doesn't stop the others
                a.last_error = str(exc)[:300]
                log.warning("Invite Bot poll %s: %s", a.name, exc)
            finally:
                await g.stop()
                db.commit()
    cutoff = utcnow() - timedelta(days=s.retention_days)
    db.query(InviteMember).filter(InviteMember.last_active_at < cutoff).delete()
    db.commit()
    return out


# --- invitations ---------------------------------------------------------------------------------------------------------


def _day_start() -> datetime:
    n = utcnow()
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def sent_today(db: Session, account_id: int) -> int:
    return db.scalar(select(func.count()).select_from(InviteSend).where(InviteSend.account_id == account_id, InviteSend.status.in_(("sent", "opened")),
                                                                        InviteSend.at >= _day_start())) or 0  # bot sends + personal ones


def queue(db: Session, account_id: int) -> list[InviteConsent]:
    """People this account may DM: they ASKED, and they chatted recently in one of the watched groups (so a random
    contact who messages the account is never invited). Never twice, never after a no."""
    invited = select(InviteSend.user_id).where(InviteSend.method.in_(("dm", "personal")), InviteSend.user_id.is_not(None))
    st = settings_row(db)
    days = st.active_days
    active = (select(InviteMember.user_id).join(InviteGroup, InviteGroup.id == InviteMember.group_id)
              .where(InviteMember.last_active_at >= utcnow() - timedelta(days=days)))
    return list(db.scalars(select(InviteConsent).where(InviteConsent.account_id == account_id, InviteConsent.revoked_at.is_(None),
                                                       InviteConsent.user_id.not_in(gw.SERVICE_IDS),
                                                       # active in a watched group, or (switch on) they messaged the account themselves
                                                       or_(InviteConsent.user_id.in_(active), and_(InviteConsent.kind == "dm", st.invite_direct_contacts)),
                                                       InviteConsent.kind.in_(("dm", "keyword")), InviteConsent.user_id.not_in(invited))
                           .order_by(InviteConsent.given_at)))


def gap(s: InviteSettings) -> float:
    """Seconds to wait after an invitation: the configured gap with ±15% jitter (never a fixed robotic rhythm),
    always within 10 s .. 1 h."""
    if RT.pace is not None:
        return random.uniform(*RT.pace)
    base = min(MAX_GAP, max(MIN_GAP, s.dm_interval_seconds))
    return min(MAX_GAP, max(MIN_GAP, base * random.uniform(0.85, 1.15)))


async def _wait(seconds: float) -> bool:
    """Sleeps in short steps so Stop works immediately. False when stopped."""
    end = asyncio.get_running_loop().time() + seconds
    while (left := end - asyncio.get_running_loop().time()) > 0:
        if RT.stop:
            return False
        await asyncio.sleep(min(1.0, left))
    return not RT.stop


def posts_due(db: Session, account_id: int, interval_days: int, enabled: bool = True) -> list[InviteGroup]:
    if not enabled:
        return []  # group posts switched off: DM-only
    cutoff = utcnow() - timedelta(days=interval_days)
    return list(db.scalars(select(InviteGroup).where(InviteGroup.account_id == account_id, InviteGroup.allow_posts.is_(True),
                                                     (InviteGroup.last_post_at.is_(None)) | (InviteGroup.last_post_at < cutoff))))


def halted(a: InviteAccount) -> bool:
    return bool(a.halted_until and a.halted_until > utcnow())


def _record(db: Session, a: InviteAccount, method: str, status: str, user_id: int | None = None, group: InviteGroup | None = None,
            error: str | None = None) -> None:
    db.add(InviteSend(account_id=a.id, method=method, user_id=user_id, group_id=group.id if group else None, status=status,
                      error=(error or "")[:300] or None, at=utcnow()))
    if group is not None and status == "sent":
        group.last_post_at = utcnow()
    db.commit()


def _note(msg: str) -> None:
    RT.run.setdefault("log", []).append(f"{utcnow().strftime('%H:%M:%S')} {msg}")
    RT.run["log"] = RT.run["log"][-80:]
    log.info("Invite Bot: %s", msg)


async def send_approved(db: Session, settings: Settings, dry_run: bool = False, only_users: set[int] | None = None) -> dict[str, int]:
    """Sends the queue (or, with `only_users`, just the people the owner approved by hand)."""
    s = settings_row(db)
    totals = {"dm_sent": 0, "group_sent": 0, "failed": 0, "skipped_limit": 0}
    accounts = db.scalars(select(InviteAccount).order_by(InviteAccount.id)).all()
    paused = [a for a in accounts if halted(a)]
    if paused:
        for a in paused:
            _note(f"{a.name} is paused until {a.halted_until:%Y-%m-%d %H:%M} UTC ({a.halt_reason}): nothing is sent from any account.")
        return totals
    RT.stop = False
    for a in [a for a in accounts if a.status == "connected" and a.session_enc]:
        left = s.daily_limit - sent_today(db, a.id)
        work: list[tuple[str, Any]] = [("dm", c) for c in queue(db, a.id) if only_users is None or c.user_id in only_users]
        if only_users is None:
            work += [("group", g) for g in posts_due(db, a.id, s.group_post_interval_days, s.group_posts_enabled)]
        if not work:
            continue
        if left <= 0:
            totals["skipped_limit"] += len(work)
            _note(f"{a.name}: daily limit ({s.daily_limit}) reached")
            continue
        async with RT.lock(a.id):
            g = None if dry_run else await _open(db, settings, a)
            try:
                for kind, target in work:
                    if RT.stop:
                        _note("Stopped by you.")
                        return totals
                    if left <= 0:
                        totals["skipped_limit"] += 1
                        continue
                    label = (f"@{target.username}" if target.username else f"user {target.user_id}") if kind == "dm" else target.title
                    if dry_run:
                        _note(f"[dry run] {a.name} would invite {label}")
                        left -= 1
                        continue
                    try:
                        await g.send(target.username or target.user_id if kind == "dm" else target.chat_id, text_for(s, kind))
                    except gw.RateLimited as exc:
                        a.halted_until = utcnow() + timedelta(seconds=max(exc.seconds, 3600))
                        a.halt_reason = str(exc)[:200]
                        db.commit()
                        _note(f"STOPPED: {a.name}: {exc}. Nothing more is sent from any account.")
                        return totals
                    except (gw.Unreachable, gw.NotAllowed, Exception) as exc:  # noqa: BLE001 - recorded, never retried
                        if kind == "dm":
                            opt_out(db, target.user_id, f"not delivered: {type(exc).__name__}", a.id)
                        elif isinstance(exc, gw.NotAllowed):
                            target.allow_posts = False  # no longer permitted there: stop posting
                        _record(db, a, kind, "failed", user_id=target.user_id if kind == "dm" else None, group=target if kind == "group" else None, error=str(exc))
                        totals["failed"] += 1
                        _note(f"{a.name}: couldn't invite {label} ({exc})")
                        continue
                    _record(db, a, kind, "sent", user_id=target.user_id if kind == "dm" else None, group=target if kind == "group" else None)
                    totals["dm_sent" if kind == "dm" else "group_sent"] += 1
                    left -= 1
                    _note(f"{a.name}: invited {label}")
                    wait = gap(s)
                    if wait >= 30:
                        _note(f"next invitation in {int(wait // 60)} min {int(wait % 60)} s")
                    if not await _wait(wait):
                        _note("Stopped by you.")
                        return totals
            finally:
                if g is not None:
                    await g.stop()
    return totals


def start_run(bind: Any, settings: Settings, dry_run: bool, only_users: set[int] | None = None, trigger: str = "manual") -> dict[str, Any]:
    if RT.run.get("state") == "running":
        raise InviteError("A send is already running.")
    RT.run = {"state": "running", "dry_run": dry_run, "trigger": trigger, "started_at": utcnow().isoformat(), "log": []}

    async def go() -> None:
        with Session(bind=bind) as db:
            try:
                totals = await send_approved(db, settings, dry_run, only_users)
                RT.run.update(state="done", totals=totals)
            except Exception as exc:  # noqa: BLE001
                RT.run.update(state="error", error=str(exc)[:300])
                _note(f"Error: {exc}")
            finally:
                RT.run["finished_at"] = utcnow().isoformat()

    RT.run["task"] = asyncio.get_running_loop().create_task(go())
    return run_status()


def has_work(db: Session) -> bool:
    s = settings_row(db)
    for a in db.scalars(select(InviteAccount).where(InviteAccount.status == "connected", InviteAccount.session_enc.is_not(None))):
        if halted(a) or sent_today(db, a.id) >= s.daily_limit:
            continue
        if queue(db, a.id) or posts_due(db, a.id, s.group_post_interval_days, s.group_posts_enabled):
            return True
    return False


def maybe_auto_send(db: Session, settings: Settings) -> bool:
    """Auto mode: after each check, start sending if someone is waiting (same rules, pace and limits as a manual send).
    Never while a run is going, an account is paused, or Auto is off."""
    s = settings_row(db)
    if not s.auto_send or RT.run.get("state") == "running" or any(halted(a) for a in db.scalars(select(InviteAccount))):
        return False
    if not has_work(db):
        return False
    start_run(db.get_bind(), settings, False, trigger="auto")
    return True


def decline(db: Session, user_id: int) -> None:
    """Manual mode: the owner chose not to invite this person (never queued again)."""
    c = db.get(InviteConsent, user_id)
    if c is None:
        raise InviteError("Not found.")
    c.revoked_at, c.revoked_reason = utcnow(), "skipped by owner"
    db.commit()


def run_status() -> dict[str, Any]:
    return {k: v for k, v in RT.run.items() if k != "task"}


# --- views ----------------------------------------------------------------------------------------------------------------


def overview(db: Session, settings: Settings) -> dict[str, Any]:
    s = settings_row(db)
    hash_saved = credentials.secret_store(settings).saved_at(HASH_SECRET) is not None
    accounts = db.scalars(select(InviteAccount).order_by(InviteAccount.id)).all()
    groups = db.scalars(select(InviteGroup).order_by(InviteGroup.id)).all()

    def n(*where: Any) -> int:
        return db.scalar(select(func.count()).select_from(InviteSend).where(*where)) or 0

    return {
        "settings": {"api_id": s.api_id, "api_hash_saved": hash_saved, "invite_link": s.invite_link, "dm_message": s.dm_message,
                     "group_message": s.group_message, "daily_limit": s.daily_limit, "group_post_interval_days": s.group_post_interval_days,
                     "opt_in_keywords": s.opt_in_keywords, "opt_out_words": s.opt_out_words, "active_days": s.active_days,
                     "retention_days": s.retention_days, "monitoring": s.monitoring, "group_posts_enabled": s.group_posts_enabled, "auto_send": s.auto_send, "invite_direct_contacts": s.invite_direct_contacts,
                     "dm_interval_seconds": s.dm_interval_seconds},
        "accounts": [{"id": a.id, "name": a.name, "telegram": f"@{a.tg_username}" if a.tg_username else a.tg_user_id, "phone_hint": a.phone_hint,
                      "status": "paused" if halted(a) else a.status, "paused_until": a.halted_until.isoformat() if halted(a) and a.halted_until else None,
                      "pause_reason": a.halt_reason if halted(a) else None, "sent_today": sent_today(db, a.id), "daily_limit": s.daily_limit,
                      "groups": sum(1 for g in groups if g.account_id == a.id), "queue": len(queue(db, a.id)), "last_poll_at": a.last_poll_at.isoformat() if a.last_poll_at else None,
                      "last_error": a.last_error, "sent_total": n(InviteSend.account_id == a.id, InviteSend.status == "sent"),
                      "failed_total": n(InviteSend.account_id == a.id, InviteSend.status == "failed")} for a in accounts],
        "pending_logins": {k: p.stage for k, p in RT.pending.items() if p.expires_at > utcnow()},
        "groups": [{"id": g.id, "account_id": g.account_id, "title": g.title, "username": g.username, "allow_posts": g.allow_posts,
                    "last_post_at": g.last_post_at.isoformat() if g.last_post_at else None} for g in groups],
        "stats": {"members": db.scalar(select(func.count()).select_from(InviteMember)) or 0,
                  "opt_ins": db.scalar(select(func.count()).select_from(InviteConsent).where(InviteConsent.revoked_at.is_(None), InviteConsent.kind != "none")) or 0,
                  "opt_outs": db.scalar(select(func.count()).select_from(InviteConsent).where(InviteConsent.revoked_at.is_not(None))) or 0,
                  "dm_sent": n(InviteSend.method == "dm", InviteSend.status == "sent"), "group_sent": n(InviteSend.method == "group", InviteSend.status == "sent"),
                  "failed": n(InviteSend.status == "failed")},
        "run": run_status(),
    }


def members(db: Session, days: int) -> list[dict[str, Any]]:
    rows = db.execute(select(InviteMember, InviteGroup.title, InviteConsent).outerjoin(InviteGroup, InviteGroup.id == InviteMember.group_id)
                      .outerjoin(InviteConsent, InviteConsent.user_id == InviteMember.user_id)
                      .where(InviteMember.last_active_at >= utcnow() - timedelta(days=days)).order_by(InviteMember.last_active_at.desc()).limit(500)).all()
    sends = {u: st for u, st in db.execute(select(InviteSend.user_id, InviteSend.status).where(InviteSend.method == "dm").order_by(InviteSend.id))}
    return [{"username": m.username, "group": title, "last_active_at": m.last_active_at.isoformat(),
             "opted_in": "opted out" if c and c.revoked_at else "yes" if c else "no", "invitation": sends.get(m.user_id)} for m, title, c in rows]


def queue_view(db: Session) -> list[dict[str, Any]]:
    s = settings_row(db)
    out = []
    for a in db.scalars(select(InviteAccount).where(InviteAccount.status == "connected").order_by(InviteAccount.id)):
        out += [{"account": a.name, "user_id": c.user_id, "target": f"@{c.username}" if c.username else f"user {c.user_id}", "how": "messaged the account" if c.kind == "dm" else "asked in a group",
                 "since": c.given_at.isoformat()} for c in queue(db, a.id)]
        out += [{"account": a.name, "target": f"Group post: {g.title}", "how": "admins allow posts",
                 "since": g.last_post_at.isoformat() if g.last_post_at else None} for g in posts_due(db, a.id, s.group_post_interval_days, s.group_posts_enabled)]
    return out


# --- one-click tool: one recipient at a time ---------------------------------------------------------------------------------


def _contacted_or_skipped() -> Any:
    return select(InviteSend.user_id).where(InviteSend.user_id.is_not(None), InviteSend.method.in_(("dm", "personal", "skip")))


def next_recipient(db: Session, account_id: int, exclude: set[int] | None = None) -> dict[str, Any] | None:
    """The next person to review for this account.

    1. People who ASKED (the queue): the bot sends the invitation itself ("auto").
    2. Otherwise, recently active members of THIS account's watched groups with a public @username: the tool only opens
       their Telegram chat with the message typed in; YOU decide and press send ("personal"). Never sent by the bot.
    Never: bots / Telegram's accounts, anyone already invited by any account, skipped, or who opted out."""
    s = settings_row(db)
    a = db.get(InviteAccount, account_id)
    if a is None or a.status != "connected":
        raise InviteError("Choose a connected account.")
    exclude = exclude or set()
    for c in queue(db, account_id):
        if c.user_id in exclude:
            continue
        m = db.get(InviteMember, c.user_id)
        g = db.get(InviteGroup, m.group_id) if m and m.group_id else None
        return {"user_id": c.user_id, "username": c.username or (m.username if m else None), "group": g.title if g else None,
                "last_active_at": (m.last_active_at if m else c.given_at).isoformat(), "mode": "auto",
                "why": "messaged the account" if c.kind == "dm" else "asked in a group"}
    any_consent = select(InviteConsent.user_id)  # asked (another account's), opted out or skipped: not a suggestion
    row = db.execute(select(InviteMember, InviteGroup.title).join(InviteGroup, InviteGroup.id == InviteMember.group_id)
                     .where(InviteGroup.chat_id.in_(select(InviteGroup.chat_id).where(InviteGroup.account_id == account_id)),  # chats this account watches
                            InviteMember.username.is_not(None),
                            InviteMember.last_active_at >= utcnow() - timedelta(days=s.active_days),
                            InviteMember.user_id.not_in(gw.SERVICE_IDS), InviteMember.user_id.not_in(_contacted_or_skipped()),
                            InviteMember.user_id.not_in(any_consent), InviteMember.user_id.not_in(exclude or {0}))
                     .order_by(InviteMember.last_active_at.desc()).limit(1)).first()
    if row is None:
        return None
    m, title = row
    return {"user_id": m.user_id, "username": m.username, "group": title, "last_active_at": m.last_active_at.isoformat(),
            "mode": "personal", "why": "recently active in a watched group"}


def one_view(db: Session, account_id: int, exclude: set[int] | None = None) -> dict[str, Any]:
    from urllib.parse import quote

    s = settings_row(db)
    a = db.get(InviteAccount, account_id)
    rec = next_recipient(db, account_id, exclude)
    text = text_for(s, "dm")
    if rec is not None and rec["mode"] == "personal":
        rec["open_url"] = f"https://t.me/{rec['username']}?text={quote(text)}"
    assert a is not None
    return {"account": {"id": a.id, "name": a.name, "telegram": f"@{a.tg_username}" if a.tg_username else None},
            "recipient": rec, "message": text, "sent_today": sent_today(db, account_id), "daily_limit": s.daily_limit,
            "paused_until": a.halted_until.isoformat() if halted(a) and a.halted_until else None}


async def one_send(db: Session, settings: Settings, account_id: int, user_id: int) -> dict[str, Any]:
    """SEND MESSAGE for one person. 'auto' (they asked): the bot sends it now. 'personal': only recorded; the page opens
    their chat with the text ready and you press send in Telegram yourself."""
    s = settings_row(db)
    a = db.get(InviteAccount, account_id)
    if a is None or a.status != "connected":
        raise InviteError("Choose a connected account.")
    if any(halted(x) for x in db.scalars(select(InviteAccount))):
        raise InviteError("Paused: Telegram asked an account to slow down. Nothing is sent until the pause ends.")
    if sent_today(db, a.id) >= s.daily_limit:
        raise InviteError(f"{a.name} reached today's limit ({s.daily_limit}).")
    if db.scalar(select(InviteSend.id).where(InviteSend.user_id == user_id, InviteSend.method.in_(("dm", "personal")))) is not None:
        raise InviteError("Already invited (by one of your accounts).")
    eligible = {c.user_id: c for c in queue(db, a.id)}
    if user_id in eligible:
        c = eligible[user_id]
        async with RT.lock(a.id):
            g = await _open(db, settings, a)
            try:
                await g.send(c.username or c.user_id, text_for(s, "dm"))
            except gw.RateLimited as exc:
                a.halted_until, a.halt_reason = utcnow() + timedelta(seconds=max(exc.seconds, 3600)), str(exc)[:200]
                db.commit()
                raise InviteError(f"Telegram asked {a.name} to slow down: everything is paused.") from exc
            except Exception as exc:  # noqa: BLE001
                opt_out(db, user_id, f"not delivered: {type(exc).__name__}", a.id)
                _record(db, a, "dm", "failed", user_id=user_id, error=str(exc))
                raise InviteError(f"Couldn't deliver ({type(exc).__name__}). They won't be suggested again.") from exc
            finally:
                await g.stop()
        _record(db, a, "dm", "sent", user_id=user_id)
        return {"status": "sent", "mode": "auto"}
    m = db.get(InviteMember, user_id)
    if m is None or not m.username or db.get(InviteConsent, user_id) is not None or user_id in gw.SERVICE_IDS:
        raise InviteError("This person can't be invited (opted out, already handled, or no public username).")
    _record(db, a, "personal", "opened", user_id=user_id)
    return {"status": "opened", "mode": "personal"}


def one_skip(db: Session, account_id: int, user_id: int) -> None:
    a = db.get(InviteAccount, account_id)
    if a is None:
        raise InviteError("Not found.")
    c = db.get(InviteConsent, user_id)
    if c is not None and c.revoked_at is None:
        c.revoked_at, c.revoked_reason = utcnow(), "skipped by owner"
    if db.scalar(select(InviteSend.id).where(InviteSend.user_id == user_id, InviteSend.method == "skip")) is None:
        _record(db, a, "skip", "skipped", user_id=user_id)
    db.commit()


def history(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.execute(select(InviteSend, InviteAccount.name, func.coalesce(InviteMember.username, InviteConsent.username))
                      .join(InviteAccount, InviteAccount.id == InviteSend.account_id)
                      .outerjoin(InviteMember, InviteMember.user_id == InviteSend.user_id).outerjoin(InviteConsent, InviteConsent.user_id == InviteSend.user_id)
                      .where(InviteSend.method.in_(("dm", "personal"))).order_by(InviteSend.id.desc()).limit(limit)).all()
    return [{"recipient": f"@{u}" if u else f"user {x.user_id}", "account": name, "at": x.at.isoformat(),
             "how": "sent by the bot" if x.method == "dm" else "you, in Telegram", "status": x.status,
             "replied_at": x.replied_at.isoformat() if x.replied_at else None} for x, name, u in rows]
