from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dependencies import current_time
from app.domain.enums import ApplicationStatus, Role
from app.models import Application, AuditLog, Base, Loan, Repayment, User
from tests.factories import make_applicant, make_application, make_user
from tests.helpers import auth_headers

# 23:00 UTC on 31 October is already 1 November in India.
NOW = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen_clock(app: FastAPI) -> None:
    app.dependency_overrides[current_time] = lambda: NOW


@pytest.fixture
def underwriter(db_session: Session) -> User:
    return make_user(db_session, role=Role.UNDERWRITER)


@pytest.fixture
def underwriter_headers(underwriter: User, settings: Settings) -> dict[str, str]:
    return auth_headers(underwriter, settings)


@pytest.fixture
def approved(db_session: Session) -> Application:
    # Rs 1,00,000 personal loan over 12 months at 14%: EMI 8978.71.
    return make_application(db_session, status=ApplicationStatus.APPROVED)


def _count(session: Session, model: type[Base]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_disbursing_books_the_loan_and_its_schedule(
    client: TestClient,
    db_session: Session,
    underwriter: User,
    underwriter_headers: dict[str, str],
    approved: Application,
) -> None:
    response = client.post(
        f"/applications/{approved.id}/disburse",
        headers=underwriter_headers | {"Idempotency-Key": "key-1"},
    )

    assert response.status_code == 201
    assert "Idempotent-Replayed" not in response.headers
    loan = response.json()
    assert {key: loan[key] for key in loan if key != "schedule"} == {
        "id": loan["id"],
        "application_id": approved.id,
        "principal": "100000.00",
        "annual_rate_pct": "14.00",
        "tenure_months": 12,
        "emi": "8978.71",
        "status": "disbursed",
        "outstanding_principal": "100000.00",
        "disbursed_at": "2026-10-31T23:00:00Z",
    }
    assert len(loan["schedule"]) == 12
    first, last = loan["schedule"][0], loan["schedule"][-1]
    # Due dates are Indian business dates: disbursed on 1 November IST.
    assert (first["installment_number"], first["due_date"], first["status"]) == (
        1,
        "2026-12-01",
        "due",
    )
    assert (last["due_date"], last["closing_balance"]) == ("2027-11-01", "0.00")
    db_session.refresh(approved)
    assert approved.status == ApplicationStatus.DISBURSED
    entry = db_session.scalars(select(AuditLog).where(AuditLog.application_id == approved.id)).one()
    assert (entry.action, entry.actor_id, entry.to_status) == (
        "loan.disbursed",
        underwriter.id,
        "disbursed",
    )


def test_a_retry_with_the_same_key_returns_the_same_loan_without_booking_another(
    client: TestClient,
    db_session: Session,
    underwriter_headers: dict[str, str],
    approved: Application,
) -> None:
    first = client.post(
        f"/applications/{approved.id}/disburse",
        headers=underwriter_headers | {"Idempotency-Key": "retry-me"},
    )

    retry = client.post(
        f"/applications/{approved.id}/disburse",
        headers=underwriter_headers | {"Idempotency-Key": "retry-me"},
    )

    assert retry.status_code == 201
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json() == first.json()
    assert (_count(db_session, Loan), _count(db_session, Repayment)) == (1, 12)
    assert _count(db_session, AuditLog) == 1


def test_reusing_a_key_for_another_application_is_422(
    client: TestClient,
    db_session: Session,
    underwriter_headers: dict[str, str],
    approved: Application,
) -> None:
    other = make_application(db_session, status=ApplicationStatus.APPROVED)
    client.post(
        f"/applications/{approved.id}/disburse",
        headers=underwriter_headers | {"Idempotency-Key": "shared"},
    )

    response = client.post(
        f"/applications/{other.id}/disburse",
        headers=underwriter_headers | {"Idempotency-Key": "shared"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"
    db_session.refresh(other)
    assert other.status == ApplicationStatus.APPROVED


def test_a_second_disbursal_with_a_new_key_is_409(
    client: TestClient, underwriter_headers: dict[str, str], approved: Application
) -> None:
    client.post(
        f"/applications/{approved.id}/disburse",
        headers=underwriter_headers | {"Idempotency-Key": "first"},
    )

    response = client.post(
        f"/applications/{approved.id}/disburse",
        headers=underwriter_headers | {"Idempotency-Key": "second"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "illegal_transition"


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": ""}, {"Idempotency-Key": "k" * 256}])
def test_the_idempotency_key_is_required(
    client: TestClient,
    underwriter_headers: dict[str, str],
    approved: Application,
    headers: dict[str, str],
) -> None:
    response = client.post(
        f"/applications/{approved.id}/disburse", headers=underwriter_headers | headers
    )

    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"


@pytest.mark.parametrize(
    "status",
    [
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.UNDER_REVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.CLOSED,
    ],
)
def test_only_approved_applications_can_be_disbursed(
    client: TestClient,
    db_session: Session,
    underwriter_headers: dict[str, str],
    status: ApplicationStatus,
) -> None:
    application = make_application(db_session, status=status)

    response = client.post(
        f"/applications/{application.id}/disburse",
        headers=underwriter_headers | {"Idempotency-Key": "key"},
    )

    assert response.status_code == 409
    assert _count(db_session, Loan) == 0


def test_applicants_cannot_disburse(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    applicant = make_applicant(db_session)
    application = make_application(
        db_session, applicant=applicant, status=ApplicationStatus.APPROVED
    )

    response = client.post(
        f"/applications/{application.id}/disburse",
        headers=auth_headers(applicant.user, settings) | {"Idempotency-Key": "key"},
    )

    assert response.status_code == 403


def test_disbursing_a_missing_application_is_404(
    client: TestClient, underwriter_headers: dict[str, str]
) -> None:
    response = client.post(
        "/applications/999999/disburse", headers=underwriter_headers | {"Idempotency-Key": "k"}
    )

    assert response.status_code == 404
