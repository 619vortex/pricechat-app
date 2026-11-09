"""Audit fixes AR-3 / TG-2 (+ND-7) and TG-3 (2026-11-09).

- The Telegram listener (telegram.ingest) runs in its own always-on loop, outside the scheduler's one-at-a-time lane: a bot
  command is answered while a long job (a backup, a News Desk run) holds the lane.
- Updates stay exactly-once: the offset is saved before a command is answered, and a restart never answers it again.
- Heavy commands (/chart, fc, /signal, /scan) are limited per Telegram user: one short notice, then silence.
"""

import asyncio
import copy
import time
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.routing import ScheduledJob
from app.models.telegram import TelegramBotState
from app.scheduler.scheduler import JobContext, JobSpec, Scheduler
from app.telegram.client import TelegramBotClient
from app.telegram.ingestion import TelegramIngestor
from app.telegram.state import TelegramRuntime
from tests.fakes import TOKEN, FakeTelegram
from tests.test_trading_os import FakeTG


@pytest.fixture(autouse=True)
def _private_chats(fake_telegram: FakeTelegram) -> None:
    for uid in (5, 7):
        fake_telegram.add_chat(uid, f"User {uid}", "private")


def _client(tg: FakeTelegram) -> TelegramBotClient:
    return TelegramBotClient(TOKEN, "https://api.telegram.test", transport=tg.transport)


