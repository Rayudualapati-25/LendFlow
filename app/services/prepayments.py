"""Paying principal ahead of the EMI schedule: part prepayment and foreclosure (D50-D52).

Both happen within an EMI period, between one due date and the next, and only once every
instalment due so far is paid. The principal paid ahead carries interest for the days of
the period that have passed, and no more (D51).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import violated_constraint
from app.domain.dates import business_date
from app.domain.emi import Installment, amortization_schedule, calculate_emi, schedule_at_emi
from app.domain.enums import ApplicationStatus, InstallmentStatus, PrepaymentKind
from app.domain.prepayment import accrued_interest, days_owed
from app.errors import (
    ForeclosureAmountChanged,
    IdempotencyKeyReused,
    IllegalTransition,
    InstallmentDue,
    PrepaymentTooLarge,
    RemainderTooSmall,
)
from app.models import Loan, Prepayment, Repayment, User
from app.services import audit, loans
from app.services.lifecycle import lock_application, move

_KEY_REUSED = "This Idempotency-Key was already used for a different payment"


@dataclass(frozen=True)
class ForeclosureTerms:
    principal: Decimal
    interest: Decimal
    interest_days: int
    period_days: int
    valid_on: date

    @property
    def amount(self) -> Decimal:
        return self.principal + self.interest


@dataclass(frozen=True)
class _Period:
    """The EMI period a loan is in: from the last due date (or disbursal) to the next one."""

    start: date
    today: date
    unpaid: list[Repayment]  # the instalments still owed; the first one ends the period

    @property
    def end(self) -> date:
        return self.unpaid[0].due_date

    @property
    def outstanding(self) -> Decimal:
        return sum((row.principal_component for row in self.unpaid), Decimal(0))

    @property
    def days_owed(self) -> int:
        return days_owed(period_start=self.start, period_end=self.end, on=self.today)

    def interest_on(self, principal: Decimal, annual_rate_pct: Decimal) -> Decimal:
        return accrued_interest(
            principal, annual_rate_pct, period_start=self.start, period_end=self.end, on=self.today
        )


def foreclosure_quote(
    session: Session, user: User, loan_id: int, *, now: datetime
) -> ForeclosureTerms:
    """What closes the loan today: the principal owed plus interest for the days so far."""
    loan = loans.get_visible_to(session, user, loan_id)
    return _foreclosure_terms(loan, _current_period(loan, now))


def foreclose(
    session: Session,
    user: User,
    loan_id: int,
    *,
    amount: Decimal,
    idempotency_key: str,
    now: datetime,
) -> tuple[Loan, bool]:
    """Pay off the loan and close it. Returns the loan and whether this was a replay.

    The client confirms the amount it showed the borrower; if today's differs (the quote
    was from another day), nothing is paid.
    """

    def same_request(earlier: Prepayment) -> bool:
        return earlier.kind == PrepaymentKind.FORECLOSURE and earlier.amount == amount

    loan = _locked(session, user, loan_id)
    if _already_paid(session, loan, idempotency_key, same_request):
        return loan, True
    period = _current_period(loan, now)
    terms = _foreclosure_terms(loan, period)
    if amount != terms.amount:
        raise ForeclosureAmountChanged(f"The amount to foreclose today is {terms.amount}")

    for row in period.unpaid:
        session.delete(row)
    session.add(
        Prepayment(
            loan=loan,
            kind=PrepaymentKind.FORECLOSURE,
            principal=terms.principal,
            interest=terms.interest,
            idempotency_key=idempotency_key,
            received_by_id=user.id,
            received_at=now,
        )
    )
    move(
        session,
        loan.application,
        ApplicationStatus.CLOSED,
        actor=user,
        action="loan.foreclosed",
        now=now,
        details={
            "principal": str(terms.principal),
            "interest": str(terms.interest),
            "amount": str(terms.amount),
        },
    )
    _commit(session, loan)
    return loan, False


def prepay(
    session: Session,
    user: User,
    loan_id: int,
    *,
    principal: Decimal,
    reduce: Literal["emi", "tenure"],
    idempotency_key: str,
    now: datetime,
) -> tuple[Loan, bool]:
    """Repay part of the principal now and rebuild the unpaid instalments (D50).

    Lowering the EMI keeps the end date; keeping the EMI ends the loan sooner and costs less
    interest. Returns the loan and whether this was a replay.
    """
    kind = PrepaymentKind.REDUCE_EMI if reduce == "emi" else PrepaymentKind.REDUCE_TENURE

    def same_request(earlier: Prepayment) -> bool:
        return earlier.kind == kind and earlier.principal == principal

    loan = _locked(session, user, loan_id)
    if _already_paid(session, loan, idempotency_key, same_request):
        return loan, True
    period = _current_period(loan, now)
    if principal >= period.outstanding:
        raise PrepaymentTooLarge(
            f"Prepay less than the {period.outstanding} still owed, or foreclose the loan"
        )

    balance = period.outstanding - principal
    first, left = period.unpaid[0].installment_number, len(period.unpaid)
    start = business_date(loan.disbursed_at)
    emi_before = loan.emi
    plan: list[Installment]
    if kind == PrepaymentKind.REDUCE_EMI:
        try:
            plan = amortization_schedule(
                balance, loan.annual_rate_pct, left, start, first_number=first
            )
        except ValueError as error:
            raise RemainderTooSmall(
                f"{balance} is too little to spread over {left} months: keep the EMI instead"
            ) from error
        loan.emi = calculate_emi(balance, loan.annual_rate_pct, left)
    else:
        plan = schedule_at_emi(
            balance,
            loan.annual_rate_pct,
            loan.emi,
            start,
            first_number=first,
            max_installments=left,
        )
        loan.tenure_months = first - 1 + len(plan)

    _rewrite(session, period.unpaid, plan)
    interest = period.interest_on(principal, loan.annual_rate_pct)
    session.add(
        Prepayment(
            loan=loan,
            kind=kind,
            principal=principal,
            interest=interest,
            idempotency_key=idempotency_key,
            received_by_id=user.id,
            received_at=now,
        )
    )
    audit.record(
        session,
        loan.application,
        action="loan.prepaid",
        actor=user,
        at=now,
        details={
            "principal": str(principal),
            "interest": str(interest),
            "reduce": reduce,
            "emi": {"before": str(emi_before), "after": str(loan.emi)},
            "installments_left": {"before": left, "after": len(plan)},
        },
    )
    _commit(session, loan)
    return loan, False


def _locked(session: Session, user: User, loan_id: int) -> Loan:
    """The loan, with its application row locked and everything re-read after the lock.

    A payment that committed while this request waited for the lock is then visible: the
    balance, EMI and schedule are the ones this request will change.
    """
    loan = loans.get_visible_to(session, user, loan_id)
    lock_application(session, loan.application_id)
    return session.scalars(
        select(Loan)
        .where(Loan.id == loan.id)
        .options(selectinload(Loan.repayments), selectinload(Loan.prepayments))
        .execution_options(populate_existing=True)
    ).one()


def _already_paid(
    session: Session, loan: Loan, key: str, same_request: Callable[[Prepayment], bool]
) -> bool:
    """True if this key already made this payment; a 422 if it made a different one."""
    earlier = session.scalars(
        select(Prepayment).where(Prepayment.idempotency_key == key)
    ).one_or_none()
    if earlier is None:
        return False
    if earlier.loan_id != loan.id or not same_request(earlier):
        raise IdempotencyKeyReused(_KEY_REUSED)
    return True


def _current_period(loan: Loan, now: datetime) -> _Period:
    if loan.application.status != ApplicationStatus.DISBURSED:
        raise IllegalTransition(
            f"Loan {loan.id} is {loan.application.status}: only an active loan can be paid ahead"
        )
    today = business_date(now)
    unpaid = [row for row in loan.repayments if row.status == InstallmentStatus.DUE]
    next_due = unpaid[0]
    if next_due.due_date <= today:
        raise InstallmentDue(
            f"Pay instalment {next_due.installment_number}, due {next_due.due_date}, first"
        )
    if next_due.installment_number == 1:
        start = business_date(loan.disbursed_at)
    else:
        start = loan.repayments[next_due.installment_number - 2].due_date
    return _Period(start=start, today=today, unpaid=unpaid)


def _foreclosure_terms(loan: Loan, period: _Period) -> ForeclosureTerms:
    principal = period.outstanding
    return ForeclosureTerms(
        principal=principal,
        interest=period.interest_on(principal, loan.annual_rate_pct),
        interest_days=period.days_owed,
        period_days=(period.end - period.start).days,
        valid_on=period.today,
    )


def _rewrite(session: Session, unpaid: list[Repayment], plan: list[Installment]) -> None:
    """Replace the unpaid instalments with `plan`, in place; surplus rows are deleted."""
    for row, installment in zip(unpaid, plan, strict=False):
        row.due_date = installment.due_date
        row.amount_due = installment.amount_due
        row.principal_component = installment.principal
        row.interest_component = installment.interest
        row.closing_balance = installment.closing_balance
    for row in unpaid[len(plan) :]:
        session.delete(row)


def _commit(session: Session, loan: Loan) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if violated_constraint(error) == "uq_prepayments_idempotency_key":
            raise IdempotencyKeyReused(_KEY_REUSED) from error
        raise
    # Deleted instalments are gone from the database but not from the loaded collections.
    session.expire(loan, ["repayments", "prepayments"])
