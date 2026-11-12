"""A small in-process job scheduler for one machine.

- Jobs run on an interval inside the backend's event loop (no extra services).
- State (last run, next run, last error, failure streak) is persisted in `scheduled_jobs`.
- A job never overlaps itself; failures back off exponentially up to a cap, so a
  broken dependency (Telegram down, model missing) can't cause a tight retry loop.
- A job can raise JobSkipped when there is nothing it can do (e.g. not configured).
"""

import asyncio
import logging
import statistics
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utcnow
from app.models.enums import ActivityLevel
from app.models.routing import ScheduledJob
from app.services.activity import ActivityLogger, redact_text

logger = logging.getLogger(__name__)

# Slow-run detection (read by the ops watchdog, app/observability.py): a successful run taking > SLOW_FACTOR x the job's
# median of its last DURATION_HISTORY successful runs (at least SLOW_MIN_SAMPLES of them, and >= SLOW_MIN_MS so a 20 ms
# job taking 80 ms is not news), SLOW_RUNS times in a row. In memory only: a restart re-learns the median.
DURATION_HISTORY, SLOW_MIN_SAMPLES, SLOW_FACTOR, SLOW_RUNS, SLOW_MIN_MS = 20, 5, 3.0, 3, 2000.0
LISTENER_WAIT_LOG_S = 30.0  # telegram.ingest waiting longer than this for its turn is logged (the bot is silent meanwhile)
LANE_IDLE_S, LANE_CRASH_BACKOFF_MAX = 0.2, 60.0  # an own-lane job's loop: shortest sleep, and its longest back-off after a crash


class JobSkipped(Exception):
    """Raised by a job that has nothing to do right now (not a failure)."""


@dataclass(slots=True)
class JobContext:
    db: Session
    trigger: str  # "schedule" | "manual"
    now: datetime
    state: dict[str, Any] = field(default_factory=dict)


JobFunc = Callable[[JobContext], Awaitable[dict[str, Any]]]
# Called after each recorded success / failure: (spec, status, error, failures in a row before, after). Owner alerts (app/alerts.py).
JobAlert = Callable[["JobSpec", str, str | None, int, int], Awaitable[Any]]


@dataclass(slots=True)
class JobSpec:
    name: str
    label: str
    description: str
    interval_seconds: int
    func: JobFunc
    initial_delay_seconds: int = 15
    timeout_seconds: int = 900
    max_backoff_seconds: int = 1800
    # Turns a successful result into one plain-English activity line (event, message), or None.
    describe: Callable[[dict[str, Any]], tuple[str, str] | None] | None = None
    # Runs in its own always-on loop, outside the one-at-a-time lane (AR-3 / TG-2): the Telegram listener, so bot replies
    # never wait behind a backup or a News Desk run. Such a job must keep its write transactions short (it shares SQLite
    # with whatever job holds the lane).
    own_lane: bool = False
    # A daily job whose failure shouldn't wait a day (the nightly backup): retried this many seconds after a failure, and
    # again `initial_delay_seconds` after a restart while its last run failed. 2026-11-12: 21 failed backups in a row, and
    # after the fix the next attempt was still up to a day away because next_run_at survives restarts.
    retry_failed_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class JobOutcome:
    name: str
    status: str  # success | failed | skipped | busy | disabled
    result: dict[str, Any]
    error: str | None = None


