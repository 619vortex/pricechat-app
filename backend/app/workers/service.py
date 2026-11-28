"""Kiwi Passport Worker System, Phase 1: Telegram invite workers.

Roles: WORKER and MANAGER are granted/revoked by the owner (admin) only, are independent of each other and of the XP rank,
and every change is a history row. All checks happen here, server-side.

How an invite becomes a verified invite (never just because a username was typed):
  1. Attribution, strongest first:
       chat_link   the person joined the community through the worker's own Telegram invite link (Telegram names the link in
                   the chat_member update; needs the bot to be a group admin with "invite users")
       bot_start   the person pressed Start on the worker's bot link (t.me/<bot>?start=wk_<code>): we get their numeric id
       form        the worker typed a @username: it's matched to a numeric id through a join the bot saw (new_chat_members /
                   chat_member) or a Passport-linked Telegram account
  2. Verification (the verify job, every few minutes): the numeric id is in the community now (getChatMember), the bot saw
     them JOIN no earlier than `join_window_hours` before the submission (or after the link was used), they never posted in
     the community before that join, they aren't a bot or the worker themself, and nobody else was credited for them
     (wk_credits.tg_user_id is unique: one worker per Telegram account, ever).
  3. A member who is in the group but whose join the bot never saw stays SUBMITTED with that evidence: a manager decides.
Daily reports: verified invites count on the day they were verified (program timezone). At the target the worker can submit
the report; a manager (or the owner) approves or rejects it with a reason; decisions are final and every action is a
wk_reviews row (append-only).
Engagement: qualifying community messages since joining, by numeric id (commands, bots, empty and repeated messages and
floods are not counted). Only the authorised community group is read, never private chats; only counts are kept here.
"""

import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.singleton import get_or_create
from app.db.base import utcnow
from app.models.members import LinkedIdentity, Member
from app.models.telegram import TelegramMessage, TelegramSource
from app.models.workers import (JOB_TELEGRAM_INVITES, JOB_TYPES, ROLES, WkAssignment, WkAudit, WkCredit, WkJoin, WkLink, WkReport, WkReview, WkRole,
                                WkSettings, WkSubmission)

START_PREFIX = "wk_"
USERNAME = re.compile(r"^[a-z][a-z0-9_]{3,31}$")
MEMBER_STATUSES = {"member", "administrator", "creator"}
COUNTED = ("VERIFIED", "PENDING_REVIEW", "APPROVED")
SUBMIT_LIMIT_PER_DAY = 60
PAGE = 20


class WorkerError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime | None) -> str | None:
    d = _aware(d)
    return d.isoformat() if d else None


# ---------------------------------------------------------------- settings, days
def settings(db: Session) -> WkSettings:
    return get_or_create(db, WkSettings, lambda: WkSettings(id=1))


def tz(db: Session) -> ZoneInfo:
    try:
        return ZoneInfo(settings(db).timezone)
    except Exception:  # noqa: BLE001 - a bad setting falls back to UTC rather than breaking reports
        return ZoneInfo("UTC")


def today(db: Session, now: datetime | None = None) -> str:
    return (_aware(now) or utcnow()).astimezone(tz(db)).date().isoformat()


def day_range(db: Session, day: str) -> tuple[datetime, datetime]:
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, tzinfo=tz(db))
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def community_chat_id(db: Session) -> int | None:
    s = settings(db)
    if s.community_chat_id:
        return s.community_chat_id
    name = s.community_chat.lstrip("@").lower()
    src = db.scalar(select(TelegramSource).where(func.lower(TelegramSource.username) == name))
    if src is not None:
        s.community_chat_id = src.chat_id
        db.flush()
        return src.chat_id
    return None


# ---------------------------------------------------------------- roles and assignments (owner only: routes enforce it)
def roles_of(db: Session, member_id: int) -> set[str]:
    return set(db.scalars(select(WkRole.role).where(WkRole.member_id == member_id, WkRole.revoked_at.is_(None))))


def require(db: Session, member: Member, role: str) -> None:
    if role not in roles_of(db, member.id):
        raise WorkerError(f"This needs the {role.title()} role.", 403)


def audit(db: Session, actor: str, action: str, member_id: int | None, **details: Any) -> None:
    db.add(WkAudit(actor=actor[:96], action=action[:32], member_id=member_id, details=details))


