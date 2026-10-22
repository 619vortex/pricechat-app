"""Production observability: what the owner learns when something breaks, and what the logs say about it.

- Request log (RequestObserver, the outermost middleware): one compact line per request - method, ROUTE TEMPLATE (never
  the raw path or query string), status, duration, request id. INFO when slow (> SLOW_REQUEST_MS) or 5xx, DEBUG
  otherwise. No bodies, cookies, auth headers or IPs. Every response carries X-Request-ID (a sane incoming one is kept).
- Error-rate alert (ErrorWindow + Watchdog): 5xx responses in the last 5 minutes. >= ERRORS_MIN, or >= ERROR_RATIO of
  >= RATIO_MIN_REQUESTS -> ONE owner alert with the top routes / error types, then one "recovered" message.
- Jobs (Watchdog): a job whose runs are > 3x its recent median several times in a row (the scheduler keeps the
  numbers), or an enabled job that hasn't run for > 3x its interval (stalled scheduler) -> one alert each, then one
  "recovered".
- Deep health (deep_checks): the owner-only /api/health's per-subsystem ok / warn / fail (off = not used here).
- Startup self-check (startup_self_check): one log line naming configuration problems (warns, never stops the app).

Alerts reuse app/alerts.py (owner_alerts rows: open until fixed, so a restart doesn't re-send and nothing spams).
"""

import asyncio
import logging
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import MutableHeaders

from app.db.base import utcnow

logger = logging.getLogger("pricechat.ops")
http_logger = logging.getLogger("pricechat.http")

SLOW_REQUEST_MS = 1000.0
# answers that wait on purpose (long-polls): never "slow"
LONG_POLL_ROUTES = frozenset({"/api/vortex/claim"})
# not counted towards the error rate: the public uptime check answers 503 on purpose when something else is down
UNCOUNTED_ROUTES = frozenset({"/api/health/public", "/api/health/live"})
_RID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

WINDOW_SECONDS, BUCKET_SECONDS = 300, 10
ERRORS_MIN, ERROR_RATIO, RATIO_MIN_REQUESTS = 20, 0.10, 50

WATCH_EVERY = 60.0  # seconds between watchdog passes
STALL_FACTOR = 3
# one long job holds the others (jobs run one at a time, each up to its timeout, 15 min by default): never call a job
# stalled before this
STALL_FLOOR = timedelta(minutes=20)
RETRY_AFTER = 900.0  # an alert that couldn't be sent (no owner chat / Telegram down) is retried after 15 min, not every pass

KEY_HTTP, KEY_SLOW, KEY_STALLED = "ops:http_errors", "ops:slow_jobs", "ops:stalled_jobs"
DPAPI = os.name == "nt"  # Windows protects stored secrets without APP_ENCRYPTION_KEY; elsewhere the key is needed


# --- request ids + request log --------------------------------------------------------------------------------------


def request_id(scope: dict[str, Any]) -> str:
    for k, v in scope.get("headers") or ():
        if k == b"x-request-id":
            try:
                rid = v.decode("ascii").strip()
            except UnicodeDecodeError:
                break
            if _RID.match(rid):
                return rid
            break
    return uuid.uuid4().hex[:16]


