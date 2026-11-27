"""Price Chat Predictions in Telegram (@CryptoPriceChatBot): the group post, inline voting, /predict /rank /stats /leaderboard, opt-in
private reminders and results.

Quiet by design:
- One group post per call (an image card + inline buttons). A vote is answered with a private pop-up (answerCallbackQuery) that only the
  voter sees: votes add no messages to the group.
- The post's count ("212 calls so far") is an edit of the same message, at most once a minute. The split is never shown before the lock.
- At the lock the same post is edited to show the split; at settlement it is edited again to show the result. The next day's post
  carries yesterday's result in its caption. Mondays carry last week's result and top five.
- A command used in the group gets a short reply that deletes itself after a minute. Everything else is in private chats, and only
  for members who turned it on (reminders, personal results).
- The bot never mutes or bans anyone (telegram/moderation.NO_MUTES).
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.predictions import PredQuestion, PredTgUser, PredVote
from app.predictions import cards, rules, service

log = logging.getLogger(__name__)
PREFIX = "pd:"
COMMANDS = {"/predict", "/rank", "/stats", "/leaderboard", "/remind"}
RULES_TEXT = ("Free game, XP only. Tap Higher or Lower before the lock (20:00 UTC daily). Settles on the Hyperliquid BTC daily close. "
              "Exact tie: no winner. Change your call until the lock.")
_taps: dict[int, deque[float]] = {}


def mode(settings: Any) -> str:
    return str(getattr(settings, "predictions_mode", "off") or "off")


def group_chat(settings: Any) -> str | None:
    m = mode(settings)
    if m == "live":
        return settings.predictions_group or None
    if m == "preview":
        return settings.predictions_preview_chat or None
    return None


def e(s: Any) -> str:
    return html.escape(str(s), quote=False)


def tapped_too_fast(uid: int, now: float | None = None) -> bool:
    now = now or time.monotonic()
    q = _taps.setdefault(uid, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= rules.TAPS_PER_MINUTE:
        return True
    q.append(now)
    return False


def touch_user(db: Session, frm: dict[str, Any], private: bool = False) -> PredTgUser:
    uid = int(frm["id"])
    tu = db.scalar(select(PredTgUser).where(PredTgUser.tg_user_id == uid))
    if tu is None:
        tu = PredTgUser(tg_user_id=uid, username=(frm.get("username") or None), first_name=str(frm.get("first_name") or "")[:64],
                        is_bot=bool(frm.get("is_bot")), dm_ok=private)
        db.add(tu)
    else:
        tu.username = frm.get("username") or tu.username
        tu.first_name = str(frm.get("first_name") or tu.first_name)[:64]
        if private:
            tu.dm_ok = True
    db.flush()
    return tu


# --- texts ------------------------------------------------------------------------------------------------------------------------


def _time(t: datetime) -> str:
    return f"{t:%H:%M} UTC"


def lock_label(q: PredQuestion) -> str:
    t = service._aware(q.locks_at)
    return _time(t) if q.kind == "daily" else f"{t:%a} {_time(t)}"  # type: ignore[arg-type]


def card_png(q: PredQuestion) -> bytes:
    ref = f"vs {'yesterday' if q.kind == 'daily' else 'Sunday'}'s close {service.money(q.ref_price or 0)}"
    iso = q.settle_day.isocalendar()
    return cards.call_card({"kind": q.kind, "title": q.title, "ref_label": ref, "lock_label": lock_label(q),
                            "options": [str(o["label"]) for o in q.options],
                            "settle_label": f"Hyperliquid {q.asset} perpetual · {'daily close 00:00 UTC' if q.kind == 'daily' else 'Sunday close 00:00 UTC'}",
                            "day_label": cards.day_label(q.settle_day) if q.kind == "daily" else f"Week {iso[1]}"})


def result_line(db: Session, q: PredQuestion) -> str:
    """One line for a settled call: "✅ Thu: Bitcoin closed higher ($62,315 vs $61,840). 58% called it." """
    if q.status == "void":
        return f"Cancelled: {e(q.title)} ({e(q.void_reason)}). No XP for it."
    if q.status != "settled":
        return ""
    t = service.tallies(db, q)
    total = sum(t.values())
    if q.outcome == "tie":
        return f"🤝 {cards.day_label(q.settle_day)}: an exact tie at {service.money(q.settle_price or 0)}. No winner; taking part still counts."
    share = f" {t.get(str(q.outcome), 0) / total:.0%} called it." if total else ""
    if q.qtype == "direction":
        return (f"✅ {cards.day_label(q.settle_day)}: {service.name(q.asset)} closed <b>{e(service.label_of(q, q.outcome).lower())}</b> "
                f"({service.money(q.settle_price or 0)} vs {service.money(q.ref_price or 0)}).{share}")
    return f"✅ Week {q.settle_day.isocalendar()[1]}: {service.name(q.asset)} closed Sunday at {service.money(q.settle_price or 0)}: <b>{e(service.label_of(q, q.outcome))}</b>.{share}"


def caption(db: Session, q: PredQuestion, now: datetime) -> str:
    """The group post's caption for its current state (open / locked / settled / void)."""
    t = service.tallies(db, q)
    total = sum(t.values())
    head = f"<b>{e(q.title)}</b>"
    if q.status == "open":
        prev = _previous(db, q)
        lines = [result_line(db, prev)] if prev is not None else []
        if q.kind == "weekly":
            lines += _last_week_top(db, q)
        lines += [head, f"Reference: {service.money(q.ref_price or 0)} ({'yesterday' if q.kind == 'daily' else 'Sunday'}'s Hyperliquid close)",
                  f"🔒 Locks {lock_label(q)} · {total:,} call{'s' if total != 1 else ''} so far · split shown at the lock",
                  "<i>Free game for XP. Not financial advice.</i>"]
        return "\n".join(x for x in lines if x)
    if q.status in ("locked", "held"):
        split = " · ".join(f"{e(o['label'])} {t.get(str(o['key']), 0) / total:.0%}" for o in q.options) if total else "no calls"
        return "\n".join([head, f"🔒 Locked: {total:,} calls. {split}",
                          "Settles on the Hyperliquid close" + (" (on hold: being checked)" if q.status == "held" else "") + "."])
    return "\n".join([head, result_line(db, q), "<i>Free game for XP. Not financial advice.</i>"])


