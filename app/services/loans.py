from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import violated_constraint
from app.domain.dates import business_date
from app.domain.emi import amortization_schedule, calculate_emi
from app.domain.enums import ApplicationStatus, InstallmentStatus, Role
from app.errors import (
    IdempotencyKeyReused,
    InstallmentAlreadyPaid,
    InstallmentNotFound,
    InstallmentOutOfOrder,
    LoanNotFound,
)
from app.models import Applicant, Application, Loan, Repayment, User
from app.services import audit
from app.services.lifecycle import lock_application, move

_KEY_REUSED = "This Idempotency-Key was already used to disburse a different application"


def disburse(
    session: Session,
    underwriter: User,
    application_id: int,
    *,
    idempotency_key: str,
    now: datetime,
) -> tuple[Loan, bool]:
    """Book the loan for an approved application. Returns the loan and whether it is a replay.

    Retrying with the same Idempotency-Key returns the loan the first request booked. Two
    things make that safe under concurrency: the application row lock makes a retry of the
    same disbursal wait for the first and then find its loan, and the unique index on the
    key stops one key from booking loans on two different applications.
    """
    application = lock_application(session, application_id)
    earlier = session.scalars(
        select(Loan).where(Loan.idempotency_key == idempotency_key)
    ).one_or_none()
    if earlier is not None:
        if earlier.application_id != application.id:
            raise IdempotencyKeyReused(_KEY_REUSED)
        return earlier, True

    loan = book_loan(session, application, underwriter, idempotency_key=idempotency_key, now=now)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if violated_constraint(error) == "uq_loans_idempotency_key":
            raise IdempotencyKeyReused(_KEY_REUSED) from error
        raise
    return loan, False


def book_loan(
    session: Session,
    application: Application,
    underwriter: User,
    *,
    idempotency_key: str,
    now: datetime,
) -> Loan:
    """Move a locked, approved application to disbursed and add its loan and EMI schedule.

    The caller commits. The loan copies the application's terms: it is the contract.
    """
    move(
        session,
        application,
        ApplicationStatus.DISBURSED,
        actor=underwriter,
        action="loan.disbursed",
        details={"idempotency_key": idempotency_key},
    )
    principal, rate, tenure = (
        application.amount,
        application.annual_rate_pct,
        application.tenure_months,
    )
    loan = Loan(
        application=application,
        principal=principal,
        annual_rate_pct=rate,
        tenure_months=tenure,
        emi=calculate_emi(principal, rate, tenure),
        idempotency_key=idempotency_key,
        disbursed_by_id=underwriter.id,
        disbursed_at=now,
    )
    loan.repayments = [
        Repayment(
            installment_number=row.number,
            due_date=row.due_date,
            amount_due=row.amount_due,
            principal_component=row.principal,
            interest_component=row.interest,
            closing_balance=row.closing_balance,
            status=InstallmentStatus.DUE,
        )
        for row in amortization_schedule(principal, rate, tenure, start=business_date(now))
    ]
    session.add(loan)
    return loan


def list_visible_to(session: Session, user: User, *, limit: int, offset: int) -> Sequence[Loan]:
    """Newest first. Applicants see only their own loans; underwriters see all."""
    query = (
        select(Loan).order_by(Loan.disbursed_at.desc(), Loan.id.desc()).limit(limit).offset(offset)
    )
    if user.role == Role.APPLICANT:
        query = (
            query.join(Loan.application)
            .join(Application.applicant)
            .where(Applicant.user_id == user.id)
        )
    return session.scalars(query).all()


def get_visible_to(session: Session, user: User, loan_id: int) -> Loan:
    """The loan, if this user may see it; others' loans are reported missing (D30)."""
    loan = session.get(Loan, loan_id)
    if loan is None or not (
        user.role == Role.UNDERWRITER or loan.application.applicant.user_id == user.id
    ):
        raise LoanNotFound(f"Loan {loan_id} not found")
    return loan


def record_repayment(
    session: Session, user: User, loan_id: int, *, installment_number: int, now: datetime
) -> Loan:
    """Mark one instalment paid; paying the last one closes the loan.

    Instalments are paid in order and each only once. Naming the instalment, rather than
    paying "the next one", makes a retried request a harmless 409 instead of a second charge.
    """
    loan = get_visible_to(session, user, loan_id)
    application = lock_application(session, loan.application_id)
    installments = session.scalars(
        select(Repayment)
        .where(Repayment.loan_id == loan.id)
        .order_by(Repayment.installment_number)
        .execution_options(populate_existing=True)
    ).all()

    target = next(
        (row for row in installments if row.installment_number == installment_number), None
    )
    if target is None:
        raise InstallmentNotFound(f"Loan {loan_id} has no instalment {installment_number}")
    if target.status == InstallmentStatus.PAID:
        raise InstallmentAlreadyPaid(f"Instalment {installment_number} is already paid")
    next_due = next(row for row in installments if row.status == InstallmentStatus.DUE)
    if target is not next_due:
        raise InstallmentOutOfOrder(f"Pay instalment {next_due.installment_number} first")

    target.status = InstallmentStatus.PAID
    target.paid_at = now
    audit.record(
        session,
        application,
        action="repayment.received",
        actor=user,
        details={"installment_number": installment_number, "amount": str(target.amount_due)},
    )
    if all(row.status == InstallmentStatus.PAID for row in installments):
        move(session, application, ApplicationStatus.CLOSED, actor=user, action="loan.closed")
    session.commit()
    return loan
