from collections.abc import Iterator
from typing import Annotated

import psycopg
from fastapi import Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


def get_session(request: Request) -> Iterator[Session]:
    """Yield one session per request. Services commit explicitly; anything else is rolled back."""
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def violated_constraint(error: IntegrityError) -> str | None:
    """The name of the constraint behind an IntegrityError, such as 'uq_users_email'."""
    if isinstance(error.orig, psycopg.Error):
        return error.orig.diag.constraint_name
    return None
