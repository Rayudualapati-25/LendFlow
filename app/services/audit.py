from typing import Any

from sqlalchemy.orm import Session

from app.domain.enums import ApplicationStatus
from app.models import Application, AuditLog, User


def record(
    session: Session,
    application: Application,
    *,
    action: str,
    actor: User | None,
    from_status: ApplicationStatus | None = None,
    to_status: ApplicationStatus | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit entry to the caller's transaction, so it commits with the change itself."""
    entry = AuditLog(
        application=application,
        action=action,
        actor_id=actor.id if actor else None,
        from_status=from_status,
        to_status=to_status,
        details=details or {},
    )
    session.add(entry)
    return entry