@pytest.fixture
def handled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every command the bot is asked to answer, with the offset saved in the database at that moment (another session)."""
    from app.tradingos import bot

    out: list[dict[str, Any]] = []

    async def record(db_: Session, client: object, chat_id: int, user_id: int, text: str, group: bool = False, **kw: Any) -> None:
        with SessionLocal() as other:
            saved = other.scalar(select(TelegramBotState.last_update_id).limit(1))
        out.append({"chat": chat_id, "text": text, "saved_offset": saved, "at": time.perf_counter(), **kw})

    monkeypatch.setattr(bot, "handle_command", record)
    return out


def _listener_spec(tg: FakeTelegram, name: str = "telegram.ingest", interval: int = 3600) -> JobSpec:
    async def listen(ctx: JobContext) -> dict[str, Any]:
        client = _client(tg)
        try:
            return (await TelegramIngestor(ctx.db, client, TelegramRuntime()).sync()).as_dict()
        finally:
            await client.aclose()

    return JobSpec(name, "Telegram ingestion", "test", interval, listen, initial_delay_seconds=0, timeout_seconds=60, own_lane=True)


def _long_spec(hold: asyncio.Event, name: str = "system.backup") -> JobSpec:
    async def backup(ctx: JobContext) -> dict[str, Any]:
        await hold.wait()  # "a backup": holds the one-at-a-time lane until the test lets go
        return {}

    return JobSpec(name, "Backup", "test", 3600, backup, initial_delay_seconds=0, timeout_seconds=60)


async def test_listener_answers_while_a_long_job_holds_the_lane(db: Session, fake_telegram: FakeTelegram, handled: list) -> None:
    hold = asyncio.Event()
    sch = Scheduler(SessionLocal, [_long_spec(hold), _listener_spec(fake_telegram)])
    sch.sync_jobs()
    long_job = asyncio.create_task(sch.run_job("system.backup"))
    await asyncio.sleep(0.05)
    assert sch.busy and sch.is_running("system.backup")

    fake_telegram.message(5, "/help", user_id=5)
    out = await asyncio.wait_for(sch.run_job("telegram.ingest"), timeout=10)  # before the fix: waited for the backup
    assert out.status == "success" and [h["text"] for h in handled] == ["/help"]
    assert not long_job.done()  # ...answered while the backup still held the lane
    row = db.get(ScheduledJob, "telegram.ingest")
    db.refresh(row)
    assert row.last_status == "success" and row.run_count == 1  # still a job: Admin's jobs page shows its runs
    hold.set()
    assert (await long_job).status == "success"


async def test_listener_loop_runs_back_to_back_beside_a_long_job(db: Session, fake_telegram: FakeTelegram, handled: list) -> None:
    hold = asyncio.Event()
    runs: list[float] = []
    listener = _listener_spec(fake_telegram)
    inner = listener.func

    async def chained(ctx: JobContext) -> dict[str, Any]:
        runs.append(time.perf_counter())
        out = await inner(ctx)
        sch.request_run("telegram.ingest", 0)  # as jobs.telegram_ingest does after each listen: always on
        return out

    listener.func = chained
    sch = Scheduler(SessionLocal, [_long_spec(hold), listener], tick_seconds=0.05)
    sch.start()
    try:
        for _ in range(100):
            if sch.busy:
                break
            await asyncio.sleep(0.02)
        assert sch.busy  # the backup holds the lane
        fake_telegram.message(5, "/help", user_id=5)
        for _ in range(200):
            if handled and len(runs) >= 3:
                break
            await asyncio.sleep(0.02)
        assert [h["text"] for h in handled] == ["/help"] and len(runs) >= 3 and sch.busy
        assert sch.is_running("system.backup") and not sch.is_queued("telegram.ingest")
    finally:
        hold.set()
        await sch.stop()


async def test_a_crashed_listener_loop_backs_off_and_is_restarted(db: Session, fake_telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch,
                                                                  caplog: pytest.LogCaptureFixture) -> None:
    from app.scheduler import scheduler as mod

    monkeypatch.setattr(mod, "LANE_CRASH_BACKOFF_MAX", 0.05)
    sch = Scheduler(SessionLocal, [_listener_spec(fake_telegram)], tick_seconds=0.05)
    real, fails = sch._due_in, {"n": 0}

    def flaky(name: str) -> float | None:
        if fails["n"] < 2:  # e.g. "database is locked" twice
            fails["n"] += 1
            raise RuntimeError("database is locked")
        return real(name)

    monkeypatch.setattr(sch, "_due_in", flaky)
    sch.start()
    try:
        for _ in range(200):
            row = db.get(ScheduledJob, "telegram.ingest")
            db.refresh(row)
            if row.run_count:
                break
            await asyncio.sleep(0.02)
        assert row.run_count >= 1 and fails["n"] == 2  # it kept going after the errors
        assert "own loop failed" in caplog.text  # ...and said so
        sch._lanes["telegram.ingest"].cancel()  # the loop itself ends: the scheduler's loop starts a new one
        await asyncio.sleep(0.2)
        assert not sch._lanes["telegram.ingest"].done()
    finally:
        await sch.stop()


async def test_a_disabled_listener_gets_no_loop(db: Session, fake_telegram: FakeTelegram) -> None:
    sch = Scheduler(SessionLocal, [_listener_spec(fake_telegram)], tick_seconds=0.05, disabled={"telegram.ingest"})
    sch.start()
    try:
        await asyncio.sleep(0.1)
        assert sch._lanes == {} and (await sch.run_job("telegram.ingest")).status == "disabled"
    finally:
        await sch.stop()


async def test_offset_is_saved_before_answering_and_a_restart_never_answers_twice(db: Session, fake_telegram: FakeTelegram, handled: list) -> None:
    first = fake_telegram.next_update_id
    fake_telegram.message(5, "/help", user_id=5)
    fake_telegram.message(5, "/markets", user_id=5)
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert [h["text"] for h in handled] == ["/help", "/markets"]
    assert all(h["saved_offset"] == first + 1 for h in handled)  # committed (in its worker thread) before any answer

    # a restart: a new session and ingestor; Telegram still holds the updates until it is asked with the next offset
    with SessionLocal() as fresh:
        await TelegramIngestor(fresh, _client(fake_telegram), TelegramRuntime()).sync()
    assert [h["text"] for h in handled] == ["/help", "/markets"]  # nothing answered again


async def test_a_restart_before_the_offset_was_saved_redelivers_without_duplicate_messages(db: Session, fake_telegram: FakeTelegram) -> None:
    """Telegram re-delivers what wasn't acknowledged; stored messages stay unique (the batch and the offset commit together)."""
    from app.models.telegram import TelegramMessage, TelegramSource

    db.add(TelegramSource(chat_id=-100111, display_name="Community"))
    db.commit()
    fake_telegram.message(-100111, "hello")
    update = copy.deepcopy(fake_telegram.updates[-1])
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    state = db.scalar(select(TelegramBotState).limit(1))
    assert state.last_update_id == update["update_id"]
    state.last_update_id -= 1  # as if the process died before the offset reached Telegram
    db.commit()
    fake_telegram.updates.append(update)  # Telegram re-sends it
    with SessionLocal() as fresh:
        report = await TelegramIngestor(fresh, _client(fake_telegram), TelegramRuntime()).sync()
    assert report.stored == 0 and report.duplicates >= 1
    assert len(db.scalars(select(TelegramMessage).where(TelegramMessage.chat_id == -100111)).all()) == 1  # stored once


# ---------------------------------------------------------------- TG-3: per-user limit for heavy commands