def grant(db: Session, member_id: int, role: str, actor: str, note: str | None = None) -> WkRole:
    if role not in ROLES:
        raise WorkerError("Unknown role.")
    m = db.get(Member, member_id)
    if m is None:
        raise WorkerError("No such member.", 404)
    cur = db.scalar(select(WkRole).where(WkRole.member_id == member_id, WkRole.role == role, WkRole.revoked_at.is_(None)))
    if cur is not None:
        return cur
    row = WkRole(member_id=member_id, role=role, granted_by=actor[:64], note=note)
    db.add(row)
    audit(db, actor, "role_granted", member_id, role=role, note=note)
    if role == "WORKER":
        link_for(db, m)
    db.commit()
    return row


def revoke(db: Session, member_id: int, role: str, actor: str) -> int:
    rows = db.scalars(select(WkRole).where(WkRole.member_id == member_id, WkRole.role == role, WkRole.revoked_at.is_(None))).all()
    for r in rows:
        r.revoked_at, r.revoked_by = utcnow(), actor[:64]
    if rows:
        audit(db, actor, "role_revoked", member_id, role=role)
    db.commit()
    return len(rows)


def active_assignment(db: Session, member_id: int, job_type: str = JOB_TELEGRAM_INVITES) -> WkAssignment | None:
    return db.scalar(select(WkAssignment).where(WkAssignment.member_id == member_id, WkAssignment.job_type == job_type,
                                                WkAssignment.ended_at.is_(None)).order_by(WkAssignment.id.desc()).limit(1))


def assign(db: Session, member_id: int, job_type: str, daily_target: int | None, manager_id: int | None, actor: str) -> WkAssignment:
    if job_type not in JOB_TYPES:
        raise WorkerError("Unknown job type.")
    if "WORKER" not in roles_of(db, member_id):
        raise WorkerError("Give the member the Worker role first.")
    if manager_id is not None and "MANAGER" not in roles_of(db, manager_id):
        raise WorkerError("That member isn't a Manager.")
    if manager_id == member_id:
        raise WorkerError("A worker can't be their own manager.")
    target = daily_target or settings(db).default_target
    if not 1 <= target <= 500:
        raise WorkerError("The daily target must be between 1 and 500.")
    old = active_assignment(db, member_id, job_type)
    if old is not None:
        old.ended_at, old.ended_by = utcnow(), actor[:64]
    row = WkAssignment(member_id=member_id, job_type=job_type, daily_target=target, manager_id=manager_id, created_by=actor[:64])
    db.add(row)
    audit(db, actor, "job_assigned", member_id, job_type=job_type, daily_target=target, manager_id=manager_id,
          replaced=old.id if old else None)
    db.commit()
    return row


def end_assignment(db: Session, assignment_id: int, actor: str) -> None:
    a = db.get(WkAssignment, assignment_id)
    if a is None or a.ended_at is not None:
        raise WorkerError("No such active assignment.", 404)
    a.ended_at, a.ended_by = utcnow(), actor[:64]
    audit(db, actor, "job_ended", a.member_id, assignment_id=a.id)
    db.commit()


# ---------------------------------------------------------------- links
def link_for(db: Session, member: Member) -> WkLink:
    row = db.scalar(select(WkLink).where(WkLink.member_id == member.id))
    if row is None:
        row = WkLink(member_id=member.id, code=secrets.token_hex(5))
        db.add(row)
        db.flush()
    return row


async def ensure_chat_link(db: Session, member: Member, client: Any) -> WkLink:
    """The worker's own Telegram invite link to the community (needs the bot to be an admin with invite rights)."""
    row = link_for(db, member)
    if row.chat_link or client is None:
        return row
    name = f"wk-{row.code}"
    try:
        row.chat_link = await client.create_invite_link(settings(db).community_chat, name)
        row.chat_link_name, row.chat_link_error = name, None
    except Exception as e:  # noqa: BLE001 - e.g. the bot isn't an admin: the bot deep link still works
        row.chat_link_error = f"{type(e).__name__}: {str(e)[:200]}"
    db.commit()
    return row


# ---------------------------------------------------------------- submissions
def normalise(raw: str) -> str | None:
    """'@Name', 'name', 't.me/Name', 'https://t.me/Name' → 'name'; None when it can't be a Telegram username."""
    s = (raw or "").strip()
    s = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/", "", s, flags=re.I).lstrip("@").strip().strip("/").lower()
    return s if USERNAME.match(s) else None


