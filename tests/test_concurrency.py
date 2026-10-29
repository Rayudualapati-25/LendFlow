"""Two transactions racing on one application, on separate connections.

These tests commit real rows (the rollback-per-test fixture uses a single connection, so it
cannot show one transaction waiting for another) and delete them afterwards.
"""

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from queue import Queue
from threading import Thread

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.enums import ApplicationStatus, PrepaymentKind, Role
from app.errors import (
    IdempotencyKeyReused,
    IllegalTransition,
    InvalidRefreshToken,
    PrepaymentTooLarge,
    RefreshTokenReused,
    TooManySignInAttempts,
)
from app.models import (
    Applicant,
    Application,
    AuditLog,
    Loan,
    Prepayment,
    RefreshToken,
    Repayment,
    SignInFailure,
    User,
)
from app.services import applications, loans, prepayments, sessions, users
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
            session.execute(delete(Prepayment).where(Prepayment.loan_id.in_(booked)))
            session.execute(delete(Repayment).where(Repayment.loan_id.in_(booked)))
            session.execute(delete(Loan).where(Loan.application_id.in_(ids.application_ids)))
            session.execute(
                delete(AuditLog).where(AuditLog.application_id.in_(ids.application_ids))
            )
            session.execute(delete(Application).where(Application.id.in_(ids.application_ids)))
            session.execute(delete(Applicant).where(Applicant.id == applicant_id))
            session.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
            session.execute(delete(User).where(User.id.in_(user_ids)))
            session.commit()


def _backend_pid(session: Session) -> int:
    pid: int = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
    return pid


def _wait_until_blocked(engine: Engine, *, waiter: int, holder: int) -> None:
    """Wait until backend `waiter` is blocked by a lock that backend `holder` holds.

    Checking the exact pair (not just "someone is waiting") means a test cannot pass early
    because an unrelated backend, such as autovacuum, happened to be waiting on something.
    """
    deadline = time.monotonic() + 10
    with engine.connect() as connection:
        while time.monotonic() < deadline:
            blocked = connection.execute(
                text(
                    "SELECT CAST(:holder AS integer)"
                    " = ANY(pg_blocking_pids(CAST(:waiter AS integer)))"
                ),
                {"holder": holder, "waiter": waiter},
            ).scalar_one()
            if blocked:
                return
            time.sleep(0.01)
    raise AssertionError(f"backend {waiter} was never blocked by backend {holder}")


class _Racer(Thread):
    """Runs `work` in its own session and connection, publishing its backend pid first.

    Any unexpected exception becomes the outcome, so a failing assertion shows it rather
    than losing it inside the thread.
    """

    def __init__(self, engine: Engine, work: Callable[[Session], str]) -> None:
        super().__init__()
        self.engine = engine
        self.work = work
        self.pid: Queue[int] = Queue()
        self.outcome = "did not finish"

    def run(self) -> None:
        try:
            with Session(self.engine, expire_on_commit=False) as session:
                self.pid.put(_backend_pid(session))
                self.outcome = self.work(session)
        except Exception as error:
            self.outcome = f"unexpected {error!r}"


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

    def second_reviewer(session: Session) -> str:
        underwriter = session.get_one(User, second_id)
        try:
            applications.start_review(session, underwriter, application_id, now=NOW)
        except IllegalTransition:
            return "illegal transition"
        return "reviewed"

    with Session(engine, expire_on_commit=False) as first:
        application = lock_application(first, application_id)
        racer = _Racer(engine, second_reviewer)
        racer.start()
        _wait_until_blocked(engine, waiter=racer.pid.get(timeout=10), holder=_backend_pid(first))

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

    assert racer.outcome == "illegal transition"
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

    def retry(session: Session) -> str:
        underwriter = session.get_one(User, second_id)
        loan, replayed = loans.disburse(
            session, underwriter, application_id, idempotency_key="race", now=NOW
        )
        return f"loan {loan.id}, replayed={replayed}"

    with Session(engine, expire_on_commit=False) as first:
        # Hold the row, as a slow first request would, while the retry arrives.
        lock_application(first, application_id)
        racer = _Racer(engine, retry)
        racer.start()
        _wait_until_blocked(engine, waiter=racer.pid.get(timeout=10), holder=_backend_pid(first))

        loan, replayed = loans.disburse(
            first, first.get_one(User, first_id), application_id, idempotency_key="race", now=NOW
        )
        racer.join(timeout=10)

    assert replayed is False
    assert racer.outcome == f"loan {loan.id}, replayed=True"
    assert _loans_for(engine, application_id) == 1


