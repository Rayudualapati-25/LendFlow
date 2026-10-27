import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.main import create_app

# Tests need a real PostgreSQL: the reports, row locks and constraints are Postgres-specific.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://lendflow:lendflow@localhost:5432/lendflow_test",
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def pytest_configure() -> None:
    # The suite drops and recreates the schema, so refuse anything that isn't a test database.
    database = make_url(TEST_DATABASE_URL).database or ""
    if not database.endswith("_test"):
        pytest.exit(f"TEST_DATABASE_URL must name a database ending in _test, got {database!r}")


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


@pytest.fixture(scope="session")
def engine(alembic_config: Config) -> Iterator[Engine]:
    """Build the schema from an empty database with the real migrations, once per run."""
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after the test, including work the code commits.

    Commits inside the code under test only release a SAVEPOINT; the outer transaction is
    rolled back at teardown, so tests never see each other's rows.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL)


@pytest.fixture
def client(settings: Settings, db_session: Session) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
