"""Database tables.

Money is NUMERIC(12, 2), never float. Timestamps are timezone-aware and stored in UTC.
Invariants that must hold whatever the application code does are CHECK constraints.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import ApplicationStatus, DecisionOutcome, InstallmentStatus, Product, Role

# Predictable names keep migrations reviewable and make constraint errors easy to trace.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

IntPK = Annotated[int, mapped_column(BigInteger, Identity(), primary_key=True)]
Money = Annotated[Decimal, mapped_column(Numeric(12, 2))]
RatePct = Annotated[Decimal, mapped_column(Numeric(5, 2))]
Timestamp = Annotated[datetime, mapped_column(DateTime(timezone=True))]
CreatedAt = Annotated[datetime, mapped_column(DateTime(timezone=True), server_default=func.now())]


def _enum(enum_class: type[StrEnum], name: str) -> Enum:
    """Store enum values as VARCHAR plus a CHECK constraint rather than a native Postgres enum."""
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    """A login. Applicants also have an Applicant profile; underwriters do not."""

    __tablename__ = "users"

    id: Mapped[IntPK]
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(_enum(Role, "role"))
    created_at: Mapped[CreatedAt]

    applicant: Mapped["Applicant | None"] = relationship(back_populates="user")


class Applicant(Base):
    """A borrower's profile. Income and other lenders' EMIs are self-declared in this MVP."""

    __tablename__ = "applicants"
    __table_args__ = (
        CheckConstraint("monthly_income > 0", name="monthly_income_positive"),
        CheckConstraint("existing_emi >= 0", name="existing_emi_non_negative"),
    )

    id: Mapped[IntPK]
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    monthly_income: Mapped[Money]
    # Monthly EMIs the applicant already pays on loans held with other lenders.
    existing_emi: Mapped[Money]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt] = mapped_column(onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="applicant")
    applications: Mapped[list["Application"]] = relationship(back_populates="applicant")


class Application(Base):
    """A request for a loan. Its status follows the loan through disbursal and closure."""

    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("tenure_months BETWEEN 1 AND 360", name="tenure_months_range"),
        CheckConstraint("annual_rate_pct BETWEEN 0 AND 100", name="annual_rate_pct_range"),
        Index("ix_applications_status_created_at", "status", "created_at"),
    )

    id: Mapped[IntPK]
    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicants.id"), index=True)
    product: Mapped[Product] = mapped_column(_enum(Product, "product"))
    amount: Mapped[Money]
    tenure_months: Mapped[int]
    # Percent per annum, copied from the product catalogue when the application is submitted.
    annual_rate_pct: Mapped[RatePct]
    status: Mapped[ApplicationStatus] = mapped_column(
        _enum(ApplicationStatus, "application_status"), default=ApplicationStatus.SUBMITTED
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt] = mapped_column(onupdate=func.now())

    applicant: Mapped[Applicant] = relationship(back_populates="applications")
    decision: Mapped["Decision | None"] = relationship(back_populates="application")
    loan: Mapped["Loan | None"] = relationship(back_populates="application")


class Decision(Base):
    """The rules' verdict plus every input they used, so the decision can be explained later."""

    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint("cardinality(reason_codes) > 0", name="has_reason_code"),
        CheckConstraint("credit_score BETWEEN 300 AND 900", name="credit_score_range"),
        CheckConstraint("foir >= 0", name="foir_non_negative"),
        CheckConstraint("active_loans >= 0", name="active_loans_non_negative"),
    )

    id: Mapped[IntPK]
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), unique=True)
    outcome: Mapped[DecisionOutcome] = mapped_column(_enum(DecisionOutcome, "decision_outcome"))
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    credit_score: Mapped[int]
    monthly_income: Mapped[Money]
    # All monthly obligations before this loan: other lenders' EMIs plus active LendFlow loans.
    existing_emi: Mapped[Money]
    proposed_emi: Mapped[Money]
    # Fixed obligations to income ratio: (existing_emi + proposed_emi) / monthly_income.
    foir: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    active_loans: Mapped[int]
    rules_version: Mapped[str] = mapped_column(String(20))
    decided_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[Timestamp] = mapped_column(index=True)

    application: Mapped[Application] = relationship(back_populates="decision")


class Loan(Base):
    """The booked contract, created when an approved application is disbursed."""

    __tablename__ = "loans"
    __table_args__ = (
        CheckConstraint("principal > 0", name="principal_positive"),
        CheckConstraint("emi > 0", name="emi_positive"),
        CheckConstraint("tenure_months BETWEEN 1 AND 360", name="tenure_months_range"),
        CheckConstraint("annual_rate_pct BETWEEN 0 AND 100", name="annual_rate_pct_range"),
    )

    id: Mapped[IntPK]
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), unique=True)
    principal: Mapped[Money]
    annual_rate_pct: Mapped[RatePct]
    tenure_months: Mapped[int]
    emi: Mapped[Money]
    # Client-supplied key that makes a retried disbursement return the first result.
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    disbursed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    disbursed_at: Mapped[Timestamp] = mapped_column(index=True)

    application: Mapped[Application] = relationship(back_populates="loan")
    repayments: Mapped[list["Repayment"]] = relationship(
        back_populates="loan", order_by="Repayment.installment_number"
    )


class Repayment(Base):
    """One instalment of a loan's EMI schedule, and whether it has been paid."""

    __tablename__ = "repayments"
    __table_args__ = (
        UniqueConstraint("loan_id", "installment_number"),
        CheckConstraint("installment_number >= 1", name="installment_number_positive"),
        CheckConstraint(
            "principal_component >= 0 AND interest_component >= 0",
            name="components_non_negative",
        ),
        CheckConstraint(
            "amount_due = principal_component + interest_component",
            name="amount_due_is_principal_plus_interest",
        ),
        CheckConstraint("closing_balance >= 0", name="closing_balance_non_negative"),
        CheckConstraint("(status = 'paid') = (paid_at IS NOT NULL)", name="paid_at_matches_status"),
        Index("ix_repayments_status_due_date", "status", "due_date"),
    )

    id: Mapped[IntPK]
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"))
    installment_number: Mapped[int]
    due_date: Mapped[date]
    amount_due: Mapped[Money]
    principal_component: Mapped[Money]
    interest_component: Mapped[Money]
    closing_balance: Mapped[Money]
    status: Mapped[InstallmentStatus] = mapped_column(
        _enum(InstallmentStatus, "installment_status"), default=InstallmentStatus.DUE
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    loan: Mapped[Loan] = relationship(back_populates="repayments")


class AuditLog(Base):
    """Append-only history of state changes, written in the same transaction as the change."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_application_id_created_at", "application_id", "created_at"),
    )

    id: Mapped[IntPK]
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))
    action: Mapped[str] = mapped_column(String(50))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[CreatedAt] = mapped_column(index=True)

    application: Mapped[Application] = relationship()
