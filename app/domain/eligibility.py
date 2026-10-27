"""Credit eligibility: three rules, each with a reason code that explains a refusal.

Every rule runs even after one fails, so a rejection names every problem at once, and an
approval carries ELIGIBLE: no decision is ever made without a reason code.
"""

from dataclasses import dataclass
from decimal import ROUND_UP, Decimal
from enum import StrEnum

# Stored on every decision. Change it whenever a rule or threshold changes.
RULES_VERSION = "2026.10"

MIN_CREDIT_SCORE = 650
# Fixed obligations to income ratio: every monthly EMI, this loan's included, over income.
MAX_FOIR = Decimal("0.50")
# LendFlow loans already approved or disbursed; the applicant may not hold a third.
MAX_ACTIVE_LOANS = 2

FOIR_PLACES = Decimal("0.0001")


class ReasonCode(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    CREDIT_SCORE_BELOW_MINIMUM = "CREDIT_SCORE_BELOW_MINIMUM"
    FOIR_ABOVE_LIMIT = "FOIR_ABOVE_LIMIT"
    ACTIVE_LOAN_LIMIT_REACHED = "ACTIVE_LOAN_LIMIT_REACHED"


@dataclass(frozen=True)
class Facts:
    """Everything the rules look at. Each decision stores a copy of these."""

    credit_score: int
    monthly_income: Decimal
    # EMIs the applicant already pays: other lenders' plus LendFlow loans approved or disbursed.
    existing_emi: Decimal
    proposed_emi: Decimal
    active_loans: int


@dataclass(frozen=True)
class Assessment:
    approved: bool
    reason_codes: tuple[ReasonCode, ...]
    foir: Decimal


def assess(facts: Facts) -> Assessment:
    obligations = facts.existing_emi + facts.proposed_emi
    failures = []
    if facts.credit_score < MIN_CREDIT_SCORE:
        failures.append(ReasonCode.CREDIT_SCORE_BELOW_MINIMUM)
    # Compared unrounded: exactly 50% passes, one paisa more does not.
    if obligations > facts.monthly_income * MAX_FOIR:
        failures.append(ReasonCode.FOIR_ABOVE_LIMIT)
    if facts.active_loans >= MAX_ACTIVE_LOANS:
        failures.append(ReasonCode.ACTIVE_LOAN_LIMIT_REACHED)

    # Rounded up for display, so a FOIR over the limit can never show as 0.5000.
    foir = (obligations / facts.monthly_income).quantize(FOIR_PLACES, rounding=ROUND_UP)
    if failures:
        return Assessment(approved=False, reason_codes=tuple(failures), foir=foir)
    return Assessment(approved=True, reason_codes=(ReasonCode.ELIGIBLE,), foir=foir)