@pytest.mark.parametrize("committed", [ApplicationStatus.APPROVED], indirect=True)
def test_one_key_on_two_applications_at_once_books_only_one_loan(
    engine: Engine, committed: Committed
) -> None:
    first_application, second_application = committed.application_ids
    first_id, second_id = committed.underwriter_ids

    def second_disbursal(session: Session) -> str:
        underwriter = session.get_one(User, second_id)
        try:
            loans.disburse(
                session, underwriter, second_application, idempotency_key="shared", now=NOW
            )
        except IdempotencyKeyReused:
            return "key reused"
        return "booked"

    with Session(engine, expire_on_commit=False) as first:
        # Book the first loan but leave its transaction open: the key is not yet visible to
        # the second request's lookup, so only the unique index can stop it.
        application = lock_application(first, first_application)
        loans.book_loan(
            first, application, first.get_one(User, first_id), idempotency_key="shared", now=NOW
        )
        first.flush()
        racer = _Racer(engine, second_disbursal)
        racer.start()
        # The second request is now waiting on the unique index for the first to finish.
        _wait_until_blocked(engine, waiter=racer.pid.get(timeout=10), holder=_backend_pid(first))

        first.commit()
        racer.join(timeout=10)

    assert racer.outcome == "key reused"
    assert _loans_for(engine, first_application) == 1
    assert _loans_for(engine, second_application) == 0
    with Session(engine) as session:
        assert session.get_one(Application, second_application).status == ApplicationStatus.APPROVED


def _disbursed(engine: Engine, committed: Committed) -> tuple[int, int]:
    """Disburse both applications (Rs 1,00,000 each) and return the two loan ids."""
    with Session(engine, expire_on_commit=False) as session:
        underwriter = session.get_one(User, committed.underwriter_ids[0])
        booked = [
            loans.disburse(
                session, underwriter, application_id, idempotency_key=f"loan-{n}", now=NOW
            )[0]
            for n, application_id in enumerate(committed.application_ids)
        ]
    return booked[0].id, booked[1].id


def _prepaid(engine: Engine, loan_id: int) -> list[Decimal]:
    with Session(engine) as session:
        return list(
            session.scalars(select(Prepayment.principal).where(Prepayment.loan_id == loan_id))
        )


@pytest.mark.parametrize("committed", [ApplicationStatus.APPROVED], indirect=True)
def test_a_second_prepayment_waits_for_the_first_and_sees_the_lower_balance(
    engine: Engine, committed: Committed
) -> None:
    loan_id, _ = _disbursed(engine, committed)
    first_id, second_id = committed.underwriter_ids

    def second_prepayment(session: Session) -> str:
        underwriter = session.get_one(User, second_id)
        try:
            prepayments.prepay(
                session,
                underwriter,
                loan_id,
                principal=Decimal("60000.00"),
                reduce="emi",
                idempotency_key="second",
                now=NOW,
            )
        except PrepaymentTooLarge:
            return "too large"
        return "prepaid"

    with Session(engine, expire_on_commit=False) as first:
        lock_application(first, committed.application_ids[0])
        racer = _Racer(engine, second_prepayment)
        racer.start()
        _wait_until_blocked(engine, waiter=racer.pid.get(timeout=10), holder=_backend_pid(first))

        prepayments.prepay(
            first,
            first.get_one(User, first_id),
            loan_id,
            principal=Decimal("60000.00"),
            reduce="emi",
            idempotency_key="first",
            now=NOW,
        )
        racer.join(timeout=10)

    # Rs 40,000 was left after the first, so a second Rs 60,000 is refused instead of
    # both reading Rs 1,00,000 and prepaying more than was lent.
    assert racer.outcome == "too large"
    assert _prepaid(engine, loan_id) == [Decimal("60000.00")]


