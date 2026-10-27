from fastapi import APIRouter, Response, status

from app.db import SessionDep
from app.dependencies import ApplicantUser
from app.schemas import Error, ProfileOut, ProfileRequest
from app.services import applicants

router = APIRouter(prefix="/applicants", tags=["applicants"])


@router.put("/me", responses={201: {"model": ProfileOut, "description": "Profile created"}})
def save_my_profile(
    body: ProfileRequest, user: ApplicantUser, session: SessionDep, response: Response
) -> ProfileOut:
    """Create (201) or replace (200) the caller's profile. Income is self-declared."""
    applicant, created = applicants.save_profile(
        session,
        user,
        full_name=body.full_name,
        monthly_income=body.monthly_income,
        existing_emi=body.existing_emi,
    )
    if created:
        response.status_code = status.HTTP_201_CREATED
    return ProfileOut.model_validate(applicant)


@router.get("/me", responses={404: {"model": Error}})
def read_my_profile(user: ApplicantUser) -> ProfileOut:
    return ProfileOut.model_validate(applicants.get_profile(user))
