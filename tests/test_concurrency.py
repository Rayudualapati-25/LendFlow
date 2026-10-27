"""Two transactions racing on one application, on separate connections.

These tests commit real rows (the rollback-per-test fixture uses a single connection, so it
cannot show one transaction waiting for another) and delete them afterwards.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Thread

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.domain.enums import ApplicationStatus, Role
from app.errors import IdempotencyKeyReused, IllegalTransition
from app.models import Applicant, Application, AuditLog, Loan, Repayment, User
from app.services import applications, loans
from app.services.lifecycle import lock_application, move
from tests.factories import make_applicant, make_application, make_user

NOW = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Committed:
    application_ids: tuple[int, int]
    underwriter_ids: tuple[int, int]


@pytest.fixture
def committed(engine: Engine, request: pytest.FixtureRequest) -> Iterator[Committed]:
    """Two applications (submitted unless parametrised otherwise) and two underwriters."""
    status = getattr(request, "param", ApplicationStatus.SUBMITTED)
    with Session(engine) as session:
        applicant = make_applicant(session)
        pair = [make_application(session, applicant=applicant, status=status) for _ in range(2)]
        underwriters = [make_user(session, role=Role.UNDERWRITER) for _ in range(2)]
        session.commit()
        ids = Committed((pair[0].id, pair[1].id), (underwriters[0].id, underwriters[1].id))
        applicant_id, user_ids = applicant.id, [applicant.user_id, *ids.underwriter_ids]
    try:
        yield ids
    finally:
        with Session(engine) as session:
            booked = select(Loan.id).where(Loan.application_id.in_(ids.application_ids))
            session.execute(delete(Repayment).where(Repayment.loan_id.in_(booked)))
            session.execute(delete(Loan).where(Loan.application_id.in_(ids.application_ids)))
            session.execute(
                delete(AuditLog).where(AuditLog.application_id.in_(ids.application_ids))
            )
            session.execute(delete(Application).where(Application.id.in_(ids.application_ids)))
            session.execute(delete(Applicant).where(Applicant.id == applicant_id))
            session.execute(delete(User).where(User.id.in_(user_ids)))
            session.commit()


def _wait_until_a_transaction_waits_for_a_lock(engine: Engine, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    with engine.connect() as connection:
        while time.monotonic() < deadline:
            waiting = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            ).scalar_one()
            if waiting:
                return
            time.sleep(0.01)
    raise AssertionError("the second transaction never waited for a lock")


def _loans_for(engine: Engine, application_id: int) -> int:
    with Session(engine) as session:
        count = session.scalar(
            select(func.count()).select_from(Loan).where(Loan.application_id == application_id)
        )
    return count or 0


def test_a_second_reviewer_waits_for_the_first_and_then_gets_409(
    engine: Engine, committed: Committed
) -> None:
    application_id = committed.application_ids[0]
    first_id, second_id = committed.underwriter_ids
    outcome: list[str] = []

    def second_reviewer() -> None:
        with Session(engine, expire_on_commit=False) as session:
            underwriter = session.get_one(User, second_id)
            try:
                applications.start_review(session, underwriter, application_id, now=NOW)
                outcome.append("reviewed")
            except IllegalTransition:
                outcome.append("illegal transition")

    with Session(engine, expire_on_commit=False) as first:
        application = lock_application(first, application_id)
        racer = Thread(target=second_reviewer)
        racer.start()
        _wait_until_a_transaction_waits_for_a_lock(engine)

        move(
            first,
            application,
            ApplicationStatus.UNDER_REVIEW,
            actor=first.get_one(User, first_id),
            action="application.review_started",
            now=NOW,
        )
        first.commit()
        racer.join(timeout=10)

    assert outcome == ["illegal transition"]
    with Session(engine) as session:
        status = session.get_one(Application, application_id).status
        audit_entries = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.application_id == application_id)
        )
    assert status == ApplicationStatus.UNDER_REVIEW
    assert audit_entries == 1


@pytest.mark.parametrize("committed", [ApplicationStatus.APPROVED], indirect=True)
def test_a_retry_racing_the_first_disbursal_waits_and_replays_it(
    engine: Engine, committed: Committed
) -> None:
    application_id = committed.application_ids[0]
    first_id, second_id = committed.underwriter_ids
    retries: list[tuple[int, bool]] = []

    def retry() -> None:
        with Session(engine, expire_on_commit=False) as session:
            underwriter = session.get_one(User, second_id)
            loan, replayed = loans.disburse(
                session, underwriter, application_id, idempotency_key="race", now=NOW
            )
            retries.append((loan.id, replayed))

    with Session(engine, expire_on_commit=False) as first:
        # Hold the row, as a slow first request would, while the retry arrives.
        lock_application(first, application_id)
        racer = Thread(target=retry)
        racer.start()
        _wait_until_a_transaction_waits_for_a_lock(engine)

        loan, replayed = loans.disburse(
            first, first.get_one(User, first_id), application_id, idempotency_key="race", now=NOW
        )
        racer.join(timeout=10)

    assert replayed is False
    assert retries == [(loan.id, True)]
    assert _loans_for(engine, application_id) == 1


@pytest.mark.parametrize("committed", [ApplicationStatus.APPROVED], indirect=True)
def test_one_key_on_two_applications_at_once_books_only_one_loan(
    engine: Engine, committed: Committed
) -> None:
    first_application, second_application = committed.application_ids
    first_id, second_id = committed.underwriter_ids
    outcome: list[str] = []

    def second_disbursal() -> None:
        with Session(engine, expire_on_commit=False) as session:
            underwriter = session.get_one(User, second_id)
            try:
                loans.disburse(
                    session, underwriter, second_application, idempotency_key="shared", now=NOW
                )
                outcome.append("booked")
            except IdempotencyKeyReused:
                outcome.append("key reused")

    with Session(engine, expire_on_commit=False) as first:
        # Book the first loan but leave its transaction open, so the key is not yet visible
        # to the second request's lookup; only the unique index can stop it.
        application = lock_application(first, first_application)
        loans.book_loan(
            first, application, first.get_one(User, first_id), idempotency_key="shared", now=NOW
        )
        first.flush()
        racer = Thread(target=second_disbursal)
        racer.start()
        _wait_until_a_transaction_waits_for_a_lock(engine)

        first.commit()
        racer.join(timeout=10)

    assert outcome == ["key reused"]
    assert _loans_for(engine, first_application) == 1
    assert _loans_for(engine, second_application) == 0
    with Session(engine) as session:
        assert session.get_one(Application, second_application).status == ApplicationStatus.APPROVED
