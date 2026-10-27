from typing import Annotated

from fastapi import APIRouter, Query

from app.db import SessionDep
from app.dependencies import CurrentUser, NowDep
from app.schemas import Error, LoanOut, RepaymentRequest
from app.services import loans

router = APIRouter(prefix="/loans", tags=["loans"])


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
    """The loan, its outstanding principal and its full EMI schedule."""
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
