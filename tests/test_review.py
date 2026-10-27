import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.enums import ApplicationStatus, Role
from app.models import AuditLog, User
from tests.factories import make_applicant, make_application, make_user
from tests.helpers import auth_headers


@pytest.fixture
def underwriter(db_session: Session) -> User:
    return make_user(db_session, role=Role.UNDERWRITER)


@pytest.fixture
def underwriter_headers(underwriter: User, settings: Settings) -> dict[str, str]:
    return auth_headers(underwriter, settings)


def test_an_underwriter_starts_the_review(
    client: TestClient,
    db_session: Session,
    underwriter: User,
    underwriter_headers: dict[str, str],
) -> None:
    application = make_application(db_session)

    response = client.post(f"/applications/{application.id}/review", headers=underwriter_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "under_review"
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.application_id == application.id)
    ).one()
    assert (entry.action, entry.actor_id, entry.from_status, entry.to_status) == (
        "application.review_started",
        underwriter.id,
        "submitted",
        "under_review",
    )


@pytest.mark.parametrize(
    "status",
    [
        ApplicationStatus.UNDER_REVIEW,
        ApplicationStatus.APPROVED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.DISBURSED,
        ApplicationStatus.CLOSED,
    ],
)
def test_only_submitted_applications_can_be_reviewed(
    client: TestClient,
    db_session: Session,
    underwriter_headers: dict[str, str],
    status: ApplicationStatus,
) -> None:
    application = make_application(db_session, status=status)

    response = client.post(f"/applications/{application.id}/review", headers=underwriter_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "illegal_transition"
    db_session.refresh(application)
    assert application.status == status
    assert (
        db_session.scalars(select(AuditLog).where(AuditLog.application_id == application.id)).all()
        == []
    )


def test_applicants_cannot_review(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    applicant = make_applicant(db_session)
    application = make_application(db_session, applicant=applicant)

    response = client.post(
        f"/applications/{application.id}/review", headers=auth_headers(applicant.user, settings)
    )

    assert response.status_code == 403


def test_reviewing_a_missing_application_is_404(
    client: TestClient, underwriter_headers: dict[str, str]
) -> None:
    response = client.post("/applications/999999/review", headers=underwriter_headers)

    assert response.status_code == 404


def test_the_audit_trail_lists_every_change_in_order(
    client: TestClient,
    db_session: Session,
    settings: Settings,
    underwriter: User,
    underwriter_headers: dict[str, str],
) -> None:
    applicant = make_applicant(db_session)
    submitted = client.post(
        "/applications",
        json={"product": "personal", "amount": "50000", "tenure_months": 12},
        headers=auth_headers(applicant.user, settings),
    ).json()
    client.post(f"/applications/{submitted['id']}/review", headers=underwriter_headers)

    trail = client.get(f"/applications/{submitted['id']}/audit", headers=underwriter_headers)

    assert trail.status_code == 200
    assert [
        (entry["action"], entry["actor_id"], entry["from_status"], entry["to_status"])
        for entry in trail.json()
    ] == [
        ("application.submitted", applicant.user_id, None, "submitted"),
        ("application.review_started", underwriter.id, "submitted", "under_review"),
    ]


def test_applicants_cannot_read_the_audit_trail(
    client: TestClient, db_session: Session, settings: Settings
) -> None:
    applicant = make_applicant(db_session)
    application = make_application(db_session, applicant=applicant)

    response = client.get(
        f"/applications/{application.id}/audit", headers=auth_headers(applicant.user, settings)
    )

    assert response.status_code == 403


def test_the_audit_trail_of_a_missing_application_is_404(
    client: TestClient, underwriter_headers: dict[str, str]
) -> None:
    assert client.get("/applications/999999/audit", headers=underwriter_headers).status_code == 404
