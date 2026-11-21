"""Corrections and retractions of published News Desk posts, from Admin, with an audit log (owner, 2026-11-21).

Telegram post 204 claimed $10.1B of Binance liquidations in an hour: a unit error, the real figure was about $17-18M, and there was no way to put it
right from Admin. The owner may now edit a channel post (its text, or a photo's caption), reply under it with a correction, delete it, and reply
under or delete the X post.

Nothing goes out without two steps: preview() says exactly what would be done and hands back a confirmation token (valid 10 minutes, single use,
bound to the event, the action and the exact text); execute() acts only with that token. Every confirm, done, refused or failed, writes an audit
row (NdValidation kind "correction") that keeps the original text for good, whatever the post says afterwards.

These are the owner's own repairs, so the emergency stop doesn't hold them (a stop is exactly when a wrong post needs fixing), but the audit row
records that it was on. X keeps the desk's dry run: in dry run nothing reaches X and the row says what would have happened. The owner is the
source of a correction, so the figure and source checks don't run on it; only the platform's length limit is a hard rule.
"""

import hashlib
import html
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdPost, NdTelegramPost, NdValidation
from app.newsdesk import checks, engine

logger = logging.getLogger(__name__)

ACTIONS = ("tg_edit", "tg_reply", "tg_delete", "x_delete", "x_reply")
WITH_TEXT = ("tg_edit", "tg_reply", "x_reply")
KIND = "correction"  # the audit row
TOKEN_KIND = "corr_token"  # a pending confirmation: ref is the token's sha256 (the token itself is never stored), alerted = used
TOKEN_TTL = timedelta(minutes=10)
TG_TEXT, TG_CAPTION, X_LIMIT = 4096, 1024, 280
# checks that don't apply to a channel post: it carries its links and @thepricechat by design
TG_NOT_APPLICABLE = {"length", "no_link", "no_mentions", "hashtags", "attribution", "tickers"}
X_NOT_APPLICABLE = {"length", "attribution", "tickers"}
_TOKENISH = re.compile(r"bot\d+:[A-Za-z0-9_-]+|\d{6,}:[A-Za-z0-9_-]{30,}")


