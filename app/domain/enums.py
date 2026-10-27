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
