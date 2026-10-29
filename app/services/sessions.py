"""Sign-in sessions held by rotating refresh tokens, with reuse detection (D53).

Signing in starts a session and issues its first refresh token. Each refresh token works
once: exchanging it issues the next one in the same session. A token presented a second
time means two parties hold it, and the server cannot tell the thief from the client, so
the whole session is revoked (RFC 9700, section 4.14.2).
"""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import InvalidRefreshToken, RefreshTokenReused
from app.models import RefreshToken, User

log = logging.getLogger(__name__)


def start(
    session: Session, user: User, *, now: datetime, settings: Settings
) -> tuple[str, RefreshToken]:
    """Open a session for `user` and return its first refresh token (and its row)."""
    session_expires_at = now + timedelta(days=settings.session_max_days)
    token, row = _issue(user.id, uuid.uuid4(), session_expires_at, now=now, settings=settings)
    session.add(row)
    session.commit()
    return token, row


def rotate(
    session: Session, token: str, *, now: datetime, settings: Settings
) -> tuple[User, str, RefreshToken]:
    """Exchange a refresh token for the next one in its session, with the user it names.

    The row is locked, so two requests with the same token take turns: the second one
    finds it already used and ends the session.
    """
    row = session.scalars(
        select(RefreshToken)
        .where(RefreshToken.token_hash == _digest(token))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None or row.revoked_at is not None:
        raise InvalidRefreshToken("Unknown or revoked refresh token: sign in again")
    if row.used_at is not None:
        _revoke_session(session, row.family_id, now)
        session.commit()
        log.warning(
            "Refresh token reused: session %s of user %s revoked", row.family_id, row.user_id
        )
        raise RefreshTokenReused("This refresh token was already used: sign in again")
    if row.expires_at <= now:
        raise InvalidRefreshToken("Expired refresh token: sign in again")

    row.used_at = now
    next_token, next_row = _issue(
        row.user_id, row.family_id, row.session_expires_at, now=now, settings=settings
    )
    session.add(next_row)
    session.commit()
    return row.user, next_token, next_row


def end(session: Session, token: str, *, now: datetime) -> None:
    """Log out: revoke every refresh token of the token's session. Unknown tokens are
    ignored, so the answer never reveals whether a token exists."""
    family_id = session.scalar(
        select(RefreshToken.family_id).where(RefreshToken.token_hash == _digest(token))
    )
    if family_id is not None:
        _revoke_session(session, family_id, now)
        session.commit()


def _issue(
    user_id: int,
    family_id: uuid.UUID,
    session_expires_at: datetime,
    *,
    now: datetime,
    settings: Settings,
) -> tuple[str, RefreshToken]:
    token = secrets.token_urlsafe(32)
    row = RefreshToken(
        user_id=user_id,
        family_id=family_id,
        token_hash=_digest(token),
        issued_at=now,
        expires_at=min(
            now + timedelta(days=settings.refresh_token_expire_days), session_expires_at
        ),
        session_expires_at=session_expires_at,
    )
    return token, row


def _revoke_session(session: Session, family_id: uuid.UUID, now: datetime) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def _digest(token: str) -> str:
    """SHA-256 suffices: the token is 256 random bits, not a guessable password."""
    return hashlib.sha256(token.encode()).hexdigest()
