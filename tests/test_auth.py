from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.dependencies import ApplicantUser, UnderwriterUser
from app.domain.enums import Role
from app.main import create_app
from app.models import User
from app.security import create_access_token
from tests.factories import DEFAULT_PASSWORD, make_user
from tests.helpers import auth_headers

PASSWORD = "a long enough passphrase"


def register(client: TestClient, email: str = "asha@example.com", **extra: str) -> Response:
    return client.post("/auth/register", json={"email": email, "password": PASSWORD, **extra})


def sign_in(client: TestClient, email: str, password: str) -> Response:
    return client.post("/auth/token", data={"username": email, "password": password})


# Registration


def test_registering_creates_an_applicant_account(client: TestClient) -> None:
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert body == {"id": body["id"], "email": "asha@example.com", "role": "applicant"}


def test_registration_stores_an_argon2_hash_not_the_password(
    client: TestClient, db_session: Session
) -> None:
    register(client)

    user = db_session.scalars(select(User).where(User.email == "asha@example.com")).one()
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


def test_emails_are_case_insensitive(client: TestClient) -> None:
    assert register(client, email="Asha@Example.COM").json()["email"] == "asha@example.com"

    response = register(client, email="ASHA@example.com")

    assert response.status_code == 409
    assert response.json()["code"] == "email_already_registered"


def test_registration_cannot_choose_the_underwriter_role(client: TestClient) -> None:
    response = register(client, role="underwriter")

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "asha@example.com", "password": "too short"},
        {"email": "asha@example.com", "password": "x" * 129},
    ],
)
def test_registration_validates_email_and_password_length(
    client: TestClient, body: dict[str, str]
) -> None:
    assert client.post("/auth/register", json=body).status_code == 422


# Sign-in


def test_signing_in_returns_a_bearer_token_for_the_user(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    user = make_user(db_session, email="ravi@example.com")

    response = sign_in(client, "Ravi@Example.com", DEFAULT_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.access_token_expire_minutes * 60
    claims = jwt.decode(
        body["access_token"], settings.jwt_secret.get_secret_value(), algorithms=["HS256"]
    )
    assert claims["sub"] == str(user.id)
    assert claims["role"] == "applicant"


@pytest.mark.parametrize(
    ("email", "password"),
    [("ravi@example.com", "wrong password entirely"), ("nobody@example.com", DEFAULT_PASSWORD)],
    ids=["wrong-password", "unknown-email"],
)
def test_failed_sign_in_does_not_reveal_which_part_was_wrong(
    client: TestClient, db_session: Session, email: str, password: str
) -> None:
    make_user(db_session, email="ravi@example.com")

    response = sign_in(client, email, password)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"] == "Incorrect email or password"


# Using the token


def test_me_returns_the_signed_in_user(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    user = make_user(db_session, role=Role.UNDERWRITER)

    response = client.get("/auth/me", headers=auth_headers(user, settings))

    assert response.status_code == 200
    assert response.json() == {"id": user.id, "email": user.email, "role": "underwriter"}


def test_me_requires_a_token(client: TestClient) -> None:
    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def _claims(user: User, now: datetime) -> dict[str, object]:
    return {
        "sub": str(user.id),
        "role": user.role.value,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }


def _sign(claims: dict[str, object], settings: Settings) -> str:
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def _payload_edited_after_signing(user: User, settings: Settings, now: datetime) -> str:
    header, _, signature = _sign(_claims(user, now), settings).split(".")
    forged_payload = _sign(_claims(user, now) | {"role": "underwriter"}, settings).split(".")[1]
    return f"{header}.{forged_payload}.{signature}"


TokenMaker = Callable[[User, Settings, datetime], str]
REJECTED_TOKENS: dict[str, TokenMaker] = {
    "expired": lambda user, settings, now: create_access_token(
        user, settings, now=now - timedelta(hours=2)
    ),
    "signed-with-another-secret": lambda user, _, now: jwt.encode(
        _claims(user, now), "another-secret-of-at-least-32-characters", algorithm="HS256"
    ),
    "payload-edited-after-signing": _payload_edited_after_signing,
    "unsigned-alg-none": lambda user, _, now: jwt.encode(
        _claims(user, now), key=None, algorithm="none"
    ),
    "missing-expiry": lambda user, settings, now: _sign(
        {"sub": str(user.id), "iat": now}, settings
    ),
    "subject-is-not-a-user-id": lambda user, settings, now: _sign(
        _claims(user, now) | {"sub": "admin"}, settings
    ),
    "user-does-not-exist": lambda user, settings, now: _sign(
        _claims(user, now) | {"sub": "999999999"}, settings
    ),
    "not-a-jwt": lambda *_: "not-a-token",
}


@pytest.mark.parametrize("make_token", REJECTED_TOKENS.values(), ids=REJECTED_TOKENS.keys())
def test_invalid_tokens_are_rejected(
    client: TestClient, db_session: Session, settings: Settings, make_token: TokenMaker
) -> None:
    token = make_token(make_user(db_session), settings, datetime.now(UTC))

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


# Role guard


@pytest.fixture
def guarded_client(settings: Settings, db_session: Session) -> Iterator[TestClient]:
    """An app with two extra routes, one per role, to exercise the role dependencies."""
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: db_session

    @app.get("/underwriters-only")
    def underwriters_only(user: UnderwriterUser) -> dict[str, int]:
        return {"id": user.id}

    @app.get("/applicants-only")
    def applicants_only(user: ApplicantUser) -> dict[str, int]:
        return {"id": user.id}

    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("role", "path", "status"),
    [
        (Role.UNDERWRITER, "/underwriters-only", 200),
        (Role.APPLICANT, "/underwriters-only", 403),
        (Role.APPLICANT, "/applicants-only", 200),
        (Role.UNDERWRITER, "/applicants-only", 403),
    ],
)
def test_routes_are_restricted_by_role(
    guarded_client: TestClient,
    db_session: Session,
    settings: Settings,
    role: Role,
    path: str,
    status: int,
) -> None:
    user = make_user(db_session, role=role)

    response = guarded_client.get(path, headers=auth_headers(user, settings))

    assert response.status_code == status
