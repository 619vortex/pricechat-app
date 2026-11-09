"""Audit fixes, operations + Telegram bot robustness: silence monitors (ND-2/AR-2), jobs off on this server (AR-5), health
reflecting failing jobs (AR-19), a build id (AR-7/8), edited commands (TG-5), where the bot answers (TG-6), transient
getUpdates errors (TG-4) and HTML cut safely (TG-10)."""

import asyncio
import json
import logging
import re
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import observability
from app.api.routes import health
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.activity import ActivityLog
from app.models.newsdesk import NdStory, NdTelegramPost
from app.models.routing import ScheduledJob
from app.models.telegram import TelegramSource
from app.observability import ErrorWindow, Watchdog
from app.scheduler.scheduler import JobSpec, Scheduler
from app.telegram import ingestion
from app.telegram.client import TelegramBotClient
from app.telegram.ingestion import TelegramIngestor
from app.telegram.state import TelegramRuntime
from tests.fakes import TOKEN, FakeTelegram
from tests.test_observability import SETTINGS, Bot, _is_open, _owner

DAY = 86400


async def _ok(ctx):  # type: ignore[no-untyped-def]
    return {}


def _sched(*names: str) -> Scheduler:
    s = Scheduler(SessionLocal, [JobSpec(n, n, "testing", DAY, _ok, initial_delay_seconds=0) for n in names])
    s.sync_jobs()
    s._loop_task = type("T", (), {"done": lambda self: False})()  # type: ignore[assignment]  # "running"
    s.started_at = utcnow() - timedelta(hours=1)
    return s


def _monitor(s: Scheduler, key: str, now: Any = None) -> dict[str, Any]:
    with SessionLocal() as db:
        return next(m for m in observability.silence_monitors(db, s, now) if m["key"] == key)


# --- ND-2: silence monitors ---------------------------------------------------------------------------------------------


async def test_no_new_story_for_3h_alerts_once_then_recovers() -> None:
    bot = Bot()
    _owner()
    s = _sched("newsdesk.tick")
    now = utcnow()
    with SessionLocal() as db:
        db.add(NdStory(link="https://a.test/1", title="Old story", source="Test", created_at=now - timedelta(hours=4)))
        db.commit()
    dog = Watchdog(SessionLocal, ErrorWindow(), settings=SETTINGS, transport=bot.transport, scheduler=s)
    await dog.check(now=now)
    await dog.check(now=now + timedelta(minutes=1))
    assert len(bot.sent) == 1 and "no new story" in bot.sent[0]  # once per incident
    assert _is_open(observability.KEY_SILENCE + "newsdesk_stories")
    with SessionLocal() as db:
        db.add(NdStory(link="https://a.test/2", title="New story", source="Test", created_at=now))
        db.commit()
    await dog.check(now=now + timedelta(minutes=2))
    assert len(bot.sent) == 2 and "recovered" in bot.sent[1] and not _is_open(observability.KEY_SILENCE + "newsdesk_stories")


def test_story_monitor_is_off_where_the_job_does_not_run() -> None:
    s = _sched("newsdesk.tick")
    s.disabled.add("newsdesk.tick")
    assert _monitor(s, "newsdesk_stories")["status"] == "off"


def test_telegram_lane_quiet_12h_alerts_by_day_and_holds_at_night() -> None:
    s = _sched("newsdesk.telegram")
    base = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        db.add(NdTelegramPost(story_link="https://a.test/t", title="t", headline="h", text="x", source="Test", status="delivered",
                              delivered_at=base - timedelta(hours=13)))
        db.commit()
    assert _monitor(s, "newsdesk_telegram", base)["status"] == "alert"  # @Pricechatupdates is the default channel
    assert _monitor(s, "newsdesk_telegram", base.replace(hour=3))["status"] == "hold"
    assert _monitor(s, "newsdesk_telegram", base - timedelta(hours=2))["status"] == "ok"  # 11 h


def test_backup_older_than_26h_alerts() -> None:
    s = _sched("system.backup")
    now = utcnow()
    assert _monitor(s, "backup", now)["status"] == "ok"  # never ran yet
    with SessionLocal() as db:
        row = db.get(ScheduledJob, "system.backup")
        row.run_count, row.last_status, row.last_error = 3, "failed", "boom"  # type: ignore[union-attr]
        row.last_finished_at = now  # type: ignore[union-attr]
        db.add(ActivityLog(event="database_backed_up", message="Database backed up", created_at=now - timedelta(hours=30)))
        db.commit()
    m = _monitor(s, "backup", now)
    assert m["status"] == "alert" and "no successful database backup" in m["alert"] and "boom" in m["alert"]
    with SessionLocal() as db:
        row = db.get(ScheduledJob, "system.backup")
        row.last_status, row.last_finished_at = "success", now - timedelta(hours=2)  # type: ignore[union-attr]
        db.commit()
    assert _monitor(s, "backup", now)["status"] == "ok"


