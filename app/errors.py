"""Expected business failures. The API renders them as `{"detail": ..., "code": ...}`.

Services and domain rules raise these instead of HTTPException, so they stay usable
outside a request (the seed script, for instance). Each subclass fixes its HTTP status
and a stable code that clients can branch on. This module has no web dependencies.
"""


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

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
