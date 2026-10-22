"""Run Alembic migrations programmatically at startup."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.db.session import build_engine

logger = logging.getLogger(__name__)

# backend/alembic.ini, wherever the backend folder is deployed (Railway deploys only backend/, as /app)
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
PHASE_1_REVISION = "0001"


def alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    return config


_HEADS: tuple[str, ...] | None = None


def _script_heads() -> tuple[str, ...]:
    """The migration heads, read once per process: building the script directory imports every revision file
    (~0.5 s), and the migrations shipped with this process never change while it runs."""
    global _HEADS
    if _HEADS is None:
        _HEADS = tuple(sorted(ScriptDirectory.from_config(alembic_config()).get_heads()))
    return _HEADS


def upgrade_database(database_url: str) -> None:
    engine = build_engine(database_url, enforce_foreign_keys=False)
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            if "alembic_version" in tables:
                current = tuple(sorted(MigrationContext.configure(connection).get_current_heads()))
                if current == _script_heads():  # already at head: nothing to run (skips loading every revision)
                    return
            config = alembic_config()
            config.attributes["connection"] = connection
            # Phase 1 databases were created with create_all() and have no version table.
            if "agent_configs" in tables and "alembic_version" not in tables:
                logger.info("Existing Phase 1 database detected; marking it as revision %s", PHASE_1_REVISION)
                command.stamp(config, PHASE_1_REVISION)
            command.upgrade(config, "head")
        logger.info("Database schema is up to date")
    finally:
        engine.dispose()
