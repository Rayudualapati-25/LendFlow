import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import reports
from app.config import Settings
from app.dependencies import current_time
from app.domain.dates import IST
from app.domain.enums import ApplicationStatus, Product, Role
from app.models import Applicant, AuditLog, Loan, Prepayment, User
from app.services import loans
from tests.factories import make_applicant, make_application, make_user
from tests.helpers import auth_headers

D = Decimal
DISBURSED_AT = datetime(2026, 1, 10, 6, 0, tzinfo=UTC)
# 11:30 on 25 April in India: 15 of the 30 days from the 10 April due date to the 10 May one.
NOW = datetime(2026, 4, 25, 6, 0, tzinfo=UTC)
EMI = "8978.71"


@pytest.fixture(autouse=True)
def frozen_clock(app: FastAPI) -> None:
    app.dependency_overrides[current_time] = lambda: NOW


@pytest.fixture
def underwriter(db_session: Session) -> User:
    return make_user(db_session, role=Role.UNDERWRITER)


@pytest.fixture
def applicant(db_session: Session) -> Applicant:
    return make_applicant(db_session)


@pytest.fixture
def owner_headers(applicant: Applicant, settings: Settings) -> dict[str, str]:
    return auth_headers(applicant.user, settings)


def _book(
    session: Session,
    underwriter: User,
    applicant: Applicant,
    *,
    product: Product = Product.PERSONAL,
    amount: str = "100000.00",
    rate: str = "14.00",
    months: int = 12,
    paid: int = 3,
) -> Loan:
    """Disburse on 10 January through the real service, then pay `paid` EMIs on their dates."""
    application = make_application(
        session,
        applicant=applicant,
        product=product,
        amount=D(amount),
        annual_rate_pct=D(rate),
        tenure_months=months,
        status=ApplicationStatus.APPROVED,
    )
    loan, _ = loans.disburse(
        session, underwriter, application.id, idempotency_key=str(uuid.uuid4()), now=DISBURSED_AT
    )
    for row in loan.repayments[:paid]:
        on_due_date = datetime.combine(row.due_date, time(10), tzinfo=IST)
        loans.record_repayment(
            session,
            applicant.user,
            loan.id,
            installment_number=row.installment_number,
            now=on_due_date,
        )
    return loan


@pytest.fixture
def loan(db_session: Session, underwriter: User, applicant: Applicant) -> Loan:
    """Rs 1,00,000 at 14% over 12 months from 10 January; three EMIs paid, Rs 76,289.39 owed."""
    return _book(db_session, underwriter, applicant)


def _prepay(
    client: TestClient,
    loan: Loan,
    headers: dict[str, str],
    *,
    principal: str = "20000.00",
    reduce: str = "emi",
    key: str | None = None,
) -> Response:
    return client.post(
        f"/loans/{loan.id}/prepayments",
        json={"principal": principal, "reduce": reduce},
        headers=headers | {"Idempotency-Key": key or str(uuid.uuid4())},
    )


def _foreclose(
    client: TestClient, loan: Loan, headers: dict[str, str], amount: str, key: str | None = None
) -> Response:
    return client.post(
        f"/loans/{loan.id}/foreclosure",
        json={"amount": amount},
        headers=headers | {"Idempotency-Key": key or str(uuid.uuid4())},
    )


def _unpaid(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in body["schedule"] if row["status"] == "due"]


# Part prepayment


def test_prepaying_and_lowering_the_emi_keeps_the_end_date(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    response = _prepay(client, loan, owner_headers, reduce="emi")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["outstanding_principal"], body["emi"], body["tenure_months"]) == (
        "56289.39",
        "6624.86",
        12,
    )
    unpaid = _unpaid(body)
    assert [row["installment_number"] for row in unpaid] == list(range(4, 13))
    assert [row["amount_due"] for row in unpaid] == ["6624.86"] * 8 + ["6624.83"]
    assert (unpaid[0]["due_date"], unpaid[-1]["due_date"]) == ("2026-05-10", "2027-01-10")
    assert sum(D(row["interest_component"]) for row in unpaid) == D("3334.32")
    [prepayment] = body["prepayments"]
    assert {key: prepayment[key] for key in ("kind", "principal", "interest", "amount")} == {
        "kind": "reduce_emi",
        "principal": "20000.00",
        "interest": "116.67",  # 15 of the period's 30 days on Rs 20,000
        "amount": "20116.67",
    }
    assert datetime.fromisoformat(prepayment["received_at"]) == NOW


