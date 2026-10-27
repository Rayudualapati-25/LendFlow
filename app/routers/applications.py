from typing import Annotated

from fastapi import APIRouter, Query, status

from app.db import SessionDep
from app.dependencies import ApplicantUser, CurrentUser
from app.domain.enums import ApplicationStatus
from app.schemas import ApplicationOut, ApplicationRequest, Error
from app.services import applications

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": Error}, 422: {"model": Error}},
)
def submit_application(
    body: ApplicationRequest, user: ApplicantUser, session: SessionDep
) -> ApplicationOut:
    """Apply for a loan. The product's current rate is quoted and locked in."""
    application = applications.submit(
        session, user, product=body.product, amount=body.amount, tenure_months=body.tenure_months
    )
    return ApplicationOut.model_validate(application)


@router.get("")
def list_applications(
    user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[ApplicationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ApplicationOut]:
    """Applicants see their own applications; underwriters see everyone's. Newest first."""
    found = applications.list_visible_to(
        session, user, status=status_filter, limit=limit, offset=offset
    )
    return [ApplicationOut.model_validate(application) for application in found]


@router.get("/{application_id}", responses={404: {"model": Error}})
def read_application(application_id: int, user: CurrentUser, session: SessionDep) -> ApplicationOut:
    return ApplicationOut.model_validate(applications.get_visible_to(session, user, application_id))