def _own_tg_ids(db: Session, member_id: int) -> set[int]:
    return {int(x) for x in db.scalars(select(LinkedIdentity.platform_user_id).where(LinkedIdentity.member_id == member_id,
                                                                                     LinkedIdentity.platform == "telegram"))
            if str(x).lstrip("-").isdigit()}


def submit(db: Session, worker: Member, raw: str) -> WkSubmission:
    require(db, worker, "WORKER")
    a = active_assignment(db, worker.id)
    if a is None:
        raise WorkerError("You don't have a Telegram invites job assigned yet.", 403)
    name = normalise(raw)
    if name is None:
        raise WorkerError("That isn't a Telegram username (5–32 letters, digits or _, starting with a letter).")
    start, end = day_range(db, today(db))
    n = db.scalar(select(func.count()).select_from(WkSubmission).where(WkSubmission.worker_id == worker.id, WkSubmission.created_at >= start)) or 0
    if n >= SUBMIT_LIMIT_PER_DAY:
        raise WorkerError("Daily submission limit reached.", 429)
    dup = db.scalar(select(WkSubmission).where(WkSubmission.username == name))
    if dup is not None:
        raise WorkerError("You already submitted @" + name if dup.worker_id == worker.id else f"@{name} was already submitted by another worker.", 409)
    own = {i.username.lower().lstrip("@") for i in db.scalars(select(LinkedIdentity).where(LinkedIdentity.member_id == worker.id,
                                                                                            LinkedIdentity.platform == "telegram")) if i.username}
    if name in own:
        raise WorkerError("That's your own Telegram account.")
    row = WkSubmission(worker_id=worker.id, assignment_id=a.id, job_type=JOB_TELEGRAM_INVITES, username=name, source="form",
                       evidence={"submitted": utcnow().isoformat()})
    db.add(row)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise WorkerError(f"@{name} was already submitted.", 409) from e
    _resolve(db, row)
    db.commit()
    return row


def _attributed(db: Session, worker_id: int, tg_user_id: int, username: str | None, source: str, evidence: dict[str, Any]) -> WkSubmission | None:
    """A link join / bot start: one submission per Telegram account (the first attribution stands)."""
    if db.scalar(select(WkSubmission.id).where(WkSubmission.tg_user_id == tg_user_id)) is not None:
        return None
    if username and db.scalar(select(WkSubmission.id).where(WkSubmission.username == username)) is not None:
        username = None  # someone typed the name first; the numeric id still decides the credit
    a = active_assignment(db, worker_id)
    if a is None or "WORKER" not in roles_of(db, worker_id):
        return None
    row = WkSubmission(worker_id=worker_id, assignment_id=a.id, job_type=JOB_TELEGRAM_INVITES, username=username, tg_user_id=tg_user_id,
                       source=source, evidence=evidence)
    try:
        with db.begin_nested():  # a form submission of the same @username can land between the check above and here
            db.add(row)
    except IntegrityError:
        if not username or db.scalar(select(WkSubmission.id).where(WkSubmission.tg_user_id == tg_user_id)) is not None:
            return None
        row = WkSubmission(worker_id=worker_id, assignment_id=a.id, job_type=JOB_TELEGRAM_INVITES, username=None,
                           tg_user_id=tg_user_id, source=source, evidence=evidence)
        db.add(row)
        db.flush()
    return row


def on_start(db: Session, code: str, tg_user_id: int, username: str | None, is_bot: bool) -> str:
    """'/start wk_<code>' privately with the bot: remember who this Telegram account is and answer with the community link."""
    s = settings(db)
    join = f"https://t.me/{s.community_chat.lstrip('@')}"
    link = db.scalar(select(WkLink).where(WkLink.code == code[:24]))
    if link is not None and not is_bot and tg_user_id not in _own_tg_ids(db, link.member_id):
        _attributed(db, link.member_id, tg_user_id, (username or "").lower() or None, "bot_start", {"bot_start": utcnow().isoformat()})
        db.commit()
    return f"👋 Welcome to Price Chat!\n\nJoin the community here:\n{join}"


def _is_community(db: Session, chat_id: int, chat_username: str | None) -> bool:
    """Only the configured community group counts. Its numeric id is learned from its @username the first time it's seen."""
    cid = community_chat_id(db)
    if cid is not None:
        return chat_id == cid
    s = settings(db)
    if chat_username and chat_username.lower() == s.community_chat.lstrip("@").lower():
        s.community_chat_id = chat_id
        db.flush()
        return True
    return False


