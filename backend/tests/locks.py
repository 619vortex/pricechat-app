"""Test helper: catch an outgoing HTTP call made while a write transaction is open on the test database.

On SQLite a write transaction holds the database's single write lock: every other writer (requests, jobs) waits for it.
So nothing may be awaiting a network answer while one is open."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from sqlalchemy.engine import make_url


def write_lock_held() -> bool:
    from app.config.settings import get_settings

    c = sqlite3.connect(make_url(get_settings().database_url).database, timeout=0)
    try:
        c.execute("BEGIN IMMEDIATE")
        c.rollback()
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        c.close()


@contextmanager
def http_calls_under_write_lock() -> Iterator[list[str]]:
    """Yields the list of HTTP calls (method host/path) that went out while the write lock was held."""
    held: list[str] = []
    calls: list[str] = []
    orig_async, orig_sync = httpx.AsyncClient.send, httpx.Client.send

    def note(request: httpx.Request) -> None:
        if request.url.host == "testserver":
            return
        what = f"{request.method} {request.url.host}{request.url.path}"
        calls.append(what)
        if write_lock_held():
            held.append(what)

    async def asend(self, request, *a, **k):  # type: ignore[no-untyped-def]
        note(request)
        return await orig_async(self, request, *a, **k)

    def ssend(self, request, *a, **k):  # type: ignore[no-untyped-def]
        note(request)
        return orig_sync(self, request, *a, **k)

    httpx.AsyncClient.send, httpx.Client.send = asend, ssend  # type: ignore[method-assign]
    try:
        yield held
    finally:
        httpx.AsyncClient.send, httpx.Client.send = orig_async, orig_sync  # type: ignore[method-assign]
    assert calls, "no HTTP call was made: the check proved nothing"
