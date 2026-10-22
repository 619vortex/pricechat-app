import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.activity import ActivityLog
from app.models.telegram import TelegramBotState, TelegramMessage, TelegramSource
from app.telegram.client import TelegramBotClient
from app.telegram.errors import (
    MalformedUpdateError,
    TelegramAuthError,
    TelegramConflictError,
    TelegramRateLimitError,
    TelegramUnavailableError,
)
from app.telegram.ingestion import TelegramIngestor
from app.telegram.parser import MembershipChange, ParsedMessage, parse_update
from app.telegram.state import TelegramRuntime
from tests.fakes import TOKEN, FakeTelegram

CHAT = {"id": -100111, "title": "Price Chat Community", "type": "supergroup"}


def _client(tg: FakeTelegram, token: str = TOKEN) -> TelegramBotClient:
    return TelegramBotClient(token, "https://api.telegram.test", transport=tg.transport)


def _source(db: Session, chat_id: int = -100111, enabled: bool = True, ingestion: bool = True) -> TelegramSource:
    source = TelegramSource(chat_id=chat_id, display_name="Community", enabled=enabled, ingestion_enabled=ingestion)
    db.add(source)
    db.commit()
    return source


def _count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(TelegramMessage)) or 0)


# --- parser ------------------------------------------------------------------------------


def test_parse_text_message_with_reply_and_forward() -> None:
    parsed = parse_update({
        "update_id": 5,
        "message": {
            "message_id": 7, "date": 1_790_000_000, "chat": CHAT,
            "from": {"id": 42, "is_bot": False, "first_name": "Ana", "last_name": "B", "username": "ana", "phone_number": "+1"},
            "text": "What does funding rate mean?",
            "reply_to_message": {"message_id": 3},
            "forward_origin": {"type": "channel", "chat": {"id": -5, "title": "News"}, "date": 1_789_999_000},
            "message_thread_id": 9, "is_topic_message": True,
        },
    })
    assert isinstance(parsed, ParsedMessage)
    assert (parsed.chat.chat_id, parsed.message_id, parsed.thread_id, parsed.reply_to_message_id) == (-100111, 7, 9, 3)
    assert (parsed.author_id, parsed.author_name) == (42, "Ana B")
    assert parsed.forward_from == "News" and parsed.forward_date is not None
    assert parsed.sent_at.tzinfo is not None
    assert not hasattr(parsed, "phone_number")  # data minimisation


def test_parse_media_caption_edit_and_service_messages() -> None:
    photo = parse_update({"update_id": 1, "message": {"message_id": 1, "date": 1, "chat": CHAT, "photo": [{"file_id": "x"}], "caption": "chart"}})
    assert isinstance(photo, ParsedMessage) and photo.media_type == "photo" and photo.text == "chart"
    edited = parse_update({"update_id": 2, "edited_message": {"message_id": 1, "date": 1, "edit_date": 5, "chat": CHAT, "text": "fixed"}})
    assert isinstance(edited, ParsedMessage) and edited.edited and edited.edited_at is not None
    assert parse_update({"update_id": 3, "message": {"message_id": 2, "date": 1, "chat": CHAT, "new_chat_members": [{}]}}) is None
    assert parse_update({"update_id": 4, "callback_query": {}}) is None
    member = parse_update({"update_id": 5, "my_chat_member": {"chat": CHAT, "new_chat_member": {"status": "member"}}})
    assert isinstance(member, MembershipChange) and member.status == "member"


@pytest.mark.parametrize("bad", [{}, {"update_id": "x"}, {"update_id": 1, "message": {"date": 1, "chat": CHAT}}, {"update_id": 1, "message": {"message_id": 1, "date": 1, "chat": {}, "text": "hi"}}])
def test_malformed_updates_raise(bad: dict) -> None:
    with pytest.raises(MalformedUpdateError):
        parse_update(bad)


