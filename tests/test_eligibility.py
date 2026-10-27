from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.eligibility import (
    MAX_ACTIVE_LOANS,
    MAX_FOIR,
    MIN_CREDIT_SCORE,
    Facts,
    ReasonCode,
    assess,
)

D = Decimal

# Comfortably eligible: score 760, FOIR 30%, no other loans.
GOOD = Facts(
    credit_score=760,
    monthly_income=D("100000.00"),
    existing_emi=D("10000.00"),
    proposed_emi=D("20000.00"),
    active_loans=0,
)


def test_the_policy_thresholds() -> None:
    assert (650, D("0.50"), 2) == (MIN_CREDIT_SCORE, MAX_FOIR, MAX_ACTIVE_LOANS)


def test_an_eligible_applicant_is_approved_with_a_reason_code() -> None:
    assessment = assess(GOOD)

    assert assessment.approved
    assert assessment.reason_codes == (ReasonCode.ELIGIBLE,)
    assert assessment.foir == D("0.3000")


@pytest.mark.parametrize(("score", "approved"), [(649, False), (650, True), (900, True)])
def test_credit_score_must_be_at_least_650(score: int, approved: bool) -> None:
    assessment = assess(replace(GOOD, credit_score=score))

    assert assessment.approved is approved
    assert (ReasonCode.CREDIT_SCORE_BELOW_MINIMUM in assessment.reason_codes) is not approved


def test_foir_of_exactly_50_percent_is_allowed() -> None:
    assessment = assess(replace(GOOD, existing_emi=D("30000.00"), proposed_emi=D("20000.00")))

    assert assessment.approved
    assert assessment.foir == D("0.5000")


def test_one_paisa_over_50_percent_is_refused_and_the_foir_shows_it() -> None:
    assessment = assess(replace(GOOD, existing_emi=D("30000.01"), proposed_emi=D("20000.00")))

    assert assessment.reason_codes == (ReasonCode.FOIR_ABOVE_LIMIT,)
    # Rounded up, so a refused FOIR never displays as 0.5000.
    assert assessment.foir == D("0.5001")


def test_foir_counts_existing_obligations_not_just_the_new_emi() -> None:
    assessment = assess(replace(GOOD, existing_emi=D("35000.00"), proposed_emi=D("20000.00")))

    assert assessment.reason_codes == (ReasonCode.FOIR_ABOVE_LIMIT,)
    assert assessment.foir == D("0.5500")


@pytest.mark.parametrize(
    ("active_loans", "approved"), [(0, True), (1, True), (2, False), (5, False)]
)
def test_at_most_two_active_loans(active_loans: int, approved: bool) -> None:
    assessment = assess(replace(GOOD, active_loans=active_loans))

    assert assessment.approved is approved


def test_a_rejection_lists_every_failed_rule_in_order() -> None:
    assessment = assess(
        Facts(
            credit_score=580,
            monthly_income=D("40000.00"),
            existing_emi=D("15000.00"),
            proposed_emi=D("10000.00"),
            active_loans=2,
        )
    )

    assert not assessment.approved
    assert assessment.reason_codes == (
        ReasonCode.CREDIT_SCORE_BELOW_MINIMUM,
        ReasonCode.FOIR_ABOVE_LIMIT,
        ReasonCode.ACTIVE_LOAN_LIMIT_REACHED,
    )


money = st.decimals(min_value=D("0"), max_value=D("10000000"), places=2)


@given(
    credit_score=st.integers(min_value=300, max_value=900),
    monthly_income=st.decimals(min_value=D("1000"), max_value=D("10000000"), places=2),
    existing_emi=money,
    proposed_emi=money,
    active_loans=st.integers(min_value=0, max_value=10),
)
def test_every_assessment_explains_itself(
    credit_score: int,
    monthly_income: Decimal,
    existing_emi: Decimal,
    proposed_emi: Decimal,
    active_loans: int,
) -> None:
    facts = Facts(credit_score, monthly_income, existing_emi, proposed_emi, active_loans)

    assessment = assess(facts)

    assert assessment.reason_codes, "every decision needs at least one reason code"
    assert assessment.approved == (assessment.reason_codes == (ReasonCode.ELIGIBLE,))
    assert assessment.approved or ReasonCode.ELIGIBLE not in assessment.reason_codes
    assert (assessment.foir > MAX_FOIR) == (ReasonCode.FOIR_ABOVE_LIMIT in assessment.reason_codes)
