from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.enums import ApplicationStatus, DecisionOutcome, InstallmentStatus, Product, Role
from app.models import Loan
from tests.factories import make_application, make_decision, make_loan, make_repayment, make_user
from tests.helpers import auth_headers

APPROVED, REJECTED = DecisionOutcome.APPROVED, DecisionOutcome.REJECTED


@pytest.fixture
def underwriter_headers(db_session: Session, settings: Settings) -> dict[str, str]:
    return auth_headers(make_user(db_session, role=Role.UNDERWRITER), settings)


def _decided(session: Session, outcome: DecisionOutcome, at: datetime) -> None:
    make_decision(session, outcome=outcome, decided_at=at)


def test_approval_rate_by_month(
    client: TestClient, db_session: Session, underwriter_headers: dict[str, str]
) -> None:
    january = [(APPROVED, 5), (APPROVED, 20), (REJECTED, 31)]
    march = [(APPROVED, 2), (REJECTED, 9), (REJECTED, 16), (REJECTED, 23)]
    for outcome, day in january:
        _decided(db_session, outcome, datetime(2026, 1, day, 10, 0, tzinfo=UTC))
    for outcome, day in march:
        _decided(db_session, outcome, datetime(2026, 3, day, 10, 0, tzinfo=UTC))
    # 19:00 UTC on 31 March is 00:30 on 1 April in India: it counts towards April.
    _decided(db_session, APPROVED, datetime(2026, 3, 31, 19, 0, tzinfo=UTC))

    response = client.get("/reports/approval-rate", headers=underwriter_headers)

    assert response.status_code == 200
    columns = (
        "month",
        "decided",
        "approved",
        "rejected",
        "approval_rate",
        "approval_rate_3m",
        "change_vs_previous_month",
    )
    assert [tuple(row[column] for column in columns) for row in response.json()] == [
        ("2026-01-01", 3, 2, 1, "0.6667", "0.6667", None),
        # No decisions in February: the month is still listed, with no rate.
        ("2026-02-01", 0, 0, 0, None, "0.6667", None),
        ("2026-03-01", 4, 1, 3, "0.2500", "0.4286", None),
        ("2026-04-01", 1, 1, 0, "1.0000", "0.4000", "0.7500"),
    ]


def test_outstanding_by_product(
    client: TestClient, db_session: Session, underwriter_headers: dict[str, str]
) -> None:
    def booked(product: Product, principal: str, status: ApplicationStatus) -> Loan:
        application = make_application(db_session, product=product, status=status)
        return make_loan(db_session, application=application, principal=Decimal(principal))

    part_repaid = booked(Product.PERSONAL, "100000.00", ApplicationStatus.DISBURSED)
    # Instalment 1 paid (Rs 7,811.04 of principal); instalment 2 still due.
    make_repayment(
        db_session, loan=part_repaid, status=InstallmentStatus.PAID, paid_at=datetime.now(UTC)
    )
    make_repayment(db_session, loan=part_repaid, installment_number=2)
    booked(Product.PERSONAL, "50000.00", ApplicationStatus.DISBURSED)
    booked(Product.HOME, "1000000.00", ApplicationStatus.DISBURSED)
    booked(Product.VEHICLE, "200000.00", ApplicationStatus.DISBURSED)
    booked(Product.VEHICLE, "300000.00", ApplicationStatus.CLOSED)  # repaid: owes nothing

    response = client.get("/reports/outstanding-by-product", headers=underwriter_headers)

    assert response.status_code == 200
    assert response.json() == [
        {
            "product": "home",
            "active_loans": 1,
            "outstanding": "1000000.00",
            "share_pct": "74.51",
            "rank": 1,
        },
        {
            "product": "vehicle",
            "active_loans": 1,
            "outstanding": "200000.00",
            "share_pct": "14.90",
            "rank": 2,
        },
        {
            "product": "personal",
            "active_loans": 2,
            "outstanding": "142188.96",
            "share_pct": "10.59",
            "rank": 3,
        },
    ]


@pytest.mark.parametrize("path", ["/reports/approval-rate", "/reports/outstanding-by-product"])
def test_reports_are_empty_without_data(
    client: TestClient, underwriter_headers: dict[str, str], path: str
) -> None:
    response = client.get(path, headers=underwriter_headers)

    assert (response.status_code, response.json()) == (200, [])


@pytest.mark.parametrize("path", ["/reports/approval-rate", "/reports/outstanding-by-product"])
def test_reports_are_for_underwriters_only(
    client: TestClient, db_session: Session, settings: Settings, path: str
) -> None:
    response = client.get(path, headers=auth_headers(make_user(db_session), settings))

    assert response.status_code == 403
