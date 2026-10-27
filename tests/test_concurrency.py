"""Two transactions racing on one application, on separate connections.

These tests commit real rows (the rollback-per-test fixture uses a single connection, so it
cannot show one transaction waiting for another) and delete them afterwards.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Thread

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.domain.enums import ApplicationStatus, Role
from app.errors import IllegalTransition
from app.models import Applicant, Application, AuditLog, User
from app.services import applications
from app.services.lifecycle import lock_application, move
from tests.factories import make_applicant, make_application, make_user


@dataclass(frozen=True)
class Committed:
    application_id: int
    underwriter_ids: tuple[int, int]


@pytest.fixture
def committed(engine: Engine) -> Iterator[Committed]:
    with Session(engine) as session:
        applicant = make_applicant(session)
        application = make_application(session, applicant=applicant)
        underwriters = (
            make_user(session, role=Role.UNDERWRITER),
            make_user(session, role=Role.UNDERWRITER),
        )
        session.commit()
        ids = Committed(application.id, (underwriters[0].id, underwriters[1].id))
        user_ids = [applicant.user_id, *ids.underwriter_ids]
        applicant_id = applicant.id
    try:
        yield ids
    finally:
        with Session(engine) as session:
            session.execute(delete(AuditLog).where(AuditLog.application_id == ids.application_id))
            session.execute(delete(Application).where(Application.id == ids.application_id))
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
    raise AssertionError("the second transaction never waited for the row lock")


def test_a_second_reviewer_waits_for_the_first_and_then_gets_409(
    engine: Engine, committed: Committed
) -> None:
    first_id, second_id = committed.underwriter_ids
    outcome: list[str] = []

    def second_reviewer() -> None:
        with Session(engine, expire_on_commit=False) as session:
            underwriter = session.get_one(User, second_id)
            try:
                applications.start_review(session, underwriter, committed.application_id)
                outcome.append("reviewed")
            except IllegalTransition:
                outcome.append("illegal transition")

    with Session(engine, expire_on_commit=False) as first:
        application = lock_application(first, committed.application_id)
        racer = Thread(target=second_reviewer)
        racer.start()
        _wait_until_a_transaction_waits_for_a_lock(engine)

        move(
            first,
            application,
            ApplicationStatus.UNDER_REVIEW,
            actor=first.get_one(User, first_id),
            action="application.review_started",
        )
        first.commit()
        racer.join(timeout=10)

    assert outcome == ["illegal transition"]
    with Session(engine) as session:
        status = session.get_one(Application, committed.application_id).status
        audit_entries = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.application_id == committed.application_id)
        )
    assert status == ApplicationStatus.UNDER_REVIEW
    assert audit_entries == 1
