from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.config import Settings
from app.db import SessionDep
from app.dependencies import CurrentUser, NowDep, SettingsDep
from app.models import RefreshToken, User
from app.schemas import Error, RefreshRequest, RegisterRequest, Token, UserOut, normalize_email
from app.security import create_access_token
from app.services import sessions, users

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
    now: NowDep,
) -> Token:
    """Exchange an email (sent as `username`) and password for an access token and a
    refresh token."""
    user = users.authenticate(session, normalize_email(form.username), form.password)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    refresh_token, row = sessions.start(session, user, now=now, settings=settings)
    return _tokens(user, refresh_token, row, settings=settings, now=now)


@router.post("/refresh", responses={401: {"model": Error}})
def refresh(body: RefreshRequest, session: SessionDep, settings: SettingsDep, now: NowDep) -> Token:
    """Exchange a refresh token for a new access token and the next refresh token.

    Each refresh token works once. Using one a second time ends the whole sign-in
    (401 `refresh_token_reused`): a copied token cannot keep a session alive.
    """
    user, refresh_token, row = sessions.rotate(
        session, body.refresh_token, now=now, settings=settings
    )
    return _tokens(user, refresh_token, row, settings=settings, now=now)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshRequest, session: SessionDep, now: NowDep) -> None:
    """End the sign-in that issued this refresh token. Access tokens already issued keep
    working until they expire, at most 30 minutes later."""
    sessions.end(session, body.refresh_token, now=now)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


def _tokens(
    user: User, refresh_token: str, row: RefreshToken, *, settings: Settings, now: datetime
) -> Token:
    return Token(
        # PyJWT checks expiry against the system clock, so access tokens are stamped with it.
        access_token=create_access_token(user, settings, now=datetime.now(UTC)),
        expires_in=settings.access_token_expire_minutes * 60,
        refresh_token=refresh_token,
        refresh_expires_in=int((row.expires_at - now).total_seconds()),
    )