def test_prepaying_and_keeping_the_emi_finishes_sooner_and_saves_more_interest(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    response = _prepay(client, loan, owner_headers, reduce="tenure")

    assert response.status_code == 200, response.text
    body = response.json()
    unpaid = _unpaid(body)
    # Seven EMIs instead of nine: the loan ends in November, not January.
    assert (body["emi"], body["tenure_months"], len(unpaid)) == (EMI, 10, 7)
    assert [row["amount_due"] for row in unpaid] == [EMI] * 6 + ["4935.10"]
    assert unpaid[-1]["due_date"] == "2026-11-10"
    # Rs 2,517.97 of interest still to pay, against Rs 3,334.32 when lowering the EMI.
    assert sum(D(row["interest_component"]) for row in unpaid) == D("2517.97")
    assert body["prepayments"][0]["kind"] == "reduce_tenure"


@pytest.mark.parametrize(
    ("now", "interest"),
    [
        (datetime(2026, 4, 10, 6, 0, tzinfo=UTC), "0.00"),  # the day instalment 3 fell due
        (NOW, "116.67"),  # 15 of 30 days
        (datetime(2026, 5, 9, 6, 0, tzinfo=UTC), "225.56"),  # 29 of 30 days
    ],
)
def test_interest_on_the_prepaid_amount_is_only_for_the_days_it_was_owed(
    app: FastAPI,
    client: TestClient,
    loan: Loan,
    owner_headers: dict[str, str],
    now: datetime,
    interest: str,
) -> None:
    app.dependency_overrides[current_time] = lambda: now

    body = _prepay(client, loan, owner_headers).json()

    assert body["prepayments"][0]["interest"] == interest


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 5, 10, 6, 0, tzinfo=UTC),  # instalment 4 is due today
        datetime(2026, 5, 20, 6, 0, tzinfo=UTC),  # instalment 4 is overdue
    ],
)
def test_instalments_already_due_are_paid_first(
    app: FastAPI, client: TestClient, loan: Loan, owner_headers: dict[str, str], now: datetime
) -> None:
    app.dependency_overrides[current_time] = lambda: now

    prepaid = _prepay(client, loan, owner_headers)
    quote = client.get(f"/loans/{loan.id}/foreclosure", headers=owner_headers)

    for response in (prepaid, quote):
        assert response.status_code == 409
        assert response.json() == {
            "detail": "Pay instalment 4, due 2026-05-10, first",
            "code": "installment_due",
        }


@pytest.mark.parametrize("principal", ["76289.39", "80000.00"])
def test_a_part_prepayment_must_leave_something_owed(
    client: TestClient, loan: Loan, owner_headers: dict[str, str], principal: str
) -> None:
    response = _prepay(client, loan, owner_headers, principal=principal)

    assert response.status_code == 422
    assert response.json()["code"] == "prepayment_too_large"


def test_lowering_the_emi_needs_enough_left_to_spread_over_the_remaining_months(
    app: FastAPI,
    client: TestClient,
    db_session: Session,
    underwriter: User,
    applicant: Applicant,
    owner_headers: dict[str, str],
) -> None:
    # A 30-year home loan with eight EMIs paid. Rs 896.39 over 352 months rounds to an EMI
    # so small that rounding would repay it early, so only keeping the EMI works.
    home = _book(
        db_session,
        underwriter,
        applicant,
        product=Product.HOME,
        amount="1000000.00",
        rate="8.75",
        months=360,
        paid=8,
    )
    app.dependency_overrides[current_time] = lambda: datetime(2026, 9, 25, 6, 0, tzinfo=UTC)
    principal = str(home.outstanding_principal - D("896.39"))

    lower_emi = _prepay(client, home, owner_headers, principal=principal, reduce="emi")
    keep_emi = _prepay(client, home, owner_headers, principal=principal, reduce="tenure")

    assert lower_emi.status_code == 422
    assert lower_emi.json()["code"] == "remainder_too_small"
    assert keep_emi.status_code == 200
    assert _unpaid(keep_emi.json())[-1]["closing_balance"] == "0.00"


