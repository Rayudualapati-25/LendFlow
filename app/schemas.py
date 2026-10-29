"""Request and response bodies.

Requests forbid unknown fields, so a client cannot slip in fields such as `role` or
`status`. Responses list their fields explicitly, so nothing leaks by accident.
Money is a Decimal with at most two places in requests and is serialised as a string in
responses ("100000.00"), so no client ever parses it into a binary float.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    computed_field,
)

from app.domain.emi import PAISA, calculate_emi
from app.domain.enums import (
    ApplicationStatus,
    DecisionOutcome,
    InstallmentStatus,
    PrepaymentKind,
    Product,
    Role,
)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _two_places(amount: Decimal) -> Decimal:
    return amount.quantize(PAISA)


Money = Annotated[Decimal, Field(max_digits=12, decimal_places=2), AfterValidator(_two_places)]


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResponseBody(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Error(BaseModel):
    detail: str
    code: str


# Accounts


class RegisterRequest(RequestBody):
    email: Annotated[EmailStr, AfterValidator(normalize_email)]
    # Length is the only rule: long passphrases welcome, no composition rules.
    password: str = Field(min_length=12, max_length=128)


class UserOut(ResponseBody):
    id: int
    email: str
    role: Role


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 (a token type, not a secret)
    expires_in: int = Field(description="Seconds until the access token expires.")
    refresh_token: str = Field(description="Exchange at /auth/refresh; it works once.")
    refresh_expires_in: int = Field(description="Seconds until the refresh token expires.")


class RefreshRequest(RequestBody):
    refresh_token: str = Field(min_length=1, max_length=255)


# Applicant profile


class ProfileRequest(RequestBody):
    full_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    monthly_income: Annotated[Money, Field(ge=1000, le=10_000_000)]
    existing_emi: Annotated[
        Money,
        Field(ge=0, le=10_000_000, description="Monthly EMIs paid on loans with other lenders."),
    ]


class ProfileOut(ResponseBody):
    id: int
    full_name: str
    monthly_income: Decimal
    existing_emi: Decimal


# Products and applications


class ProductOut(ResponseBody):
    product: Product
    annual_rate_pct: Decimal
    min_amount: Decimal
    max_amount: Decimal
    min_tenure_months: int
    max_tenure_months: int


class ApplicationRequest(RequestBody):
    product: Product
    amount: Annotated[Money, Field(gt=0)]
    tenure_months: int = Field(ge=1, le=360)


class ApplicationOut(ResponseBody):
    id: int
    product: Product
    amount: Decimal
    tenure_months: int
    annual_rate_pct: Decimal
    status: ApplicationStatus
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emi(self) -> Decimal:
        """The monthly instalment these terms imply."""
        return calculate_emi(self.amount, self.annual_rate_pct, self.tenure_months)


class AuditEntryOut(ResponseBody):
    id: int
    action: str
    actor_id: int | None
    from_status: str | None
    to_status: str | None
    details: dict[str, Any]
    created_at: datetime


# Decisions


class DecisionRequest(RequestBody):
    credit_score: int = Field(
        ge=300, le=900, description="The bureau score (such as CIBIL) pulled for the applicant."
    )


class DecisionOut(ResponseBody):
    application_id: int
    outcome: DecisionOutcome
    reason_codes: list[str]
    credit_score: int
    monthly_income: Decimal
    existing_emi: Decimal
    proposed_emi: Decimal
    foir: Decimal
    active_loans: int
    rules_version: str
    decided_by_id: int
    decided_at: datetime


# Loans and repayments


class InstallmentOut(ResponseBody):
    installment_number: int
    due_date: date
    amount_due: Decimal
    principal_component: Decimal
    interest_component: Decimal
    closing_balance: Decimal
    status: InstallmentStatus
    paid_at: datetime | None


class PrepaymentOut(ResponseBody):
    kind: PrepaymentKind
    principal: Decimal
    interest: Decimal = Field(description="Interest on the principal for the days it was owed.")
    amount: Decimal
    received_at: datetime


class LoanOut(ResponseBody):
    id: int
    application_id: int
    principal: Decimal
    annual_rate_pct: Decimal
    tenure_months: int = Field(description="Instalments in the repayment plan in force.")
    emi: Decimal = Field(description="The regular instalment of the plan in force.")
    status: ApplicationStatus
    outstanding_principal: Decimal
    disbursed_at: datetime
    schedule: list[InstallmentOut] = Field(validation_alias="repayments")
    prepayments: list[PrepaymentOut]


class RepaymentRequest(RequestBody):
    installment_number: int = Field(ge=1, description="Which instalment this payment settles.")


class PrepaymentRequest(RequestBody):
    principal: Annotated[Money, Field(gt=0, description="Principal to repay ahead of time.")]
    reduce: Literal["emi", "tenure"] = Field(
        description="Lower the EMI and keep the end date, or keep the EMI and finish sooner."
    )


class ForeclosureQuote(BaseModel):
    loan_id: int
    principal: Decimal = Field(description="Principal still owed.")
    interest: Decimal = Field(description="Interest for the days of this EMI period so far.")
    amount: Decimal = Field(description="What closes the loan today.")
    interest_days: int
    period_days: int
    valid_on: date = Field(description="The day in India the quote is for.")


class ForeclosureRequest(RequestBody):
    amount: Annotated[
        Money, Field(gt=0, description="Today's amount from GET /loans/{id}/foreclosure.")
    ]


# Reports


class ApprovalRateRow(BaseModel):
    month: date
    decided: int
    approved: int
    rejected: int
    approval_rate: Decimal | None = Field(description="Approved / decided; null with none decided.")
    approval_rate_3m: Decimal | None = Field(description="Over this month and the two before.")
    change_vs_previous_month: Decimal | None


class OutstandingRow(BaseModel):
    product: Product
    active_loans: int
    outstanding: Decimal
    share_pct: Decimal = Field(description="Share of all outstanding principal, in percent.")
    rank: int


class OverdueBucketRow(BaseModel):
    bucket: Literal["current", "sma_0", "sma_1", "sma_2", "npa"]
    days_past_due_min: int
    days_past_due_max: int | None = Field(description="Null for NPA, which has no upper limit.")
    loans: int
    outstanding: Decimal = Field(description="Principal still owed at the day-end.")
    overdue: Decimal = Field(description="Unpaid instalments past their due date, in full.")
    share_pct: Decimal | None = Field(description="Share of the book's principal, in percent.")
    at_risk_pct: Decimal | None = Field(
        description="Share of the book in this bucket or a worse one; the sma_1 row is PAR30."
    )


class OverdueReport(BaseModel):
    as_of: date = Field(description="The day in India whose day-end the report describes.")
    buckets: list[OverdueBucketRow]
