from decimal import Decimal

from sqlalchemy.orm import Session

from app.errors import ProfileNotFound
from app.models import Applicant, User


def get_profile(user: User) -> Applicant:
    if user.applicant is None:
        raise ProfileNotFound("You have not created an applicant profile yet")
    return user.applicant


def save_profile(
    session: Session,
    user: User,
    *,
    full_name: str,
    monthly_income: Decimal,
    existing_emi: Decimal,
) -> tuple[Applicant, bool]:
    """Create or replace the user's applicant profile. The flag is True when it was created."""
    applicant = user.applicant
    created = applicant is None
    if applicant is None:
        applicant = Applicant(user=user)
        session.add(applicant)
    applicant.full_name = full_name
    applicant.monthly_income = monthly_income
    applicant.existing_emi = existing_emi
    session.commit()
    return applicant, created