def test_monitors_are_logged_at_startup_even_without_an_owner_chat(caplog: Any) -> None:
    caplog.set_level(logging.INFO, logger="pricechat.ops")
    s = _sched("system.backup")
    with SessionLocal() as db:
        observability.log_monitors(observability.silence_monitors(db, s))
    assert "Silence monitors:" in caplog.text and "Nightly database backup: ok" in caplog.text


# --- AR-19: health reflects jobs failing in a row -----------------------------------------------------------------------


def test_health_reports_jobs_failing_in_a_row(client: TestClient) -> None:
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    runtime.scheduler._loop_task = type("T", (), {"done": lambda self: False})()
    runtime.scheduler.started_at = utcnow()
    runtime.scheduler.last_tick_at = utcnow()
    name = next(n for n in runtime.scheduler.specs if n not in runtime.scheduler.disabled)
    try:
        health._cache["body"] = None
        assert client.get("/api/health/public").json()["jobs"] == "ok"
        with SessionLocal() as db:
            db.get(ScheduledJob, name).consecutive_failures = 3  # type: ignore[union-attr]
            db.commit()
        health._cache["body"] = None
        body = client.get("/api/health/public").json()
        assert body["jobs"] == "failing" and name not in json.dumps(body)  # a word, never the job's name
        checks = client.get("/api/health").json()["checks"]
        assert checks["failing_jobs"]["status"] == "fail" and name in checks["failing_jobs"]["detail"]
        mons = {m["key"]: m for m in client.get("/api/jobs/monitors").json()}
        assert mons["failing_jobs"]["status"] == "alert"
    finally:
        runtime.scheduler._loop_task = None
        health._cache["body"] = None


# --- AR-5: jobs turned off on this server ---------------------------------------------------------------------------------


def test_jobs_off_on_this_server_are_shown_as_such(client: TestClient) -> None:
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    runtime.scheduler.disabled.add("x.ingest")
    try:
        jobs = {j["name"]: j for j in client.get("/api/jobs").json()}
        assert jobs["x.ingest"]["server_state"] == "off_here"
        assert all(j["server_state"] == "on" for n, j in jobs.items() if n not in runtime.scheduler.disabled)
        r = client.patch("/api/jobs/x.ingest", json={"enabled": True}).json()
        assert r["status"] == "off_here" and "this server" in r["error"]
    finally:
        runtime.scheduler.disabled.discard("x.ingest")


# --- AR-7/8: build id ----------------------------------------------------------------------------------------------------


def test_build_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    for k in ("RAILWAY_GIT_COMMIT_SHA", "BUILD_ID", "RAILWAY_DEPLOYMENT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(health, "BUILD_ID_FILE", tmp_path / "BUILD_ID")
    assert health._version() == "unknown"
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "1234abcd-5678")
    assert health._version() == "deploy-1234abcd"
    (tmp_path / "BUILD_ID").write_text("7ee7077abcdef\n", encoding="utf-8")
    assert health._version() == "7ee7077"
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "fedcba9876543210")
    assert health._version() == "fedcba9"


# --- TG-5 / TG-6: what the bot answers -------------------------------------------------------------------------------------


def _client(tg: FakeTelegram) -> TelegramBotClient:
    return TelegramBotClient(TOKEN, "https://api.telegram.test", transport=tg.transport)


