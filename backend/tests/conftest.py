"""Shared pytest fixtures.

Each test runs the real application against a temporary SQLite database in a
tmp directory — migrated with Alembic exactly like a real deployment — so
tests never touch development data and the migration itself is exercised.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db_admin import run_migrations
from app.main import create_app
from app.seed import seed_demo_data


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        secret_key="test-secret",
    )


@pytest.fixture()
def client(settings) -> Iterator[TestClient]:
    """App with a migrated but EMPTY database."""
    run_migrations(settings.database_url)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def seeded_client(client, settings) -> TestClient:
    """App whose database additionally contains the fictional demo data."""
    session = client.app.state.session_factory()
    try:
        seed_demo_data(session, settings)
    finally:
        session.close()
    return client
