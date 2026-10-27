"""Password hashing (Argon2id) and signed access tokens (JWT, HS256)."""

from datetime import datetime, timedelta
from functools import cache

import jwt
from pwdlib import PasswordHash

from app.config import Settings
from app.models import User

ALGORITHM = "HS256"
_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _hasher.verify(password, password_hash)


@cache
def _dummy_hash() -> str:
    return hash_password("only used to spend time on unknown emails")


def spend_verification_time(password: str) -> None:
    """Do the work of a real check, so an unknown email takes as long as a wrong password."""
    _hasher.verify(password, _dummy_hash())


def create_access_token(user: User, settings: Settings, now: datetime) -> str:
    claims = {
        "sub": str(user.id),
        "role": user.role.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def read_access_token(token: str, settings: Settings) -> int:
    """Return the user id from a valid token; raise jwt.InvalidTokenError for anything else."""
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        # Pinned, so a token can never pick its own algorithm (such as "none").
        algorithms=[ALGORITHM],
        options={"require": ["sub", "iat", "exp"]},
    )
    try:
        return int(claims["sub"])
    except ValueError as error:
        raise jwt.InvalidTokenError("subject is not a user id") from error
