"""Request and response bodies.

Requests forbid unknown fields, so a client cannot slip in fields such as `role`.
Responses list their fields explicitly, so nothing (like a password hash) leaks by accident.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.domain.enums import Role


def normalize_email(email: str) -> str:
    return email.strip().lower()


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResponseBody(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Error(BaseModel):
    detail: str
    code: str


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
    expires_in: int = Field(description="Seconds until the token expires.")