def record_join(db: Session, *, key: str, chat_id: int, user: dict[str, Any], at: datetime, link_name: str | None,
                chat_username: str | None = None) -> WkJoin | None:
    """A join the bot saw in the community group (idempotent on `key`). A join through a worker's link is attributed."""
    if not _is_community(db, chat_id, chat_username):
        return None
    uid = user.get("id")
    if not isinstance(uid, int) or db.scalar(select(WkJoin.id).where(WkJoin.event_key == key[:80])):
        return None
    j = WkJoin(event_key=key[:80], chat_id=chat_id, tg_user_id=uid, username=(user.get("username") or "").lower() or None,
               first_name=(user.get("first_name") or "")[:128] or None, is_bot=bool(user.get("is_bot")), joined_at=at, invite_link_name=link_name)
    db.add(j)
    db.flush()
    if link_name and not j.is_bot:
        link = db.scalar(select(WkLink).where(WkLink.chat_link_name == link_name))
        if link is not None and uid not in _own_tg_ids(db, link.member_id):
            _attributed(db, link.member_id, uid, j.username, "chat_link", {"joined_via_link": link_name, "joined_at": at.isoformat()})
    return j


def joins_from_update(db: Session, update: dict[str, Any]) -> int:
    """new_chat_members messages and chat_member updates for the community group → wk_joins. Returns how many were new."""
    n = 0
    msg = update.get("message") or {}
    if msg.get("new_chat_members"):
        chat = msg.get("chat") or {}
        at = datetime.fromtimestamp(int(msg.get("date") or 0), tz=timezone.utc)
        for u in msg["new_chat_members"]:
            if record_join(db, key=f"msg:{chat.get('id')}:{msg.get('message_id')}:{u.get('id')}", chat_id=int(chat.get("id") or 0), user=u, at=at, link_name=None,
                           chat_username=chat.get("username")):
                n += 1
    cm = update.get("chat_member")
    if cm:
        new, old = (cm.get("new_chat_member") or {}), (cm.get("old_chat_member") or {})
        if new.get("status") in MEMBER_STATUSES and old.get("status") in ("left", "kicked", None):
            link = (cm.get("invite_link") or {}).get("name")
            at = datetime.fromtimestamp(int(cm.get("date") or 0), tz=timezone.utc)
            ch = cm.get("chat") or {}
            if record_join(db, key=f"cm:{update.get('update_id')}", chat_id=int(ch.get("id") or 0), user=new.get("user") or {},
                           at=at, link_name=link, chat_username=ch.get("username")):
                n += 1
    return n


def _resolve(db: Session, row: WkSubmission) -> None:
    """Find the numeric id behind a typed username: a join the bot saw, or a Passport-linked Telegram account."""
    if row.tg_user_id or not row.username:
        return
    j = db.scalar(select(WkJoin).where(WkJoin.username == row.username).order_by(WkJoin.joined_at.desc()).limit(1))
    if j is not None:
        row.tg_user_id = j.tg_user_id
        row.evidence = {**row.evidence, "resolved_by": "join_seen", "join_seen_at": _iso(j.joined_at)}
        return
    li = db.scalar(select(LinkedIdentity).where(LinkedIdentity.platform == "telegram", func.lower(LinkedIdentity.username) == row.username))
    if li is not None and str(li.platform_user_id).isdigit():
        row.tg_user_id = int(li.platform_user_id)
        row.evidence = {**row.evidence, "resolved_by": "passport_linked_telegram"}


def _in(info: dict[str, Any]) -> bool:
    st = info.get("status")
    return st in MEMBER_STATUSES or (st == "restricted" and bool(info.get("is_member")))