def _previous(db: Session, q: PredQuestion) -> PredQuestion | None:
    return db.scalar(select(PredQuestion).where(PredQuestion.kind == q.kind, PredQuestion.asset == q.asset, PredQuestion.id != q.id,
                                                PredQuestion.settle_day < q.settle_day, PredQuestion.status.in_(("settled", "void")))
                     .order_by(PredQuestion.settle_day.desc()).limit(1))


def _last_week_top(db: Session, q: PredQuestion) -> list[str]:
    start = q.settle_day - timedelta(days=13)  # last Monday..Sunday
    board = service.leaderboard(db, "week", limit=5, start=start, end=start + timedelta(days=6))
    if not board["rows"]:
        return []
    return ["🏆 Last week: " + " · ".join(f"{r['position']}. {e(r['name'])} {r['points']}" for r in board["rows"])]


def vote_buttons(q: PredQuestion) -> list[list[dict[str, str]]]:
    opts = [{"text": ("▲ " if o["key"] == "up" else "▼ " if o["key"] == "down" else "") + str(o["label"]),
             "callback_data": f"{PREFIX}v:{q.id}:{o['key']}"} for o in q.options]
    rows = [opts] if len(opts) <= 2 else [opts[:2], opts[2:]]
    return rows + info_buttons()


def info_buttons() -> list[list[dict[str, str]]]:
    return [[{"text": "📊 My stats", "callback_data": f"{PREFIX}s"}, {"text": "ℹ️ Rules", "callback_data": f"{PREFIX}r"}]]


# --- posting ----------------------------------------------------------------------------------------------------------------------


async def announce(db: Session, client: Any, settings: Any, q: PredQuestion, now: datetime) -> bool:
    chat = group_chat(settings)
    if not chat or q.tg_message_id:
        return False
    sent = await client.send_photo(chat, card_png(q), caption(db, q, now), buttons=vote_buttons(q), html=True)
    q.tg_chat, q.tg_message_id, q.tg_shown, q.tg_edited_at = str(chat), int(sent.get("message_id") or 0) or None, 0, now
    db.commit()
    return True


