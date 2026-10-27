from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.enums import ApplicationStatus, Product, Role
from app.models import Applicant, AuditLog, User
from tests.factories import make_applicant, make_application, make_user
from tests.helpers import auth_headers

REQUEST = {"product": "personal", "amount": "100000", "tenure_months": 12}


@pytest.fixture
def applicant(db_session: Session) -> Applicant:
    return make_applicant(db_session)


@pytest.fixture
def applicant_headers(applicant: Applicant, settings: Settings) -> dict[str, str]:
    return auth_headers(applicant.user, settings)


@pytest.fixture
def underwriter_headers(db_session: Session, settings: Settings) -> dict[str, str]:
    return auth_headers(make_user(db_session, role=Role.UNDERWRITER), settings)


# Submitting


def test_submitting_quotes_the_product_rate_and_emi(
    client: TestClient, applicant_headers: dict[str, str]
) -> None:
    response = client.post("/applications", json=REQUEST, headers=applicant_headers)

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "id": body["id"],
        "product": "personal",
        "amount": "100000.00",
        "tenure_months": 12,
        "annual_rate_pct": "14.00",
        "emi": "8978.71",
        "status": "submitted",
        "created_at": body["created_at"],
        "updated_at": body["updated_at"],
    }


def test_submitting_is_recorded_in_the_audit_log(
    client: TestClient,
    db_session: Session,
    applicant: Applicant,
    applicant_headers: dict[str, str],
) -> None:
    response = client.post("/applications", json=REQUEST, headers=applicant_headers)
    application_id = response.json()["id"]

    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.application_id == application_id)
    ).one()
    assert (entry.action, entry.actor_id, entry.from_status, entry.to_status) == (
        "application.submitted",
        applicant.user_id,
        None,
        "submitted",
    )


def test_submitting_needs_a_profile_first(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    headers = auth_headers(make_user(db_session), settings)

    response = client.post("/applications", json=REQUEST, headers=headers)

    assert response.status_code == 409
    assert response.json()["code"] == "profile_required"


@pytest.mark.parametrize(
    "change",
    [{"amount": "5000"}, {"tenure_months": 120}, {"product": "home", "amount": "100000"}],
    ids=["amount-below-minimum", "tenure-too-long", "below-home-loan-minimum"],
)
def test_terms_outside_the_product_are_422(
    client: TestClient, applicant_headers: dict[str, str], change: dict[str, Any]
) -> None:
    response = client.post("/applications", json=REQUEST | change, headers=applicant_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "terms_outside_product"


@pytest.mark.parametrize(
    "change",
    [{"product": "yacht"}, {"amount": "-100"}, {"amount": "1000.005"}, {"status": "approved"}],
    ids=["unknown-product", "negative-amount", "fractions-of-a-paisa", "client-sets-status"],
)
def test_malformed_requests_are_422(
    client: TestClient, applicant_headers: dict[str, str], change: dict[str, Any]
) -> None:
    response = client.post("/applications", json=REQUEST | change, headers=applicant_headers)

    assert response.status_code == 422


def test_underwriters_cannot_apply(client: TestClient, underwriter_headers: dict[str, str]) -> None:
    response = client.post("/applications", json=REQUEST, headers=underwriter_headers)

    assert response.status_code == 403


# Listing and reading


def _ids(response_json: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in response_json]


def test_applicants_list_only_their_own_applications_newest_first(
    client: TestClient, db_session: Session, applicant: Applicant, applicant_headers: dict[str, str]
) -> None:
    older = make_application(db_session, applicant=applicant)
    newer = make_application(db_session, applicant=applicant)
    make_application(db_session)  # someone else's

    response = client.get("/applications", headers=applicant_headers)

    assert response.status_code == 200
    assert _ids(response.json()) == [newer.id, older.id]


def test_underwriters_list_everything_and_can_filter_by_status(
    client: TestClient, db_session: Session, underwriter_headers: dict[str, str]
) -> None:
    submitted = make_application(db_session)
    approved = make_application(db_session, status=ApplicationStatus.APPROVED)

    everything = client.get("/applications", headers=underwriter_headers).json()
    only_approved = client.get(
        "/applications", params={"status": "approved"}, headers=underwriter_headers
    ).json()

    assert _ids(everything) == [approved.id, submitted.id]
    assert _ids(only_approved) == [approved.id]


def test_listing_is_paginated(
    client: TestClient, db_session: Session, underwriter_headers: dict[str, str]
) -> None:
    ids = [make_application(db_session).id for _ in range(5)]

    page = client.get(
        "/applications", params={"limit": 2, "offset": 1}, headers=underwriter_headers
    )

    assert _ids(page.json()) == ids[::-1][1:3]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_page_bounds_are_validated(
    client: TestClient, underwriter_headers: dict[str, str], params: dict[str, int]
) -> None:
    response = client.get("/applications", params=params, headers=underwriter_headers)

    assert response.status_code == 422


def test_applicants_can_read_their_own_application(
    client: TestClient, db_session: Session, applicant: Applicant, applicant_headers: dict[str, str]
) -> None:
    application = make_application(db_session, applicant=applicant, product=Product.VEHICLE)

    response = client.get(f"/applications/{application.id}", headers=applicant_headers)

    assert response.status_code == 200
    assert response.json()["product"] == "vehicle"


def test_other_peoples_applications_look_like_they_do_not_exist(
    client: TestClient, db_session: Session, applicant_headers: dict[str, str]
) -> None:
    someone_elses = make_application(db_session)

    response = client.get(f"/applications/{someone_elses.id}", headers=applicant_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "application_not_found"


def test_underwriters_can_read_any_application(
    client: TestClient, db_session: Session, underwriter_headers: dict[str, str]
) -> None:
    application = make_application(db_session)

    response = client.get(f"/applications/{application.id}", headers=underwriter_headers)

    assert response.status_code == 200


def test_reading_a_missing_application_is_404(
    client: TestClient, underwriter_headers: dict[str, str]
) -> None:
    assert client.get("/applications/999999", headers=underwriter_headers).status_code == 404


def test_listing_requires_sign_in(client: TestClient) -> None:
    assert client.get("/applications").status_code == 401


def test_signed_in_users_without_a_profile_see_an_empty_list(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    user: User = make_user(db_session)

    response = client.get("/applications", headers=auth_headers(user, settings))

    assert response.json() == []