async def verify(db: Session, bot: Any, limit: int = 200) -> dict[str, int]:
    """Resolve, verify (or reject) SUBMITTED invites and refresh engagement. Safe to run any time (idempotent)."""
    out = {"checked": 0, "verified": 0, "rejected": 0, "waiting": 0, "engagement": 0, "errors": 0}
    s = settings(db)
    cid = community_chat_id(db)
    now = utcnow()
    # Least recently checked first: rows can stay SUBMITTED for good (in the group but no join seen), and in id order
    # `limit` of those would starve every newer submission.
    for row in db.scalars(select(WkSubmission).where(WkSubmission.status == "SUBMITTED")
                          .order_by(WkSubmission.updated_at, WkSubmission.id).limit(limit)).all():
        out["checked"] += 1
        row.updated_at = now
        _resolve(db, row)
        ev = dict(row.evidence or {})
        if row.tg_user_id is None:
            ev["note"] = "username not seen joining yet (the bot needs to see them join, or them press Start on your link)"
            row.evidence = ev
            if now - _aware(row.created_at) > timedelta(days=7):  # type: ignore[operator]
                row.status, row.reject_reason = "REJECTED", "never seen joining the community within 7 days"
                out["rejected"] += 1
            else:
                out["waiting"] += 1
            continue
        reason = None
        if row.tg_user_id in _own_tg_ids(db, row.worker_id):
            reason = "that's the worker's own Telegram account"
        joins = db.scalars(select(WkJoin).where(WkJoin.tg_user_id == row.tg_user_id).order_by(WkJoin.joined_at.desc())).all()
        if not reason and any(j.is_bot for j in joins):
            reason = "a bot"
        earliest = _aware(_aware(row.created_at) - timedelta(hours=s.join_window_hours))  # type: ignore[operator]
        join = next((j for j in joins if _aware(j.joined_at) >= earliest), None)  # type: ignore[operator]
        if row.source == "chat_link":
            join = next((j for j in joins if j.invite_link_name), join)
        first_msg = db.scalar(select(func.min(TelegramMessage.sent_at)).where(TelegramMessage.author_id == row.tg_user_id,
                                                                            *( [TelegramMessage.chat_id == cid] if cid else [])))
        if not reason and first_msg is not None and (join is None or _aware(first_msg) < _aware(join.joined_at)):  # type: ignore[operator]
            reason = "already active in the community before this invite"
        inside = None
        if not reason and bot is not None:
            try:
                inside = _in(await bot.get_chat_member(s.community_chat, row.tg_user_id))
            except Exception:  # noqa: BLE001 - can't see the group right now: try again next run
                out["errors"] += 1
                continue
        ev.update({"tg_user_id": row.tg_user_id, "join_seen_at": _iso(join.joined_at) if join else None,
                   "joined_via_link": join.invite_link_name if join else None, "in_group_now": inside, "checked_at": now.isoformat()})
        row.evidence = ev
        if reason:
            row.status, row.reject_reason = "REJECTED", reason
            out["rejected"] += 1
            continue
        if inside is not True or join is None:
            unseen = bool(inside) and join is None  # can't become verifiable later: only a join the bot sees counts
            ev["note"] = ("in the group, but the bot didn't see them join: can't be verified (closes after 7 days)" if unseen
                          else "not in the community yet")
            row.evidence = ev
            if (inside is False or unseen) and now - _aware(row.created_at) > timedelta(days=7):  # type: ignore[operator]
                row.status, row.reject_reason = "REJECTED", ("in the group, but the bot never saw them join within 7 days, so the "
                                                             "invite can't be verified" if unseen else "didn't join within 7 days")
                out["rejected"] += 1
            else:
                out["waiting"] += 1
            continue
        try:
            with db.begin_nested():
                db.add(WkCredit(tg_user_id=row.tg_user_id, submission_id=row.id, worker_id=row.worker_id))
        except IntegrityError:
            row.status, row.reject_reason = "REJECTED", "this Telegram account was already credited to a worker"
            out["rejected"] += 1
            continue
        row.status, row.verified_at, row.joined_at, row.still_member = "VERIFIED", now, join.joined_at, True
        out["verified"] += 1
    db.commit()
    out["engagement"] = refresh_engagement(db)
    if bot is not None:  # still in the group? (verified in the last 14 days, a few per run, least recently checked first)
        for row in db.scalars(select(WkSubmission).where(WkSubmission.status.in_(COUNTED), WkSubmission.verified_at >= now - timedelta(days=14))
                              .order_by(WkSubmission.member_checked_at.asc().nulls_first(), WkSubmission.id).limit(40)).all():
            row.member_checked_at = now
            try:
                row.still_member = _in(await bot.get_chat_member(s.community_chat, row.tg_user_id))
            except Exception:  # noqa: BLE001
                out["errors"] += 1
        db.commit()
    return out


def qualifying(messages: list[tuple[datetime, str, str | None]]) -> int:
    """Count real messages: no commands, no empty or 1-character texts (media counts), no repeats of the previous text,
    at most 3 in any one minute (floods)."""
    n, last, per_min = 0, None, {}
    for at, text, media in sorted(messages, key=lambda m: m[0]):
        t = (text or "").strip()
        if t.startswith("/") or (len(t) < 2 and not media):
            continue
        if t and t.lower() == last:
            continue
        k = _aware(at).replace(second=0, microsecond=0)  # type: ignore[union-attr]
        if per_min.get(k, 0) >= 3:
            continue
        per_min[k] = per_min.get(k, 0) + 1
        last = t.lower() or last
        n += 1
    return n