async def refresh(db: Session, client: Any, q: PredQuestion, now: datetime, force: bool = False) -> bool:
    """Edit the group post to its current state. While open: only when the count changed, at most once a minute."""
    if not q.tg_message_id or not q.tg_chat:
        return False
    total = sum(service.tallies(db, q).values())
    if q.status == "open" and not force:
        last = service._aware(q.tg_edited_at)
        if total == q.tg_shown or (last is not None and (now - last).total_seconds() < rules.TALLY_EDIT_SECONDS):
            return False
    buttons = vote_buttons(q) if q.status == "open" else info_buttons()
    try:
        await client.edit_caption(q.tg_chat, int(q.tg_message_id), caption(db, q, now), buttons=buttons, html=True)
    except Exception as exc:  # noqa: BLE001 - "message is not modified" and friends: the next state change tries again
        log.info("predictions: post edit skipped (%s)", type(exc).__name__)
    q.tg_shown, q.tg_edited_at = total, now
    db.commit()
    return True


async def remind(db: Session, client: Any, q: PredQuestion, now: datetime) -> int:
    """Opt-in private reminders an hour before the lock, to those who haven't called it yet. Once per call."""
    if q.reminded_at is not None or q.status != "open" or now < service._aware(q.locks_at) - timedelta(minutes=rules.REMIND_BEFORE_LOCK_MIN):  # type: ignore[operator]
        return 0
    q.reminded_at = now
    db.commit()
    voted = {v.tg_user_id for v in db.scalars(select(PredVote).where(PredVote.question_id == q.id, PredVote.tg_user_id.is_not(None)))}
    voted_members = {v.member_id for v in db.scalars(select(PredVote).where(PredVote.question_id == q.id, PredVote.member_id.is_not(None)))}
    n = 0
    for tu in db.scalars(select(PredTgUser).where(PredTgUser.reminders.is_(True), PredTgUser.dm_ok.is_(True), PredTgUser.is_bot.is_(False))):
        if tu.tg_user_id in voted:
            continue
        m = service.member_for_tg(db, tu.tg_user_id)
        if m is not None and m.id in voted_members:
            continue
        try:
            await client.send_message(tu.tg_user_id, f"🔔 {e(q.title)} locks at {lock_label(q)}: about an hour left, and you haven't called it yet.",
                                      buttons=vote_buttons(q)[:-1] + [[{"text": "🔕 Stop reminders", "callback_data": f"{PREFIX}rem:off"}]], html=True)
            n += 1
        except Exception as exc:  # noqa: BLE001 - a blocked bot etc.: stop writing to them
            log.info("predictions: reminder not sent (%s)", type(exc).__name__)
            tu.dm_ok = False
        await asyncio.sleep(0.05)
    db.commit()
    return n


async def personal_results(db: Session, client: Any, q: PredQuestion) -> int:
    """Opt-in private result messages after a call settles."""
    n = 0
    for v in db.scalars(select(PredVote).where(PredVote.question_id == q.id, PredVote.status == "counted", PredVote.tg_user_id.is_not(None))):
        tu = db.scalar(select(PredTgUser).where(PredTgUser.tg_user_id == v.tg_user_id, PredTgUser.results_dm.is_(True), PredTgUser.dm_ok.is_(True)))
        if tu is None:
            continue
        mark = "🤝" if q.outcome == "tie" else ("✅ Right" if v.option == q.outcome else "❌ Not this time")
        try:
            await client.send_message(tu.tg_user_id, f"{mark}: you called <b>{e(service.label_of(q, v.option))}</b>. {result_line(db, q)}", html=True)
            n += 1
        except Exception:  # noqa: BLE001
            tu.dm_ok = False
        await asyncio.sleep(0.05)
    db.commit()
    return n


# --- buttons -------------------------------------------------------------------------------------------------------------------------


def stats_text(st: dict[str, Any]) -> str:
    acc = f"{st['accuracy']:.0%}" if st["accuracy"] is not None else f"after {st['accuracy_after']} calls"
    pos = f"#{st['week_position']} this week" if st["week_position"] else "not on this week's board yet"
    xp = f"{st['xp']} XP" + (f" · {st['pending_xp']} pending" if st["pending_xp"] else "")
    return f"{st['level']['name']} · {st['calls']} calls, {st['right']} right ({acc}) · streak {st['streak']} · {pos} · {xp}"


