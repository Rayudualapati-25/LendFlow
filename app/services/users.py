import math
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import violated_constraint
from app.domain.enums import Role
from app.errors import EmailAlreadyRegistered, TooManySignInAttempts
from app.models import SignInFailure, User
from app.security import hash_password, spend_verification_time, verify_password


def register_applicant(session: Session, email: str, password: str) -> User:
    """Self-service sign-up, which only ever creates applicants (D23)."""
    return create_user(session, email=email, password=password, role=Role.APPLICANT)


def create_user(session: Session, *, email: str, password: str, role: Role) -> User:
    """Create a login. The unique index, not a prior SELECT, settles races on the email."""
    user = User(email=email, password_hash=hash_password(password), role=role)
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


def sign_in(
    session: Session, email: str, password: str, *, now: datetime, settings: Settings
) -> User | None:
    """`authenticate`, allowing only a few failures per email in any 15 minutes (D54).

    Attempts for one email take turns behind an advisory lock, so parallel requests cannot
    all count the same failures and slip past the limit. While the limit is reached every
    attempt is refused with 429, even with the right password, and refusals are not
    counted, so they do not extend the lock. Unknown emails are limited like real ones.
    The caller commits a successful sign-in.
    """
    session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(email, 0))))
    window = timedelta(minutes=settings.sign_in_window_minutes)
    failures = session.scalars(
        select(SignInFailure.failed_at)
        .where(SignInFailure.email == email, SignInFailure.failed_at > now - window)
        .order_by(SignInFailure.failed_at)
    ).all()
    if len(failures) >= settings.sign_in_max_failures:
        # Allowed again once enough of the failures have left the window.
        reopens_at = failures[len(failures) - settings.sign_in_max_failures] + window
        seconds = max(1, math.ceil((reopens_at - now).total_seconds()))
        raise TooManySignInAttempts(
            f"Too many failed sign-ins for this email: try again in {seconds} seconds",
            headers={"Retry-After": str(seconds)},
        )

    user = authenticate(session, email, password)
    if user is None:
        session.add(SignInFailure(email=email, failed_at=now))
        # Failures that no longer count are kept for nobody: delete them as we go.
        session.execute(delete(SignInFailure).where(SignInFailure.failed_at <= now - window))
        session.commit()
        return None
    session.execute(delete(SignInFailure).where(SignInFailure.email == email))
    return user