@pytest.mark.parametrize("committed", [ApplicationStatus.APPROVED], indirect=True)
def test_one_key_on_two_loans_at_once_prepays_only_one(
    engine: Engine, committed: Committed
) -> None:
    first_loan, second_loan = _disbursed(engine, committed)
    first_id, second_id = committed.underwriter_ids

    def second_prepayment(session: Session) -> str:
        underwriter = session.get_one(User, second_id)
        try:
            prepayments.prepay(
                session,
                underwriter,
                second_loan,
                principal=Decimal("10000.00"),
                reduce="emi",
                idempotency_key="shared",
                now=NOW,
            )
        except IdempotencyKeyReused:
            return "key reused"
        return "prepaid"

    with Session(engine, expire_on_commit=False) as first:
        # Record the first prepayment but leave its transaction open: the key is not yet
        # visible to the second request's lookup, so only the unique index can stop it.
        first.add(
            Prepayment(
                loan_id=first_loan,
                kind=PrepaymentKind.REDUCE_EMI,
                principal=Decimal("10000.00"),
                interest=Decimal("0.00"),
                idempotency_key="shared",
                received_by_id=first_id,
                received_at=NOW,
            )
        )
        first.flush()
        racer = _Racer(engine, second_prepayment)
        racer.start()
        _wait_until_blocked(engine, waiter=racer.pid.get(timeout=10), holder=_backend_pid(first))

        first.commit()
        racer.join(timeout=10)

    assert racer.outcome == "key reused"
    assert _prepaid(engine, second_loan) == []
    with Session(engine) as session:
        untouched = session.get_one(Loan, second_loan)
        assert untouched.outstanding_principal == Decimal("100000.00")


def test_two_refreshes_with_one_token_at_once_end_the_session(
    engine: Engine, committed: Committed, settings: Settings
) -> None:
    with Session(engine, expire_on_commit=False) as session:
        user = session.get_one(User, committed.underwriter_ids[0])
        token, _ = sessions.start(session, user, now=NOW, settings=settings)

    def second_refresh(session: Session) -> str:
        try:
            sessions.rotate(session, token, now=NOW, settings=settings)
        except RefreshTokenReused:
            return "reused"
        return "rotated"

    with Session(engine, expire_on_commit=False) as first:
        # Hold the token's row, as a slow first refresh would, while the second arrives.
        first.scalars(select(RefreshToken).with_for_update()).all()
        racer = _Racer(engine, second_refresh)
        racer.start()
        _wait_until_blocked(engine, waiter=racer.pid.get(timeout=10), holder=_backend_pid(first))

        _, next_token, _ = sessions.rotate(first, token, now=NOW, settings=settings)
        racer.join(timeout=10)

    # The second request saw the token already used: two holders, so the session is over,
    # including the token the first request just received.
    assert racer.outcome == "reused"
    with Session(engine) as session, pytest.raises(InvalidRefreshToken):
        sessions.rotate(session, next_token, now=NOW, settings=settings)


@pytest.fixture
def guessed_email(engine: Engine) -> Iterator[str]:
    """An email with four failed sign-ins already: one more reaches the limit of five."""
    email = "parallel-guesses@example.com"
    with Session(engine) as session:
        session.add_all([SignInFailure(email=email, failed_at=NOW) for _ in range(4)])
        session.commit()
    yield email
    with Session(engine) as session:
        session.execute(delete(SignInFailure).where(SignInFailure.email == email))
        session.commit()


def test_parallel_sign_ins_for_one_email_take_turns_at_the_limit(
    engine: Engine, guessed_email: str, settings: Settings
) -> None:
    def second_guess(session: Session) -> str:
        try:
            users.sign_in(session, guessed_email, "second wrong guess", now=NOW, settings=settings)
        except TooManySignInAttempts:
            return "refused"
        return "checked"

    with Session(engine, expire_on_commit=False) as first:
        # Take the email's lock, as a first attempt busy checking its password would.
        first.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(guessed_email, 0))))
        racer = _Racer(engine, second_guess)
        racer.start()
        _wait_until_blocked(engine, waiter=racer.pid.get(timeout=10), holder=_backend_pid(first))

        users.sign_in(first, guessed_email, "first wrong guess", now=NOW, settings=settings)
        racer.join(timeout=10)

    # The second guess waited, then counted the first one's failure: five, so refused. In
    # parallel, both would have counted four and both passwords would have been checked.
    assert racer.outcome == "refused"