def test_a_retried_prepayment_is_applied_once(
    client: TestClient, db_session: Session, loan: Loan, owner_headers: dict[str, str]
) -> None:
    first = _prepay(client, loan, owner_headers, key="prepay-1")
    retry = _prepay(client, loan, owner_headers, key="prepay-1")

    assert (first.status_code, retry.status_code) == (200, 200)
    assert "Idempotent-Replayed" not in first.headers
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json()["outstanding_principal"] == "56289.39"
    count = select(func.count()).select_from(Prepayment).where(Prepayment.loan_id == loan.id)
    assert db_session.scalar(count) == 1


@pytest.mark.parametrize(
    "change", [{"principal": "25000.00"}, {"reduce": "tenure"}, {"other_loan": True}]
)
def test_reusing_a_key_for_a_different_prepayment_is_422(
    client: TestClient,
    db_session: Session,
    underwriter: User,
    applicant: Applicant,
    loan: Loan,
    owner_headers: dict[str, str],
    change: dict[str, Any],
) -> None:
    _prepay(client, loan, owner_headers, key="prepay-1")
    target = _book(db_session, underwriter, applicant) if change.get("other_loan") else loan

    response = _prepay(
        client,
        target,
        owner_headers,
        principal=change.get("principal", "20000.00"),
        reduce=change.get("reduce", "emi"),
        key="prepay-1",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "prepayments", {"principal": "20000.00", "reduce": "emi"}),
        ("POST", "foreclosure", {"amount": "76734.41"}),
    ],
)
@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "k" * 256}])
def test_paying_ahead_needs_an_idempotency_key(
    client: TestClient,
    loan: Loan,
    owner_headers: dict[str, str],
    method: str,
    path: str,
    body: dict[str, str],
    headers: dict[str, str],
) -> None:
    response = client.request(
        method, f"/loans/{loan.id}/{path}", json=body, headers=owner_headers | headers
    )

    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"


def test_an_underwriter_can_record_a_prepayment_received_another_way(
    client: TestClient, db_session: Session, loan: Loan, underwriter: User, settings: Settings
) -> None:
    response = _prepay(client, loan, auth_headers(underwriter, settings))

    assert response.status_code == 200
    assert db_session.scalars(select(Prepayment)).one().received_by_id == underwriter.id


def test_strangers_cannot_pay_ahead_on_a_loan(
    client: TestClient, db_session: Session, loan: Loan, settings: Settings
) -> None:
    stranger = auth_headers(make_applicant(db_session).user, settings)

    assert _prepay(client, loan, stranger).status_code == 404
    assert client.get(f"/loans/{loan.id}/foreclosure", headers=stranger).status_code == 404
    assert _foreclose(client, loan, stranger, "76734.41").status_code == 404


def test_a_prepayment_is_audited(
    client: TestClient, db_session: Session, loan: Loan, owner_headers: dict[str, str]
) -> None:
    _prepay(client, loan, owner_headers, reduce="tenure")

    entry = db_session.scalars(select(AuditLog).where(AuditLog.action == "loan.prepaid")).one()
    assert (entry.application_id, entry.actor_id) == (
        loan.application_id,
        loan.application.applicant.user_id,
    )
    assert (entry.from_status, entry.to_status) == (None, None)
    assert entry.details == {
        "principal": "20000.00",
        "interest": "116.67",
        "reduce": "tenure",
        "emi": {"before": EMI, "after": EMI},
        "installments_left": {"before": 9, "after": 7},
    }


# Foreclosure


@pytest.mark.parametrize(
    ("now", "amount"),
    [
        (DISBURSED_AT, "100000.00"),  # the day of disbursal: the principal and nothing more
        (datetime(2026, 1, 15, 6, 0, tzinfo=UTC), "100188.17"),  # 5 of the 31 days to 10 Feb
    ],
)
def test_in_the_first_period_interest_runs_from_the_disbursal_date(
    app: FastAPI,
    client: TestClient,
    db_session: Session,
    underwriter: User,
    applicant: Applicant,
    owner_headers: dict[str, str],
    now: datetime,
    amount: str,
) -> None:
    fresh = _book(db_session, underwriter, applicant, paid=0)
    app.dependency_overrides[current_time] = lambda: now

    quote = client.get(f"/loans/{fresh.id}/foreclosure", headers=owner_headers).json()
    closed = _foreclose(client, fresh, owner_headers, amount)

    assert (quote["amount"], quote["period_days"]) == (amount, 31)
    assert (closed.status_code, closed.json()["status"]) == (200, "closed")
    assert closed.json()["schedule"] == []