async def handle_callback(db: Session, client: Any, settings: Any, cq: dict[str, Any], now: datetime | None = None) -> bool:
    """A "pd:" button. True when it was ours (handled, or refused with a pop-up)."""
    data = str(cq.get("data") or "")
    if not data.startswith(PREFIX):
        return False
    now = now or utcnow()
    frm = cq.get("from") or {}
    uid = int(frm.get("id") or 0)
    if mode(settings) == "off":
        await client.answer_callback(cq["id"], "Predictions are paused right now.")
        return True
    if not uid or frm.get("is_bot"):
        await client.answer_callback(cq["id"], "Bots can't take part.")
        return True
    if tapped_too_fast(uid):
        await client.answer_callback(cq["id"], "One moment: too many taps in a row.")
        return True
    private = ((cq.get("message") or {}).get("chat") or {}).get("type") == "private"
    tu = touch_user(db, frm, private=private)
    db.commit()
    member = service.member_for_tg(db, uid)
    voter = service.Voter(member=member, tg_user_id=None if member else uid)
    parts = data[len(PREFIX):].split(":")
    if parts[0] == "v" and len(parts) == 3:
        q = db.get(PredQuestion, int(parts[1])) if parts[1].isdigit() else None
        if q is None:
            await client.answer_callback(cq["id"], "That call no longer exists.")
            return True
        if mode(settings) == "live" and not await _in_group(db, client, settings, tu, now):
            await client.answer_callback(cq["id"], f"Join {settings.predictions_group} to make calls.", alert=True)
            return True
        if member is not None and member.status != "active":
            await client.answer_callback(cq["id"], "This account can't take part.")
            return True
        if member is not None:
            voter.tg_user_id = uid
        try:
            res = service.vote(db, q, voter, parts[2], "telegram", now)
        except service.PredictionError as err:
            await client.answer_callback(cq["id"], str(err))
            return True
        xp = rules.XP[f"predictions.{q.kind}_play"]
        verb = "changed to" if res["changed"] else ("is" if not res["first"] else "is")
        tail = (f"+{xp} XP when it settles." if member is not None else
                f"+{xp} XP when it settles, pending until you link a Passport (tap My stats).")
        await client.answer_callback(cq["id"], f"Your call {verb} {res['label']}. You can change it until {lock_label(q)}. {tail}"[:200])
        return True
    if parts[0] == "s":
        st = service.stats(db, voter, now)
        text = stats_text(st)
        if member is None:
            text += " · Link a Passport at pricechat.live to keep your XP."
        await client.answer_callback(cq["id"], text[:200], alert=True)
        return True
    if parts[0] == "r":
        await client.answer_callback(cq["id"], RULES_TEXT[:200], alert=True)
        return True
    if parts[0] in ("rem", "res") and len(parts) == 2:
        on = parts[1] == "on"
        if parts[0] == "rem":
            tu.reminders = on
        else:
            tu.results_dm = on
        db.commit()
        what = "Reminders" if parts[0] == "rem" else "Private results"
        await client.answer_callback(cq["id"], f"{what} {'on' if on else 'off'}.")
        return True
    await client.answer_callback(cq["id"], "That button has expired.")
    return True


async def _in_group(db: Session, client: Any, settings: Any, tu: PredTgUser, now: datetime) -> bool:
    checked = service._aware(tu.group_checked_at)
    if tu.in_group is not None and checked is not None and now - checked < timedelta(hours=rules.GROUP_CHECK_HOURS):
        return bool(tu.in_group)
    try:
        m = await client.get_chat_member(settings.predictions_group, tu.tg_user_id)
        tu.in_group = str(m.get("status")) in ("member", "administrator", "creator", "restricted")
    except Exception:  # noqa: BLE001 - Telegram unreachable: let the vote through rather than block everyone
        return True
    tu.group_checked_at = now
    db.commit()
    return bool(tu.in_group)


# --- commands ---------------------------------------------------------------------------------------------------------------------


def is_ours(text: str) -> bool:
    word = text.strip().split()[0].split("@")[0].lower() if text.strip() else ""
    return word in COMMANDS or (word == "/start" and len(text.split()) > 1 and text.split()[1].startswith("pd"))


