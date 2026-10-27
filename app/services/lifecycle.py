"""Building blocks for every status change: lock the row, check the move, audit it."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import ApplicationStatus
from app.domain.state_machine import check_transition
from app.errors import ApplicationNotFound
from app.models import Application, User
from app.services import audit


def lock_application(session: Session, application_id: int) -> Application:
    """Load the application and lock its row until the transaction ends.

    A second request for the same application waits here until the first one commits,
    then reads the status that request left behind, so two underwriters can never both
    act on the same "approved" application. `populate_existing` refreshes any copy the
    session already holds, so the check below never sees a stale status.
    """
    application = session.scalars(
        select(Application)
        .where(Application.id == application_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if application is None:
        raise ApplicationNotFound(f"Application {application_id} not found")
    return application


def move(
    session: Session,
    application: Application,
    target: ApplicationStatus,
    *,
    actor: User | None,
    action: str,
    now: datetime,
    details: dict[str, Any] | None = None,
) -> None:
    """Change the status if the lifecycle allows it, recording the change in the audit log.

    The caller commits, so the status change and its audit entry land together or not at all.
    """
    check_transition(application.status, target)
    audit.record(
        session,
        application,
        action=action,
        actor=actor,
        at=now,
        from_status=application.status,
        to_status=target,
        details=details,
    )
    application.status = target
    application.updated_at = now
