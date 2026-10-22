import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def build_engine(database_url: str, enforce_foreign_keys: bool = True, **kwargs: Any) -> Engine:
    """Create an engine. Migrations pass enforce_foreign_keys=False: SQLite rebuilds
    tables to alter them, and cascading FKs would otherwise delete dependent rows."""
    url = make_url(database_url)
    is_sqlite = url.get_backend_name() == "sqlite"
    if is_sqlite:
        kwargs.setdefault("connect_args", {"check_same_thread": False})
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, **kwargs)

    if is_sqlite:
        pragma = "PRAGMA foreign_keys=ON" if enforce_foreign_keys else "PRAGMA foreign_keys=OFF"

        file_db = bool(url.database and url.database != ":memory:")

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute(pragma)
            # Background jobs and the dashboard write concurrently: WAL lets readers proceed during a
            # write, and a writer waits (instead of failing) while another one finishes.
            cursor.execute("PRAGMA busy_timeout=15000")
            if file_db:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Restore a shipped first-deploy snapshot if the volume is empty, then bring the schema to the latest migration."""
    from app.db.bootstrap import restore_if_empty
    from app.db.migrate import upgrade_database

    restore_if_empty(get_settings())
    upgrade_database(get_settings().database_url)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