def test_the_foreclosure_quote_is_the_principal_plus_interest_for_days_owed(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    response = client.get(f"/loans/{loan.id}/foreclosure", headers=owner_headers)

    assert response.status_code == 200
    assert response.json() == {
        "loan_id": loan.id,
        "principal": "76289.39",
        "interest": "445.02",
        "amount": "76734.41",
        "interest_days": 15,
        "period_days": 30,
        "valid_on": "2026-04-25",
    }


def test_foreclosing_closes_the_loan_and_cancels_the_remaining_schedule(
    client: TestClient, db_session: Session, loan: Loan, owner_headers: dict[str, str]
) -> None:
    response = _foreclose(client, loan, owner_headers, "76734.41")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["outstanding_principal"]) == ("closed", "0.00")
    assert [(row["installment_number"], row["status"]) for row in body["schedule"]] == [
        (1, "paid"),
        (2, "paid"),
        (3, "paid"),
    ]
    [foreclosure] = body["prepayments"]
    assert (foreclosure["kind"], foreclosure["principal"], foreclosure["interest"]) == (
        "foreclosure",
        "76289.39",
        "445.02",
    )
    trail = db_session.scalars(
        select(AuditLog).where(AuditLog.application_id == loan.application_id).order_by(AuditLog.id)
    ).all()
    last = trail[-1]
    assert (last.action, last.from_status, last.to_status) == (
        "loan.foreclosed",
        "disbursed",
        "closed",
    )
    assert last.details == {"principal": "76289.39", "interest": "445.02", "amount": "76734.41"}


def test_foreclosing_needs_todays_amount(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    # Yesterday's quote was a day's interest lower.
    response = _foreclose(client, loan, owner_headers, "76704.74")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "The amount to foreclose today is 76734.41",
        "code": "foreclosure_amount_changed",
    }


def test_a_retried_foreclosure_is_replayed(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    first = _foreclose(client, loan, owner_headers, "76734.41", key="close-1")
    retry = _foreclose(client, loan, owner_headers, "76734.41", key="close-1")
    other_amount = _foreclose(client, loan, owner_headers, "70000.00", key="close-1")

    assert (first.status_code, retry.status_code) == (200, 200)
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json()["prepayments"] == first.json()["prepayments"]
    assert other_amount.status_code == 422


def test_a_closed_loan_cannot_be_paid_ahead(
    client: TestClient, loan: Loan, owner_headers: dict[str, str]
) -> None:
    _foreclose(client, loan, owner_headers, "76734.41")

    prepaid = _prepay(client, loan, owner_headers)
    quote = client.get(f"/loans/{loan.id}/foreclosure", headers=owner_headers)
    foreclosed_again = _foreclose(client, loan, owner_headers, "76734.41")
    paid = client.post(
        f"/loans/{loan.id}/repayments", json={"installment_number": 4}, headers=owner_headers
    )

    for response in (prepaid, quote, foreclosed_again):
        assert response.status_code == 409
        assert response.json()["code"] == "illegal_transition"
    assert paid.status_code == 404  # the schedule ended with the foreclosure


# Reports


def test_prepaid_principal_leaves_the_outstanding_book(
    app: FastAPI,
    client: TestClient,
    db_session: Session,
    loan: Loan,
    owner_headers: dict[str, str],
) -> None:
    _prepay(client, loan, owner_headers)
    [personal] = reports.outstanding_by_product(db_session)
    assert personal["outstanding"] == D("56289.39")

    def outstanding_on(day: date) -> Decimal:
        rows = reports.overdue_by_bucket(db_session, as_of=day)
        return sum((row["outstanding"] for row in rows), D(0))

    assert outstanding_on(date(2026, 4, 24)) == D("76289.39")
    assert outstanding_on(date(2026, 4, 25)) == D("56289.39")


def test_a_foreclosed_loan_leaves_the_book_on_the_day_it_was_paid_off(
    client: TestClient, db_session: Session, loan: Loan, owner_headers: dict[str, str]
) -> None:
    _foreclose(client, loan, owner_headers, "76734.41")

    def loans_on(day: date) -> int:
        return sum(row["loans"] for row in reports.overdue_by_bucket(db_session, as_of=day))

    assert reports.outstanding_by_product(db_session) == []
    assert (loans_on(date(2026, 4, 24)), loans_on(date(2026, 4, 25))) == (1, 0)
