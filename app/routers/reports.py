from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app import reports
from app.db import SessionDep
from app.dependencies import NowDep, UnderwriterUser
from app.domain.dates import last_completed_day
from app.errors import DayNotEnded
from app.schemas import (
    ApprovalRateRow,
    Error,
    OutstandingRow,
    OverdueBucketRow,
    OverdueReport,
)

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


@router.get("/overdue-by-bucket", responses={422: {"model": Error}})
def overdue_by_bucket(
    _: UnderwriterUser,
    session: SessionDep,
    now: NowDep,
    as_of: Annotated[
        date | None,
        Query(description="A day in India that has ended. Defaults to the latest one."),
    ] = None,
) -> OverdueReport:
    """The book at a day-end in India by days past due, in RBI's buckets: current, SMA-0
    (1-30 days), SMA-1 (31-60), SMA-2 (61-90) and NPA (over 90). A loan's days past due
    count from its oldest unpaid instalment, its due date being day 1."""
    latest = last_completed_day(now)
    if as_of is None:
        as_of = latest
    elif as_of > latest:
        raise DayNotEnded(f"Choose a day that has ended in India: {latest} or earlier")
    rows = reports.overdue_by_bucket(session, as_of=as_of)
    return OverdueReport(
        as_of=as_of, buckets=[OverdueBucketRow.model_validate(dict(row)) for row in rows]
    )
