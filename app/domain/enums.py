"""Vocabulary shared by the business rules, the database and the API."""

from enum import StrEnum


class Role(StrEnum):
    APPLICANT = "applicant"
    UNDERWRITER = "underwriter"


class Product(StrEnum):
    PERSONAL = "personal"
    VEHICLE = "vehicle"
    HOME = "home"


class ApplicationStatus(StrEnum):
    """Where a loan is in its life, from application to closure."""

    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISBURSED = "disbursed"
    CLOSED = "closed"


class DecisionOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class InstallmentStatus(StrEnum):
    DUE = "due"
    PAID = "paid"


class PrepaymentKind(StrEnum):
    """Principal paid ahead of the schedule, and what happens to the rest of it."""

    REDUCE_EMI = "reduce_emi"  # same end date, lower EMI
    REDUCE_TENURE = "reduce_tenure"  # same EMI, earlier end date
    FORECLOSURE = "foreclosure"  # everything owed: the loan closes
