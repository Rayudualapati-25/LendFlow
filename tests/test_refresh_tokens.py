import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import current_time
from app.models import RefreshToken, User
from tests.factories import DEFAULT_PASSWORD, make_user

SIGNED_IN_AT = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)
DAY = 24 * 60 * 60


class Clock:
    """The injected clock, which a test can move forward."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock(app: FastAPI) -> Clock:
    clock = Clock(SIGNED_IN_AT)
    app.dependency_overrides[current_time] = clock
    return clock


@pytest.fixture
def user(db_session: Session) -> User:
    return make_user(db_session, email="asha@example.com")


def sign_in(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/auth/token", data={"username": "asha@example.com", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def refresh(client: TestClient, token: str) -> Response:
    return client.post("/auth/refresh", json={"refresh_token": token})


def logout(client: TestClient, token: str) -> Response:
    return client.post("/auth/logout", json={"refresh_token": token})


def _refused(response: Response, code: str) -> bool:
    return (
        response.status_code == 401
        and response.json()["code"] == code
        and response.headers["WWW-Authenticate"] == "Bearer"
    )


def test_signing_in_also_returns_a_refresh_token_stored_only_as_a_digest(
    client: TestClient, db_session: Session, user: User, clock: Clock
) -> None:
    tokens = sign_in(client)

    assert len(tokens["refresh_token"]) >= 43  # 256 random bits
    assert tokens["refresh_expires_in"] == DAY
    row = db_session.scalars(select(RefreshToken)).one()
    assert row.token_hash == hashlib.sha256(tokens["refresh_token"].encode()).hexdigest()
    assert (row.user_id, row.issued_at, row.expires_at, row.session_expires_at) == (
        user.id,
        SIGNED_IN_AT,
        SIGNED_IN_AT + timedelta(days=1),
        SIGNED_IN_AT + timedelta(days=7),
    )


def test_a_refresh_token_buys_a_new_access_token_and_a_new_refresh_token(
    client: TestClient, user: User, clock: Clock
) -> None:
    first = sign_in(client)
    clock.advance(hours=1)

    response = refresh(client, first["refresh_token"])

    assert response.status_code == 200, response.text
    second = response.json()
    assert second["token_type"] == "bearer"
    assert second["refresh_token"] != first["refresh_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"})
    assert me.json()["email"] == "asha@example.com"
    # Each new token works once in turn.
    assert refresh(client, second["refresh_token"]).status_code == 200


def test_reusing_an_exchanged_refresh_token_ends_the_whole_session(
    client: TestClient, user: User, clock: Clock
) -> None:
    first = sign_in(client)
    second = refresh(client, first["refresh_token"]).json()

    # Someone replays the first token: it was copied, so nobody in this session is trusted.
    replayed = refresh(client, first["refresh_token"])

    assert _refused(replayed, "refresh_token_reused")
    assert _refused(refresh(client, second["refresh_token"]), "invalid_refresh_token")


@pytest.mark.parametrize(("idle", "status"), [(DAY - 1, 200), (DAY, 401)])
def test_a_refresh_token_lasts_a_day_unused(
    client: TestClient, user: User, clock: Clock, idle: int, status: int
) -> None:
    tokens = sign_in(client)
    clock.advance(seconds=idle)

    assert refresh(client, tokens["refresh_token"]).status_code == status


def test_a_sign_in_lasts_at_most_seven_days_however_often_it_is_refreshed(
    client: TestClient, user: User, clock: Clock
) -> None:
    tokens = sign_in(client)
    for _ in range(7):  # every 23 hours: never idle for a day
        clock.advance(hours=23)
        tokens = refresh(client, tokens["refresh_token"]).json()

    # 161 hours in: the last token runs only to the end of the 168-hour session.
    assert tokens["refresh_expires_in"] == 7 * 60 * 60
    clock.advance(hours=7)
    assert _refused(refresh(client, tokens["refresh_token"]), "invalid_refresh_token")


def test_logging_out_ends_the_session(client: TestClient, user: User, clock: Clock) -> None:
    first = sign_in(client)
    latest = refresh(client, first["refresh_token"]).json()

    response = logout(client, latest["refresh_token"])

    assert response.status_code == 204
    assert _refused(refresh(client, latest["refresh_token"]), "invalid_refresh_token")


def test_logging_out_leaves_other_sign_ins_alone(
    client: TestClient, user: User, clock: Clock
) -> None:
    laptop, phone = sign_in(client), sign_in(client)

    logout(client, laptop["refresh_token"])

    assert refresh(client, phone["refresh_token"]).status_code == 200


@pytest.mark.parametrize("token", ["not-a-token-we-issued", "x" * 43])
def test_unknown_refresh_tokens_are_refused_and_logging_them_out_is_harmless(
    client: TestClient, user: User, clock: Clock, token: str
) -> None:
    assert _refused(refresh(client, token), "invalid_refresh_token")
    # Logout answers the same either way, so it cannot be used to test tokens.
    assert logout(client, token).status_code == 204


@pytest.mark.parametrize("body", [{}, {"refresh_token": ""}, {"refresh_token": "t", "user_id": 1}])
@pytest.mark.parametrize("path", ["/auth/refresh", "/auth/logout"])
def test_refresh_and_logout_validate_the_request(
    client: TestClient, path: str, body: dict[str, Any]
) -> None:
    assert client.post(path, json=body).status_code == 422
