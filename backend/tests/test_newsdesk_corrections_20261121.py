"""Corrections and retractions of News Desk posts from Admin (owner, 2026-11-21, after Telegram post 204 claimed $10.1B of Binance liquidations;
the real figure was about $17-18M). Preview sends nothing; only a fresh, single-use token does it; every attempt is audited and the original
text is kept. Telegram is a fake, X a mocked API: nothing reaches a real service."""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.newsdesk import NdEvent, NdPost, NdTelegramPost, NdValidation
from app.newsdesk import corrections, engine
from app.services.agent_service import get_agent_config
from app.services.safety import get_or_create_policy
from app.telegram.errors import TelegramRequestError
from tests.conftest import connect_x_user

CHANNEL_ID = -100123
POST_204 = "JUST IN: BTCUSD_PERP longs liquidated: $10.1B on Binance in an hour.\n\nData: public exchange liquidation feeds.\n\n@thepricechat"
FIXED_204 = ("CORRECTION: BTCUSD_PERP longs liquidated: about $17.6M on Binance in an hour, not $10.1B as first posted (a unit error).\n\n"
             "Data: public exchange liquidation feeds.\n\n@thepricechat")
REPLY_204 = "Correction: the figure above was wrong. About $17.6M of BTCUSD_PERP longs were liquidated on Binance in that hour, not $10.1B."