async def handle_command(db: Session, client: Any, settings: Any, chat_id: int, user_id: int, text: str, group: bool,
                         reply_to: int | None = None, author: str | None = None, now: datetime | None = None) -> bool:
    """/predict /rank /stats /leaderboard /remind (and /start pd...). True when it was ours."""
    if not is_ours(text):
        return False
    now = now or utcnow()
    if mode(settings) == "off":
        return False
    word = text.strip().split()[0].split("@")[0].lower()
    tu = touch_user(db, {"id": user_id, "first_name": author or ""}, private=not group)
    db.commit()
    member = service.member_for_tg(db, user_id)
    voter = service.Voter(member=member, tg_user_id=None if member else user_id)
    site = settings.predictions_site_url.rstrip("/")
    buttons: list[list[dict[str, str]]] | None = None
    if word in ("/predict", "/start"):
        open_qs = service.current(db, now)
        if not open_qs:
            body = "No call is open right now. The daily call opens at 13:30 UTC."
        elif group:
            body = "Today's call is the latest Predictions post in this group: tap Higher or Lower on it. /stats for your record."
        else:
            q = open_qs[0]
            mine = service.my_vote(db, q, voter)
            body = f"<b>{e(q.title)}</b>\nReference {service.money(q.ref_price or 0)} · locks {lock_label(q)}" + (
                f"\nYour call: <b>{e(service.label_of(q, mine.option))}</b> (tap to change)" if mine else "")
            buttons = vote_buttons(q)
    elif word in ("/stats", "/rank"):
        st = service.stats(db, voter, now)
        lvl = st["level"]
        nxt = f" · {lvl['next_at'] - lvl['xp']} XP to {lvl['next_name']}" if lvl["next_at"] else ""
        if word == "/rank":
            body = f"🏅 {e(author or 'You')}: <b>{lvl['name']}</b> (Predictor level {lvl['level']}){nxt}\n" + (
                f"#{st['week_position']} of {st['week_players']} this week" if st["week_position"] else
                f"Not on this week's board yet ({rules.MIN_CALLS['week']} calls needed)")
        else:
            acc = f"{st['accuracy']:.0%}" if st["accuracy"] is not None else f"shown after {st['accuracy_after']} calls"
            body = (f"📊 <b>{e(author or 'Your')} stats</b>\nCalls: {st['calls']} · right {st['right']} ({acc})\n"
                    f"Streak: {st['streak']} · best {st['best_streak']}\nPredictor: {lvl['name']}{nxt}\nXP: {st['xp']}"
                    + (f" · pending {st['pending_xp']} (link a Passport to keep it)" if st["pending_xp"] else ""))
        if not group:
            buttons = [[{"text": "🔗 Link my Passport" if member is None else "🪪 My Passport", "url": f"{site}/passport"}],
                       [{"text": f"🔔 Reminders: {'on' if tu.reminders else 'off'}", "callback_data": f"{PREFIX}rem:{'off' if tu.reminders else 'on'}"},
                        {"text": f"📬 Results: {'on' if tu.results_dm else 'off'}", "callback_data": f"{PREFIX}res:{'off' if tu.results_dm else 'on'}"}]]
    elif word == "/leaderboard":
        board = service.leaderboard(db, "week", now, limit=10)
        rows = "\n".join(f"{r['position']}. {e(r['name'])} · {r['points']} pts · {r['right']}/{r['decided']}" for r in board["rows"]) or \
            f"Nobody has {board['min_calls']} settled calls this week yet."
        body = f"🏆 <b>This week</b> (Mon–Sun UTC, {board['min_calls']}+ calls)\n{rows}\nMonth and all time: {site}/passport"
    else:  # /remind on|off
        arg = (text.split()[1].lower() if len(text.split()) > 1 else "")
        if group:
            body = "Reminders are private: message me directly and send /remind on."
        else:
            tu.reminders = arg != "off"
            db.commit()
            body = f"🔔 Reminders {'on' if tu.reminders else 'off'}: an hour before a call locks, if you haven't called it."
    sent = await client.send_message(chat_id, body, buttons=buttons, html=True, reply_to=reply_to if group else None, silent=group)
    if group and sent.get("message_id"):
        asyncio.get_running_loop().call_later(rules.GROUP_REPLY_TTL, lambda: asyncio.ensure_future(_delete(client, chat_id, int(sent["message_id"]))))
    return True


async def _delete(client: Any, chat_id: int, message_id: int) -> None:
    try:
        await client.delete_message(chat_id, message_id)
    except Exception:  # noqa: BLE001 - already gone
        pass
