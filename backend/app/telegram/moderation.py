"""The rules of Price Chat's Telegram groups, and how they're enforced.

Rules (the owner's, 2026-11-08):
  1. No advertisements / promotions without admin permission
  2. No sexual / porn, or disturbing images
  3. No political discussion, harassment or abuse
  4. Breaking rules results in a 12 h / 24 h / permanent mute. For disputes contact @jacktheblack
  5. No links / scams, English only

Enforcement:
  · links (rule 5) are caught automatically in the allow-listed groups (Admin → Telegram sources): a message with a link from
    anyone who isn't a group admin is deleted and its author goes one step up the ladder. Price Chat's own site, channel and
    bot are allowed;
  · everything a bot can't judge reliably (images, politics, harassment, ads without links, other languages) is an admin's call:
    an admin replies to the message with /warn [rule] [reason]. The message is deleted and its author goes one step up the ladder;
  · the ladder: the 1st strike mutes for 12 hours, the 2nd for 24 hours, the 3rd and later for good. Every mute says which rule,
    what comes next and who to contact. /unmute lifts a mute (add "reset" to clear the strikes); /strikes shows someone's record;
  · admins are never muted by the bot, and the bot only acts in allow-listed groups. It needs to be an admin there with the
    "Delete messages" and "Ban users" rights; without them it says so instead of failing silently.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.telegram import TgStrike

logger = logging.getLogger(__name__)

DISPUTE = "@jacktheblack"
RULES_TEXT = ("📜 <b>Price Chat group rules</b>\n\n"
              "1. No advertisements or promotions without admin permission\n"
              "2. No sexual, porn or disturbing images\n"
              "3. No political discussion, harassment or abuse\n"
              f"4. Breaking the rules means a 12 hour mute, then 24 hours, then a permanent mute. For disputes, contact {DISPUTE}\n"
              "5. No links or scams. English only")
RULE_NAMES = {1: "advertising without admin permission", 2: "sexual, porn or disturbing images", 3: "politics, harassment or abuse",
              4: "the group rules", 5: "links, scams or a language other than English"}
LADDER: tuple[timedelta | None, ...] = (timedelta(hours=12), timedelta(hours=24), None)  # None: for good
COMMANDS = ("/warn", "/unmute", "/strikes")
NEEDS_RIGHTS = "I need to be a group admin with the \"Delete messages\" and \"Ban users\" rights to enforce the rules."

_LINK = re.compile(r"(?i)(?:https?://|www\.)\S+|\b(?:t\.me|telegram\.me|telegram\.dog)/\S+"
                   r"|(?<![@\w.])\b[a-z0-9-]{2,}(?:\.[a-z0-9-]+)*\.(?:com|io|xyz|net|org|gg|finance|exchange|club|site|online|ly|ru|cc|info|vip)\b(?:/\S*)?")
# Bare domains only on TLDs that are rarely ordinary words or tickers (not .ai/.me/.top/.co/.pro/.app/.link: "Fetch.ai", "lol.me too",
# "the dip.Top"), and never inside an e-mail address. Written links (https://, www., t.me/) are always caught.
_ALLOWED = ("pricechat.live", "t.me/pricechatupdates", "t.me/botpricechat")


def esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def links_in(text: str, bot_username: str | None = None) -> list[str]:
    """The links in a message that aren't Price Chat's own."""
    allowed = _ALLOWED + ((f"t.me/{bot_username.lower()}",) if bot_username else ())
    out = []
    for m in _LINK.finditer(text or ""):
        url = m.group(0).lower().rstrip(".,)!?")
        bare = re.sub(r"^(?:https?://)?(?:www\.)?", "", url)
        if not any(bare == a or bare.startswith(a + "/") or bare.startswith(a + "?") or bare.endswith("." + a) for a in allowed):
            out.append(url)
    return out


def ladder_step(strikes_before: int) -> timedelta | None:
    return LADDER[min(strikes_before, len(LADDER) - 1)]


def strikes(db: Session, chat_id: int, user_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(TgStrike).where(TgStrike.chat_id == chat_id, TgStrike.user_id == user_id,
                                                                         TgStrike.cleared.is_(False))) or 0)


def _length(d: timedelta | None) -> str:
    return "for good" if d is None else f"for {int(d.total_seconds() // 3600)} hours"


_admins: dict[int, tuple[float, set[int]]] = {}
ADMIN_CACHE_SECONDS = 120
NOT_PEOPLE = {136817688, 1087968824, 777000}  # @Channel_Bot (sent as a channel), @GroupAnonymousBot, Telegram's automatic forwards
BURST = timedelta(minutes=10)  # one strike per member per chat in this window: a burst of links (or a link and its edit) is one offence
_no_rights_logged: set[int] = set()


async def is_admin(client: Any, chat_id: int, user_id: int) -> bool:
    """Group admins (cached ten minutes per group). If Telegram can't be asked, nobody counts as an admin for commands, and the
    automatic link check stands down (never punish someone because Telegram didn't answer)."""
    hit = _admins.get(chat_id)
    if hit is None or time.monotonic() - hit[0] > ADMIN_CACHE_SECONDS:
        rows = await client.get_chat_administrators(chat_id)
        hit = (time.monotonic(), {int((r.get("user") or {}).get("id") or 0) for r in rows})
        _admins[chat_id] = hit
    return user_id in hit[1]


async def punish(db: Session, client: Any, chat_id: int, user_id: int, name: str | None, rule: int | None, reason: str, by: str,
                 message_id: int | None) -> str:
    """Deletes the message, mutes its author one step up the ladder, records the strike. Returns the text for the group.
    An automatic second offence inside BURST only deletes the message: no new strike, nothing said (an admin's /warn always counts)."""
    recent = None if by != "auto" else db.scalar(select(TgStrike.id).where(TgStrike.chat_id == chat_id, TgStrike.user_id == user_id, TgStrike.cleared.is_(False),
                                                 TgStrike.created_at >= utcnow() - BURST).limit(1))
    if recent is not None:
        if message_id:
            await delete_quietly(client, chat_id, message_id)
        return ""
    before = strikes(db, chat_id, user_id)
    length = ladder_step(before)
    until = utcnow() + length if length else None
    if message_id:
        try:
            await client.delete_message(chat_id, message_id)
        except Exception as e:  # noqa: BLE001 - deleted already, or no right to delete: the mute still matters
            logger.info("rules: couldn't delete message %s in %s (%s)", message_id, chat_id, type(e).__name__)
    try:
        await client.restrict_member(chat_id, user_id, int(until.timestamp()) if until else 0, can_send=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("rules: couldn't mute %s in %s (%s)", user_id, chat_id, type(e).__name__)
        return NEEDS_RIGHTS
    db.add(TgStrike(chat_id=chat_id, user_id=user_id, user_name=(name or "")[:128] or None, rule=rule, reason=reason[:500], by=by[:32],
                    muted_until=until, permanent=until is None))
    db.commit()
    n = before + 1
    after = ladder_step(n)
    what = f"rule {rule} ({RULE_NAMES.get(rule, 'the group rules')})" if rule else "the group rules"
    nxt = "" if length is None else f" Next time: {'a permanent mute' if after is None else _length(after).replace('for ', '')}."
    return (f"🔇 {esc(name or 'This member')} is muted {_length(length)} for breaking {what} (strike {n}).{nxt}\n"
            f"Rules: /rules · Disputes: {DISPUTE}")


async def delete_quietly(client: Any, chat_id: int, message_id: int) -> None:
    try:
        await client.delete_message(chat_id, message_id)
    except Exception as e:  # noqa: BLE001
        logger.info("rules: couldn't delete message %s in %s (%s)", message_id, chat_id, type(e).__name__)


def _target_and_rest(cmd: str, words: list[str], reply_author_id: int | None) -> tuple[int | None, list[str]]:
    """The member acted on: the author of the replied-to message; for /unmute and /strikes also a Telegram user id (5+ digits, so
    "/warn 1" can never mean user 1)."""
    if reply_author_id:
        return reply_author_id, words
    if cmd != "/warn" and words and words[0].isdigit() and len(words[0]) >= 5:
        return int(words[0]), words[1:]
    return None, words


def _next(n: int) -> str:
    step = ladder_step(n)
    return "a permanent mute" if step is None else _length(step).replace("for ", "")


async def command(db: Session, client: Any, chat_id: int, issuer_id: int, anonymous: bool, cmd: str, words: list[str],
                  reply_author_id: int | None, reply_author_name: str | None, reply_message_id: int | None, message_id: int | None,
                  bot_id: int | None = None) -> str | None:
    """/warn, /unmute, /strikes in a group, from an admin. Returns the reply for the group (None: nothing to say)."""
    try:
        admin = anonymous or await is_admin(client, chat_id, issuer_id)
    except Exception:  # noqa: BLE001
        return "Couldn't check the group's admins with Telegram right now. Try again in a moment."
    if not admin:
        return f"Only group admins can use {cmd}."
    target, rest = _target_and_rest(cmd, words, reply_author_id)
    if target is None:
        return {"/warn": "Reply to the message that broke a rule: /warn 1-5 [reason]",
                "/unmute": "Reply to the member's message, or send /unmute USER_ID [reset]",
                "/strikes": "Reply to the member's message, or send /strikes USER_ID"}[cmd]
    if cmd == "/strikes":
        n = strikes(db, chat_id, target)
        last = db.scalar(select(TgStrike).where(TgStrike.chat_id == chat_id, TgStrike.user_id == target, TgStrike.cleared.is_(False))
                         .order_by(TgStrike.id.desc()).limit(1))
        state = "" if last is None else (" · muted for good" if last.permanent else
                                         (f" · muted until {last.muted_until:%d %b %H:%M} UTC" if last.muted_until and last.muted_until > utcnow() else ""))
        return f"{esc(reply_author_name or target)}: {n} strike(s){state}. Next: {_next(n)}."
    if cmd == "/unmute":
        reset = any(w.lower() == "reset" for w in rest)
        try:
            await client.restrict_member(chat_id, target, 0, can_send=True)
        except Exception:  # noqa: BLE001
            return NEEDS_RIGHTS
        if reset:
            for row in db.scalars(select(TgStrike).where(TgStrike.chat_id == chat_id, TgStrike.user_id == target, TgStrike.cleared.is_(False))):
                row.cleared = True
        db.commit()
        if message_id:
            try:
                await client.delete_message(chat_id, message_id)
            except Exception:  # noqa: BLE001
                pass
        return f"🔊 {esc(reply_author_name or 'The member')} can write again." + (" Their strikes were cleared." if reset else "")
    # /warn
    if bot_id and target == bot_id:
        return None
    if target in NOT_PEOPLE:
        if reply_message_id:
            await delete_quietly(client, chat_id, reply_message_id)
        return "That was sent as a channel or as the group, so there's no member to mute. I deleted it; to stop a channel, ban it in the group settings."
    try:
        if await is_admin(client, chat_id, target):
            return "Admins aren't muted by the bot."
    except Exception:  # noqa: BLE001
        return "Couldn't check the group's admins with Telegram right now. Try again in a moment."
    rule = int(rest[0]) if rest and rest[0].isdigit() and 1 <= int(rest[0]) <= 5 else None
    reason = " ".join(rest[1:] if rule else rest)[:300]
    text = await punish(db, client, chat_id, target, reply_author_name, rule, reason, "admin:anonymous" if anonymous else f"admin:{issuer_id}",
                        reply_message_id)
    if message_id and text != NEEDS_RIGHTS:
        try:
            await client.delete_message(chat_id, message_id)  # the /warn itself
        except Exception:  # noqa: BLE001
            pass
    return text


async def check_links(db: Session, client: Any, chat_id: int, user_id: int, name: str | None, message_id: int, text: str,
                      bot_username: str | None) -> str | None:
    """Rule 5, automatically: a link from a non-admin is deleted and its author goes one step up the ladder."""
    found = links_in(text, bot_username)
    if not found:
        return None
    try:
        if await is_admin(client, chat_id, user_id):
            return None
    except Exception:  # noqa: BLE001 - Telegram didn't answer: never punish on a guess
        return None
    text = await punish(db, client, chat_id, user_id, name, 5, f"link: {found[0][:120]}", "auto", message_id)
    if text == NEEDS_RIGHTS:  # the automatic check stays quiet without rights (no reply to every link); the log says it once per group
        if chat_id not in _no_rights_logged:
            _no_rights_logged.add(chat_id)
            logger.warning("rules: no rights to enforce the link rule in %s; give the bot Delete messages + Ban users", chat_id)
        return None
    return text or None
