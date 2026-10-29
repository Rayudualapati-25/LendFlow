"""Expected business failures. The API renders them as `{"detail": ..., "code": ...}`.

Services and domain rules raise these instead of HTTPException, so they stay usable
outside a request (the seed script, for instance). Each subclass fixes its HTTP status
and a stable code that clients can branch on. This module has no web dependencies.
"""


class DomainError(Exception):
    status_code = 400
    code = "bad_request"
    headers: dict[str, str] | None = None

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EmailAlreadyRegistered(DomainError):
    status_code = 409
    code = "email_already_registered"


class ProfileNotFound(DomainError):
    status_code = 404
    code = "profile_not_found"


class ProfileRequired(DomainError):
    status_code = 409
    code = "profile_required"


class TermsOutsideProduct(DomainError):
    status_code = 422
    code = "terms_outside_product"


class ApplicationNotFound(DomainError):
    status_code = 404
    code = "application_not_found"


class IllegalTransition(DomainError):
    status_code = 409
    code = "illegal_transition"


class DecisionNotFound(DomainError):
    status_code = 404
    code = "decision_not_found"


class IdempotencyKeyRequired(DomainError):
    status_code = 400
    code = "idempotency_key_required"


class IdempotencyKeyReused(DomainError):
    status_code = 422
    code = "idempotency_key_reused"


class LoanNotFound(DomainError):
    status_code = 404
    code = "loan_not_found"


class InstallmentNotFound(DomainError):
    status_code = 404
    code = "installment_not_found"


class InstallmentAlreadyPaid(DomainError):
    status_code = 409
    code = "installment_already_paid"


class InstallmentOutOfOrder(DomainError):
    status_code = 409
    code = "installment_out_of_order"


class DayNotEnded(DomainError):
    status_code = 422
    code = "day_not_ended"


class InstallmentDue(DomainError):
    status_code = 409
    code = "installment_due"


class PrepaymentTooLarge(DomainError):
    status_code = 422
    code = "prepayment_too_large"


class RemainderTooSmall(DomainError):
    status_code = 422
    code = "remainder_too_small"


class ForeclosureAmountChanged(DomainError):
    status_code = 409
    code = "foreclosure_amount_changed"


class InvalidRefreshToken(DomainError):
    status_code = 401
    code = "invalid_refresh_token"
    headers = {"WWW-Authenticate": "Bearer"}  # noqa: RUF012 (read-only class constant)


class RefreshTokenReused(DomainError):
    status_code = 401
    code = "refresh_token_reused"
    headers = {"WWW-Authenticate": "Bearer"}  # noqa: RUF012 (read-only class constant)
