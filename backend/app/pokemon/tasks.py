"""Long admin actions (syncs, imports, re-checks) as background tasks.

Requests through Vercel's proxy and Railway's edge are cut off after a few minutes, while a catalogue sync or a sales
re-check can take longer. So these actions start a task here and return at once; the dashboard polls the task's status
and shows the same result when it is done. One task per name at a time (a second start returns the running one).
Results live in memory only: the work itself (imports, sync logs) is in the database.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_tasks: dict[str, dict[str, Any]] = {}
_handles: set[asyncio.Task[Any]] = set()  # keep references: asyncio only holds weak ones


def status(name: str) -> dict[str, Any]:
    return dict(_tasks.get(name) or {"name": name, "state": "idle"})


def start(name: str, work: Callable[[Session], Any], bind: Any, blocking: bool = False) -> dict[str, Any]:
    """Run `work(db)` in the background with a database session of its own (bound like the caller's). `blocking=True`
    for plain (non-async) work: it runs in a worker thread, with the session created there."""
    cur = _tasks.get(name)
    if cur and cur["state"] == "running":
        return status(name)
    _tasks[name] = {"name": name, "state": "running", "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
                    "result": None, "error": None}

    async def run() -> None:
        try:
            if blocking:
                def in_thread() -> Any:
                    with Session(bind=bind) as db:
                        return work(db)

                result = await asyncio.to_thread(in_thread)
            else:
                with Session(bind=bind) as db:
                    result = await work(db)
            _tasks[name].update(state="done", result=result)
        except Exception as exc:  # noqa: BLE001 - the task reports its failure instead of vanishing
            logger.warning("Background task %s failed", name, exc_info=True)
            detail = getattr(exc, "reason", None) or getattr(exc, "detail", None) or type(exc).__name__
            _tasks[name].update(state="error", error=str(detail)[:300])
        finally:
            _tasks[name]["finished_at"] = datetime.now(timezone.utc).isoformat()
        # a sync / re-check clears the market caches: rebuild the homepage now, not on the next visitor's request
        from app.config.settings import get_settings

        if get_settings().is_production:
            from app.pokemon import market

            try:
                await asyncio.to_thread(market.warm_homepage, bind)
            except Exception:  # noqa: BLE001 - only means the next request builds it
                logger.warning("Re-warming the market after %s failed", name, exc_info=True)

    t = asyncio.get_running_loop().create_task(run())
    _handles.add(t)
    t.add_done_callback(_handles.discard)
    return status(name)
