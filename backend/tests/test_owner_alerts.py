"""Owner alerts (app/alerts.py): the owner's private Telegram message when something important breaks."""

import json
from datetime import timedelta
from typing import Any

import httpx

from app import alerts
from app.config.settings import Settings
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.alerts import OwnerAlert
from app.models.brand_manager import BmAccount, BmAutomation, BmBrand
from app.scheduler.scheduler import JobSpec, Scheduler

SETTINGS = Settings(telegram_bot_token="123:abc", telegram_api_base="https://api.telegram.test")


class Bot:
    """A Bot API that records every sendMessage (or fails them all)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.down = False

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        if request.url.path.endswith("/getChatAdministrators"):  # finding the owner's chat (alerts.discover_owner_chat): no creator here
            return httpx.Response(200, json={"ok": True, "result": []})
        self.sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)


def _owner(chat: str = "424242") -> None:
    with SessionLocal() as db:
        alerts.settings_row(db).telegram_chat = chat
        db.commit()


def _scheduler(bot: Bot, func, name: str = "pokemon.sync") -> Scheduler:  # type: ignore[no-untyped-def]
    async def alert(spec, status, error, previous, failures):  # type: ignore[no-untyped-def]
        with SessionLocal() as db:
            await alerts.job_outcome(db, spec.name, spec.label, status, error, previous, failures, settings=SETTINGS, transport=bot.transport)

    s = Scheduler(SessionLocal, [JobSpec(name, "Test job", "testing", 60, func, initial_delay_seconds=0)], alert=alert)
    s.sync_jobs()
    return s


async def test_cooldown_same_key_once_per_6h() -> None:
    bot = Bot()
    _owner()
    now = utcnow()
    with SessionLocal() as db:
        assert await alerts.notify_owner(db, "k", "one", settings=SETTINGS, transport=bot.transport, now=now)
        assert not await alerts.notify_owner(db, "k", "two", settings=SETTINGS, transport=bot.transport, now=now + timedelta(hours=5))
        assert await alerts.notify_owner(db, "other", "three", settings=SETTINGS, transport=bot.transport, now=now)  # per key
        assert await alerts.notify_owner(db, "k", "four", settings=SETTINGS, transport=bot.transport, now=now + timedelta(hours=6, minutes=1))
    assert [m["text"] for m in bot.sent] == ["one", "three", "four"]
    assert bot.sent[0]["chat_id"] == 424242
    with SessionLocal() as db:  # durable: survives a restart
        assert db.get(OwnerAlert, "k").sent_count == 2  # type: ignore[union-attr]


async def test_three_failures_alert_then_recovered() -> None:
    bot = Bot()
    _owner()
    state = {"fail": True}

    async def job(ctx):  # type: ignore[no-untyped-def]
        if state["fail"]:
            raise RuntimeError("feed returned 502\nstack details")
        return {}

    s = _scheduler(bot, job)
    for _ in range(2):
        await s.run_job("pokemon.sync")
    assert bot.sent == []  # 2 failures: not yet
    await s.run_job("pokemon.sync")
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "pokemon.sync" in text and "3 runs in a row" in text and "RuntimeError: feed returned 502" in text and "stack details" not in text
    await s.run_job("pokemon.sync")  # 4th failure within 6 h: no repeat
    assert len(bot.sent) == 1

    state["fail"] = False
    await s.run_job("pokemon.sync")
    assert len(bot.sent) == 2 and "recovered" in bot.sent[1]["text"] and "after 4 failed runs" in bot.sent[1]["text"]
    await s.run_job("pokemon.sync")  # only once
    assert len(bot.sent) == 2
    with SessionLocal() as db:
        assert db.get(OwnerAlert, "job:pokemon.sync").open is False  # type: ignore[union-attr]


async def test_success_after_short_streak_sends_nothing() -> None:
    bot = Bot()
    _owner()
    state = {"fail": True}

    async def job(ctx):  # type: ignore[no-untyped-def]
        if state["fail"]:
            raise RuntimeError("x")
        return {}

    s = _scheduler(bot, job)
    await s.run_job("pokemon.sync")
    state["fail"] = False
    await s.run_job("pokemon.sync")
    assert bot.sent == []


async def test_backup_alerts_on_first_failure() -> None:
    bot = Bot()
    _owner()

    async def job(ctx):  # type: ignore[no-untyped-def]
        raise OSError("bucket refused upload")

    s = _scheduler(bot, job, name="system.backup")
    await s.run_job("system.backup")
    assert len(bot.sent) == 1 and "backup FAILED" in bot.sent[0]["text"] and "Failed: 1 run" in bot.sent[0]["text"]


async def test_unset_owner_or_telegram_down_never_crashes() -> None:
    bot = Bot()

    async def job(ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    s = _scheduler(bot, job)
    for _ in range(3):
        out = await s.run_job("pokemon.sync")
        assert out.status == "failed"
    assert bot.sent == []  # no owner chat: logged only
    with SessionLocal() as db:
        assert db.get(OwnerAlert, "job:pokemon.sync") is None  # not marked sent: the owner hears once it is set

    _owner()
    bot.down = True
    out = await s.run_job("pokemon.sync")
    assert out.status == "failed" and bot.sent == []
    with SessionLocal() as db:
        assert not await alerts.notify_owner(db, "x", "y", settings=Settings(telegram_bot_token=""), transport=bot.transport)  # no bot token
    bot.down = False
    await s.run_job("pokemon.sync")  # Telegram back: the owner is told now
    assert len(bot.sent) == 1


async def test_tiktok_expired_while_auto_on() -> None:
    bot = Bot()
    _owner()
    with SessionLocal() as db:
        b = BmBrand(name="Price Chat")
        db.add(b)
        db.flush()
        db.add(BmAutomation(brand_id=b.id, running=True, status="running", reason=""))
        db.add(BmAccount(brand_id=b.id, platform="tiktok", status="expired", scopes=[]))
        db.commit()
        out = await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)
        assert out["tiktok_alerts"] == 1 and "TikTok sign-in expired while AUTO is on" in bot.sent[0]["text"]
        await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)  # cooldown
        assert len(bot.sent) == 1
        db.query(BmAccount).one().status = "connected"
        db.commit()
        out = await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)
        assert out["recovered"] == 1 and "connected again" in bot.sent[1]["text"]

        db.query(BmAutomation).one().running = False  # AUTO off: an expired account is not an alert
        db.query(BmAccount).one().status = "expired"
        db.commit()
        db.query(OwnerAlert).delete()
        db.commit()
        await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)
        assert len(bot.sent) == 2


def test_admin_settings_api(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/system/alerts")
    assert r.status_code == 200 and r.json()["telegram_chat"] == "" and r.json()["enabled"] is True
    assert client.post("/api/system/alerts/test").status_code == 400  # nothing set yet
    assert client.put("/api/system/alerts", json={"telegram_chat": "@someone"}).status_code == 422
    r = client.put("/api/system/alerts", json={"telegram_chat": "424242", "enabled": False})
    assert r.json()["telegram_chat"] == "424242" and r.json()["enabled"] is False
    with SessionLocal() as db:
        assert alerts.owner_chat(db) is None  # off: log only


def _brand(db, status: str = "running", reason: str = ""):  # type: ignore[no-untyped-def]
    b = BmBrand(name="Price Chat")
    db.add(b)
    db.flush()
    db.add(BmAutomation(brand_id=b.id, running=True, status=status, reason=reason))
    db.add(BmAccount(brand_id=b.id, platform="tiktok", status="connected", scopes=["video.upload"]))
    db.commit()
    return b


async def test_brand_manager_blocked_while_auto_on_is_told_once_then_recovered() -> None:
    bot = Bot()
    _owner()
    with SessionLocal() as db:
        _brand(db, "blocked", "Video storage isn't set up (the S3 bucket: PORTFOLIO_BUCKET…).")
        out = await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)
        assert out["brand_alerts"] == 1 and "isn't publishing" in bot.sent[0]["text"] and "Video storage" in bot.sent[0]["text"]
        await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)  # 10 min later: no repeat
        assert len(bot.sent) == 1
        a = db.query(BmAutomation).one()
        a.status, a.reason = "running", ""
        db.commit()
        out = await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)
        assert out["recovered"] == 1 and "publishing on schedule again" in bot.sent[1]["text"]
        await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)
        assert len(bot.sent) == 2


async def test_a_single_failed_tick_is_not_an_alert_but_a_failing_streak_is() -> None:
    from app.brand_manager import brands

    bot = Bot()
    _owner()
    now = utcnow()
    with SessionLocal() as db:
        b = _brand(db, "error", "OperationalError: database is locked")
        brands.log(db, b.id, "Something went wrong (OperationalError); retrying next minute.", level="error", commit=True)
        assert (await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport, now=now))["brand_alerts"] == 0
        for _ in range(2):
            brands.log(db, b.id, "Something went wrong (OperationalError); retrying next minute.", level="error", commit=True)
        assert (await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport, now=now))["brand_alerts"] == 1
        assert "keeps failing" in bot.sent[0]["text"]


async def test_videos_past_their_slot_and_failed_sends_reach_the_owner() -> None:
    from app.brand_manager import brands
    from app.models.brand_manager import BmVideo

    bot = Bot()
    _owner()
    now = utcnow()
    with SessionLocal() as db:
        b = _brand(db)
        for i, st in enumerate(("queued", "rendering", "needs_review")):
            db.add(BmVideo(brand_id=b.id, category="facts", template="facts", topic=f"t{i}", topic_key=f"k{i}", status=st, stages={},
                           slot_at=now - timedelta(hours=2)))
        db.add(BmVideo(brand_id=b.id, category="facts", template="facts", topic="soon", topic_key="k9", status="queued", stages={},
                       slot_at=now - timedelta(minutes=10)))  # only just due: not late yet
        db.commit()
        out = await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport, now=now)
        text = bot.sent[0]["text"]
        assert out["brand_alerts"] == 1 and "2 videos past the posting time still waiting for an AI worker" in text
        assert "1 video past the posting time waiting for your approval" in text
        # a send that failed is reported once, not again on the next check
        brands.log(db, b.id, "“t0”: TikTok's answer was lost (ReadTimeout): Check TikTok before sending it again.", level="error",
                   stage="publish", commit=True)
        out = await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport, now=now + timedelta(minutes=1))
        assert out["send_failed_alerts"] == 1 and "1 video not sent to TikTok" in bot.sent[-1]["text"] and "ReadTimeout" in bot.sent[-1]["text"]
        n = len(bot.sent)
        out = await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport, now=now + timedelta(hours=7))
        assert out["send_failed_alerts"] == 0 and not any("not sent to TikTok" in m["text"] for m in bot.sent[n:])


async def test_publishing_alerts_stay_quiet_when_auto_is_off_or_tiktok_has_its_own_alert() -> None:
    bot = Bot()
    _owner()
    with SessionLocal() as db:
        _brand(db, "blocked", "Pick at least one content category in Settings › Content.")
        db.query(BmAccount).one().status = "expired"
        db.commit()
        out = await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)
        assert out["tiktok_alerts"] == 1 and out["brand_alerts"] == 0
        db.query(BmAccount).one().status = "connected"
        a = db.query(BmAutomation).one()
        a.running = False
        db.commit()
        db.query(OwnerAlert).delete()
        db.commit()
        out = await alerts.check_brand_accounts(db, settings=SETTINGS, transport=bot.transport)
        assert out["brand_alerts"] == 0
