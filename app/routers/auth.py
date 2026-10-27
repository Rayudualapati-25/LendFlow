from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.db import SessionDep
from app.dependencies import CurrentUser, SettingsDep
from app.schemas import Error, RegisterRequest, Token, UserOut, normalize_email
from app.security import create_access_token
from app.services import users

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED, responses={409: {"model": Error}})
def register(body: RegisterRequest, session: SessionDep) -> UserOut:
    """Create an applicant account. Underwriter accounts are provisioned by an operator."""
    return UserOut.model_validate(users.register_applicant(session, body.email, body.password))


@router.post("/token", responses={401: {"model": Error}})
def sign_in(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
    settings: SettingsDep,
) -> Token:
    """Exchange an email (sent as `username`) and password for a bearer token."""
    user = users.authenticate(session, normalize_email(form.username), form.password)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(
        access_token=create_access_token(user, settings, now=datetime.now(UTC)),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
