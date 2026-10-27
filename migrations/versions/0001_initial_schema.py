"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-27 12:34:10.440678
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "applicant",
                "underwriter",
                name="role",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "applicants",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("monthly_income", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("existing_emi", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "existing_emi >= 0", name=op.f("ck_applicants_existing_emi_non_negative")
        ),
        sa.CheckConstraint(
            "monthly_income > 0", name=op.f("ck_applicants_monthly_income_positive")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_applicants_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applicants")),
        sa.UniqueConstraint("user_id", name=op.f("uq_applicants_user_id")),
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("applicant_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "product",
            sa.Enum(
                "personal",
                "vehicle",
                "home",
                name="product",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("tenure_months", sa.Integer(), nullable=False),
        sa.Column("annual_rate_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "submitted",
                "under_review",
                "approved",
                "rejected",
                "disbursed",
                "closed",
                name="application_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_applications_amount_positive")),
        sa.CheckConstraint(
            "annual_rate_pct BETWEEN 0 AND 100", name=op.f("ck_applications_annual_rate_pct_range")
        ),
        sa.CheckConstraint(
            "tenure_months BETWEEN 1 AND 360", name=op.f("ck_applications_tenure_months_range")
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id"],
            ["applicants.id"],
            name=op.f("fk_applications_applicant_id_applicants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
    )
    op.create_index(
        op.f("ix_applications_applicant_id"), "applications", ["applicant_id"], unique=False
    )
    op.create_index(
        "ix_applications_status_created_at", "applications", ["status", "created_at"], unique=False
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_audit_log_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_audit_log_application_id_applications"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        "ix_audit_log_application_id_created_at",
        "audit_log",
        ["application_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_audit_log_created_at"), "audit_log", ["created_at"], unique=False)
    op.create_table(
        "decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "approved",
                "rejected",
                name="decision_outcome",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(length=50)), nullable=False),
        sa.Column("credit_score", sa.Integer(), nullable=False),
        sa.Column("monthly_income", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("existing_emi", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("proposed_emi", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("foir", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("active_loans", sa.Integer(), nullable=False),
        sa.Column("rules_version", sa.String(length=20), nullable=False),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "active_loans >= 0", name=op.f("ck_decisions_active_loans_non_negative")
        ),
        sa.CheckConstraint(
            "cardinality(reason_codes) > 0", name=op.f("ck_decisions_has_reason_code")
        ),
        sa.CheckConstraint(
            "credit_score BETWEEN 300 AND 900", name=op.f("ck_decisions_credit_score_range")
        ),
        sa.CheckConstraint("foir >= 0", name=op.f("ck_decisions_foir_non_negative")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_decisions_application_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"], ["users.id"], name=op.f("fk_decisions_decided_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decisions")),
        sa.UniqueConstraint("application_id", name=op.f("uq_decisions_application_id")),
    )
    op.create_index(op.f("ix_decisions_decided_at"), "decisions", ["decided_at"], unique=False)
    op.create_table(
        "loans",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("principal", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("annual_rate_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("tenure_months", sa.Integer(), nullable=False),
        sa.Column("emi", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("disbursed_by_id", sa.BigInteger(), nullable=False),
        sa.Column("disbursed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "annual_rate_pct BETWEEN 0 AND 100", name=op.f("ck_loans_annual_rate_pct_range")
        ),
        sa.CheckConstraint("emi > 0", name=op.f("ck_loans_emi_positive")),
        sa.CheckConstraint("principal > 0", name=op.f("ck_loans_principal_positive")),
        sa.CheckConstraint(
            "tenure_months BETWEEN 1 AND 360", name=op.f("ck_loans_tenure_months_range")
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_loans_application_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["disbursed_by_id"], ["users.id"], name=op.f("fk_loans_disbursed_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_loans")),
        sa.UniqueConstraint("application_id", name=op.f("uq_loans_application_id")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_loans_idempotency_key")),
    )
    op.create_index(op.f("ix_loans_disbursed_at"), "loans", ["disbursed_at"], unique=False)
    op.create_table(
        "repayments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("loan_id", sa.BigInteger(), nullable=False),
        sa.Column("installment_number", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("amount_due", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("principal_component", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("interest_component", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("closing_balance", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "due",
                "paid",
                name="installment_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'paid') = (paid_at IS NOT NULL)",
            name=op.f("ck_repayments_paid_at_matches_status"),
        ),
        sa.CheckConstraint(
            "amount_due = principal_component + interest_component",
            name=op.f("ck_repayments_amount_due_is_principal_plus_interest"),
        ),
        sa.CheckConstraint(
            "closing_balance >= 0", name=op.f("ck_repayments_closing_balance_non_negative")
        ),
        sa.CheckConstraint(
            "installment_number >= 1", name=op.f("ck_repayments_installment_number_positive")
        ),
        sa.CheckConstraint(
            "principal_component >= 0 AND interest_component >= 0",
            name=op.f("ck_repayments_components_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["loan_id"], ["loans.id"], name=op.f("fk_repayments_loan_id_loans")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repayments")),
        sa.UniqueConstraint(
            "loan_id", "installment_number", name=op.f("uq_repayments_loan_id_installment_number")
        ),
    )
    op.create_index(
        "ix_repayments_status_due_date", "repayments", ["status", "due_date"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_repayments_status_due_date", table_name="repayments")
    op.drop_table("repayments")
    op.drop_index(op.f("ix_loans_disbursed_at"), table_name="loans")
    op.drop_table("loans")
    op.drop_index(op.f("ix_decisions_decided_at"), table_name="decisions")
    op.drop_table("decisions")
    op.drop_index(op.f("ix_audit_log_created_at"), table_name="audit_log")
    op.drop_index("ix_audit_log_application_id_created_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_applications_status_created_at", table_name="applications")
    op.drop_index(op.f("ix_applications_applicant_id"), table_name="applications")
    op.drop_table("applications")
    op.drop_table("applicants")
    op.drop_table("users")