def refresh_engagement(db: Session) -> int:
    cid = community_chat_id(db)
    rows = db.scalars(select(WkSubmission).where(WkSubmission.status.in_(COUNTED), WkSubmission.tg_user_id.is_not(None))).all()
    if not cid:
        return len(rows)  # the community group isn't known yet: count nothing rather than other chats
    now = utcnow()
    for start in range(0, len(rows), 500):  # one query per 500 invited people, not one per person
        chunk = rows[start : start + 500]
        q = select(TelegramMessage.author_id, TelegramMessage.sent_at, TelegramMessage.text, TelegramMessage.media_type).where(
            TelegramMessage.author_id.in_({r.tg_user_id for r in chunk}), TelegramMessage.author_is_bot.is_(False),
            TelegramMessage.sent_at >= min(_aware(r.joined_at or r.verified_at) for r in chunk),  # type: ignore[type-var]
            TelegramMessage.chat_id == cid)
        by_author: dict[int, list[tuple[datetime, str, str | None]]] = {}
        for author, at, text, media in db.execute(q):
            by_author.setdefault(author, []).append((_aware(at), text, media))  # type: ignore[arg-type]
        for row in chunk:
            since = _aware(row.joined_at or row.verified_at)
            row.engagement = qualifying([m for m in by_author.get(row.tg_user_id, []) if m[0] >= since])  # type: ignore[operator, arg-type]
            row.engagement_at = now
    db.commit()
    return len(rows)


# ---------------------------------------------------------------- daily reports
def _day_verified(db: Session, worker_id: int, day: str) -> list[WkSubmission]:
    start, end = day_range(db, day)
    return list(db.scalars(select(WkSubmission).where(WkSubmission.worker_id == worker_id, WkSubmission.status.in_(COUNTED),
                                                      WkSubmission.verified_at >= start, WkSubmission.verified_at < end).order_by(WkSubmission.verified_at)))


def day_status(db: Session, worker_id: int, day: str) -> dict[str, Any]:
    a = active_assignment(db, worker_id)
    target = a.daily_target if a else settings(db).default_target
    rep = db.scalar(select(WkReport).where(WkReport.worker_id == worker_id, WkReport.job_type == JOB_TELEGRAM_INVITES, WkReport.day == day))
    n = len(_day_verified(db, worker_id, day))
    if rep is not None:
        st = {"PENDING": "PENDING_APPROVAL", "APPROVED": "APPROVED", "REJECTED": "REJECTED"}[rep.status]
    else:
        st = "READY_TO_SUBMIT" if n >= target else "IN_PROGRESS"
    return {"day": day, "target": target, "verified": n, "status": st, "report_id": rep.id if rep else None,
            "reason": rep.reason if rep else None}


def submit_report(db: Session, worker: Member) -> WkReport:
    require(db, worker, "WORKER")
    a = active_assignment(db, worker.id)
    if a is None:
        raise WorkerError("No job assigned.", 403)
    day = today(db)
    if db.scalar(select(WkReport.id).where(WkReport.worker_id == worker.id, WkReport.job_type == a.job_type, WkReport.day == day)):
        raise WorkerError("Today's report was already submitted.", 409)
    subs = [x for x in _day_verified(db, worker.id, day) if x.report_id is None]
    if len(subs) < a.daily_target:
        raise WorkerError(f"{len(subs)} of {a.daily_target} verified invites today: not ready yet.")
    rep = WkReport(worker_id=worker.id, assignment_id=a.id, job_type=a.job_type, day=day, target=a.daily_target, verified_count=len(subs),
                   submission_ids=[x.id for x in subs])
    db.add(rep)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise WorkerError("Today's report was already submitted.", 409) from e
    for x in subs:
        x.status, x.report_id = "PENDING_REVIEW", rep.id
    db.add(WkReview(report_id=rep.id, actor_kind="worker", actor_member_id=worker.id, actor_name=worker.username, action="SUBMITTED"))
    db.commit()
    return rep


