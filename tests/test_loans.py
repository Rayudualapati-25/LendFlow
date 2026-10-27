from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dependencies import current_time
from app.domain.enums import ApplicationStatus, Role
from app.models import Applicant, AuditLog, Loan, User
from app.services import loans
from tests.factories import make_applicant, make_application, make_user
from tests.helpers import auth_headers

DISBURSED_AT = datetime(2026, 1, 10, 6, 0, tzinfo=UTC)
PAID_AT = datetime(2026, 2, 9, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen_clock(app: FastAPI) -> None:
    app.dependency_overrides[current_time] = lambda: PAID_AT


@pytest.fixture
def underwriter(db_session: Session) -> User:
    return make_user(db_session, role=Role.UNDERWRITER)


@pytest.fixture
def applicant(db_session: Session) -> Applicant:
    return make_applicant(db_session)


@pytest.fixture
def owner_headers(applicant: Applicant, settings: Settings) -> dict[str, str]:
    return auth_headers(applicant.user, settings)


@pytest.fixture
def loan(db_session: Session, underwriter: User, applicant: Applicant) -> Loan:
    """A Rs 60,000 personal loan over 6 months, disbursed through the real service."""
    application = make_application(
        db_session,
        applicant=applicant,
        amount=Decimal("60000.00"),
        tenure_months=6,
        status=ApplicationStatus.APPROVED,
    )
    loan, _ = loans.disburse(
        db_session, underwriter, application.id, idempotency_key="test-loan", now=DISBURSED_AT
    )
    return loan


def pay(client: TestClient, loan: Loan, number: int, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        f"/loans/{loan.id}/repayments", json={"installment_number": number}, headers=headers
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# Reading loans


def test_the_borrower_can_read_their_loan_and_schedule(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    response = client.get(f"/loans/{loan.id}", headers=owner_headers)

    assert response.status_code == 200
    body = response.json()
    assert (body["principal"], body["status"], len(body["schedule"])) == (
        "60000.00",
        "disbursed",
        6,
    )


def test_other_applicants_cannot_see_a_loan(
    client: TestClient, db_session: Session, settings: Settings, loan: Loan
) -> None:
    stranger = auth_headers(make_user(db_session), settings)

    response = client.get(f"/loans/{loan.id}", headers=stranger)

    assert response.status_code == 404
    assert response.json()["code"] == "loan_not_found"


def test_underwriters_can_see_any_loan(
    client: TestClient, settings: Settings, underwriter: User, loan: Loan
) -> None:
    response = client.get(f"/loans/{loan.id}", headers=auth_headers(underwriter, settings))

    assert response.status_code == 200


def test_a_missing_loan_is_404(client: TestClient, owner_headers: dict[str, str]) -> None:
    assert client.get("/loans/999999", headers=owner_headers).status_code == 404


def test_listing_loans_is_scoped_like_applications(
    client: TestClient,
    db_session: Session,
    settings: Settings,
    underwriter: User,
    loan: Loan,
    owner_headers: dict[str, str],
) -> None:
    other_application = make_application(db_session, status=ApplicationStatus.APPROVED)
    other, _ = loans.disburse(
        db_session, underwriter, other_application.id, idempotency_key="other", now=DISBURSED_AT
    )

    mine = client.get("/loans", headers=owner_headers).json()
    everything = client.get("/loans", headers=auth_headers(underwriter, settings)).json()

    assert [item["id"] for item in mine] == [loan.id]
    assert [item["id"] for item in everything] == [other.id, loan.id]


# Repayments


def test_paying_an_instalment_marks_it_paid_and_reduces_the_balance(
    client: TestClient,
    db_session: Session,
    applicant: Applicant,
    loan: Loan,
    owner_headers: dict[str, str],
) -> None:
    first = loan.repayments[0]

    body = pay(client, loan, 1, owner_headers)

    paid = body["schedule"][0]
    assert (paid["status"], paid["paid_at"]) == ("paid", "2026-02-09T12:00:00Z")
    assert body["outstanding_principal"] == str(first.closing_balance)
    assert body["status"] == "disbursed"
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "repayment.received")
    ).one()
    assert (entry.actor_id, entry.details) == (
        applicant.user_id,
        {"installment_number": 1, "amount": str(first.amount_due)},
    )


def test_instalments_are_paid_in_order(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    response = client.post(
        f"/loans/{loan.id}/repayments", json={"installment_number": 3}, headers=owner_headers
    )

    assert response.status_code == 409
    assert response.json()["code"] == "installment_out_of_order"


def test_paying_the_same_instalment_twice_is_409_not_a_double_charge(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    pay(client, loan, 1, owner_headers)

    again = client.post(
        f"/loans/{loan.id}/repayments", json={"installment_number": 1}, headers=owner_headers
    )

    assert again.status_code == 409
    assert again.json()["code"] == "installment_already_paid"


@pytest.mark.parametrize(("number", "status"), [(7, 404), (0, 422)])
def test_the_instalment_must_exist(
    client: TestClient, loan: Loan, owner_headers: dict[str, str], number: int, status: int
) -> None:
    response = client.post(
        f"/loans/{loan.id}/repayments", json={"installment_number": number}, headers=owner_headers
    )

    assert response.status_code == status


def test_paying_the_last_instalment_closes_the_loan(
    client: TestClient, db_session: Session, loan: Loan, owner_headers: dict[str, str]
) -> None:
    for number in range(1, 7):
        body = pay(client, loan, number, owner_headers)

    assert (body["status"], body["outstanding_principal"]) == ("closed", "0.00")
    db_session.refresh(loan.application)
    assert loan.application.status == ApplicationStatus.CLOSED
    closing = db_session.scalars(select(AuditLog).where(AuditLog.action == "loan.closed")).one()
    assert (closing.from_status, closing.to_status) == ("disbursed", "closed")


def test_a_closed_loan_takes_no_more_payments(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    for number in range(1, 7):
        pay(client, loan, number, owner_headers)

    response = client.post(
        f"/loans/{loan.id}/repayments", json={"installment_number": 6}, headers=owner_headers
    )

    assert response.status_code == 409


def test_underwriters_can_record_a_payment_received_another_way(
    client: TestClient, settings: Settings, underwriter: User, loan: Loan
) -> None:
    body = pay(client, loan, 1, auth_headers(underwriter, settings))

    assert body["schedule"][0]["status"] == "paid"


def test_strangers_cannot_pay_into_a_loan(
    client: TestClient, db_session: Session, settings: Settings, loan: Loan
) -> None:
    response = client.post(
        f"/loans/{loan.id}/repayments",
        json={"installment_number": 1},
        headers=auth_headers(make_user(db_session), settings),
    )

    assert response.status_code == 404


def test_listing_loans_takes_the_same_number_of_queries_however_many_there_are(
    client: TestClient,
    db_session: Session,
    engine: Engine,
    settings: Settings,
    underwriter: User,
) -> None:
    headers = auth_headers(underwriter, settings)

    def book(count: int) -> None:
        for number in range(count):
            application = make_application(db_session, status=ApplicationStatus.APPROVED)
            loans.disburse(
                db_session,
                underwriter,
                application.id,
                idempotency_key=f"n-plus-one-{application.id}-{number}",
                now=DISBURSED_AT,
            )
        db_session.expire_all()  # make the API load everything afresh, as a new request would

    def queries_to_list_loans() -> int:
        statements: list[str] = []

        def record(*args: object) -> None:
            statements.append(str(args[2]))

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert client.get("/loans", headers=headers).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(statements)

    book(2)
    with_two = queries_to_list_loans()
    book(4)
    with_six = queries_to_list_loans()

    assert with_six == with_two
