from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import ApplicationStatus, Product, Role
from app.domain.products import check_terms
from app.errors import ApplicationNotFound, ProfileRequired
from app.models import Applicant, Application, AuditLog, User
from app.services import audit
from app.services.lifecycle import lock_application, move


def submit(
    session: Session,
    user: User,
    *,
    product: Product,
    amount: Decimal,
    tenure_months: int,
    now: datetime,
) -> Application:
    """File a new application at the product's current rate."""
    if user.applicant is None:
        raise ProfileRequired("Create your applicant profile before applying")
    terms = check_terms(product, amount, tenure_months)
    application = Application(
        applicant=user.applicant,
        product=product,
        amount=amount,
        tenure_months=tenure_months,
        annual_rate_pct=terms.annual_rate_pct,
        status=ApplicationStatus.SUBMITTED,
        created_at=now,
        updated_at=now,
    )
    session.add(application)
    audit.record(
        session,
        application,
        action="application.submitted",
        actor=user,
        at=now,
        to_status=ApplicationStatus.SUBMITTED,
    )
    session.commit()
    return application


def list_visible_to(
    session: Session,
    user: User,
    *,
    status: ApplicationStatus | None,
    limit: int,
    offset: int,
) -> Sequence[Application]:
    """Newest first. Applicants see only their own applications; underwriters see all."""
    query = (
        select(Application)
        .order_by(Application.created_at.desc(), Application.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if user.role == Role.APPLICANT:
        query = query.join(Application.applicant).where(Applicant.user_id == user.id)
    if status is not None:
        query = query.where(Application.status == status)
    return session.scalars(query).all()


def get_visible_to(session: Session, user: User, application_id: int) -> Application:
    """The application, if this user may see it. Others' applications are reported missing,
    so a guessed ID reveals nothing about whether it exists."""
    application = session.get(Application, application_id)
    if application is None or not _can_see(user, application):
        raise ApplicationNotFound(f"Application {application_id} not found")
    return application


def _can_see(user: User, application: Application) -> bool:
    return user.role == Role.UNDERWRITER or application.applicant.user_id == user.id


def start_review(
    session: Session, underwriter: User, application_id: int, *, now: datetime
) -> Application:
    """Take a submitted application into review."""
    application = lock_application(session, application_id)
    move(
        session,
        application,
        ApplicationStatus.UNDER_REVIEW,
        actor=underwriter,
        action="application.review_started",
        now=now,
    )
    session.commit()
    return application


def audit_trail(session: Session, application_id: int) -> Sequence[AuditLog]:
    """Every recorded change to the application, oldest first."""
    if session.get(Application, application_id) is None:
        raise ApplicationNotFound(f"Application {application_id} not found")
    return session.scalars(
        select(AuditLog)
        .where(AuditLog.application_id == application_id)
        .order_by(AuditLog.created_at, AuditLog.id)
    ).all()
