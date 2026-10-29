"""The schema is built only by Alembic migrations, and enforces invariants on its own.

Constraint tests insert bad rows directly, bypassing the API, to prove the database refuses
them even if application code has a bug.
"""

from decimal import Decimal

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import InstallmentStatus
from app.models import Base
from tests.factories import (
    make_application,
    make_decision,
    make_loan,
    make_repayment,
    make_user,
)

EXPECTED_TABLES = {
    "users",
    "applicants",
    "applications",
    "decisions",
    "loans",
    "repayments",
    "prepayments",
    "audit_log",
}


def test_migrations_create_the_expected_tables(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}

    assert tables == EXPECTED_TABLES


def test_models_and_migrations_describe_the_same_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)

    assert differences == []


def test_migrations_downgrade_to_empty_and_upgrade_again(
    engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}

    command.upgrade(alembic_config, "head")
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("applications", ["status", "created_at"]),
        ("decisions", ["decided_at"]),
        ("loans", ["disbursed_at"]),
        ("repayments", ["status", "due_date"]),
        ("audit_log", ["created_at"]),
    ],
)
def test_status_and_date_columns_are_indexed(
    engine: Engine, table: str, columns: list[str]
) -> None:
    indexed_column_lists = [index["column_names"] for index in inspect(engine).get_indexes(table)]

    assert columns in indexed_column_lists


def test_emails_are_unique(db_session: Session) -> None:
    make_user(db_session, email="same@example.com")

    with pytest.raises(IntegrityError, match="uq_users_email"):
        make_user(db_session, email="same@example.com")


def test_loan_amount_must_be_positive(db_session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_applications_amount_positive"):
        make_application(db_session, amount=Decimal("0.00"))


def test_tenure_must_be_between_1_and_360_months(db_session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_applications_tenure_months_range"):
        make_application(db_session, tenure_months=361)


def test_status_must_be_a_known_value(db_session: Session) -> None:
    application = make_application(db_session)

    with pytest.raises(IntegrityError, match="ck_applications_application_status"):
        db_session.execute(
            text("UPDATE applications SET status = 'teleported' WHERE id = :id"),
            {"id": application.id},
        )


def test_every_decision_carries_at_least_one_reason_code(db_session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_decisions_has_reason_code"):
        make_decision(db_session, reason_codes=[])


def test_credit_score_must_be_in_bureau_range(db_session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_decisions_credit_score_range"):
        make_decision(db_session, credit_score=950)


# These two set the foreign key directly: assigning the relationship instead would make the
# ORM detach the first row (NULL its foreign key) and never reach the unique constraint.
def test_an_application_has_at_most_one_decision(db_session: Session) -> None:
    decision = make_decision(db_session)

    with pytest.raises(IntegrityError, match="uq_decisions_application_id"):
        make_decision(db_session, application_id=decision.application_id)


def test_an_application_is_disbursed_at_most_once(db_session: Session) -> None:
    loan = make_loan(db_session)

    with pytest.raises(IntegrityError, match="uq_loans_application_id"):
        make_loan(db_session, application_id=loan.application_id)


def test_idempotency_keys_are_unique(db_session: Session) -> None:
    make_loan(db_session, idempotency_key="disburse-once")

    with pytest.raises(IntegrityError, match="uq_loans_idempotency_key"):
        make_loan(db_session, idempotency_key="disburse-once")


def test_installment_numbers_are_unique_per_loan(db_session: Session) -> None:
    repayment = make_repayment(db_session, installment_number=1)

    with pytest.raises(IntegrityError, match="uq_repayments_loan_id_installment_number"):
        make_repayment(db_session, loan=repayment.loan, installment_number=1)


def test_installment_amount_is_principal_plus_interest(db_session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_repayments_amount_due_is_principal_plus_interest"):
        make_repayment(
            db_session,
            amount_due=Decimal("1000.00"),
            principal_component=Decimal("900.00"),
            interest_component=Decimal("99.99"),
        )


def test_a_paid_installment_records_when_it_was_paid(db_session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_repayments_paid_at_matches_status"):
        make_repayment(db_session, status=InstallmentStatus.PAID, paid_at=None)
