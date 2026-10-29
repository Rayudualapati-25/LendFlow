from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.dependencies import current_time
from app.domain.enums import ApplicationStatus, InstallmentStatus, Role
from app.models import Loan
from tests.factories import make_application, make_loan, make_repayment, make_user
from tests.helpers import auth_headers

# 11:30 on 15 June in India, so the last day-end that has run there is 14 June's.
NOW = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)
PATH = "/reports/overdue-by-bucket"


@pytest.fixture(autouse=True)
def frozen_clock(app: FastAPI) -> None:
    app.dependency_overrides[current_time] = lambda: NOW


@pytest.fixture
def headers(db_session: Session, settings: Settings) -> dict[str, str]:
    return auth_headers(make_user(db_session, role=Role.UNDERWRITER), settings)


def _loan(
    session: Session,
    *instalments: tuple[date, datetime | None],
    disbursed_at: datetime = datetime(2021, 1, 10, 6, 0, tzinfo=UTC),
) -> Loan:
    """A loan with one instalment per (due date, paid at) pair, each repaying Rs 1,000 + Rs 10.

    The report reads only due dates, payment times and amounts, so the round figures keep
    the expected numbers easy to check by hand.
    """
    count = len(instalments)
    principal = Decimal(1000 * count)
    fully_paid = all(paid_at is not None for _, paid_at in instalments)
    application = make_application(
        session,
        amount=principal,
        tenure_months=count,
        status=ApplicationStatus.CLOSED if fully_paid else ApplicationStatus.DISBURSED,
    )
    loan = make_loan(
        session,
        application=application,
        principal=principal,
        tenure_months=count,
        emi=Decimal("1010.00"),
        disbursed_at=disbursed_at,
    )
    for number, (due_date, paid_at) in enumerate(instalments, start=1):
        make_repayment(
            session,
            loan=loan,
            installment_number=number,
            due_date=due_date,
            amount_due=Decimal("1010.00"),
            principal_component=Decimal("1000.00"),
            interest_component=Decimal("10.00"),
            closing_balance=Decimal(1000 * (count - number)),
            status=InstallmentStatus.PAID if paid_at else InstallmentStatus.DUE,
            paid_at=paid_at,
        )
    return loan


def _report(
    client: TestClient, headers: dict[str, str], as_of: date | None = None
) -> dict[str, Any]:
    params = {"as_of": as_of.isoformat()} if as_of else {}
    response = client.get(PATH, params=params, headers=headers)
    assert response.status_code == 200, response.text
    report: dict[str, Any] = response.json()
    return report


def _bucket_of_the_only_loan(report: dict[str, Any]) -> str:
    [bucket] = [row["bucket"] for row in report["buckets"] if row["loans"]]
    return str(bucket)


@pytest.mark.parametrize(
    ("oldest_unpaid_due_date", "bucket"),
    [
        (date(2026, 6, 15), "current"),  # not due by the 14 June day-end
        (date(2026, 6, 14), "sma_0"),  # unpaid at the end of its due date: 1 day past due
        (date(2026, 5, 16), "sma_0"),  # 30 days
        (date(2026, 5, 15), "sma_1"),  # 31 days
        (date(2026, 4, 16), "sma_1"),  # 60 days
        (date(2026, 4, 15), "sma_2"),  # 61 days
        (date(2026, 3, 17), "sma_2"),  # 90 days
        (date(2026, 3, 16), "npa"),  # 91 days: more than 90
    ],
)
def test_days_past_due_place_a_loan_in_rbis_buckets(
    client: TestClient,
    db_session: Session,
    headers: dict[str, str],
    oldest_unpaid_due_date: date,
    bucket: str,
) -> None:
    _loan(db_session, (oldest_unpaid_due_date, None))

    report = _report(client, headers)

    assert _bucket_of_the_only_loan(report) == bucket


@pytest.mark.parametrize(
    ("as_of", "bucket"),
    [
        (date(2021, 3, 30), "current"),
        (date(2021, 3, 31), "sma_0"),
        (date(2021, 4, 29), "sma_0"),
        (date(2021, 4, 30), "sma_1"),
        (date(2021, 5, 29), "sma_1"),
        (date(2021, 5, 30), "sma_2"),
        (date(2021, 6, 28), "sma_2"),
        (date(2021, 6, 29), "npa"),
    ],
)
def test_matches_the_worked_example_in_rbis_2021_clarification(
    client: TestClient, db_session: Session, headers: dict[str, str], as_of: date, bucket: str
) -> None:
    # Dues of 31 March 2021, still unpaid: SMA-1 at the day-end of 30 April, SMA-2 on
    # 30 May and NPA on 29 June (RBI/2021-2022/125).
    _loan(db_session, (date(2021, 3, 31), None))

    assert _bucket_of_the_only_loan(_report(client, headers, as_of)) == bucket


def test_days_past_due_run_from_the_oldest_unpaid_instalment(
    client: TestClient, db_session: Session, headers: dict[str, str]
) -> None:
    # Instalment 1 was paid late; 2 and 3 are unpaid; 4 is not due yet. 1 May to 14 June is
    # 45 days past due (SMA-1). The overdue amount is instalments 2 and 3.
    _loan(
        db_session,
        (date(2026, 4, 1), datetime(2026, 5, 20, 6, 0, tzinfo=UTC)),
        (date(2026, 5, 1), None),
        (date(2026, 6, 1), None),
        (date(2026, 7, 1), None),
    )

    [sma_1] = [row for row in _report(client, headers)["buckets"] if row["bucket"] == "sma_1"]

    assert (sma_1["loans"], sma_1["outstanding"], sma_1["overdue"]) == (1, "3000.00", "2020.00")


