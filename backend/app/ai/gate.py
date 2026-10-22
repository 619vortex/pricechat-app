"""Priority gate for model calls on one workstation.

Interactive requests (dashboard chat) never wait. Background work (summaries,
extraction, opportunity reasoning...) runs one call at a time and holds back
while any interactive request is in flight, so a 30-second strong-model job
can't make the chat feel frozen.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class AIGate:
    def __init__(self, max_background: int = 1, max_wait_seconds: float = 120.0) -> None:
        self._interactive = 0
        self._idle: asyncio.Event | None = None
        self._bg: asyncio.Semaphore | None = None
        self._max_background = max_background
        self.max_wait_seconds = max_wait_seconds
        self.background_task: str | None = None
        self.background_started: float | None = None
        self.waiting = 0

    def _ensure(self) -> None:
        if self._idle is None:
            self._idle = asyncio.Event()
            self._idle.set()
            self._bg = asyncio.Semaphore(self._max_background)

    @asynccontextmanager
    async def interactive(self) -> AsyncIterator[None]:
        self._ensure()
        assert self._idle is not None
        self._interactive += 1
        self._idle.clear()
        try:
            yield
        finally:
            self._interactive -= 1
            if self._interactive == 0:
                self._idle.set()

    @asynccontextmanager
    async def background(self, task: str) -> AsyncIterator[None]:
        self._ensure()
        assert self._idle is not None and self._bg is not None
        self.waiting += 1
        waiting = True
        try:
            async with self._bg:
                try:
                    # Yield to interactive work, but never starve background jobs forever.
                    await asyncio.wait_for(self._idle.wait(), timeout=self.max_wait_seconds)
                except TimeoutError:
                    pass
                self.waiting -= 1
                waiting = False
                self.background_task, self.background_started = task, time.monotonic()
                try:
                    yield
                finally:
                    self.background_task, self.background_started = None, None
        finally:
            if waiting:  # cancelled (e.g. a job timeout) while still queued
                self.waiting -= 1

    def status(self) -> dict[str, object]:
        return {
            "interactive_active": self._interactive,
            "background_task": self.background_task,
            "background_seconds": round(time.monotonic() - self.background_started, 1) if self.background_started else None,
            "background_waiting": max(0, self.waiting),
        }
