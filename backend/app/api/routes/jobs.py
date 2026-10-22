from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.community_schemas import JobOut, JobRunOut, JobUpdate
from app.api.deps import DbSession, Runtime
from app.models.routing import ScheduledJob
from app.services.activity import ActivityLogger

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(db: DbSession, runtime: Runtime) -> list[JobOut]:
    scheduler = runtime.scheduler
    assert scheduler is not None
    rows = {r.name: r for r in db.scalars(select(ScheduledJob))}
    out = []
    for name, spec in scheduler.specs.items():
        row = rows.get(name)
        out.append(
            JobOut(
                name=name,
                label=spec.label,
                description=spec.description,
                enabled=row.enabled if row else True,
                interval_seconds=spec.interval_seconds,
                status=row.last_status if row else "never",
                running=scheduler.is_running(name),
                last_run_at=row.last_run_at if row else None,
                last_finished_at=row.last_finished_at if row else None,
                last_duration_ms=row.last_duration_ms if row else None,
                next_run_at=row.next_run_at if row and (row.enabled and scheduler._loop_task is not None) else None,
                last_error=row.last_error if row else None,
                last_result=row.last_result if row else {},
                run_count=row.run_count if row else 0,
                failure_count=row.failure_count if row else 0,
                consecutive_failures=row.consecutive_failures if row else 0,
            )
        )
    return out


@router.post("/{name}/run", response_model=JobRunOut)
async def run_job(name: str, runtime: Runtime) -> JobRunOut:
    scheduler = runtime.scheduler
    if scheduler is None or name not in scheduler.specs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown job")
    outcome = await scheduler.run_job(name, trigger="manual")
    return JobRunOut(name=outcome.name, status=outcome.status, result=outcome.result, error=outcome.error)


@router.patch("/{name}", response_model=JobRunOut)
def update_job(name: str, payload: JobUpdate, db: DbSession, runtime: Runtime) -> JobRunOut:
    row = db.get(ScheduledJob, name)
    if row is None or runtime.scheduler is None or name not in runtime.scheduler.specs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown job")
    row.enabled = payload.enabled
    db.commit()
    ActivityLogger(db).log(
        "job_updated", f"Background job '{runtime.scheduler.specs[name].label}' {'enabled' if payload.enabled else 'paused'}", actor="dashboard"
    )
    return JobRunOut(name=name, status="enabled" if payload.enabled else "disabled", result={}, error=None)
