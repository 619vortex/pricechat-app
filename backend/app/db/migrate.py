"""Run Alembic migrations programmatically at startup."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
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


def upgrade_database(database_url: str) -> None:
    engine = build_engine(database_url, enforce_foreign_keys=False)
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
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
