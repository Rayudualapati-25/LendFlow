"""Builders for valid rows. Each accepts overrides, so a test states only what it cares about."""

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from app.domain.enums import ApplicationStatus, DecisionOutcome, InstallmentStatus, Product, Role
from app.models import Applicant, Application, Decision, Loan, Repayment, User

Row = TypeVar("Row")
_sequence = count(1)


def _missing(overrides: dict[str, Any], relation: str) -> bool:
    """True when the caller set neither the relationship nor its foreign-key column."""
    return relation not in overrides and f"{relation}_id" not in overrides


def _save(session: Session, row: Row) -> Row:
    session.add(row)
    session.flush()
    return row


def make_user(session: Session, **overrides: Any) -> User:
    fields: dict[str, Any] = {
        "email": f"user{next(_sequence)}@example.com",
        "password_hash": "not-a-real-hash",
        "role": Role.APPLICANT,
    }
    return _save(session, User(**(fields | overrides)))


def make_applicant(session: Session, **overrides: Any) -> Applicant:
    fields: dict[str, Any] = {
        "full_name": "Test Applicant",
        "monthly_income": Decimal("80000.00"),
        "existing_emi": Decimal("0.00"),
    }
    if _missing(overrides, "user"):
        fields["user"] = make_user(session)
    return _save(session, Applicant(**(fields | overrides)))


def make_application(session: Session, **overrides: Any) -> Application:
    fields: dict[str, Any] = {
        "product": Product.PERSONAL,
        "amount": Decimal("100000.00"),
        "tenure_months": 12,
        "annual_rate_pct": Decimal("14.00"),
        "status": ApplicationStatus.SUBMITTED,
    }
    if _missing(overrides, "applicant"):
        fields["applicant"] = make_applicant(session)
    return _save(session, Application(**(fields | overrides)))


def make_decision(session: Session, **overrides: Any) -> Decision:
    fields: dict[str, Any] = {
        "outcome": DecisionOutcome.APPROVED,
        "reason_codes": ["ELIGIBLE"],
        "credit_score": 750,
        "monthly_income": Decimal("80000.00"),
        "existing_emi": Decimal("0.00"),
        "proposed_emi": Decimal("8978.71"),
        "foir": Decimal("0.1122"),
        "active_loans": 0,
        "rules_version": "test",
        "decided_at": datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
    }
    if _missing(overrides, "application"):
        fields["application"] = make_application(session, status=ApplicationStatus.APPROVED)
    if "decided_by_id" not in overrides:
        fields["decided_by_id"] = make_user(session, role=Role.UNDERWRITER).id
    return _save(session, Decision(**(fields | overrides)))


def make_loan(session: Session, **overrides: Any) -> Loan:
    fields: dict[str, Any] = {
        "principal": Decimal("100000.00"),
        "annual_rate_pct": Decimal("14.00"),
        "tenure_months": 12,
        "emi": Decimal("8978.71"),
        "idempotency_key": f"disburse-{next(_sequence)}",
        "disbursed_at": datetime(2026, 1, 20, 10, 0, tzinfo=UTC),
    }
    if _missing(overrides, "application"):
        fields["application"] = make_application(session, status=ApplicationStatus.DISBURSED)
    if "disbursed_by_id" not in overrides:
        fields["disbursed_by_id"] = make_user(session, role=Role.UNDERWRITER).id
    return _save(session, Loan(**(fields | overrides)))


def make_repayment(session: Session, **overrides: Any) -> Repayment:
    fields: dict[str, Any] = {
        "installment_number": 1,
        "due_date": date(2026, 2, 20),
        "amount_due": Decimal("8978.71"),
        "principal_component": Decimal("7811.04"),
        "interest_component": Decimal("1167.67"),
        "closing_balance": Decimal("92188.96"),
        "status": InstallmentStatus.DUE,
    }
    if _missing(overrides, "loan"):
        fields["loan"] = make_loan(session)
    return _save(session, Repayment(**(fields | overrides)))
