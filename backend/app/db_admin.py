"""Helpers to run Alembic migrations programmatically.

Used by the seed CLI, the test fixtures, and any future tooling — so there is
exactly one way to bring a database to the current schema version.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[1]


def run_migrations(database_url: str) -> None:
    """Upgrade the database at ``database_url`` to the latest schema (head)."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
