"""A small in-process job scheduler for one machine.

- Jobs run on an interval inside the backend's event loop (no extra services).
- State (last run, next run, last error, failure streak) is persisted in `scheduled_jobs`.
- A job never overlaps itself; failures back off exponentially up to a cap, so a
  broken dependency (Telegram down, model missing) can't cause a tight retry loop.
- A job can raise JobSkipped when there is nothing it can do (e.g. not configured).
"""

import asyncio
import logging
import time
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
        self._running: set[asyncio.Task[JobOutcome]] = set()

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
            db.commit()

    @property
    def busy(self) -> bool:
        """A background job is running (others wait their turn)."""
        return self._serial.locked()

    def is_running(self, name: str) -> bool:
        return self._active == name

    def queue(self) -> dict[str, Any]:
        """What is running now and what is waiting for its turn (labels, for the dashboard)."""
        waiting = [n for n in self.specs if self.is_queued(n)]
        return {
            "running": {"name": self._active, "label": self.specs[self._active].label} if self._active else None,
            "waiting": [{"name": n, "label": self.specs[n].label} for n in waiting],
        }

    def is_queued(self, name: str) -> bool:
        return self._locks[name].locked() and self._active != name

    def request_run(self, name: str, delay_seconds: int = 5) -> None:
        """Bring a job's next run forward (used to chain pipeline stages). Never delays a job."""
        if name not in self.specs or name in self.disabled:
            return
        soon = utcnow() + timedelta(seconds=delay_seconds)
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
            return JobOutcome(name, "busy", {}, "Already running" if self._active == name else "Already queued: it runs after the current background job")
        async with lock, self._serial:
            self._active = name
            try:
                return await self._execute(spec, name, trigger)
            finally:
                self._active = None

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
            try:
                result = await asyncio.wait_for(
                    spec.func(JobContext(db=db, trigger=trigger, now=started_at)), timeout=spec.timeout_seconds
                ) or {}
            except JobSkipped as exc:
                status, error = "skipped", str(exc) or None
                db.rollback()
            except TimeoutError:
                status, error = "failed", f"Timed out after {spec.timeout_seconds}s"
                db.rollback()
            except Exception as exc:  # noqa: BLE001 - a job must never crash the scheduler
                status, error = "failed", redact_text(f"{type(exc).__name__}: {exc}")[:1000]
                db.rollback()
                logger.warning("Job %s failed: %s", name, error, exc_info=not _expected(exc))

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
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
                row.next_run_at = now + timedelta(seconds=max(backoff, spec.interval_seconds))
            else:
                row.consecutive_failures = 0
                row.last_error = error if status == "skipped" else None
                row.next_run_at = now + timedelta(seconds=spec.interval_seconds)
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
            task = asyncio.create_task(self.run_job(name))
            self._running.add(task)
            task.add_done_callback(self._running.discard)
            started.append(name)
        return started

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)

    def start(self) -> None:
        self.sync_jobs()
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        for task in list(self._running):
            task.cancel()
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)


def _expected(exc: Exception) -> bool:
    """Errors we understand don't need a stack trace in the console."""
    from app.providers.errors import ProviderError
    from app.telegram.errors import TelegramError

    return isinstance(exc, (ProviderError, TelegramError))
