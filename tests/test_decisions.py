from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dependencies import current_time
from app.domain.eligibility import RULES_VERSION
from app.domain.enums import ApplicationStatus, Product, Role
from app.models import Applicant, Application, AuditLog, Decision, User
from tests.factories import make_applicant, make_application, make_user
from tests.helpers import auth_headers

NOW = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)


@pytest.fixture
def underwriter(db_session: Session) -> User:
    return make_user(db_session, role=Role.UNDERWRITER)


@pytest.fixture
def underwriter_headers(underwriter: User, settings: Settings) -> dict[str, str]:
    return auth_headers(underwriter, settings)


@pytest.fixture
def applicant(db_session: Session) -> Applicant:
    return make_applicant(
        db_session, monthly_income=Decimal("100000.00"), existing_emi=Decimal("0.00")
    )


@pytest.fixture
def under_review(db_session: Session, applicant: Applicant) -> Application:
    # Rs 1,00,000 personal loan over 12 months at 14%: EMI 8978.71.
    return make_application(db_session, applicant=applicant, status=ApplicationStatus.UNDER_REVIEW)


@pytest.fixture(autouse=True)
def frozen_clock(app: FastAPI) -> None:
    app.dependency_overrides[current_time] = lambda: NOW


def decide(
    client: TestClient, application: Application, headers: dict[str, str], credit_score: int = 760
) -> dict[str, object]:
    response = client.post(
        f"/applications/{application.id}/decision",
        json={"credit_score": credit_score},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def test_an_eligible_application_is_approved_with_its_inputs_recorded(
    client: TestClient,
    db_session: Session,
    underwriter: User,
    underwriter_headers: dict[str, str],
    under_review: Application,
) -> None:
    decision = decide(client, under_review, underwriter_headers)

    assert decision == {
        "application_id": under_review.id,
        "outcome": "approved",
        "reason_codes": ["ELIGIBLE"],
        "credit_score": 760,
        "monthly_income": "100000.00",
        "existing_emi": "0.00",
        "proposed_emi": "8978.71",
        "foir": "0.0898",
        "active_loans": 0,
        "rules_version": RULES_VERSION,
        "decided_by_id": underwriter.id,
        "decided_at": "2026-10-01T09:30:00Z",
    }
    db_session.refresh(under_review)
    assert under_review.status == ApplicationStatus.APPROVED


def test_the_decision_is_audited_with_its_reasons(
    client: TestClient,
    db_session: Session,
    underwriter: User,
    underwriter_headers: dict[str, str],
    under_review: Application,
) -> None:
    decide(client, under_review, underwriter_headers, credit_score=600)

    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.application_id == under_review.id)
    ).one()
    assert (entry.action, entry.actor_id, entry.from_status, entry.to_status, entry.details) == (
        "application.rejected",
        underwriter.id,
        "under_review",
        "rejected",
        {"reason_codes": ["CREDIT_SCORE_BELOW_MINIMUM"]},
    )


def test_a_low_score_is_rejected_with_its_reason(
    client: TestClient,
    db_session: Session,
    underwriter_headers: dict[str, str],
    under_review: Application,
) -> None:
    decision = decide(client, under_review, underwriter_headers, credit_score=600)

    assert (decision["outcome"], decision["reason_codes"]) == (
        "rejected",
        ["CREDIT_SCORE_BELOW_MINIMUM"],
    )
    db_session.refresh(under_review)
    assert under_review.status == ApplicationStatus.REJECTED


def test_approved_and_disbursed_loans_count_as_existing_obligations(
    client: TestClient,
    db_session: Session,
    underwriter_headers: dict[str, str],
    applicant: Applicant,
    under_review: Application,
) -> None:
    applicant.existing_emi = Decimal("5000.00")
    for status in (ApplicationStatus.APPROVED, ApplicationStatus.DISBURSED):
        # Rs 2,00,000 vehicle loan over 24 months at 10.5%: EMI 9275.21.
        make_application(
            db_session,
            applicant=applicant,
            product=Product.VEHICLE,
            amount=Decimal("200000.00"),
            tenure_months=24,
            annual_rate_pct=Decimal("10.50"),
            status=status,
        )
    for status in (
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.CLOSED,
    ):
        make_application(db_session, applicant=applicant, status=status)

    decision = decide(client, under_review, underwriter_headers)

    assert decision["active_loans"] == 2
    assert decision["existing_emi"] == "23550.42"  # 5000 + 2 x 9275.21
    assert decision["foir"] == "0.3253"  # (23550.42 + 8978.71) / 100000, rounded up
    assert decision["reason_codes"] == ["ACTIVE_LOAN_LIMIT_REACHED"]


