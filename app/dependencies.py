"""Dependencies shared by the routers: settings, the signed-in user and role checks."""

from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.config import Settings
from app.db import SessionDep
from app.domain.enums import Role
from app.models import User
from app.security import read_access_token

_bearer_token = OAuth2PasswordBearer(tokenUrl="/auth/token")


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_current_user(
    token: Annotated[str, Depends(_bearer_token)], session: SessionDep, settings: SettingsDep
) -> User:
    """The user named by a valid bearer token, loaded fresh so a deleted user is refused."""
    try:
        user = session.get(User, read_access_token(token, settings))
    except jwt.InvalidTokenError:
        user = None
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def _require_role(role: Role) -> Callable[[User], User]:
    def check(user: CurrentUser) -> User:
        if user.role != role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Only {role.value}s can do this")
        return user

    return check


ApplicantUser = Annotated[User, Depends(_require_role(Role.APPLICANT))]
UnderwriterUser = Annotated[User, Depends(_require_role(Role.UNDERWRITER))]