def can_review(db: Session, reviewer: Member, rep: WkReport) -> bool:
    if "MANAGER" not in roles_of(db, reviewer.id) or rep.worker_id == reviewer.id:
        return False
    a = db.get(WkAssignment, rep.assignment_id) if rep.assignment_id else None
    return a is not None and a.manager_id == reviewer.id


def decide(db: Session, rep_id: int, approve: bool, reason: str | None, *, actor_kind: str, actor_member: Member | None, actor_name: str) -> WkReport:
    """Approve or reject a PENDING report. Final: a decided report can't be decided again (the trail stays as it is)."""
    rep = db.scalar(select(WkReport).where(WkReport.id == rep_id).with_for_update())
    if rep is None:
        raise WorkerError("No such report.", 404)
    if actor_kind == "manager" and (actor_member is None or not can_review(db, actor_member, rep)):
        raise WorkerError("You can only review reports of workers assigned to you.", 403)
    if rep.status != "PENDING":
        raise WorkerError(f"This report was already {rep.status.lower()}.", 409)
    reason = (reason or "").strip()[:1000] or None
    if not approve and not reason:
        raise WorkerError("Give a reason for the rejection.")
    rep.status = "APPROVED" if approve else "REJECTED"
    rep.decided_at, rep.decided_by, rep.reason = utcnow(), f"{actor_kind}:{actor_name}"[:96], reason
    for x in db.scalars(select(WkSubmission).where(WkSubmission.report_id == rep.id)):
        x.status = "APPROVED" if approve else "REJECTED"
        if not approve:
            x.reject_reason = f"report rejected: {reason}"
    db.add(WkReview(report_id=rep.id, actor_kind=actor_kind, actor_member_id=actor_member.id if actor_member else None, actor_name=actor_name[:96],
                    action=rep.status, reason=reason))
    db.commit()
    return rep


# ---------------------------------------------------------------- views
def sub_out(x: WkSubmission, evidence: bool = False) -> dict[str, Any]:
    out = {"id": x.id, "username": x.username, "tg_user_id": x.tg_user_id, "source": x.source, "status": x.status, "submitted_at": _iso(x.created_at),
           "verified_at": _iso(x.verified_at), "joined_at": _iso(x.joined_at), "engagement": x.engagement, "engagement_at": _iso(x.engagement_at),
           "still_member": x.still_member, "reject_reason": x.reject_reason, "report_id": x.report_id, "note": (x.evidence or {}).get("note")}
    if evidence:
        out["evidence"] = x.evidence
    return out


def report_out(db: Session, r: WkReport, detail: bool = False) -> dict[str, Any]:
    w = db.get(Member, r.worker_id)
    out = {"id": r.id, "worker": {"id": r.worker_id, "username": w.username if w else "?"}, "day": r.day, "job_type": r.job_type, "target": r.target,
           "verified_count": r.verified_count, "status": r.status, "submitted_at": _iso(r.created_at), "decided_at": _iso(r.decided_at),
           "decided_by": r.decided_by, "reason": r.reason}
    if detail:
        subs = db.scalars(select(WkSubmission).where(WkSubmission.id.in_(r.submission_ids or [0]))).all()
        out["submissions"] = [sub_out(x, evidence=True) for x in subs]
        out["engagement_total"] = sum(x.engagement for x in subs)
        out["reviews"] = [{"at": _iso(v.created_at), "actor_kind": v.actor_kind, "actor": v.actor_name, "action": v.action, "reason": v.reason}
                          for v in db.scalars(select(WkReview).where(WkReview.report_id == r.id).order_by(WkReview.id))]
    return out


