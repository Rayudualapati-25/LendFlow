from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import violated_constraint
from app.domain.enums import Role
from app.errors import EmailAlreadyRegistered
from app.models import User
from app.security import hash_password, spend_verification_time, verify_password


def register_applicant(session: Session, email: str, password: str) -> User:
    """Create an applicant login. The unique index, not a prior SELECT, settles races."""
    user = User(email=email, password_hash=hash_password(password), role=Role.APPLICANT)
    session.add(user)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if violated_constraint(error) == "uq_users_email":
            raise EmailAlreadyRegistered("This email is already registered") from error
        raise
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    """The user if the password matches; None for a wrong password or an unknown email."""
    user = session.scalars(select(User).where(User.email == email)).one_or_none()
    if user is None:
        spend_verification_time(password)
        return None
    return user if verify_password(password, user.password_hash) else None
