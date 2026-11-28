"""Community Intelligence in Telegram (@CryptoPriceChatBot): as simple as it gets.

- In a private chat, paste a link or forward a post: that's a tip. No command needed (/tip only explains this).
- The bot answers with a receipt (CI-0142), what the automatic checks found, and one question with two buttons: do you hold a position?
  (Required: the desk never sees a tip before it's answered.) A second row switches "Spotted by" credit and the tip's type.
- In the group, /tip gets a short private-chat link that deletes itself after a minute. Tips are never posted anywhere by the bot.
- The desk's decisions and questions arrive here too (deliver_notices, from the job). Inert while COMMUNITY_INTEL_MODE is off.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.community_intel import rules
from app.community_intel import service as ci
from app.models.community_intel import CiEvent, CiSubmission

log = logging.getLogger(__name__)
PREFIX = "ci:"
GROUP_REPLY_TTL = 60
HOW = ("🔎 <b>Send Price Chat a tip</b>\nPaste a link here, or forward me a post from a channel. Add a line on why it matters if you like.\n\n"
       "News, research, market anomalies and discoveries are all welcome. An editor reads every tip; nothing is published automatically.")


def e(s: Any) -> str:
    return html.escape(str(s), quote=False)


def is_command(text: str) -> bool:
    parts = text.strip().split()
    if not parts:
        return False
    word = parts[0].split("@")[0].lower()
    return word == "/tip" or (word == "/start" and len(parts) > 1 and parts[1].lower() == "tip")


def looks_like_tip(text: str, forward_from: str | None, links: tuple[str, ...] = ()) -> bool:
    return bool(forward_from) or bool(links) or bool(ci.find_url(text))


def _site(settings: Any) -> str:
    return str(getattr(settings, "members_public_url", "") or "https://pricechat.live").rstrip("/")


def receipt(s: CiSubmission) -> tuple[str, list[list[dict[str, str]]]]:
    c = s.checks or {}
    src, cor, fig, dup = c.get("source") or {}, c.get("corroboration") or {}, c.get("figures") or {}, s.dup or {}
    lines = [f"✅ <b>Received · {ci.ref(s)}</b> ({e(rules.KIND_LABEL[s.kind])})", "", "Checked so far:"]
    lines.append(f"• Source: {e(src.get('label', 'none'))}" + (f" ({e(src.get('outlet'))})" if src.get("outlet") else ""))
    if s.verified:
        lines.append(f"• Confirmed: {e(s.verified_by)} ✓")
    elif cor.get("independent"):
        lines.append(f"• Also reported by: {e(', '.join(cor['independent'][:3]))}")
    else:
        lines.append("• Not confirmed by another source yet: the desk keeps checking")
    for f in (fig.get("figures") or [])[:3]:
        lines.append(f"• {e(f['what'])} {e(f['said'])}: measured {e(f['measured'])} {'✓' if f['ok'] else '✗'}")
    if dup.get("kind") == "tip":
        lines.append(f"• Another member sent this first ({e(dup.get('ref'))}): you're credited as \"also spotted\"" if dup.get("exact")
                     else f"• Looks like {e(dup.get('ref'))}, sent earlier")
    elif dup.get("kind") in ("story", "covered"):
        lines.append("• The Terminal already has this story" + (", from before your tip" if dup.get("kind") == "covered" else ""))
    else:
        lines.append("• New: nothing like it in the Terminal yet")
    rows: list[list[dict[str, str]]] = []
    if s.disclosure is None and s.status != "duplicate":
        lines += ["", "<b>One question before the desk sees it:</b> do you hold a position in what this is about? (Only the editor sees the answer.)"]
        rows.append([{"text": "No position", "callback_data": f"{PREFIX}d:{s.id}:none"}, {"text": "I hold a position", "callback_data": f"{PREFIX}d:{s.id}:holds"}])
    else:
        lines += ["", "An editor will look at it. Nothing is published automatically."]
    rows.append([{"text": "✓ Credit me if published" if s.credit else "Credit: anonymous", "callback_data": f"{PREFIX}c:{s.id}"},
                 {"text": f"Type: {rules.KIND_LABEL[s.kind]}", "callback_data": f"{PREFIX}k:{s.id}"}])
    return "\n".join(lines), rows


async def handle_message(db: Session, client: Any, settings: Any, chat_id: int, user_id: int, text: str, *, forward_from: str | None = None,
                         forward_key: str | None = None, links: tuple[str, ...] = ()) -> bool:
    """A private message with a link or a forwarded post. True when it was handled as a tip (or refused with a reason)."""
    if ci.mode(settings) == "off" or not looks_like_tip(text, forward_from, links):
        return False
    from app.predictions.service import member_for_tg

    member = member_for_tg(db, user_id) if user_id else None
    if not ci.visible(member, settings):
        if ci.mode(settings) != "live":
            return False  # preview: everyone else sees the bot as it was
        await client.send_message(chat_id, "🔎 Link Telegram to your Kiwi Passport and you can send Price Chat tips here: paste a link or forward a post.",
                                  buttons=[[{"text": "🔗 Link my Passport", "url": f"{_site(settings)}/passport"}]])
        return True
    url = ci.find_url(text) or (links[0] if links else "")
    try:
        s = ci.submit(db, member, via="telegram", text=text, url=url, forward_from=forward_from, forward_key=forward_key, tg_chat_id=chat_id)  # type: ignore[arg-type]
    except ci.CiError as exc:
        db.rollback()
        await client.send_message(chat_id, f"⚠️ {e(exc)}", html=True)
        return True
    body, rows = receipt(s)
    await client.send_message(chat_id, body, buttons=rows, html=True)
    return True


async def handle_command(db: Session, client: Any, settings: Any, chat_id: int, user_id: int, text: str, group: bool, reply_to: int | None = None) -> bool:
    if not is_command(text) or ci.mode(settings) == "off":
        return False
    from app.predictions.service import member_for_tg

    member = member_for_tg(db, user_id) if user_id else None
    if ci.mode(settings) == "preview" and not ci.visible(member, settings):
        return False
    if not group:
        await client.send_message(chat_id, HOW, html=True)
        return True
    bot = None
    try:
        from app.telegram.ingestion import get_bot_state

        bot = get_bot_state(db).bot_username
    except Exception:  # noqa: BLE001
        pass
    sent = await client.send_message(chat_id, "🔎 Tips go to me in a private chat: paste a link or forward a post there.",
                                     buttons=[[{"text": "Send a tip", "url": f"https://t.me/{bot}?start=tip"}]] if bot else None, reply_to=reply_to, silent=True)
    if isinstance(sent, dict) and sent.get("message_id"):
        mid = int(sent["message_id"])

        async def _gone() -> None:
            try:
                await client.delete_message(chat_id, mid)
            except Exception:  # noqa: BLE001
                pass

        try:
            asyncio.get_running_loop().call_later(GROUP_REPLY_TTL, lambda: asyncio.ensure_future(_gone()))
        except RuntimeError:
            pass
    return True


async def handle_callback(db: Session, client: Any, settings: Any, cq: dict[str, Any]) -> bool:
    data = str(cq.get("data") or "")
    if not data.startswith(PREFIX):
        return False
    uid = int((cq.get("from") or {}).get("id") or 0)
    from app.predictions.service import member_for_tg

    member = member_for_tg(db, uid) if uid else None
    parts = data[len(PREFIX):].split(":")
    s = db.get(CiSubmission, int(parts[1])) if len(parts) >= 2 and parts[1].isdigit() else None
    if s is None or member is None or s.member_id != member.id or ci.mode(settings) == "off":
        await client.answer_callback(cq["id"], "That button isn't for you.")
        return True
    if parts[0] == "d" and len(parts) == 3:
        if s.disclosure is None:
            ci.set_disclosure(db, s, parts[2])
        await client.answer_callback(cq["id"], "Thanks. The desk has it now." if parts[2] == "none" else "Noted for the editor. The desk has it now.")
    elif parts[0] == "c":
        if s.status in rules.OPEN or s.status == "accepted":
            s.credit = not s.credit
            ci.log(db, s, f"member:{member.id}", "credit", on=s.credit)
            db.commit()
        await client.answer_callback(cq["id"], "You'll be credited if it's published." if s.credit else "You'll stay anonymous.")
    elif parts[0] == "k":
        if s.status in rules.OPEN:
            s.kind = rules.KINDS[(rules.KINDS.index(s.kind) + 1) % len(rules.KINDS)]
            ci.log(db, s, f"member:{member.id}", "kind", kind=s.kind)
            db.commit()
        await client.answer_callback(cq["id"], f"Type: {rules.KIND_LABEL[s.kind]}")
    else:
        await client.answer_callback(cq["id"], "That button has expired.")
        return True
    msg = cq.get("message") or {}
    if msg.get("message_id"):
        body, rows = receipt(s)
        try:
            await client.edit_message_text(msg["chat"]["id"], int(msg["message_id"]), body, html=True, buttons=rows)
        except Exception as exc:  # noqa: BLE001 - "not modified" and friends
            log.info("intel: receipt not edited (%s)", type(exc).__name__)
    return True


async def deliver_notices(db: Session, client: Any, limit: int = 30) -> int:
    """The desk's decisions, questions and appeal results, sent privately to the member (where the tip came from, or their linked
    Telegram). Each notice once."""
    from app.models.members import LinkedIdentity

    n = 0
    rows = [ev for ev in db.scalars(select(CiEvent).where(CiEvent.action == "notice").order_by(CiEvent.id.desc()).limit(300))
            if (ev.details or {}).get("tg") == "pending"][:limit]
    for ev in reversed(rows):
        s = db.get(CiSubmission, ev.submission_id)
        chat = s.tg_chat_id if s is not None else None
        if s is not None and chat is None:
            link = db.scalar(select(LinkedIdentity).where(LinkedIdentity.member_id == s.member_id, LinkedIdentity.platform == "telegram"))
            chat = int(link.platform_user_id) if link is not None and str(link.platform_user_id).isdigit() else None
        state = "none"
        if chat is not None:
            try:
                await client.send_message(chat, f"🔎 <b>{e((ev.details or {}).get('title') or '')}</b>\n{e(ev.note)}", html=True)
                state, n = "sent", n + 1
            except Exception as exc:  # noqa: BLE001 - blocked bot and friends: the Passport still has it
                log.info("intel: notice not sent (%s)", type(exc).__name__)
                state = "failed"
        ev.details = {**(ev.details or {}), "tg": state}
        db.commit()
        await asyncio.sleep(0.05)
    return n
