import asyncio
import logging
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from app.api.deps import DbSession, Runtime, provider_status
from app.api.schemas import HealthOut
from app.db.base import utcnow
from app.security import rate_limit
from app.services.agent_service import get_agent_config

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthOut)
async def health(request: Request, db: DbSession, runtime: Runtime) -> HealthOut:
    """The owner's detailed check: database, AI model, and every subsystem's ok / warn / fail (app/observability.py)."""
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("Database health check failed")
        db_ok = False

    model = get_agent_config(db).model_name if db_ok else None
    llm = await runtime.llm_health(model)
    from app import observability

    checks = observability.deep_checks(db, runtime, getattr(request.app.state, "watchdog", None),
                                       getattr(request.app.state, "http_errors", None))
    overall = observability.worst(checks)
    return HealthOut(
        status="ok" if db_ok and overall != "fail" else "degraded",
        app=runtime.settings.app_name,
        environment=runtime.settings.environment,
        database=db_ok,
        llm=provider_status(llm),
        checks=checks,
        checks_status=overall,
    )


@router.get("/health/live")
def health_live(db: DbSession) -> dict[str, object]:
    """Public liveness probe (Railway's health check): the database only, nothing about the setup, no AI call."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": True}
    except Exception:
        logger.exception("Database health check failed")
        return {"status": "degraded", "database": False}


# --- public health check for uptime monitors (GET/HEAD /api/health/public) --------------------------------------------
# ok/error words only: no job names, errors, counts, settings. One answer is shared for a few seconds, so a flood of
# monitors (or anyone) costs at most one SELECT 1 per CACHE_SECONDS.
DB_TIMEOUT, CACHE_SECONDS, STALE = 3.0, 5.0, timedelta(minutes=30)
_cache: dict[str, Any] = {"at": float("-inf"), "body": None}


def _probe(scheduler: Any) -> tuple[bool, str, dict[str, str]]:
    """(database answers SELECT 1, scheduler ok|stale|off, {"jobs": ok|failing|off, "backup": ok|old|off}). Runs in a worker
    thread. jobs "failing": an enabled job failed 3+ runs in a row; backup "old": no successful backup for 26 h (AR-19). They
    don't turn the answer into a 503: a monitor can watch the words, and a dead process still reads as down."""
    from app.db.session import SessionLocal
    from app.models.routing import ScheduledJob

    state = "off" if scheduler is None or scheduler._loop_task is None else "stale" if scheduler._loop_task.done() else "ok"
    words = {"jobs": "off", "backup": "off"}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            now = utcnow()
            if state == "ok" and not (scheduler.started_at and now - scheduler.started_at < STALE):  # past the start-up grace
                names = [n for n in scheduler.specs if n not in scheduler.disabled]
                last = db.scalar(select(func.max(ScheduledJob.last_run_at)).where(ScheduledJob.enabled.is_(True), ScheduledJob.name.in_(names)))
                if last is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=now.tzinfo)
                state = "ok" if last and now - last < STALE else "stale"
            if scheduler is not None and scheduler._loop_task is not None:
                from app import observability

                for m in observability.silence_monitors(db, scheduler, now):
                    if m["key"] == "failing_jobs":
                        words["jobs"] = {"alert": "failing", "ok": "ok"}.get(m["status"], "off")
                    elif m["key"] == "backup":
                        words["backup"] = {"alert": "old", "ok": "ok"}.get(m["status"], "off")
        return True, state, words
    except Exception:  # noqa: BLE001 - reported as "error", logged privately
        logger.warning("Public health check: database unreachable", exc_info=True)
        return False, state, words


BUILD_ID_FILE = Path(__file__).resolve().parents[3] / "BUILD_ID"  # backend/BUILD_ID, written at build / deploy time (optional)


def _version() -> str:
    """Which build is live (AR-8): Railway's commit SHA (git deploys), BUILD_ID (env, or the backend/BUILD_ID file written
    before a `railway up`), else Railway's deployment id; "unknown" when none is set."""
    build = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("BUILD_ID") or "").strip()
    if not build:
        try:
            build = BUILD_ID_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip() if BUILD_ID_FILE.is_file() else ""
        except (OSError, IndexError, UnicodeDecodeError):
            build = ""
    if build:
        return build[:7] if all(c in "0123456789abcdefABCDEF" for c in build) else build[:40]  # a commit SHA, or a free-form id
    deploy = os.environ.get("RAILWAY_DEPLOYMENT_ID", "").strip()
    return f"deploy-{deploy[:8]}" if deploy else "unknown"


@router.api_route("/health/public", methods=["GET", "HEAD"], dependencies=[Depends(rate_limit.limit("health"))])
async def health_public(runtime: Runtime) -> JSONResponse:
    """Public uptime check: 200 when the database answers and background jobs are running, else 503."""
    if _cache["body"] is None or time.monotonic() - _cache["at"] > CACHE_SECONDS:
        try:
            db_ok, sched, words = await asyncio.wait_for(asyncio.to_thread(_probe, runtime.scheduler), DB_TIMEOUT)
        except TimeoutError:
            db_ok, sched = False, "off" if runtime.scheduler is None or runtime.scheduler._loop_task is None else "ok"
            words = {"jobs": "off", "backup": "off"}
        ok = db_ok and sched != "stale"
        _cache.update(at=time.monotonic(), body={"ok": ok, "db": "ok" if db_ok else "error", "scheduler": sched, **words,
                                                 "version": _version()})
    body = {**_cache["body"], "time": utcnow().isoformat()}
    return JSONResponse(body, status_code=200 if body["ok"] else 503, headers={"Cache-Control": "no-store"})