class CorrectionError(Exception):
    """A correction that can't go ahead, in plain words for Admin. status is the HTTP answer."""

    def __init__(self, message: str, status: int = 409, audit: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.audit = status, audit


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _norm(action: str, text: str | None) -> str:
    return (text or "").strip() if action in WITH_TEXT else ""


_TG_TAG = re.compile(r"</?(b|i|s|u|code)>|<a href=\"https://[^\"<>]+\">|</a>")


def tg_html(text: str) -> bool:
    """A correction written with Telegram's own formatting (bold, italic, strikethrough, underline, code, https links) is sent as HTML, so
    "<s>the wrong line</s>" is shown struck through, not as tags. Only those tags, balanced, and no other "<" or "&" markup; anything
    else is sent as plain text, exactly as written."""
    if "<" not in text:
        return False
    rest = _TG_TAG.sub("", text)
    if "<" in rest or ">" in rest:
        return False
    stack: list[str] = []
    for m in _TG_TAG.finditer(text):
        tag = m.group(0)
        name = "a" if tag.startswith(("<a ", "</a")) else (m.group(1) or "")
        if tag.startswith("</"):
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


def tg_length(text: str) -> int:
    """Telegram counts UTF-16 units (an emoji is two) of what the reader sees: formatting tags don't count."""
    shown = html.unescape(_TG_TAG.sub("", text)) if tg_html(text) else text
    return len(shown.encode("utf-16-le")) // 2


def safe(message: str) -> str:
    """A platform's own refusal, passed on as it is, minus anything that looks like a bot token."""
    return _TOKENISH.sub("[hidden]", str(message))[:500]


def _tg_record(db: Session, ev: NdEvent) -> NdTelegramPost | None:
    row = db.get(NdTelegramPost, ev.tg_post_id) if ev.tg_post_id else None
    if row is None and ev.tg_message_id:
        row = db.scalar(select(NdTelegramPost).where(NdTelegramPost.message_id == ev.tg_message_id).order_by(NdTelegramPost.id.desc()).limit(1))
    return row


def _tg_is_photo(db: Session, message_id: int) -> bool:
    """A published picture is audited under telegram:<message id> (validation.record_image): then the text is a caption."""
    return db.scalar(select(NdValidation.id).where(NdValidation.kind == "image", NdValidation.ref == f"telegram:{message_id}")) is not None


def _warnings(text: str, limit: int, skip: set[str]) -> list[str]:
    """check_post as for a person's edit (no figure or source checks): anything else it dislikes is shown, never blocks the owner."""
    result = checks.check_post(text, source_text=text, source_names=[], as_edited=True, max_len=limit)
    return [c["detail"] for c in result.checks if not c["passed"] and c["check"] not in skip]


def plan(db: Session, ev: NdEvent, action: str, text: str | None) -> dict[str, Any]:
    """Exactly what the correction would do, and what stands in its way. Sends nothing."""
    if action not in ACTIONS:
        raise CorrectionError(f"Unknown correction: {action}", 400)
    new = _norm(action, text)
    stop = engine.emergency_stop(db)
    out: dict[str, Any] = {"event_id": ev.id, "action": action, "platform": "telegram" if action.startswith("tg_") else "x", "target": {},
                           "chat": None, "format": None, "current_text": None, "new_text": new or None, "chars": tg_length(new) if new else 0,
                           "limit": None, "problems": [], "warnings": [], "emergency_stop": bool(stop), "stop_reason": stop or None,
                           "mode": None, "dry_run": None}
    problems: list[str] = out["problems"]
    if action in WITH_TEXT and not new:
        problems.append("Write the correction text first")
    if out["platform"] == "telegram":
        if not ev.tg_message_id:
            problems.append("This event has no Telegram message to correct")
            return out
        row = _tg_record(db, ev)
        out["target"] = {"message_id": int(ev.tg_message_id), "post_id": row.id if row else None, "url": row.url if row else None}
        if row is not None and row.chat_id:
            out["chat"], out["target"]["chat_id"] = row.chat or str(row.chat_id), int(row.chat_id)
        else:
            from app.members.telegram_missions import channel_handle

            out["chat"] = (row.chat if row is not None else None) or channel_handle(db)
            if not out["chat"]:
                problems.append("No announcement channel is set")
        out["current_text"] = row.text if row is not None else None
        if ev.tg_status == "deleted" or (row is not None and row.status == "deleted"):
            problems.append("This Telegram message was already deleted")
        photo = _tg_is_photo(db, int(ev.tg_message_id))
        out["format"] = "photo" if photo else "text"
        if action == "tg_edit":
            out["limit"] = TG_CAPTION if photo else TG_TEXT
        elif action == "tg_reply":
            out["limit"] = TG_TEXT
        if new and out["limit"]:
            if out["chars"] > out["limit"]:
                what = "a photo caption" if action == "tg_edit" and photo else "a Telegram message"
                problems.append(f"Too long for {what}: {out['chars']} characters, the limit is {out['limit']}")
            out["warnings"] = _warnings(new, out["limit"], TG_NOT_APPLICABLE)
        return out
    s = engine.settings_row(db)
    out["mode"], out["dry_run"] = s.mode, s.mode != "live"
    post = db.get(NdPost, ev.x_post_id) if ev.x_post_id else None
    if post is None or not post.x_id:
        problems.append("This event has no X post id to correct: nothing was published to X for it")
        out["current_text"] = post.text if post is not None else None
        return out
    out["target"] = {"tweet_id": post.x_id, "post_id": post.id, "url": f"https://x.com/i/status/{post.x_id}"}
    out["current_text"] = post.text
    if post.status == "deleted" or ev.x_status == "deleted":
        problems.append("This X post was already deleted")
    if action == "x_reply":
        out["limit"] = X_LIMIT
        if new:
            from app.newsdesk import network

            out["chars"] = network.x_length(new)
            if out["chars"] > X_LIMIT:
                problems.append(f"Too long for X: {out['chars']} characters (weighted), the limit is {X_LIMIT}")
            out["warnings"] = _warnings(new, X_LIMIT, X_NOT_APPLICABLE)
    return out


# --- the confirmation token ---------------------------------------------------------------------------------------


def issue_token(db: Session, ev: NdEvent, action: str, text: str | None, now: datetime | None = None) -> tuple[str, datetime]:
    now = now or utcnow()
    token = secrets.token_urlsafe(24)
    expires = now + TOKEN_TTL
    db.add(NdValidation(kind=TOKEN_KIND, ref=_sha(token), data={"event": ev.id, "action": action, "text_sha": _sha(_norm(action, text)),
                                                                  "expires_at": expires.isoformat(), "issued_at": now.isoformat()}))
    db.commit()
    return token, expires


def _take_token(db: Session, ev: NdEvent, action: str, text: str | None, token: str, now: datetime) -> str | None:
    """Uses up the token, or says why it can't be used. One UPDATE marks it used, so two confirms can never both pass."""
    row = db.scalar(select(NdValidation).where(NdValidation.kind == TOKEN_KIND, NdValidation.ref == _sha(token or "")))
    if row is None:
        return "This confirmation token is not valid. Preview the correction again."
    data = row.data or {}
    if row.alerted:
        return "This confirmation token was already used. Preview the correction again."
    if data.get("event") != ev.id or data.get("action") != action or data.get("text_sha") != _sha(_norm(action, text)):
        return "This confirmation token was issued for a different correction (the event, action or text changed). Preview it again."
    expires = datetime.fromisoformat(data["expires_at"])
    if (expires if expires.tzinfo else expires.replace(tzinfo=now.tzinfo)) <= now:
        return "This confirmation token has expired (they last 10 minutes). Preview the correction again."
    taken = db.execute(update(NdValidation).where(NdValidation.id == row.id, NdValidation.alerted.is_(False))
                       .values(alerted=True, data={**data, "used_at": now.isoformat()}))
    db.commit()
    if taken.rowcount != 1:
        return "This confirmation token was already used. Preview the correction again."
    return None


async def preview(db: Session, ev: NdEvent, action: str, text: str | None, now: datetime | None = None) -> dict[str, Any]:
    """What would be done, and a token to confirm it with, only when nothing stands in its way."""
    out = plan(db, ev, action, text)
    out["confirm_token"] = out["expires_at"] = None
    if not out["problems"]:
        token, expires = issue_token(db, ev, action, text, now)
        out["confirm_token"], out["expires_at"] = token, expires.isoformat()
    return out


# --- the audit log ------------------------------------------------------------------------------------------------


def _audit(db: Session, p: dict[str, Any], outcome: str, error: str | None, now: datetime, result: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {"at": now.isoformat(), "actor": "owner", "event": p["event_id"], "action": p["action"], "platform": p["platform"], "target": p.get("target") or {},
            "chat": p.get("chat"), "format": p.get("format"), "old_text": p.get("current_text"), "new_text": p.get("new_text"), "outcome": outcome,
            "error": error, "emergency_stop": bool(p.get("emergency_stop")), "stop_reason": p.get("stop_reason"),
            "dry_run": p.get("dry_run"), "mode": p.get("mode"), "result": result or {}}
    row = NdValidation(kind=KIND, ref=f"{p['event_id']}:{secrets.token_hex(8)}", flag=(error or "")[:500], data=data, created_at=now, updated_at=now)
    db.add(row)
    db.commit()
    level = logging.WARNING if outcome in ("failed", "refused") else logging.INFO
    logger.log(level, "newsdesk correction %s event=%s %s target=%s stop=%s: %s", outcome, p["event_id"], p["action"], p.get("target"), bool(p.get("emergency_stop")), error or "ok")
    return audit_out(row)


def audit_out(row: NdValidation) -> dict[str, Any]:
    return {"id": row.id, "ref": row.ref, **(row.data or {})}


def audit_list(db: Session, event_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q = select(NdValidation).where(NdValidation.kind == KIND)
    if event_id is not None:
        q = q.where(NdValidation.ref.like(f"{int(event_id)}:%"))
    return [audit_out(r) for r in db.scalars(q.order_by(NdValidation.id.desc()).limit(limit))]


# --- doing it -----------------------------------------------------------------------------------------------------


def _tg_client(settings: Any) -> Any:
    from app.telegram.service import make_client

    return make_client(settings)


def _note(old: str, line: str) -> str:
    return f"{old} | {line}" if old else line


async def _telegram(db: Session, ev: NdEvent, p: dict[str, Any], client: Any, now: datetime) -> dict[str, Any]:
    from app.telegram.errors import TelegramRequestError

    chat = p["target"].get("chat_id") or p["chat"]
    message_id = p["target"]["message_id"]
    new = p["new_text"] or ""
    action = p["action"]
    fmt = tg_html(new)  # Telegram formatting tags in the owner's text: sent as HTML (see tg_html)
    result: dict[str, Any] = {"html": fmt}
    if action == "tg_edit":
        # the stored format is a good guess, not a promise (a refused card goes out as text): Telegram's own answer settles it
        caption_first = p["format"] == "photo"
        try:
            if caption_first:
                await client.edit_caption(chat, message_id, new, html=fmt)
            else:
                await client.edit_message_text(chat, message_id, new, html=fmt)
        except TelegramRequestError as e:
            words = str(e).lower()
            if caption_first and "no caption" in words and tg_length(new) <= TG_TEXT:
                await client.edit_message_text(chat, message_id, new, html=fmt)
            elif not caption_first and "no text" in words and tg_length(new) <= TG_CAPTION:
                await client.edit_caption(chat, message_id, new, html=fmt)
            else:
                raise
        result["edited"] = message_id
    elif action == "tg_reply":
        sent = await client.send_message(chat, new, reply_to=message_id, html=fmt)
        result["reply_message_id"] = sent.get("message_id") if isinstance(sent, dict) else None
    else:
        await client.delete_message(chat, message_id)
        result["deleted"] = message_id
    # the stored records follow what the channel now shows; the original text lives on in the audit row
    row = _tg_record(db, ev)
    stamp = f"{now:%d %b %H:%M} UTC"
    if action == "tg_edit":
        if row is not None:
            row.text, row.status = new, "corrected"
        ev.tg_note = _note(ev.tg_note, f"corrected by the owner {stamp} (edited in place)")[:2000]
    elif action == "tg_reply":
        if row is not None and row.status != "deleted":
            row.status = "corrected"
        ev.tg_note = _note(ev.tg_note, f"correction posted under it by the owner {stamp} (message {result.get('reply_message_id')})")[:2000]
    else:
        if row is not None:
            row.status = "deleted"
        ev.tg_status = "deleted"
        ev.tg_note = _note(ev.tg_note, f"deleted by the owner {stamp}")[:2000]
    if row is not None:
        row.updated_at = now
    db.commit()
    return result


async def _x(db: Session, ev: NdEvent, p: dict[str, Any], settings: Any, runtime: Any, agent_id: int, now: datetime) -> dict[str, Any]:
    from app.models.enums import Platform, SocialAction
    from app.social.guard import ActionRequest, SocialActionGuard
    from app.social.x_service import make_x_client

    tweet = p["target"]["tweet_id"]
    reply = p["action"] == "x_reply"
    req = ActionRequest(Platform.X, SocialAction.REPLY if reply else SocialAction.DELETE, target_external_id=tweet, text=p["new_text"] if reply else None)
    guard = SocialActionGuard(db, agent_id, settings)
    try:
        permit = guard.issue_correction_permit(req)
    except PermissionError as e:
        raise CorrectionError(f"X guard: {e}") from None
    client = make_x_client(db, settings, runtime)
    try:
        if reply:
            data = await client.create_post(p["new_text"], permit=permit, reply_to=tweet)
            result = {"reply_tweet_id": str(data.get("id") or "") or None}
        else:
            await client.delete_post(tweet, permit=permit)
            result = {"deleted": tweet}
    finally:
        await client.aclose()
    stamp = f"{now:%d %b %H:%M} UTC"
    if reply:
        ev.x_note = _note(ev.x_note, f"correction posted under it by the owner {stamp} (post {result['reply_tweet_id']})")[:2000]
    else:
        post = db.get(NdPost, p["target"]["post_id"])
        if post is not None:
            post.status = "deleted"
        ev.x_status = "deleted"
        ev.x_note = _note(ev.x_note, f"deleted on X by the owner {stamp}")[:2000]
    db.commit()
    return result


async def execute(db: Session, ev: NdEvent, action: str, text: str | None, token: str, *, settings: Any = None, tg: Any = None, x_runtime: Any = None,
                  agent_id: int | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Do the confirmed correction. Every attempt is audited; CorrectionError carries the audit row of a refusal or failure."""
    from app.connectors.x.errors import XError
    from app.telegram.errors import TelegramError

    now = now or utcnow()
    if action not in ACTIONS:
        raise CorrectionError(f"Unknown correction: {action}", 400)
    p = plan(db, ev, action, text)
    why = _take_token(db, ev, action, text, token, now)
    if why:
        raise CorrectionError(why, 409, _audit(db, p, "refused", why, now))
    if p["problems"]:
        why = "; ".join(p["problems"])
        raise CorrectionError(why, 400, _audit(db, p, "refused", why, now))
    try:
        if p["platform"] == "telegram":
            own = tg is None
            client = tg or _tg_client(settings)
            try:
                result = await _telegram(db, ev, p, client, now)
            finally:
                if own:
                    await client.aclose()
        elif p["dry_run"]:
            # the desk is in dry run: X is never called, the row says what would have happened
            result = {"would": f"{'reply under' if action == 'x_reply' else 'delete'} X post {p['target']['tweet_id']}"}
            return _audit(db, p, "dry_run", None, now, result)
        else:
            if agent_id is None:
                from app.services.agent_service import get_agent_config

                agent_id = get_agent_config(db).id
            result = await _x(db, ev, p, settings, x_runtime, agent_id, now)
    except CorrectionError as e:
        db.rollback()
        raise CorrectionError(str(e), 502, _audit(db, p, "failed", safe(str(e)), now)) from None
    except TelegramError as e:
        db.rollback()
        why = f"Telegram refused it: {safe(str(e))}"
        raise CorrectionError(why, 502, _audit(db, p, "failed", why, now)) from None
    except XError as e:
        db.rollback()
        why = f"X refused it: {safe(str(e))}"
        raise CorrectionError(why, 502, _audit(db, p, "failed", why, now)) from None
    except Exception as e:  # noqa: BLE001 - anything else is still audited, never a silent half-done repair
        db.rollback()
        why = f"The correction failed ({type(e).__name__}): {safe(str(e))[:200]}"
        logger.exception("newsdesk correction failed")
        raise CorrectionError(why, 502, _audit(db, p, "failed", why, now)) from None
    return _audit(db, p, "done", None, now, result)