class Scheduler:
    def __init__(self, session_factory: sessionmaker[Session], specs: list[JobSpec], tick_seconds: float = 5.0,
                 disabled: set[str] | None = None, alert: JobAlert | None = None) -> None:
        self.session_factory = session_factory
        self.alert = alert
        # jobs that must never run in THIS process (SCHEDULER_DISABLED_JOBS), e.g. Telegram reading on a laptop while
        # production reads the same bot: neither on schedule, nor chained, nor by hand
        self.disabled = set(disabled or ())
        self.specs = {s.name: s for s in specs}
        self.tick_seconds = tick_seconds
        self._locks = {name: asyncio.Lock() for name in self.specs}
        # One job at a time: model calls are serialised by the AI gate anyway, and this keeps
        # jobs from holding competing SQLite write transactions.
        self._serial = asyncio.Lock()
        self._active: str | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self.started_at: datetime | None = None  # when the loop started (the public health check's grace period)
        self._running: set[asyncio.Task[JobOutcome]] = set()
        # request_run() for a job while it runs (a stage chaining itself: "more may be waiting"): applied when it finishes
        self._chained: dict[str, datetime] = {}
        self.last_tick_at: datetime | None = None  # the loop's last pass (deep health: is the scheduler alive?)
        self.durations: dict[str, deque[float]] = {}  # recent successful run times (ms) per job
        self.slow: dict[str, dict[str, float]] = {}  # job -> {"streak", "last_ms", "median_ms"} while its runs are slow
        self._lanes: dict[str, asyncio.Task[None]] = {}  # own-lane jobs' loops (started with the scheduler)
        self._lane_running: set[str] = set()  # own-lane jobs running right now (they never take the one-at-a-time lane)

    def note_duration(self, name: str, duration_ms: float) -> None:
        """Remember a successful run's time and keep the slow-run streak (SLOW_FACTOR x the recent median)."""
        hist = self.durations.setdefault(name, deque(maxlen=DURATION_HISTORY))
        if len(hist) >= SLOW_MIN_SAMPLES:
            median = statistics.median(hist)
            if duration_ms >= SLOW_MIN_MS and duration_ms > SLOW_FACTOR * median:
                prev = self.slow.get(name, {}).get("streak", 0)
                self.slow[name] = {"streak": prev + 1, "last_ms": duration_ms, "median_ms": median}
            else:
                self.slow.pop(name, None)
        hist.append(duration_ms)

    def slow_jobs(self) -> dict[str, dict[str, float]]:
        """Jobs whose last SLOW_RUNS (or more) successful runs were all slow."""
        return {n: v for n, v in self.slow.items() if v["streak"] >= SLOW_RUNS}

    # --- state ------------------------------------------------------------------------

    def sync_jobs(self) -> None:
        """Create rows for new jobs and apply interval changes from configuration."""
        now = utcnow()
        with self.session_factory() as db:
            for spec in self.specs.values():
                row = db.get(ScheduledJob, spec.name)
                if row is None:
                    db.add(
                        ScheduledJob(
                            name=spec.name,
                            interval_seconds=spec.interval_seconds,
                            next_run_at=now + timedelta(seconds=spec.initial_delay_seconds),
                        )
                    )
                else:
                    row.interval_seconds = spec.interval_seconds
                    if row.last_status == "running":
                        row.last_status = "failed"  # interrupted by a restart
                        row.last_error = "Interrupted by a restart"
                    if row.next_run_at is None or row.next_run_at < now:
                        row.next_run_at = now + timedelta(seconds=spec.initial_delay_seconds)
                    elif spec.retry_failed_seconds and row.last_status == "failed" and row.consecutive_failures > 0:
                        row.next_run_at = min(row.next_run_at, now + timedelta(seconds=spec.initial_delay_seconds))
            db.commit()

    @property
    def busy(self) -> bool:
        """A background job is running (others wait their turn)."""
        return self._serial.locked()

    def is_running(self, name: str) -> bool:
        return self._active == name or name in self._lane_running

    def queue(self) -> dict[str, Any]:
        """What is running now and what is waiting for its turn (labels, for the dashboard)."""
        waiting = [n for n in self.specs if self.is_queued(n)]
        return {
            "running": {"name": self._active, "label": self.specs[self._active].label} if self._active else None,
            "waiting": [{"name": n, "label": self.specs[n].label} for n in waiting],
        }

    def is_queued(self, name: str) -> bool:
        return self._locks[name].locked() and not self.is_running(name)

    def request_run(self, name: str, delay_seconds: int = 5) -> None:
        """Bring a job's next run forward (used to chain pipeline stages). Never delays a job."""
        if name not in self.specs or name in self.disabled:
            return
        soon = utcnow() + timedelta(seconds=delay_seconds)
        if self.is_running(name):  # its row is rewritten when this run ends: remember the request until then
            self._chained[name] = min(soon, self._chained.get(name, soon))
            return
        with self.session_factory() as db:
            row = db.get(ScheduledJob, name)
            if row is not None and row.enabled and (row.next_run_at is None or row.next_run_at > soon):
                row.next_run_at = soon
                db.commit()

    # --- execution ------------------------------------------------------------------

    async def run_job(self, name: str, trigger: str = "schedule") -> JobOutcome:
        spec = self.specs[name]
        if name in self.disabled:
            return JobOutcome(name, "disabled", {}, "Turned off on this server (SCHEDULER_DISABLED_JOBS)")
        lock = self._locks[name]
        if lock.locked():
            return JobOutcome(name, "busy", {}, "Already running" if self.is_running(name) else "Already queued: it runs after the current background job")
        if spec.own_lane:  # never waits for the one-at-a-time lane (AR-3 / TG-2)
            async with lock:
                self._lane_running.add(name)
                try:
                    return await self._execute(spec, name, trigger)
                finally:
                    self._lane_running.discard(name)
        async with lock:
            asked = time.perf_counter()
            ahead = self._active
            await self._serial.acquire()
            waited = time.perf_counter() - asked
            if name == "telegram.ingest" and waited >= LISTENER_WAIT_LOG_S:  # the bot answers nothing while it waits its turn
                logger.warning("Telegram listener waited %.0fs for its turn (behind %s)", waited, ahead or "other jobs")
            self._active = name
            try:
                return await self._execute(spec, name, trigger)
            finally:
                self._active = None
                self._serial.release()

    def _begin(self, name: str, trigger: str) -> datetime | None:
        """Mark the job running. Returns None when it is disabled."""
        with self.session_factory() as db:
            row = db.get(ScheduledJob, name)
            if row is None:
                raise KeyError(name)
            if not row.enabled and trigger != "manual":
                return None
            started_at = utcnow()
            row.last_status = "running"
            row.last_run_at = started_at
            db.commit()
            return started_at

    async def _execute(self, spec: JobSpec, name: str, trigger: str) -> JobOutcome:
        # Bookkeeping runs in a worker thread: if SQLite has to wait for a lock, the event loop
        # (and with it the dashboard and whatever holds the lock) keeps running.
        started_at = await asyncio.to_thread(self._begin, name, trigger)
        if started_at is None:
            return JobOutcome(name, "disabled", {})

        started = time.perf_counter()
        status, result, error = "success", {}, None
        with self.session_factory() as db:
            limit = asyncio.timeout(spec.timeout_seconds)
            try:
                async with limit:
                    result = await spec.func(JobContext(db=db, trigger=trigger, now=started_at)) or {}
            except JobSkipped as exc:
                status, error = "skipped", str(exc) or None
                db.rollback()
            except TimeoutError as exc:
                # only the scheduler's own limit is "timed out"; a TimeoutError the job raised itself (a socket, its own
                # wait_for) is an ordinary failure with its own message
                status, error = "failed", (f"Timed out after {spec.timeout_seconds}s" if limit.expired()
                                           else redact_text(f"{type(exc).__name__}: {exc}")[:1000])
                db.rollback()
                logger.warning("Job %s failed: %s", name, error)
            except Exception as exc:  # noqa: BLE001 - a job must never crash the scheduler
                status, error = "failed", redact_text(f"{type(exc).__name__}: {exc}")[:1000]
                db.rollback()
                logger.warning("Job %s failed: %s", name, error, exc_info=not _expected(exc))

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        if status == "success":
            self.note_duration(name, duration_ms)
        previous, failures = await asyncio.to_thread(self._record, spec, status, result, error, duration_ms, trigger)
        if self.alert is not None and status in ("success", "failed"):
            try:
                await self.alert(spec, status, error, previous, failures)
            except Exception:  # noqa: BLE001 - an alert must never break the scheduler
                logger.warning("Owner alert for job %s failed", name, exc_info=True)
        return JobOutcome(name, status, result, error)

    def _record(self, spec: JobSpec, status: str, result: dict[str, Any], error: str | None, duration_ms: float, trigger: str) -> tuple[int, int]:
        from app.services import usage

        usage.flush(self.session_factory)  # model-call / request tallies from this job (own short transaction)
        now = utcnow()
        chained = self._chained.pop(spec.name, None)
        with self.session_factory() as db:
            row = db.get(ScheduledJob, spec.name)
            assert row is not None
            previous_failures = row.consecutive_failures
            row.last_status = status
            row.last_finished_at = now
            row.last_duration_ms = duration_ms
            row.last_result = result if status == "success" else {}
            row.run_count += 1
            if status == "failed":
                row.failure_count += 1
                row.consecutive_failures += 1
                row.last_error = error
                backoff = min(spec.interval_seconds * 2 ** (row.consecutive_failures - 1), spec.max_backoff_seconds)
                row.next_run_at = now + timedelta(seconds=spec.retry_failed_seconds or max(backoff, spec.interval_seconds))
            else:
                row.consecutive_failures = 0
                row.last_error = error if status == "skipped" else None
                row.next_run_at = now + timedelta(seconds=spec.interval_seconds)
                if chained is not None and row.enabled:
                    row.next_run_at = min(row.next_run_at, max(chained, now))
            failures = row.consecutive_failures
            db.commit()

            activity = ActivityLogger(db)
            if status == "failed" and previous_failures == 0:
                activity.log(
                    "job_failed",
                    f"Background job '{spec.label}' failed: {error}. It will retry with increasing delays.",
                    level=ActivityLevel.ERROR,
                    details={"job": spec.name, "trigger": trigger},
                )
            elif status == "success" and previous_failures > 0:
                activity.log("job_recovered", f"Background job '{spec.label}' is working again", details={"job": spec.name})
            if status == "success" and spec.describe is not None:
                try:
                    line = spec.describe(result)
                except Exception:  # noqa: BLE001 - a summary must never break bookkeeping
                    line = None
                if line:
                    activity.log(line[0], line[1], details={"job": spec.name, "duration_ms": duration_ms})
            return previous_failures, failures

    # --- loop -------------------------------------------------------------------------

    async def tick(self) -> list[str]:
        """Start every enabled job that is due. Returns the names started."""
        now = utcnow()
        with self.session_factory() as db:
            due = [
                row.name
                for row in db.query(ScheduledJob).filter(ScheduledJob.enabled.is_(True))
                if row.name in self.specs and row.name not in self.disabled and (row.next_run_at is None or row.next_run_at <= now)
            ]
        started = []
        for name in due:
            if self._locks[name].locked():  # running or waiting for its turn
                continue
            if name in self._lanes and not self._lanes[name].done():  # its own loop starts it
                continue
            task = asyncio.create_task(self.run_job(name))
            self._running.add(task)
            task.add_done_callback(self._running.discard)
            started.append(name)
        return started

    def _due_in(self, name: str) -> float | None:
        """Seconds until an own-lane job is due (<= 0: now), or None while it is switched off."""
        with self.session_factory() as db:
            row = db.get(ScheduledJob, name)
            if row is None or not row.enabled:
                return None
            return 0.0 if row.next_run_at is None else (row.next_run_at - utcnow()).total_seconds()

    async def _lane(self, name: str) -> None:
        """An own-lane job's always-on loop: runs it whenever it is due (a listener chaining itself runs back to back). A
        failed run backs off through its row like any job; an error in this loop itself is logged and retried with a growing
        pause, and _ensure_lanes restarts the loop if it ever ends."""
        pause = 0.0
        while True:
            try:
                due_in = self._due_in(name)
                if due_in is not None and due_in <= 0 and not self._locks[name].locked():
                    outcome = await self.run_job(name)
                    pause = 0.0
                    if outcome.status not in ("disabled", "busy"):  # (those leave its row as it was: don't spin on it)
                        continue
                    due_in = None
                await asyncio.sleep(self.tick_seconds if due_in is None else min(self.tick_seconds, max(due_in, LANE_IDLE_S)))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive any one error (a locked database, a bad row)
                pause = min(max(pause * 2, 1.0), LANE_CRASH_BACKOFF_MAX)
                logger.exception("Job %s's own loop failed; retrying in %.0fs", name, pause)
                await asyncio.sleep(pause)

    def _ensure_lanes(self) -> None:
        """Start (or restart, if one ever ended) the own-lane jobs' loops."""
        for name, spec in self.specs.items():
            if not spec.own_lane or name in self.disabled:
                continue
            task = self._lanes.get(name)
            if task is not None and not task.done():
                continue
            if task is not None:
                exc = task.exception() if not task.cancelled() else None
                logger.warning("Job %s's own loop stopped (%s); restarting it", name, type(exc).__name__ if exc else "cancelled")
            self._lanes[name] = asyncio.create_task(self._lane(name))

    async def _loop(self) -> None:
        while True:
            try:
                self._ensure_lanes()
                await self.tick()
                self.last_tick_at = utcnow()
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)

    def start(self) -> None:
        self.sync_jobs()
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop())
            self.started_at = utcnow()

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        for task in self._lanes.values():
            task.cancel()
        if self._lanes:
            await asyncio.gather(*self._lanes.values(), return_exceptions=True)
            self._lanes = {}
        for task in list(self._running):
            task.cancel()
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)


def _expected(exc: Exception) -> bool:
    """Errors we understand don't need a stack trace in the console."""
    from app.providers.errors import ProviderError
    from app.telegram.errors import TelegramError

    return isinstance(exc, (ProviderError, TelegramError))
