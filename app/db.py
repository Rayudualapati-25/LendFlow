from collections.abc import Iterator
from typing import Annotated

import psycopg
from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    """An engine whose connections use UTC, so timestamps never depend on server settings."""
    return create_engine(
        database_url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"}
    )


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
