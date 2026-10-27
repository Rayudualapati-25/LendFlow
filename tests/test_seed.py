from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app import reports
from app.domain.dates import business_date
from app.domain.enums import ApplicationStatus, DecisionOutcome, InstallmentStatus, Role
from app.models import Application, AuditLog, Base, Decision, Loan, Repayment, User
from app.security import verify_password
from app.seed import DEMO_APPLICANT, DEMO_PASSWORD, DEMO_UNDERWRITER, Summary, seed

UNTIL = date(2026, 10, 26)


@pytest.fixture(scope="module")
def seeded(engine: Engine) -> Iterator[tuple[Session, Summary]]:
    """Seed once for the whole module, inside a transaction that is rolled back at the end.

    Replaying a year takes seconds, and every test here only reads, so they share one run.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session, seed(session, until=UNTIL)
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def db_session(seeded: tuple[Session, Summary]) -> Session:
    return seeded[0]


@pytest.fixture
def summary(seeded: tuple[Session, Summary]) -> Summary:
    return seeded[1]


def _count(session: Session, model: type[Base]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_the_seed_always_produces_the_numbers_in_the_readme(summary: Summary) -> None:
    # Fixed random seed and end date: if this changes, update the README's numbers too.
    assert summary == Summary(
        applicants=60,
        applications=112,
        decisions=108,
        loans=74,
        closed_loans=8,
        installments_paid=353,
    )


def test_the_summary_counts_what_is_in_the_database(summary: Summary, db_session: Session) -> None:
    paid = (
        select(func.count())
        .select_from(Repayment)
        .where(Repayment.status == InstallmentStatus.PAID)
    )
    closed = (
        select(func.count())
        .select_from(Application)
        .where(Application.status == ApplicationStatus.CLOSED)
    )

    assert summary.applications == _count(db_session, Application)
    assert summary.decisions == _count(db_session, Decision)
    assert summary.loans == _count(db_session, Loan)
    assert summary.closed_loans == db_session.scalar(closed)
    assert summary.installments_paid == db_session.scalar(paid)


def test_demo_logins_work_with_the_documented_password(
    summary: Summary, db_session: Session
) -> None:
    for email, role in ((DEMO_UNDERWRITER, Role.UNDERWRITER), (DEMO_APPLICANT, Role.APPLICANT)):
        user = db_session.scalars(select(User).where(User.email == email)).one()
        assert user.role == role
        assert verify_password(DEMO_PASSWORD, user.password_hash)


def test_every_booked_loan_was_approved_and_has_its_full_schedule(
    summary: Summary, db_session: Session
) -> None:
    booked = db_session.scalars(
        select(Application).where(
            Application.status.in_([ApplicationStatus.DISBURSED, ApplicationStatus.CLOSED])
        )
    ).all()

    assert len(booked) == summary.loans
    for application in booked:
        assert application.decision is not None
        assert application.decision.outcome == DecisionOutcome.APPROVED
        assert application.loan is not None
        assert len(application.loan.repayments) == application.tenure_months


def test_closed_loans_are_fully_repaid_and_active_ones_are_not(
    summary: Summary, db_session: Session
) -> None:
    for loan in db_session.scalars(select(Loan)).all():
        unpaid = [row for row in loan.repayments if row.status == InstallmentStatus.DUE]
        if loan.status == ApplicationStatus.CLOSED:
            assert unpaid == []
            assert loan.outstanding_principal == Decimal("0.00")
        else:
            assert unpaid


def test_every_history_runs_forwards_in_time(summary: Summary, db_session: Session) -> None:
    for application in db_session.scalars(select(Application)).all():
        trail = db_session.scalars(
            select(AuditLog).where(AuditLog.application_id == application.id).order_by(AuditLog.id)
        ).all()
        assert trail[0].action == "application.submitted"
        times = [entry.created_at for entry in trail]
        assert times == sorted(times)
        assert business_date(times[-1]) <= UNTIL


def test_borrowers_pay_on_the_due_date(summary: Summary, db_session: Session) -> None:
    payments = db_session.scalars(
        select(Repayment).where(Repayment.status == InstallmentStatus.PAID)
    ).all()

    assert payments
    for payment in payments:
        assert payment.paid_at is not None
        assert business_date(payment.paid_at) == payment.due_date


def test_the_reports_have_a_year_to_show(summary: Summary, db_session: Session) -> None:
    months = reports.approval_rate_by_month(db_session)
    products = reports.outstanding_by_product(db_session)

    assert len(months) >= 12
    assert sum(row["decided"] for row in months) == summary.decisions
    assert {row["product"] for row in products} == {"personal", "vehicle", "home"}