@pytest.fixture
def handled(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    from app.tradingos import bot

    out: list[tuple[int, str]] = []

    async def record(db_: Session, client: object, chat_id: int, user_id: int, text: str, group: bool = False, **kw: object) -> None:
        out.append((chat_id, text))

    monkeypatch.setattr(bot, "handle_command", record)
    return out


async def test_an_edited_command_is_not_run_again(db: Session, fake_telegram: FakeTelegram, handled: list) -> None:
    db.add(TelegramSource(chat_id=-100111, display_name="Community", enabled=True, ingestion_enabled=True))
    db.commit()
    fake_telegram.add_chat(42, "Ana", "private")
    mid = fake_telegram.message(-100111, "/price btc")
    dm = fake_telegram.message(42, "/signal", user_id=42)
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    fake_telegram.message(-100111, "/price eth", edited_of=mid)
    fake_telegram.message(42, "/signal", user_id=42, edited_of=dm)
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert handled == [(-100111, "/price btc"), (42, "/signal")]


async def test_bot_answers_only_allow_listed_groups_people_and_private_chats(db: Session, fake_telegram: FakeTelegram, handled: list) -> None:
    db.add(TelegramSource(chat_id=-100111, display_name="Community", enabled=True, ingestion_enabled=True))
    db.commit()
    fake_telegram.add_chat(42, "Ana", "private")
    channel = {"id": -100333, "title": "News", "type": "channel"}
    fake_telegram.message(-100222, "/price btc")  # someone else's group: not on the allow-list
    fake_telegram.message(-100222, "fc btc")
    fake_telegram.raw({"channel_post": {"message_id": 5, "date": 1_790_000_000, "chat": channel, "sender_chat": channel, "text": "fc btc"}})
    fake_telegram.message(-100111, "/price btc", user_id=777000, name="Telegram",  # a channel's automatic forward
                          extra={"is_automatic_forward": True, "sender_chat": channel})
    fake_telegram.message(-100111, "fc eth")  # a person in the allow-listed group
    fake_telegram.message(42, "/price sol", user_id=42)  # a private chat
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert handled == [(-100111, "/fc eth"), (42, "/price sol")]


# --- TG-4: transient getUpdates errors -------------------------------------------------------------------------------------


class Flaky:
    """getUpdates fails with the given answers first, then the fake answers normally."""

    def __init__(self, tg: FakeTelegram, failures: list[Any]) -> None:
        self.tg, self.failures = tg, list(failures)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates") and self.failures:
            f = self.failures.pop(0)
            if f == "network":
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(f[0], json=f[1])
        return self.tg._handle(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)


async def test_rate_limit_and_5xx_on_getupdates_are_waited_out(db: Session, fake_telegram: FakeTelegram, handled: list,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(ingestion.asyncio, "sleep", fake_sleep)
    fake_telegram.add_chat(42, "Ana", "private")
    fake_telegram.message(42, "/price sol", user_id=42)
    flaky = Flaky(fake_telegram, [(429, {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 5}}),
                                  (502, {"ok": False, "error_code": 502, "description": "Bad Gateway"}), "network"])
    client = TelegramBotClient(TOKEN, "https://api.telegram.test", transport=flaky.transport)
    await TelegramIngestor(db, client, TelegramRuntime()).sync()
    assert slept == [5.0, 2.0, 4.0] and handled == [(42, "/price sol")]


async def test_a_long_outage_still_fails_the_job(db: Session, fake_telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.telegram.errors import TelegramRateLimitError, TelegramUnavailableError

    async def fake_sleep(s: float) -> None:
        return None

    monkeypatch.setattr(ingestion.asyncio, "sleep", fake_sleep)
    down = Flaky(fake_telegram, ["network"] * 10)
    with pytest.raises(TelegramUnavailableError):
        await TelegramIngestor(db, TelegramBotClient(TOKEN, "https://api.telegram.test", transport=down.transport), TelegramRuntime()).sync()
    long_limit = Flaky(fake_telegram, [(429, {"ok": False, "error_code": 429, "description": "x", "parameters": {"retry_after": 600}})])
    with pytest.raises(TelegramRateLimitError):
        await TelegramIngestor(db, TelegramBotClient(TOKEN, "https://api.telegram.test", transport=long_limit.transport), TelegramRuntime()).sync()


def test_telegram_ingest_backs_off_at_most_a_minute(client: TestClient) -> None:
    assert client.app.state.runtime.scheduler.specs["telegram.ingest"].max_backoff_seconds == 60  # type: ignore[attr-defined]


# --- TG-10: HTML cut on safe boundaries ---------------------------------------------------------------------------------


def _visible(html: str) -> str:
    import html as h

    return h.unescape(re.sub(r"<[^>]*>", "", html))


def _balanced(html: str) -> bool:
    stack: list[str] = []
    for closing, name in re.findall(r"<(/?)([a-z-]+)[^>]*>", html):
        if closing:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


@pytest.mark.parametrize("limit", [1024, 4096])
def test_fit_html_never_cuts_a_tag_or_entity(limit: int) -> None:
    from app.telegram.client import fit_html

    line = 'News &amp; more: <a href="https://example.com/a-very-long-link-to-a-story">read the <b>whole</b> story</a> 🥝\n'
    text = "<b>Headline</b>\n" + line * 200
    out = fit_html(text, limit)
    vis = _visible(out)
    assert len(vis.encode("utf-16-le")) // 2 <= limit and out.endswith(("…", "</a>", "</b>")) and _balanced(out)
    assert "<a href=\"https://example.com/a-very" in out and not re.search(r"<[^>]*$", out) and not re.search(r"&[a-z]*$", out.split("…")[0])
    assert fit_html("<b>short</b>", limit) == "<b>short</b>"
    one = "<i>" + "word " * 2000 + "</i>"  # one overlong line inside a tag
    cut = fit_html(one, limit)
    assert _balanced(cut) and cut.endswith("…</i>") and len(_visible(cut)) <= limit


async def test_send_message_fits_long_html(fake_telegram: FakeTelegram) -> None:
    client = _client(fake_telegram)
    text = ('<a href="https://example.com/x">' + "x" * 50 + "</a> ") * 120
    await client.send_message(42, text, html=True)
    sent = next(b for m, b in fake_telegram.calls if m == "sendMessage")["text"]
    assert _balanced(sent) and len(_visible(sent)) <= 4096 and sent != text[:4096]
    await client.aclose()
