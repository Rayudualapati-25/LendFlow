"""sign in failures

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29 10:19:02.508319
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sign_in_failures",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sign_in_failures")),
    )
    op.create_index(
        "ix_sign_in_failures_email_failed_at",
        "sign_in_failures",
        ["email", "failed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sign_in_failures_failed_at"), "sign_in_failures", ["failed_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sign_in_failures_failed_at"), table_name="sign_in_failures")
    op.drop_index("ix_sign_in_failures_email_failed_at", table_name="sign_in_failures")
    op.drop_table("sign_in_failures")
