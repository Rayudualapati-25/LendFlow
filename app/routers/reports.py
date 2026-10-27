from fastapi import APIRouter

from app import reports
from app.db import SessionDep
from app.dependencies import UnderwriterUser
from app.schemas import ApprovalRateRow, OutstandingRow

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/approval-rate")
def approval_rate_by_month(_: UnderwriterUser, session: SessionDep) -> list[ApprovalRateRow]:
    """Decisions and approval rate per month in India, with a 3-month rolling rate and the
    change from the previous month. Months without decisions are listed with no rate."""
    rows = reports.approval_rate_by_month(session)
    return [ApprovalRateRow.model_validate(dict(row)) for row in rows]


@router.get("/outstanding-by-product")
def outstanding_by_product(_: UnderwriterUser, session: SessionDep) -> list[OutstandingRow]:
    """Principal still owed on active loans per product, its share of the book and rank."""
    rows = reports.outstanding_by_product(session)
    return [OutstandingRow.model_validate(dict(row)) for row in rows]
