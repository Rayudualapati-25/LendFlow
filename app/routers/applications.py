from typing import Annotated

from fastapi import APIRouter, Query, status

from app.db import SessionDep
from app.dependencies import ApplicantUser, CurrentUser, NowDep, UnderwriterUser
from app.domain.enums import ApplicationStatus
from app.schemas import (
    ApplicationOut,
    ApplicationRequest,
    AuditEntryOut,
    DecisionOut,
    DecisionRequest,
    Error,
)
from app.services import applications, decisions

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


@router.post("/{application_id}/review", responses={404: {"model": Error}, 409: {"model": Error}})
def start_review(
    application_id: int, underwriter: UnderwriterUser, session: SessionDep
) -> ApplicationOut:
    """Take a submitted application into review (submitted -> under_review)."""
    return ApplicationOut.model_validate(
        applications.start_review(session, underwriter, application_id)
    )


@router.get("/{application_id}/audit", responses={404: {"model": Error}})
def read_audit_trail(
    application_id: int, _: UnderwriterUser, session: SessionDep
) -> list[AuditEntryOut]:
    """Every recorded change to the application, oldest first."""
    return [
        AuditEntryOut.model_validate(entry)
        for entry in applications.audit_trail(session, application_id)
    ]


@router.post(
    "/{application_id}/decision",
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": Error}, 409: {"model": Error}},
)
def decide(
    application_id: int,
    body: DecisionRequest,
    underwriter: UnderwriterUser,
    session: SessionDep,
    now: NowDep,
) -> DecisionOut:
    """Run the eligibility rules and approve or reject (under_review -> approved | rejected).

    Every decision carries reason codes and the inputs the rules saw.
    """
    decision = decisions.decide(
        session, underwriter, application_id, credit_score=body.credit_score, now=now
    )
    return DecisionOut.model_validate(decision)


@router.get("/{application_id}/decision", responses={404: {"model": Error}})
def read_decision(application_id: int, user: CurrentUser, session: SessionDep) -> DecisionOut:
    """The decision and its reasons, for the applicant who applied or any underwriter."""
    return DecisionOut.model_validate(decisions.get_visible_to(session, user, application_id))
