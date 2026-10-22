from alembic import context
from sqlalchemy import Connection

import app.models  # noqa: F401  (registers tables on Base.metadata)
from app.config.settings import get_settings
from app.db.base import Base, UTCDateTime
from app.db.session import build_engine

target_metadata = Base.metadata


def _render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    # Render the UTCDateTime decorator as its plain column type so migrations don't import app code.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Batch mode lets SQLite drop/alter columns; harmless on PostgreSQL.
        render_as_batch=True,
        compare_type=True,
        render_item=_render_item,
    )


def run_migrations_online() -> None:
    # app.db.migrate passes an open connection; the CLI falls back to settings.
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
    engine = build_engine(url, enforce_foreign_keys=False)
    with engine.connect() as conn:
        _configure(conn)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
run_migrations_online()
