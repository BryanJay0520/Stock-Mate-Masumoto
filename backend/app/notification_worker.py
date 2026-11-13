"""Dedicated in-app notification worker. Launch explicitly after migrations.

    cd backend
    /home/bryanjay/.venvs/stockmate/bin/python -m app.notification_worker

The FastAPI application never starts this loop. SIGINT/SIGTERM release the lease.
"""

import argparse
import logging
import signal
import threading
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import create_db_engine, create_session_factory
from app.services.notification_scheduler import release, tick, HEARTBEAT_SECONDS

logger = logging.getLogger("stockmate.notification_worker")


def run_loop(session_factory, *, settings, stop, owner_token, clock=None, once=False):
    """Single threaded, bounded-backoff polling. No browser session is used."""
    failures = 0
    try:
        while not stop.is_set():
            try:
                result = tick(session_factory, settings=settings, owner_token=owner_token, clock=clock)
                failures = 0
                if result["status"] == "evaluated":
                    logger.info("Notification evaluation succeeded: %s", {
                        key: result["result"].get(key) for key in ("created", "updated", "transitioned")})
                elif result["status"] == "lost":
                    logger.warning("Worker lease was lost; waiting before another claim")
                delay = HEARTBEAT_SECONDS
            except Exception as error:
                failures += 1
                delay = min(300, HEARTBEAT_SECONDS * (2 ** min(failures - 1, 4)))
                logger.error("Notification worker check failed (%s); retry in %s seconds",
                             type(error).__name__, delay)
            if once:
                break
            stop.wait(delay)
    finally:
        try:
            release(session_factory, owner_token=owner_token,
                    now_utc=clock() if clock else None)
        except Exception as error:
            logger.error("Could not release worker lease (%s); it will expire", type(error).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run one enabled check and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    url = make_url(settings.database_url)
    if url.drivername.startswith("sqlite") and url.database not in (None, ":memory:"):
        if not Path(unquote(url.database)).is_file():
            parser.error("The configured SQLite database does not exist; migrate it before starting the worker.")
    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    stop = threading.Event()
    def shutdown(signum, _frame):
        logger.info("Stopping notification worker after signal %s", signum)
        stop.set()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    owner_token = uuid4().hex
    try:
        run_loop(session_factory, settings=settings, stop=stop, owner_token=owner_token, once=args.once)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
