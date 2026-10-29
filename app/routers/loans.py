from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.db import SessionDep
from app.dependencies import CurrentUser, IdempotencyKey, NowDep
from app.schemas import (
    Error,
    ForeclosureQuote,
    ForeclosureRequest,
    LoanOut,
    PrepaymentRequest,
    RepaymentRequest,
)
from app.services import loans, prepayments

router = APIRouter(prefix="/loans", tags=["loans"])

_PAYMENT_ERRORS: dict[int | str, dict[str, object]] = {
    status: {"model": Error} for status in (400, 404, 409, 422)
}


@router.get("")
def list_loans(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LoanOut]:
    """Borrowers see their own loans; underwriters see all of them. Newest first."""
    found = loans.list_visible_to(session, user, limit=limit, offset=offset)
    return [LoanOut.model_validate(loan) for loan in found]


@router.get("/{loan_id}", responses={404: {"model": Error}})
def read_loan(loan_id: int, user: CurrentUser, session: SessionDep) -> LoanOut:
    """The loan, its outstanding principal, its EMI schedule and any prepayments."""
    return LoanOut.model_validate(loans.get_visible_to(session, user, loan_id))


@router.post("/{loan_id}/repayments", responses={404: {"model": Error}, 409: {"model": Error}})
def record_repayment(
    loan_id: int, body: RepaymentRequest, user: CurrentUser, session: SessionDep, now: NowDep
) -> LoanOut:
    """Pay one instalment, in order. Paying the last one closes the loan.

    The borrower pays their own EMIs; an underwriter can record a payment received
    another way.
    """
    loan = loans.record_repayment(
        session, user, loan_id, installment_number=body.installment_number, now=now
    )
    return LoanOut.model_validate(loan)


@router.post("/{loan_id}/prepayments", responses=_PAYMENT_ERRORS)
def prepay(
    loan_id: int,
    body: PrepaymentRequest,
    user: CurrentUser,
    session: SessionDep,
    now: NowDep,
    key: IdempotencyKey,
    response: Response,
) -> LoanOut:
    """Repay part of the principal ahead of the schedule, once every EMI due is paid.

    Interest on the prepaid principal is charged only for the days of this EMI period
    that have passed. The unpaid instalments are rebuilt: `reduce: "emi"` keeps the end
    date with a lower EMI; `reduce: "tenure"` keeps the EMI and finishes sooner, which
    costs less interest. A retry with the same Idempotency-Key is replayed, not repeated.
    """
    loan, replayed = prepayments.prepay(
        session,
        user,
        loan_id,
        principal=body.principal,
        reduce=body.reduce,
        idempotency_key=key,
        now=now,
    )
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return LoanOut.model_validate(loan)


@router.get("/{loan_id}/foreclosure", responses={404: {"model": Error}, 409: {"model": Error}})
def foreclosure_quote(
    loan_id: int, user: CurrentUser, session: SessionDep, now: NowDep
) -> ForeclosureQuote:
    """What it takes to close the loan today: principal owed plus interest for the days of
    this EMI period so far. The amount changes daily."""
    terms = prepayments.foreclosure_quote(session, user, loan_id, now=now)
    return ForeclosureQuote(
        loan_id=loan_id,
        principal=terms.principal,
        interest=terms.interest,
        amount=terms.amount,
        interest_days=terms.interest_days,
        period_days=terms.period_days,
        valid_on=terms.valid_on,
    )


@router.post("/{loan_id}/foreclosure", responses=_PAYMENT_ERRORS)
def foreclose(
    loan_id: int,
    body: ForeclosureRequest,
    user: CurrentUser,
    session: SessionDep,
    now: NowDep,
    key: IdempotencyKey,
    response: Response,
) -> LoanOut:
    """Pay off the loan and close it, confirming today's quoted amount (409 if it differs).

    The unpaid instalments are cancelled. A retry with the same Idempotency-Key is
    replayed, not repeated.
    """
    loan, replayed = prepayments.foreclose(
        session, user, loan_id, amount=body.amount, idempotency_key=key, now=now
    )
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return LoanOut.model_validate(loan)
