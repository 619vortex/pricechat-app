"""AUDIT (2026-11-08, AR-3): every background job shares ONE asyncio lock (Scheduler._serial). A long-running job (here a
stand-in for telegram.ingest's 90 s listen, or pokemon.sync) delays a 20-second job (tradingos.vortex_expire, whose contract
is "answer within 60 s") by the full length of the long job; and a job doing synchronous work on the event loop also
overruns its own timeout because asyncio.timeout can only fire at an await. Asserts CURRENT behaviour."""

import asyncio
import time

import pytest

from app.db.session import SessionLocal
from app.scheduler.scheduler import JobSpec, Scheduler


@pytest.mark.asyncio
async def test_a_long_job_starves_a_fast_one(db) -> None:  # type: ignore[no-untyped-def]
    async def listen(ctx):  # type: ignore[no-untyped-def]
        await asyncio.sleep(1.0)
        return {}

    async def fast(ctx):  # type: ignore[no-untyped-def]
        return {}

    def blocking(ctx):  # type: ignore[no-untyped-def]
        time.sleep(0.6)  # synchronous DB/CPU work inside an async job: the event loop is blocked
        return {}

    async def sync_heavy(ctx):  # type: ignore[no-untyped-def]
        return blocking(ctx)

    specs = [JobSpec("t.listen", "L", "", 20, listen, 0, 150), JobSpec("t.fast", "F", "", 20, fast, 0, 18),
             JobSpec("t.sync", "S", "", 20, sync_heavy, 0, timeout_seconds=0.1)]  # type: ignore[arg-type]
    sch = Scheduler(SessionLocal, specs)
    sch.sync_jobs()
    long_task = asyncio.create_task(sch.run_job("t.listen"))
    await asyncio.sleep(0.05)
    t0 = time.perf_counter()
    out = await sch.run_job("t.fast")
    waited = time.perf_counter() - t0
    await long_task
    assert out.status == "success" and waited >= 0.9           # the fast job waited for the whole long job

    t1 = time.perf_counter()
    out = await sch.run_job("t.sync")
    took = time.perf_counter() - t1
    assert took >= 0.6                                           # a 0.1 s timeout could not stop 0.6 s of blocking work
    assert out.status == "success"                               # ...and it is even recorded as a success
