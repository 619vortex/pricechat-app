"""Telegram → raw messages. Allow-list enforced here; nothing else reaches the database."""

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.connectors.state import get_connector_state
from app.models.enums import Platform
from app.models.telegram import TelegramBotState, TelegramMessage, TelegramSource
from app.telegram.client import TelegramBotClient
from app.telegram.errors import MalformedUpdateError, TelegramError
from app.telegram.parser import MembershipChange, ParsedMessage, parse_update
from app.telegram.state import TelegramRuntime

logger = logging.getLogger(__name__)

MAX_BATCHES_PER_SYNC = 10  # 10 × 100 updates; the next run continues from the saved offset


@dataclass(slots=True)
class SyncReport:
    updates: int = 0
    stored: int = 0
    edited: int = 0
    duplicates: int = 0
    ignored_unauthorised: int = 0
    ignored_disabled: int = 0
    ignored_other: int = 0
    malformed: int = 0
    sources: set[int] = field(default_factory=set)

    def as_dict(self) -> dict[str, int]:
        return {
            "updates": self.updates,
            "stored": self.stored,
            "edited": self.edited,
            "duplicates": self.duplicates,
            "ignored_unauthorised": self.ignored_unauthorised,
            "ignored_disabled": self.ignored_disabled,
            "ignored_other": self.ignored_other,
            "malformed": self.malformed,
        }


def looks_like_unsigned_group_id(chat_id: int) -> bool:
    """Telegram group / supergroup ids are negative (-100xxxxxxxxxx). A positive id of that shape is almost
    certainly the same chat typed without its minus sign: as entered it never matches, so nothing is ingested."""
    s = str(chat_id)
    return chat_id > 0 and s.startswith("100") and len(s) >= 13


def repair_source_ids(db: Session) -> int:
    """Fix allow-list entries saved without the minus sign (see above). Returns how many were repaired."""
    fixed = 0
    for source in db.scalars(select(TelegramSource)):
        if looks_like_unsigned_group_id(source.chat_id) and db.scalar(
                select(TelegramSource.id).where(TelegramSource.chat_id == -source.chat_id)) is None:
            logger.warning("Telegram source %s was saved without its minus sign; using %s", source.chat_id, -source.chat_id)
            if source.display_name == str(source.chat_id):
                source.display_name = str(-source.chat_id)
            source.chat_id = -source.chat_id
            fixed += 1
    if fixed:
        db.commit()
    return fixed


def get_bot_state(db: Session) -> TelegramBotState:
    state = db.scalar(select(TelegramBotState).limit(1))
    if state is None:
        state = TelegramBotState()
        db.add(state)
        db.flush()
    return state