@pytest.fixture
def answered(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from app.tradingos import bot

    out: list[str] = []

    async def fake_command(db_: Session, client: Any, chat_id: Any, user_id: int, cmd: str, args: list[str], reply_to: Any = None,
                           brief: bool = False) -> str:
        out.append(cmd)
        return f"answer to {cmd}"

    monkeypatch.setattr(bot, "_command", fake_command)
    return out


async def test_heavy_commands_are_limited_per_user_with_one_notice(db: Session, answered: list) -> None:
    from app.tradingos import bot

    tg = FakeTG()
    await bot.handle_command(db, tg, -5, 7, "/chart BTC", group=True, reply_to=1, throttle=True)
    await bot.handle_command(db, tg, -5, 7, "/signal", group=True, reply_to=2, throttle=True)  # within 10 s: refused, one notice
    await bot.handle_command(db, tg, -5, 7, "/fc eth", group=True, reply_to=3, throttle=True)  # ("fc eth"): silence now
    await bot.handle_command(db, tg, -5, 7, "/scan", group=True, reply_to=4, throttle=True)
    assert answered == ["/chart"]
    notices = [m for m in tg.sent if "every 10 seconds" in m[2]]
    assert len(notices) == 1 and "📢" not in notices[0][2]  # one short line, no channel footer
    await bot.handle_command(db, tg, -5, 8, "/chart SOL", group=True, reply_to=5, throttle=True)  # someone else: fine
    await bot.handle_command(db, tg, -5, 7, "/price BTC", group=True, reply_to=6, throttle=True)  # light commands: never limited
    assert answered == ["/chart", "/chart", "/price"]


async def test_heavy_limit_frees_up_and_warns_again_after_a_pass(db: Session, answered: list) -> None:
    from app.security.rate_limit import LIMITS
    from app.tradingos import bot

    tg = FakeTG()
    clock = {"t": 1000.0}
    real_hit = LIMITS["tg_heavy"].hit
    LIMITS["tg_heavy"].hit = lambda key, now=None: real_hit(key, clock["t"])  # type: ignore[method-assign]
    try:
        await bot.handle_command(db, tg, 7, 7, "/chart BTC", throttle=True)
        await bot.handle_command(db, tg, 7, 7, "/chart BTC", throttle=True)
        clock["t"] += 10.5
        await bot.handle_command(db, tg, 7, 7, "/chart BTC", throttle=True)  # 10 s later: answered
        await bot.handle_command(db, tg, 7, 7, "/chart BTC", throttle=True)  # too soon again: a fresh notice
    finally:
        del LIMITS["tg_heavy"].hit
    assert answered == ["/chart", "/chart"]
    assert len([m for m in tg.sent if "every 10 seconds" in m[2]]) == 2


async def test_commands_not_from_the_listener_are_never_limited(db: Session, answered: list) -> None:
    from app.tradingos import bot

    tg = FakeTG()
    for _ in range(3):
        await bot.handle_command(db, tg, 7, 7, "/chart BTC")  # throttle defaults to off (direct callers, tests)
    assert answered == ["/chart"] * 3


async def test_the_listener_throttles_heavy_commands(db: Session, fake_telegram: FakeTelegram, answered: list) -> None:
    for _ in range(3):
        fake_telegram.message(7, "/chart BTC", user_id=7)
    await TelegramIngestor(db, _client(fake_telegram), TelegramRuntime()).sync()
    assert answered == ["/chart"]
    sent = [b for m, b in fake_telegram.calls if m == "sendMessage"]
    assert sum("every 10 seconds" in str(b.get("text")) for b in sent) == 1


async def test_fc_buttons_are_limited_per_user(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tradingos import bot

    drawn: list[str] = []

    async def fake_fc(db_: Session, client: Any, cq: dict[str, Any], uid: int, data: str) -> None:
        drawn.append(data)

    monkeypatch.setattr(bot, "_fc_callback", fake_fc)
    tg = FakeTG()
    ing = TelegramIngestor(db, tg, TelegramRuntime())  # type: ignore[arg-type]
    cbs = [{"id": str(i), "from": {"id": 7}, "data": f"fc:{tf}:BTC", "message": {"chat": {"id": -5}, "message_id": 9}}
           for i, tf in enumerate(["1h", "4h", "1d", "15m", "5m"])]
    await ing._trading_os(cbs)
    assert drawn == ["fc:1h:BTC", "fc:4h:BTC", "fc:1d:BTC"]  # browsing a few timeframes is fine
    assert [m[2] for m in tg.sent if m[0] == "answer"] == ["One moment: wait a few seconds before the next timeframe."] * 2


def test_only_the_telegram_listener_has_its_own_lane(client: Any) -> None:
    from app.jobs import build_jobs

    own = [s.name for s in build_jobs(client.app.state.runtime) if s.own_lane]
    assert own == ["telegram.ingest"]  # everything else keeps the one-at-a-time lane (SQLite writers stay serial)