def route_of(scope: dict[str, Any]) -> str:
    """The matched route's template (/api/pokemon/cards/{card_id}); never the raw path (ids, tokens, names)."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "unmatched"


class ErrorWindow:
    """Requests and 5xx in 10-second buckets over the last 5 minutes (in memory, one process)."""

    def __init__(self, window: int = WINDOW_SECONDS, bucket: int = BUCKET_SECONDS) -> None:
        self.window, self.bucket = window, bucket
        self._b: dict[int, list[Any]] = {}  # bucket -> [requests, errors, Counter(route), Counter(type)]

    def _slot(self, now: float) -> list[Any]:
        i = int(now // self.bucket)
        slot = self._b.get(i)
        if slot is None:
            slot = self._b[i] = [0, 0, Counter(), Counter()]
            old = i - self.window // self.bucket
            for k in [k for k in self._b if k <= old]:
                del self._b[k]
        return slot

    def record(self, route: str, status: int, error_type: str | None = None, now: float | None = None) -> None:
        if route in UNCOUNTED_ROUTES:
            return
        slot = self._slot(time.monotonic() if now is None else now)
        slot[0] += 1
        if status >= 500:
            slot[1] += 1
            slot[2][route] += 1
            slot[3][error_type or f"HTTP {status}"] += 1

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        first = int(now // self.bucket) - self.window // self.bucket
        total = errors = 0
        routes: Counter[str] = Counter()
        types: Counter[str] = Counter()
        for i, (n, e, r, t) in list(self._b.items()):
            if i > first:
                total, errors = total + n, errors + e
                routes.update(r)
                types.update(t)
        return {"requests": total, "errors": errors, "routes": routes.most_common(3), "types": types.most_common(3)}

    @staticmethod
    def tripped(snap: dict[str, Any]) -> bool:
        return snap["errors"] >= ERRORS_MIN or (snap["requests"] >= RATIO_MIN_REQUESTS and snap["errors"] >= ERROR_RATIO * snap["requests"])

    @staticmethod
    def healthy(snap: dict[str, Any]) -> bool:
        """Clearly back to normal (half the alert levels: no flapping around the line)."""
        return snap["errors"] < ERRORS_MIN / 2 and (snap["requests"] < RATIO_MIN_REQUESTS or snap["errors"] < ERROR_RATIO / 2 * snap["requests"])


class RequestObserver:
    """Pure ASGI middleware (add it last so it wraps everything but Starlette's own error page)."""

    def __init__(self, app: Any, window: ErrorWindow) -> None:
        self.app, self.window = app, window

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = request_id(scope)
        scope.setdefault("state", {})["request_id"] = rid  # request.state.request_id (the 500 handler logs it)
        started = time.perf_counter()
        first_byte: list[float] = []
        status = [500]

        async def send_with_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status[0] = message["status"]
                first_byte.append(time.perf_counter())
                MutableHeaders(scope=message)["X-Request-ID"] = rid
            await send(message)

        error_type: str | None = None
        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            if not first_byte:
                status[0] = 500
            error_type = type(exc).__name__
            raise
        finally:
            route = route_of(scope)
            # time to the answer's first byte: a streamed download / live feed isn't "slow" for staying open
            ms = ((first_byte[0] if first_byte else time.perf_counter()) - started) * 1000
            self.window.record(route, status[0], error_type)
            slow = ms > SLOW_REQUEST_MS and route not in LONG_POLL_ROUTES
            level = logging.INFO if (status[0] >= 500 or slow) else logging.DEBUG
            if http_logger.isEnabledFor(level):
                http_logger.log(level, "%s %s %d %.0fms rid=%s%s%s", scope.get("method", "?"), route, status[0], ms, rid,
                                " SLOW" if slow else "", f" error={error_type}" if error_type else "")


# --- watchdog: error-rate, slow and stalled jobs -> owner alerts ---------------------------------------------------------


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _when(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M UTC")


def _secs(s: float) -> str:
    return f"{s:.1f}s" if s < 120 else f"{s / 60:.0f} min"


def stalled_jobs(db: Session, scheduler: Any, now: datetime | None = None) -> list[str]:
    """Enabled jobs (running on this server) that haven't started for > 3x their interval (or their failure back-off)."""
    from app.models.routing import ScheduledJob

    now = now or utcnow()
    if scheduler is None or getattr(scheduler, "_loop_task", None) is None:
        return []
    up_since = _aware(scheduler.started_at) or now
    out = []
    for row in db.scalars(select(ScheduledJob).where(ScheduledJob.enabled.is_(True))):
        spec = scheduler.specs.get(row.name)
        if spec is None or row.name in scheduler.disabled or scheduler.is_running(row.name):
            continue
        every = row.interval_seconds or spec.interval_seconds
        if row.consecutive_failures:
            every = max(every, min(every * 2 ** (row.consecutive_failures - 1), spec.max_backoff_seconds))
        limit = max(timedelta(seconds=STALL_FACTOR * every), STALL_FLOOR)
        last = max([d for d in (_aware(row.last_run_at), _aware(row.last_finished_at)) if d is not None], default=None)
        since = max(last, up_since) if last is not None else up_since  # a fresh process gets one full limit to start it
        if now - since > limit:
            out.append(f"{spec.label} ({row.name}): last ran {_ago(now, last)}, runs every {_secs(every)}")
    return out


def _ago(now: datetime, then: datetime | None) -> str:
    if then is None:
        return "never"
    s = (now - then).total_seconds()
    return f"{s / 60:.0f} min ago" if s < 7200 else f"{s / 3600:.1f} h ago"


class Watchdog:
    """Checks every WATCH_EVERY seconds; each problem is one owner_alerts key (open until fixed)."""

    def __init__(self, session_factory: sessionmaker[Session], window: ErrorWindow, *, settings: Any = None,
                 transport: Any = None, scheduler: Any = None) -> None:
        self.session_factory, self.window = session_factory, window
        self.settings, self.transport, self.scheduler = settings, transport, scheduler
        self._tried: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None
        self.last_pass_at: datetime | None = None

    async def _problem(self, db: Session, key: str, text_: str) -> bool:
        from app import alerts

        if alerts._is_open(db, key):
            return False
        t = time.monotonic()
        if t - self._tried.get(key, float("-inf")) < RETRY_AFTER:
            return False
        self._tried[key] = t
        return await alerts.notify_owner(db, key, text_, settings=self.settings, transport=self.transport, cooldown=False, open_=True)

    async def _fixed(self, db: Session, key: str, text_: str) -> bool:
        from app import alerts

        if not alerts._is_open(db, key):
            return False
        if await alerts.notify_owner(db, f"{key}:recovered", text_, settings=self.settings, transport=self.transport, cooldown=False):
            alerts._close(db, key)
            self._tried.pop(key, None)
            return True
        return False

    async def check(self, now: datetime | None = None, mono: float | None = None) -> dict[str, int]:
        now = now or utcnow()
        out = {"alerts": 0, "recovered": 0}
        self.last_pass_at = now
        with self.session_factory() as db:
            # 1) 5xx rate
            snap = self.window.snapshot(mono)
            if ErrorWindow.tripped(snap):
                routes = "\n".join(f"- {r}: {n}" for r, n in snap["routes"])
                types = ", ".join(f"{t} x{n}" for t, n in snap["types"])
                msg = (f"Price Chat alert: server errors\n{snap['errors']} of {snap['requests']} requests failed (5xx) in the "
                       f"last 5 min.\nTop routes:\n{routes}\nErrors: {types}\nDetails: search the Railway logs for 'error=' "
                       f"or the request ids.\nTime: {_when(now)}")
                out["alerts"] += await self._problem(db, KEY_HTTP, msg)
            elif ErrorWindow.healthy(snap):
                out["recovered"] += await self._fixed(db, KEY_HTTP, f"Price Chat: recovered\nServer errors are back to normal "
                                                                    f"({snap['errors']} in the last 5 min).\nTime: {_when(now)}")
            sched = self.scheduler
            if sched is None:
                return out
            # 2) jobs much slower than usual
            slow = sched.slow_jobs()
            if slow:
                lines = "\n".join(f"- {sched.specs[n].label} ({n}): {_secs(v['last_ms'] / 1000)}, usually {_secs(v['median_ms'] / 1000)}"
                                  f" ({int(v['streak'])} runs in a row)" for n, v in sorted(slow.items()) if n in sched.specs)
                out["alerts"] += await self._problem(db, KEY_SLOW, f"Price Chat alert: background jobs running slow\n{lines}\n"
                                                                    f"Time: {_when(now)}")
            else:
                out["recovered"] += await self._fixed(db, KEY_SLOW, f"Price Chat: recovered\nBackground jobs are running at "
                                                                     f"their usual speed again.\nTime: {_when(now)}")
            # 3) the scheduler stopped / jobs not starting
            dead = sched._loop_task is not None and sched._loop_task.done()
            stalled = stalled_jobs(db, sched, now)
            if dead or stalled:
                lines = (["- the scheduler loop has stopped (no background job will run until a restart)"] if dead else []) + [f"- {s}" for s in stalled[:8]]
                more = f"\n(+{len(stalled) - 8} more)" if len(stalled) > 8 else ""
                out["alerts"] += await self._problem(db, KEY_STALLED, "Price Chat alert: background jobs not running\n"
                                                                       + "\n".join(lines) + more + f"\nTime: {_when(now)}")
            else:
                out["recovered"] += await self._fixed(db, KEY_STALLED, f"Price Chat: recovered\nBackground jobs are running on "
                                                                        f"schedule again.\nTime: {_when(now)}")
        return out

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(WATCH_EVERY)
            try:
                await self.check()
            except Exception:  # noqa: BLE001 - the watchdog must never die
                logger.warning("Ops watchdog pass failed", exc_info=True)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


# --- deep health (owner-only /api/health) ---------------------------------------------------------------------------


def _c(status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "detail": detail, **extra}


def _age(now: datetime, then: datetime | None) -> float | None:
    then = _aware(then)
    return None if then is None else round((now - then).total_seconds(), 1)


def deep_checks(db: Session, runtime: Any, watchdog: Any = None, window: ErrorWindow | None = None) -> dict[str, dict[str, Any]]:
    """Each subsystem: ok | warn | fail (off: not used / not running on this server)."""
    from app.models.routing import ScheduledJob

    now = utcnow()
    out: dict[str, dict[str, Any]] = {}

    t = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        ms = round((time.perf_counter() - t) * 1000, 1)
        out["database"] = _c("ok" if ms < 100 else "warn" if ms < 1000 else "fail", f"round trip {ms} ms", ms=ms)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {"database": _c("fail", f"unreachable ({type(exc).__name__})")}

    try:
        from alembic.runtime.migration import MigrationContext

        from app.db.migrate import _script_heads

        code, current = list(_script_heads()), sorted(MigrationContext.configure(db.connection()).get_current_heads())
        out["migrations"] = _c("ok" if current == code else "fail", "up to date" if current == code else
                               "database revision differs from the code's migration head", code=code, database=current)
    except Exception as exc:  # noqa: BLE001
        out["migrations"] = _c("warn", f"couldn't read ({type(exc).__name__})")

    sched = getattr(runtime, "scheduler", None)
    task = getattr(sched, "_loop_task", None)
    if sched is None or task is None:
        out["scheduler"] = _c("off", "not running on this server")
    elif task.done():
        out["scheduler"] = _c("fail", "the scheduler loop has stopped")
    else:
        age = _age(now, sched.last_tick_at)
        limit = max(60.0, sched.tick_seconds * 6)
        out["scheduler"] = _c("ok" if age is not None and age < limit else "warn" if age is None or age < limit * 5 else "fail",
                              "alive" if age is not None and age < limit else "no recent tick", last_tick_age_s=age,
                              running=sched._active)
    if sched is not None:
        names = [n for n in sched.specs if n not in sched.disabled]
        late_before = now - timedelta(minutes=5)
        overdue = [r.name for r in db.scalars(select(ScheduledJob).where(ScheduledJob.enabled.is_(True), ScheduledJob.name.in_(names),
                                                                           ScheduledJob.next_run_at < late_before))]
        waiting = len(sched.queue()["waiting"])
        stalled = len(stalled_jobs(db, sched, now))
        n = len(overdue)
        st = "off" if task is None else "ok" if n == 0 and not stalled else "warn" if n <= 3 and not stalled else "fail"
        out["job_backlog"] = _c(st, f"{n} job(s) overdue > 5 min, {waiting} waiting, {stalled} stalled",
                                overdue=overdue[:10], waiting=waiting, stalled=stalled, slow=sorted(sched.slow_jobs()))

    try:
        from app.telegram.ingestion import get_bot_state

        on = bool(runtime.settings.telegram_configured) and sched is not None and "telegram.ingest" not in sched.disabled
        state = get_bot_state(db)
        age = _age(now, state.last_success_at)
        if not on:
            out["telegram_ingest"] = _c("off", "not read on this server", last_success_age_s=age)
        else:
            out["telegram_ingest"] = _c("ok" if age is not None and age < 300 else "warn" if age is not None and age < 1800 else "fail",
                                        f"last success {_ago(now, _aware(state.last_success_at))}", last_success_age_s=age)
        db.rollback()  # get_bot_state may have added the row: a health check writes nothing
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        out["telegram_ingest"] = _c("warn", f"couldn't read ({type(exc).__name__})")

    try:
        from app.models.tradingos import TsAssistState
        from app.tradingos import assist

        enabled = assist.config(db)["enabled"]
        st_row = db.get(TsAssistState, 1)
        seen = st_row.last_seen_at if st_row else None
        age = _age(now, seen)
        # Vortex is the owner's home PC: away is a warning, never a failure (the bot answers by itself meanwhile)
        status = "off" if not enabled else "ok" if age is not None and age <= assist.SEEN_WINDOW.total_seconds() else "warn"
        out["vortex_assist"] = _c(status, f"last seen {_ago(now, _aware(seen))}", last_seen_age_s=age)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        out["vortex_assist"] = _c("warn", f"couldn't read ({type(exc).__name__})")

    try:
        from app.models.vpn import VpnServer

        servers = list(db.scalars(select(VpnServer).where(VpnServer.enabled.is_(True))))
        online = sum(1 for v in servers if v.online)
        out["vpn_servers"] = _c("off" if not servers else "ok" if online == len(servers) else "fail" if online == 0 else "warn",
                                f"{online} of {len(servers)} online", online=online, total=len(servers))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        out["vpn_servers"] = _c("warn", f"couldn't read ({type(exc).__name__})")

    if watchdog is not None:
        if watchdog._task is None:
            out["watchdog"] = _c("off", "not running on this server")
        else:
            age = _age(now, watchdog.last_pass_at)
            ok = watchdog._task.done() is False and (age is None or age < WATCH_EVERY * 3)
            out["watchdog"] = _c("ok" if ok else "fail", "alerts on errors / slow / stalled jobs" if ok else "stopped",
                                 last_pass_age_s=age)
    if window is not None:
        snap = window.snapshot()
        out["http_errors"] = _c("fail" if ErrorWindow.tripped(snap) else "ok",
                                f"{snap['errors']} server errors in {snap['requests']} requests (5 min)",
                                errors=snap["errors"], requests=snap["requests"], top_routes=snap["routes"])
    return out


def worst(checks: dict[str, dict[str, Any]]) -> str:
    states = {c["status"] for c in checks.values()}
    return "fail" if "fail" in states else "warn" if "warn" in states else "ok"


# --- startup self-check -----------------------------------------------------------------------------------------------

# features that need their job in production's SCHEDULER_ONLY_JOBS once they are in use: (job, in use?, why)
def _features_in_use(db: Session | None) -> list[tuple[str, bool, str]]:
    vpn = False
    owner = False
    if db is not None:
        try:
            from app.models.alerts import OwnerAlertSettings
            from app.models.vpn import VpnServer

            vpn = db.scalar(select(VpnServer.id).where(VpnServer.enabled.is_(True)).limit(1)) is not None
            row = db.get(OwnerAlertSettings, 1)
            owner = bool(row and row.enabled and row.telegram_chat.strip())
        except Exception:  # noqa: BLE001 - a self-check never stops the start
            db.rollback()
    return [
        ("vpn.sync", vpn, "Kiwi VPN servers are enabled"),
        ("tradingbot.affiliate", True, "the Hyperliquid affiliate snapshot"),
        ("system.alerts", owner, "owner alerts are switched on"),
    ]


def startup_self_check(settings: Any, job_names: list[str], db: Session | None = None) -> list[str]:
    """Configuration problems worth knowing at boot. Logs ONE line (warning if any, info if none); never raises."""
    problems: list[str] = []
    try:
        key = settings.app_encryption_key
        if not (key and key.get_secret_value().strip()) and (settings.is_production or not DPAPI):
            problems.append("APP_ENCRYPTION_KEY is not set (stored secrets can't be encrypted here)")
        if settings.is_production and not settings.scheduler_enabled:
            problems.append("SCHEDULER_ENABLED is off in production (no background job runs)")
        if settings.scheduler_only_jobs.strip():
            off = settings.jobs_off(job_names)
            for job, used, why in _features_in_use(db):
                if used and job in job_names and job in off and job not in settings.disabled_jobs:
                    prefix = job.split(".")[0] + "."
                    problems.append(f"SCHEDULER_ONLY_JOBS leaves out {job} although {why} (add '{job}' or '{prefix}')")
        if settings.is_production and not settings.telegram_configured:
            problems.append("no Telegram bot token: owner alerts can't be delivered")
        elif db is not None:
            from app.models.alerts import OwnerAlertSettings

            row = db.get(OwnerAlertSettings, 1)
            if settings.is_production and not (row and row.enabled and row.telegram_chat.strip()):
                problems.append("owner alerts have no Telegram chat (Admin > Settings > Background jobs > Owner alerts): alerts are only logged")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"self-check itself failed ({type(exc).__name__})")
    if problems:
        logger.warning("Startup self-check: %d problem(s): %s", len(problems), "; ".join(problems))
    else:
        logger.info("Startup self-check: configuration OK")
    return problems
