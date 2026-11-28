"""/passport in Telegram (@CryptoPriceChatBot): the member's personal share card.

- In a private chat: the card (drawn from the public page's choices), with buttons to open the Passport and, when the page is public,
  to share it on X or Telegram.
- In the group: the card goes to the member's private chat; the group only sees a short reply that deletes itself after a minute.
- Not linked yet: one button to link Telegram to a Kiwi Passport. Inert while PASSPORT2_MODE is off (the message goes on as before).
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.passport import cards, service

log = logging.getLogger(__name__)
GROUP_REPLY_TTL = 60


def is_ours(text: str) -> bool:
    parts = text.strip().split()
    if not parts:
        return False
    word = parts[0].split("@")[0].lower()
    return word == "/passport" or (word == "/start" and len(parts) > 1 and parts[1].lower() == "passport")


def _site(settings: Any) -> str:
    return str(getattr(settings, "members_public_url", "") or "https://pricechat.live").rstrip("/")


def _bot(db: Session) -> str | None:
    try:
        from app.telegram.ingestion import get_bot_state

        return get_bot_state(db).bot_username
    except Exception:  # noqa: BLE001
        return None


def card_message(db: Session, member: Any, settings: Any) -> tuple[bytes, str, list[list[dict[str, str]]]]:
    from app.api.routes.passport import _payload

    service.ensure_catalog(db)
    p = _payload(db, member)
    site = _site(settings)
    host = site.split("://", 1)[-1]
    png = cards.link_card(p, host)
    name = html.escape(str(p.get("display_name") or p["username"]), quote=False)
    lines = [f"🪪 <b>{name}</b>" + (f" · {html.escape(str(p['title']), quote=False)}" if p.get("title") else ""),
             f"{p['rank']['name']} · Rank {p['rank']['level']}" + (f" · Community {p['community']['level']}" if p.get("community") else "")]
    buttons: list[list[dict[str, str]]] = [[{"text": "🪪 Open my Passport", "url": f"{site}/passport#profile"}]]
    if member.public_profile:
        url = f"{site}/u/{member.username}"
        text = "My Kiwi Passport on Price Chat 🥝"
        lines.append(f"Share it: {url}")
        buttons.append([{"text": "Share on X", "url": f"https://x.com/intent/post?text={quote(text)}&url={quote(url)}"},
                        {"text": "Share on Telegram", "url": f"https://t.me/share/url?url={quote(url)}&text={quote(text)}"}])
    else:
        lines.append("Your Passport is private, so only you can see this card. Make it public in your Passport to share it.")
    return png, "\n".join(lines), buttons


async def handle_command(db: Session, client: Any, settings: Any, chat_id: int, user_id: int, text: str, group: bool,
                         reply_to: int | None = None) -> bool:
    """True when it was ours (answered)."""
    if not is_ours(text) or service.mode(settings) == "off":
        return False
    from app.predictions.service import member_for_tg

    member = member_for_tg(db, user_id) if user_id else None
    if member is not None and member.status != "active":
        return False
    if not service.visible(member, settings):
        if service.mode(settings) != "live":
            return False  # preview: everyone else sees the bot as it was
        site = _site(settings)
        body = "Link Telegram to your Kiwi Passport and /passport makes your personal share card."
        sent = await client.send_message(chat_id, body, buttons=[[{"text": "🔗 Link my Passport", "url": f"{site}/passport"}]],
                                         reply_to=reply_to if group else None, silent=group)
        _expire(client, chat_id, sent, group)
        return True
    png, caption, buttons = card_message(db, member, settings)
    if not group:
        await client.send_photo(chat_id, png, caption, buttons=buttons, html=True)
        return True
    try:  # the card goes to the private chat; the group sees one short line
        await client.send_photo(user_id, png, caption, buttons=buttons, html=True)
        body, kb = "🪪 I've sent your Passport card in a private chat.", None
    except Exception as exc:  # noqa: BLE001 - they never started the bot: give them the way in
        log.info("passport: private card not sent (%s)", type(exc).__name__)
        bot = _bot(db)
        body = "🪪 Tap below and I'll send your Passport card in a private chat."
        kb = [[{"text": "Get my Passport card", "url": f"https://t.me/{bot}?start=passport"}]] if bot else None
    sent = await client.send_message(chat_id, body, buttons=kb, reply_to=reply_to, silent=True)
    _expire(client, chat_id, sent, True)
    return True


def _expire(client: Any, chat_id: int, sent: Any, group: bool) -> None:
    if not group or not isinstance(sent, dict) or not sent.get("message_id"):
        return
    mid = int(sent["message_id"])

    async def _gone() -> None:
        try:
            await client.delete_message(chat_id, mid)
        except Exception:  # noqa: BLE001 - already gone
            pass

    try:
        asyncio.get_running_loop().call_later(GROUP_REPLY_TTL, lambda: asyncio.ensure_future(_gone()))
    except RuntimeError:
        pass
