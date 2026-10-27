"""Expected business failures, rendered by one handler as `{"detail": ..., "code": ...}`.

Services raise these instead of HTTPException, so they stay usable outside a request
(the seed script, for instance). Each subclass fixes its HTTP status and a stable code
that clients can branch on.
"""

from fastapi import Request
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EmailAlreadyRegistered(DomainError):
    status_code = 409
    code = "email_already_registered"


async def handle_domain_error(_: Request, error: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code, content={"detail": error.message, "code": error.code}
    )
