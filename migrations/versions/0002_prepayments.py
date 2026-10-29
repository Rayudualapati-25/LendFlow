"""prepayments

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29 09:18:50.485514
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prepayments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("loan_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "reduce_emi",
                "reduce_tenure",
                "foreclosure",
                name="prepayment_kind",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("principal", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("interest", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("received_by_id", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("interest >= 0", name=op.f("ck_prepayments_interest_non_negative")),
        sa.CheckConstraint("principal > 0", name=op.f("ck_prepayments_principal_positive")),
        sa.ForeignKeyConstraint(
            ["loan_id"], ["loans.id"], name=op.f("fk_prepayments_loan_id_loans")
        ),
        sa.ForeignKeyConstraint(
            ["received_by_id"], ["users.id"], name=op.f("fk_prepayments_received_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prepayments")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_prepayments_idempotency_key")),
    )
    op.create_index(op.f("ix_prepayments_loan_id"), "prepayments", ["loan_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_prepayments_loan_id"), table_name="prepayments")
    op.drop_table("prepayments")