class Tg:
    """Answers like Telegram, and remembers every call. refuse: method -> the error Telegram gives."""

    def __init__(self, refuse: dict[str, Exception] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.refuse = refuse or {}
        self.next_id = 300

    def _maybe(self, method: str) -> None:
        if method in self.refuse:
            raise self.refuse[method]

    async def send_message(self, chat: Any, text: str, **kw: Any) -> dict[str, Any]:
        self.calls.append({"method": "send_message", "chat": chat, "text": text, **kw})
        self._maybe("send_message")
        self.next_id += 1
        return {"message_id": self.next_id, "chat": {"id": CHANNEL_ID, "type": "channel"}}

    async def edit_message_text(self, chat: Any, message_id: int, text: str, **kw: Any) -> dict[str, Any]:
        self.calls.append({"method": "edit_message_text", "chat": chat, "message_id": message_id, "text": text, **kw})
        self._maybe("edit_message_text")
        return {"message_id": message_id}

    async def edit_caption(self, chat: Any, message_id: int, caption: str, **kw: Any) -> None:
        self.calls.append({"method": "edit_caption", "chat": chat, "message_id": message_id, "text": caption, **kw})
        self._maybe("edit_caption")

    async def delete_message(self, chat: Any, message_id: int) -> None:
        self.calls.append({"method": "delete_message", "chat": chat, "message_id": message_id})
        self._maybe("delete_message")

    async def aclose(self) -> None:
        pass

    @property
    def methods(self) -> list[str]:
        return [c["method"] for c in self.calls]


class X:
    """A mocked X API v2: post create and delete."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []
        self.delete_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content))
        if request.method == "POST" and request.url.path == "/2/tweets":
            return httpx.Response(201, json={"data": {"id": "1990000000000000777", "text": "t"}})
        if request.method == "DELETE" and request.url.path.startswith("/2/tweets/"):
            if self.delete_status != 200:
                return httpx.Response(self.delete_status, json={"title": "Forbidden", "detail": "You are not allowed to delete this Tweet.", "status": 403})
            return httpx.Response(200, json={"data": {"deleted": True}})
        return httpx.Response(404, json={"title": "Not Found"})

    @property
    def runtime(self) -> Any:
        return type("Runtime", (), {"transport": httpx.MockTransport(self.handler)})()


def tg_event(db: Session, message_id: int = 204, text: str = POST_204, photo: bool = False, n: int = 1) -> NdEvent:
    now = utcnow()
    row = NdTelegramPost(story_link=f"event:liq{n}", title="BTC liquidations", headline="BTCUSD_PERP longs liquidated", text=text, source="Price Chat",
                         sources=[], status="delivered", chat="@thepricechat", chat_id=CHANNEL_ID, message_id=message_id, delivered_at=now)
    db.add(row)
    db.flush()
    ev = NdEvent(key=f"liq:BTC:{n}", kind="market", topic="liquidations", title="BTCUSD_PERP longs liquidated", headline="BTCUSD_PERP longs liquidated",
                 category="crypto", status="released", tg_status="delivered", tg_post_id=row.id, tg_message_id=message_id, released_at=now)
    db.add(ev)
    if photo:
        db.add(NdValidation(kind="image", ref=f"telegram:{message_id}", data={}))
    db.commit()
    return ev


def x_event(db: Session, x_id: str | None = "1990000000000000001", n: int = 1) -> NdEvent:
    post = NdPost(kind="news", category="crypto", status="published" if x_id else "dry_run", text="EXCHANGE BYBAZ HALTS WITHDRAWALS\n\nSource: CoinDesk",
                  key=f"k{n}", x_id=x_id, published_at=utcnow())
    db.add(post)
    db.flush()
    ev = NdEvent(key=f"story:https://coindesk.test/{n}", kind="news", title="Bybaz halts withdrawals", headline="Bybaz halts withdrawals", category="crypto",
                 status="released", x_status="published", x_post_id=post.id, tg_status="delivered")
    db.add(ev)
    db.commit()
    return ev


def x_mode(db: Session, mode: str) -> None:
    s = engine.settings_row(db)
    s.mode = mode
    db.commit()


def go_live(db: Session) -> None:
    connect_x_user(db)
    policy = get_or_create_policy(db, get_agent_config(db).id)
    policy.outbound_enabled, policy.kill_switch = True, False
    x_mode(db, "live")


async def confirm(db: Session, ev: NdEvent, action: str, text: str | None = None, **kw: Any) -> dict[str, Any]:
    p = await corrections.preview(db, ev, action, text)
    assert p["confirm_token"], p["problems"]
    return await corrections.execute(db, ev, action, text, p["confirm_token"], settings=get_settings(), **kw)


def audits(db: Session) -> list[dict[str, Any]]:
    db.expire_all()
    return corrections.audit_list(db)


# --- preview ------------------------------------------------------------------------------------------------------


async def test_preview_says_exactly_what_would_happen_and_sends_nothing(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_client(_: Any) -> Any:
        raise AssertionError("preview must not make a Telegram client")

    monkeypatch.setattr(corrections, "_tg_client", no_client)
    ev = tg_event(db)
    p = await corrections.preview(db, ev, "tg_edit", FIXED_204)
    assert p["platform"] == "telegram" and p["target"]["message_id"] == 204 and p["target"]["chat_id"] == CHANNEL_ID and p["chat"] == "@thepricechat"
    assert p["current_text"] == POST_204 and p["new_text"] == FIXED_204 and p["chars"] == len(FIXED_204) and p["limit"] == 4096
    assert p["format"] == "text" and p["problems"] == [] and p["confirm_token"] and p["expires_at"]
    assert p["emergency_stop"] is False
    assert audits(db) == []  # a preview is not an attempt


async def test_preview_problems_give_no_token(db: Session) -> None:
    ev = NdEvent(key="story:x", kind="news", title="t", headline="t", category="crypto")
    db.add(ev)
    db.commit()
    p = await corrections.preview(db, ev, "tg_edit", "fix")
    assert p["problems"] == ["This event has no Telegram message to correct"] and p["confirm_token"] is None
    p = await corrections.preview(db, ev, "x_delete", None)
    assert "This event has no X post id" in p["problems"][0] and p["confirm_token"] is None
    ev2 = tg_event(db, 205, photo=True, n=2)
    p = await corrections.preview(db, ev2, "tg_edit", "x" * 1025)
    assert p["format"] == "photo" and p["limit"] == 1024 and "Too long for a photo caption: 1025 characters" in p["problems"][0] and p["confirm_token"] is None
    p = await corrections.preview(db, ev2, "tg_reply", "   ")
    assert p["problems"] == ["Write the correction text first"]
    ev3 = x_event(db, n=3)
    p = await corrections.preview(db, ev3, "x_reply", "y" * 281)
    assert "Too long for X: 281 characters" in p["problems"][0]


# --- the token ----------------------------------------------------------------------------------------------------


async def test_a_wrong_token_is_refused_and_audited(db: Session) -> None:
    ev, tg = tg_event(db), Tg()
    await corrections.preview(db, ev, "tg_edit", FIXED_204)
    with pytest.raises(corrections.CorrectionError, match="not valid") as e:
        await corrections.execute(db, ev, "tg_edit", FIXED_204, "made-up", tg=tg)
    assert e.value.status == 409 and tg.calls == []
    [row] = audits(db)
    assert row["outcome"] == "refused" and "not valid" in row["error"] and row["old_text"] == POST_204


async def test_an_expired_token_is_refused_and_audited(db: Session) -> None:
    ev, tg = tg_event(db), Tg()
    p = await corrections.preview(db, ev, "tg_delete", None, now=utcnow() - timedelta(minutes=11))
    with pytest.raises(corrections.CorrectionError, match="expired"):
        await corrections.execute(db, ev, "tg_delete", None, p["confirm_token"], tg=tg)
    assert tg.calls == [] and audits(db)[0]["outcome"] == "refused"


async def test_a_token_works_once(db: Session) -> None:
    ev, tg = tg_event(db), Tg()
    p = await corrections.preview(db, ev, "tg_reply", REPLY_204)
    await corrections.execute(db, ev, "tg_reply", REPLY_204, p["confirm_token"], tg=tg)
    with pytest.raises(corrections.CorrectionError, match="already used"):
        await corrections.execute(db, ev, "tg_reply", REPLY_204, p["confirm_token"], tg=tg)
    assert tg.methods == ["send_message"]
    assert [a["outcome"] for a in audits(db)] == ["refused", "done"]


async def test_a_token_is_bound_to_its_text_and_action(db: Session) -> None:
    ev, tg = tg_event(db), Tg()
    p = await corrections.preview(db, ev, "tg_edit", FIXED_204)
    with pytest.raises(corrections.CorrectionError, match="different correction"):
        await corrections.execute(db, ev, "tg_edit", FIXED_204 + " (and something else)", p["confirm_token"], tg=tg)
    p = await corrections.preview(db, ev, "tg_edit", FIXED_204)
    with pytest.raises(corrections.CorrectionError, match="different correction"):
        await corrections.execute(db, ev, "tg_delete", None, p["confirm_token"], tg=tg)
    assert tg.calls == [] and [a["outcome"] for a in audits(db)] == ["refused", "refused"]


# --- Telegram -----------------------------------------------------------------------------------------------------


async def test_edit_a_text_post_keeps_the_original_in_the_audit(db: Session) -> None:
    ev, tg = tg_event(db), Tg()
    out = await confirm(db, ev, "tg_edit", FIXED_204, tg=tg)
    assert tg.calls == [{"method": "edit_message_text", "chat": CHANNEL_ID, "message_id": 204, "text": FIXED_204}]
    assert out["outcome"] == "done" and out["old_text"] == POST_204 and out["new_text"] == FIXED_204 and out["actor"] == "owner"
    db.expire_all()
    row = db.get(NdTelegramPost, ev.tg_post_id)
    assert row.text == FIXED_204 and row.status == "corrected" and row.message_id == 204
    assert "corrected by the owner" in db.get(NdEvent, ev.id).tg_note


async def test_edit_a_photo_post_edits_its_caption(db: Session) -> None:
    ev, tg = tg_event(db, 205, photo=True), Tg()
    await confirm(db, ev, "tg_edit", "Corrected caption", tg=tg)
    assert tg.methods == ["edit_caption"] and tg.calls[0]["text"] == "Corrected caption" and tg.calls[0]["message_id"] == 205


async def test_a_text_post_thought_to_be_a_photo_falls_back_to_a_text_edit(db: Session) -> None:
    ev = tg_event(db, 206, photo=True)
    tg = Tg({"edit_caption": TelegramRequestError("Telegram rejected editMessageCaption: Bad Request: there is no caption in the message to edit")})
    await confirm(db, ev, "tg_edit", "Corrected", tg=tg)
    assert tg.methods == ["edit_caption", "edit_message_text"]


async def test_reply_carries_the_message_it_answers(db: Session) -> None:
    ev, tg = tg_event(db), Tg()
    out = await confirm(db, ev, "tg_reply", REPLY_204, tg=tg)
    assert tg.calls[0]["method"] == "send_message" and tg.calls[0]["reply_to"] == 204 and tg.calls[0]["chat"] == CHANNEL_ID
    assert out["result"]["reply_message_id"] == 301
    db.expire_all()
    assert db.get(NdTelegramPost, ev.tg_post_id).text == POST_204  # a reply leaves the post itself as it was


async def test_the_real_client_sends_the_reply_target_to_telegram() -> None:
    from app.telegram.client import TelegramBotClient

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7, "chat": {"id": CHANNEL_ID}}})

    client = TelegramBotClient("123:abc", "https://api.telegram.test", transport=httpx.MockTransport(handler))
    await client.send_message(CHANNEL_ID, REPLY_204, reply_to=204)
    await client.aclose()
    assert seen[0]["reply_parameters"]["message_id"] == 204


async def test_delete_marks_the_records_deleted(db: Session) -> None:
    ev, tg = tg_event(db), Tg()
    out = await confirm(db, ev, "tg_delete", None, tg=tg)
    assert tg.calls == [{"method": "delete_message", "chat": CHANNEL_ID, "message_id": 204}]
    assert out["outcome"] == "done" and out["old_text"] == POST_204 and out["new_text"] is None
    db.expire_all()
    assert db.get(NdTelegramPost, ev.tg_post_id).status == "deleted" and db.get(NdTelegramPost, ev.tg_post_id).text == POST_204
    ev = db.get(NdEvent, ev.id)
    assert ev.tg_status == "deleted" and "deleted by the owner" in ev.tg_note
    p = await corrections.preview(db, ev, "tg_edit", "again")
    assert "already deleted" in p["problems"][0]


async def test_telegram_s_refusal_is_passed_on_without_the_token(db: Session) -> None:
    ev = tg_event(db)
    tg = Tg({"delete_message": TelegramRequestError("Telegram rejected deleteMessage: Bad Request: message can't be deleted (bot123456:AAEsecretsecretsecretsecretsecret00)")})
    with pytest.raises(corrections.CorrectionError, match="message can't be deleted") as e:
        await confirm(db, ev, "tg_delete", None, tg=tg)
    assert e.value.status == 502 and "AAEsecret" not in str(e.value)
    [row] = audits(db)
    assert row["outcome"] == "failed" and "message can't be deleted" in row["error"] and "AAEsecret" not in row["error"]
    db.expire_all()
    assert db.get(NdTelegramPost, ev.tg_post_id).status == "delivered"  # nothing changed: Telegram said no


# --- X ------------------------------------------------------------------------------------------------------------


async def test_x_delete_and_reply_in_dry_run_are_recorded_and_never_called(db: Session) -> None:
    x = X()
    ev = x_event(db)
    x_mode(db, "dry_run")
    out = await confirm(db, ev, "x_delete", None, x_runtime=x.runtime)
    assert out["outcome"] == "dry_run" and out["dry_run"] is True and out["target"]["tweet_id"] == "1990000000000000001"
    out = await confirm(db, ev, "x_reply", "Correction: the figure was wrong.", x_runtime=x.runtime)
    assert out["outcome"] == "dry_run" and out["new_text"] == "Correction: the figure was wrong."
    assert x.calls == []
    db.expire_all()
    assert db.get(NdPost, ev.x_post_id).status == "published"


async def test_x_reply_live_goes_under_the_post(db: Session) -> None:
    x = X()
    ev = x_event(db)
    go_live(db)
    out = await confirm(db, ev, "x_reply", "Correction: the figure was wrong.", x_runtime=x.runtime)
    assert out["outcome"] == "done" and out["dry_run"] is False and out["result"]["reply_tweet_id"] == "1990000000000000777"
    [(method, path, body)] = x.calls
    assert method == "POST" and path == "/2/tweets" and b'"in_reply_to_tweet_id":"1990000000000000001"' in body.replace(b" ", b"")


async def test_x_delete_live_deletes_the_post(db: Session) -> None:
    x = X()
    ev = x_event(db)
    go_live(db)
    out = await confirm(db, ev, "x_delete", None, x_runtime=x.runtime)
    assert out["outcome"] == "done" and x.calls[0][:2] == ("DELETE", "/2/tweets/1990000000000000001")
    db.expire_all()
    assert db.get(NdPost, ev.x_post_id).status == "deleted" and db.get(NdEvent, ev.id).x_status == "deleted"
    assert db.get(NdPost, ev.x_post_id).text.startswith("EXCHANGE BYBAZ")


async def test_x_refusing_a_delete_is_audited(db: Session) -> None:
    x = X()
    x.delete_status = 403
    ev = x_event(db)
    go_live(db)
    with pytest.raises(corrections.CorrectionError, match="X refused it"):
        await confirm(db, ev, "x_delete", None, x_runtime=x.runtime)
    assert audits(db)[0]["outcome"] == "failed"
    db.expire_all()
    assert db.get(NdPost, ev.x_post_id).status == "published"


async def test_x_without_a_tweet_id_is_refused(db: Session) -> None:
    ev = x_event(db, x_id=None)
    p = await corrections.preview(db, ev, "x_delete", None)
    assert p["confirm_token"] is None and "no X post id" in p["problems"][0]


# --- the emergency stop -------------------------------------------------------------------------------------------


async def test_the_emergency_stop_still_allows_a_correction_and_says_it_was_on(db: Session) -> None:
    s = engine.settings_row(db)
    s.kill_switch = True
    db.commit()
    ev, tg = tg_event(db), Tg()
    p = await corrections.preview(db, ev, "tg_edit", FIXED_204)
    assert p["emergency_stop"] is True and p["stop_reason"] == "Emergency stop is on" and p["confirm_token"]
    out = await corrections.execute(db, ev, "tg_edit", FIXED_204, p["confirm_token"], tg=tg)
    assert out["outcome"] == "done" and out["emergency_stop"] is True and out["stop_reason"] == "Emergency stop is on"
    assert tg.methods == ["edit_message_text"]


# --- the audit list and post 204 ----------------------------------------------------------------------------------


async def test_post_204_corrected_in_place_then_with_a_reply(db: Session) -> None:
    ev, tg = tg_event(db), Tg()
    other = tg_event(db, 210, n=2)
    p = await corrections.preview(db, ev, "tg_edit", FIXED_204)
    assert p["current_text"] == POST_204 and p["problems"] == []
    await corrections.execute(db, ev, "tg_edit", FIXED_204, p["confirm_token"], tg=tg)
    await confirm(db, ev, "tg_reply", REPLY_204, tg=tg)
    await confirm(db, other, "tg_delete", None, tg=tg)
    assert tg.methods == ["edit_message_text", "send_message", "delete_message"]
    assert tg.calls[0]["message_id"] == 204 and tg.calls[1]["reply_to"] == 204 and "$17.6M" in tg.calls[0]["text"]

    every = audits(db)
    assert [(a["event"], a["action"]) for a in every] == [(other.id, "tg_delete"), (ev.id, "tg_reply"), (ev.id, "tg_edit")]  # newest first
    mine = corrections.audit_list(db, ev.id)
    assert [a["action"] for a in mine] == ["tg_reply", "tg_edit"]
    edit = mine[1]
    assert edit["old_text"] == POST_204 and "$10.1B on Binance" in edit["old_text"] and edit["new_text"] == FIXED_204
    assert edit["target"]["message_id"] == 204 and edit["platform"] == "telegram" and edit["emergency_stop"] is False and edit["at"]
    assert mine[0]["old_text"] == FIXED_204  # the reply's row shows what the post said by then
    # the first wording survives in the log, whatever the post says now
    db.expire_all()
    assert db.get(NdTelegramPost, ev.tg_post_id).text == FIXED_204
    assert any(a["old_text"] == POST_204 for a in corrections.audit_list(db, ev.id))


# --- the Admin API ------------------------------------------------------------------------------------------------


def test_the_admin_api_previews_confirms_and_lists(client: Any, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    tg = Tg()
    monkeypatch.setattr(corrections, "_tg_client", lambda _settings: tg)
    ev = tg_event(db)
    body = {"action": "tg_edit", "text": FIXED_204}
    r = client.post(f"/api/x/newsdesk/events/{ev.id}/corrections/preview", json=body)
    assert r.status_code == 200, r.text
    token = r.json()["confirm_token"]
    assert tg.calls == []
    r = client.post(f"/api/x/newsdesk/events/{ev.id}/corrections", json={**body, "confirm_token": token})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "done"
    assert tg.methods == ["edit_message_text"]
    r = client.post(f"/api/x/newsdesk/events/{ev.id}/corrections", json={**body, "confirm_token": token})
    assert r.status_code == 409 and "already used" in r.json()["detail"]
    r = client.post(f"/api/x/newsdesk/events/{ev.id}/corrections/preview", json={"action": "tg_burn", "text": None})
    assert r.status_code == 422
    assert client.post("/api/x/newsdesk/events/99999/corrections/preview", json={"action": "tg_delete"}).status_code == 404
    log = client.get("/api/x/newsdesk/corrections").json()
    assert [a["outcome"] for a in log] == ["refused", "done"]
    assert len(client.get(f"/api/x/newsdesk/events/{ev.id}/corrections").json()) == 2
    no_tg = NdEvent(key="story:none", kind="news", title="t", headline="t", category="crypto")
    db.add(no_tg)
    db.commit()
    r = client.post(f"/api/x/newsdesk/events/{no_tg.id}/corrections/preview", json={"action": "tg_delete"})
    assert r.json()["problems"] == ["This event has no Telegram message to correct"]
    r = client.post(f"/api/x/newsdesk/events/{no_tg.id}/corrections", json={"action": "tg_delete", "confirm_token": "x"})
    assert r.status_code == 409
    assert db.scalars(select(NdValidation).where(NdValidation.kind == "correction")).all()
