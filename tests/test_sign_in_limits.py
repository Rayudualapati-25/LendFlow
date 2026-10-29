from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dependencies import current_time
from app.models import SignInFailure, User
from tests.factories import DEFAULT_PASSWORD, make_user

START = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)
WRONG = "not the password at all"


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
    clock = Clock(START)
    app.dependency_overrides[current_time] = clock
    return clock


@pytest.fixture
def user(db_session: Session) -> User:
    return make_user(db_session, email="asha@example.com")


def sign_in(client: TestClient, password: str, email: str = "asha@example.com") -> Response:
    return client.post("/auth/token", data={"username": email, "password": password})


def fail(client: TestClient, times: int, email: str = "asha@example.com") -> None:
    for _ in range(times):
        assert sign_in(client, WRONG, email).status_code == 401


def test_five_failures_lock_the_email_even_for_the_right_password(
    client: TestClient, user: User, clock: Clock
) -> None:
    fail(client, 5)

    response = sign_in(client, DEFAULT_PASSWORD)

    assert response.status_code == 429
    assert response.json() == {
        "detail": "Too many failed sign-ins for this email: try again in 900 seconds",
        "code": "too_many_sign_in_attempts",
    }
    assert response.headers["Retry-After"] == "900"


def test_four_failures_still_allow_the_right_password(
    client: TestClient, user: User, clock: Clock
) -> None:
    fail(client, 4)

    assert sign_in(client, DEFAULT_PASSWORD).status_code == 200


def test_the_lock_lifts_as_the_oldest_failure_leaves_the_15_minute_window(
    client: TestClient, user: User, clock: Clock
) -> None:
    for _ in range(5):  # one failure a minute
        fail(client, 1)
        clock.advance(minutes=1)

    # Five minutes in: the first failure leaves the window at minute 15.
    assert sign_in(client, DEFAULT_PASSWORD).headers["Retry-After"] == "600"
    clock.advance(minutes=10)
    assert sign_in(client, DEFAULT_PASSWORD).status_code == 200


def test_refused_attempts_do_not_extend_the_lock(
    client: TestClient, user: User, clock: Clock
) -> None:
    fail(client, 5)
    for _ in range(20):  # an attacker keeps trying while locked out
        assert sign_in(client, WRONG).status_code == 429

    clock.advance(minutes=15)

    assert sign_in(client, DEFAULT_PASSWORD).status_code == 200


def test_signing_in_clears_earlier_failures(client: TestClient, user: User, clock: Clock) -> None:
    fail(client, 4)
    assert sign_in(client, DEFAULT_PASSWORD).status_code == 200

    fail(client, 4)

    assert sign_in(client, DEFAULT_PASSWORD).status_code == 200


def test_unknown_emails_are_limited_the_same_way(client: TestClient, clock: Clock) -> None:
    # Otherwise a 429 would reveal which emails have accounts.
    fail(client, 5, email="nobody@example.com")

    assert sign_in(client, WRONG, email="nobody@example.com").status_code == 429


def test_the_limit_is_per_email_and_ignores_case(
    client: TestClient, db_session: Session, user: User, clock: Clock
) -> None:
    make_user(db_session, email="ravi@example.com")
    fail(client, 5, email="Asha@Example.COM")

    assert sign_in(client, DEFAULT_PASSWORD, email="asha@example.com").status_code == 429
    assert sign_in(client, DEFAULT_PASSWORD, email="ravi@example.com").status_code == 200


def test_failures_older_than_the_window_are_deleted(
    client: TestClient, db_session: Session, clock: Clock
) -> None:
    fail(client, 3, email="first@example.com")
    clock.advance(minutes=16)

    fail(client, 1, email="second@example.com")

    emails = db_session.scalars(select(SignInFailure.email)).all()
    assert emails == ["second@example.com"]
    assert db_session.scalar(select(func.count()).select_from(SignInFailure)) == 1