class TelegramIngestor:
    def __init__(self, db: Session, client: TelegramBotClient, runtime: TelegramRuntime) -> None:
        self.db = db
        self.client = client
        self._confirm: list[tuple[int, str]] = []  # (private chat id, purpose) of valid website sign-ins this sync
        self._crew: list[tuple[int, int, str, bool]] = []  # (chat id, user id, code, is_bot): "Invite your crew" Start presses
        self._wk_updates: list[dict] = []  # Worker System: community join updates seen in this batch
        self._wk_starts: list[tuple[int, int, str, str | None, bool]] = []  # Worker System: (chat, user, code, username, is_bot)
        self._welcome: dict[int, list[str]] = {}  # signal group chat id -> names of people who just joined it
        self._moderation: list[tuple] = []  # group rules (app/telegram/moderation.py): admin commands and messages with links
        self._commands: list[tuple[int, int, str, bool, int | None]] = []  # Trading OS: (chat, user, "/cmd args", in a group?, message id)
        self._followups: list[tuple[int, int, str, int, int]] = []  # Vortex trade checks: (chat, user, text, message id, replied-to id)
        self.runtime = runtime

    async def verify(self) -> TelegramBotState:
        """Check the token and record the bot's identity."""
        me = await self.client.get_me()
        state = get_bot_state(self.db)
        state.bot_id = me.get("id")
        state.bot_username = me.get("username")
        state.bot_name = me.get("first_name")
        # getMe tells us whether privacy mode is off (needed to read ordinary group messages).
        conn = get_connector_state(self.db, Platform.TELEGRAM)
        conn.capabilities = {
            **(conn.capabilities or {}),
            "can_read_all_group_messages": me.get("can_read_all_group_messages"),
            "can_join_groups": me.get("can_join_groups"),
        }
        conn.account_external_id = str(me.get("id")) if me.get("id") is not None else None
        conn.account_username = me.get("username")
        self.db.commit()
        return state

    async def sync(self, long_poll_seconds: int = 0, listen_seconds: float = 0.0) -> SyncReport:
        """One pass. With `listen_seconds` it keeps long-polling for that long and handles each batch the moment it arrives
        (a command is answered within about a second), instead of returning after the first batch and leaving a gap until
        the scheduler's next run."""
        report = SyncReport()
        state = get_bot_state(self.db)
        if state.bot_id is None:
            self.db.commit()  # (a first run creates the state row)
            await self.verify()
            state = get_bot_state(self.db)
        until = time.monotonic() + listen_seconds

        for batch in range(MAX_BATCHES_PER_SYNC if not listen_seconds else 100_000):
            offset = state.last_update_id + 1 if state.last_update_id is not None else None
            remaining = until - time.monotonic()
            wait = long_poll_seconds if batch == 0 else (max(1, min(long_poll_seconds, int(remaining))) if remaining >= 1 else 0)
            # the long poll waits up to `wait` seconds: whatever the last batch's handlers wrote is saved first, so no
            # write transaction (SQLite's single write lock) is held while Telegram has nothing to say
            self.db.commit()
            updates = await self.client.get_updates(offset, timeout=wait)
            if not updates:
                if until - time.monotonic() >= 1:
                    continue  # nothing yet: keep listening until this pass's time is up
                break
            if batch == 0:
                repair_source_ids(self.db)
            sources = {s.chat_id: s for s in self.db.scalars(select(TelegramSource))}
            max_update_id = state.last_update_id or 0
            stored_before = report.stored  # `report` adds up every batch of this pass
            access_updates: list[dict] = []  # VIP Trading Bot private group: join requests and joins/leaves
            try:
                for update in updates:
                    report.updates += 1
                    uid = update.get("update_id") if isinstance(update, dict) else None
                    if isinstance(uid, int):
                        max_update_id = max(max_update_id, uid)
                    if isinstance(update, dict) and "callback_query" in update:
                        access_updates.append(update)  # Trading OS signal buttons (handled after the commit)
                        report.ignored_other += 1
                        continue
                    if isinstance(update, dict) and ("chat_join_request" in update or "chat_member" in update):
                        access_updates.append(update)  # never stored as community content
                        if "chat_member" in update:
                            self._wk_updates.append(update)  # Worker System: joins through a worker's invite link
                        report.ignored_other += 1
                        continue
                    try:
                        self._handle(update, sources, report)
                    except SQLAlchemyError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - one odd update must not block every later one
                        # Its offset is still acknowledged below: re-raising would re-deliver (and fail on) it every run.
                        report.malformed += 1
                        logger.warning("Skipping Telegram update %s that could not be handled (%s)", uid, type(exc).__name__)
                # Messages and the new offset are committed together: if this fails, nothing
                # is acknowledged, Telegram re-delivers, and the unique key prevents duplicates.
                state.last_update_id = max_update_id
                state.total_ingested += report.stored - stored_before
                self.db.commit()
            except SQLAlchemyError:
                self.db.rollback()
                raise
            await self._moderate()
            await self._send_confirmations()
            await self._crew_starts()
            await self._workers()
            await self._welcomes()
            await self._private_group([u for u in access_updates if "callback_query" not in u])
            await self._trading_os([u["callback_query"] for u in access_updates if "callback_query" in u])
            if len(updates) < 100 and until - time.monotonic() < 1:
                break

        now = utcnow()
        state = get_bot_state(self.db)
        state.status = "connected"
        state.last_sync_at = now
        state.last_success_at = now
        state.last_error = None
        state.ignored_updates += report.ignored_unauthorised
        for source in self.db.scalars(select(TelegramSource).where(TelegramSource.enabled.is_(True))):
            source.last_sync_at = now
            if source.id in report.sources:
                source.last_error = None
        self.db.commit()
        return report

    def _handle(self, update: object, sources: dict[int, TelegramSource], report: SyncReport) -> None:
        if isinstance(update, dict) and (update.get("message") or {}).get("new_chat_members"):
            self._wk_updates.append(update)  # Worker System: who joined the community (after the commit)
        joined = new_members_in_signal_group(update, self._signal_group())
        if joined:
            chat_id, names = joined
            self._welcome.setdefault(chat_id, []).extend(names)  # one welcome per batch, after the commit
        try:
            parsed = parse_update(update)  # type: ignore[arg-type]
        except MalformedUpdateError as exc:
            report.malformed += 1
            logger.warning("Skipping malformed Telegram update: %s", exc)
            return

        if parsed is None:
            report.ignored_other += 1
            return

        if self._member_sign_in(parsed):
            report.ignored_other += 1  # a website sign-in; never stored as a message
            return
        fc = None
        # an anonymous admin / a post as the group or channel comes "from" Telegram's stand-in bot: in a group it may use the
        # public market commands (never linked to a member; account and trading commands stay private, see bot.handle_command)
        anonymous = isinstance(parsed, ParsedMessage) and parsed.author_is_anonymous and parsed.chat.chat_type != "private"
        person = isinstance(parsed, ParsedMessage) and (not parsed.author_is_bot or anonymous) and parsed.author_id
        if person:
            from app.tradingos.bot import fc_command

            fc = fc_command(parsed.text)  # "fc gold": a plain-text price request, as another price bot takes them
        if person and (fc or parsed.text.strip().startswith("/")):
            from app.tradingos.bot import PUBLIC, is_ours

            group = parsed.chat.chat_type != "private"
            word = (fc or parsed.text.strip()).split()[0].split("@")[0].lower()
            from app.telegram import moderation

            if group and word in moderation.COMMANDS:  # /warn, /unmute, /strikes: only in the allow-listed groups, handled after the commit
                # only an anonymous admin (posting as the group itself) skips the admin check; posting as a channel never does
                if parsed.chat.chat_id in sources and is_ours(parsed.text, get_bot_state(self.db).bot_username) and not parsed.edited:
                    self._moderation.append(("cmd", parsed.chat.chat_id, parsed.author_id, parsed.as_group, word, parsed.text.split()[1:],
                                             parsed.reply_author_id, parsed.reply_author_name, parsed.reply_to_message_id, parsed.message_id))
                report.ignored_other += 1
                return
            if (fc or not group or is_ours(parsed.text, get_bot_state(self.db).bot_username)) and (not anonymous or word in PUBLIC):
                logger.info("Bot command received: %s in %s chat %s%s (update %s)", "fc" if fc else word, "a group" if group else "a private",
                            parsed.chat.chat_id if group else "-", " from an anonymous admin / the group" if anonymous else "", parsed.update_id)
                self._commands.append((parsed.chat.chat_id, parsed.author_id, (fc or parsed.text.strip())[:400], group,
                                       getattr(parsed, "message_id", None), parsed.chat.username, parsed.author_name))  # answered after the commit
                report.ignored_other += 1  # a command to the bot; never stored as a message
                return
        if (isinstance(parsed, ParsedMessage) and not parsed.author_is_bot and parsed.author_id and parsed.reply_to_message_id
                and not parsed.edited and parsed.text.strip()):
            from app.tradingos import assist

            if assist.is_reply_to_check(self.db, parsed.chat.chat_id, parsed.reply_to_message_id):  # a follow-up to a trade check
                self._followups.append((parsed.chat.chat_id, parsed.author_id, parsed.text.strip()[:assist.MAX_QUESTION],
                                        parsed.message_id, parsed.reply_to_message_id))  # answered after the commit

        source = sources.get(parsed.chat.chat_id)
        if isinstance(parsed, MembershipChange):
            if source is None:
                self.runtime.note_unauthorised(parsed.chat, bot_status=parsed.status, message=False)
            report.ignored_other += 1
            return

        if source is None:
            # Not on the allow-list: discard the message; remember only that the chat exists.
            self.runtime.note_unauthorised(parsed.chat)
            report.ignored_unauthorised += 1
            return
        # rule 5, automatically: a link from a member (admins are checked after the commit) is never stored, and is deleted
        # never a channel's own post or its automatic copy (777000, linked discussion group), never the group itself (anonymous admins),
        # never another bot. A member posting as their own channel can't be muted: the message is deleted (no strike).
        if (source.enabled and isinstance(parsed, ParsedMessage) and parsed.chat.chat_type in ("group", "supergroup") and parsed.author_id
                and not parsed.automatic_forward and not parsed.channel_post and not parsed.as_group and (not parsed.author_is_bot or parsed.as_channel)):
            from app.telegram import moderation

            if moderation.links_in(parsed.text, get_bot_state(self.db).bot_username):
                if parsed.as_channel:
                    self._moderation.append(("delete", parsed.chat.chat_id, parsed.message_id))
                else:
                    self._moderation.append(("link", parsed.chat.chat_id, parsed.author_id, parsed.author_name, parsed.message_id, parsed.text))
                report.ignored_other += 1
                return
        if not (source.enabled and source.ingestion_enabled):
            report.ignored_disabled += 1
            return

        self._store(source, parsed, report)

    def _member_sign_in(self, parsed: object) -> bool:
        """'/start pc_<code>' sent privately to the bot from a Price Chat website sign-in link. Only who pressed Start
        (Telegram user id, @username, name) is used. A valid code gets one fixed confirmation back (after the commit)."""
        from app.members.auth import TELEGRAM_CODE_PREFIX, telegram_seen_start

        if not isinstance(parsed, ParsedMessage) or parsed.chat.chat_type != "private" or parsed.author_is_bot or not parsed.author_id:
            return False
        parts = parsed.text.strip().split(maxsplit=1)
        from app.members.crew import START_PREFIX

        if len(parts) == 2 and parts[0] == "/start" and parts[1].startswith(START_PREFIX):
            self._crew.append((parsed.chat.chat_id, parsed.author_id, parts[1][len(START_PREFIX):][:24], parsed.author_is_bot))
            return True  # a friend's invite (answered after the commit); never stored as a message
        from app.workers.service import START_PREFIX as WK_PREFIX

        if len(parts) == 2 and parts[0] == "/start" and parts[1].startswith(WK_PREFIX):
            self._wk_starts.append((parsed.chat.chat_id, parsed.author_id, parts[1][len(WK_PREFIX):][:24], parsed.chat.username, parsed.author_is_bot))
            return True  # a Worker's invite link (answered after the commit); never stored as a message
        if len(parts) != 2 or parts[0] != "/start" or not parts[1].startswith(TELEGRAM_CODE_PREFIX):
            return False
        purpose = telegram_seen_start(self.db, parts[1][len(TELEGRAM_CODE_PREFIX):], parsed.author_id, parsed.chat.username, parsed.author_name)
        if purpose:
            self._confirm.append((parsed.chat.chat_id, purpose))
        return True

    async def _moderate(self) -> None:
        """The group rules: admins' /warn, /unmute, /strikes, and messages with links (app/telegram/moderation.py)."""
        from app.telegram import moderation

        pending, self._moderation = self._moderation, []
        if not pending:
            return
        state = get_bot_state(self.db)
        for item in pending:
            try:
                if item[0] == "delete":
                    await moderation.delete_quietly(self.client, item[1], item[2])
                    continue
                if item[0] == "cmd":
                    _, chat, uid, anon, cmd, words, r_uid, r_name, r_mid, mid = item
                    reply = await moderation.command(self.db, self.client, chat, uid, anon, cmd, words, r_uid, r_name, r_mid, mid, state.bot_id)
                else:
                    _, chat, uid, name, mid, text = item
                    reply = await moderation.check_links(self.db, self.client, chat, uid, name, mid, text, state.bot_username)
                if reply:
                    await self.client.send_message(chat, reply, html=True)
            except Exception as exc:  # noqa: BLE001 - one failed action never stops the others or the sync
                self.db.rollback()
                logger.warning("Group rule action not done (%s)", type(exc).__name__)

    async def _send_confirmations(self) -> None:
        """'It worked' for each valid sign-in just seen. Best effort: a failure never affects the sign-in itself."""
        texts = {"login": "✅ Done! You're signed in to Price Chat.\n\nGo back to the Price Chat page in your browser: it continues by itself.",
                 "link": "✅ Telegram is now connected to your Kiwi Passport.\n\nGo back to the Price Chat page in your browser."}
        pending, self._confirm = self._confirm, []
        for chat_id, purpose in pending:
            try:
                await self.client.confirm_sign_in(chat_id, texts.get(purpose, texts["login"]))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Sign-in confirmation not sent (%s)", type(exc).__name__)

    def _signal_group(self) -> str:
        from app.tradingos.signals import settings as ts_settings

        if not hasattr(self, "_group_name"):
            try:
                self._group_name = (ts_settings(self.db).publish_chat or "").lstrip("@").lower()
            except Exception:  # noqa: BLE001
                self._group_name = ""
        return self._group_name

    async def _welcomes(self) -> None:
        """New people in the signal group: one short welcome with a Start button (the private setup comes after Start)."""
        from app.tradingos.bot import welcome_group

        pending, self._welcome = self._welcome, {}
        bot = get_bot_state(self.db).bot_username or "cryptopricechatbot"
        for chat_id, names in pending.items():
            try:
                text, buttons = welcome_group(names, bot)
                await self.client.send_message(chat_id, text, buttons, html=True)
            except Exception as exc:  # noqa: BLE001 - e.g. the bot may not post there: never breaks the sync
                logger.warning("Signal group welcome not sent (%s)", type(exc).__name__)

    async def _workers(self) -> None:
        """Worker System: record community joins (idempotent) and answer worker-link Starts privately. Never breaks the sync."""
        from app.workers import service as wk

        updates, self._wk_updates = self._wk_updates, []
        for u in updates:
            try:
                wk.joins_from_update(self.db, u)
                self.db.commit()
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                logger.warning("Worker join not recorded (%s)", type(exc).__name__)
        starts, self._wk_starts = self._wk_starts, []
        for chat_id, user_id, code, username, is_bot in starts:
            try:
                await self.client.send_message(chat_id, wk.on_start(self.db, code, user_id, username, is_bot))
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                logger.warning("Worker invite start not handled (%s)", type(exc).__name__)

    async def _crew_starts(self) -> None:
        """Invite your crew: record who invited this Telegram account and answer privately with the community link (only
        to the person who just pressed Start). A failure never affects the rest of the sync."""
        from app.members import crew

        pending, self._crew = self._crew, []
        for chat_id, user_id, code, is_bot in pending:
            try:
                text = await crew.on_start(self.db, self.client, code, user_id, is_bot)
                await self.client.send_message(chat_id, text)
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                logger.warning("Crew invite not handled (%s)", type(exc).__name__)

    async def _trading_os(self, callbacks: list[dict]) -> None:
        """Trading OS: answer the private-chat commands and signal-card buttons seen in this batch (after its commit)."""
        from app.tradingos import bot

        pending, self._commands = self._commands, []
        for chat_id, user_id, text, group, msg_id, chat_username, author in pending:
            try:
                await bot.handle_command(self.db, self.client, chat_id, user_id, text, group=group, reply_to=msg_id,
                                         chat_username=chat_username, author=author)
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                # TelegramError messages are written by our client and never carry the token (see client._call)
                logger.warning("Bot command not answered in chat %s (%s%s)", chat_id if group else "-", type(exc).__name__,
                               f": {str(exc)[:200]}" if isinstance(exc, TelegramError) else "")
        followups, self._followups = self._followups, []
        if followups:
            from app.tradingos import assist

            for chat_id, user_id, text, msg_id, replied_to in followups:
                try:
                    await assist.followup(self.db, self.client, chat_id, user_id, text, msg_id, replied_to)
                except Exception as exc:  # noqa: BLE001
                    self.db.rollback()
                    logger.warning("Trade check follow-up not handled (%s)", type(exc).__name__)
        for cq in callbacks:
            try:
                await bot.handle_callback(self.db, self.client, cq)
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                logger.warning("Signal button not handled (%s: %s)", type(exc).__name__, str(exc)[:200])

    async def _private_group(self, updates: list[dict]) -> None:
        """Join requests / joins / leaves for the VIP Trading Bot private group (after the batch is committed, so a
        failure here never loses messages). Updates for any other chat are ignored."""
        from app.tradingbot.service import on_update

        for update in updates:
            try:
                await on_update(self.db, self.client, update)
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                logger.warning("Private group update not handled (%s)", type(exc).__name__)

    def _store(self, source: TelegramSource, msg: ParsedMessage, report: SyncReport) -> None:
        store_message(self.db, source, msg, report)


