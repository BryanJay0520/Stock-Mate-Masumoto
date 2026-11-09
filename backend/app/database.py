"""Database engine, session factory, and declarative base.

The engine is created by the application factory (``app.main.create_app``)
and stored on ``app.state``, so tests can run the real app against a
temporary database without touching development data.

SQLite is configured per decision D15 (WAL mode + busy timeout) and D24
(foreign key enforcement). The same code targets PostgreSQL later simply by
changing ``STOCKMATE_DATABASE_URL``.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models (populated from Step 2)."""


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def create_db_engine(database_url: str) -> Engine:
    """Create an engine with the project's standard pragmas.

    - ``check_same_thread=False`` lets FastAPI worker threads share connections.
    - ``timeout`` is SQLite's busy timeout: a writer waits up to 30 s for the
      lock instead of failing immediately under concurrent stock entries.
    """
    connect_args = {}
    if _is_sqlite(database_url):
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(database_url, connect_args=connect_args)

    if _is_sqlite(database_url):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")    # readers don't block writers (D15)
            cursor.execute("PRAGMA busy_timeout=30000")  # wait for locks instead of erroring
            cursor.execute("PRAGMA foreign_keys=ON")     # enforce FK constraints (D24)
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding one session per request."""
    session_factory = request.app.state.session_factory
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
