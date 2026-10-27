"""The loan lifecycle, as the only status changes allowed:

    submitted -> under_review -> approved -> disbursed -> closed
                              -> rejected

Every other change is an IllegalTransition, which the API reports as 409 Conflict.
"""

from app.domain.enums import ApplicationStatus as Status
from app.errors import IllegalTransition

ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.SUBMITTED: frozenset({Status.UNDER_REVIEW}),
    Status.UNDER_REVIEW: frozenset({Status.APPROVED, Status.REJECTED}),
    Status.APPROVED: frozenset({Status.DISBURSED}),
    Status.REJECTED: frozenset(),
    Status.DISBURSED: frozenset({Status.CLOSED}),
    Status.CLOSED: frozenset(),
}


def is_terminal(status: Status) -> bool:
    return not ALLOWED_TRANSITIONS[status]


def check_transition(current: Status, target: Status) -> None:
    """Raise IllegalTransition unless the lifecycle allows moving from `current` to `target`."""
    allowed = ALLOWED_TRANSITIONS[current]
    if target in allowed:
        return
    if is_terminal(current):
        reason = f"{current} is final"
    else:
        reason = f"from {current} it can only move to: {', '.join(sorted(allowed))}"
    raise IllegalTransition(f"Cannot move an application from {current} to {target} ({reason})")
