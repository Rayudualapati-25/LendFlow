from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.enums import Role
from tests.factories import make_user
from tests.helpers import auth_headers

PROFILE = {"full_name": "Asha Rao", "monthly_income": "85000.00", "existing_emi": "4500.50"}


@pytest.fixture
def applicant_headers(db_session: Session, settings: Settings) -> dict[str, str]:
    return auth_headers(make_user(db_session), settings)


def test_creating_a_profile_returns_201_and_it_can_be_read_back(
    client: TestClient, applicant_headers: dict[str, str]
) -> None:
    created = client.put("/applicants/me", json=PROFILE, headers=applicant_headers)

    assert created.status_code == 201
    assert created.json() == {"id": created.json()["id"], **PROFILE}
    assert client.get("/applicants/me", headers=applicant_headers).json() == created.json()


def test_updating_a_profile_returns_200(
    client: TestClient, applicant_headers: dict[str, str]
) -> None:
    client.put("/applicants/me", json=PROFILE, headers=applicant_headers)

    updated = client.put(
        "/applicants/me", json=PROFILE | {"monthly_income": "90000"}, headers=applicant_headers
    )

    assert updated.status_code == 200
    assert updated.json()["monthly_income"] == "90000.00"


def test_reading_a_missing_profile_is_404(
    client: TestClient, applicant_headers: dict[str, str]
) -> None:
    response = client.get("/applicants/me", headers=applicant_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "profile_not_found"


@pytest.mark.parametrize("method", ["get", "put"])
def test_underwriters_have_no_applicant_profile(
    client: TestClient, db_session: Session, settings: Settings, method: str
) -> None:
    headers = auth_headers(make_user(db_session, role=Role.UNDERWRITER), settings)

    response = client.request(method, "/applicants/me", json=PROFILE, headers=headers)

    assert response.status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"monthly_income": "0"},
        {"monthly_income": "999.99"},
        {"monthly_income": "85000.001"},
        {"existing_emi": "-1"},
        {"full_name": "   "},
        {"credit_score": 800},
    ],
    ids=[
        "zero-income",
        "income-below-minimum",
        "fractions-of-a-paisa",
        "negative-emi",
        "blank-name",
        "unknown-field",
    ],
)
def test_profile_validation(
    client: TestClient, applicant_headers: dict[str, str], change: dict[str, Any]
) -> None:
    response = client.put("/applicants/me", json=PROFILE | change, headers=applicant_headers)

    assert response.status_code == 422
