from itertools import product

import pytest

from app.domain.enums import ApplicationStatus as Status
from app.domain.state_machine import ALLOWED_TRANSITIONS, check_transition, is_terminal
from app.errors import IllegalTransition

# The lifecycle from the README, written out independently of the implementation.
LEGAL = {
    (Status.SUBMITTED, Status.UNDER_REVIEW),
    (Status.UNDER_REVIEW, Status.APPROVED),
    (Status.UNDER_REVIEW, Status.REJECTED),
    (Status.APPROVED, Status.DISBURSED),
    (Status.DISBURSED, Status.CLOSED),
}
ALL_PAIRS = list(product(Status, Status))


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL))
def test_legal_transitions_are_allowed(current: Status, target: Status) -> None:
    check_transition(current, target)


@pytest.mark.parametrize(("current", "target"), [pair for pair in ALL_PAIRS if pair not in LEGAL])
def test_every_other_transition_is_illegal(current: Status, target: Status) -> None:
    with pytest.raises(IllegalTransition) as raised:
        check_transition(current, target)

    assert raised.value.status_code == 409
    assert raised.value.code == "illegal_transition"


def test_the_table_has_exactly_the_legal_transitions() -> None:
    table = {
        (current, target) for current, targets in ALLOWED_TRANSITIONS.items() for target in targets
    }

    assert table == LEGAL


def test_only_rejected_and_closed_are_terminal() -> None:
    assert {status for status in Status if is_terminal(status)} == {Status.REJECTED, Status.CLOSED}


def test_every_status_is_reachable_from_submitted() -> None:
    reached, frontier = {Status.SUBMITTED}, [Status.SUBMITTED]
    while frontier:
        for target in ALLOWED_TRANSITIONS[frontier.pop()] - reached:
            reached.add(target)
            frontier.append(target)

    assert reached == set(Status)


def test_the_error_says_what_is_allowed_instead() -> None:
    with pytest.raises(IllegalTransition) as raised:
        check_transition(Status.APPROVED, Status.UNDER_REVIEW)

    assert raised.value.message == (
        "Cannot move an application from approved to under_review "
        "(from approved it can only move to: disbursed)"
    )


def test_the_error_names_terminal_statuses() -> None:
    with pytest.raises(IllegalTransition, match="rejected is final"):
        check_transition(Status.REJECTED, Status.APPROVED)