@pytest.mark.parametrize(
    ("paid_at", "bucket"),
    [
        (datetime(2026, 6, 10, 12, 0, tzinfo=UTC), "current"),  # 17:30 on 10 June in India
        (datetime(2026, 6, 10, 19, 0, tzinfo=UTC), "sma_1"),  # 00:30 on 11 June in India
    ],
)
def test_a_payment_counts_from_its_date_in_india(
    client: TestClient,
    db_session: Session,
    headers: dict[str, str],
    paid_at: datetime,
    bucket: str,
) -> None:
    # Due 1 May. By the end of 10 June it is either paid or 41 days past due.
    _loan(db_session, (date(2026, 5, 1), paid_at), (date(2026, 7, 1), None))

    assert _bucket_of_the_only_loan(_report(client, headers, date(2026, 6, 10))) == bucket


def test_the_book_is_what_was_disbursed_and_still_owed_at_the_day_end(
    client: TestClient, db_session: Session, headers: dict[str, str]
) -> None:
    # Disbursed at 00:30 on 15 June in India: after the 14 June day-end.
    late_evening_utc = datetime(2026, 6, 14, 19, 0, tzinfo=UTC)
    _loan(db_session, (date(2026, 7, 15), None), disbursed_at=late_evening_utc)
    # Repaid in full before the day-end: owes nothing.
    _loan(db_session, (date(2026, 5, 1), datetime(2026, 5, 1, 6, 0, tzinfo=UTC)))
    # Repaid in full this morning, after the day-end: still owed on 14 June, 14 days late.
    _loan(db_session, (date(2026, 6, 1), datetime(2026, 6, 15, 5, 0, tzinfo=UTC)))

    report = _report(client, headers)

    assert sum(row["loans"] for row in report["buckets"]) == 1
    assert _bucket_of_the_only_loan(report) == "sma_0"


def test_lists_every_bucket_with_its_share_and_the_portfolio_at_risk(
    client: TestClient, db_session: Session, headers: dict[str, str]
) -> None:
    paid_on_time = datetime(2026, 5, 20, 6, 0, tzinfo=UTC)
    _loan(db_session, (date(2026, 6, 20), None), (date(2026, 7, 20), None))  # current
    _loan(db_session, (date(2026, 5, 20), paid_on_time), (date(2026, 6, 20), None))  # current
    _loan(db_session, (date(2026, 6, 1), None), (date(2026, 7, 1), None))  # 14 days
    _loan(
        db_session,
        (date(2026, 4, 10), None),
        (date(2026, 5, 10), None),
        (date(2026, 6, 10), None),
    )  # 66 days
    _loan(db_session, (date(2026, 1, 15), None), (date(2026, 2, 15), None))  # 151 days

    report = _report(client, headers)

    assert report["as_of"] == "2026-06-14"
    columns = (
        "bucket",
        "days_past_due_min",
        "days_past_due_max",
        "loans",
        "outstanding",
        "overdue",
        "share_pct",
        "at_risk_pct",
    )
    assert [tuple(row[column] for column in columns) for row in report["buckets"]] == [
        ("current", 0, 0, 2, "3000.00", "0.00", "30.00", "100.00"),
        ("sma_0", 1, 30, 1, "2000.00", "1010.00", "20.00", "70.00"),
        ("sma_1", 31, 60, 0, "0.00", "0.00", "0.00", "50.00"),
        ("sma_2", 61, 90, 1, "3000.00", "3030.00", "30.00", "50.00"),
        ("npa", 91, None, 1, "2000.00", "2020.00", "20.00", "20.00"),
    ]


def test_an_empty_book_still_lists_every_bucket(
    client: TestClient, headers: dict[str, str]
) -> None:
    report = _report(client, headers)

    assert [row["bucket"] for row in report["buckets"]] == [
        "current",
        "sma_0",
        "sma_1",
        "sma_2",
        "npa",
    ]
    for row in report["buckets"]:
        assert (row["loans"], row["outstanding"], row["overdue"]) == (0, "0.00", "0.00")
        assert (row["share_pct"], row["at_risk_pct"]) == (None, None)


def test_defaults_to_the_last_day_end_in_india(
    app: FastAPI, client: TestClient, headers: dict[str, str]
) -> None:
    # 19:00 UTC on 14 June is 00:30 on 15 June in India: the 14 June day-end has run.
    app.dependency_overrides[current_time] = lambda: datetime(2026, 6, 14, 19, 0, tzinfo=UTC)

    assert _report(client, headers)["as_of"] == "2026-06-14"


@pytest.mark.parametrize("as_of", [date(2026, 6, 15), date(2026, 6, 15) + timedelta(days=30)])
def test_the_day_must_have_ended_in_india(
    client: TestClient, headers: dict[str, str], as_of: date
) -> None:
    response = client.get(PATH, params={"as_of": as_of.isoformat()}, headers=headers)

    assert response.status_code == 422
    assert response.json()["code"] == "day_not_ended"
