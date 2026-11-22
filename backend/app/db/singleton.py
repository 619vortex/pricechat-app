"""Singleton settings rows (the one row with id 1), created race-safely.

Production found on a fresh database (2026-11-22): the first admin page load fires several requests at once, each found no
settings row and inserted id 1, and all but one failed with "UNIQUE constraint failed: <table>.id" (HTTP 500 on
/api/system/alerts, /api/terminal, /api/vpn/admin/overview, /api/x/newsdesk, /api/x/newsdesk/network).

get_or_create() reads the row; when it is missing it inserts it inside a SAVEPOINT. If another request inserted it first, only
that savepoint is rolled back and the winner's row is read and returned: the caller's transaction and its other changes stay.
"""
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

T = TypeVar("T")

SINGLETON_ID = 1


def get_or_create(db: Session, model: type[T], make: Callable[[], T]) -> T:
    """The model's row with id 1, created by make() (which must set id=1 and the table's defaults) if it doesn't exist yet."""
    row = db.get(model, SINGLETON_ID)
    if row is not None:
        return row
    # the caller's own pending changes go to the database first, so a lost race below rolls back only this one insert
    db.flush()
    try:
        with db.begin_nested():
            row = make()
            db.add(row)
    except IntegrityError:
        # another request created it a moment ago: use that one
        row = db.get(model, SINGLETON_ID, populate_existing=True)
        if row is None:
            raise
    return row