# --- client errors ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"ok": False, "error_code": 401, "description": "Unauthorized"}, TelegramAuthError),
        (409, {"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates"}, TelegramConflictError),
        (429, {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 17}}, TelegramRateLimitError),
        (502, {"ok": False, "error_code": 502, "description": "Bad Gateway"}, TelegramUnavailableError),
    ],
)
async def test_client_maps_errors_without_leaking_token(fake_telegram: FakeTelegram, status: int, body: dict, error: type) -> None:
    fake_telegram.error = (status, body)
    with pytest.raises(error) as exc:
        await _client(fake_telegram).get_updates(None)
    assert TOKEN not in str(exc.value)
    if error is TelegramRateLimitError:
        assert exc.value.retry_after == 17


async def test_network_failure_is_unavailable_and_hides_url(fake_telegram: FakeTelegram) -> None:
    fake_telegram.network_down = True
    with pytest.raises(TelegramUnavailableError) as exc:
        await _client(fake_telegram).get_me()
    assert TOKEN not in str(exc.value) and exc.value.__cause__ is None
    assert TOKEN not in repr(_client(fake_telegram))


async def test_wrong_token_is_auth_error(fake_telegram: FakeTelegram) -> None:
    with pytest.raises(TelegramAuthError):
        await _client(fake_telegram, token="1:wrong").get_me()


async def test_client_writes_only_what_is_intended() -> None:
    """The bot reads authorised chats. Its only writes: Trading OS replies / signal cards (send_message, send_photo,
    edit_caption, answer_callback), the sign-in confirmation, the private group's access management, and deleting its own
    main-chat messages (delete_message, tradingos.autodelete). It can never forward messages."""
    assert not any(hasattr(TelegramBotClient, m) for m in ("forward_message", "deleteMessage", "sendMessage"))
    assert all(hasattr(TelegramBotClient, m) for m in ("send_message", "send_photo", "edit_caption", "answer_callback", "delete_message"))


# --- ingestion: allow-list, dedup, failures ---------------------------------------------------------


async def test_allow_list_enforced(db: Session, fake_telegram: FakeTelegram) -> None:
    _source(db, -100111)
    _source(db, -100333, ingestion=False)
    fake_telegram.message(-100111, "Hello from the authorised community")
    fake_telegram.message(-100222, "Secret talk in an unauthorised group")
    fake_telegram.message(-100333, "Paused chat message")
    runtime = TelegramRuntime(configured=True)

    report = await TelegramIngestor(db, _client(fake_telegram), runtime).sync()

    assert (report.stored, report.ignored_unauthorised, report.ignored_disabled) == (1, 1, 1)
    texts = list(db.scalars(select(TelegramMessage.text)))
    assert texts == ["Hello from the authorised community"]
    discovered = runtime.discovered[-100222]
    assert discovered.title == "Random Other Group" and discovered.ignored_messages == 1
    assert not hasattr(discovered, "text")  # only the chat's existence is remembered


async def test_no_duplicates_across_restarts_and_redelivery(db: Session, fake_telegram: FakeTelegram) -> None:
    _source(db)
    for i in range(3):
        fake_telegram.message(-100111, f"message number {i}")
    first = await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert first.stored == 3
    state = db.scalar(select(TelegramBotState))
    assert state is not None and state.last_update_id == 1002

    # A "restart": a new ingestor/client. Telegram has already dropped acknowledged updates.
    again = await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert again.updates == 0 and _count(db) == 3

    # Telegram re-delivers old updates (e.g. offset lost): the unique key prevents duplicates.
    state.last_update_id = None
    db.commit()
    fake_telegram.message(-100111, "a genuinely new message")  # message id 4
    fake_telegram.updates.insert(0, {"update_id": 1000, "message": {"message_id": 1, "date": 1_790_000_000, "chat": CHAT, "from": {"id": 1, "first_name": "Alice"}, "text": "message number 0"}})
    redelivered = await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert redelivered.duplicates == 1 and redelivered.stored == 1
    assert _count(db) == 4


async def test_edits_update_text_and_requeue_processing(db: Session, fake_telegram: FakeTelegram) -> None:
    _source(db)
    mid = fake_telegram.message(-100111, "Funding rats are high")
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    row = db.scalar(select(TelegramMessage))
    assert row is not None
    row.processed_at = row.sent_at
    db.commit()
    fake_telegram.message(-100111, "Funding rates are high", edited_of=mid)
    report = await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    db.refresh(row)
    assert report.edited == 1 and row.text == "Funding rates are high" and row.processed_at is None and _count(db) == 1