def store_message(db: Session, source: TelegramSource, msg: ParsedMessage, report: SyncReport) -> None:
    """Store one message from an authorised source (bot or account mode). Duplicates are skipped;
    an edited message updates the stored text and is processed again."""
    existing = db.scalar(
        select(TelegramMessage).where(
            TelegramMessage.chat_id == msg.chat.chat_id, TelegramMessage.message_id == msg.message_id
        )
    )
    if existing is not None:
        if msg.edited and msg.text != existing.text:
            existing.text = msg.text
            existing.edited_at = msg.edited_at or utcnow()
            existing.processed_at = None  # re-process the new wording
            report.edited += 1
        else:
            report.duplicates += 1
        return

    db.add(
        TelegramMessage(
            source_id=source.id,
            chat_id=msg.chat.chat_id,
            message_id=msg.message_id,
            thread_id=msg.thread_id,
            reply_to_message_id=msg.reply_to_message_id,
            author_id=msg.author_id,
            author_name=msg.author_name,
            author_is_bot=msg.author_is_bot,
            sent_at=msg.sent_at,
            edited_at=msg.edited_at,
            text=msg.text,
            media_type=msg.media_type,
            forward_from=msg.forward_from,
            forward_date=msg.forward_date,
        )
    )
    db.flush()  # surfaces a duplicate in the same batch immediately
    source.message_count += 1
    if source.last_message_at is None or msg.sent_at > source.last_message_at:
        source.last_message_at = msg.sent_at
    report.stored += 1
    report.sources.add(source.id)


def new_members_in_signal_group(update: object, group_username: str) -> tuple[int, list[str]] | None:
    """(chat id, first names) of people (not bots) who just joined the Trading OS signal group, from Telegram's join message."""
    if not group_username or not isinstance(update, dict):
        return None
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if (chat.get("username") or "").lower() != group_username:
        return None
    names = [str(u.get("first_name") or u.get("username") or "there")[:40] for u in msg.get("new_chat_members") or [] if not u.get("is_bot")]
    return (int(chat["id"]), names) if names else None
