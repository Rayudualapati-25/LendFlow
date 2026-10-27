import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

# Tests need a real PostgreSQL: the reports, row locks and constraints are Postgres-specific.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://lendflow:lendflow@localhost:5432/lendflow_test",
)


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