async def test_malformed_updates_are_skipped_not_fatal(db: Session, fake_telegram: FakeTelegram) -> None:
    _source(db)
    fake_telegram.raw({"update_id": 2000, "message": {"date": 1, "chat": CHAT}})
    fake_telegram.raw({"update_id": 2001, "message": {"message_id": 50, "date": 1_790_000_000, "chat": CHAT, "from": {"id": 2}, "text": "valid one"}})
    report = await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert (report.malformed, report.stored) == (1, 1)
    assert db.scalar(select(TelegramBotState.last_update_id)) == 2001


async def test_database_failure_does_not_lose_or_duplicate(db: Session, fake_telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.exc import OperationalError

    _source(db)
    fake_telegram.message(-100111, "important message")
    ingestor = TelegramIngestor(db, _client(fake_telegram), TelegramRuntime())
    await ingestor.verify()
    original_commit = db.commit
    calls = {"n": 0}

    def failing_commit() -> None:
        calls["n"] += 1
        raise OperationalError("COMMIT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(OperationalError):
        await ingestor.sync()
    monkeypatch.setattr(db, "commit", original_commit)
    assert _count(db) == 0 and db.scalar(select(TelegramBotState.last_update_id)) is None  # offset not advanced

    report = await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert report.stored == 1 and _count(db) == 1


# --- API ------------------------------------------------------------------------------------------


def test_status_when_not_configured(client: TestClient) -> None:
    body = client.get("/api/telegram/status").json()
    assert body["configured"] is False and body["status"] == "not_configured"
    assert any("BotFather" in s for s in body["setup_steps"])
    jobs = {j["name"]: j for j in client.get("/api/jobs").json()}
    run = client.post("/api/jobs/telegram.ingest/run").json()
    assert run["status"] == "skipped" and "not configured" in run["error"]
    assert "telegram.ingest" in jobs
    connectors = {c["platform"]: c for c in client.get("/api/connectors").json()}
    assert connectors["telegram"]["status"] == "not_connected"


def test_end_to_end_ingestion_via_api(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    fake_telegram.message(-100222, "unauthorised group says hi")
    sync = tg_client.post("/api/telegram/sync").json()
    assert sync["status"] == "success" and sync["result"]["ignored_unauthorised"] == 1

    status = tg_client.get("/api/telegram/status").json()
    assert status["status"] == "connected" and status["bot_username"] == "pricechat_test_bot"
    assert [d["chat_id"] for d in status["discovered_chats"]] == [-100222]
    assert TOKEN not in str(status)

    added = tg_client.post("/api/telegram/sources", json={"chat_id": -100111})
    assert added.status_code == 201 and added.json()["display_name"] == "Price Chat Community"
    assert tg_client.post("/api/telegram/sources", json={"chat_id": -100111}).status_code == 409

    fake_telegram.message(-100111, "What does Price Chat actually do?", user_id=7, name="Bo")
    assert tg_client.post("/api/telegram/sync").json()["result"]["stored"] == 1
    messages = tg_client.get("/api/telegram/messages").json()
    assert messages[0]["text"] == "What does Price Chat actually do?" and messages[0]["author_name"] == "Bo"

    sources = tg_client.get("/api/telegram/status").json()["sources"]
    assert sources[0]["message_count"] == 1 and sources[0]["last_sync_at"]
    connectors = {c["platform"]: c for c in tg_client.get("/api/connectors").json()}
    assert connectors["telegram"]["status"] == "connected"

    events = [a["event"] for a in tg_client.get("/api/activity", params={"category": "community"}).json()]
    assert "telegram_source_added" in events and "telegram_ingested" in events


def test_telegram_outage_and_recovery(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    tg_client.post("/api/telegram/sync")
    fake_telegram.network_down = True
    run = tg_client.post("/api/telegram/sync").json()
    assert run["status"] == "failed" and "Cannot reach Telegram" in run["error"]
    tg_client.post("/api/telegram/sync")  # second failure: no second error event
    status = tg_client.get("/api/telegram/status").json()
    assert status["status"] == "error" and status["last_error"]
    job = {j["name"]: j for j in tg_client.get("/api/jobs").json()}["telegram.ingest"]
    assert job["consecutive_failures"] == 2 and job["last_error"]

    fake_telegram.network_down = False
    assert tg_client.post("/api/telegram/sync").json()["status"] == "success"
    events = [a["event"] for a in tg_client.get("/api/activity").json()]
    assert events.count("telegram_error") == 1 and "telegram_recovered" in events
    assert tg_client.get("/api/health").status_code == 200  # the rest of Price Chat kept working


def test_invalid_token_reported(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    fake_telegram.error = (401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
    resp = tg_client.post("/api/telegram/verify")
    assert resp.status_code == 502 and resp.json()["code"] == "telegram_auth"
    run = tg_client.post("/api/telegram/sync").json()
    assert run["status"] == "failed" and "rejected the bot token" in run["error"]


def test_source_toggle_and_delete_message(tg_client: TestClient, fake_telegram: FakeTelegram) -> None:
    source = tg_client.post("/api/telegram/sources", json={"chat_id": -100111, "display_name": "Main"}).json()
    fake_telegram.message(-100111, "first message here")
    fake_telegram.message(-100111, "second message here")
    tg_client.post("/api/telegram/sync")
    patched = tg_client.patch(f"/api/telegram/sources/{source['id']}", json={"ingestion_enabled": False}).json()
    assert patched["ingestion_enabled"] is False
    fake_telegram.message(-100111, "ignored while paused")
    assert tg_client.post("/api/telegram/sync").json()["result"]["ignored_disabled"] == 1

    msg = tg_client.get("/api/telegram/messages").json()[0]
    deleted = tg_client.delete(f"/api/telegram/messages/{msg['id']}").json()
    assert deleted["messages"] == 1
    assert len(tg_client.get("/api/telegram/messages").json()) == 1
    assert tg_client.delete(f"/api/telegram/messages/{msg['id']}").status_code == 404


async def test_a_listening_pass_handles_messages_that_arrive_while_it_waits(db: Session, fake_telegram: FakeTelegram) -> None:
    import time as _time

    _source(db)
    fake_telegram.message(-100111, "first, already waiting")
    polls = {"n": 0}
    inner = fake_telegram.transport

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            polls["n"] += 1
            if polls["n"] == 3:  # a member writes while the pass is listening
                fake_telegram.message(-100111, "second, sent a moment later")
        return await inner.handle_async_request(request)

    client = TelegramBotClient(TOKEN, "https://api.telegram.test", transport=httpx.MockTransport(handle))
    t0 = _time.monotonic()
    report = await TelegramIngestor(db, client, TelegramRuntime()).sync(listen_seconds=1.5)
    assert report.stored == 2 and _count(db) == 2  # both handled in the same pass
    assert _time.monotonic() - t0 < 10 and polls["n"] > 3  # it kept polling (the last second is left out: long-polls wait whole seconds)
    once = await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()  # listen_seconds=0: one batch, as before
    assert once.updates == 0


async def test_an_update_that_cannot_be_handled_is_skipped_not_redelivered_forever(db: Session, fake_telegram: FakeTelegram) -> None:
    """A non-numeric date used to raise ValueError out of the batch: the offset was never saved, so every run fetched the
    same update again and failed, and nothing after it (messages, bot commands) was ever handled."""
    _source(db)
    fake_telegram.raw({"update_id": 3000, "message": {"message_id": 60, "date": "soon", "chat": CHAT, "from": {"id": 2}, "text": "odd"}})
    fake_telegram.raw({"update_id": 3001, "message": {"message_id": 61, "date": 1_790_000_000, "chat": CHAT, "from": {"id": 2}, "text": "valid one"}})
    report = await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert (report.malformed, report.stored) == (1, 1)
    assert db.scalar(select(TelegramBotState.last_update_id)) == 3001


async def test_total_ingested_counts_each_message_once_across_batches(db: Session, fake_telegram: FakeTelegram) -> None:
    _source(db)
    for i in range(150):  # two batches of getUpdates (100 + 50)
        fake_telegram.message(-100111, f"message number {i}")
    report = await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert report.stored == 150
    assert db.scalar(select(TelegramBotState.total_ingested)) == 150
