import pytest
from pydantic import SecretStr, ValidationError

from app.config import DatabaseSettings, Settings
from tests.conftest import TEST_JWT_SECRET


@pytest.mark.parametrize(
    ("given", "used"),
    [
        # What hosts such as Render hand out: SQLAlchemy would pick psycopg2, not installed.
        (
            "postgres://user:pass@db.internal/lendflow",
            "postgresql+psycopg://user:pass@db.internal/lendflow",
        ),
        (
            "postgresql://user:pass@db:5432/lendflow",
            "postgresql+psycopg://user:pass@db:5432/lendflow",
        ),
        (
            "postgresql+psycopg://user:pass@db/lendflow",
            "postgresql+psycopg://user:pass@db/lendflow",
        ),
    ],
)
def test_database_urls_always_name_the_psycopg_driver(given: str, used: str) -> None:
    assert DatabaseSettings(database_url=given).database_url == used
    settings = Settings(database_url=given, jwt_secret=SecretStr(TEST_JWT_SECRET))
    assert settings.database_url == used


def test_the_api_refuses_to_start_with_a_short_signing_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(jwt_secret=SecretStr("too short"))
