from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from tests.conftest import TEST_JWT_SECRET


def test_health_is_ok_when_the_database_answers(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_when_the_database_is_unreachable() -> None:
    # Port 1 on localhost refuses connections immediately, so this fails fast.
    unreachable = Settings(
        database_url="postgresql+psycopg://nobody:secret@127.0.0.1:1/none",
        jwt_secret=SecretStr(TEST_JWT_SECRET),
    )

    with TestClient(create_app(unreachable)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