def worker_view(db: Session, worker: Member, page: int = 1) -> dict[str, Any]:
    roles = roles_of(db, worker.id)
    out: dict[str, Any] = {"roles": sorted(roles)}
    if "WORKER" not in roles:
        return out
    a = active_assignment(db, worker.id)
    link = link_for(db, worker)
    db.commit()
    base = select(WkSubmission).where(WkSubmission.worker_id == worker.id)
    count = lambda *w: int(db.scalar(select(func.count()).select_from(WkSubmission).where(WkSubmission.worker_id == worker.id, *w)) or 0)  # noqa: E731
    total = count()
    subs = db.scalars(base.order_by(WkSubmission.id.desc()).offset((max(1, page) - 1) * PAGE).limit(PAGE)).all()
    eng = int(db.scalar(select(func.coalesce(func.sum(WkSubmission.engagement), 0)).where(WkSubmission.worker_id == worker.id,
                                                                                          WkSubmission.status.in_(COUNTED))) or 0)
    from app.telegram.ingestion import get_bot_state

    bot = get_bot_state(db).bot_username or "cryptopricechatbot"
    out.update({
        "assignment": {"id": a.id, "job_type": a.job_type, "daily_target": a.daily_target, "since": _iso(a.created_at)} if a else None,
        "today": day_status(db, worker.id, today(db)), "timezone": settings(db).timezone,
        "links": {"bot": f"https://t.me/{bot}?start={START_PREFIX}{link.code}", "chat": link.chat_link,
                  "chat_unavailable": None if link.chat_link else (link.chat_link_error or "not created yet")},
        "totals": {"submissions": total, "verified": count(WkSubmission.status.in_(COUNTED)), "approved": count(WkSubmission.status == "APPROVED"),
                   "pending": count(WkSubmission.status.in_(("SUBMITTED", "PENDING_REVIEW"))), "rejected": count(WkSubmission.status == "REJECTED"),
                   "engagement": eng},
        "submissions": [sub_out(x) for x in subs], "page": page, "pages": max(1, -(-total // PAGE)),
    })
    return out


def reports_of(db: Session, worker_id: int, page: int = 1) -> dict[str, Any]:
    q = select(WkReport).where(WkReport.worker_id == worker_id)
    total = int(db.scalar(select(func.count()).select_from(q.subquery())) or 0)
    rows = db.scalars(q.order_by(WkReport.day.desc()).offset((max(1, page) - 1) * PAGE).limit(PAGE)).all()
    return {"items": [report_out(db, r) for r in rows], "page": page, "pages": max(1, -(-total // PAGE))}


def manager_view(db: Session, manager: Member | None, *, admin: bool = False, status: str | None = None, page: int = 1) -> dict[str, Any]:
    """A manager's workers (or, for the owner, everyone), their productivity, and reports to review."""
    aq = select(WkAssignment).where(WkAssignment.ended_at.is_(None))
    if not admin:
        aq = aq.where(WkAssignment.manager_id == manager.id)  # type: ignore[union-attr]
    assigns = db.scalars(aq).all()
    wids = [a.member_id for a in assigns]
    since = utcnow() - timedelta(days=14)
    workers = []
    for a in assigns:
        m = db.get(Member, a.member_id)
        days: dict[str, int] = {}
        for (v,) in db.execute(select(WkSubmission.verified_at).where(WkSubmission.worker_id == a.member_id, WkSubmission.status.in_(COUNTED),
                                                                      WkSubmission.verified_at >= since)):
            d = _aware(v).astimezone(tz(db)).date().isoformat()  # type: ignore[union-attr]
            days[d] = days.get(d, 0) + 1
        workers.append({"id": a.member_id, "username": m.username if m else "?", "active_worker": "WORKER" in roles_of(db, a.member_id),
                        "assignment": {"id": a.id, "job_type": a.job_type, "daily_target": a.daily_target, "manager_id": a.manager_id},
                        "today": day_status(db, a.member_id, today(db)), "last14": days})
    rq = select(WkReport).where(WkReport.worker_id.in_(wids or [0]))
    if status:
        rq = rq.where(WkReport.status == status)
    total = int(db.scalar(select(func.count()).select_from(rq.subquery())) or 0)
    reps = db.scalars(rq.order_by(WkReport.id.desc()).offset((max(1, page) - 1) * PAGE).limit(PAGE)).all()
    return {"workers": workers, "reports": [report_out(db, r) for r in reps], "page": page, "pages": max(1, -(-total // PAGE))}


def public_roles(db: Session, member_id: int) -> list[str]:
    return sorted(roles_of(db, member_id))


def search_members(db: Session, q: str) -> list[dict[str, Any]]:
    q = q.strip().lstrip("@").lower()
    if not q:
        return []
    ids = set(db.scalars(select(Member.id).where(or_(func.lower(Member.username).contains(q), func.lower(Member.display_name).contains(q))).limit(20)))
    ids |= set(db.scalars(select(LinkedIdentity.member_id).where(func.lower(LinkedIdentity.username).contains(q)).limit(20)))
    if q.isdigit():
        ids.add(int(q))
    out = []
    for m in db.scalars(select(Member).where(Member.id.in_(ids)).limit(20)):
        out.append({"id": m.id, "username": m.username, "display_name": m.display_name, "rank_level": m.rank_level, "roles": sorted(roles_of(db, m.id))})
    return out
