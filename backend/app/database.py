"""Database engine, session factory, and declarative base.

The engine is created by the application factory (``app.main.create_app``)
and stored on ``app.state``, so tests can run the real app against a
temporary database without touching development data.

SQLite is configured per decision D15 (WAL mode + busy timeout) and D24
(foreign key enforcement). The same code targets PostgreSQL later simply by
changing ``STOCKMATE_DATABASE_URL``.
"""

from collections.abc import Iterator
import threading

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Write-intent flag for the CURRENT worker thread (D15/D29). The ledger
# service sets it before its atomic write transaction; the "begin" listener
# below then emits BEGIN IMMEDIATE instead of a deferred BEGIN. Ordinary
# read-only requests never set it, so they never take the SQLite write lock.
_write_intent = threading.local()


def set_write_intent(active: bool) -> None:
    _write_intent.active = active


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
            # Autocommit at the DBAPI level: SQLAlchemy (and our "begin"
            # listener below) controls transactions explicitly.
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")    # readers don't block writers (D15)
            cursor.execute("PRAGMA busy_timeout=30000")  # wait for locks instead of erroring
            cursor.execute("PRAGMA foreign_keys=ON")     # enforce FK constraints (D24)
            cursor.close()

        @event.listens_for(engine, "begin")
        def _begin(connection):
            # D15/D29: write-intent transactions (the ledger service flags the
            # current thread via set_write_intent) acquire the SQLite write
            # lock up front via BEGIN IMMEDIATE — the approved serialization
            # strategy instead of SELECT FOR UPDATE. Ordinary read-only
            # requests get a deferred BEGIN so they never take a write lock.
            if getattr(_write_intent, "active", False):
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                connection.info["stockmate_begin_mode"] = "immediate"
            else:
                connection.exec_driver_sql("BEGIN")
                connection.info["stockmate_begin_mode"] = "deferred"

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