def test_other_applicants_loans_do_not_count(
    client: TestClient,
    db_session: Session,
    underwriter_headers: dict[str, str],
    under_review: Application,
) -> None:
    make_application(db_session, status=ApplicationStatus.DISBURSED)  # someone else's

    decision = decide(client, under_review, underwriter_headers)

    assert decision["active_loans"] == 0


@pytest.mark.parametrize(
    "status",
    [
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.APPROVED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.DISBURSED,
        ApplicationStatus.CLOSED,
    ],
)
def test_only_applications_under_review_can_be_decided(
    client: TestClient,
    db_session: Session,
    underwriter_headers: dict[str, str],
    status: ApplicationStatus,
) -> None:
    application = make_application(db_session, status=status)

    response = client.post(
        f"/applications/{application.id}/decision",
        json={"credit_score": 760},
        headers=underwriter_headers,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "illegal_transition"
    assert (
        db_session.scalars(select(Decision).where(Decision.application_id == application.id)).all()
        == []
    )


def test_deciding_twice_is_409(
    client: TestClient, underwriter_headers: dict[str, str], under_review: Application
) -> None:
    decide(client, under_review, underwriter_headers)

    again = client.post(
        f"/applications/{under_review.id}/decision",
        json={"credit_score": 760},
        headers=underwriter_headers,
    )

    assert again.status_code == 409


@pytest.mark.parametrize("body", [{"credit_score": 299}, {"credit_score": 901}, {}])
def test_the_credit_score_must_be_a_bureau_score(
    client: TestClient,
    underwriter_headers: dict[str, str],
    under_review: Application,
    body: dict[str, int],
) -> None:
    response = client.post(
        f"/applications/{under_review.id}/decision", json=body, headers=underwriter_headers
    )

    assert response.status_code == 422


def test_applicants_cannot_decide(
    client: TestClient, settings: Settings, applicant: Applicant, under_review: Application
) -> None:
    response = client.post(
        f"/applications/{under_review.id}/decision",
        json={"credit_score": 900},
        headers=auth_headers(applicant.user, settings),
    )

    assert response.status_code == 403


def test_deciding_a_missing_application_is_404(
    client: TestClient, underwriter_headers: dict[str, str]
) -> None:
    response = client.post(
        "/applications/999999/decision", json={"credit_score": 760}, headers=underwriter_headers
    )

    assert response.status_code == 404


# Reading a decision


def test_applicants_can_read_the_reasons_for_their_decision(
    client: TestClient,
    settings: Settings,
    underwriter_headers: dict[str, str],
    applicant: Applicant,
    under_review: Application,
) -> None:
    decide(client, under_review, underwriter_headers, credit_score=600)

    response = client.get(
        f"/applications/{under_review.id}/decision", headers=auth_headers(applicant.user, settings)
    )

    assert response.status_code == 200
    assert response.json()["reason_codes"] == ["CREDIT_SCORE_BELOW_MINIMUM"]


def test_other_applicants_cannot_read_a_decision(
    client: TestClient,
    db_session: Session,
    settings: Settings,
    underwriter_headers: dict[str, str],
    under_review: Application,
) -> None:
    decide(client, under_review, underwriter_headers)
    stranger = make_user(db_session)

    response = client.get(
        f"/applications/{under_review.id}/decision", headers=auth_headers(stranger, settings)
    )

    assert response.status_code == 404


def test_an_undecided_application_has_no_decision_yet(
    client: TestClient, underwriter_headers: dict[str, str], under_review: Application
) -> None:
    response = client.get(f"/applications/{under_review.id}/decision", headers=underwriter_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "decision_not_found"
